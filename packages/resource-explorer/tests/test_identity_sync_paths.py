"""The catalogue commit and the file-system FileFolder publish write AS the person who pressed (2026-10-23).

Evidence (re-proof phase 1, 2026-10-22): pressed by peterprofile through POST /api/catalogue-scope/<slug>/commit, yet
every element RE wrote had `createdBy=resourceexplorernpa`. Both paths now reuse #836's mechanism (`presser_hold`): the
press holds the presser's session in memory under the run id, the queued run's writes run inside
`publishing_as_presser`, and a run whose session is gone writes nothing (never the service account).

Only the Egeria boundary is faked: the catalogue gateway's Egeria (`FakeEgeria`, the existing stateful fake) and the
client factory (`egeria_clients.egeria_client`, autospec'd). The route, the hold, the queue's `execute_run`, the
handler, `execute_commit` and the inline outbox drain are real. Every token and name is invented.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer import egeria_clients, presser_hold, run_queue  # noqa: E402
from resource_explorer.a2a_auth import CallerIdentity, current_caller  # noqa: E402
from resource_explorer.egeria_clients import (  # noqa: E402
    EXPIRED_SENTENCE,
    Daemon,
    DaemonReason,
    acting_as,
    current_principal,
)
from resource_explorer.presser_hold import SIGN_IN_EXPIRED_WORDS  # noqa: E402
from tests.test_catalogue_commit import (  # noqa: E402,F401 - fixtures: the commit tests' tmp registry and world
    SOURCE,
    _curation_access_reads,
    _zones,
    choose,
    registry,
    world,
)

PRESSER = "peterprofile"


def _token(exp_in: float, marker: str) -> str:
    return jwt.encode({"sub": PRESSER, "exp": int(time.time() + exp_in), "marker": marker}, "k", algorithm="HS256")


LIVE_TOKEN = _token(3600, "live-sync-token")


@pytest.fixture(autouse=True)
def _clean_hold():
    presser_hold.clear_all()
    yield
    presser_hold.clear_all()


def _who() -> tuple:
    try:
        p = current_principal()
    except Exception as exc:  # noqa: BLE001 - recorded, so a test says "nobody" rather than crashing in the fake
        return ("none", type(exc).__name__, "")
    return (p.kind, p.client_user or p.user_id, p.token or "")


class _WhoList(list):
    """FakeEgeria's call log, recording who `current_principal()` was at each Egeria call."""

    def __init__(self):
        super().__init__()
        self.who: list[tuple] = []

    def append(self, item):
        super().append(item)
        self.who.append((item[0], *_who()))


@pytest.fixture
def fake():
    f = FakeEgeria(SOURCE)
    f.calls = _WhoList()
    return f


@pytest.fixture
def factory(monkeypatch, fake):
    """`make_gateway` replaced by an autospec of itself that builds the REAL gateway, asks it for its pyegeria client
    (the factory autospec'd, so nothing is constructed), records who it was built for, and hands back the fake."""
    built: list = []
    real_factory = egeria_clients.egeria_client

    def build(db_entity, identity=None):
        real = gw.PyegeriaCatalogueGateway(db_entity, identity=identity)
        spy = create_autospec(real_factory, side_effect=lambda ident, **kw: built.append(ident) or MagicMock())
        with patch("resource_explorer.egeria_clients.egeria_client", spy):
            real._factory()
        return fake

    monkeypatch.setattr(cc, "make_gateway", create_autospec(cc.make_gateway, side_effect=build))
    return built


@pytest.fixture
def readback(monkeypatch, fake):
    """The run's read of the database element by GUID ("written as <x>"): the pyegeria client factory autospec'd, the
    AssetMaker autospec'd. Egeria's `updatedBy` is whoever made the LAST write call on the fake (createdBy someone
    earlier), so a regression that writes as the service account reads back as the service account."""
    from pyegeria import AssetMaker

    state = {"reads": [], "fail": False, "bodies": [], "by_guid": {}}

    def build(identity, **kw):
        clients = create_autospec(egeria_clients.EgeriaClients, instance=True, spec_set=True)
        maker = create_autospec(AssetMaker, instance=True, spec_set=True)

        def get(guid, *a, **k):
            from datetime import datetime, timezone

            state["reads"].append((identity.kind, identity.client_user or identity.user_id, guid))
            state["bodies"].append(k.get("body"))
            if state["fail"]:
                raise RuntimeError("test boundary: the read-back failed")
            if guid in state["by_guid"]:                     # an element a test says something else about
                return {"elementHeader": {"guid": guid, "versions": state["by_guid"][guid]}}
            writers = [w[2] for w in _writes(fake)]
            versions = {"createdBy": "someone-earlier", **({"updatedBy": writers[-1]} if writers else {}),
                        "updateTime": datetime.now(timezone.utc).isoformat()}
            return {"elementHeader": {"guid": guid, "versions": versions}}

        maker.get_asset_by_guid.side_effect = get
        clients.of.return_value = maker
        return clients

    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        create_autospec(egeria_clients.egeria_client, side_effect=build))
    return state


