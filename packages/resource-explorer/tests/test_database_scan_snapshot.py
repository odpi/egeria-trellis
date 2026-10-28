"""Brief D (7f.1 / 7f.2 / 7f.3 / 7f.5): one scan gives ONE database snapshot,
with sizes, a recorded stats-reset word, and activity all on the same row.

Found live 2026-10-09 on `localhost_docker_egeria_advisor`: a Database
Scouting Scan dispatches each step as its own `DatabaseSurveyor.survey()` call,
and each call stamped its own `surveyed_at`, so one scan left THREE
`database_surveys` rows 2-6 s apart. Per-table activity sat on the first only;
the later two had sizes and no activity, so which one a reader called "latest"
changed the answer.

What is pinned here:

* the executor hands every step of one scan the SAME scan time
  (`scan_surveyed_at`), prerequisite auto-runs included;
* the database adapter's runners pass it on to `DatabaseSurveyor.survey()`;
* three steps sharing one scan time leave ONE `database_surveys` row that
  carries the tables, their sizes AND the activity -- a later step does not
  wipe an earlier step's activity or coverage (the detail writers REPLACE per
  `(slug, surveyed_at, source)`, so a shared key is only safe because the
  back-fill is merge-aware);
* a stale `get_database_surveys` cache never serves the pre-merge blob;
* a size the database could not give stays NULL (never 0); the schema total is
  a sum only when every table in it was sized;
* "never reset" (Postgres NULL) is stored as a word, distinct from "not read"
  (NULL), and Change Rates treats never->timestamp as a reset.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
from resource_explorer.surveyors.survey_definition_executor import (
    SurveyDefinitionExecutor,
    get_adapter,
)

FULL_CAPS = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)
SCAN_AT = "2026-10-09T21:49:03.000001"


class _FakeConnection:
    def __init__(self, schema_info, statistics=None, views=None):
        self._schema_info = schema_info
        self._statistics = statistics or {}
        self._views = views or []

    def get_schema_info(self):
        return self._schema_info

    def get_statistics(self):
        return self._statistics

    def get_views(self):
        return self._views

    @property
    def capabilities(self):
        return FULL_CAPS


@contextmanager
def _patched_connection(conn):
    with patch(
        "resource_explorer.surveyors.database.database_surveyor.database_connection",
    ) as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = conn
        mock_ctx.return_value.__exit__.return_value = False
        yield


def _table(name):
    return {
        "name": name, "type": "BASE TABLE", "description": "",
        "columns": [{
            "name": "id", "type": "integer", "base_type": "integer",
            "nullable": False, "default": None, "position": 1,
            "description": "", "is_primary_key": True, "foreign_key": None,
            "source": "information_schema",
        }],
        "source": "information_schema",
    }


def _schema_info(*names):
    names = names or ("orders",)
    return {
        "schemas": [{"name": "public", "description": "",
                     "tables": [_table(n) for n in names]}],
        "total_tables": len(names), "total_columns": len(names),
    }


def _statistics(names=("orders",), sizes=None, stats_reset="2026-01-01T00:00:00",
                reset_evidence="2026-01-01T00:00:00"):
    sizes = {n: 8192 for n in names} if sizes is None else sizes
    return {
        "stats_reset": stats_reset,
        "stats_reset_evidence": reset_evidence,
        "row_stats": [{"schemaname": "public", "tablename": n, "row_count": 5,
                       "last_analyzed": "", "last_vacuumed": "", "pending_changes": 0}
                      for n in names],
        "table_stats": [{"schemaname": "public", "tablename": n,
                         "total_bytes": sizes[n], "total_size": "x"}
                        for n in names if n in sizes],
        "column_stats": [],
        "table_activity": [{
            "schemaname": "public", "tablename": n,
            "rows_inserted": 761184, "rows_updated": 1435, "rows_deleted": 0,
            "hot_updates": 0, "live_tuples": 761184, "dead_tuples": 0,
            "seq_scan": 861, "idx_scan": 0,
            "last_vacuum": "", "last_autovacuum": "", "last_analyze": "",
            "last_autoanalyze": "", "pending_changes": 0,
        } for n in names],
    }


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def db_entity(registry):
    entity = DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
        db_user="admin", db_password="secret",
    )
    registry.register_database(entity)
    return entity


def _surveyor(db_entity, registry):
    return DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)


def _three_step_scan(registry, db_entity, at=SCAN_AT, statistics=None):
    """The scouting scan's real shape: a statistics-bearing step first, then two
    steps that collect none (operations / credential_capability style)."""
    s = _surveyor(db_entity, registry)
    with _patched_connection(_FakeConnection(_schema_info(), statistics or _statistics())):
        s.survey(steps=["schema", "statistics"], surveyed_at=at)
    with _patched_connection(_FakeConnection(_schema_info())):
        s.survey(steps=["schema"], surveyed_at=at)
    with _patched_connection(_FakeConnection(_schema_info(), views=[])):
        s.survey(steps=["schema", "views"], surveyed_at=at)


# ── 7f.1 / 7f.5: one scan, one snapshot ─────────────────────────────────────


class TestExecutorSharesOneScanTime:
    def test_every_step_of_a_run_gets_the_same_scan_surveyed_at(
            self, registry, db_entity, monkeypatch):
        adapter = get_adapter("database")
        seen: dict[str, object] = {}

        def schema_and_stats(entity, reg, **kwargs):
            seen["postgres_schema_and_stats"] = kwargs.get("scan_surveyed_at")
            reg.write_detail_rows(
                "database_tables", entity.slug, "SNAP", "local",
                [{"schema_name": "public", "table_name": "t1"}])
            return {"schema_info": {}, "statistics": {}}

        def column_profile(entity, reg, **kwargs):
            seen["postgres_column_profile"] = kwargs.get("scan_surveyed_at")
            return {"column_profile": {"columns": []}}

        monkeypatch.setitem(adapter.re_analysis_steps,
                            "postgres_schema_and_stats", schema_and_stats)
        monkeypatch.setitem(adapter.re_analysis_steps,
                            "postgres_column_profile", column_profile)

        result = SurveyDefinitionExecutor(registry).run_synthetic_step(
            "database", "mydb", "postgres_column_profile",
            executes_at="resource-explorer")

        assert seen["postgres_schema_and_stats"], "the producer got no scan time"
        assert seen["postgres_schema_and_stats"] == seen["postgres_column_profile"], (
            "an auto-run prerequisite and the step that demanded it are one scan "
            "and must share one surveyed_at")
        assert result["surveyed_at"] == seen["postgres_column_profile"]


class TestAdapterRunnersPassTheScanTimeOn:
    @pytest.mark.parametrize("runner_name", [
        "_run_postgres_schema_and_stats", "_run_postgres_operations",
        "_run_credential_capability", "_run_postgres_sql_analysis",
        "_run_postgres_nested_columns",
    ])
    def test_runner_hands_surveyed_at_to_survey(self, runner_name, registry, db_entity):
        from resource_explorer.surveyors.database import survey_definition_adapter as sda

        captured = {}

        def fake_survey(self, steps=None, **kw):
            captured["surveyed_at"] = kw.get("surveyed_at")
            return {"schema_info": {}, "statistics": {}}

        with patch.object(DatabaseSurveyor, "survey", fake_survey):
            getattr(sda, runner_name)(db_entity, registry, db_user="a", db_pwd="b",
                                      scan_surveyed_at=SCAN_AT)
        assert captured["surveyed_at"] == SCAN_AT


class TestOneScanIsOneSnapshot:
    def test_three_steps_leave_one_database_surveys_row(self, registry, db_entity):
        _three_step_scan(registry, db_entity)
        rows = registry.get_database_surveys(db_entity.slug)
        assert [r["surveyed_at"] for r in rows] == [SCAN_AT], (
            "one scan must be ONE database_surveys row, not one per step")

    def test_that_row_carries_tables_with_sizes_and_the_activity(self, registry, db_entity):
        _three_step_scan(registry, db_entity)
        tables = registry.query_detail_rows("database_tables", db_entity.slug, SCAN_AT)
        assert tables and tables[0]["size_bytes"] == 8192
        activity = registry.query_detail_rows(
            "database_table_activity", db_entity.slug, SCAN_AT)
        assert activity and activity[0]["rows_inserted"] == 761184, (
            "a later step of the same scan must not wipe the activity an earlier "
            "step measured")

    def test_activity_coverage_is_still_measured_after_the_later_steps(
            self, registry, db_entity):
        _three_step_scan(registry, db_entity)
        cov = registry.get_section_coverage("database", db_entity.slug, SCAN_AT)
        assert cov["table_activity"]["state"] == "measured"
        assert cov["table_activity"]["row_count"] == 1

    def test_the_stored_blob_is_the_merged_one(self, registry, db_entity):
        import json
        _three_step_scan(registry, db_entity)
        blob = json.loads(registry.get_latest_database_survey(db_entity.slug)["survey_data"])
        assert blob["statistics"]["table_activity"], (
            "the last step's blob must keep the statistics an earlier step collected")

    def test_a_primed_cache_does_not_serve_the_pre_merge_row(self, registry, db_entity):
        import json
        s = _surveyor(db_entity, registry)
        with _patched_connection(_FakeConnection(_schema_info())):
            s.survey(steps=["schema"], surveyed_at=SCAN_AT)
        before = registry.get_database_surveys(db_entity.slug)        # primes the cache
        assert not json.loads(before[0]["survey_data"]).get("statistics")
        with _patched_connection(_FakeConnection(_schema_info(), _statistics())):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        after = registry.get_database_surveys(db_entity.slug)
        assert len(after) == 1
        assert json.loads(after[0]["survey_data"]).get("statistics"), (
            "the merged write was not seen through the freshness-keyed cache")

    def test_a_publish_mark_on_the_row_survives_a_later_step(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        with _patched_connection(_FakeConnection(_schema_info(), _statistics())):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        registry.record_database_survey_published(db_entity.slug, SCAN_AT, "guid-1")
        with _patched_connection(_FakeConnection(_schema_info())):
            s.survey(steps=["schema"], surveyed_at=SCAN_AT)
        row = registry.get_database_surveys(db_entity.slug)[0]
        assert row.get("published_at"), "the merge dropped the row's published_at"

    def test_two_scans_stay_two_snapshots(self, registry, db_entity):
        _three_step_scan(registry, db_entity, at="2026-10-09T21:00:00.000001")
        _three_step_scan(registry, db_entity, at="2026-10-09T22:00:00.000001")
        assert len(registry.get_database_surveys(db_entity.slug)) == 2

    def test_runs_without_a_scan_time_still_write_a_row_each(self, registry, db_entity):
        """Unchanged behaviour: a lone per-card run keeps its own surveyed_at."""
        s = _surveyor(db_entity, registry)
        for _ in range(2):
            with _patched_connection(_FakeConnection(_schema_info(), _statistics())):
                s.survey(steps=["schema", "statistics"])
        assert len(registry.get_database_surveys(db_entity.slug)) == 2


# ── 7f.2: sizes ────────────────────────────────────────────────────────────


class TestSizes:
    def test_a_table_the_database_could_not_size_stays_null_not_zero(
            self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        stats = _statistics(("orders", "items"))
        stats["table_stats"][1]["total_bytes"] = None       # pg gave NULL
        with _patched_connection(_FakeConnection(_schema_info("orders", "items"), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        by = {t["table_name"]: t for t in registry.query_detail_rows(
            "database_tables", db_entity.slug, SCAN_AT)}
        assert by["orders"]["size_bytes"] == 8192
        assert by["items"]["size_bytes"] is None, "an unread size must not become 0"

    def test_schema_total_is_the_sum_when_every_table_was_sized(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        stats = _statistics(("orders", "items"), sizes={"orders": 100, "items": 50})
        with _patched_connection(_FakeConnection(_schema_info("orders", "items"), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        schemas = registry.query_detail_rows("database_schemas", db_entity.slug, SCAN_AT)
        assert schemas[0]["total_table_size_bytes"] == 150

    def test_schema_total_is_null_when_any_table_is_unsized(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        stats = _statistics(("orders", "items"), sizes={"orders": 100})   # items not read
        with _patched_connection(_FakeConnection(_schema_info("orders", "items"), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        schemas = registry.query_detail_rows("database_schemas", db_entity.slug, SCAN_AT)
        assert schemas[0]["total_table_size_bytes"] is None, (
            "a partial sum is a confident wrong total")


# ── 7f.3: the stats-reset word ─────────────────────────────────────────────


class TestStatsResetStorage:
    def _activity(self, registry, db_entity, evidence):
        s = _surveyor(db_entity, registry)
        stats = _statistics(stats_reset="" if evidence in (None, "never") else evidence,
                            reset_evidence=evidence)
        with _patched_connection(_FakeConnection(_schema_info(), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        return registry.query_detail_rows(
            "database_table_activity", db_entity.slug, SCAN_AT)[0]

    def test_a_reset_time_is_stored(self, registry, db_entity):
        assert self._activity(registry, db_entity, "2026-10-01 00:00:00+00")[
            "stats_reset"] == "2026-10-01 00:00:00+00"

    def test_never_reset_is_stored_as_the_never_word(self, registry, db_entity):
        from resource_explorer.registry import STATS_NEVER_RESET
        assert self._activity(registry, db_entity, STATS_NEVER_RESET)[
            "stats_reset"] == STATS_NEVER_RESET

    def test_not_read_stays_null_and_differs_from_never(self, registry, db_entity):
        assert self._activity(registry, db_entity, None)["stats_reset"] is None


class TestConnectionReadsTheResetWord:
    def _conn(self, rows=None, raises=False):
        from resource_explorer.surveyors.database.connection import PostgreSQLConnection
        c = PostgreSQLConnection("h", 5432, "d", "u", "p")

        def execute_query(q, params=()):
            if raises:
                raise RuntimeError("no privilege")
            return rows
        c.execute_query = execute_query
        return c

    def test_timestamp(self):
        from datetime import datetime
        c = self._conn([{"stats_reset": datetime(2026, 10, 1, 3, 4, 5)}])
        assert "2026-10-01" in c.get_stats_reset_evidence()

    def test_null_in_postgres_is_never_reset(self):
        from resource_explorer.registry import STATS_NEVER_RESET
        assert self._conn([{"stats_reset": None}]).get_stats_reset_evidence() == STATS_NEVER_RESET

    def test_a_failed_read_is_none_not_never(self):
        assert self._conn(raises=True).get_stats_reset_evidence() is None

    def test_no_row_for_the_database_is_none(self):
        assert self._conn([]).get_stats_reset_evidence() is None

    def test_legacy_get_stats_reset_is_unchanged(self):
        assert self._conn([{"stats_reset": None}]).get_stats_reset() == ""


class TestChangeRatesAcrossAReset:
    def _seed(self, registry, db_entity, old_reset, new_reset):
        for at, reset, ins in (("2026-10-01T00:00:00", old_reset, 100),
                               ("2026-10-02T00:00:00", new_reset, 150)):
            registry.record_database_survey(
                slug=db_entity.slug, schema_count=1, table_count=1, column_count=1,
                survey_data={"schema_info": _schema_info()}, surveyed_at=at)
            registry.write_detail_rows(
                "database_table_activity", db_entity.slug, at,
                rows=[{"schema_name": "public", "table_name": "orders",
                       "rows_inserted": ins, "rows_updated": 0, "rows_deleted": 0,
                       "stats_reset": reset, "state": "measured"}],
                coverage_section="table_activity")

    def _entry(self, registry, db_entity):
        from resource_explorer.surveyors.database.db_derived import (
            derive_change_rates, load_inputs)
        res = derive_change_rates(registry, load_inputs(registry, db_entity.slug))
        return res["per_table"][0]

    def test_never_then_a_timestamp_is_a_reset_not_a_rate(self, registry, db_entity):
        from resource_explorer.registry import STATS_NEVER_RESET
        self._seed(registry, db_entity, STATS_NEVER_RESET, "2026-10-01T12:00:00")
        assert self._entry(registry, db_entity)["change"] == "counters_reset"

    def test_never_then_never_is_a_rate(self, registry, db_entity):
        from resource_explorer.registry import STATS_NEVER_RESET
        self._seed(registry, db_entity, STATS_NEVER_RESET, STATS_NEVER_RESET)
        assert self._entry(registry, db_entity)["change"] == "active"

    def test_not_read_then_a_timestamp_makes_no_reset_claim(self, registry, db_entity):
        self._seed(registry, db_entity, None, "2026-10-01T12:00:00")
        assert self._entry(registry, db_entity)["change"] == "active"


class TestCatalogueActivityWordForNeverReset:
    def test_never_reset_is_named_not_called_a_missing_date(self):
        from resource_explorer.catalogue_scope import activity_for
        from resource_explorer.registry import STATS_NEVER_RESET

        zero = activity_for({"writes": 0, "reset": STATS_NEVER_RESET,
                             "at": "2026-10-09T10:00:00"})
        assert zero["state"] == "cant_tell"
        assert zero["reason"] == "counters never reset"
        assert "never reset" in zero["text"]
        busy = activity_for({"writes": 12, "reset": STATS_NEVER_RESET,
                             "at": "2026-10-09T10:00:00"})
        assert busy["state"] == "active" and "never reset" in busy["text"]

    def test_a_missing_reset_keeps_its_old_words(self):
        from resource_explorer.catalogue_scope import activity_for
        assert activity_for({"writes": 0, "reset": "", "at": "2026-10-09T10:00:00"})[
            "text"] == "can't tell · reset date not recorded"


class TestPrefectStepsShareTheScanTime:
    def test_a_planned_database_step_receives_scan_surveyed_at(self, monkeypatch):
        from resource_explorer.prefect import flows

        seen = {}

        def fake_step(**kw):
            seen.update(kw["runner_kwargs"])
            return {"ok": True}

        monkeypatch.setattr(flows.run_surveyor_step_task, "fn", fake_step)
        monkeypatch.setattr(flows, "_record_step_cost", lambda *a, **k: None)
        out = flows.run_planned_step_task.fn(
            entity_type="database", slug="mydb", step_key="postgres_operations",
            qualified_name="S::ops", runner_kwargs={}, upstream=[], guarded_by={},
            surveyed_at=SCAN_AT)
        assert out["status"] == "ok"
        assert seen.get("scan_surveyed_at") == SCAN_AT

    def test_other_entity_types_are_left_alone(self, monkeypatch):
        from resource_explorer.prefect import flows

        seen = {}
        monkeypatch.setattr(flows.run_surveyor_step_task, "fn",
                            lambda **kw: seen.update(kw["runner_kwargs"]) or {})
        monkeypatch.setattr(flows, "_record_step_cost", lambda *a, **k: None)
        flows.run_planned_step_task.fn(
            entity_type="filesystem", slug="fs", step_key="x", qualified_name="S::x",
            runner_kwargs={}, upstream=[], guarded_by={}, surveyed_at=SCAN_AT)
        assert "scan_surveyed_at" not in seen


# ── per-table heap / index sizes (Brief D, owner-approved columns) ─────────


def _sized_stats(names, index=None, heap=None):
    stats = _statistics(names, sizes={n: 8192 for n in names})
    for row in stats["table_stats"]:
        n = row["tablename"]
        row["relation_bytes"] = (heap or {}).get(n, 4096)
        row["index_bytes"] = (index or {}).get(n, 4096)
    return stats


class TestIndexBytesColumns:
    def test_fresh_registry_has_both_columns(self, registry):
        with registry._conn() as conn:
            cols = registry._get_table_columns(conn, "database_tables")
        assert {"table_bytes", "index_bytes"} <= set(cols)

    def test_migration_adds_them_to_a_registry_that_lacks_them_and_is_idempotent(
            self, tmp_path):
        import sqlite3
        from resource_explorer.registry import _DB_FS_DETAIL_TABLE_DDL

        path = str(tmp_path / "old.db")
        ddl = next(d for d in _DB_FS_DETAIL_TABLE_DDL
                   if "CREATE TABLE IF NOT EXISTS database_tables" in d)
        old = "\n".join(l for l in ddl.splitlines()
                        if "table_bytes" not in l and "index_bytes" not in l
                        and "Brief D: total" not in l)
        assert "index_bytes" not in old
        raw = sqlite3.connect(path)
        raw.executescript(old)
        raw.close()
        for _ in range(2):                      # second start: a no-op, no error
            reg = ProjectRegistry(db_path=path)
            with reg._conn() as conn:
                cols = set(reg._get_table_columns(conn, "database_tables"))
            assert {"table_bytes", "index_bytes"} <= cols

    def test_a_scan_fills_index_bytes_for_every_sized_table(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        stats = _sized_stats(("orders", "items"), index={"orders": 100, "items": 50},
                             heap={"orders": 7, "items": 8})
        with _patched_connection(_FakeConnection(_schema_info("orders", "items"), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        by = {t["table_name"]: t for t in registry.query_detail_rows(
            "database_tables", db_entity.slug, SCAN_AT)}
        assert by["orders"]["index_bytes"] == 100 and by["orders"]["table_bytes"] == 7
        assert by["items"]["index_bytes"] == 50 and by["items"]["table_bytes"] == 8

    def test_an_unread_index_size_stays_null(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        stats = _sized_stats(("orders",))
        stats["table_stats"][0]["index_bytes"] = None
        with _patched_connection(_FakeConnection(_schema_info(), stats)):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        t = registry.query_detail_rows("database_tables", db_entity.slug, SCAN_AT)[0]
        assert t["index_bytes"] is None and t["size_bytes"] == 8192

    def test_a_later_step_of_the_scan_keeps_the_index_sizes(self, registry, db_entity):
        s = _surveyor(db_entity, registry)
        with _patched_connection(_FakeConnection(_schema_info(), _sized_stats(("orders",)))):
            s.survey(steps=["schema", "statistics"], surveyed_at=SCAN_AT)
        with _patched_connection(_FakeConnection(_schema_info())):
            s.survey(steps=["schema"], surveyed_at=SCAN_AT)
        t = registry.query_detail_rows("database_tables", db_entity.slug, SCAN_AT)[0]
        assert t["index_bytes"] == 4096

    def test_rollup_is_a_sum_only_when_every_base_table_was_read(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            index_bytes_total)
        t = lambda i, ty="BASE TABLE": {"index_bytes": i, "table_type": ty}  # noqa: E731
        assert index_bytes_total([t(10), t(5)]) == 15
        assert index_bytes_total([t(10), t(None)]) is None
        assert index_bytes_total([t(10), t(None, "VIEW")]) == 10, "views own no storage"
        assert index_bytes_total([]) is None
        assert index_bytes_total([t(0)]) == 0, "a read zero is a zero"

    def test_change_rates_show_index_drift_separately(self, registry, db_entity):
        for at, ins, idx in (("2026-10-01T00:00:00", 100, 1000),
                             ("2026-10-02T00:00:00", 150, 1600)):
            registry.record_database_survey(
                slug=db_entity.slug, schema_count=1, table_count=1, column_count=1,
                survey_data={"schema_info": _schema_info()}, surveyed_at=at)
            registry.write_detail_rows(
                "database_tables", db_entity.slug, at,
                rows=[{"schema_name": "public", "table_name": "orders",
                       "size_bytes": 5000, "index_bytes": idx, "state": "measured"}],
                coverage_section="tables")
            registry.write_detail_rows(
                "database_table_activity", db_entity.slug, at,
                rows=[{"schema_name": "public", "table_name": "orders",
                       "rows_inserted": ins, "rows_updated": 0, "rows_deleted": 0,
                       "state": "measured"}],
                coverage_section="table_activity")
        from resource_explorer.surveyors.database.db_derived import (
            derive_change_rates, load_inputs)
        e = derive_change_rates(registry, load_inputs(registry, db_entity.slug))["per_table"][0]
        assert e["index_bytes_delta"] == 600 and e["size_bytes_delta"] == 0
