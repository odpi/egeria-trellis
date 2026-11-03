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


def _expert_serving(*entries):
    from tests.report_pages import paged_expert

    return paged_expert(lambda guid: list(entries))


class TestGetAnnotationsByReportGuid:
    """The annotations of a report are read through the paged `ReportedAnnotation` list (MetadataExpert), never from
    one graph read of the report, which holds one page."""

    def test_parses_reported_annotations(self):
        expert = _expert_serving(("ann-guid-1", "ResourceMeasureAnnotation", {
            "summary": "Database size: 42 MB", "confidence": 100, "analysisStep": "Profiling Associated Resources",
            "explanation": "Point-in-time size measurement.",
            "additionalProperties": {"unit": "MB"}, "jsonProperties": '{"a": 1}'}))
        annotations = get_annotations_by_report_guid(expert, "report-guid-1")
        assert len(annotations) == 1
        assert annotations[0]["annotation_type"] == "ResourceMeasureAnnotation"
        assert annotations[0]["summary"] == "Database size: 42 MB"
        assert annotations[0]["type_name"] == "ResourceMeasureAnnotation" and annotations[0]["guid"] == "ann-guid-1"
        assert annotations[0]["confidence"] == 100 and annotations[0]["analysis_step"] == "Profiling Associated Resources"
        assert annotations[0]["additional_properties"] == {"unit": "MB"} and annotations[0]["json_properties"] == {"a": 1}

    def test_annotation_type_falls_back_to_property_when_no_type_name(self):
        expert = _expert_serving(("ann-guid-2", "", {"annotationType": "SchemaAnalysisAnnotation", "summary": "5 tables"}))
        annotations = get_annotations_by_report_guid(expert, "report-guid-1")
        assert annotations[0]["annotation_type"] == "SchemaAnalysisAnnotation"

    def test_enum_property_reads_as_its_symbolic_name(self):
        from tests.report_pages import related_entry

        entry = related_entry("a1", "Annotation", {"summary": "s"})
        entry["element"]["elementProperties"]["propertyValueMap"]["contentStatus"] = {
            "class": "EnumTypePropertyValue", "symbolicName": "DRAFT"}
        from resource_explorer.surveyors.egeria_survey_reader import annotation_from_related_entry

        assert annotation_from_related_entry(entry)["content_status"] == "DRAFT"

    def test_no_annotations_returns_empty_list(self):
        assert get_annotations_by_report_guid(_expert_serving(), "report-guid-1") == []

    def test_exception_returns_empty_list_not_raised(self):
        expert = _expert_serving()
        expert.get_related_metadata_elements.side_effect = Exception("Egeria unreachable")
        assert get_annotations_by_report_guid(expert, "report-guid-1") == []

    def test_every_page_is_read_to_the_end(self):
        from tests.report_pages import paged_expert

        calls = []
        entries = [(f"a{i}", "Annotation", {"qualifiedName": f"qn-{i}"}) for i in range(1200)]
        expert = paged_expert(lambda guid: entries, calls=calls)
        got = get_annotations_by_report_guid(expert, "report-guid-1")
        assert [a["qualified_name"] for a in got] == [f"qn-{i}" for i in range(1200)]
        assert sum(c[3] for c in calls) == 1200 and len(calls) >= 3   # 500 + 500 + 200, then the empty end

    def test_a_list_that_could_not_be_read_to_its_end_is_empty_never_a_partial_page(self):
        from tests.report_pages import paged_expert

        entries = [(f"a{i}", "Annotation", {"qualifiedName": f"qn-{i}"}) for i in range(1200)]
        expert = paged_expert(lambda guid: entries)
        real = expert.get_related_metadata_elements.side_effect

        def second_page_fails(*args, **kwargs):
            if kwargs.get("start_from"):
                raise RuntimeError("503 from the view server")
            return real(*args, **kwargs)

        expert.get_related_metadata_elements.side_effect = second_page_fails
        assert get_annotations_by_report_guid(expert, "report-guid-1") == []

    def test_the_report_is_read_from_end_1_with_the_real_signature(self):
        calls = []
        from tests.report_pages import paged_expert

        expert = paged_expert(lambda guid: [], calls=calls)
        get_annotations_by_report_guid(expert, "report-guid-1")
        [(args, kwargs)] = [(c.args, c.kwargs) for c in expert.get_related_metadata_elements.call_args_list]
        assert args[:2] == ("report-guid-1", "ReportedAnnotation") and kwargs["starting_at_end"] == 1


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
