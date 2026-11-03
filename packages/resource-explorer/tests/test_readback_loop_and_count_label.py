"""The native read-back sweep and the outbox drain on their own 60 s loop (2026-10-21), the card's "not checked
since" cue, and the annotation count label.

Defect (loading_bay, 2026-10-21): the scheduler tick ran `_run_due`, then RFA reconciliation, then the outbox drain,
then the read-back sweep, all on one thread every 900 s. A RagIngestion started inside `_run_due` ran 14+ minutes, so
an Egeria folder survey that finished in 7.4 s sat at "waiting" on its card. Nothing here reaches Egeria or a live
database; the Egeria port is an autospec of the real class.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import create_autospec

import pytest

from resource_explorer import catalog_and_survey, egeria_clients, scheduler
from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import DatabaseEntity, FileSystemEntity, ProjectRegistry

REAL_PORT = catalog_and_survey.PyegeriaRegistrationPort        # captured before any test patches the name
TECH = "PostgreSQL Relational Database"
SURVEY_QN = "PostgreSQLSurvey::survey-postgres-database"
ASSET = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = ProjectRegistry(db_path=str(tmp_path / "rb.db"))
    reg.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql", host="localhost", port=5432,
        database_name="adventureworks", egeria_asset_guid=ASSET))
    # The scheduler builds `ProjectRegistry()` inside its passes: hand it this one.
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    return reg


def _port(seen: list):
    """An autospec of the real registration port (real signatures); `read_action` records WHO it is called as."""
    port = create_autospec(REAL_PORT, instance=True, spec_set=True)

    def read_action(guid):
        seen.append(egeria_clients.current_principal())
        return nsr.ActionRead(status="IN_PROGRESS", message="")

    port.read_action.side_effect = read_action
    return port


def _in_flight_row(registry, guid="ea-1"):
    registry.record_native_survey_submission("database", "adventureworks", SURVEY_QN, "2026-10-21T20:00:00",
                                             engine_action_guid=guid, submitted_by="dan")


# ── the tick no longer owns the sweep or the drain ──────────────────────────────────────────────────────────────────

def test_the_main_tick_no_longer_calls_the_sweep_or_the_drain(monkeypatch):
    calls = []
    for name in ("_run_due", "_reconcile_rfa_actions"):
        monkeypatch.setattr(scheduler, name, lambda n=name: calls.append(n))
    for name in ("_sweep_native_surveys", "_drain_egeria_outbox"):
        monkeypatch.setattr(scheduler, name, lambda n=name: calls.append("FORBIDDEN " + n))
    scheduler._tick()
    assert calls == ["_run_due", "_reconcile_rfa_actions"]


def test_a_pass_of_the_new_loop_runs_the_sweep_and_the_drain(monkeypatch):
    calls = []
    for name in ("_sweep_native_surveys", "_drain_egeria_outbox"):
        monkeypatch.setattr(scheduler, name, lambda n=name: calls.append(n))
    scheduler._readback_pass()
    assert calls == ["_sweep_native_surveys", "_drain_egeria_outbox"]


# ── idle: no Egeria call; in flight: read back; and as the daemon ───────────────────────────────────────────────────

def test_an_idle_pass_makes_no_port_call_and_the_drain_claims_nothing(registry, monkeypatch):
    """Asserts on what the pass DID, not on a client builder that a broad `except` would swallow: the port class is
    never constructed, and the drain ran, claimed nothing and returned its own empty summary."""
    port_cls = create_autospec(REAL_PORT, spec_set=True)
    monkeypatch.setattr(catalog_and_survey, "PyegeriaRegistrationPort", port_cls)
    from resource_explorer import egeria_outbox

    summaries: list = []
    real_drain = egeria_outbox.drain_outbox
    monkeypatch.setattr(egeria_outbox, "drain_outbox",
                        lambda *a, **k: summaries.append(real_drain(*a, **k)) or summaries[-1])
    claims: list = []
    real_claim = ProjectRegistry.claim_due_outbox_elements
    monkeypatch.setattr(ProjectRegistry, "claim_due_outbox_elements",
                        lambda self, *a, **k: claims.append(1) or real_claim(self, *a, **k))
    assert registry.list_in_flight_native_survey_runs(limit=1) == []
    scheduler._readback_pass()
    assert port_cls.mock_calls == [], "nothing awaiting read-back: the port is not even constructed"
    assert len(summaries) == 1, "the drain did run (the assertion below is about a real summary)"
    assert summaries[0].get("claimed") == 0 and not summaries[0].get("identity_error")
    assert claims, "the drain asked the registry for due rows and was told there were none"


def test_a_pass_with_one_row_in_flight_reads_it_back_as_the_scheduler_daemon(registry, monkeypatch):
    seen: list = []
    monkeypatch.setattr(catalog_and_survey, "PyegeriaRegistrationPort", lambda *a, **k: _port(seen))
    _in_flight_row(registry)
    scheduler._readback_pass()
    row = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    assert row["engine_action_status"] == "IN_PROGRESS" and row["engine_action_read_at"]
    assert len(seen) == 1
    who = seen[0]
    assert who.kind == "daemon" and who.reason == egeria_clients.DaemonReason.SCHEDULER, "never '' and never a person"


def test_the_pass_declares_its_identity_in_a_bare_thread_that_inherited_none(registry, monkeypatch):
    """A bare threading.Thread starts with no ContextVars at all (no signed-in caller is set here, and none could be
    inherited): the pass must declare the daemon itself, or the read would have no identity."""
    seen: list = []
    monkeypatch.setattr(catalog_and_survey, "PyegeriaRegistrationPort", lambda *a, **k: _port(seen))
    _in_flight_row(registry)
    errors: list = []

    def target():
        try:
            scheduler._readback_pass()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
    t = threading.Thread(target=target)
    t.start()
    t.join(10)
    assert not errors and len(seen) == 1 and seen[0].kind == "daemon"


# ── a long _run_due does not delay the new loop ─────────────────────────────────────────────────────────────────────

def test_a_long_run_due_does_not_delay_the_readback_pass(registry, monkeypatch):
    seen: list = []
    monkeypatch.setattr(catalog_and_survey, "PyegeriaRegistrationPort", lambda *a, **k: _port(seen))
    _in_flight_row(registry)
    started, release = threading.Event(), threading.Event()

    def long_run_due():
        started.set()
        release.wait(30)
    monkeypatch.setattr(scheduler, "_run_due", long_run_due)
    monkeypatch.setattr(scheduler, "_reconcile_rfa_actions", lambda: None)
    tick = threading.Thread(target=scheduler._tick)
    tick.start()
    try:
        assert started.wait(10)
        t0 = time.monotonic()
        scheduler._readback_pass()                     # while the tick is still inside _run_due
        assert tick.is_alive(), "the fake _run_due is still blocking"
        assert len(seen) == 1 and time.monotonic() - t0 < 5
    finally:
        release.set()
        tick.join(10)


# ── start/stop: bounded, and only the leader runs it ────────────────────────────────────────────────────────────────

def test_start_runs_both_threads_and_stop_ends_both_within_the_bound(monkeypatch):
    monkeypatch.setattr(scheduler, "_CHECK_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(scheduler, "_READBACK_INTERVAL_SECONDS", 3600)
    scheduler.stop_scheduler()
    scheduler.start_scheduler()
    try:
        names = {t.name for t in threading.enumerate()}
        assert {"resource-explorer-scheduler", "resource-explorer-readback"} <= names
        t0 = time.monotonic()
    finally:
        scheduler.stop_scheduler(timeout=5.0)
    assert time.monotonic() - t0 < 5.0
    names = {t.name for t in threading.enumerate()}
    assert "resource-explorer-scheduler" not in names and "resource-explorer-readback" not in names


def _supervise_with(monkeypatch, wins: bool):
    """Run the worker's real supervisor over the REAL scheduler spec (real start/stop) with a lock that wins or loses."""
    from resource_explorer import worker

    spec = dataclasses.replace(worker._scheduler_spec(), interval_seconds=0.01)
    assert spec.start is scheduler.start_scheduler and spec.stop is scheduler.stop_scheduler
    monkeypatch.setattr(scheduler, "_CHECK_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(scheduler, "_READBACK_INTERVAL_SECONDS", 3600)

    class Lock:
        key = 1

        def __init__(self, name): ...
        def acquire(self): return wins
        def release(self): ...
    monkeypatch.setattr(worker, "LeaderLock", Lock)
    scheduler.stop_scheduler()
    stop = threading.Event()
    t = threading.Thread(target=worker._supervise, args=(spec, stop, False))
    t.start()
    time.sleep(0.2)
    seen = {th.name for th in threading.enumerate()}
    stop.set()
    t.join(10)
    scheduler.stop_scheduler()
    return seen


def test_the_loop_is_started_only_by_the_leader(monkeypatch):
    """Through the real `start_scheduler`: a process that loses the `scheduler` advisory lock has no read-back thread
    (and no tick thread); one that wins has both. The winning case is the control: without it the losing assertion
    could pass for a reason that has nothing to do with the lock."""
    standby = _supervise_with(monkeypatch, wins=False)
    assert "resource-explorer-readback" not in standby and "resource-explorer-scheduler" not in standby
    leader = _supervise_with(monkeypatch, wins=True)
    assert {"resource-explorer-readback", "resource-explorer-scheduler"} <= leader


def test_start_scheduler_called_again_with_either_thread_dead_restarts_the_pair(monkeypatch):
    """The re-entry guard checks BOTH threads (review N3): a live tick thread beside a dead read-back thread is a
    half-started scheduler, and a second start must not return early on it."""
    monkeypatch.setattr(scheduler, "_CHECK_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(scheduler, "_READBACK_INTERVAL_SECONDS", 3600)
    scheduler.stop_scheduler()
    scheduler.start_scheduler()
    try:
        first_tick, first_rb, first_stop = scheduler._thread, scheduler._readback_thread, scheduler._stop_event
        scheduler.start_scheduler()
        assert (scheduler._thread, scheduler._readback_thread) == (first_tick, first_rb), "both alive: idempotent"
        # Half-started: the tick thread is alive, the read-back thread is dead.
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        scheduler._readback_thread = dead
        scheduler.start_scheduler()
        assert scheduler._readback_thread is not dead and scheduler._readback_thread.is_alive()
        assert scheduler._thread is not first_tick and scheduler._thread.is_alive()
        assert first_stop.is_set(), "the old pair is told to leave, so the restart never doubles the tick"
        first_tick.join(5)
        assert not first_tick.is_alive()
    finally:
        scheduler.stop_scheduler(timeout=5.0)
        first_rb.join(5)


# ── "not checked since": from the proof row's read_at only ──────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 21, 21, 0, 0)


def _row(read_at, **kw):
    base = {"surveyed_at": "2026-10-21T20:00:00", "engine_action_guid": "ea-1", "engine_action_status": "APPROVED",
            "engine_action_message": "", "engine_action_read_at": read_at, "engine_action_read_error": "",
            "survey_report_guid": "", "survey_report_at": "", "report_read_at": "",
            "report_annotation_count": None, "submit_error": ""}
    base.update(kw)
    return base


def test_the_cue_shows_when_the_proof_row_was_read_three_minutes_ago_and_not_at_one():
    old = nsr.derive_native_state(_row("2026-10-21T20:57:00"), now=NOW)          # 3 min old (fake clock)
    fresh = nsr.derive_native_state(_row("2026-10-21T20:59:00"), now=NOW)        # 1 min old
    assert old["state"] == nsr.RUNNING and old["not_checked_since"] == "2026-10-21T20:57:00"
    assert fresh["state"] == nsr.RUNNING and fresh["not_checked_since"] == ""


def test_the_cue_boundary_is_two_loop_intervals():
    assert nsr.NOT_CHECKED_AFTER_S == 2 * scheduler._READBACK_INTERVAL_SECONDS
    exactly = nsr.derive_native_state(_row("2026-10-21T20:58:00"), now=NOW)       # 120 s: not older than 120 s
    over = nsr.derive_native_state(_row("2026-10-21T20:57:59"), now=NOW)
    assert exactly["not_checked_since"] == "" and over["not_checked_since"]


def test_a_finished_run_or_one_never_read_carries_no_cue():
    done = nsr.derive_native_state(_row("2026-10-21T20:00:00", engine_action_status="FAILED"), now=NOW)
    never = nsr.derive_native_state(_row(""), now=NOW)
    assert done["not_checked_since"] == "" and never["not_checked_since"] == ""


def test_the_cue_is_in_the_rows_the_card_reads(registry):
    _in_flight_row(registry)
    registry.record_native_survey_readback("ea-1", read_at="2000-01-01T00:00:00", status="APPROVED")
    row = nsr.native_survey_rows(registry, "database", "adventureworks", TECH)[0]
    assert row["run"]["not_checked_since"] == "2000-01-01T00:00:00"


# ── the count label ─────────────────────────────────────────────────────────────────────────────────────────────────

def _ann(n, kind="Capture File Counts"):
    return {"guid": f"ann-{n}", "annotation_type": kind, "analysis_step": "", "summary": f"a{n}", "explanation": "",
            "confidence": 0, "detail": {}}


class _Port:
    def __init__(self, annotations):
        self.annotations = annotations

    def read_action(self, guid):
        return nsr.ActionRead(status="COMPLETED", message="done")

    def report_guid_for_action(self, guid):
        return "rep-1"

    def read_report(self, report_guid):
        return nsr.ReportRead(guid=report_guid, at="2026-10-21T20:00:00+00:00", originator_guid="ea-1",
                              annotations=self.annotations)


def test_a_filesystem_report_with_seven_incl_the_names_log_gets_the_three_part_label(registry, tmp_path):
    root = tmp_path / "lake"
    root.mkdir()
    registry.register_filesystem(FileSystemEntity(slug="lake", display_name="Lake", local_mount_point=str(root),
                                                  canonical_mount_point="/deployments/lake", egeria_asset_guid=ASSET))
    registry.record_native_survey_submission("filesystem", "lake", nsr.FOLDER_SURVEY_QN, "2026-10-21T20:00:00",
                                             engine_action_guid="ea-1", submitted_by="dan")
    anns = [_ann(i) for i in range(1, 7)] + [_ann(7, nsr.NAMES_LOG_ANNOTATION)]
    run = registry.list_native_survey_runs("filesystem", "lake", nsr.FOLDER_SURVEY_QN)[0]
    derived = nsr.refresh_run(registry, _Port(anns), run, entity_type="filesystem", slug="lake")
    assert derived["state"] == nsr.COMPLETE
    assert derived["count_label"] == ("7 annotations on the report; 6 read into RE; "
                                      "1 not read by design (file-name log)")
    assert registry.count_native_survey_annotations("rep-1") == 6          # the 6 are what is stored


def test_a_database_report_gets_the_single_label(registry):
    _in_flight_row(registry)
    run = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    derived = nsr.refresh_run(registry, _Port([_ann(i) for i in range(1, 8)]), run, entity_type="database",
                              slug="adventureworks")
    assert derived["count_label"] == "7 annotations on the report · read into RE"


def test_a_row_read_before_the_totals_were_kept_gets_no_label_not_a_guess(registry):
    _in_flight_row(registry)
    run = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    registry.record_native_survey_report("ea-1", entity_type="database", slug="adventureworks",
                                         process_qualified_name=SURVEY_QN, report_guid="rep-9", report_at="t",
                                         read_at="t", annotations=[_ann(1), _ann(2)])          # no totals passed
    registry.record_native_survey_readback("ea-1", read_at="t", status="COMPLETED")
    derived = nsr.native_survey_rows(registry, "database", "adventureworks", TECH)[0]["run"]
    assert derived["state"] == nsr.COMPLETE and derived["annotation_count"] == 2 and derived["count_label"] == ""


def test_the_totals_are_kept_without_new_columns(registry):
    """No DDL: the totals ride in the step_runs row's existing `declared` JSON."""
    import json

    _in_flight_row(registry)
    registry.record_native_survey_report("ea-1", entity_type="database", slug="adventureworks",
                                         process_qualified_name=SURVEY_QN, report_guid="rep-9", report_at="t",
                                         read_at="t", annotations=[_ann(1)],
                                         report_totals={"on_report": 3, "not_read_by_design": 0})
    row = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    assert json.loads(row["declared"])["report_totals"] == {"on_report": 3, "not_read_by_design": 0}


# ═══ review fix round 1 (2026-10-21) ════════════════════════════════════════════════════════════════════════════════
import json  # noqa: E402


def _declared_row(read_at, retry=None, **kw):
    declared = {nsr.READBACK_RETRY_KEY: retry} if retry else {}
    return _row(read_at, declared=json.dumps(declared), **kw)


# ── B1: a registered database is not "waiting" ──────────────────────────────────────────────────────────────────────

def test_a_registered_database_read_three_minutes_ago_carries_no_cue():
    run = _row("2026-10-21T20:57:00", engine_action_status="COMPLETED")           # COMPLETED, read 3 min ago
    registered = catalog_and_survey.derive_catalog_state(run, has_pointer=True, now=NOW)
    assert registered["state"] == catalog_and_survey.REGISTERED
    assert registered["not_checked_since"] == "", "REGISTERED is not an in-flight state: no 'not checked since'"


def test_the_cue_survives_where_it_is_true_awaiting_registration_and_a_running_registration():
    """Fail-on-purpose companion: the same row WITHOUT a stored pointer is still being waited for, and says so; so does a
    registration still running. If the fix above had simply removed the cue everywhere, these would fail."""
    done = _row("2026-10-21T20:57:00", engine_action_status="COMPLETED")
    waiting = catalog_and_survey.derive_catalog_state(done, has_pointer=False, now=NOW)
    assert waiting["state"] == catalog_and_survey.AWAITING_REGISTRATION and waiting["not_checked_since"]
    running = catalog_and_survey.derive_catalog_state(_row("2026-10-21T20:57:00"), has_pointer=True, now=NOW)
    assert running["state"] == nsr.RUNNING and running["not_checked_since"]


def test_the_catalog_row_the_card_reads_has_no_cue_once_registered(registry):
    cat_qn = next(p.qualified_name for p in nsr._processes_for_rows("database", TECH)
                  if p.kind == nsr.KIND_CATALOG_AND_SURVEY)
    registry.record_native_survey_submission("database", "adventureworks", cat_qn, "2026-10-21T20:00:00",
                                             engine_action_guid="reg-1", submitted_by="dan")
    registry.record_native_survey_readback("reg-1", read_at="2000-01-01T00:00:00", status="COMPLETED")
    row = next(r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)
               if r["qualified_name"] == cat_qn)
    assert row["run"]["state"] == catalog_and_survey.REGISTERED and row["run"]["not_checked_since"] == ""


def test_every_remap_of_state_recomputes_the_read_words():
    """The remap sites that change `state` after `derive_native_state` computed its words: the catalog row
    (derive_catalog_state) and the process run (derive_mixed_state). Both end in `with_read_words`."""
    import inspect

    assert "with_read_words(" in inspect.getsource(catalog_and_survey.derive_catalog_state)
    assert "with_read_words(" in inspect.getsource(nsr.derive_mixed_state)


# ── the one list ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_one_definition_of_in_flight_feeds_every_list():
    assert set(nsr.readback_sql_clauses()) == set(nsr.IN_FLIGHT_STATES)
    assert nsr._IN_FLIGHT is nsr.IN_FLIGHT_STATES
    assert nsr._CHECKABLE_STATES == frozenset(nsr.IN_FLIGHT_STATES) | {"awaiting_registration"}
    assert set(nsr.RETRY_BOUNDED_STATES) <= set(nsr.IN_FLIGHT_STATES)


def _make_rows(registry):
    """One row per derivable state; returns {guid: expected state}."""
    rows = {}

    def submit(guid):
        registry.record_native_survey_submission("database", "adventureworks", SURVEY_QN, "2026-10-21T20:00:00",
                                                 engine_action_guid=guid, submitted_by="dan")
    submit("g-submitted"); rows["g-submitted"] = nsr.SUBMITTED
    submit("g-unreadable")
    registry.record_native_survey_readback("g-unreadable", read_at="t", error="boom"); rows["g-unreadable"] = nsr.UNREADABLE
    submit("g-running")
    registry.record_native_survey_readback("g-running", read_at="t", status="IN_PROGRESS"); rows["g-running"] = nsr.RUNNING
    submit("g-awaiting")
    registry.record_native_survey_readback("g-awaiting", read_at="t", status="COMPLETED"); rows["g-awaiting"] = nsr.AWAITING_REPORT
    for guid, report, anns in (("g-incomplete", "rep-i", [_ann(11), _ann(12)]), ("g-complete", "rep-c", [_ann(1), _ann(2)])):
        submit(guid)
        registry.record_native_survey_report(guid, entity_type="database", slug="adventureworks",
                                             process_qualified_name=SURVEY_QN, report_guid=report, report_at="t",
                                             read_at="t", annotations=anns)
        registry.record_native_survey_readback(guid, read_at="t", status="COMPLETED")
    with registry._conn() as conn:
        conn.execute("DELETE FROM native_survey_annotations WHERE annotation_guid = 'ann-12'")
    rows["g-incomplete"] = nsr.REPORT_INCOMPLETE
    rows["g-complete"] = nsr.COMPLETE
    submit("g-failed")
    registry.record_native_survey_readback("g-failed", read_at="t", status="FAILED", message="x"); rows["g-failed"] = nsr.FAILED
    return rows


def test_the_sql_selection_and_the_derivation_agree_on_every_state(registry):
    """The SQL side (what the sweep selects) and the Python side (`derive_native_state`) are one list: for a row in
    every state, the sweep selects exactly the ones whose derived state is in IN_FLIGHT_STATES."""
    expected = _make_rows(registry)
    picked = {r["engine_action_guid"] for r in registry.list_in_flight_native_survey_runs()}
    by_derivation = {}
    for r in registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN):
        stored = (registry.count_native_survey_annotations(r["survey_report_guid"]) if r.get("survey_report_guid") else None)
        by_derivation[r["engine_action_guid"]] = nsr.derive_native_state(r, stored_annotation_count=stored)["state"]
    assert by_derivation == expected                                            # the fixtures are what they claim
    assert picked == {g for g, st in expected.items() if st in nsr.IN_FLIGHT_STATES}
    assert "g-incomplete" in picked and "g-complete" not in picked and "g-failed" not in picked


