"""`DatabaseSurveyor._store_results` must not clobber the `operations`/
`credential_capability` survey_data sections back to `{}` when a later
run's own requested steps never collected them at all.

Same class of bug `test_store_results_preserves_prior_stats.py` already
pins for `row_count`/`size_bytes`, found one level up: found live
2026-09-26 running a database Survey Definition (Slice 12) end to end.
`SurveyDefinitionExecutor` dispatches each step in a Survey Definition as
its OWN separate `DatabaseSurveyor.survey()` call, so a 3-step Scouting
definition (`postgres_schema_and_stats` -> `postgres_operations` ->
`credential_capability`) writes THREE survey rows a couple of seconds
apart. The last step, `credential_capability`, correctly collects no
`operations` at all for ITS OWN run — but `_store_results` wrote that run's
empty `operations: {}` unconditionally, making it the newest row's value
and silently shadowing the real operations data `postgres_operations` had
written two rows earlier. `db_activity_signals`/`db_resilience` (which read
the operations section off the single latest row) then read nothing and
degraded from a real answer ("0 writes and 6 reads since statistics
collection began") to "ran and found nothing" — a per-analysis-path answer
clobbered by an unrelated LATER step in the same Survey Definition run.

This blocks #303 (a Survey Definition run must not degrade answers the
per-analysis path already produced), so it is exercised both at the
`_store_results` unit level (this file) and end to end through
`SurveyDefinitionExecutor` (the class at the bottom), asserting the
operations section survives a real multi-step definition run intact.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

FULL_CAPS = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True,
                                credential_introspection=True)


class _FakeConnection:
    """Duck-typed stand-in for PostgreSQLConnection -- only implements what
    DatabaseSurveyor actually calls for the "schema"/"operations"/
    "credential_capability" steps exercised here."""

    def __init__(self, schema_info, operations=None, credential_capability=None):
        self._schema_info = schema_info
        self._operations = operations or {}
        self._credential_capability = credential_capability or {}

    def get_schema_info(self):
        return self._schema_info

    def get_statistics(self):
        return {}

    def get_views(self):
        return []

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

    def get_credential_capability(self):
        return self._credential_capability

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


def _latest_operations(registry, slug) -> dict:
    import json
    survey = registry.get_latest_database_survey(slug)
    return json.loads(survey["survey_data"]).get("operations") or {}


def _latest_credential_capability(registry, slug) -> dict:
    import json
    survey = registry.get_latest_database_survey(slug)
    return json.loads(survey["survey_data"]).get("credential_capability") or {}


def _table_activity(operations: dict):
    """`_survey_operations()`'s real nested shape: table_activity/stats_reset
    live under `operations["activity_signals"]`, not at the top level."""
    return (operations.get("activity_signals") or {}).get("table_activity")


def _stats_reset(operations: dict):
    return (operations.get("activity_signals") or {}).get("stats_reset")


class TestOperationsSurvivesALaterRunThatDidNotRequestIt:
    def test_operations_survives_a_later_credential_capability_only_run(self, registry, db_entity):
        # First run: postgres_operations's real shape -- schema + operations.
        operations = {"table_activity": [{"schemaname": "public", "tablename": "orders",
                                           "n_tup_ins": 0, "n_tup_upd": 0, "n_tup_del": 0}],
                      "stats_reset": "2026-01-01T00:00:00"}
        conn = _FakeConnection(_schema_info(), operations=operations)
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "operations"])

        assert _table_activity(_latest_operations(registry, db_entity.slug))

        # Second run: credential_capability's real shape -- schema +
        # credential_capability, NO operations at all. Must NOT clobber the
        # operations section a prior run measured.
        conn2 = _FakeConnection(_schema_info(), credential_capability={"schema_total": 8})
        with _patched_connection(conn2):
            surveyor.survey(steps=["schema", "credential_capability"])

        operations_after = _latest_operations(registry, db_entity.slug)
        assert _table_activity(operations_after), (
            "a credential_capability-only run must preserve the prior "
            "run's real operations section, not overwrite it with {}"
        )
        assert _latest_credential_capability(registry, db_entity.slug) == {"schema_total": 8}

    def test_credential_capability_survives_a_later_operations_only_run(self, registry, db_entity):
        """Symmetric case: an operations-only run must not clobber a prior
        credential_capability reading either."""
        conn = _FakeConnection(_schema_info(), credential_capability={"schema_total": 8})
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema", "credential_capability"])

        conn2 = _FakeConnection(_schema_info(), operations={"stats_reset": "2026-01-01T00:00:00"})
        with _patched_connection(conn2):
            surveyor.survey(steps=["schema", "operations"])

        assert _latest_credential_capability(registry, db_entity.slug) == {"schema_total": 8}
        assert _stats_reset(_latest_operations(registry, db_entity.slug))

    def test_a_genuine_first_run_with_no_operations_yet_stays_empty(self, registry, db_entity):
        """Not a blanket "operations is never empty" — a database that has
        never had the operations step run at all still correctly reports
        no section, matching the pre-existing "step didn't run" convention."""
        conn = _FakeConnection(_schema_info())
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(conn):
            surveyor.survey(steps=["schema"])

        assert _latest_operations(registry, db_entity.slug) == {}


class TestSurveyDefinitionRunPreservesOperationsAcrossItsOwnSteps:
    """End to end through SurveyDefinitionExecutor, the actual path #303 is
    about: a real multi-step database Survey Definition run must not
    degrade an operations answer a per-analysis run already produced,
    purely because a LATER step in the same definition run collects a
    different section."""

    def test_operations_section_is_non_empty_after_a_full_definition_run(self, registry, db_entity):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _run_credential_capability,
            _run_postgres_operations,
        )

        operations = {"table_activity": [{"schemaname": "public", "tablename": "orders",
                                           "n_tup_ins": 0, "n_tup_upd": 0, "n_tup_del": 0}],
                      "stats_reset": "2026-01-01T00:00:00"}

        # Simulates SurveyDefinitionExecutor's own per-step dispatch: each
        # re_analysis_step runner is called independently, in the same
        # order a real Scouting definition's steps chain would run them.
        with _patched_connection(_FakeConnection(_schema_info(), operations=operations)):
            _run_postgres_operations(db_entity, registry, db_user="a", db_pwd="b")

        with _patched_connection(_FakeConnection(_schema_info(), credential_capability={"schema_total": 8})):
            _run_credential_capability(db_entity, registry, db_user="a", db_pwd="b")

        final_operations = _latest_operations(registry, db_entity.slug)
        assert final_operations, (
            "the operations section must survive the credential_capability "
            "step that runs after it in the same Survey Definition"
        )
        assert _table_activity(final_operations) == operations["table_activity"], (
            "the surviving operations section must be identical in shape "
            "to what the per-analysis path itself wrote, not a partial "
            "or re-derived copy"
        )
        assert _stats_reset(final_operations) == operations["stats_reset"]
