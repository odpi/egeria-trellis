"""BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §B's write-side rule, applied to the
`survey_data` JSON blob's `statistics`/`views` sections — generalizing the
same preserve-prior pattern `test_store_results_preserves_prior_operations.py`
already pins for `operations`/`credential_capability`, and closing
`docs/Backlog.md`'s "`_store_results` clobbers a survey_data section a run
didn't collect" generic item for these two remaining sections.

Before this fix, a run whose own requested steps did not include
`"statistics"`/`"views"` wrote `statistics: {}` / `views: []` into that run's
`survey_data` blob unconditionally, shadowing a PRIOR run's real statistics/
view list the moment `get_latest_database_survey()` was read — the same
"ran, collected nothing THIS time, and reported that nothing as fact" shape
`row_count`/`size_bytes` and `operations`/`credential_capability` were each
independently fixed for.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

FULL_CAPS = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)


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


def _latest_survey_data(registry, slug) -> dict:
    survey = registry.get_latest_database_survey(slug)
    return json.loads(survey["survey_data"]) if survey else {}


class TestSurveyDataSectionsSurviveARunThatDidNotCollectThem:
    def test_statistics_survives_a_later_views_only_run(self, registry, db_entity):
        statistics = {"row_stats": [{"schemaname": "public", "tablename": "orders",
                                      "row_count": 42, "last_analyzed": "2026-09-20T00:00:00",
                                      "last_vacuumed": "", "pending_changes": 0}],
                      "table_stats": []}
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info(), statistics=statistics)):
            surveyor.survey(steps=["schema", "statistics"])

        assert _latest_survey_data(registry, db_entity.slug)["statistics"] == statistics

        with _patched_connection(_FakeConnection(_schema_info(), views=[])):
            surveyor.survey(steps=["schema", "views"])

        after = _latest_survey_data(registry, db_entity.slug)
        assert after["statistics"] == statistics, (
            "a views-only run must preserve the prior run's real statistics "
            "section, not overwrite it with {}"
        )

    def test_views_survives_a_later_statistics_only_run(self, registry, db_entity):
        # `_survey_views` does its own `information_schema.views` query and
        # SQLGlot analysis rather than reading `conn.get_views()` — patched
        # directly here so this test exercises `_store_results`'s preserve-
        # prior rule for the "views" survey_data key, not SQLGlot itself.
        views = [{"name": "orders_view", "schema": "public", "definition": "SELECT 1"}]
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info())), \
                patch.object(DatabaseSurveyor, "_survey_views", return_value=views):
            surveyor.survey(steps=["schema", "views"])

        assert _latest_survey_data(registry, db_entity.slug)["views"] == views

        with _patched_connection(_FakeConnection(_schema_info(), statistics={
            "row_stats": [{"schemaname": "public", "tablename": "orders", "row_count": 1,
                           "last_analyzed": "", "last_vacuumed": "", "pending_changes": 0}],
            "table_stats": [],
        })):
            surveyor.survey(steps=["schema", "statistics"])

        after = _latest_survey_data(registry, db_entity.slug)
        assert after["views"] == views, (
            "a statistics-only run must preserve the prior run's real views "
            "section, not overwrite it with []"
        )

    def test_a_genuine_first_run_with_neither_stays_empty(self, registry, db_entity):
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info())):
            surveyor.survey(steps=["schema"])

        after = _latest_survey_data(registry, db_entity.slug)
        assert after["statistics"] == {}
        assert after["views"] == []

    def test_a_fresh_statistics_run_still_overwrites_with_a_real_new_value(self, registry, db_entity):
        stats_1 = {"row_stats": [{"schemaname": "public", "tablename": "orders", "row_count": 1,
                                   "last_analyzed": "", "last_vacuumed": "", "pending_changes": 0}],
                   "table_stats": []}
        stats_2 = {"row_stats": [{"schemaname": "public", "tablename": "orders", "row_count": 2,
                                   "last_analyzed": "", "last_vacuumed": "", "pending_changes": 0}],
                   "table_stats": []}
        surveyor = DatabaseSurveyor(db_entity, {"user": "a", "password": "b"}, registry)
        with _patched_connection(_FakeConnection(_schema_info(), statistics=stats_1)):
            surveyor.survey(steps=["schema", "statistics"])
        with _patched_connection(_FakeConnection(_schema_info(), statistics=stats_2)):
            surveyor.survey(steps=["schema", "statistics"])

        assert _latest_survey_data(registry, db_entity.slug)["statistics"] == stats_2