def test_a_state_added_to_the_python_list_without_a_sql_clause_fails_loudly(registry, monkeypatch):
    monkeypatch.setattr(nsr, "IN_FLIGHT_STATES", (*nsr.IN_FLIGHT_STATES, "brand_new_state"))
    with pytest.raises(KeyError):
        registry.list_in_flight_native_survey_runs()


# ── the bounded retry of AWAITING_REPORT / REPORT_INCOMPLETE ────────────────────────────────────────────────────────

class _NoReportPort:
    """Egeria says COMPLETED but has linked no report yet: the row stays AWAITING_REPORT. Counts its reads."""
    def __init__(self):
        self.reads = 0

    def read_action(self, guid):
        self.reads += 1
        return nsr.ActionRead(status="COMPLETED", message="")

    def report_guid_for_action(self, guid):
        return ""


def _awaiting_row(registry, guid="ea-1", retry=None):
    _in_flight_row(registry, guid)
    registry.record_native_survey_readback(guid, read_at="2026-10-21T20:00:00", status="COMPLETED")
    if retry is not None:
        registry.merge_native_survey_declared(guid, {nsr.READBACK_RETRY_KEY: retry})


def _stored(registry, guid="ea-1"):
    row = next(r for r in registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)
               if r["engine_action_guid"] == guid)
    stored = registry.count_native_survey_annotations(row["survey_report_guid"]) if row.get("survey_report_guid") else None
    return row, nsr.readback_retry(row), nsr.derive_native_state(row, stored_annotation_count=stored)