@pytest.fixture
def client(registry, world, fake, factory, readback, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import catalogue_scope as routes

    monkeypatch.setattr(routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def _as_presser():
    from resource_explorer.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(user_id=PRESSER, egeria_token=LIVE_TOKEN)}


def _in_a_bare_thread(fn):
    """Run `fn` on a plain `threading.Thread` (an EMPTY context, as the queue runner's thread is): an identity carried
    only in a ContextVar does not survive this hop. Returns (fn's result, the caller the thread saw at its start)."""
    out: dict = {}

    def body():
        out["caller_at_start"] = current_caller.get()
        out["acting_at_start"] = egeria_clients._acting.get()
        out["result"] = fn()

    t = threading.Thread(target=body)
    t.start()
    t.join(60)
    assert not t.is_alive()
    return out


def _press(client, world, fake, factory, monkeypatch):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: True)
    choose(world, "sales", "catalogue")
    r = client.post("/api/catalogue-scope/db/commit", json={}, headers=_as_presser())
    assert r.status_code == 200, r.text
    # the press's own preview reads were made in the request, as the presser; only the run's calls are asserted below
    assert factory and all(i.kind == "caller" and i.user_id == PRESSER for i in factory)
    factory.clear()
    del fake.calls[:]
    fake.calls.who.clear()
    return r.json()


def _writes(fake) -> list[tuple]:
    reads = {"read_element", "qualified_name_of", "list_catalog_targets", "relationships", "raw_element",
             "raw_related", "read_zones", "elements_under", "schema_elements", "archived_siblings",
             "engine_action_status", "connector_status", "survey_outcome"}
    return [w for w in fake.calls.who if w[0] not in reads]


# ── path 1: the catalogue commit ────────────────────────────────────────────

