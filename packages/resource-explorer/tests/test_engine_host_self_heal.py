"""RE's engine declaration: opt-in self-heal, the Admin press, the status `state` (F54 / W65, brief
DESIGN-BRIEF-ENGINE-HOST-SELF-HEAL-2026-11-04).

Egeria is never contacted: the canary read is monkeypatched, and the documents run through the real
`dr_egeria_runner` with test_dr_egeria_runner's fakes (pyegeria clients autospecced at `egeria_clients._build`, the
`dr_egeria` child faked at `bootstrap.subprocess.run`). The registry is a temp SQLite one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer import bootstrap as bs
from tests.test_dr_egeria_runner import IDENT, Fakes, _rows, reg, root  # noqa: F401 - the fixtures

OPT = "RE_ENGINE_HOST_HEAL"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.delenv(OPT, raising=False)
    monkeypatch.setattr(engine_host, "_engine_answer", {"present": None, "at": ""})
    with bs._status_lock:
        bs._status.clear()
    yield
    with bs._status_lock:
        bs._status.clear()


def _engine_batch(docs, files=("decl.md",)):
    (docs / "engine-host" / bs.BATCH_MANIFEST_FILE).write_text(json.dumps({
        "display_name": "Resource Explorer survey engine (engine host)", "idempotent": False, "opt_in": OPT,
        "runner": "dr_egeria_runner", "canary": {"type": "SurveyActionEngine", "qualified_name": "ResourceExplorerSurvey"},
        "files": list(files)}))
    (docs / bs.FOLDER_ORDER_FILE).write_text(json.dumps({"folders": ["engine-host"]}))


def _canary(monkeypatch, answer):
    monkeypatch.setattr(bs, "canary_present", lambda b, expert=None: answer)


# ── F54: opt-in ──────────────────────────────────────────────────────────────

def test_opted_out_batch_is_read_but_never_healed_and_reported_opted_out(root, monkeypatch):
    _engine_batch(root)
    _canary(monkeypatch, False)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("an opted-out batch must not be healed"))
    out = bs.check_and_heal(root)
    assert out["batches"]["engine-host"]["action"] == "opted_out"
    assert "RE_ENGINE_HOST_HEAL is off" in out["batches"]["engine-host"]["reason"]
    st = bs.get_status(root)["batches"]["engine-host"]
    # The canary was READ: the engine is known missing, so RE says so (the banner line), heal or not.
    assert st["state"] == "missing" and st["opt_in"] == {"key": OPT, "on": False}


def test_a_forced_heal_all_still_leaves_an_opted_out_batch_alone(root, monkeypatch):
    _engine_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("force must not reach an opted-out batch"))
    assert bs.check_and_heal(root, force=True)["batches"]["engine-host"]["action"] == "opted_out"


def test_never_read_opted_out_batch_reads_opted_out_not_unknown(root):
    _engine_batch(root)
    assert bs.get_status(root)["batches"]["engine-host"]["state"] == "opted_out"


def test_opted_in_and_missing_heals_every_document_in_order_through_the_runner(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    (root / "engine-host" / "library").mkdir()
    (root / "engine-host" / "library" / "x.md").write_text((root / "plain" / "only.md").read_text())
    _engine_batch(root, files=("decl.md", "library/x.md"))
    monkeypatch.setenv(OPT, "true")
    _canary(monkeypatch, False)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    seen = []
    real = runner.run_document

    def spy(path, **kw):
        seen.append((str(path).split("dr-egeria/")[-1], kw.get("heal_waiver", "")))
        return real(path, **kw)

    monkeypatch.setattr(runner, "run_document", spy)
    Fakes(monkeypatch)
    out = bs.check_and_heal(root)
    assert out["batches"]["engine-host"]["action"] == "healed", out
    assert [p for p, _w in seen] == ["engine-host/decl.md", "engine-host/library/x.md"]
    assert all(w.startswith("canary ResourceExplorerSurvey verified missing at ") for _p, w in seen)
    assert len({w for _p, w in seen}) == 1, "the waiver is decided ONCE at pass start and covers the whole pass"
    assert "2 documents run" in out["batches"]["engine-host"]["reason"]
    rows = _rows(reg)
    assert {json.loads(r["detail"])["identity_source"] for r in rows} == {bs.SOURCE_STARTUP_HEAL}


# ── the waiver: a reset must not leave the press stuck behind a 409 only force passes ──

def test_a_verified_missing_canary_sets_aside_a_run_whose_elements_a_reset_wiped(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _engine_batch(root)
    Fakes(monkeypatch)
    first = runner.run_document("engine-host/decl.md", force=True, identity=IDENT, registry=reg)
    with pytest.raises(runner.RepeatRefused):          # unchanged by hand: the batch and the earlier run both refuse
        runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    again = runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg,
                                heal_waiver="canary ResourceExplorerSurvey verified missing at T")
    assert again["status"] == "ok"
    detail = json.loads(next(r for r in _rows(reg) if r["id"] == again["activity_id"])["detail"])
    assert detail["heal_waiver"].startswith("canary ResourceExplorerSurvey verified missing")
    assert [r["id"] for r in detail["superseded_runs"]] == [first["activity_id"]]
    assert detail["superseded_words"] == (f"earlier run {first['activity_id']} at {first_ts(reg, first)} superseded: "
                                          "canary ResourceExplorerSurvey verified missing at T")


def first_ts(reg, out):
    return next(r for r in _rows(reg) if r["id"] == out["activity_id"])["ts"]


def _running_row(reg, plan, row_id="live-1"):
    from resource_explorer.registry import ActivityEntry

    reg.write_activity(ActivityEntry(
        id=row_id, ts="2026-11-04T10:00:00+00:00", operation="dr_egeria", intent="enrichment",
        entity_type="dr_egeria_document", entity_slug=plan.rel, entity_name=plan.path.name, entity_location="",
        status="running", summary="running",
        detail=json.dumps({"sha256": plan.sha256, "_runner": {"host": os.uname().nodename, "pid": os.getpid()}}),
        items=[]))


def test_a_running_row_for_any_file_refuses_the_whole_pass_before_it_starts(root, reg, monkeypatch):
    """Architect ruling Q2: document 2 is running elsewhere, so not even document 1 runs."""
    from resource_explorer import dr_egeria_runner as runner

    (root / "engine-host" / "second.md").write_text((root / "plain" / "only.md").read_text())
    _engine_batch(root, files=("decl.md", "second.md"))
    _canary(monkeypatch, False)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    Fakes(monkeypatch)
    _running_row(reg, runner.plan_document("engine-host/second.md"))
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("nothing of the batch may start"))
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "failed" and "second.md is in progress" in out["reason"]
    assert "nothing of this batch was started" in out["reason"]


def test_status_says_when_and_by_whom_the_state_was_checked(root, monkeypatch):
    from resource_explorer import engine_host

    _engine_batch(root)
    _canary(monkeypatch, False)
    bs.check_and_heal(root)
    st = bs.get_status(root)["batches"]["engine-host"]
    assert st["source"] == "bootstrap check" and st["checked_at"]
    engine_host.record_engine_answer(False, at="2999-01-01T00:00:00+00:00")
    st = bs.get_status(root)["batches"]["engine-host"]
    assert (st["source"], st["checked_at"]) == ("engine host", "2999-01-01T00:00:00+00:00")


def test_the_waiver_never_passes_a_run_in_progress(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch)
    _running_row(reg, runner.plan_document("engine-host/decl.md"))
    with pytest.raises(runner.RunRefused, match="in progress"):
        runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg, heal_waiver="canary verified missing")


# ── the one-batch run (the Admin press) ─────────────────────────────────────

def test_one_batch_run_works_with_the_setting_off(root, monkeypatch):
    _engine_batch(root)
    _canary(monkeypatch, False)
    healed = []
    monkeypatch.setattr(bs, "heal_batch", lambda b, **k: healed.append((b.batch_id, k)) or (True, "1 document run"))
    out = bs.check_and_heal(root, only="engine-host", requested_by="dan")
    assert out["batches"]["engine-host"]["action"] == "healed"
    assert healed[0][0] == "engine-host" and "pressed by dan" in healed[0][1]["waiver"]


def test_one_batch_run_on_a_present_engine_answers_already_declared_and_writes_nothing(root, reg, monkeypatch):
    _engine_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("a present engine is never re-declared"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    # F55: with no Egeria to verify the pending document's elements absent, the press completes nothing and says so.
    assert out["action"] == "present"
    assert out["reason"].startswith("already declared · no run of its first document is recorded here · "
                                    "0 of 1 documents applied · not completed: could not verify that decl.md's elements "
                                    "are absent (")
    assert out["reason"].endswith("could not be looked up) · nothing run")


def test_already_declared_names_the_date_its_content_was_applied(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _engine_batch(root)
    Fakes(monkeypatch)
    runner.run_document("engine-host/decl.md", force=True, identity=IDENT, registry=reg)
    batch = bs.discover_batches(root)[0]
    words = bs.already_declared_words(batch, registry=reg)
    assert words.startswith("already declared · content applied on 20") and words.endswith(" · 1 of 1 documents applied")


def test_one_batch_run_refuses_unknown_canary_without_a_write(root, monkeypatch):
    _engine_batch(root)
    _canary(monkeypatch, None)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("no heal on an unreadable canary"))
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out == {"action": "unknown", "reason": "could not check Egeria: could not reach Egeria"}


def test_one_batch_run_is_never_forced_and_names_a_real_batch(root):
    _engine_batch(root)
    with pytest.raises(ValueError, match="never forced"):
        bs.check_and_heal(root, force=True, only="engine-host")
    with pytest.raises(bs.UnknownBatch):
        bs.check_and_heal(root, only="nope")


def test_price_counts_what_the_documents_declare(root, monkeypatch):
    _engine_batch(root)
    price = bs.batch_price("engine-host", root)
    assert price["documents"] == 1 and price["engines"] == 1 and price["services"] == 1
    assert price["governance_action_types"] == 1 and price["processes"] == 0
    assert price["applying_as"] == "resourceexplorernpa"
    assert price["self_heal"] == {"key": OPT, "on": False, "interval_seconds": bs.CHECK_INTERVAL_SECONDS}
    assert price["words"].startswith("runs 1 Dr.Egeria document in order as resourceexplorernpa · creates 1 engine")
    assert price["words"].endswith("refused if already declared")


def test_the_real_price_is_eight_documents():
    price = bs.batch_price("engine-host")
    assert price["documents"] == 8 and price["engines"] == 1 and price["processes"] == 1
    assert price["services"] >= 5 and price["governance_action_types"] >= price["services"]


# ── status: state and the engine host's own answer ──────────────────────────

def test_the_engine_hosts_newer_answer_wins_over_the_bootstrap_read(root, monkeypatch):
    from resource_explorer import engine_host

    _engine_batch(root)
    _canary(monkeypatch, False)
    bs.check_and_heal(root)
    assert bs.get_status(root)["batches"]["engine-host"]["state"] == "missing"
    engine_host.record_engine_answer(True, at="2999-01-01T00:00:00+00:00")       # act A, seen by the 5 s sweep
    st = bs.get_status(root)["batches"]["engine-host"]
    assert st["state"] == "present" and st["last_sweep_at"] == "2999-01-01T00:00:00+00:00"
    assert engine_host.engine_declared() is True


def test_the_sweep_records_both_answers(monkeypatch):
    from resource_explorer import engine_host

    host = engine_host.EngineHost()

    class Expert:
        answer = "No elements found"

        def get_metadata_guid_by_unique_name(self, name):
            return self.answer

    expert = Expert()
    monkeypatch.setattr(host, "_client", lambda cls: expert)
    assert host.resolve_engine_guid() is None and engine_host.last_engine_answer()["present"] is False
    expert.answer = "0a1b2c3d-0000-4000-8000-00000000e001"
    assert host.resolve_engine_guid() and engine_host.last_engine_answer()["present"] is True


def test_every_status_entry_has_a_state():
    for st in bs.get_status()["batches"].values():
        assert st["state"] in ("present", "missing", "opted_out", "unknown")


# ── the route ───────────────────────────────────────────────────────────────

ADMIN = "resource_explorer.web.admin_auth.is_admin_request"
CONFIGURED = "resource_explorer.web.admin_auth.admin_configured"
USER = "resource_explorer.auth.get_current_user"


@pytest.fixture()
def client(monkeypatch):
    from resource_explorer.web.app import app

    monkeypatch.setattr(ADMIN, lambda *a, **k: True)
    monkeypatch.setattr(CONFIGURED, lambda *a, **k: True)
    monkeypatch.setattr(USER, lambda *a, **k: {"sub": "dan"})
    return TestClient(app)


def test_route_runs_one_batch_as_the_pressing_admin(client):
    with patch("resource_explorer.bootstrap.check_and_heal", return_value={"batches": {}}) as heal:
        assert client.post("/api/bootstrap/run", json={"batch": "engine-host"}).status_code == 200
    assert heal.call_args.kwargs == {"only": "engine-host", "requested_by": "dan"}
    assert heal.call_args.args[1] is False


def test_route_refuses_a_forced_one_batch_run_and_an_unknown_batch(client):
    with patch("resource_explorer.bootstrap.check_and_heal") as heal:
        assert client.post("/api/bootstrap/run", json={"batch": "engine-host", "force": True}).status_code == 400
        assert client.post("/api/bootstrap/run", json={"batch": "nope"}).status_code == 404
    heal.assert_not_called()


def test_route_409s_the_press_while_a_heal_runs(client, monkeypatch):
    assert bs._heal_lock.acquire(blocking=False)
    try:
        assert client.post("/api/bootstrap/run", json={"batch": "engine-host"}).status_code == 409
    finally:
        bs._heal_lock.release()
    monkeypatch.setattr(bs, "_reinitializing", True)
    assert client.post("/api/bootstrap/run", json={"batch": "engine-host"}).status_code == 409


def test_status_poll_makes_no_egeria_call(client, monkeypatch):
    monkeypatch.setattr(bs, "canary_present", lambda *a, **k: pytest.fail("the poll must not read Egeria"))
    from resource_explorer import engine_host

    monkeypatch.setattr(engine_host.EngineHost, "resolve_engine_guid", lambda self: pytest.fail("no lookup on poll"))
    body = client.get("/api/bootstrap/status").json()
    eh = body["batches"]["engine-host"]
    assert eh["state"] in ("opted_out", "unknown", "missing", "present") and "healing" in body
    assert eh["opt_in"]["key"] == OPT


def test_price_route(client):
    assert client.get("/api/bootstrap/price/engine-host").json()["documents"] == 8
    assert client.get("/api/bootstrap/price/nope").status_code == 404


@pytest.mark.requires_egeria
def test_live_the_canary_resolves_by_qualified_name_once_declared():
    """Live, read-only (opt-in tier): the canary's qualifiedName is the one resolve_engine_guid looks up, and the
    bootstrap canary read answers without raising (True after act A, False before it; None means unreachable)."""
    batch = next(b for b in bs.discover_batches() if b.batch_id == "engine-host")
    assert batch.canary["qualified_name"] == "ResourceExplorerSurvey"
    assert bs.canary_present(batch) in (True, False)


# ── W64: every RE-engine card refuses before the press while the engine is verified absent ──

from tests.test_native_survey_run import TECH, registry  # noqa: E402,F401 - the fixture

RE_ENGINE_ROWS = ("ResourceExplorerSurvey::re-db-derived", "GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived")


def _rows_by_qn(registry):
    from resource_explorer import native_survey_run as nsr

    return {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}


def test_w64_re_engine_rows_refuse_with_their_words_while_the_engine_is_absent(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(False)
    by = _rows_by_qn(registry)
    for qn in RE_ENGINE_ROWS:
        assert by[qn]["runnable"] is False, qn
        assert by[qn]["cannot_run_reason"] == nsr.ENGINE_NOT_DECLARED, qn
    assert nsr.ENGINE_NOT_DECLARED == ("not declared in this Egeria · runs only where the owner has declared the "
                                       "engine (Admin › Repair › Declare…) and RE's engine host is on")
    # Egeria's own survey does not need RE's engine: not refused for this reason.
    assert by["PostgreSQLSurvey::survey-postgres-database"]["cannot_run_reason"] != nsr.ENGINE_NOT_DECLARED


def test_w64_the_refusal_lifts_once_the_engine_is_seen_and_never_on_not_known(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    for answer in (None, True):
        if answer is not None:
            engine_host.record_engine_answer(answer)
        by = _rows_by_qn(registry)
        for qn in RE_ENGINE_ROWS:
            assert by[qn]["cannot_run_reason"] != nsr.ENGINE_NOT_DECLARED, (answer, qn)


def test_w64_engine_host_off_still_says_engine_host_off(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr

    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    engine_host.record_engine_answer(False)
    by = _rows_by_qn(registry)
    assert by["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"] == nsr.ENGINE_HOST_OFF


def test_w64_the_press_is_refused_before_any_egeria_call(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(False)

    class NoPort:
        def __getattr__(self, name):
            raise AssertionError(f"the press reached Egeria ({name})")

    with pytest.raises(nsr.NativeSurveyCannotRun, match="not declared in this Egeria"):
        nsr.submit_native_survey(registry, NoPort(), "database", "adventureworks",
                                 "ResourceExplorerSurvey::re-db-derived", technology_type=TECH)


def test_w64_served_status_carries_the_state_the_cards_read(registry, monkeypatch):
    """The bootstrap pass's canary read alone (engine host never asked) is enough for the cards to refuse."""
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    with bs._status_lock:
        bs._status[bs.ENGINE_HOST_BATCH] = bs.BatchStatus(present=False, last_verified_at="2026-11-04T17:00:00+00:00")
    assert bs.get_status()["batches"]["engine-host"]["state"] == "missing"
    assert _rows_by_qn(registry)["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"] == nsr.ENGINE_NOT_DECLARED