def _recent():
    return datetime.utcnow().isoformat()


def test_a_row_at_attempt_19_is_swept_and_at_attempt_20_gets_the_terminal_word_and_is_skipped(registry):
    _awaiting_row(registry, retry={"state": nsr.AWAITING_REPORT, "attempts": 19, "last_attempt_at": _recent(),
                                   "since": _recent()})
    port = _NoReportPort()
    assert nsr.sweep_in_flight(registry, port) == 1 and port.reads == 1          # attempt 19 -> swept (the 20th try)
    row, rec, derived = _stored(registry)
    assert rec["attempts"] == 20 and rec["state"] == nsr.AWAITING_REPORT and rec["last_attempt_at"]
    assert derived["read_gave_up"] is True
    assert derived["read_gave_up_words"] == "report not read after 20 tries · refresh to retry"
    assert derived["not_checked_since"] == "", "the cue stops past the bound"
    assert nsr.sweep_in_flight(registry, port) == 0 and port.reads == 1          # attempt 20 -> skipped, no read
    assert registry.list_in_flight_native_survey_runs(skip=lambda r: nsr._row_gave_up(registry, r)) == []
    rows = nsr.native_survey_rows(registry, "database", "adventureworks", TECH)
    survey = next(r for r in rows if r["qualified_name"] == SURVEY_QN)
    assert survey["in_flight"] is False and survey["run"]["read_gave_up"] is True


