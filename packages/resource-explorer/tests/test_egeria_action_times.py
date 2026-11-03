"""Egeria's own request and start times for a native survey's engine action, kept on `step_runs`
(`engine_action_requested_at`, `engine_action_started_at`: ISO UTC TEXT, nullable). Owner, 2026-10-12.

Registries are a tmp SQLite file or the throwaway Postgres test schema (`pg_registry`); never the real one.
No Egeria: the engine-action element is faked at the client boundary."""
from __future__ import annotations

from datetime import datetime

import pytest

from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

NEW = {"engine_action_requested_at", "engine_action_started_at"}
GUID = "ea-times-1"


def _columns(reg) -> set[str]:
    with reg._conn() as conn:
        return set(reg._get_table_columns(conn, "step_runs"))


def _row(reg, guid=GUID) -> dict:
    with reg._conn() as conn:
        return dict(conn.execute("SELECT * FROM step_runs WHERE engine_action_guid = ?", (guid,)).fetchone())


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="db1", display_name="DB1", db_type="postgresql",
                                       host="localhost", port=5442, database_name="db1"))
    return r


def _submit(reg):
    with reg._conn() as conn:
        conn.execute("INSERT INTO step_runs (slug, entity_type, step_key, surveyed_at, engine_action_guid) "
                     "VALUES ('db1', 'database', ?, '2026-10-09T01:41:00', ?)",
                     (nsr.NATIVE_SURVEY_STEP_PREFIX + "proc", GUID))


# ── the migration ───────────────────────────────────────────────────────────────────────────────

def test_columns_are_added_once_and_a_second_init_is_a_no_op(tmp_path):
    path = str(tmp_path / "twice.db")
    first = _columns(ProjectRegistry(db_path=path))
    assert NEW <= first and _columns(ProjectRegistry(db_path=path)) == first


def test_an_old_step_runs_table_gains_the_columns_and_keeps_its_rows(tmp_path):
    path = str(tmp_path / "old.db")
    r = ProjectRegistry(db_path=path)
    _submit(r)
    with r._conn() as conn:                               # rebuild the table as it was before the change
        conn.execute("ALTER TABLE step_runs DROP COLUMN engine_action_requested_at")
        conn.execute("ALTER TABLE step_runs DROP COLUMN engine_action_started_at")
    assert not NEW & _columns(r)
    again = ProjectRegistry(db_path=path)
    assert NEW <= _columns(again)
    assert _row(again)["engine_action_requested_at"] is None and _row(again)["engine_action_started_at"] is None


def test_the_migration_is_idempotent_on_postgres(pg_registry, monkeypatch):
    assert "resource_explorer_test" in pg_registry.database_url
    before = _columns(pg_registry)
    monkeypatch.setattr(ProjectRegistry, "_pg_schema_ready", set())
    again = ProjectRegistry(database_url=pg_registry.database_url)
    assert NEW <= before and _columns(again) == before


def test_an_old_table_is_altered_on_postgres_and_a_blind_second_process_does_not_fail(pg_registry, monkeypatch):
    assert "resource_explorer_test" in pg_registry.database_url
    with pg_registry._conn() as conn:
        for c in NEW:
            conn.execute(f"ALTER TABLE step_runs DROP COLUMN IF EXISTS {c}")
    monkeypatch.setattr(ProjectRegistry, "_pg_schema_ready", set())
    assert NEW <= _columns(ProjectRegistry(database_url=pg_registry.database_url))
    real = ProjectRegistry._get_table_columns                         # a second process that still sees them missing
    monkeypatch.setattr(ProjectRegistry, "_get_table_columns",
                        lambda self, conn, t: real(self, conn, t) - NEW if t == "step_runs" else real(self, conn, t))
    monkeypatch.setattr(ProjectRegistry, "_pg_schema_ready", set())
    again = ProjectRegistry(database_url=pg_registry.database_url)
    monkeypatch.setattr(ProjectRegistry, "_get_table_columns", real)
    assert NEW <= _columns(again)


# ── epoch milliseconds -> ISO ───────────────────────────────────────────────────────────────────

def test_epoch_ms_become_naive_utc_iso_and_missing_stays_none():
    assert nsr.epoch_ms_to_iso(1791723341925) == "2026-10-11T12:55:41.925000"
    assert nsr.epoch_ms_to_iso({"primitiveValue": 1791723341925}) == "2026-10-11T12:55:41.925000"
    assert nsr.epoch_ms_to_iso(1791723341) is None            # seconds, not milliseconds: unknown, never 1970
    assert nsr.epoch_ms_to_iso(99_999_999_999) is None and nsr.epoch_ms_to_iso(-5) is None
    for missing in (None, "", 0, "x", {}, {"primitiveValue": None}, True):
        assert nsr.epoch_ms_to_iso(missing) is None


