"""Re-attach Egeria pointers by qualifiedName (resource_explorer/egeria_reattach.py; brief RE-REATTACH-BY-QN
2026-11-12).

Only temp SQLite (REGISTRY_DATABASE_URL is a tmp file in every test; the shared registry is never named). Egeria is
a `create_autospec(MetadataExpert, spec_set=True)` fake: every call binds the real pyegeria signature. Its answers
take their SHAPE from captures: the by-GUID answer is tests/fixtures/egeria_6_2/
metadata_expert_by_guid__product_zones_anchors.json with its GUID, type and qualifiedName replaced; the miss is the
captured "No elements found" (…memento_original_qn.json); the duplicate is pyegeria's real 404-002 exception
(tests/qn_lookup_bridge.py). Postgres is exercised only through the URL and current_schema fakes, as the clear
script's suite does.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from pyegeria import MetadataExpert
from pyegeria.core._exceptions import PyegeriaConnectionException, PyegeriaNotFoundException

from resource_explorer import egeria_reattach as R
from resource_explorer.registry import ProjectRegistry
from tests.qn_lookup_bridge import multiple_entities_404
from tests.test_lookup_failure_round2 import _egeria_error

FIX = Path(__file__).parent / "fixtures" / "egeria_6_2"
NOW = datetime(2026, 11, 12, 15, 0, 0)
TOKEN = "dwolfson/2026-11-12T14:55Z"

LIVE_QN = "JulesKeeper::Project::First30DaysLearningPhase"
GONE_QN = "Project::Gone::OldInvestigation"
DUP_QN = "Project::Twice"
TAKEN_QN = "Project::NowAGlossary"
FLAKY_QN = "Project::Flaky"
SET_QN = "Collection::WorkingSet::Folio1"
GAP_QN = "GovernanceActionProcess::SurveyDefinition::Repo"

OLD = "11111111-1111-1111-1111-111111111111"           # dead since the clear
NEW = "22222222-2222-2222-2222-222222222222"           # the re-seeded project
LIVE_OK = "33333333-3333-3333-3333-333333333333"       # still there, same name
OTHER = "44444444-4444-4444-4444-444444444444"         # still there, now holds another name
SET_NEW = "55555555-5555-5555-5555-555555555555"
GAP_NEW = "66666666-6666-6666-6666-666666666666"
GLOSSARY = "77777777-7777-7777-7777-777777777777"
FLAKY_GUID = "88888888-8888-8888-8888-888888888888"


def _captured(name: str):
    return json.loads((FIX / name).read_text())["answer"]


MISS = _captured("metadata_expert_by_unique_name_for_lineage__memento_original_qn.json")
assert MISS == "No elements found"
_BY_GUID = _captured("metadata_expert_by_guid__product_zones_anchors.json")


def by_guid_answer(guid: str, type_name: str, qn: str, supers=None) -> dict:
    """The captured 6.2 by-GUID answer, re-labelled."""
    a = copy.deepcopy(_BY_GUID)
    a["elementGUID"] = guid
    a["type"]["typeName"] = type_name
    a["type"]["superTypeNames"] = list(supers if supers is not None else ["Referenceable", "OpenMetadataRoot"])
    a["elementProperties"]["propertiesAsStrings"]["qualifiedName"] = qn
    a["elementProperties"]["propertyValueMap"]["qualifiedName"]["primitiveValue"] = qn
    return a


def not_found_404():
    return _egeria_error(PyegeriaNotFoundException, 404, "OMAG-REPOSITORY-HANDLER-404-001",
                         "OMAG-REPOSITORY-HANDLER-404-001 the entity was not found")


class World:
    """What Egeria holds now, and counters of what was asked."""

    def __init__(self):
        self.elements = {  # guid -> (type, supers, qn)
            NEW: ("Project", ["Referenceable", "OpenMetadataRoot"], LIVE_QN),
            LIVE_OK: ("Project", ["Referenceable", "OpenMetadataRoot"], "Project::StillThere"),
            OTHER: ("Project", ["Referenceable", "OpenMetadataRoot"], "Project::Renamed"),
            SET_NEW: ("DigitalProduct", ["Collection", "AuthoredReferenceable", "Referenceable", "OpenMetadataRoot"],
                      SET_QN),
            GAP_NEW: ("GovernanceActionProcess", ["Process", "Referenceable", "OpenMetadataRoot"], GAP_QN),
            GLOSSARY: ("Glossary", ["Referenceable", "OpenMetadataRoot"], TAKEN_QN),
        }
        self.name_calls: list = []
        self.guid_calls: list = []
        self.down = False

    def expert(self):
        fake = create_autospec(MetadataExpert, instance=True, spec_set=True)

        def by_name(name=None, property_name="qualifiedName", body=None, **kw):
            self.name_calls.append(name)
            if self.down or name == FLAKY_QN:
                raise PyegeriaConnectionException()
            if name == DUP_QN:
                raise multiple_entities_404(name)
            for guid, (t, supers, qn) in self.elements.items():
                if qn == name:
                    from tests.qn_lookup_bridge import element_answer
                    return element_answer(guid, t, supers)
            return MISS

        def by_guid(guid=None, *a, **kw):
            self.guid_calls.append(guid)
            if self.down or guid == FLAKY_GUID:
                raise PyegeriaConnectionException()
            if guid in self.elements:
                t, supers, qn = self.elements[guid]
                return by_guid_answer(guid, t, qn, supers)
            raise not_found_404()

        fake.get_metadata_element_by_unique_name.side_effect = by_name
        fake.get_metadata_element_by_guid.side_effect = by_guid
        return fake

    def reads(self):
        return R.EgeriaReads(self.expert())


def ins(reg, table, **cols):
    with reg._conn() as c:
        c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                  list(cols.values()))


def seed(reg):
    ctx = "entity_egeria_project_context"
    # state (b): what the clear script leaves, an empty GUID beside the name, status unbound (3 copies of one name)
    for slug in ("r1", "r2", "r3"):
        ins(reg, ctx, entity_type="repo", entity_slug=slug, user_id="", status="unbound",
            egeria_project_guid="", egeria_project_qualified_name=LIVE_QN)
    # state (a): a dead GUID, still `linked` (tonight before the clear)
    ins(reg, ctx, entity_type="repo", entity_slug="r4", user_id="", status="linked",
        egeria_project_guid=OLD, egeria_project_qualified_name=LIVE_QN)
    ins(reg, ctx, entity_type="database", entity_slug="d1", user_id="", status="unbound",
        egeria_project_guid="", egeria_project_qualified_name=GONE_QN)
    ins(reg, ctx, entity_type="database", entity_slug="d2", user_id="", status="linked",
        egeria_project_guid=LIVE_OK, egeria_project_qualified_name="Project::StillThere")
    ins(reg, ctx, entity_type="database", entity_slug="d3", user_id="", status="linked",
        egeria_project_guid=OTHER, egeria_project_qualified_name="Project::WasThis")
    ins(reg, ctx, entity_type="file_system", entity_slug="f1", user_id="", status="unbound",
        egeria_project_guid="", egeria_project_qualified_name=DUP_QN)
    ins(reg, ctx, entity_type="file_system", entity_slug="f2", user_id="", status="unbound",
        egeria_project_guid="", egeria_project_qualified_name=TAKEN_QN)
    ins(reg, ctx, entity_type="file_system", entity_slug="f3", user_id="", status="unbound",
        egeria_project_guid="", egeria_project_qualified_name=FLAKY_QN)
    ins(reg, ctx, entity_type="file_system", entity_slug="f4", user_id="", status="linked",
        egeria_project_guid=FLAKY_GUID, egeria_project_qualified_name=LIVE_QN)
    ins(reg, ctx, entity_type="repo", entity_slug="r5", user_id="", status="deferred",
        egeria_project_guid="", egeria_project_qualified_name=LIVE_QN)
    ins(reg, ctx, entity_type="repo", entity_slug="r6", user_id="", status="linked",
        egeria_project_guid=OLD, egeria_project_qualified_name="")
    ins(reg, ctx, entity_type="repo", entity_slug="r7", user_id="", status="personal")          # nothing stored
    ins(reg, "investigations", slug="inv1", display_name="I1", created_at="2026-09-01", egeria_project_guid="",
        egeria_project_qualified_name=LIVE_QN, egeria_project_status="unbound")
    ins(reg, "working_sets", slug="ws1", display_name="W1", created_at="2026-09-01", egeria_collection_guid=OLD,
        egeria_collection_qualified_name=SET_QN)
    ins(reg, "survey_definition_cache", entity_type="repo", entity_slug="r1", technology_type="t", process_guid=OLD,
        process_qualified_name=GAP_QN, cached_at="2026-09-01")
    ins(reg, "projects", slug="p1", display_name="P1", github_url="https://x/p1", created_at="2026-09-01",
        egeria_asset_guid=OLD)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "reattach_test.db"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("PGVECTOR_PORT", "1")
    reg = ProjectRegistry(db_path=str(db))
    seed(reg)
    world = World()
    return {"reg": reg, "dir": tmp_path / "plans", "name": "reattach_test.db", "world": world}


class Cap:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append(str(s))

    @property
    def text(self):
        return "\n".join(self.lines)


def dry(env, *extra):
    cap = Cap()
    rc = R.run(["--out-dir", str(env["dir"]), *extra], out=cap, now=NOW, reads_factory=env["world"].reads)
    h = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan hash: ")), "")
    f = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan file: ")), "")
    return rc, cap, h, f


def apply(env, h, f, **over):
    args = {"--database": env["name"], "--reattached-by": TOKEN, "--plan-hash": h, "--plan-file": f}
    args.update(over)
    argv = ["--out-dir", str(env["dir"]), "--apply"]
    for k, v in args.items():
        if v is not None:
            argv += [k, v]
    cap = Cap()
    rc = R.run(argv, out=cap, now=NOW, reads_factory=env["world"].reads)
    return rc, cap


def plan_of(env) -> dict:
    with env["reg"]._conn() as c:
        return R.build_plan(c, env["world"].reads(), "d", "main", "main", R.check_schema(c))


def row(plan, table, **key):
    t = next(t for t in plan["tables"] if t["table"] == table)
    return next(r for r in t["rows"] if all(r["key"][k] == v for k, v in key.items()))


def ctx(plan, slug):
    return row(plan, "entity_egeria_project_context", entity_slug=slug)


def dump(reg, table, cols):
    with reg._conn() as c:
        return [dict(r) for r in c.execute(f"SELECT {cols} FROM {table} ORDER BY 1, 2").fetchall()]


# ── verdicts ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_each_verdict(env):
    p = plan_of(env)
    assert ctx(p, "r1")["verdict"] == R.REATTACH and ctx(p, "r1")["new_guid"] == NEW
    assert ctx(p, "d1")["verdict"] == R.MISSING
    assert ctx(p, "d2")["verdict"] == R.OK
    assert ctx(p, "d3")["verdict"] == R.MISMATCH and "Project::Renamed" in ctx(p, "d3")["detail"]
    assert ctx(p, "f1")["verdict"] == R.AMBIGUOUS
    assert ctx(p, "f2")["verdict"] == R.WRONG_TYPE
    assert ctx(p, "f3")["verdict"] == R.UNREADABLE          # the name read failed
    assert ctx(p, "f4")["verdict"] == R.UNREADABLE          # the GUID read failed: never re-resolved, never missing
    assert ctx(p, "r6")["verdict"] == R.NO_NAME
    assert "r7" not in {r["key"]["entity_slug"] for r in p["tables"][0]["rows"]}   # nothing stored: not a row


def test_both_input_states_reattach_dead_guid_and_empty_guid(env):
    p = plan_of(env)
    assert ctx(p, "r1")["old_guid"] == "" and ctx(p, "r1")["verdict"] == R.REATTACH       # (b)
    assert ctx(p, "r4")["old_guid"] == OLD and ctx(p, "r4")["verdict"] == R.REATTACH      # (a)
    assert ctx(p, "r4")["new_guid"] == NEW


def test_subtypes_pass_the_collection_check_and_the_cache_reattaches_in_state_a(env):
    p = plan_of(env)
    assert row(p, "working_sets", slug="ws1")["new_guid"] == SET_NEW                      # a DigitalProduct
    assert row(p, "survey_definition_cache", entity_slug="r1")["new_guid"] == GAP_NEW


def test_copies_get_the_same_answer_with_one_lookup_per_distinct_name(env):
    w = env["world"]
    p = plan_of(env)
    assert all(ctx(p, s)["new_guid"] == NEW for s in ("r1", "r2", "r3", "r4"))
    assert w.name_calls.count(LIVE_QN) == 1                     # contexts + investigation: same (QN, Project)
    assert len(w.guid_calls) == len(set(w.guid_calls))          # each GUID read once
    assert p["distinct_names_looked_up"] == len(set(w.name_calls))


def test_unreadable_never_writes_and_is_not_counted_missing(env):
    env["world"].down = True
    p = plan_of(env)
    t = p["tables"][0]
    assert t["counts"][R.MISSING] == 0 and t["counts"][R.REATTACH] == 0
    assert t["counts"][R.UNREADABLE] == len(t["rows"]) - t["counts"][R.NO_NAME]
    rc, cap, _, _ = dry(env)
    assert rc == 0 and "to re-attach: 0" in cap.text and "to apply:" not in cap.text


def test_status_moves_unbound_to_linked_only(env):
    p = plan_of(env)
    assert ctx(p, "r1")["status_after"] == "linked"
    assert ctx(p, "r4")["status_after"] == "linked"             # already linked: kept
    assert ctx(p, "r5")["status_after"] == "deferred"           # any other value: kept, and said
    assert "status deferred kept" in ctx(p, "r5")["detail"]
    assert row(p, "investigations", slug="inv1")["status_after"] == "linked"


def test_a_guid_without_a_name_is_its_own_count_never_unresolved(env):
    p = plan_of(env)
    assert R.cannot_reattach_by_name(p) == (1, 1)          # r6 (context); projects.p1 (no name column)
    assert ctx(p, "r6")["verdict"] == R.NO_NAME and "r6" not in env["world"].name_calls
    _, cap, _, _ = dry(env)
    assert ("cannot re-attach by name (a GUID with no stored qualifiedName): 1 in the tables above, "
            "1 in columns that keep no name (left alone)") in cap.text


def test_out_of_scope_columns_are_counted_never_touched(env):
    p = plan_of(env)
    left = {x["table"]: x for x in p["left_alone"]}
    assert left["projects"]["count"] == 1 and left["projects"]["words"] == R.LEFT_NO_NAME
    assert left["architecture_materialized_blueprints"]["words"] == R.LEFT_CACHE


# ── the CLI: dry run, apply, idempotence, guards ─────────────────────────────────────────────────────────────

def test_dry_run_writes_only_a_0600_plan_and_prints_the_apply_line(env):
    before = dump(env["reg"], "entity_egeria_project_context", "entity_type, entity_slug, egeria_project_guid, status")
    rc, cap, h, f = dry(env)
    assert rc == 0, cap.text
    assert dump(env["reg"], "entity_egeria_project_context",
                "entity_type, entity_slug, egeria_project_guid, status") == before
    assert oct(Path(f).stat().st_mode & 0o777) == "0o600"
    assert "to re-attach: 8" in cap.text           # r1-r5, inv1, ws1, the cache row
    hint = next(l for l in cap.text.splitlines() if l.startswith("to apply:"))
    for part in ("--apply", f"--plan-file {f}", f"--plan-hash {h}", f"--database {env['name']}", "--schema main",
                 "--reattached-by <who>/<UTC>"):
        assert part in hint
    assert "distinct names looked up:" in cap.text and "missing · needs republish" in cap.text


def test_show_lists_the_rows_of_one_verdict(env):
    _, cap, _, _ = dry(env, "--show", "ambiguous", "--show", "missing")
    assert "rows with verdict ambiguous · needs a person:" in cap.text and DUP_QN in cap.text
    assert "rows with verdict missing · needs republish:" in cap.text and GONE_QN in cap.text


def test_apply_writes_only_reattach_rows_then_a_second_dry_run_is_empty(env):
    reg = env["reg"]
    _, _, h, f = dry(env)
    rc, cap = apply(env, h, f)
    assert rc == 0, cap.text
    rows = {r["entity_slug"]: r for r in dump(reg, "entity_egeria_project_context",
                                              "entity_slug, egeria_project_guid, status")}
    assert rows["r1"] == {"entity_slug": "r1", "egeria_project_guid": NEW, "status": "linked"}
    assert rows["r4"]["egeria_project_guid"] == NEW and rows["r4"]["status"] == "linked"
    assert rows["r5"]["egeria_project_guid"] == NEW and rows["r5"]["status"] == "deferred"
    for slug, guid in (("d1", ""), ("d3", OTHER), ("f1", ""), ("f2", ""), ("f3", ""), ("f4", FLAKY_GUID),
                       ("r6", OLD)):
        assert rows[slug]["egeria_project_guid"] == guid, slug               # no write on any other verdict
    assert dump(reg, "investigations", "slug, egeria_project_guid, egeria_project_status")[0] == {
        "slug": "inv1", "egeria_project_guid": NEW, "egeria_project_status": "linked"}
    assert dump(reg, "working_sets", "slug, egeria_collection_guid")[0]["egeria_collection_guid"] == SET_NEW
    assert dump(reg, "projects", "slug, egeria_asset_guid")[0]["egeria_asset_guid"] == OLD
    snap = next(env["dir"].glob("egeria-reattach-snapshot-*.json"))
    assert oct(snap.stat().st_mode & 0o777) == "0o600"
    before = {r["key"]["entity_slug"]: r["old_guid"] for r in json.loads(snap.read_text())["rows_before"]
              if r["table"] == "entity_egeria_project_context"}
    assert before["r4"] == OLD and before["r1"] == ""
    with reg._conn() as c:
        act = [dict(r) for r in c.execute("SELECT * FROM activity_log WHERE operation = ?", (R.OPERATION,)).fetchall()]
    assert len(act) == 1 and act[0]["status"] == "ok"
    detail = json.loads(act[0]["detail"])
    assert detail["plan_hash"] == h and detail["reattached_by"] == TOKEN
    assert detail["counts"]["entity_egeria_project_context"]["reattach"] == 5
    # idempotent: the second dry run shows them ok and nothing to re-attach
    rc, cap2, _, _ = dry(env)
    assert "to re-attach: 0" in cap2.text
    p = plan_of(env)
    assert ctx(p, "r1")["verdict"] == R.OK and ctx(p, "r4")["verdict"] == R.OK


def test_the_plan_hash_refuses_after_a_registry_change(env):
    _, _, h, f = dry(env)
    with env["reg"]._conn() as c:
        c.execute("UPDATE entity_egeria_project_context SET egeria_project_qualified_name = ? "
                  "WHERE entity_slug = 'd1'", (LIVE_QN,))
    rc, cap = apply(env, h, f)
    assert rc == 2 and "changed since the dry run" in cap.text
    assert dump(env["reg"], "investigations", "slug, egeria_project_guid")[0]["egeria_project_guid"] == ""


def test_an_edited_or_old_or_foreign_plan_file_is_refused(env):
    _, _, h, f = dry(env)
    data = json.loads(Path(f).read_text())
    data["plan"]["tables"][0]["rows"][0]["new_guid"] = OTHER
    Path(f).write_text(json.dumps(data))
    rc, cap = apply(env, h, f)
    assert rc == 2 and "edited" in cap.text
    _, _, h, f = dry(env)
    rc, cap = apply(env, h, f, **{"--database": "other"})
    assert rc == 2 and "--database" in cap.text
    rc, cap = apply(env, h, f, **{"--reattached-by": "nobody"})
    assert rc == 2 and "--reattached-by must be" in cap.text
    data = json.loads(Path(f).read_text())
    data["generated_at"] = "2026-11-12T13:00:00"
    Path(f).write_text(json.dumps(data))
    rc, cap = apply(env, h, f)
    assert rc == 2 and "older than 15 minutes" in cap.text


def test_the_active_writer_refusal(env):
    _, _, h, f = dry(env)
    with env["reg"]._conn() as c:
        c.execute("INSERT INTO runs (id, kind, state, enqueued_at) VALUES ('run-1', 'survey', 'running', '2026-11-12')")
    rc, cap = apply(env, h, f)
    assert rc == 2 and "looks active" in cap.text and "runs row run-1 is running" in cap.text
    assert dump(env["reg"], "investigations", "slug, egeria_project_guid")[0]["egeria_project_guid"] == ""


def test_an_egeria_client_that_cannot_open_is_named_without_a_traceback(env):
    cap = Cap()

    def boom():
        raise RuntimeError("secretvalue")

    assert R.run(["--out-dir", str(env["dir"])], out=cap, now=NOW, reads_factory=boom) == 2
    assert "RuntimeError" in cap.text and "secretvalue" not in cap.text and "Traceback" not in cap.text


def test_a_lazy_table_not_created_yet_is_reported_not_refused(env):
    with env["reg"]._conn() as c:
        c.execute("DROP TABLE work_list_runs")
        c.execute("DROP TABLE work_list_members")
        c.execute("DROP TABLE work_lists")
    rc, cap, _, _ = dry(env)
    assert rc == 0 and "table not created yet" in cap.text


# ── Postgres: the schema guard (fakes; SQLite cannot show current_schema) ─────────────────────────────────────

def test_a_postgres_url_without_a_schema_is_refused(monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", "postgresql://u:secret@localhost:1/egeria_advisor")
    cap = Cap()
    assert R.run([], out=cap, now=NOW, reads_factory=lambda: None) == 2
    assert "names no schema" in cap.text and "secret" not in cap.text


def test_a_postgres_connection_in_another_schema_is_refused(monkeypatch):
    class FakeConn:
        def execute(self, sql, params=None):
            assert "current_schema" in sql
            return self

        def fetchone(self):
            return {"s": "public"}

        def close(self):
            pass

    monkeypatch.setenv("REGISTRY_DATABASE_URL",
                       "postgresql://u:secret@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer")
    monkeypatch.setattr(R, "connect", lambda url: FakeConn())
    cap = Cap()
    assert R.run([], out=cap, now=NOW, reads_factory=lambda: None) == 2
    assert "current_schema() is 'public'" in cap.text and "secret" not in cap.text


def test_apply_needs_the_schema_named_again_on_postgres(monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL",
                       "postgresql://u:secret@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer")
    cap = Cap()
    rc = R.run(["--apply", "--database", "egeria_advisor", "--reattached-by", TOKEN, "--plan-file", "x",
                "--plan-hash", "y"], out=cap, now=NOW, reads_factory=lambda: None)
    assert rc == 2 and "--schema" in cap.text and "secret" not in cap.text


# ── Admin → Egeria Alignment ─────────────────────────────────────────────────────────────────────────────────

def test_alignment_order_and_not_unattended():
    from resource_explorer import egeria_resync as E

    steps = list(E.REPAIR_STEPS)
    assert steps.index("reattach_by_name") < steps.index("clear_stale_investigations")
    assert steps.index("reattach_by_name") < steps.index("clear_stale_contexts")
    assert "reattach_by_name" not in E.SAFE_SCHEDULED_STEPS
    assert "reattach_by_name" not in E.EXPENSIVE_STEPS
    f = E.Finding(key="k", title="t", detail="d", items=[1], repair_step="reattach_by_name", plan_hash="abc")
    assert f.as_dict()["unticked"] is True and f.as_dict()["plan_hash"] == "abc"
    assert E.Finding(key="k", title="t", detail="d", repair_step="catalog_assets").as_dict()["unticked"] is False


def _resync(env):
    from resource_explorer.egeria_resync import EgeriaResync

    r = EgeriaResync(registry=env["reg"])
    r._clients = {"expert": env["world"].expert()}
    r._connect = lambda: (True, "")
    return r


def test_alignment_scan_carries_the_counts_and_the_plan_hash_and_never_writes(env):
    from resource_explorer.egeria_resync import ScanResult

    before = dump(env["reg"], "entity_egeria_project_context", "entity_slug, egeria_project_guid, status")
    res = ScanResult()
    f = _resync(env)._scan_reattach_by_name(res)
    assert f.count == 8 and f.plan_hash and f.repair_step == "reattach_by_name"
    assert "rewrites links by name" in f.detail.lower() and "missing · needs republish 1" in f.detail
    assert any(u["kind"] == "re-attach by name" for u in res.undetermined)   # the unreadable rows
    assert dump(env["reg"], "entity_egeria_project_context", "entity_slug, egeria_project_guid, status") == before


def test_alignment_apply_refuses_without_or_with_a_stale_hash_then_applies(env):
    from resource_explorer.egeria_resync import ScanResult

    r = _resync(env)
    f = r._scan_reattach_by_name(ScanResult())
    out = r.apply(["reattach_by_name"])["applied"]["reattach_by_name"]
    assert out["reattached"] == 0 and "scan again" in out["refused"]
    with env["reg"]._conn() as c:
        c.execute("UPDATE entity_egeria_project_context SET egeria_project_qualified_name = ? "
                  "WHERE entity_slug = 'd1'", (LIVE_QN,))
    out = r.apply(["reattach_by_name"], {"reattach_by_name": f.plan_hash})["applied"]["reattach_by_name"]
    assert out == {"reattached": 0, "refused": "something changed since the scan; scan again"}
    f = r._scan_reattach_by_name(ScanResult())
    out = r.apply(["reattach_by_name"], {"reattach_by_name": f.plan_hash})["applied"]["reattach_by_name"]
    assert out["reattached"] == 9 and Path(out["snapshot"]).exists()
    assert r._scan_reattach_by_name(ScanResult()).count == 0


def test_alignment_apply_refuses_while_another_writer_is_active(env):
    from resource_explorer.egeria_resync import ScanResult

    r = _resync(env)
    f = r._scan_reattach_by_name(ScanResult())
    with env["reg"]._conn() as c:
        c.execute("INSERT INTO runs (id, kind, state, enqueued_at) VALUES ('run-9', 'survey', 'claimed', '2026-11-12')")
    out = r.apply(["reattach_by_name"], {"reattach_by_name": f.plan_hash})["applied"]["reattach_by_name"]
    assert out["reattached"] == 0 and "run-9" in out["refused"]


def test_the_unattended_pass_does_not_read_for_reattach(env, monkeypatch):
    from resource_explorer import egeria_resync as E

    called = []
    monkeypatch.setattr(E.EgeriaResync, "_connect", lambda self: (True, ""))
    monkeypatch.setattr(E.EgeriaResync, "_scan_reattach_by_name",
                        lambda self, res: called.append(1) or E.Finding(key="k", title="t", detail="d"))
    for name in [n for n in dir(E.EgeriaResync) if n.startswith("_scan_") and n != "_scan_reattach_by_name"]:
        monkeypatch.setattr(E.EgeriaResync, name, lambda self, *a: E.Finding(key="k", title="t", detail="d"))
    E.scan_and_clear(env["reg"])
    assert called == []
    E.EgeriaResync(registry=env["reg"]).scan()
    assert called == [1]


# ── the executor: a cached Survey Definition GUID is checked before use ──────────────────────────────────────

class _Reader:
    def __init__(self, world):
        self.world = world
        self.cold = 0

    def _connect_metadata_expert(self):
        return self.world.expert()

    def find_candidate_process_guids(self, technology_type):
        self.cold += 1
        return [{"guid": GAP_NEW, "qualified_name": GAP_QN}]


def _executor(env):
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    ex = SurveyDefinitionExecutor.__new__(SurveyDefinitionExecutor)
    ex.registry, ex.reader = env["reg"], _Reader(env["world"])
    return ex


def _cache(env, guid, qn=GAP_QN):
    env["reg"].set_survey_definition_guid("repo", "r1", "t", guid, qn)


def test_executor_uses_a_live_cached_guid(env):
    ex = _executor(env)
    _cache(env, GAP_NEW)
    assert ex._resolve_process_guid("repo", "r1", "t", None, False) == (GAP_NEW, GAP_QN)
    assert ex.reader.cold == 0


def test_executor_re_resolves_a_dead_cached_guid_by_name(env):
    ex = _executor(env)
    _cache(env, OLD)
    assert ex._resolve_process_guid("repo", "r1", "t", None, False) == (GAP_NEW, GAP_QN)
    assert env["reg"].get_survey_definition_guid("repo", "r1", "t")["process_guid"] == GAP_NEW
    assert ex.reader.cold == 0


def test_executor_drops_the_row_and_rebuilds_cold_on_a_miss(env):
    ex = _executor(env)
    _cache(env, OLD, "GovernanceActionProcess::Gone")
    assert ex._resolve_process_guid("repo", "r1", "t", None, False) == (GAP_NEW, GAP_QN)
    assert ex.reader.cold == 1
    assert env["reg"].get_survey_definition_guid("repo", "r1", "t")["process_qualified_name"] == GAP_QN


def test_executor_never_runs_with_a_guid_it_could_not_check(env):
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutorError

    ex = _executor(env)
    _cache(env, OLD)
    env["world"].down = True
    with pytest.raises(SurveyDefinitionExecutorError, match="Could not check the cached"):
        ex._resolve_process_guid("repo", "r1", "t", None, False)
    assert env["reg"].get_survey_definition_guid("repo", "r1", "t")["process_guid"] == OLD


# ── the manual clear steps keep the name, so a re-attach can still recover the row (Architect read-back) ──────

def test_the_default_ticked_clear_steps_keep_the_name_and_reattach_then_recovers(env):
    from unittest.mock import MagicMock

    from resource_explorer.egeria_resync import ScanResult

    w = env["world"]
    ins(env["reg"], "investigations", slug="inv2", display_name="I2", created_at="2026-09-01",
        egeria_project_guid=OLD, egeria_project_qualified_name=LIVE_QN, egeria_project_status="linked")

    def get_by_guid(guid, *a, **k):
        if w.down or guid == FLAKY_GUID:
            raise PyegeriaConnectionException()
        if guid in w.elements:
            return {"elementHeader": {"guid": guid}}
        raise not_found_404()

    r = _resync(env)
    r._clients.update(project=MagicMock(get_project_by_guid=MagicMock(side_effect=get_by_guid)),
                      collection=MagicMock(get_collection_by_guid=MagicMock(side_effect=get_by_guid)))
    # what "Scan -> Apply with the defaults" runs: both clear steps, without the unticked re-attach
    out = r.apply(["clear_stale_investigations", "clear_stale_contexts"])["applied"]
    assert out["clear_stale_contexts"]["contexts_cleared"] == 2          # r4, r6 (dead OLD); f4 undetermined
    reg = env["reg"]
    r4 = next(x for x in dump(reg, "entity_egeria_project_context",
                              "entity_slug, egeria_project_guid, egeria_project_qualified_name, status")
              if x["entity_slug"] == "r4")
    assert r4 == {"entity_slug": "r4", "egeria_project_guid": "", "egeria_project_qualified_name": LIVE_QN,
                  "status": "unbound"}
    inv2 = next(x for x in dump(reg, "investigations",
                                "slug, egeria_project_guid, egeria_project_qualified_name, egeria_project_status")
                if x["slug"] == "inv2")
    assert inv2 == {"slug": "inv2", "egeria_project_guid": "", "egeria_project_qualified_name": LIVE_QN,
                    "egeria_project_status": "unbound"}
    ws = dump(reg, "working_sets", "slug, egeria_collection_guid, egeria_collection_qualified_name")[0]
    assert ws == {"slug": "ws1", "egeria_collection_guid": "", "egeria_collection_qualified_name": SET_QN}
    # f4's GUID could not be checked: untouched (unreachable is never stale)
    assert ctx(plan_of(env), "f4")["old_guid"] == FLAKY_GUID
    # the re-attach recovers every one of them
    f = r._scan_reattach_by_name(ScanResult())
    applied = r.apply(["reattach_by_name"], {"reattach_by_name": f.plan_hash})["applied"]["reattach_by_name"]
    assert applied["reattached"] >= 3
    p = plan_of(env)
    assert ctx(p, "r4")["verdict"] == R.OK and row(p, "investigations", slug="inv2")["verdict"] == R.OK
    assert row(p, "working_sets", slug="ws1")["verdict"] == R.OK
    assert dump(reg, "investigations", "slug, egeria_project_status")[1]["egeria_project_status"] == "linked"
    assert next(x for x in dump(reg, "entity_egeria_project_context", "entity_slug, status")
                if x["entity_slug"] == "r4")["status"] == "linked"


def test_every_applied_alignment_step_writes_one_audit_row(env):
    from unittest.mock import MagicMock

    from resource_explorer.egeria_resync import EgeriaResync, ScanResult

    def get_by_guid(guid, *a, **k):
        if guid in env["world"].elements:
            return {"elementHeader": {"guid": guid}}
        raise not_found_404()

    r = _resync(env)
    r._clients.update(project=MagicMock(get_project_by_guid=MagicMock(side_effect=get_by_guid)),
                      collection=MagicMock(get_collection_by_guid=MagicMock(side_effect=get_by_guid)))

    def audit_rows():
        with env["reg"]._conn() as c:
            return [dict(x) for x in c.execute(
                "SELECT entity_slug, status, summary, detail FROM activity_log WHERE operation = ? ORDER BY ts, id",
                (EgeriaResync.AUDIT_OPERATION,)).fetchall()]

    r.apply(["reattach_by_name"])                                        # no hash: refused, and recorded
    f = r._scan_reattach_by_name(ScanResult())
    r.apply(["clear_stale_contexts", "reattach_by_name"], {"reattach_by_name": f.plan_hash})
    rows = audit_rows()
    assert [x["entity_slug"] for x in rows].count("reattach_by_name") == 2
    assert [x["entity_slug"] for x in rows].count("clear_stale_contexts") == 1
    refused = next(x for x in rows if x["entity_slug"] == "reattach_by_name" and x["status"] == "refused")
    assert json.loads(refused["detail"])["counts"] == {"reattached": 0}
    done = [x for x in rows if x["entity_slug"] == "reattach_by_name" and x["status"] == "ok"]
    assert len(done) == 1
    d = json.loads(done[0]["detail"])
    assert d["step"] == "reattach_by_name" and d["plan_hash"] == f.plan_hash and d["counts"]["reattached"] == 8
    assert d["applied_by"] and "reattached 8" in done[0]["summary"]
    cleared = json.loads(next(x for x in rows if x["entity_slug"] == "clear_stale_contexts")["detail"])
    assert cleared["counts"]["contexts_cleared"] == 2 and cleared["plan_hash"] == ""