def test_inside_the_bound_the_cue_stays_and_the_state_is_unchanged(registry):
    _awaiting_row(registry, retry={"state": nsr.AWAITING_REPORT, "attempts": 19, "last_attempt_at": _recent(),
                                   "since": _recent()})
    row, rec, derived = _stored(registry)
    assert derived["read_gave_up"] is False and derived["state"] == nsr.AWAITING_REPORT
    old = nsr.derive_native_state({**row, "engine_action_read_at": "2026-10-21T20:00:00"}, now=NOW)
    assert old["not_checked_since"] == "2026-10-21T20:00:00" and old["read_gave_up"] is False


def test_the_24_hour_bound_ends_it_too_and_the_words_carry_the_real_count(registry):
    since = (datetime.utcnow() - timedelta(hours=25)).isoformat()
    _awaiting_row(registry, retry={"state": nsr.AWAITING_REPORT, "attempts": 3, "last_attempt_at": since, "since": since})
    _, _, derived = _stored(registry)
    assert derived["read_gave_up"] is True
    assert derived["read_gave_up_words"] == "report not read after 3 tries · refresh to retry"
    assert nsr.sweep_in_flight(registry, _NoReportPort()) == 0
    young = (datetime.utcnow() - timedelta(hours=23)).isoformat()
    registry.merge_native_survey_declared("ea-1", {nsr.READBACK_RETRY_KEY: {
        "state": nsr.AWAITING_REPORT, "attempts": 3, "last_attempt_at": young, "since": young}})
    assert nsr.sweep_in_flight(registry, _NoReportPort()) == 1