def _element(props):
    return {"elementProperties": {"propertyValueMap": props}}


def _port(props):
    p = nsr.PyegeriaSurveyPort.__new__(nsr.PyegeriaSurveyPort)
    p._enter = lambda: None
    class Expert:
        def get_metadata_element_by_guid(self, guid):
            return _element(props)
    p._get_expert = lambda: Expert()
    return p


def test_the_real_port_reads_both_times_from_the_element_and_leaves_missing_ones_none():
    both = _port({"activityStatus": {"symbolicName": "IN_PROGRESS"},
                  "requestedStartTime": {"primitiveValue": 1791550000000}, "startTime": {"primitiveValue": 1791723341925}})
    a = both.read_action(GUID)
    assert (a.status, a.requested_at, a.started_at) == ("IN_PROGRESS", "2026-10-09T12:46:40", "2026-10-11T12:55:41.925000")
    waiting = _port({"activityStatus": {"symbolicName": "REQUESTED"}, "requestedStartTime": {"primitiveValue": 1791550000000}})
    w = waiting.read_action(GUID)
    assert w.requested_at == "2026-10-09T12:46:40" and w.started_at is None
    assert _port({"activityStatus": {"symbolicName": "REQUESTED"}}).read_action(GUID).requested_at is None


# ── the refresh writes them ─────────────────────────────────────────────────────────────────────

class _Port:
    def __init__(self, action):
        self.action = action

    def read_action(self, guid):
        return self.action

    def report_guid_for_action(self, guid):
        return None


def _refresh(reg, action):
    run = reg.list_native_survey_runs("database", "db1")[0]
    return nsr.refresh_run(reg, _Port(action), run, entity_type="database", slug="db1")


def test_the_refresh_stores_both_times_with_the_status_in_one_update(registry):
    _submit(registry)
    _refresh(registry, nsr.ActionRead("IN_PROGRESS", "", "2026-10-09T12:46:40", "2026-10-11T12:55:41.925000"))
    row = _row(registry)
    assert (row["engine_action_status"], row["engine_action_requested_at"], row["engine_action_started_at"]) == (
        "IN_PROGRESS", "2026-10-09T12:46:40", "2026-10-11T12:55:41.925000")


def test_a_read_without_times_stores_null_and_never_erases_what_was_stored(registry):
    _submit(registry)
    _refresh(registry, nsr.ActionRead("REQUESTED", ""))
    assert _row(registry)["engine_action_requested_at"] is None and _row(registry)["engine_action_started_at"] is None
    _refresh(registry, nsr.ActionRead("IN_PROGRESS", "", "2026-10-09T12:46:40", "2026-10-11T12:15:41"))
    _refresh(registry, nsr.ActionRead("IN_PROGRESS", ""))                       # a read that states none
    assert _row(registry)["engine_action_started_at"] == "2026-10-11T12:15:41"


def test_a_failed_read_leaves_the_times_alone(registry):
    _submit(registry)
    _refresh(registry, nsr.ActionRead("IN_PROGRESS", "", "2026-10-09T12:46:40", "2026-10-11T12:15:41"))
    registry.record_native_survey_readback(GUID, read_at="2026-10-11T13:00:00", error="refused")
    assert _row(registry)["engine_action_started_at"] == "2026-10-11T12:15:41"


# ── the derived state carries queued and running ────────────────────────────────────────────────

def _derive(**kw):
    run = {"surveyed_at": "2026-10-09T01:41:00", "engine_action_guid": GUID, "engine_action_status": "IN_PROGRESS",
           "engine_action_message": "", "engine_action_read_at": "2026-10-11T23:00:00", "engine_action_read_error": ""}
    run.update(kw)
    return nsr.derive_native_state(run, now=datetime(2026, 10, 12, 0, 0, 0))


def test_queued_and_running_are_derived_from_the_stored_times():
    d = _derive(engine_action_requested_at="2026-10-09T01:41:00", engine_action_started_at="2026-10-11T13:00:00")
    assert d["queued_s"] == (datetime(2026, 10, 11, 13) - datetime(2026, 10, 9, 1, 41)).total_seconds()
    assert d["running_s"] == 11 * 3600
    assert d["started_at"] == "2026-10-11T13:00:00"


def test_without_stored_times_both_are_none_and_waiting_has_no_running_time():
    d = _derive()
    assert d["queued_s"] is None and d["running_s"] is None
    w = _derive(engine_action_status="WAITING", engine_action_requested_at="2026-10-09T01:41:00")
    assert w["phase"] == "waiting" and w["running_s"] is None and w["requested_at"] == "2026-10-09T01:41:00"
