"""The structured-table twin of `test_store_results_preserves_prior_stats.py`
and `test_store_results_preserves_prior_operations.py`, one layer down:
`database_table_activity`/`database_column_profiles` are written per
`(slug, surveyed_at, source)` row, not per table, so a run whose own steps
never requested `"statistics"` must write NO rows for those tables at its
OWN `surveyed_at` — not a row per table with every counter NULL.

BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §B, found live on `laz_local_adventureworks`
(2026-09-27, `docs/Backlog.md`): of 8 recorded survey runs, only 2 carried
real tuple counters; `db_derived.load_inputs()` picked ONE `surveyed_at` for
every structured table (the newest overall), so it read the newest run's own
`database_table_activity` rows — which, for a run whose steps did include
"statistics" but genuinely found nothing in `pg_stat_user_tables` for a given
connection, are all `state=not_collected`/`not_supported` with every counter
NULL — instead of an earlier run's real measurement. `db_classification`
then reported "No data for: activity" on a database with 761,184 real
inserts sitting one run earlier.

This file pins two things: `_store_results` writes no `database_table_
activity`/`database_column_profiles` rows at all for a run that never
requested `"statistics"` (the write-side half, already correct via the
`if table_activity_rows:` gate added 2026-09-21 — pinned here so it cannot
regress), and `db_derived.load_inputs()` falls back PER TABLE to the newest
run that has a genuine measurement, skipping a newer run's all-NULL rows
(the read-side fix this brief section adds).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
from resource_explorer.surveyors.database.db_derived import load_inputs

FULL_CAPS = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)


class _FakeConnection:
    """Duck-typed stand-in for PostgreSQLConnection -- only implements what
    DatabaseSurveyor actually calls for the "schema"/"statistics"/"views"
    steps exercised here."""

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


def _schema_info():
    return {
        "schemas": [{
            "name": "public",
            "description": "",
            "tables": [{
                "name": "orders",
                "type": "BASE TABLE",
                "description": "",
                "columns": [{
                    "name": "id", "type": "integer", "base_type": "integer",
                    "nullable": False, "default": None, "position": 1,
                    "description": "", "is_primary_key": True, "foreign_key": None,
                    "source": "information_schema",
                }],
                "source": "information_schema",
            }],
        }],
        "total_tables": 1,
        "total_columns": 1,
    }


def _measured_statistics():
    """A "statistics" step's real shape: real tuple counters and pg_stats."""
    return {
        "stats_reset": "2026-01-01T00:00:00",
        "row_stats": [],
        "table_stats": [],
        "column_stats": [{
            "schemaname": "public", "tablename": "orders", "attname": "id",
            "null_frac": 0.0, "n_distinct": -1.0, "avg_width": 4,
            "correlation": 1.0, "most_common_vals": None, "most_common_freqs": None,
            "histogram_bounds": None,
        }],
        "table_activity": [{
            "schemaname": "public", "tablename": "orders",
            "rows_inserted": 761184, "rows_updated": 1435, "rows_deleted": 0,
            "hot_updates": 0, "live_tuples": 761184, "dead_tuples": 0,
            "seq_scan": 861, "idx_scan": 0,
            "last_vacuum": "", "last_autovacuum": "", "last_analyze": "",
            "last_autoanalyze": "", "pending_changes": 0,
        }],
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


def _surveyed_ats(registry, slug) -> list[str]:
    return [s["surveyed_at"] for s in registry.get_database_surveys(slug)]


class TestActivityAndProfilesSurviveAStatisticsFreeRun:
    def test_a_schema_only_run_writes_no_activity_or_profile_rows_of_its_own(
        self, registry, db_entity,
    ):
        # First run: db_activity_signals's real shape -- schema + statistics,
        # real tuple counters and column stats.
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info(), statistics=_measured_statistics())):
            surveyor.survey(steps=["schema", "statistics"])

        first_at = _surveyed_ats(registry, db_entity.slug)[0]
        first_activity = registry.query_detail_rows(
            "database_table_activity", db_entity.slug, first_at
        )
        assert first_activity and first_activity[0]["rows_inserted"] == 761184

        # Second run: schema_inventory's real shape -- schema + views, NO
        # statistics at all. Must write NO database_table_activity/
        # database_column_profiles rows for ITS OWN surveyed_at -- not 157
        # NULL-counter rows shadowing the first run's real measurement.
        with _patched_connection(_FakeConnection(_schema_info(), views=[])):
            surveyor.survey(steps=["schema", "views"])

        second_at = _surveyed_ats(registry, db_entity.slug)[0]
        assert second_at != first_at

        second_activity = registry.query_detail_rows(
            "database_table_activity", db_entity.slug, second_at
        )
        assert second_activity == [], (
            "a statistics-free run must write NO database_table_activity "
            "rows for its own surveyed_at, not a full table of NULL counters"
        )
        second_profiles = registry.query_detail_rows(
            "database_column_profiles", db_entity.slug, second_at
        )
        assert second_profiles == [], (
            "a statistics-free run must write NO database_column_profiles "
            "rows for its own surveyed_at either"
        )

        # The first run's real activity rows must still be there, untouched.
        assert registry.query_detail_rows(
            "database_table_activity", db_entity.slug, first_at
        ) == first_activity

    def test_load_inputs_falls_back_to_the_last_run_that_measured_activity(
        self, registry, db_entity,
    ):
        """The read-side half: `load_inputs()` (no explicit surveyed_at, the
        "what do we currently know" call `db_classification` etc. use) must
        resolve `database_table_activity` to the newest run that actually
        measured it, even though `database_tables`/`database_schemas` are
        legitimately newer (from the later schema-only run)."""
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info(), statistics=_measured_statistics())):
            surveyor.survey(steps=["schema", "statistics"])
        activity_at = _surveyed_ats(registry, db_entity.slug)[0]

        with _patched_connection(_FakeConnection(_schema_info(), views=[])):
            surveyor.survey(steps=["schema", "views"])
        schema_at = _surveyed_ats(registry, db_entity.slug)[0]
        assert schema_at != activity_at

        inputs = load_inputs(registry, db_entity.slug)

        assert inputs.activity, (
            "load_inputs() must not come back with zero activity rows just "
            "because a LATER run's own steps never touched that table"
        )
        assert inputs.activity[0]["rows_inserted"] == 761184
        assert inputs.table_surveyed_at["database_table_activity"] == activity_at
        assert inputs.table_surveyed_at["database_tables"] == schema_at, (
            "the schema/table catalog must still resolve to the run that "
            "actually produced it, independently of activity's own resolution"
        )

    def test_ratchet_a_single_analysis_run_does_not_change_activity_totals(
        self, registry, db_entity,
    ):
        """A ratchet, per the brief's own test list: running ONE unrelated
        analysis (schema_inventory alone, no statistics) must never change
        what `load_inputs()` reports for activity, before or after."""
        from resource_explorer.surveyors.database.db_derived import (
            _activity_evidence,
        )

        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info(), statistics=_measured_statistics())):
            surveyor.survey(steps=["schema", "statistics"])

        before = _activity_evidence(load_inputs(registry, db_entity.slug))
        assert before is not None

        with _patched_connection(_FakeConnection(_schema_info(), views=[])):
            surveyor.survey(steps=["schema", "views"])

        after = _activity_evidence(load_inputs(registry, db_entity.slug))
        assert after == before, (
            "a schema_inventory-only run must not change the activity "
            "evidence db_classification derives"
        )