def test_a_pressed_commit_writes_as_the_presser_across_the_thread_hop_and_the_activity_says_so(
        client, world, fake, factory, readback, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    held = presser_hold.take(out["run_id"])
    assert held is not None and (held.person, held.token) == (PRESSER, LIVE_TOKEN)
    row = registry.get_run(out["run_id"])
    assert LIVE_TOKEN not in row["target"] and "live-sync-token" not in row["target"]

    ran = _in_a_bare_thread(lambda: run_queue.execute_run(row, registry))
    assert ran["caller_at_start"] is None and ran["acting_at_start"] is None   # the hop is real: nothing carried over
    assert ran["result"].state == "succeeded", ran["result"].error

    # the client the run's gateway built is the presser's, never the service account's
    assert [(i.kind, i.user_id, i.token, i.is_service_account) for i in factory] == [
        ("caller", PRESSER, LIVE_TOKEN, False)]
    # every Egeria call the run made (the inline outbox drain's attach included) was made as the presser
    assert fake.calls.who and {w[1:] for w in fake.calls.who} == {("caller", PRESSER, LIVE_TOKEN)}
    assert {"publish_database", "create_schema_element"} <= {w[0] for w in _writes(fake)}, _writes(fake)
    # the activity row says the intent (who the run acted as) and the proof (who Egeria says wrote, from a read of
    # the schema element THIS commit attached, by GUID, made as the presser)
    summary = registry.get_activity(out["activity_id"])["summary"]
    assert summary.endswith(f"applying as {PRESSER} from {presser_hold.SOURCE_PRESSER} · written as {PRESSER}"), summary
    attached = _attached_guids(registry, out["curation"]["id"])
    assert attached and readback["reads"] == [("caller", PRESSER, attached[0])]
    assert registry.get_database("db").egeria_asset_guid not in {r[2] for r in readback["reads"]}
    assert presser_hold.take(out["run_id"]) is None                          # released when the run ended


def test_a_pressed_commit_claimed_where_nothing_is_held_writes_nothing_and_says_the_expired_words(
        client, world, fake, factory, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    presser_hold.release(out["run_id"])                                       # e.g. claimed by another process
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    assert ran["result"].state == "failed"
    assert list(fake.calls) == [] and factory == []                           # nothing written, as anyone
    rec = registry.get_run(out["run_id"])
    from resource_explorer.curate_plan import Curations

    steps = {s["name"]: s for s in Curations(registry).get(out["curation"]["id"])["steps"]}
    assert steps["publish_elements"]["state"] == "failed"
    assert steps["publish_elements"]["detail"] == f"{EXPIRED_SENTENCE} · nothing written"
    assert all(s["state"] == "skipped" for n, s in steps.items() if n != "publish_elements")
    assert registry.get_activity(out["activity_id"])["summary"].endswith(f"not applied: {EXPIRED_SENTENCE}")
    assert rec["state"] == "failed"


def test_a_commit_row_with_no_person_keeps_the_service_account(client, world, fake, factory, registry, monkeypatch):
    """The daemon bucket: a catalogue_commit row that carries no presser (none is enqueued today; an old row, or a
    future automated one) runs as `Daemon(RUN_QUEUE, requested_by)`, as before."""
    out = _press(client, world, fake, factory, monkeypatch)
    presser_hold.release(out["run_id"])
    row_id = registry.enqueue_run("catalogue_commit", {"slug": "db", "curation_id": out["curation"]["id"]},
                                  requested_by=PRESSER, result_ref=out["activity_id"])
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(row_id), registry))
    assert ran["result"].state == "succeeded", ran["result"].error
    daemon_user = Daemon(DaemonReason.RUN_QUEUE).client_user
    assert [(i.kind, i.is_service_account, i.requested_by) for i in factory] == [("daemon", True, PRESSER)]
    assert {w[1:3] for w in fake.calls.who} == {("daemon", daemon_user)}
    summary = registry.get_activity(out["activity_id"])["summary"]
    assert summary.endswith(f"applying as {daemon_user} from {presser_hold.SOURCE_SERVICE_ACCOUNT}, "
                            f"on behalf of {PRESSER} · written as {daemon_user}"), summary
    with registry._conn() as conn:          # an unpressed commit's rows OMIT the key: never `"as_presser": false`,
        texts = [r["payload_json"] for r in conn.execute("SELECT payload_json FROM egeria_outbox").fetchall()]
    assert texts and not any("as_presser" in t for t in texts)          # which the claim's text rule counts as marked


def test_a_signed_out_commit_is_refused_and_queues_nothing(client, world, fake, registry, monkeypatch):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: True)
    choose(world, "sales", "catalogue")
    assert client.post("/api/catalogue-scope/db/commit", json={}).status_code == 401
    with registry._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0


# ── path 2: the file system's FileFolder (the adaptive step of a queued survey) ─────────────────────────────────

@pytest.fixture
def fs_world(tmp_path):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    root = tmp_path / "lake"
    root.mkdir()
    (root / "a.csv").write_text("id,name\n1,x\n")
    reg.register_filesystem(FileSystemEntity(slug="lake", display_name="Lake", local_mount_point=str(root),
                                             canonical_mount_point="/deployments/lake",
                                             egeria_url="https://egeria.test:9443", egeria_server="qs-view-server"))
    return reg


@pytest.fixture
def fs_factory(monkeypatch):
    """The pyegeria client factory, autospec'd: records who each client is built for, then stops (nothing reaches a
    platform; the surveyor reports the stop as a failed publish, which these tests do not care about)."""
    built: list = []

    def stop(identity, **kw):
        built.append(identity)
        raise RuntimeError("test boundary: no Egeria here")

    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        create_autospec(egeria_clients.egeria_client, side_effect=stop))
    return built


def _adaptive(reg):
    from resource_explorer.surveyors.filesystem.survey_definition_adapter import _run_egeria_adaptive

    return _run_egeria_adaptive(reg.get_filesystem("lake"), reg, MagicMock(), force_egeria_publish=True,
                                egeria_url="https://egeria.test:9443", egeria_server="qs-view-server")


def _as_queue_worker(row_requester=PRESSER):
    return acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=row_requester))


def test_a_pressed_survey_builds_the_filefolder_client_for_the_presser_across_the_thread_hop(fs_world, fs_factory):
    presser_hold.hold("fs-run-1", PRESSER, LIVE_TOKEN)

    def run():
        with _as_queue_worker(), presser_hold.pressed_run("fs-run-1", PRESSER):
            return _adaptive(fs_world)

    ran = _in_a_bare_thread(run)
    assert ran["caller_at_start"] is None
    assert [(i.kind, i.user_id, i.token) for i in fs_factory] == [("caller", PRESSER, LIVE_TOKEN)]
    outcome = ran["result"]
    # the step's detail (what the run's activity row carries) names who it wrote as
    assert outcome["applying_as_intent"] == f"applying as {PRESSER} from {presser_hold.SOURCE_PRESSER}"
    assert "applying_as" not in outcome and "written_as" not in outcome      # intent only: no proof is claimed


