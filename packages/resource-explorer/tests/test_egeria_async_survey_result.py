"""Tests for egeria_async_survey_result.py — the poll/resolve/convert logic
that turns a triggered Egeria-native survey engine action into a real RE
result (EGERIA-ASYNC-RESULT-RETRIEVAL-IMPLEMENTED.md).

Every pyegeria client and every AssetMaker read is mocked; these tests
verify RE's own polling-timeout handling, report-attribution logic, and
annotation conversion — not pyegeria's real network behavior. Layer 1 of
the two-layer test plan (Layer 2 is a live, coordinated run, see the design
notes doc).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.surveyors.egeria_async_survey_result import (
    SurveyReportAttributionError,
    convert_wire_annotation,
    poll_trigger_and_retrieve_annotations,
    resolve_triggered_survey_report,
)
from resource_explorer.surveyors.egeria_delegated_step import EgeriaEngineActionTimeoutError
from resource_explorer.surveyors.survey_report import (
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


def _mock_element(status: str, completion_message: str = "") -> dict:
    return {
        "elementProperties": {
            "propertyValueMap": {
                "activityStatus": {"symbolicName": status},
                "completionMessage": {"primitiveValue": completion_message},
            }
        }
    }


class TestConvertWireAnnotation:
    def test_maps_known_type_to_the_right_dataclass(self):
        raw = {
            "guid": "ann-1", "annotation_type": "SchemaAnalysisAnnotation",
            "summary": "found a table", "confidence": 80,
            "analysis_step": "egeria-native-step", "explanation": "detail",
            "expression": "expr", "json_properties": {"k": "v"},
        }
        ann = convert_wire_annotation(raw, analysis_step="postgres_schema_and_stats")
        assert isinstance(ann, SchemaAnalysisAnnotation)
        assert ann.summary == "found a table"
        assert ann.analysis_step == "postgres_schema_and_stats"
        assert ann.annotation_type_name == "SchemaAnalysisAnnotation"
        assert ann.confidence == 80
        assert ann.source == "egeria"
        assert ann.check_name == "egeria-native-step"
        assert ann.item_key == "ann-1"
        assert ann.json_properties == {"k": "v"}

    def test_unknown_type_falls_back_to_resource_measure(self):
        raw = {"guid": "ann-2", "annotation_type": "SomeUnknownAnnotationType", "summary": "x"}
        ann = convert_wire_annotation(raw, analysis_step="postgres_schema_and_stats")
        assert isinstance(ann, ResourceMeasureAnnotation)
        assert ann.annotation_type_name == "SomeUnknownAnnotationType"

    def test_request_for_action_gets_its_extra_fields(self):
        raw = {
            "guid": "ann-3", "annotation_type": "RequestForActionAnnotation",
            "summary": "needs review", "explanation": "stale schema",
        }
        ann = convert_wire_annotation(raw, analysis_step="postgres_schema_and_stats")
        assert isinstance(ann, RequestForActionAnnotation)
        assert ann.action_requested == "stale schema"
        assert ann.action_target_name == "ann-3"

    def test_non_numeric_confidence_defaults_to_100(self):
        raw = {"guid": "ann-4", "annotation_type": "ResourceMeasureAnnotation", "confidence": "not-a-number"}
        ann = convert_wire_annotation(raw, analysis_step="x")
        assert ann.confidence == 100


class TestResolveTriggeredSurveyReport:
    def test_picks_the_single_report_created_after_trigger(self):
        reports = [
            {"guid": "old-report", "surveyed_at": (NOW - timedelta(hours=1)).isoformat()},
            {"guid": "new-report", "surveyed_at": (NOW + timedelta(seconds=30)).isoformat()},
        ]
        result = resolve_triggered_survey_report(reports, triggered_at=NOW)
        assert result["guid"] == "new-report"

    def test_zero_matches_raises_attribution_error(self):
        reports = [
            {"guid": "old-report", "surveyed_at": (NOW - timedelta(hours=1)).isoformat()},
        ]
        with pytest.raises(SurveyReportAttributionError, match="no SurveyReport"):
            resolve_triggered_survey_report(reports, triggered_at=NOW)

    def test_two_reports_after_trigger_raises_ambiguity_not_a_guess(self):
        """The exact hazard flagged by the live peer: two reports created in
        the same window must be reported as an explicit ambiguity, never
        silently resolved to 'the newest one' or 'the first one'."""
        same_instant = (NOW + timedelta(seconds=1)).isoformat()
        reports = [
            {"guid": "concurrent-report-a", "surveyed_at": same_instant},
            {"guid": "concurrent-report-b", "surveyed_at": same_instant},
        ]
        with pytest.raises(SurveyReportAttributionError, match="Ambiguous"):
            resolve_triggered_survey_report(reports, triggered_at=NOW)

    def test_unparseable_timestamp_is_excluded_not_guessed(self):
        reports = [
            {"guid": "weird-report", "surveyed_at": "not-a-real-timestamp"},
        ]
        with pytest.raises(SurveyReportAttributionError, match="no SurveyReport"):
            resolve_triggered_survey_report(reports, triggered_at=NOW)

    def test_epoch_millis_timestamps_are_handled(self):
        after_millis = str(int((NOW + timedelta(seconds=5)).timestamp() * 1000))
        before_millis = str(int((NOW - timedelta(seconds=5)).timestamp() * 1000))
        reports = [
            {"guid": "old", "surveyed_at": before_millis},
            {"guid": "new", "surveyed_at": after_millis},
        ]
        result = resolve_triggered_survey_report(reports, triggered_at=NOW)
        assert result["guid"] == "new"


class TestPollTriggerAndRetrieveAnnotations:
    def test_happy_path_polls_resolves_and_converts(self):
        surveyor = MagicMock()
        surveyor.get_survey_reports_by_guid.return_value = [
            {"guid": "old-report", "surveyed_at": (NOW - timedelta(hours=1)).isoformat()},
            {"guid": "new-report", "surveyed_at": (NOW + timedelta(seconds=10)).isoformat()},
        ]
        surveyor.get_annotations_by_report_guid.return_value = [
            {"guid": "a1", "annotation_type": "ResourceMeasureAnnotation", "summary": "42 tables"},
        ]
        metadata_expert = MagicMock()
        metadata_expert.get_metadata_element_by_guid.return_value = _mock_element("COMPLETED", "done")

        with patch(
            "resource_explorer.surveyors.egeria_async_survey_result._get_clients",
            return_value=(MagicMock(), metadata_expert),
        ):
            result = poll_trigger_and_retrieve_annotations(
                surveyor=surveyor, engine_action_guid="action-1", resource_guid="db-guid-1",
                triggered_at=NOW, analysis_step="postgres_schema_and_stats",
            )

        assert result["final_status"] == "COMPLETED"
        assert result["report_guid"] == "new-report"
        assert len(result["annotations"]) == 1
        assert isinstance(result["annotations"][0], ResourceMeasureAnnotation)
        surveyor.get_annotations_by_report_guid.assert_called_once_with("new-report")

    def test_poll_timeout_raises_specific_timeout_error_not_a_hang_or_silent_success(self):
        surveyor = MagicMock()
        metadata_expert = MagicMock()
        metadata_expert.get_metadata_element_by_guid.return_value = _mock_element("IN_PROGRESS")

        with patch(
            "resource_explorer.surveyors.egeria_async_survey_result._get_clients",
            return_value=(MagicMock(), metadata_expert),
        ), patch("resource_explorer.surveyors.egeria_delegated_step.time.sleep"):
            with pytest.raises(EgeriaEngineActionTimeoutError):
                poll_trigger_and_retrieve_annotations(
                    surveyor=surveyor, engine_action_guid="action-2", resource_guid="db-guid-1",
                    triggered_at=NOW, analysis_step="postgres_schema_and_stats",
                    poll_interval=0.01, timeout=0.02,
                )

        # Never got far enough to even look at reports on a timeout.
        surveyor.get_survey_reports_by_guid.assert_not_called()

    def test_ambiguous_report_attribution_propagates_as_its_own_error(self):
        surveyor = MagicMock()
        same_instant = (NOW + timedelta(seconds=1)).isoformat()
        surveyor.get_survey_reports_by_guid.return_value = [
            {"guid": "concurrent-a", "surveyed_at": same_instant},
            {"guid": "concurrent-b", "surveyed_at": same_instant},
        ]
        metadata_expert = MagicMock()
        metadata_expert.get_metadata_element_by_guid.return_value = _mock_element("COMPLETED")

        with patch(
            "resource_explorer.surveyors.egeria_async_survey_result._get_clients",
            return_value=(MagicMock(), metadata_expert),
        ):
            with pytest.raises(SurveyReportAttributionError, match="Ambiguous"):
                poll_trigger_and_retrieve_annotations(
                    surveyor=surveyor, engine_action_guid="action-3", resource_guid="db-guid-1",
                    triggered_at=NOW, analysis_step="postgres_schema_and_stats",
                )

        surveyor.get_annotations_by_report_guid.assert_not_called()