def test_the_report_incomplete_word(registry):
    assert nsr.read_gave_up_words(nsr.REPORT_INCOMPLETE, 20) == "report incomplete after 20 tries · refresh to retry"
    row = _declared_row("2026-10-21T20:57:00", retry={"state": nsr.REPORT_INCOMPLETE, "attempts": 20, "since": "2026-10-21T20:30:00"},
                        engine_action_status="COMPLETED", survey_report_guid="r", report_read_at="t", report_annotation_count=2)
    d = nsr.derive_native_state(row, stored_annotation_count=1, now=NOW)
    assert d["state"] == nsr.REPORT_INCOMPLETE and d["read_gave_up"] is True
    assert d["read_gave_up_words"] == "report incomplete after 20 tries · refresh to retry"


def test_a_count_recorded_for_another_state_does_not_carry_over(registry):
    """The 20 tries were at REPORT_INCOMPLETE; the row is AWAITING_REPORT now: a fresh count, not given up."""
    _awaiting_row(registry, retry={"state": nsr.REPORT_INCOMPLETE, "attempts": 20, "last_attempt_at": _recent(),
                                   "since": _recent()})
    assert _stored(registry)[2]["read_gave_up"] is False
    assert nsr.sweep_in_flight(registry, _NoReportPort()) == 1
    assert _stored(registry)[1]["attempts"] == 1 and _stored(registry)[1]["state"] == nsr.AWAITING_REPORT


