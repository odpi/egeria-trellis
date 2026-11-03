"""A queued run publishes AS the person who pressed it (architect ruling, option a, 2026-10-21).

Only the pyegeria boundary is faked (`create_autospec(AssetMaker, spec_set=True)`); the identity machinery
(`egeria_clients`, `a2a_auth`, `presser_hold`, `run_queue`, the executor) is real, so a regression that makes the
publish act as the service account fails here. Every token and name is invented. A token is a real unsigned-claims JWT
only because RE reads its `exp` the same way (`trellis_auth.egeria_token_expiry`, unverified).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import jwt
import pytest

from resource_explorer import presser_hold, run_queue
from resource_explorer.a2a_auth import CallerIdentity
from resource_explorer.egeria_clients import (
    Daemon,
    DaemonReason,
    acting_as,
    current_principal,
)
from resource_explorer.presser_hold import SIGN_IN_EXPIRED_WORDS, PresserSignInExpired
from resource_explorer.surveyors import survey_definition_executor as sde
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep

PRESSER = "peterprofile"
DAEMON_NAME = "resourceexplorernpa"


def _token(exp_in: float, marker: str = "alpha") -> str:
    return jwt.encode({"sub": PRESSER, "exp": int(time.time() + exp_in), "marker": marker}, "k", algorithm="HS256")


LIVE_TOKEN = _token(3600, "live-session-token")
EXPIRED_TOKEN = _token(-60, "expired-session-token")


@pytest.fixture(autouse=True)
def _clean_hold():
    presser_hold.clear_all()
    yield
    presser_hold.clear_all()


# ── the hold ────────────────────────────────────────────────────────────────

def test_the_token_is_in_no_text_form_of_the_hold():
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    held = presser_hold.take("run-1")
    assert held is not None and held.token == LIVE_TOKEN
    for text in (repr(held), str(held), repr(presser_hold._HOLDER)):
        assert LIVE_TOKEN not in text and "live-session-token" not in text


def test_nothing_is_held_without_a_run_a_person_or_a_token():
    assert presser_hold.hold("", PRESSER, LIVE_TOKEN) is False
    assert presser_hold.hold("run-1", "", LIVE_TOKEN) is False
    assert presser_hold.hold("run-1", PRESSER, None) is False
    assert presser_hold.take("run-1") is None


def test_a_held_session_ends_with_the_run_at_the_tokens_expiry_and_at_the_cap(monkeypatch):
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    presser_hold.release("run-1")
    assert presser_hold.take("run-1") is None
    # the token's own exp bounds the hold (an hour token is not kept for the 2 h cap)
    presser_hold.hold("run-2", PRESSER, _token(30))
    assert presser_hold.take("run-2").expires_at <= time.time() + 31
    # the cap bounds a token with no exp
    monkeypatch.setenv(presser_hold.TTL_ENV, "5")
    presser_hold.hold("run-3", PRESSER, "opaque-token-without-exp")
    assert presser_hold.take("run-3").expires_at <= time.time() + 6
    # a hold past its time is gone
    clock = [1000.0]
    holder = presser_hold._Holder(clock=lambda: clock[0])
    holder.put("r", PRESSER, LIVE_TOKEN, ttl=10)
    clock[0] = 1011.0
    assert holder.take("r") is None


# ── who the publish acts as ─────────────────────────────────────────────────

def _worker_as_daemon(row_requester=PRESSER):
    return acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=row_requester))


def test_a_pressed_run_acts_as_the_presser_inside_the_publish_and_as_the_daemon_outside_it():
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        assert current_principal().kind == "daemon"                      # the run's measuring stays the daemon's
        with presser_hold.publishing_as_presser():
            who = current_principal()
            assert (who.kind, who.user_id, who.token, who.is_service_account) == ("caller", PRESSER, LIVE_TOKEN, False)
        assert current_principal().kind == "daemon"                      # and it comes back


def test_a_run_nobody_pressed_publishes_as_the_service_account():
    with _worker_as_daemon(row_requester=None):
        with presser_hold.publishing_as_presser():
            who = current_principal()
        assert who.kind == "daemon" and who.is_service_account and who.token is None


def test_a_pressed_run_with_nothing_held_refuses_before_the_block_runs():
    ran = []
    with _worker_as_daemon(), presser_hold.pressed_run("run-never-held", PRESSER):
        with pytest.raises(PresserSignInExpired) as raised:
            with presser_hold.publishing_as_presser():
                ran.append("wrote")
    assert ran == [] and str(raised.value) == SIGN_IN_EXPIRED_WORDS


def test_an_expired_token_and_another_persons_hold_both_refuse():
    presser_hold._HOLDER.put("run-1", PRESSER, EXPIRED_TOKEN, ttl=3600)
    presser_hold.hold("run-2", "someone_else", LIVE_TOKEN)
    for run_id in ("run-1", "run-2"):
        with _worker_as_daemon(), presser_hold.pressed_run(run_id, PRESSER):
            with pytest.raises(PresserSignInExpired):
                with presser_hold.publishing_as_presser():
                    raise AssertionError("must not run")


def test_the_words_are_the_agreed_sentence():
    assert SIGN_IN_EXPIRED_WORDS == "published: no · your sign-in expired · press Publish on the card"


# ── the executor: the real publish seam ─────────────────────────────────────

def _run_definition(publish, *, hold_for=None):
    """One real `SurveyDefinitionExecutor.run` over a fake 'fake_qid' adapter whose publish is `publish`."""
    adapter = sde.ResourceTypeAdapter(
        entity_type="fake_qid", technology_type="Fake Qid Tech",
        re_analysis_steps={"known_step": MagicMock(return_value={"ok": True})},
        get_entity=lambda registry, slug: object(), publish=publish)
    sde.register_adapter(adapter)
    survey_def = SurveyDefinition(
        process_guid="proc-1", display_name="Fake", qualified_name="GovActionProcess::Fake",
        supported_technology_type="Fake Qid Tech",
        steps=[SurveyStep(guid="s1", display_name="Known", qualified_name="Step::Known",
                          executes_at="resource-explorer", re_analysis_step="known_step")])
    reader = MagicMock()
    reader.fetch.return_value = survey_def
    reader.find_candidate_process_guids.return_value = [
        {"guid": "proc-1", "qualified_name": "GovActionProcess::Fake", "display_name": "Fake"}]
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None
    registry.has_assigned_egeria_project.return_value = True
    executor = sde.SurveyDefinitionExecutor(registry, reader=reader)
    return executor.run(entity_type="fake_qid", slug="my-fake")


@pytest.fixture
def no_write_refusal(monkeypatch):
    monkeypatch.setattr("resource_explorer.workflows.curate.person_write_refusal", lambda *a, **k: "")


def test_a_pressed_runs_publish_calls_the_port_under_the_presser_never_the_daemon(no_write_refusal, monkeypatch):
    seen = []

    def publish(entity, outputs, at, registry):
        seen.append(current_principal())
        return "report-guid-1"

    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: PRESSER)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(publish)
    assert len(seen) == 1
    assert (seen[0].kind, seen[0].user_id, seen[0].token) == ("caller", PRESSER, LIVE_TOKEN)
    assert not seen[0].is_service_account
    assert result["egeria_report_guid"] == "report-guid-1" and result["published"] is True
    assert result["published_as"] == PRESSER


def test_an_automated_run_publishes_as_the_service_account(no_write_refusal, monkeypatch):
    seen = []

    def publish(entity, outputs, at, registry):
        seen.append(current_principal())
        return "report-guid-2"

    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: DAEMON_NAME)
    with _worker_as_daemon(row_requester=None):                           # scheduled / dispatched: no person, no flag
        result = _run_definition(publish)
    assert len(seen) == 1 and seen[0].kind == "daemon" and seen[0].is_service_account and seen[0].token is None
    assert result["published"] is True and result["published_as"] == DAEMON_NAME


@pytest.mark.parametrize("held", ["nothing", "expired"])
def test_an_expired_or_absent_session_writes_nothing_and_says_so(held, no_write_refusal, monkeypatch):
    calls = []
    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: calls.append(guid) or "x")
    if held == "expired":
        presser_hold._HOLDER.put("run-1", PRESSER, EXPIRED_TOKEN, ttl=3600)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(lambda *a, **k: calls.append("PUBLISHED AS " + current_principal().user_id) or "g")
    assert calls == []                                                    # no publish, as anyone; no read-back either
    assert result["publish_state"] == SIGN_IN_EXPIRED_WORDS
    assert result["published"] is False and result["egeria_report_guid"] == ""
    assert "published_as" not in result
    assert result["errors"] == []                                         # the measuring succeeded; nothing failed


def test_published_as_comes_from_the_read_back_not_from_the_path_the_code_took(no_write_refusal, monkeypatch):
    """The regression this exists to show: a pressed run whose report Egeria says the daemon created."""
    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: DAEMON_NAME)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(lambda *a, **k: "report-guid-3")
    assert result["published_as"] == DAEMON_NAME                          # shown as it is, so the card shows the fault
    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: "")
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        unread = _run_definition(lambda *a, **k: "report-guid-3")
    assert "published_as" not in unread                                   # could not tell: said as nothing


# ── the read-back itself ────────────────────────────────────────────────────

def test_the_read_back_is_one_get_by_guid_and_reads_versions_created_by(monkeypatch):
    from pyegeria import AssetMaker

    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.get_asset_by_guid.return_value = {"elementHeader": {"guid": "g", "versions": {"createdBy": PRESSER}}}
    clients = MagicMock()
    clients.of.return_value = maker
    seen = {}

    def fake_client(identity, *, purpose, view_server=None, platform_url=None, shared=True):
        seen["identity"], seen["purpose"] = identity, purpose
        return clients

    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client", fake_client)
    with _worker_as_daemon():
        assert presser_hold.read_published_by("g") == PRESSER
    maker.get_asset_by_guid.assert_called_once()
    assert maker.get_asset_by_guid.call_args.args == ("g",)
    clients.of.assert_called_once_with(AssetMaker)
    assert seen["purpose"] == "publish read-back"


def test_the_read_back_says_nothing_when_it_cannot_tell(monkeypatch):
    assert presser_hold.created_by_of({}) == "" and presser_hold.created_by_of(None) == ""
    assert presser_hold.created_by_of({"elementHeader": {"versions": {}}}) == ""
    assert presser_hold.created_by_of({"versions": {"createdBy": "top"}}) == "top"
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom " + LIVE_TOKEN)))
    with _worker_as_daemon():
        assert presser_hold.read_published_by("g") == ""
    assert presser_hold.read_published_by("") == ""


# ── the queue ───────────────────────────────────────────────────────────────

def _row(run_id, registry_target):
    return {"id": run_id, "kind": "survey_definition_run", "target": json.dumps(registry_target),
            "requested_by": PRESSER, "result_ref": "", "claimed_by": "w"}


def _stub_execute(monkeypatch, record):
    def fake(entity_type, slug, params, activity_id, *, registry=None):
        record["pressed"] = presser_hold.is_pressed()
        record["principal_in_run"] = current_principal()
        try:
            with presser_hold.publishing_as_presser():
                record["principal_in_publish"] = current_principal()
        except PresserSignInExpired as exc:
            record["expired"] = str(exc)
        from resource_explorer.workflows.survey_definition import SurveyDefinitionRunResult

        return SurveyDefinitionRunResult(status="ok")

    monkeypatch.setattr("resource_explorer.workflows.survey_definition.execute_and_record_definition", fake)


def _params(**kw):
    from resource_explorer.workflows.survey_definition import SurveyDefinitionRunParams

    return SurveyDefinitionRunParams(survey_definition_ref="GovActionProcess::X", started_by_person=PRESSER, **kw).to_dict()


def test_the_queue_runs_a_pressed_row_as_the_presser_at_publish_and_releases_the_hold(monkeypatch):
    record = {}
    _stub_execute(monkeypatch, record)
    registry = MagicMock()
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    outcome = run_queue.execute_run(
        _row("run-1", {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=True)}), registry)
    assert outcome.state == "succeeded"
    assert record["pressed"] is True
    assert record["principal_in_run"].kind == "daemon"                    # measuring: the queue's daemon, as before
    assert record["principal_in_publish"].kind == "caller" and record["principal_in_publish"].token == LIVE_TOKEN
    assert presser_hold.take("run-1") is None                             # released when the run ended


def test_a_row_claimed_where_nothing_is_held_ends_with_the_expired_words_not_the_daemon(monkeypatch):
    record = {}
    _stub_execute(monkeypatch, record)
    run_queue.execute_run(
        _row("run-elsewhere", {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=True)}),
        MagicMock())
    assert record["expired"] == SIGN_IN_EXPIRED_WORDS and "principal_in_publish" not in record


def test_a_row_that_is_not_pressed_is_not_touched(monkeypatch):
    record = {}
    _stub_execute(monkeypatch, record)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    run_queue.execute_run(
        _row("run-1", {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=False)}),
        MagicMock())
    assert record["pressed"] is False and record["principal_in_publish"].kind == "daemon"


def test_the_hold_is_released_even_when_the_run_crashes(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr("resource_explorer.workflows.survey_definition.execute_and_record_definition", boom)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    outcome = run_queue.execute_run(
        _row("run-1", {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=True)}),
        MagicMock())
    assert outcome.state == "failed" and presser_hold.take("run-1") is None


# ── the press route: the token is held, never stored ────────────────────────

def test_the_press_holds_the_session_and_the_row_carries_no_token(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: True)
    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / "press.db"))
    registry.register_database(DatabaseEntity(slug="mydb", display_name="My DB", db_type="postgresql", host="localhost",
                                              port=5432, database_name="mydb"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(
        "resource_explorer.a2a_auth.caller",
        lambda: CallerIdentity(user_id=PRESSER, egeria_token=LIVE_TOKEN, auth_source="app-jwt", role="user"))
    from resource_explorer.web.app import app

    caplog.set_level(logging.DEBUG)
    resp = TestClient(app).post("/api/survey-definitions/database/mydb/run",
                                json={"survey_definition_ref": "GovActionProcess::Test"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    held = presser_hold.take(run_id)
    assert held is not None and (held.person, held.token) == (PRESSER, LIVE_TOKEN)
    row = registry.get_run(run_id)
    target = json.loads(row["target"])
    assert target["params"]["publish_as_presser"] is True and target["params"]["started_by_person"] == PRESSER
    # the token (or any piece of it) is in no stored text and no log line
    stored = json.dumps(row, default=str) + json.dumps(registry.get_activity(resp.json()["activity_id"]), default=str)
    for secret in (LIVE_TOKEN, "live-session-token"):
        assert secret not in stored
        assert secret not in caplog.text


def test_a_signed_out_press_is_not_a_pressed_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / "press2.db"))
    registry.register_database(DatabaseEntity(slug="mydb", display_name="My DB", db_type="postgresql", host="localhost",
                                              port=5432, database_name="mydb"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/survey-definitions/database/mydb/run",
                                json={"survey_definition_ref": "GovActionProcess::Test"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    assert presser_hold.take(run_id) is None
    assert json.loads(registry.get_run(run_id)["target"])["params"]["publish_as_presser"] is False


def test_nothing_in_the_source_writes_the_hold_anywhere_but_memory():
    """The module imports no store, opens no file and has no logger: the token has nowhere to go."""
    import ast

    tree = ast.parse(Path(presser_hold.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"sqlite3", "psycopg2", "json", "logging", "pickle", "shelve", "tempfile"}
    called = {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"open", "print", "getLogger", "dump", "dumps"}


# ── fix round 1 (2026-10-21): claim affinity, repr, read as presser, release, expiry mid-publish ──────────────────

HOLDER = {"pid": 222, "started_at": "Wed Oct 21 10:00:00 2026"}
OTHER = {"pid": 111, "started_at": "Wed Oct 21 09:00:00 2026"}


def _queued(registry, held_by=None, *, run_id=None):
    target = {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=True)}
    if held_by is not None:
        target["held_by"] = held_by
    return registry.enqueue_run("survey_definition_run", target, requested_by=PRESSER, run_id=run_id)


@pytest.fixture
def sqlite_registry(tmp_path):
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry(db_path=str(tmp_path / "claim.db"))


def _stamp(**over):
    return {**HOLDER, **presser_hold.host_identity(), "until": time.time() + 3600, **over}


def test_a_second_live_process_skips_a_held_row_and_the_holder_claims_it(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: True)
    run_id = _queued(sqlite_registry, _stamp())
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is None
    assert sqlite_registry.get_run(run_id)["state"] == "queued"
    claimed = sqlite_registry.claim_next_run("holder-worker", HOLDER)
    assert claimed is not None and claimed["id"] == run_id


def test_a_holder_whose_liveness_cannot_be_told_still_keeps_its_row_until_the_hold_ends(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: None)
    _queued(sqlite_registry, _stamp())
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is None


def test_a_dead_holders_row_is_claimable_and_ends_with_the_expired_words(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: False)
    run_id = _queued(sqlite_registry, _stamp())
    row = sqlite_registry.claim_next_run("other-worker", OTHER)
    assert row is not None and row["id"] == run_id
    record = {}
    _stub_execute(monkeypatch, record)
    run_queue.execute_run(row, sqlite_registry)
    assert record["expired"] == SIGN_IN_EXPIRED_WORDS and "principal_in_publish" not in record   # no service-account write


def test_a_row_whose_hold_has_ended_is_claimable_by_anyone(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: True)
    _queued(sqlite_registry, _stamp(until=time.time() - 1))
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is not None


def test_an_unpressed_row_is_claimable_by_anyone(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: True)
    _queued(sqlite_registry, None)
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is not None


def test_a_held_row_at_the_head_does_not_block_the_rows_behind_it(sqlite_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: True)
    held = _queued(sqlite_registry, _stamp())
    free = sqlite_registry.enqueue_run("survey_definition_run", {"entity_type": "database", "slug": "e", "params": {}},
                                       requested_by="someoneelse")
    assert sqlite_registry.claim_next_run("other-worker", OTHER)["id"] == free
    assert sqlite_registry.get_run(held)["state"] == "queued"


def test_the_stamp_the_press_writes_is_the_process_identity_and_holds_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: True)
    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.run_reconciler import process_identity

    registry = ProjectRegistry(db_path=str(tmp_path / "stamp.db"))
    registry.register_database(DatabaseEntity(slug="mydb", display_name="My DB", db_type="postgresql", host="localhost",
                                              port=5432, database_name="mydb"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(
        "resource_explorer.a2a_auth.caller",
        lambda: CallerIdentity(user_id=PRESSER, egeria_token=LIVE_TOKEN, auth_source="app-jwt", role="user"))
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/survey-definitions/database/mydb/run",
                                json={"survey_definition_ref": "GovActionProcess::Test"})
    assert resp.status_code == 200, resp.text
    stored = registry.get_run(resp.json()["run_id"])["target"]
    held_by = json.loads(stored)["held_by"]
    mine = process_identity()
    assert (held_by["pid"], held_by["started_at"]) == (mine["pid"], mine["started_at"]) and held_by["until"] > time.time()
    assert LIVE_TOKEN not in stored and "live-session-token" not in stored


# ── the token is in no repr ─────────────────────────────────────────────────

def test_no_identity_prints_the_token():
    from resource_explorer.egeria_identity import EgeriaIdentity

    caller = CallerIdentity(user_id=PRESSER, egeria_token=LIVE_TOKEN, auth_source="held-session")
    identity = EgeriaIdentity(user_id=PRESSER, token=LIVE_TOKEN, password="pw-secret-9", kind="caller")
    for text in (repr(caller), str(caller), repr(identity), str(identity), repr([caller, identity]),
                 f"{caller!r} {identity!r}"):
        assert LIVE_TOKEN not in text and "live-session-token" not in text and "pw-secret-9" not in text
    assert caller.user_id == PRESSER and identity.token == LIVE_TOKEN        # still carried, just not printed


# ── "published as" is read as the presser ───────────────────────────────────

def test_the_creator_read_is_made_as_the_presser_and_unread_is_blank(monkeypatch):
    seen = []
    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: seen.append(current_principal()) or PRESSER)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        assert presser_hold.read_published_by_presser("g") == PRESSER
    assert len(seen) == 1 and (seen[0].kind, seen[0].user_id, seen[0].token) == ("caller", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-gone", PRESSER):         # session gone: cannot read
        assert presser_hold.read_published_by_presser("g") == ""
    assert presser_hold.read_published_by_presser("") == ""


def test_the_executor_reads_the_creator_inside_the_presser_scope(no_write_refusal, monkeypatch):
    seen = []
    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: seen.append(current_principal().kind) or PRESSER)
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(lambda *a, **k: "report-guid-9")
    assert seen == ["caller"] and result["published_as"] == PRESSER


# ── the hold is released on every exit ──────────────────────────────────────

def test_the_hold_is_released_when_the_row_has_no_handler(monkeypatch):
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    row = {**_row("run-1", {}), "kind": "no_such_kind"}
    assert run_queue.execute_run(row, MagicMock()).state == "failed"
    assert presser_hold.take("run-1") is None


def test_the_hold_is_released_when_a_person_action_has_no_requester():
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    row = {**_row("run-1", {}), "kind": "curate_commit", "requested_by": ""}
    assert run_queue.execute_run(row, MagicMock()).state == "failed"
    assert presser_hold.take("run-1") is None


@pytest.mark.parametrize("failing", ["mark_run_running", "set_activity_runner"])
def test_the_hold_is_released_when_marking_the_run_running_fails(failing):
    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    registry = MagicMock()
    getattr(registry, failing).side_effect = RuntimeError("registry unreachable")
    row = {**_row("run-1", {"entity_type": "database", "slug": "d", "params": _params(publish_as_presser=True)}),
           "result_ref": "act-1"}
    with pytest.raises(RuntimeError):
        run_queue.execute_run(row, registry)
    assert presser_hold.take("run-1") is None


def test_the_hold_is_released_when_the_queued_row_is_cancelled_before_a_claim(sqlite_registry):
    run_id = _queued(sqlite_registry, _stamp())
    presser_hold.hold(run_id, PRESSER, LIVE_TOKEN)
    assert sqlite_registry.cancel_queued_run(run_id) is True
    assert presser_hold.take(run_id) is None
    # a refused cancel (already claimed) keeps the hold
    other = _queued(sqlite_registry, None)
    presser_hold.hold(other, PRESSER, LIVE_TOKEN)
    sqlite_registry.claim_next_run("w", HOLDER)
    assert sqlite_registry.cancel_queued_run(other) is False
    assert presser_hold.take(other) is not None


# ── the sign-in expiring part-way through the publish ───────────────────────

def test_an_expiry_during_the_publish_ends_with_the_expired_words_and_a_partial_write_note(no_write_refusal, monkeypatch):
    from resource_explorer.egeria_clients import CallerTokenExpired

    monkeypatch.setattr(presser_hold, "read_published_by", lambda guid: "x")

    def publish(entity, outputs, at, registry):
        raise CallerTokenExpired()

    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(publish)
    assert result["publish_state"] == SIGN_IN_EXPIRED_WORDS
    assert "publish failed" not in result["publish_state"] and result["published"] is False
    assert result["errors"] == []
    assert "some writes may already have been made" in result["publish_note"]


def test_an_expiry_wrapped_in_another_error_is_still_the_expired_words(no_write_refusal, monkeypatch):
    from resource_explorer.egeria_clients import CallerTokenExpired

    def publish(entity, outputs, at, registry):
        try:
            raise CallerTokenExpired()
        except CallerTokenExpired as inner:
            raise RuntimeError("wrapped") from inner

    presser_hold.hold("run-1", PRESSER, LIVE_TOKEN)
    with _worker_as_daemon(), presser_hold.pressed_run("run-1", PRESSER):
        result = _run_definition(publish)
    assert result["publish_state"] == SIGN_IN_EXPIRED_WORDS and "publish_note" in result


def test_an_unpressed_run_that_fails_its_publish_still_says_publish_failed(no_write_refusal):
    def publish(entity, outputs, at, registry):
        raise RuntimeError("egeria said no")

    with _worker_as_daemon(row_requester=None):
        result = _run_definition(publish)
    assert "publish_note" not in result and result["errors"]


# ── fix round 2 (2026-10-21): host identity, stamp only when consumed, cached start time, SQLite claim ─────

def test_a_holder_on_another_host_is_not_claimable_until_the_hold_ends(sqlite_registry, monkeypatch):
    checked = []
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: checked.append(owner) or False)
    run_id = _queued(sqlite_registry, _stamp(host="some-other-host"))
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is None           # a pid check there means nothing
    assert checked == [] and sqlite_registry.get_run(run_id)["state"] == "queued"
    sqlite_registry.cancel_queued_run(run_id)
    _queued(sqlite_registry, _stamp(host="some-other-host", until=time.time() - 1))
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is not None       # claimable once the hold ends
    # another boot of the same host name is also another host
    _queued(sqlite_registry, _stamp(boot_id="a-different-boot"))
    here = presser_hold.host_identity()
    if here["boot_id"]:
        assert sqlite_registry.claim_next_run("other-worker", OTHER) is None


def test_liveness_is_asked_once_per_claim_call(sqlite_registry, monkeypatch):
    asked = []
    monkeypatch.setattr("resource_explorer.run_reconciler._is_alive", lambda owner: asked.append(1) or True)
    for _ in range(3):
        _queued(sqlite_registry, _stamp())
    assert sqlite_registry.claim_next_run("other-worker", OTHER) is None
    assert len(asked) == 1


def test_the_start_time_is_read_once_and_a_stamp_is_never_blank(monkeypatch):
    from resource_explorer import run_reconciler

    calls = []
    run_reconciler._OWN_START.clear()
    monkeypatch.setattr(run_reconciler, "_process_start_time", lambda pid: calls.append(pid) or "Wed Oct 21 10:00:00 2026")
    first, second = run_reconciler.process_identity(), run_reconciler.process_identity()
    assert first == second and len(calls) == 1 and first["started_at"]
    # unreadable: not cached, and no stamp is made
    run_reconciler._OWN_START.clear()
    monkeypatch.setattr(run_reconciler, "_process_start_time", lambda pid: "")
    presser_hold.hold("run-x", PRESSER, LIVE_TOKEN)
    assert presser_hold.held_by_for("run-x") is None
    run_reconciler._OWN_START.clear()


def test_the_claim_returns_none_when_the_update_matches_no_row(sqlite_registry, monkeypatch):
    _queued(sqlite_registry, None)
    from contextlib import contextmanager

    real = sqlite_registry._conn

    class Wrapped:
        def __init__(self, conn): self._c = conn
        def __getattr__(self, n): return getattr(self._c, n)
        def execute(self, sql, *a):
            if sql.lstrip().startswith("UPDATE runs SET state='claimed'"):
                self._c.execute("UPDATE runs SET state='cancelled'")           # another claimer got there first
            return self._c.execute(sql, *a)

    @contextmanager
    def conn():
        with real() as c:
            yield Wrapped(c)

    monkeypatch.setattr(sqlite_registry, "_conn", conn)
    assert sqlite_registry.claim_next_run("w", OTHER) is None


def _press(tmp_path, monkeypatch, name):
    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / f"{name}.db"))
    registry.register_database(DatabaseEntity(slug="mydb", display_name="My DB", db_type="postgresql", host="localhost",
                                              port=5432, database_name="mydb"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(
        "resource_explorer.a2a_auth.caller",
        lambda: CallerIdentity(user_id=PRESSER, egeria_token=LIVE_TOKEN, auth_source="app-jwt", role="user"))
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/survey-definitions/database/mydb/run",
                                json={"survey_definition_ref": "GovActionProcess::Test"})
    assert resp.status_code == 200, resp.text
    return registry, resp.json()["run_id"]


def test_a_process_that_takes_the_kind_holds_and_stamps(tmp_path, monkeypatch):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: True)
    registry, run_id = _press(tmp_path, monkeypatch, "consumes")
    assert presser_hold.take(run_id) is not None
    target = json.loads(registry.get_run(run_id)["target"])
    assert target["held_by"]["started_at"] and target["held_by"]["host"] == presser_hold.host_identity()["host"]


def test_a_process_that_does_not_take_the_kind_neither_holds_nor_stamps(tmp_path, monkeypatch):
    monkeypatch.setattr(run_queue, "consumes_kind_here", lambda kind: False)
    registry, run_id = _press(tmp_path, monkeypatch, "no-consume")
    assert presser_hold.take(run_id) is None
    target = json.loads(registry.get_run(run_id)["target"])
    assert "held_by" not in target and target["params"]["publish_as_presser"] is True      # fails fast with the words


def test_consumes_kind_here_follows_the_running_loop_and_its_kinds(monkeypatch):
    runner = run_queue.QueueRunner()
    monkeypatch.setattr(run_queue, "_runner", runner)
    assert run_queue.consumes_kind_here("survey_definition_run") is False          # loop not started
    runner._thread = object()
    assert run_queue.consumes_kind_here("survey_definition_run") is True
    runner.kinds = ["engine_action"]
    assert run_queue.consumes_kind_here("survey_definition_run") is False
    runner.kinds = None
    monkeypatch.setattr(run_queue, "HELD_KINDS", frozenset({"survey_definition_run"}))
    assert run_queue.consumes_kind_here("survey_definition_run") is False