def test_a_pressed_survey_with_nothing_held_writes_no_filefolder_and_says_the_words(fs_world, fs_factory):
    with _as_queue_worker(), presser_hold.pressed_run("fs-run-never-held", PRESSER):
        outcome = _adaptive(fs_world)
    assert fs_factory == []                                                   # no client was built, for anyone
    assert outcome["publish_state"] == SIGN_IN_EXPIRED_WORDS
    assert "applying_as_intent" not in outcome


def test_a_scheduled_survey_keeps_the_service_account(fs_world, fs_factory):
    with acting_as(Daemon(DaemonReason.SCHEDULER)):
        outcome = _adaptive(fs_world)
    assert [(i.kind, i.is_service_account) for i in fs_factory] == [("daemon", True)]
    assert outcome["applying_as_intent"].endswith(f"from {presser_hold.SOURCE_SERVICE_ACCOUNT}")


# ── the identity line ───────────────────────────────────────────────────────

def test_the_identity_line_holds_no_token():
    presser_hold.hold("r", PRESSER, LIVE_TOKEN)
    with _as_queue_worker(), presser_hold.pressed_run("r", PRESSER), presser_hold.publishing_as_presser():
        line = presser_hold.applying_as_line(current_principal())
    assert line == f"applying as {PRESSER} from {presser_hold.SOURCE_PRESSER}"
    assert LIVE_TOKEN not in line and presser_hold.applying_as_line(None) == ""