def test_a_manual_refresh_resets_the_count_and_reads_again(registry):
    _awaiting_row(registry, retry={"state": nsr.AWAITING_REPORT, "attempts": 20, "last_attempt_at": _recent(),
                                   "since": _recent()})
    assert _stored(registry)[2]["read_gave_up"] is True
    port = _NoReportPort()
    assert nsr.refresh_resource(registry, port, "database", "adventureworks") == 1 and port.reads == 1
    _, rec, derived = _stored(registry)
    assert rec == {} and derived["read_gave_up"] is False, "the counter starts again"
    assert nsr.sweep_in_flight(registry, port) == 1                              # and the sweep counts it from 1
    assert _stored(registry)[1]["attempts"] == 1


def test_a_poll_of_a_row_still_inside_its_bound_does_not_reset_the_sweeps_count(registry):
    _awaiting_row(registry, retry={"state": nsr.AWAITING_REPORT, "attempts": 7, "last_attempt_at": _recent(),
                                   "since": _recent()})
    nsr.refresh_resource(registry, _NoReportPort(), "database", "adventureworks")
    assert _stored(registry)[1]["attempts"] == 7


def test_the_count_is_stored_on_the_proof_row_in_declared_json_and_cleared_when_the_report_lands(registry):
    _awaiting_row(registry)
    nsr.sweep_in_flight(registry, _NoReportPort())
    row, rec, _ = _stored(registry)
    assert set(rec) == {"state", "attempts", "last_attempt_at", "since"}
    assert json.loads(row["declared"])[nsr.READBACK_RETRY_KEY] == rec            # no new column: the existing JSON

    nsr.sweep_in_flight(registry, _Port([_ann(1)]))                              # the report is now there
    row, rec, derived = _stored(registry)
    assert derived["state"] == nsr.COMPLETE and rec == {}


