"""Phase 1 of PLAN-EXECUTION-MODES-VERIFICATION.md — Path C characterisation
tests (mocked half only).

`HybridDatabaseSurveyor.survey()` (surveyors/database/hybrid_database_surveyor.py)
and `run_hybrid_filesystem_survey()` (surveyors/filesystem/hybrid_filesystem_surveyor.py)
are strategy selectors with real behaviour that nothing under tests/ previously
exercised (grepped, zero hits, per the plan's §1 "Path C" and §2's "Coverage
today: none"). These are characterisation tests: they lock in what the code
does today, mocking only `_check_egeria_available` and the Egeria-side
surveyor (the expensive, non-deterministic half) and letting everything else
— the strategy logic, the `source` labelling, the fallback/error shapes — run
for real, so a later refactor (the plan's §3 fold-in into `executes_at:
egeria-hybrid`) cannot silently change this behaviour.

No live Egeria, no network, no database connection — every case below is
reachable through mocking alone.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import DatabaseEntity, FileSystemEntity
from resource_explorer.surveyors.database.hybrid_database_surveyor import HybridDatabaseSurveyor
from resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor import run_hybrid_filesystem_survey


def _db_entity(**overrides) -> DatabaseEntity:
    defaults = dict(
        slug="my-postgres", display_name="My Postgres", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
    )
    defaults.update(overrides)
    return DatabaseEntity(**defaults)


def _surveyor(registry=None) -> HybridDatabaseSurveyor:
    registry = registry or MagicMock()
    return HybridDatabaseSurveyor(registry=registry)


class TestHybridDatabaseSurveyorStrategySelection:
    """Table-driven over `survey()`, per plan §2 Path C's own bullet list."""

    def test_egeria_unreachable_falls_back_to_custom(self):
        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity

        surveyor = _surveyor(registry)
        surveyor._check_egeria_available = MagicMock(return_value=False)

        with patch(
            "resource_explorer.surveyors.database.database_surveyor.DatabaseSurveyor"
        ) as mock_surveyor_cls:
            mock_surveyor_cls.return_value.survey.return_value = {
                "schema_info": {"schemas": []}, "statistics": {},
            }
            result = surveyor.survey("my-postgres", credentials={"user": "u", "password": "p"})

        assert result["source"] == "custom"
        mock_surveyor_cls.assert_called_once()

    def test_reachable_with_existing_survey_and_no_refresh_returns_cached_report_without_surveying(self):
        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity

        surveyor = _surveyor(registry)
        surveyor._check_egeria_available = MagicMock(return_value=True)

        fake_egeria_surveyor = MagicMock()
        fake_egeria_surveyor.get_latest_survey.return_value = {
            "guid": "report-guid-1", "surveyed_at": "2026-09-18T00:00:00",
            "schema_count": 3, "table_count": 10, "column_count": 42,
            "annotation_count": 5,
        }
        fake_egeria_surveyor.get_annotations.return_value = ["ann1", "ann2"]
        surveyor._egeria_surveyor = fake_egeria_surveyor

        with patch(
            "resource_explorer.surveyors.database.database_surveyor.DatabaseSurveyor"
        ) as mock_local_surveyor_cls:
            result = surveyor.survey("my-postgres", refresh=False)

        assert result["source"] == "egeria"
        assert result["egeria_report_guid"] == "report-guid-1"
        assert result["annotations"] == ["ann1", "ann2"]
        # The point of the cache-or-run branch: no survey of any kind runs.
        mock_local_surveyor_cls.assert_not_called()
        fake_egeria_surveyor.publish_local_survey.assert_not_called()
        fake_egeria_surveyor.catalog_and_survey.assert_not_called()

    def test_refresh_with_credentials_runs_local_scan_then_publishes_as_egeria_custom(self):
        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity

        surveyor = _surveyor(registry)
        surveyor._check_egeria_available = MagicMock(return_value=True)

        fake_egeria_surveyor = MagicMock()
        fake_egeria_surveyor.publish_local_survey.return_value = {"report_guid": "published-guid-1"}
        surveyor._egeria_surveyor = fake_egeria_surveyor

        with patch(
            "resource_explorer.surveyors.database.database_surveyor.DatabaseSurveyor"
        ) as mock_local_surveyor_cls:
            mock_local_surveyor_cls.return_value.survey.return_value = {
                "schema_info": {"schemas": ["public"], "total_tables": 4, "total_columns": 20},
                "statistics": {"row_count": 1000},
                "surveyed_at": "2026-09-18T00:00:00",
            }
            result = surveyor.survey(
                "my-postgres", credentials={"user": "u", "password": "p"}, refresh=True,
            )

        # refresh=True means get_latest_survey is never even asked.
        fake_egeria_surveyor.get_latest_survey.assert_not_called()
        mock_local_surveyor_cls.assert_called_once()
        fake_egeria_surveyor.publish_local_survey.assert_called_once()
        assert result["source"] == "egeria-custom"
        assert result["egeria_report_guid"] == "published-guid-1"
        assert "errors" not in result or not result["errors"]

    def test_publish_failure_falls_back_to_custom_with_egeria_error_appended_not_swallowed(self):
        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity

        surveyor = _surveyor(registry)
        surveyor._check_egeria_available = MagicMock(return_value=True)

        fake_egeria_surveyor = MagicMock()
        fake_egeria_surveyor.publish_local_survey.side_effect = RuntimeError("Egeria publish failed: connection refused")
        surveyor._egeria_surveyor = fake_egeria_surveyor

        with patch(
            "resource_explorer.surveyors.database.database_surveyor.DatabaseSurveyor"
        ) as mock_local_surveyor_cls:
            mock_local_surveyor_cls.return_value.survey.return_value = {
                "schema_info": {"schemas": ["public"], "total_tables": 4, "total_columns": 20},
                "statistics": {"row_count": 1000},
                "surveyed_at": "2026-09-18T00:00:00",
            }
            result = surveyor.survey(
                "my-postgres", credentials={"user": "u", "password": "p"}, refresh=True,
            )

        assert result["source"] == "custom"
        # Not swallowed: the local (successful) survey is still returned, but
        # carrying the Egeria failure alongside it rather than in place of it.
        assert any("Egeria integration failed" in e and "connection refused" in e
                    for e in result["errors"])
        assert result["schema_info"]["schemas"] == ["public"]

    def test_no_credentials_returns_explicit_error_source_never_an_empty_success(self):
        db_entity = _db_entity()
        registry = MagicMock()
        registry.get_database.return_value = db_entity

        surveyor = _surveyor(registry)
        # Egeria unreachable, and no credentials at all — nothing this call
        # could plausibly succeed at.
        surveyor._check_egeria_available = MagicMock(return_value=False)

        result = surveyor.survey("my-postgres", credentials=None)

        assert result["source"] == "error"
        assert result["errors"]
        assert "credentials" in result["errors"][0].lower()
        # It must be an explicit failure, not a quiet, empty "it worked".
        assert "annotations" not in result or not result["annotations"]

    def test_unregistered_database_slug_is_also_an_explicit_error(self):
        """The other error() shape survey() can produce — before any strategy
        selection even happens."""
        registry = MagicMock()
        registry.get_database.return_value = None
        surveyor = _surveyor(registry)

        result = surveyor.survey("does-not-exist")

        assert result["source"] == "error"
        assert "not found in registry" in result["errors"][0]


