"""Tests for the engine-agnostic egeria_survey_reader module — the shared
guid-walk logic used by database/repo/filesystem surveyors alike."""
from __future__ import annotations

from unittest.mock import MagicMock

from resource_explorer.surveyors.egeria_survey_reader import (
    get_annotations_by_report_guid,
    get_survey_reports_by_guid,
)


class TestGetSurveyReportsByGuid:
    def test_parses_reports_key(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = {
            "reports": [{
                "relatedElement": {
                    "elementHeader": {
                        "guid": "report-guid-1",
                        "versions": {"createTime": "2026-07-08T00:00:00.000+00:00"},
                    },
                    "properties": {
                        "qualifiedName": "SurveyReport::Test::1",
                        "displayName": "Survey: Test",
                        "description": "A test report",
                        "additionalProperties": {"annotation_count": "3"},
                    },
                },
            }],
        }
        reports = get_survey_reports_by_guid(asset_maker, "resource-guid-1")
        assert len(reports) == 1
        assert reports[0]["guid"] == "report-guid-1"
        assert reports[0]["annotation_count"] == 3
        asset_maker.get_asset_by_guid.assert_called_once_with(
            "resource-guid-1", body={"class": "GetRequestBody", "graphQueryDepth": 1}, output_format="JSON"
        )

    def test_no_reports_key_returns_empty_list(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = {"elementHeader": {}, "properties": {}}
        assert get_survey_reports_by_guid(asset_maker, "resource-guid-1") == []

    def test_non_dict_result_returns_empty_list(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = "No elements found"
        assert get_survey_reports_by_guid(asset_maker, "resource-guid-1") == []

    def test_exception_returns_empty_list_not_raised(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.side_effect = Exception("Egeria unreachable")
        assert get_survey_reports_by_guid(asset_maker, "resource-guid-1") == []


class TestGetAnnotationsByReportGuid:
    def test_parses_reported_annotations_key(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = {
            "reportedAnnotations": [{
                "relatedElement": {
                    "elementHeader": {"guid": "ann-guid-1", "type": {"typeName": "ResourceMeasureAnnotation"}},
                    "properties": {
                        "summary": "Database size: 42 MB",
                        "confidence": 100,
                        "analysisStep": "Profiling Associated Resources",
                        "explanation": "Point-in-time size measurement.",
                    },
                },
            }],
        }
        annotations = get_annotations_by_report_guid(asset_maker, "report-guid-1")
        assert len(annotations) == 1
        assert annotations[0]["annotation_type"] == "ResourceMeasureAnnotation"
        assert annotations[0]["summary"] == "Database size: 42 MB"

    def test_annotation_type_falls_back_to_property_when_no_type_name(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = {
            "reportedAnnotations": [{
                "relatedElement": {
                    "elementHeader": {"guid": "ann-guid-2"},
                    "properties": {"annotationType": "SchemaAnalysisAnnotation", "summary": "5 tables"},
                },
            }],
        }
        annotations = get_annotations_by_report_guid(asset_maker, "report-guid-1")
        assert annotations[0]["annotation_type"] == "SchemaAnalysisAnnotation"

    def test_no_reported_annotations_key_returns_empty_list(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.return_value = {"elementHeader": {}, "properties": {}}
        assert get_annotations_by_report_guid(asset_maker, "report-guid-1") == []

    def test_exception_returns_empty_list_not_raised(self):
        asset_maker = MagicMock()
        asset_maker.get_asset_by_guid.side_effect = Exception("Egeria unreachable")
        assert get_annotations_by_report_guid(asset_maker, "report-guid-1") == []


# ── jsonProperties: Egeria stores a JSON STRING; the reader hands back a dict ──

_OFFENDING = '{"outcome": "recovered", "detail": {"chunks": 51729}}'


def _report_with(json_properties):
    props = {"annotationType": "Fake", "summary": "s"}
    if json_properties is not _MISSING:
        props["jsonProperties"] = json_properties
    return {"reportedAnnotations": [{"relatedElement": {
        "elementHeader": {"guid": "a1", "type": {"typeName": "Annotation"}},
        "properties": props}}]}


_MISSING = object()


class TestJsonPropertiesNormalised:
    def _jp(self, value):
        from resource_explorer.surveyors.egeria_survey_reader import annotations_from_report
        return annotations_from_report(_report_with(value))[0]["json_properties"]

    def test_string_is_parsed_to_dict(self):
        assert self._jp(_OFFENDING) == {"outcome": "recovered", "detail": {"chunks": 51729}}

    def test_invalid_json_keeps_raw_text(self):
        assert self._jp("{not json") == {"raw": "{not json"}

    def test_non_object_json_keeps_raw_text(self):
        assert self._jp("[1, 2]") == {"raw": "[1, 2]"}
        assert self._jp("42") == {"raw": "42"}

    def test_dict_passes_through(self):
        assert self._jp({"a": 1}) == {"a": 1}

    def test_empty_and_missing_become_empty_dict(self):
        assert self._jp("") == {}
        assert self._jp(None) == {}
        assert self._jp("   ") == {}
        assert self._jp(_MISSING) == {}

    def test_other_type_keeps_text(self):
        assert self._jp(7) == {"raw": "7"}