# ── W66: a private resource never offers an Egeria-dispatched RE survey ──

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _as(user_id: str):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
    try:
        yield
    finally:
        current_caller.reset(reset)


def _make_private(registry, owner="alice", name="Alice Private"):
    with _as(owner):
        inv = registry.create_investigation(name, project_classification="PersonalProject")
        ws = registry.get_or_create_working_set(inv["slug"])
        registry.add_working_set_member(ws["slug"], "database", "adventureworks")
    return inv


def _w66_rows(registry, viewer=""):
    from resource_explorer import native_survey_run as nsr

    return {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH,
                                                                   viewer=viewer)}


# F56: the sentence says "creator-only investigation" — "private investigation" names only local + creator-only.
W66_SENTENCE = ("not offered: this database is in a creator-only investigation, and Resource Explorer's engine host "
                "does not claim Egeria engine actions on creator-only resources (nothing of a creator-only "
                "investigation is written into Egeria's engine queue) · Resource Explorer's own Run on the card runs it "
                "here instead")


def test_w66_a_private_database_offers_no_re_engine_run_and_says_why(registry, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    _make_private(registry)
    by = _w66_rows(registry, viewer="bob")
    for qn in RE_ENGINE_ROWS:
        assert by[qn]["runnable"] is False and by[qn]["cannot_run_reason"] == W66_SENTENCE, qn
    # Egeria's own engines are not RE's rule.
    assert "creator-only investigation" not in by["PostgreSQLSurvey::survey-postgres-database"]["cannot_run_reason"]


def test_w66_private_is_asked_first_even_with_the_engine_absent_or_the_host_off(registry, monkeypatch):
    from resource_explorer import engine_host

    _make_private(registry)
    engine_host.record_engine_answer(False)
    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    assert _w66_rows(registry)["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"] == W66_SENTENCE


def test_w66_the_owner_sees_the_investigation_named_another_viewer_does_not(registry, monkeypatch):
    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    _make_private(registry, owner="alice", name="Alice Private")
    owner_reason = _w66_rows(registry, viewer="alice")["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"]
    other_reason = _w66_rows(registry, viewer="bob")["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"]
    assert owner_reason == W66_SENTENCE + " (creator-only investigation: Alice Private)"
    assert other_reason == W66_SENTENCE and "Alice" not in other_reason


def test_w66_the_api_refuses_with_the_same_sentence_before_egeria(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    _make_private(registry)

    class NoPort:
        def __getattr__(self, name):
            raise AssertionError(f"the press reached Egeria ({name})")

    for qn in RE_ENGINE_ROWS:
        with pytest.raises(nsr.NativeSurveyCannotRun) as refused:
            nsr.submit_native_survey(registry, NoPort(), "database", "adventureworks", qn, technology_type=TECH)
        assert str(refused.value) == W66_SENTENCE, qn


def test_w66_route_answers_422_with_the_sentence(registry, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer.web.routes import native_surveys as route

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    _make_private(registry)
    src = Path(route.__file__).read_text()
    assert "except nsr.NativeSurveyCannotRun as exc:\n        raise HTTPException(status_code=422" in src


def test_w66_a_shared_investigation_changes_nothing(registry, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    with _as("alice"):
        inv = registry.create_investigation("Team task", project_classification="Task")
        ws = registry.get_or_create_working_set(inv["slug"])
        registry.add_working_set_member(ws["slug"], "database", "adventureworks")
    assert "creator-only investigation" not in _w66_rows(registry)["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"]


def _in_investigation(registry, kind, visibility, owner="alice"):
    with _as(owner):
        inv = registry.create_investigation(f"{kind} {visibility}", project_classification=kind, visibility=visibility)
        ws = registry.get_or_create_working_set(inv["slug"])
        registry.add_working_set_member(ws["slug"], "database", "adventureworks")
    return inv


def test_f56_a_shared_personal_projects_database_is_offered(registry, monkeypatch):
    """F56: Q-O3 follows the investigation's visibility, not its kind. A PersonalProject made shared is claimable."""
    from resource_explorer import engine_host

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    _in_investigation(registry, "PersonalProject", "shared")
    assert "creator-only investigation" not in _w66_rows(registry)["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"]


def test_f56_a_creator_only_studys_database_is_not_offered(registry, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    _in_investigation(registry, "StudyProject", "creator-only")
    assert _w66_rows(registry, viewer="bob")["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"] == W66_SENTENCE
