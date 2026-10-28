"""Tests for the `executes_at: egeria-adaptive` fold-in
(docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md), which registers the
strategy-selector logic previously only reachable via `HybridDatabaseSurveyor`/
`run_hybrid_filesystem_survey` as a fourth legal `executes_at` value in
`other_engine_handlers` on the database and filesystem adapters.

`tests/test_execution_modes_path_c_hybrid.py` already characterizes
`HybridDatabaseSurveyor`/`run_hybrid_filesystem_survey`'s own strategy logic
directly and is left unchanged. These tests instead cover the NEW layer: the
`_run_egeria_adaptive` handlers (which delegate to those same two, unchanged,
characterized functions) and the executor's promotion of `source` into
`steps_report`.

No live Egeria, no network, no database connection.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import DatabaseEntity, FileSystemEntity
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _run_egeria_adaptive as _db_run_egeria_adaptive,
)
from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
    _run_egeria_adaptive as _fs_run_egeria_adaptive,
)


def _db_entity(**overrides) -> DatabaseEntity:
    defaults = dict(
        slug="my-postgres", display_name="My Postgres", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
    )
    defaults.update(overrides)
    return DatabaseEntity(**defaults)


def _fs_entity(**overrides) -> FileSystemEntity:
    defaults = dict(slug="my-fs", display_name="My FS", local_mount_point="/data/my-fs")
    defaults.update(overrides)
    return FileSystemEntity(**defaults)


class _FakeStep:
    """Stand-in for a SurveyStep — the handler signature takes `step` but
    none of these handlers currently read anything off it."""
    re_analysis_step = "test_step"
    qualified_name = "test::step"


class TestDatabaseEgeriaAdaptiveHandler:
    """`_run_egeria_adaptive` in database/survey_definition_adapter.py —
    delegates to HybridDatabaseSurveyor.survey(), whose own strategy logic
    is characterized by test_execution_modes_path_c_hybrid.py. These tests
    cover what the HANDLER does with that result: forwarding kwargs
    correctly, and relocating schema_info/statistics so the executor's
    generic publish step never re-publishes them a second time.
    """

    def _patch_hybrid(self, survey_return: dict):
        return patch(
            "resource_explorer.surveyors.database.hybrid_database_surveyor.HybridDatabaseSurveyor.survey",
            return_value=survey_return,
        )

    def test_egeria_unreachable_falls_back_to_custom(self):
        db_entity = _db_entity()
        registry = MagicMock()

        with self._patch_hybrid({
            "source": "custom", "database_slug": db_entity.slug,
            "surveyed_at": "2026-09-20T00:00:00",
            "schema_info": {"schemas": []}, "statistics": {},
        }):
            result = _db_run_egeria_adaptive(
                db_entity, registry, _FakeStep(), db_user="u", db_pwd="p", refresh=False,
            )

        assert result["source"] == "custom"
        assert result["status"] == "ok"

    def test_reachable_with_existing_survey_and_no_refresh_returns_cached_report(self):
        db_entity = _db_entity()
        registry = MagicMock()

        with self._patch_hybrid({
            "source": "egeria", "database_slug": db_entity.slug,
            "surveyed_at": "2026-09-18T00:00:00",
            "egeria_report_guid": "report-guid-1",
            "annotations": ["ann1", "ann2"],
        }) as mock_survey:
            result = _db_run_egeria_adaptive(
                db_entity, registry, _FakeStep(), refresh=False,
            )

        assert result["source"] == "egeria"
        assert result["status"] == "ok"
        assert result["egeria_report_guid"] == "report-guid-1"
        # The handler forwards refresh=False through unchanged — the "no new
        # survey ran" guarantee itself lives in HybridDatabaseSurveyor.survey,
        # already characterized by test_execution_modes_path_c_hybrid.py.
        _, kwargs = mock_survey.call_args
        assert kwargs["refresh"] is False

    def test_refresh_with_credentials_relocates_schema_info_to_avoid_double_publish(self):
        db_entity = _db_entity()
        registry = MagicMock()

        with self._patch_hybrid({
            "source": "egeria-custom", "database_slug": db_entity.slug,
            "surveyed_at": "2026-09-18T00:00:00",
            "egeria_report_guid": "published-guid-1",
            "schema_info": {"schemas": ["public"], "total_tables": 4, "total_columns": 20},
            "statistics": {"row_count": 1000},
        }) as mock_survey:
            result = _db_run_egeria_adaptive(
                db_entity, registry, _FakeStep(),
                db_user="u", db_pwd="p", refresh=True,
            )

        assert result["source"] == "egeria-custom"
        assert result["egeria_report_guid"] == "published-guid-1"
        # Not at the top level — that's what the executor's generic publish
        # step scans for to decide what to (re-)publish.
        assert "schema_info" not in result
        assert "statistics" not in result
        # Still fully present, just relocated, so a caller (e.g. the web
        # route) can reconstruct the historic flat response shape.
        assert result["result"]["schema_info"]["schemas"] == ["public"]
        assert result["result"]["statistics"]["row_count"] == 1000
        _, kwargs = mock_survey.call_args
        assert kwargs["refresh"] is True

    def test_publish_failure_degrades_to_custom_with_error_preserved(self):
        db_entity = _db_entity()
        registry = MagicMock()

        with self._patch_hybrid({
            "source": "custom", "database_slug": db_entity.slug,
            "surveyed_at": "2026-09-18T00:00:00",
            "schema_info": {"schemas": ["public"], "total_tables": 4, "total_columns": 20},
            "statistics": {"row_count": 1000},
            "errors": ["Egeria integration failed: connection refused"],
        }):
            result = _db_run_egeria_adaptive(
                db_entity, registry, _FakeStep(),
                db_user="u", db_pwd="p", refresh=True,
            )

        assert result["source"] == "custom"
        assert any("Egeria integration failed" in e and "connection refused" in e
                    for e in result["errors"])
        # The (real, successful) local survey is still there — not swallowed
        # by the error — just relocated like any other schema_info/statistics.
        assert result["result"]["schema_info"]["schemas"] == ["public"]

    def test_no_credentials_returns_explicit_error_source_never_an_empty_success(self):
        db_entity = _db_entity()
        registry = MagicMock()

        with self._patch_hybrid({
            "source": "error", "database_slug": db_entity.slug,
            "surveyed_at": "2026-09-18T00:00:00",
            "errors": ["Custom survey requires credentials. Provide --user and --password options."],
        }):
            result = _db_run_egeria_adaptive(db_entity, registry, _FakeStep())

        assert result["source"] == "error"
        assert result["status"] == "failed"
        assert result["errors"]
        assert "annotations" not in result or not result["annotations"]


class TestFilesystemEgeriaAdaptiveHandler:
    """`_run_egeria_adaptive` in filesystem/survey_definition_adapter.py —
    delegates to run_hybrid_filesystem_survey, characterized directly by
    test_execution_modes_path_c_hybrid.py's TestHybridFilesystemSurvey.
    """

    def test_local_only_when_no_credentials_source_custom(self):
        fs_entity = _fs_entity()  # no egeria_* fields set
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ) as mock_egeria_cls:
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-20T00:00:00", "file_count": 3,
                "total_files": 3, "total_data_files": 1, "total_size": 100,
            }
            result = _fs_run_egeria_adaptive(fs_entity, registry, _FakeStep())

        mock_egeria_cls.assert_not_called()
        assert result["source"] == "custom"
        assert result["status"] == "ok"
        assert result["file_count"] == 3
        assert "result" not in result

    def test_publish_success_source_egeria_custom_relocates_publish_result(self):
        fs_entity = _fs_entity(
            egeria_url="http://egeria:9443", egeria_server="view1",
            egeria_user="u", egeria_password="p",
        )
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ) as mock_egeria_cls:
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-20T00:00:00", "file_count": 12,
                "total_files": 12, "total_data_files": 5, "total_size": 4096,
            }
            mock_egeria_cls.return_value.catalog_and_survey.return_value = {
                "report_guid": "g1", "filesystem_guid": "fsguid1", "annotation_count": 7,
            }
            result = _fs_run_egeria_adaptive(fs_entity, registry, _FakeStep(),
                                             force_egeria_publish=True)  # Brief I round 4: publishing is an explicit choice, never implied by a URL

        assert result["source"] == "egeria-custom"
        assert result["file_count"] == 12
        # Relocated, not left as a top-level "survey_data"/"egeria_publish"
        # key the generic publish step could pick up a second time.
        assert "egeria_publish" not in result
        assert result["result"]["egeria_publish"]["report_guid"] == "g1"

    def test_publish_failure_degrades_to_custom_without_losing_local_survey(self):
        fs_entity = _fs_entity(
            egeria_url="http://egeria:9443", egeria_server="view1",
            egeria_user="u", egeria_password="p",
        )
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ) as mock_egeria_cls:
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-20T00:00:00", "file_count": 7,
                "total_files": 7, "total_data_files": 2, "total_size": 512,
            }
            mock_egeria_cls.return_value.catalog_and_survey.side_effect = RuntimeError("Egeria unreachable")
            result = _fs_run_egeria_adaptive(fs_entity, registry, _FakeStep(),
                                             force_egeria_publish=True)  # Brief I round 4: publishing is an explicit choice, never implied by a URL

        assert result["source"] == "custom"
        assert result["file_count"] == 7
        registry.update_filesystem_status.assert_called_once()

    def test_local_scan_exception_is_explicit_error_never_swallowed(self):
        fs_entity = _fs_entity()
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls:
            mock_local_cls.return_value.run.side_effect = OSError("permission denied")
            result = _fs_run_egeria_adaptive(fs_entity, registry, _FakeStep())

        assert result["source"] == "error"
        assert result["status"] == "error"
        assert result["errors"]


class TestSourceLandsInStepsReport:
    """The executor's other_engine_handlers dispatch branch (survey_
    definition_executor.py) must promote a handler's `source` to a
    top-level field on that step's own `steps_report` entry — not leave it
    reachable only via `detail`. Exercised through
    SurveyDefinitionExecutor.run_synthetic_step, the new one-step
    synthetic-definition entry point the egeria-adaptive call sites use.
    """

    def test_database_egeria_adaptive_source_is_top_level_in_steps_report(self):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutor,
        )

        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity
        registry.has_assigned_egeria_project.return_value = False

        with patch(
            "resource_explorer.surveyors.database.hybrid_database_surveyor.HybridDatabaseSurveyor.survey",
            return_value={
                "source": "egeria-custom", "database_slug": db_entity.slug,
                "surveyed_at": "2026-09-20T00:00:00",
                "egeria_report_guid": "g1",
                "schema_info": {"schemas": ["public"]}, "statistics": {},
            },
        ):
            executor = SurveyDefinitionExecutor(registry)
            result = executor.run_synthetic_step(
                entity_type="database",
                slug=db_entity.slug,
                re_analysis_step="postgres_schema_and_stats",
                executes_at="egeria-adaptive",
                db_user="u", db_pwd="p", refresh=True,
            )

        assert len(result["steps"]) == 1
        step_entry = result["steps"][0]
        assert step_entry["status"] == "ok"
        # First-class, top-level — not only nested inside "detail".
        assert step_entry["source"] == "egeria-custom"
        assert step_entry["detail"]["source"] == "egeria-custom"

    def test_filesystem_egeria_adaptive_source_is_top_level_in_steps_report(self):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutor,
        )

        fs_entity = _fs_entity()
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity
        registry.has_assigned_egeria_project.return_value = False

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ):
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-20T00:00:00",
                "total_files": 3, "total_data_files": 1, "total_size": 100,
            }
            executor = SurveyDefinitionExecutor(registry)
            result = executor.run_synthetic_step(
                entity_type="filesystem",
                slug=fs_entity.slug,
                re_analysis_step="filesystem_inventory",
                executes_at="egeria-adaptive",
            )

        assert len(result["steps"]) == 1
        step_entry = result["steps"][0]
        assert step_entry["status"] == "ok"
        assert step_entry["source"] == "custom"
