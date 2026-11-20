"""Coverage for `repo_survey_definition_adapter._trigger_egeria_native_survey`
and its supporting `EgeriaPublisher` methods (`trigger_survey_by_guid`,
`_initiate_survey`, `_find_survey_process_name`) — the repo "egeria" handler
closing Backlog "Path B3" (PLAN-EXECUTION-MODES-VERIFICATION.md §1 Path B). Mirrors the shape of
tests/test_execution_modes_path_b1_failure_modes.py's database coverage —
pure unit tests, no live Egeria needed.

Three outcomes are covered, matching the database/filesystem "egeria"
handlers' contract:
  1. An uncataloged repo (no stored egeria_asset_guid) raises immediately,
     with a clear message, WITHOUT attempting to reach Egeria.
  2. A cataloged repo triggers, polls to a terminal status, and returns the
     produced report/annotations shape.
  3. No matching Egeria survey process exists for repos yet (the expected,
     honest state until a real repo survey action service is authored in
     Egeria) — a clear, specific error, not a crash or silent no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import resource_explorer.surveyors.repo_survey_definition_adapter as repo_adapter
from resource_explorer.registry import Project
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher, EgeriaPublisherError
from resource_explorer.surveyors.survey_definition_executor import (
    SurveyDefinitionExecutor,
    get_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep


def _fake_project(**overrides) -> Project:
    defaults = dict(
        slug="fixture-repo",
        display_name="Fixture Repo",
        github_url="https://github.com/example/fixture-repo",
        egeria_asset_guid="",
    )
    defaults.update(overrides)
    return Project(**defaults)


def _fake_reader(survey_def, candidates=None):
    reader = MagicMock()
    reader.fetch.return_value = survey_def
    reader.find_candidate_process_guids.return_value = candidates or []
    return reader


def _fake_registry():
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None
    registry.has_assigned_egeria_project.return_value = False
    return registry


class TestAdapterRegistersTheEgeriaHandler:
    def test_repo_adapter_now_has_an_egeria_handler_registered(self):
        """The premise this whole build changes: repos used to have none."""
        adapter = get_adapter("repo")
        assert "egeria" in adapter.other_engine_handlers
        assert adapter.other_engine_handlers["egeria"] is repo_adapter._trigger_egeria_native_survey


class TestUncataloguedRepoRaises:
    def test_direct_call_raises_with_the_exact_message(self):
        project = _fake_project(egeria_asset_guid="")
        with pytest.raises(RuntimeError, match="no stored Egeria asset guid"):
            repo_adapter._trigger_egeria_native_survey(project, MagicMock(), step=MagicMock())

    def test_through_the_full_executor_the_raise_becomes_a_reported_error_not_a_crash(self):
        project = _fake_project(egeria_asset_guid="")
        registry = _fake_registry()
        registry.get.return_value = project

        survey_def = SurveyDefinition(
            process_guid="proc-uncatalogued-repo",
            display_name="Uncatalogued Repo Survey",
            qualified_name="GovActionProcess::UncataloguedRepo",
            supported_technology_type="Git Repository",
            steps=[
                SurveyStep(
                    guid="s1", display_name="EgeriaNative", qualified_name="Step::EgeriaNative",
                    executes_at="egeria", re_analysis_step=None,
                ),
            ],
        )
        reader = _fake_reader(survey_def, candidates=[
            {"guid": "proc-uncatalogued-repo", "qualified_name": "GovActionProcess::UncataloguedRepo",
             "display_name": "UncataloguedRepo"},
        ])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="repo", slug="fixture-repo")

        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses["Step::EgeriaNative"] == "error"
        assert len(result["errors"]) == 1
        assert "no stored Egeria asset guid" in result["errors"][0]


class TestCataloguedRepoTriggersAndPolls:
    def test_a_guid_present_does_not_raise_and_triggers_the_native_survey(self):
        """Mirrors database's own happy-path test: both pyegeria clients
        (trigger/poll) and the report/annotation reads are mocked here — the
        happy-path detail of report attribution/annotation conversion is
        covered by test_egeria_async_survey_result.py instead of duplicated."""
        project = _fake_project(egeria_asset_guid="asset-guid-123")

        fake_publisher = MagicMock()
        fake_publisher.trigger_survey_by_guid.return_value = "engine-action-guid-1"
        fake_publisher.get_survey_reports_by_guid.return_value = [
            {"guid": "report-guid-1", "qualified_name": "SurveyReport::x",
             "surveyed_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
        ]
        fake_publisher.get_annotations_by_report_guid.return_value = []

        metadata_expert = MagicMock()
        metadata_expert.get_metadata_element_by_guid.return_value = {
            "elementProperties": {"propertyValueMap": {
                "activityStatus": {"symbolicName": "COMPLETED"},
            }}
        }

        with patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher",
            return_value=fake_publisher,
        ), patch(
            "resource_explorer.surveyors.egeria_async_survey_result._get_clients",
            return_value=(MagicMock(), metadata_expert),
        ):
            result = repo_adapter._trigger_egeria_native_survey(project, MagicMock(), step=MagicMock())

        assert result["status"] == "ok"
        assert result["engine_action_guid"] == "engine-action-guid-1"
        assert result["final_status"] == "COMPLETED"
        assert result["report_guid"] == "report-guid-1"
        assert result["annotations"] == []
        fake_publisher.trigger_survey_by_guid.assert_called_once_with("asset-guid-123")


REFUSAL = ("Egeria's survey engine takes only an Asset as its target; a repository survey will target its "
           "branch folder")


class TestRepositorySurveyIsRefused:
    """TR-89: a repository is a SourceControlLibrary, not an Asset, and Egeria's survey engine keeps only an
    Asset action target (SurveyActionEngineHandler drops the rest, silently). So the native repository survey
    is refused in words, before any call to Egeria, and the handler surfaces that one clear, specific error.
    (Until TR-89 the same path ended in "No native survey process configured": nothing had been authored.)
    The repository survey will target the branch FileFolder, an Asset (TR-89 Part D, after EG-19)."""

    def _publisher_with_no_candidates(self) -> EgeriaPublisher:
        publisher = EgeriaPublisher(platform_url="https://example.test:9443")
        publisher._connect = MagicMock()  # never talk to real pyegeria
        publisher._automated_curation = MagicMock()
        publisher._find_survey_process_name = MagicMock(return_value=None)
        return publisher

    def test_a_repository_survey_is_refused_in_words_and_nothing_is_sent(self):
        publisher = self._publisher_with_no_candidates()

        with pytest.raises(EgeriaPublisherError, match=REFUSAL):
            publisher.trigger_survey_by_guid("repo-guid-999")

        publisher._automated_curation.initiate_gov_action_process.assert_not_called()
        publisher._connect.assert_not_called()

    def test_the_error_says_why_and_what_comes_next(self):
        publisher = self._publisher_with_no_candidates()

        with pytest.raises(EgeriaPublisherError) as excinfo:
            publisher.trigger_survey_by_guid("repo-guid-999")

        assert "Asset" in str(excinfo.value) and "branch folder" in str(excinfo.value)

    def test_through_the_full_handler_this_is_a_reported_error_not_a_crash(self):
        project = _fake_project(egeria_asset_guid="asset-guid-456")

        with patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher",
            return_value=self._publisher_with_no_candidates(),
        ):
            with pytest.raises(EgeriaPublisherError, match=REFUSAL):
                repo_adapter._trigger_egeria_native_survey(project, MagicMock(), step=MagicMock())

    def test_through_the_full_executor_it_becomes_an_error_step_not_a_crash(self):
        project = _fake_project(egeria_asset_guid="asset-guid-456")
        registry = _fake_registry()
        registry.get.return_value = project

        survey_def = SurveyDefinition(
            process_guid="proc-repo-no-process",
            display_name="Repo Survey, No Native Process Yet",
            qualified_name="GovActionProcess::RepoNoProcess",
            supported_technology_type="Git Repository",
            steps=[
                SurveyStep(
                    guid="s1", display_name="EgeriaNative", qualified_name="Step::EgeriaNative",
                    executes_at="egeria", re_analysis_step=None,
                ),
            ],
        )
        reader = _fake_reader(survey_def, candidates=[
            {"guid": "proc-repo-no-process", "qualified_name": "GovActionProcess::RepoNoProcess",
             "display_name": "RepoNoProcess"},
        ])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        with patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher",
            return_value=self._publisher_with_no_candidates(),
        ):
            result = executor.run(entity_type="repo", slug="fixture-repo")

        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses["Step::EgeriaNative"] == "error"
        assert len(result["errors"]) == 1
        assert "survey engine takes only an Asset" in result["errors"][0]