def test_the_refresh_route_also_reads_a_row_that_gave_up(registry, monkeypatch):
    """The route's guard (`in_flight or read_gave_up`): without it "refresh to retry" would never reach the read."""
    import inspect

    from resource_explorer.web.routes import native_surveys

    assert "read_gave_up" in inspect.getsource(native_surveys.refresh_native_surveys)


# ── N2: the report and its totals land together ─────────────────────────────────────────────────────────────────────

def test_a_failing_totals_write_leaves_no_half_stored_report(registry, monkeypatch):
    _in_flight_row(registry)

    def boom(conn, guid, patch):
        raise RuntimeError("totals write failed")
    monkeypatch.setattr(ProjectRegistry, "_merge_declared_in", staticmethod(boom))
    with pytest.raises(RuntimeError):
        registry.record_native_survey_report("ea-1", entity_type="database", slug="adventureworks",
                                             process_qualified_name=SURVEY_QN, report_guid="rep-n2", report_at="t",
                                             read_at="t", annotations=[_ann(1), _ann(2)],
                                             report_totals={"on_report": 2, "not_read_by_design": 0})
    row = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    assert registry.count_native_survey_annotations("rep-n2") == 0 and not row["report_read_at"]
    assert not row["survey_report_guid"], "one transaction: nothing of the report is stored when the totals fail"