def test_written_as_says_could_not_check_when_the_read_back_fails(client, world, fake, factory, readback, registry,
                                                                    monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    readback["fail"] = True
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    assert ran["result"].state == "succeeded", ran["result"].error
    summary = registry.get_activity(out["activity_id"])["summary"]
    assert summary.endswith(f"applying as {PRESSER} from {presser_hold.SOURCE_PRESSER} · "
                            f"{presser_hold.WRITTEN_AS_UNCHECKED}"), summary
    assert presser_hold.WRITTEN_AS_UNCHECKED == "written as: could not check"


def _attached_guids(registry, curation_id):
    return [p["element_guid"] for p in registry.list_catalogue_commit_proofs("db")
            if p["curation_id"] == curation_id and p["proof"] == cc.P_TARGET and p["element_guid"]]


def test_written_as_names_this_commits_writer_not_the_database_elements_earlier_one(
        client, world, fake, factory, readback, registry, monkeypatch):
    """F2: the database element was last written by the NPA (an earlier commit); this commit's schema attach was
    made by the person. The line reads the schema element this commit wrote, so it names the person."""
    out = _press(client, world, fake, factory, monkeypatch)
    db_guid = registry.get_database("db").egeria_asset_guid
    readback["by_guid"][db_guid] = {"createdBy": "resourceexplorernpa", "updatedBy": "resourceexplorernpa",
                                    "updateTime": "2026-01-01T00:00:00+00:00"}
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    assert ran["result"].state == "succeeded", ran["result"].error
    summary = registry.get_activity(out["activity_id"])["summary"]
    assert summary.endswith(f" · written as {PRESSER}"), summary
    assert "resourceexplorernpa" not in summary
    assert readback["bodies"] and all(b.get("forLineage") is True for b in readback["bodies"])


def test_written_as_could_not_check_when_the_element_was_last_changed_before_the_run(
        client, world, fake, factory, readback, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    old = {"createdBy": PRESSER, "updatedBy": PRESSER, "updateTime": "2026-01-01T00:00:00+00:00"}
    real_get = registry.list_catalogue_commit_proofs

    def proofs_with_old_versions(slug):          # every attached element this run reads is "older than the run"
        rows = real_get(slug)
        for p in rows:
            if p["proof"] == cc.P_TARGET and p["element_guid"]:
                readback["by_guid"][p["element_guid"]] = old
        return rows

    monkeypatch.setattr(registry, "list_catalogue_commit_proofs", proofs_with_old_versions)
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    assert ran["result"].state == "succeeded", ran["result"].error
    summary = registry.get_activity(out["activity_id"])["summary"]
    assert summary.endswith(f" · {presser_hold.WRITTEN_AS_UNCHECKED}"), summary


def test_written_as_could_not_check_when_every_write_step_was_skipped(registry, readback):
    from datetime import datetime, timezone

    from resource_explorer.curate_plan import Curations

    cur = Curations(registry)
    rec = cur.create("database", "db", author=PRESSER, selection={}, manifest={}, steps=list(cc.STEPS_DB))
    for st in cc.STEPS_DB:
        cur.set_step(rec["id"], st, "skipped", "nothing to do")
    registry.append_catalogue_commit_proof("db", proof=cc.P_TARGET, node_kind="schema", schema_name="sales",
                                           element_guid="guid-from-elsewhere", curation_id=rec["id"])
    line = cc.commit_written_as_line(registry, rec["id"], datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert line == presser_hold.WRITTEN_AS_UNCHECKED
    assert readback["reads"] == []                                    # nothing written: nothing is read or named


def test_version_time_reads_epoch_ms_and_iso():
    from datetime import datetime, timezone

    t = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    assert presser_hold.version_time_of({"versions": {"updateTime": int(t.timestamp() * 1000)}}) == t
    assert presser_hold.version_time_of({"elementHeader": {"versions": {"createTime": "2026-10-23T12:00:00Z"}}}) == t
    assert presser_hold.version_time_of({"versions": {"updateTime": "not a time"}}) is None
    assert presser_hold.version_time_of(None) is None


# ── a pressed commit's outbox rows: only its own inline drain, as the presser, applies them ─────────────────────

def _background_drain(registry):
    """The scheduler's drain (`scheduler.py`: `drain_outbox(registry, identity=Daemon(OUTBOX))`), on a bare thread."""
    from resource_explorer.egeria_outbox import drain_outbox

    return _in_a_bare_thread(lambda: drain_outbox(registry, identity=Daemon(DaemonReason.OUTBOX)))["result"]


def _attach_rows(registry, curation_id):
    return [r for r in registry.list_catalogue_outbox_rows("db")
            if r["run_id"] == curation_id and r["element_kind"] == cc.KIND_ATTACH]


def test_a_pressed_commits_outbox_rows_carry_the_marker(
        client, world, fake, factory, registry, monkeypatch):
    import json

    out = _press(client, world, fake, factory, monkeypatch)
    _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    with registry._conn() as conn:
        payloads = [json.loads(r["payload_json"]) for r in conn.execute(
            "SELECT payload_json FROM egeria_outbox WHERE run_id=?", (out["curation"]["id"],)).fetchall()]
    assert payloads and all(p.get("as_presser") is True for p in payloads)


def _rows_with_payload_text(registry, cases) -> dict:
    ids = {}
    for name, payload_json in cases:
        rid = registry.enqueue_outbox_element("database", "db", cc.KIND_ATTACH, f"qn::{name}", {})
        with registry._conn() as conn:
            conn.execute("UPDATE egeria_outbox SET payload_json=? WHERE id=?", (payload_json, rid))
        ids[rid] = name
    return ids


def test_the_claim_treats_any_payload_naming_the_key_as_marked_and_never_raises(registry):
    """F1: one fail-safe text rule, no JSON parse: a payload that NAMES `"as_presser"` is marked whatever its value,
    nesting or validity (a wrong guess only makes a row wait); corrupt and \\u0000 payloads do not abort the claim."""
    import json

    ids = _rows_with_payload_text(registry, (
        ("marked", json.dumps({"by": PRESSER, "as_presser": True})),
        ("marked_compact", json.dumps({"as_presser": True, "by": PRESSER}, separators=(",", ":"))),
        ("false", json.dumps({"by": PRESSER, "as_presser": False})),
        ("nested", json.dumps({"inner": {"as_presser": True}})),
        ("corrupt", '{"by": "' + PRESSER + '", "as_presser": tru'),
        ("nul_marked", '{"by": "x\\u0000", "as_presser": true}'),
        ("nul_plain", '{"by": "x\\u0000"}'),
        ("look_alike", json.dumps({"asXpresser": True})),
        ("plain", json.dumps({"by": PRESSER}))))
    claimed = {ids[r["id"]] for r in registry.claim_due_outbox_elements()}
    assert claimed == {"nul_plain", "look_alike", "plain"}
    # another person's drain-as-presser takes none of the marked rows
    assert registry.claim_due_outbox_elements(as_presser_by="someone-else") == []
    # the presser's own drain takes the marked rows whose parsed `by` is them; corrupt, or no `by`, waits
    assert {ids[r["id"]] for r in registry.claim_due_outbox_elements(as_presser_by=PRESSER)} == {
        "marked", "marked_compact", "false"}
    left = {ids[r["id"]] for r in registry.list_catalogue_outbox_rows("db") if r["status"] == "pending"}
    assert left == {"nested", "corrupt", "nul_marked"}


def test_the_claims_presser_rule_is_the_same_text_sql_on_postgres_and_sqlite():
    """F1: no `::jsonb` cast and no per-dialect JSON parse: the Postgres claim's SQL is the SQLite SQL plus the lock."""
    from contextlib import contextmanager

    from resource_explorer.registry import AS_PRESSER_LIKE, ProjectRegistry

    def captured(is_pg: bool) -> tuple[str, tuple]:
        executed: list = []

        class _Rows:
            rowcount = 0

            def fetchall(self):
                return []

            def fetchone(self):
                return None

        class _Conn:
            is_postgres = is_pg

            def execute(self, sql, params=()):
                executed.append((" ".join(sql.split()), tuple(params)))
                return _Rows()

        reg = ProjectRegistry.__new__(ProjectRegistry)
        reg._conn = contextmanager(lambda: (yield _Conn()))
        assert reg.claim_due_outbox_elements(now="2026-10-23T12:00:00") == []
        return next(e for e in executed if e[0].startswith("SELECT o.id, o.payload_json FROM egeria_outbox o"))

    pg_sql, pg_params = captured(True)
    lite_sql, lite_params = captured(False)
    assert pg_sql == lite_sql + " FOR UPDATE SKIP LOCKED" and pg_params == lite_params
    assert "::jsonb" not in pg_sql and "json_" not in pg_sql and "->>" not in pg_sql
    assert "NOT (COALESCE(o.payload_json, '') LIKE ? ESCAPE '!')" in pg_sql and AS_PRESSER_LIKE in pg_params
    assert AS_PRESSER_LIKE == '%"as!_presser"%'


def _drain_as_person(registry, person, run_id, **kw):
    """A person's own drain (`drain_outbox` with no identity: `current_principal()` is that person), on a bare thread."""
    from resource_explorer.egeria_outbox import drain_outbox

    presser_hold.hold("drain-" + person, person, _token(3600, "drain-" + person))

    def run():
        with _as_queue_worker(person), presser_hold.pressed_run("drain-" + person, person), \
                presser_hold.publishing_as_presser():
            return drain_outbox(registry, run_id=run_id, **kw)

    return _in_a_bare_thread(run)["result"]


def _left_by_a_pressed_run(registry, cid):
    return registry.enqueue_outbox_element("database", "db", cc.KIND_ATTACH, "qn::left-by-a-pressed-run",
                                           {"slug": "db", "schema": "sales", "curation_id": cid, "by": PRESSER,
                                            "as_presser": True}, run_id=cid)


def test_a_persons_scoped_drain_without_drain_as_presser_leaves_a_pressed_row(registry, fake, factory):
    """Exclusion by proof, not convention: a person's drain scoped to the run's id is not enough; only the drain
    that says `drain_as_presser=True` (the catalogue commit's inline drain) may claim a marked row."""
    _left_by_a_pressed_run(registry, "cur-1")
    summary = _drain_as_person(registry, PRESSER, "cur-1")
    assert summary["claimed"] == 0 and list(fake.calls) == []
    assert [r["status"] for r in _attach_rows(registry, "cur-1")] == ["pending"]


def test_drain_as_presser_by_another_person_leaves_the_row(registry, fake, factory):
    _left_by_a_pressed_run(registry, "cur-2")
    summary = _drain_as_person(registry, "someone-else", "cur-2", drain_as_presser=True)
    assert summary["claimed"] == 0 and list(fake.calls) == []
    assert [r["status"] for r in _attach_rows(registry, "cur-2")] == ["pending"]


def test_a_marked_row_the_pressed_drain_did_not_finish_says_press_catalogue_again():
    """A marked row left pending or failed by its own drain is never retried in the background: its step says so."""
    cur = MagicMock()
    rows = [{"id": 1, "status": "pending", "payload": {"schema": "sales", "as_presser": True}},
            {"id": 2, "status": "failed", "last_error": "SERVER_ERROR_500. More.",
             "payload": {"schema": "hr", "as_presser": True}},
            {"id": 3, "status": "pending", "payload": {"schema": "ops"}}]
    cc._step_from_outbox(cur, "cid", "schema_targets", rows, "attach")
    (_cid, step, state, words), _kw = cur.set_step.call_args
    assert (step, state) == ("schema_targets", "failed")
    assert f"sales: {cc.PRESS_AGAIN_WORDS}" in words
    assert f"hr: SERVER_ERROR_500. · {cc.PRESS_AGAIN_WORDS}" in words
    assert "1 queued in the outbox (#3)" in words                     # an unmarked row still waits for the drain
    assert cc.PRESS_AGAIN_WORDS == "not sent · press Publish again"


def test_the_background_drain_never_applies_a_pressed_row(client, world, fake, factory, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    cid = out["curation"]["id"]
    registry.enqueue_outbox_element("database", "db", cc.KIND_ATTACH, "qn::left-by-a-pressed-run",
                                    {"slug": "db", "schema": "sales", "curation_id": cid, "by": PRESSER,
                                     "as_presser": True}, run_id=cid)
    summary = _background_drain(registry)
    assert summary["claimed"] == 0 and list(fake.calls) == [] and factory == []
    assert [r["status"] for r in _attach_rows(registry, cid)] == ["pending"]


def test_a_5xx_on_a_pressed_attach_is_never_retried_as_the_service_account(
        client, world, fake, factory, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    cid = out["curation"]["id"]
    fake.fail["create_schema_element"] = "SERVER_ERROR_500 => Egeria detected error (test)"
    _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    rows = _attach_rows(registry, cid)
    assert rows and {r["status"] for r in rows} == {"failed"}, rows
    from resource_explorer.curate_plan import Curations

    step = {s["name"]: s for s in Curations(registry).get(cid)["steps"]}["schema_targets"]
    assert step["state"] == "failed" and cc.PRESS_AGAIN_WORDS in step["detail"], step
    # Egeria is back, and the 15-minute retry is due: the background drain still leaves the row for the presser
    del fake.fail["create_schema_element"]
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at='2000-01-01T00:00:00' WHERE run_id=?", (cid,))
    del fake.calls[:]
    fake.calls.who.clear()
    summary = _background_drain(registry)
    assert summary["claimed"] == 0
    assert _writes(fake) == []                                       # nothing written, as the NPA or anyone
    assert {r["status"] for r in _attach_rows(registry, cid)} == {"failed"}
    # (the row WAS due: the presser's own claim takes it)
    assert {r["id"] for r in registry.claim_due_outbox_elements(run_id=cid, as_presser_by=PRESSER)} == {
        r["id"] for r in rows}


def test_a_scheduler_drain_between_enqueue_and_the_inline_drain_leaves_the_rows_for_the_presser(
        client, world, fake, factory, registry, monkeypatch):
    out = _press(client, world, fake, factory, monkeypatch)
    raced: list[dict] = []
    real_enqueue = registry.enqueue_outbox_element

    def enqueue_then_race(*args, **kwargs):
        row_id = real_enqueue(*args, **kwargs)
        raced.append(_background_drain(registry))           # the scheduler's loop fires right here
        return row_id

    # the handler's ProjectRegistry() shares this registry's __dict__ (the `client` fixture), so it sees the wrapper
    monkeypatch.setattr(registry, "enqueue_outbox_element", enqueue_then_race)
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(registry.get_run(out["run_id"]), registry))
    assert ran["result"].state == "succeeded", ran["result"].error
    assert raced and all(s["claimed"] == 0 for s in raced)
    rows = _attach_rows(registry, out["curation"]["id"])
    assert rows and {r["status"] for r in rows} == {"done"}
    # the attach the inline drain made was made as the presser
    attaches = [w for w in _writes(fake) if w[0] == "create_schema_element"]
    assert attaches and {w[1:3] for w in attaches} == {("caller", PRESSER)}


# ── path 2, end to end: a queued file-system Survey Definition run, through the queue and the executor ──────────

FS_DEFINITION = "FileSystemDirectorySurvey"


@pytest.fixture
def fs_queue(fs_world, fs_factory, monkeypatch):
    """The real queue: `execute_run` -> `_handle_survey_definition_run` -> `execute_and_record_definition` -> the
    executor's step dispatch -> the file system's `egeria-adaptive` handler. Faked: reading the definition from Egeria
    (autospec'd `SurveyDefinitionReader`), the pyegeria client factory (`fs_factory`)."""
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinition,
        SurveyDefinitionReader,
        SurveyStep,
    )

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", fs_world.__dict__) or None)
    monkeypatch.setattr(type(fs_world), "has_assigned_egeria_project", lambda self, *a, **k: False)
    definition = SurveyDefinition(
        process_guid="guid-fs-process", display_name="File system", qualified_name=f"GovActionProcess::{FS_DEFINITION}",
        supported_technology_type="File System Directory",
        steps=[SurveyStep(guid="guid-fs-step", display_name="Inventory", qualified_name="Step::Inventory",
                          executes_at="egeria-adaptive", re_analysis_step="filesystem_inventory",
                          supported_technology_type="File System Directory")])
    with patch.object(SurveyDefinitionReader, "find_process_guid_by_name", autospec=True,
                      side_effect=lambda self, name, *a, **k: "guid-fs-process" if name == FS_DEFINITION else ""), \
            patch.object(SurveyDefinitionReader, "fetch", autospec=True,
                         side_effect=lambda self, guid, *a, **k: definition):
        yield fs_world


def _enqueue_fs_press(reg, run_id: str) -> tuple[str, str]:
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.workflows.survey_definition import SurveyDefinitionRunParams

    activity_id = log_survey(reg, entity_type="filesystem", entity_slug="lake", entity_name="lake",
                             entity_location="", intent="assessment", status="running", summary="running",
                             detail="{}")
    params = SurveyDefinitionRunParams(survey_definition_ref=FS_DEFINITION, engine_override="resource-explorer",
                                       started_by_person=PRESSER, publish_as_presser=True)
    reg.enqueue_run("survey_definition_run", {"entity_type": "filesystem", "slug": "lake", "params": params.to_dict()},
                    result_ref=activity_id, requested_by=PRESSER, run_id=run_id)
    return run_id, activity_id


def _fs_step_detail(reg, activity_id) -> dict:
    import json

    detail = json.loads(reg.get_activity(activity_id)["detail"])
    steps = detail.get("steps") or []
    assert len(steps) == 1, detail
    return steps[0]["detail"]


def test_a_queued_fs_press_writes_the_filefolder_as_the_presser_and_the_activity_row_says_the_intent(fs_queue,
                                                                                                    fs_factory):
    run_id, activity_id = _enqueue_fs_press(fs_queue, "fs-run-e2e")
    presser_hold.hold(run_id, PRESSER, LIVE_TOKEN)
    ran = _in_a_bare_thread(lambda: run_queue.execute_run(fs_queue.get_run(run_id), fs_queue))
    assert ran["caller_at_start"] is None and ran["acting_at_start"] is None
    # the surveyor's client, built in the adaptive step's publish, was built for the presser and nobody else
    assert fs_factory and {(i.kind, i.user_id, i.token) for i in fs_factory} == {("caller", PRESSER, LIVE_TOKEN)}
    step = _fs_step_detail(fs_queue, activity_id)
    assert step["applying_as_intent"] == f"applying as {PRESSER} from {presser_hold.SOURCE_PRESSER}"
    assert step["publish_state"].startswith("publish failed")          # the test boundary refused the client
    assert LIVE_TOKEN not in fs_queue.get_activity(activity_id)["detail"]
    assert presser_hold.take(run_id) is None                            # released when the run ended


def test_a_queued_fs_press_with_nothing_held_builds_no_client_and_the_activity_row_says_the_words(fs_queue, fs_factory):
    run_id, activity_id = _enqueue_fs_press(fs_queue, "fs-run-e2e-gone")
    _in_a_bare_thread(lambda: run_queue.execute_run(fs_queue.get_run(run_id), fs_queue))
    assert fs_factory == []
    step = _fs_step_detail(fs_queue, activity_id)
    assert step["publish_state"] == SIGN_IN_EXPIRED_WORDS and "applying_as_intent" not in step


def test_a_token_that_expires_mid_publish_says_the_runs_own_words(fs_world, monkeypatch):
    """Finding 6: the presser's token ageing out DURING the FileFolder publish ends with the run's own publish words
    and its partial-write note, never "publish failed"."""
    from resource_explorer.egeria_clients import CallerTokenExpired
    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as efs

    def expire(self, *a, **k):
        raise RuntimeError("a write failed") from CallerTokenExpired()

    monkeypatch.setattr(efs.EgeriaFileSystemSurveyor, "catalog_and_survey", expire)
    monkeypatch.setattr(efs.EgeriaFileSystemSurveyor, "__init__", lambda self, *a, **k: None)
    presser_hold.hold("fs-run-x", PRESSER, LIVE_TOKEN)
    with _as_queue_worker(), presser_hold.pressed_run("fs-run-x", PRESSER):
        outcome = _adaptive(fs_world)
    assert outcome["publish_state"] == SIGN_IN_EXPIRED_WORDS
    assert outcome["publish_note"] == presser_hold.PARTIAL_WRITE_NOTE