class TestHybridFilesystemSurvey:
    """`run_hybrid_filesystem_survey()` — the loose-function hybrid path for
    filesystems (plan §0(b): a real hybrid path, just not a class)."""

    def _fs_entity(self, **overrides) -> FileSystemEntity:
        defaults = dict(slug="my-fs", display_name="My FS", local_mount_point="/data/my-fs")
        defaults.update(overrides)
        return FileSystemEntity(**defaults)

    def test_local_survey_saved_with_source_local_before_the_egeria_attempt(self):
        """hybrid_filesystem_surveyor.py:39-45 — the local save must happen,
        and must happen BEFORE any Egeria involvement, so a failed Egeria
        publish never costs the local result."""
        fs_entity = self._fs_entity(
            egeria_url="http://egeria:9443", egeria_server="view1",
            egeria_user="u", egeria_password="p",
        )
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        call_order = []
        registry.add_filesystem_survey.side_effect = lambda **kw: call_order.append("local_save")

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ) as mock_egeria_cls:
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-18T00:00:00", "file_count": 12,
            }
            mock_egeria_cls.return_value.catalog_and_survey.side_effect = (
                lambda *a, **kw: call_order.append("egeria_publish") or {"report_guid": "g1"}
            )

            survey_data = run_hybrid_filesystem_survey("my-fs", registry=registry,
                                                       force_egeria_publish=True)  # Brief I round 4: publishing is an explicit choice, never implied by a URL

        registry.add_filesystem_survey.assert_called_once()
        _, kwargs = registry.add_filesystem_survey.call_args
        assert kwargs["source"] == "local"
        assert kwargs["fs_slug"] == "my-fs"
        assert call_order == ["local_save", "egeria_publish"]
        assert survey_data["file_count"] == 12

    def test_publish_failure_records_the_error_on_the_entity_rather_than_losing_the_survey(self):
        """hybrid_filesystem_surveyor.py:69-76 — non-fatal: the survey_data
        already returned to the caller must not be discarded just because the
        publish step afterwards failed."""
        fs_entity = self._fs_entity(
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
                "surveyed_at": "2026-09-18T00:00:00", "file_count": 7,
            }
            mock_egeria_cls.return_value.catalog_and_survey.side_effect = RuntimeError("Egeria unreachable")

            survey_data = run_hybrid_filesystem_survey("my-fs", registry=registry,
                                                       force_egeria_publish=True)  # Brief I round 4: publishing is an explicit choice, never implied by a URL

        # The survey is not lost — the caller still gets the local result.
        assert survey_data["file_count"] == 7
        assert "egeria_publish" not in survey_data
        # The error is recorded on the entity's status, not swallowed.
        registry.update_filesystem_status.assert_called_once()
        _, kwargs = registry.update_filesystem_status.call_args
        assert "Egeria unreachable" in kwargs["error_message"]

    def test_local_only_when_no_egeria_credentials_and_not_forced(self):
        fs_entity = self._fs_entity()  # no egeria_* fields set
        registry = MagicMock()
        registry.get_filesystem.return_value = fs_entity

        with patch(
            "resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor.LocalFileSystemSurveyor"
        ) as mock_local_cls, patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor"
        ) as mock_egeria_cls:
            mock_local_cls.return_value.run.return_value = {
                "surveyed_at": "2026-09-18T00:00:00", "file_count": 3,
            }
            survey_data = run_hybrid_filesystem_survey("my-fs", registry=registry)

        mock_egeria_cls.assert_not_called()
        registry.update_filesystem_status.assert_not_called()
        assert survey_data["file_count"] == 3
