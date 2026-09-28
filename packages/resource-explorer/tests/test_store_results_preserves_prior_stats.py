"""`DatabaseSurveyor._store_results` must not clobber a table's `row_count`/
`size_bytes` back to zero when a later run's own requested steps never
fetched `"statistics"` at all (design ruling 2026-09-26, generalizing slice
17c's collector-honesty fix one level up — from a results READER to the
survey WRITER).

Trigger: `coco_pharma` (2026-09-26). `schema_inventory` requests steps
`["schema", "views"]` and `db_activity_signals` requests
`["schema", "operations"]` — neither includes `"statistics"`. Running
EITHER after a `row_count_snapshot` run (steps `["schema", "statistics"]`)
silently overwrote every non-catalog-fallback table's real, previously
measured `row_count` with a bare `0` — a run that fetched no statistics
manufacturing a "measured zero" by the act of writing. Reproduced live: a
table with a real `row_count` correctly went to `None` after slice 17c's
never-analyzed fix, then flipped straight back to `0` the moment
`schema_inventory` ran again.

This is the honest STOPGAP, not the real fix: the underlying cause is one
row per table getting overwritten by every survey run instead of survey
rows keyed `(slug, surveyed_at, source)` per design rule D — the
structured-tables rework (stream 3) is the real fix. Preserving prior
values here only prevents the visible symptom.

No live Postgres is used — `DatabaseSurveyor.survey()` is exercised
end-to-end (including the real registry write/read-back) against a fake
connection, the same pattern
`test_postgres_schema_and_stats_extension.py`'s `_patched_connection` uses.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

FULL_CAPS = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)


class _FakeConnection:
    """Duck-typed stand-in for PostgreSQLConnection -- only implements what
    DatabaseSurveyor actually calls for the "schema"/"statistics"/"views"/
    "operations" steps exercised here."""

    def __init__(self, schema_info, statistics=None, views=None, operations=None):
        self._schema_info = schema_info
        self._statistics = statistics or {}
        self._views = views or []
        self._operations = operations or {}

    def get_schema_info(self):
        return self._schema_info

    def get_statistics(self):
        return self._statistics

    def get_views(self):
        return self._views

    def get_privilege_audit(self):
        return self._operations.get("privilege_audit", {})

    def get_replication_status(self):
        return self._operations.get("replication_status", {})

    def get_wal_archiving_status(self):
        return self._operations.get("wal_archiving_status", {})

    def get_backup_tool_signals(self):
        return self._operations.get("backup_tool_signals", {})

    def get_clustering_info(self):
        return self._operations.get("clustering_info", {})

    def get_external_dependencies(self):
        return self._operations.get("external_dependencies", {})

    def get_table_activity(self):
        return self._operations.get("table_activity", [])

    def get_stats_reset(self):
        return self._operations.get("stats_reset", "")

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


class TestPriorStatsSurviveAStatisticsFreeRun:
    def test_row_count_survives_a_later_schema_only_run(self, registry, db_entity):
        # First run: row_count_snapshot's shape -- schema + statistics,
        # real row_stats data.
        statistics = {
            "row_stats": [
                {"schemaname": "public", "tablename": "orders", "row_count": 42,
                 "last_analyzed": "2026-09-20T00:00:00", "last_vacuumed": "",
                 "pending_changes": 0},
            ],
            "table_stats": [
                {"schemaname": "public", "tablename": "orders", "total_bytes": 8192,
                 "total_size": "8192 bytes"},
            ],
        }
        conn = _FakeConnection(_schema_info(), statistics=statistics)
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "statistics"])

        tables = registry.query_detail_rows("database_tables", db_entity.slug)
        assert tables[0]["row_count"] == 42
        assert tables[0]["size_bytes"] == 8192

        # Second run: schema_inventory's real shape -- schema + views, NO
        # statistics at all. Must NOT clobber the row_count/size_bytes a
        # prior run measured.
        conn2 = _FakeConnection(_schema_info(), views=[])
        with _patched_connection(conn2):
            surveyor.survey(steps=["schema", "views"])

        tables = registry.query_detail_rows("database_tables", db_entity.slug)
        assert tables[0]["row_count"] == 42, (
            "a statistics-free run must preserve the prior real row_count, "
            "not overwrite it with a fabricated 0"
        )
        assert tables[0]["size_bytes"] == 8192

    def test_row_count_survives_a_later_operations_only_run(self, registry, db_entity):
        """db_activity_signals's real shape -- schema + operations, no
        statistics -- is the other real analysis found live to clobber
        prior row counts."""
        statistics = {
            "row_stats": [
                {"schemaname": "public", "tablename": "orders", "row_count": 42,
                 "last_analyzed": "2026-09-20T00:00:00", "last_vacuumed": "",
                 "pending_changes": 0},
            ],
            "table_stats": [],
        }
        conn = _FakeConnection(_schema_info(), statistics=statistics)
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "statistics"])

        conn2 = _FakeConnection(_schema_info(), operations={})
        with _patched_connection(conn2):
            surveyor.survey(steps=["schema", "operations"])

        tables = registry.query_detail_rows("database_tables", db_entity.slug)
        assert tables[0]["row_count"] == 42

    def test_a_table_never_measured_at_all_is_none_not_zero(self, registry, db_entity):
        """No prior value exists yet (first survey ever, no statistics
        requested) -- must be None, never a fabricated 0."""
        conn = _FakeConnection(_schema_info(), views=[])
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "views"])

        tables = registry.query_detail_rows("database_tables", db_entity.slug)
        assert tables[0]["row_count"] is None
        assert tables[0]["size_bytes"] is None

    def test_a_fresh_statistics_run_still_overwrites_with_a_real_new_value(self, registry, db_entity):
        """The fix must not freeze row_count forever -- a run that DOES
        fetch fresh statistics still updates it normally."""
        conn = _FakeConnection(_schema_info(), statistics={
            "row_stats": [
                {"schemaname": "public", "tablename": "orders", "row_count": 42,
                 "last_analyzed": "2026-09-20T00:00:00", "last_vacuumed": "",
                 "pending_changes": 0},
            ],
            "table_stats": [],
        })
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "statistics"])

        conn2 = _FakeConnection(_schema_info(), statistics={
            "row_stats": [
                {"schemaname": "public", "tablename": "orders", "row_count": 100,
                 "last_analyzed": "2026-09-25T00:00:00", "last_vacuumed": "",
                 "pending_changes": 0},
            ],
            "table_stats": [],
        })
        with _patched_connection(conn2):
            surveyor.survey(steps=["schema", "statistics"])

        tables = registry.query_detail_rows("database_tables", db_entity.slug)
        assert tables[0]["row_count"] == 100