def test_the_read_back_says_the_report_could_not_be_read_only_when_nothing_was_stored(registry, monkeypatch):
    _in_flight_row(registry)
    monkeypatch.setattr(ProjectRegistry, "_merge_declared_in", staticmethod(lambda *a: (_ for _ in ()).throw(RuntimeError("x"))))
    run = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    derived = nsr.refresh_run(registry, _Port([_ann(1)]), run, entity_type="database", slug="adventureworks")
    assert "report read failed" in derived["error"]
    assert derived["state"] == nsr.AWAITING_REPORT and derived["report_guid"] == "", "true: nothing of it is stored"


def test_the_merge_is_a_locking_read_on_postgres_and_removes_a_key_patched_to_none(registry):
    import inspect

    assert "FOR UPDATE" in inspect.getsource(ProjectRegistry._merge_declared_in)
    _in_flight_row(registry)
    registry.merge_native_survey_declared("ea-1", {"a": 1, "b": 2})
    registry.merge_native_survey_declared("ea-1", {"a": None})
    row = registry.list_native_survey_runs("database", "adventureworks", SURVEY_QN)[0]
    assert json.loads(row["declared"]) == {"b": 2}


# ── N4: the count label never shows an impossible sum ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entity_type,on_report,read,by_design,expected", [
    ("filesystem", 7, 6, 1, "7 annotations on the report; 6 read into RE; 1 not read by design (file-name log)"),
    ("filesystem", 6, 6, 0, "6 annotations on the report · read into RE"),      # 0 by design: no clause
    ("database", 7, 7, 0, "7 annotations on the report · read into RE"),
    ("database", 1, 1, 0, "1 annotation on the report · read into RE"),
    ("database", 7, 5, 0, "7 annotations on the report; 5 read into RE; 2 not stored"),
    ("database", 3, 5, 0, "3 annotations on the report; 5 read into RE; 2 more stored than the report lists"),
    ("filesystem", 7, 8, 1, "7 annotations on the report; 8 read into RE; 1 not read by design (file-name log); "
                            "2 more stored than the report lists"),
])
def test_the_count_label_clauses(entity_type, on_report, read, by_design, expected):
    derived = {"annotations_on_report": on_report, "annotation_count": read, "annotations_not_read_by_design": by_design}
    assert nsr.annotation_count_label(derived, entity_type) == expected
