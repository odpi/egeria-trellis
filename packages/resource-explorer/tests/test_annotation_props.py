"""Tests for the shared annotation->Egeria properties mapping.

These exist because the filesystem publisher shipped a copy of this logic that
raised AttributeError on the first annotation of every run, and the suite could
not see it: tests/test_filesystem_survey_definition_adapter.py patches
`_create_annotations` out entirely, so the broken code never executed under
test. A mock sat exactly where the bug was.

The guard against a repeat is not "test the filesystem copy" — it is that there
is only one copy, and that every publisher is asserted to produce byte-identical
output from it. A future divergence fails here rather than in production.
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.annotation_props import build_annotation_props, to_string_map
from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
from resource_explorer.surveyors.survey_report import (
    AnnotationType,
    ClassificationAnnotation,
    DataClassAnnotation,
    QualityScoreAnnotation,
    RelationshipAnnotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

PUBLISHERS = [EgeriaPublisher, EgeriaDatabaseSurveyor, EgeriaFileSystemSurveyor]

ALL_ANNOTATIONS = [
    ResourceMeasureAnnotation(summary="1234 files", analysis_step="a", resource_properties={"n": 1}),
    ClassificationAnnotation(summary="s", analysis_step="a", candidate_classifications=["python"]),
    QualityScoreAnnotation(summary="s", analysis_step="a"),
    DataClassAnnotation(summary="s", analysis_step="a"),
    RequestForActionAnnotation(summary="s", analysis_step="a"),
    SchemaAnalysisAnnotation(summary="s", analysis_step="a"),
    RelationshipAnnotation(summary="s", analysis_step="a",
                           related_entity_name="E", relationship_type_name="T"),
]


def _call(cls, ann, qn="QN::test"):
    """Invoke a publisher's method without constructing one (no Egeria needed)."""
    return cls._build_annotation_props(cls.__new__(cls), ann, qn)


class TestNoPublisherDrift:
    @pytest.mark.parametrize("ann", ALL_ANNOTATIONS, ids=lambda a: type(a).__name__)
    def test_every_publisher_produces_identical_props(self, ann):
        """The actual regression guard. Three copies of this logic drifted far
        enough that one emitted valueProperties/confidenceLevel where the others
        emitted resourceProperties/confidence, and dropped a whole annotation
        type — all while the suite stayed green."""
        outputs = [_call(cls, ann) for cls in PUBLISHERS]
        assert outputs[0] == outputs[1] == outputs[2], (
            f"{type(ann).__name__} diverges: "
            + " vs ".join(f"{c.__name__}={o}" for c, o in zip(PUBLISHERS, outputs))
        )

    @pytest.mark.parametrize("cls", PUBLISHERS, ids=lambda c: c.__name__)
    @pytest.mark.parametrize("ann", ALL_ANNOTATIONS, ids=lambda a: type(a).__name__)
    def test_no_publisher_raises_on_any_annotation_type(self, cls, ann):
        """Direct repro of the shipped bug: the filesystem copy read
        `ann.egeria_type_name`, which exists on no annotation class, so this call
        raised AttributeError for every type."""
        props = _call(cls, ann)
        assert props["qualifiedName"] == "QN::test"
        assert props["class"].endswith("Properties")


class TestSharedMapping:
    def test_every_annotation_type_has_a_dedicated_egeria_class(self):
        """A type missing from the class map silently degrades to the generic
        AnnotationProperties — which is how RELATIONSHIP was being dropped."""
        for ann in ALL_ANNOTATIONS:
            assert build_annotation_props(ann, "QN")["class"] != "AnnotationProperties", (
                f"{ann.annotation_type} falls through to the generic class"
            )

    def test_relationship_annotation_is_mapped(self):
        """Regression: the filesystem copy omitted RELATIONSHIP from both its
        dispatch and its class map, so relationship advice was unpublishable."""
        props = build_annotation_props(ALL_ANNOTATIONS[-1], "QN")
        assert props["class"] == "RelationshipAdviceAnnotationProperties"
        assert props["relatedEntityName"] == "E"
        assert props["relationshipTypeName"] == "T"

    def test_resource_measure_uses_resourceProperties_not_valueProperties(self):
        """The filesystem copy emitted `valueProperties`, which is not the field
        Egeria's ResourceMeasureAnnotation declares."""
        props = build_annotation_props(
            ResourceMeasureAnnotation(summary="s", analysis_step="a", resource_properties={"n": 1}), "QN")
        assert "resourceProperties" in props and "valueProperties" not in props

    def test_confidence_not_confidenceLevel(self):
        """Likewise `confidenceLevel` vs the declared `confidence`."""
        props = build_annotation_props(ALL_ANNOTATIONS[0], "QN")
        assert "confidence" in props and "confidenceLevel" not in props

    def test_annotation_type_is_always_emitted(self):
        for ann in ALL_ANNOTATIONS:
            assert build_annotation_props(ann, "QN")["annotationType"] == ann.annotation_type.value

    def test_empty_optional_fields_are_omitted_not_sent_blank(self):
        ann = ResourceMeasureAnnotation(summary="s", analysis_step="a")  # no explanation/expression
        props = build_annotation_props(ann, "QN")
        assert "explanation" not in props and "expression" not in props


class TestToStringMap:
    def test_nested_structures_are_json_encoded(self):
        """Egeria typed map fields are map<string,string>; nested structures must
        not escape into the wire format."""
        out = to_string_map({"scalar": 1, "nested": {"a": 2}, "list": [1, 2]})
        assert out == {"scalar": "1", "nested": '{"a": 2}', "list": "[1, 2]"}
        assert all(isinstance(v, str) for v in out.values())

    @pytest.mark.parametrize("cls", PUBLISHERS, ids=lambda c: c.__name__)
    def test_publishers_share_one_to_string_map(self, cls):
        assert cls._to_string_map({"a": {"b": 1}}) == to_string_map({"a": {"b": 1}})


class TestEvidenceOfField:
    """docs/annotation-linking-plan.md Phase 1: Annotation.evidence_of — a
    same-run list-index signal, not a GUID (see the field's own docstring
    for why cross-run reconstruction is unsafe)."""

    def test_default_is_none_for_every_existing_annotation(self):
        """Every annotation constructed the old way (no evidence_of kwarg)
        must be unaffected — this is the compatibility guarantee the plan's
        rollback section relies on."""
        for ann in ALL_ANNOTATIONS:
            assert ann.evidence_of is None

    def test_can_be_set_to_point_at_another_annotation_in_the_same_run(self):
        per_file = SchemaAnalysisAnnotation(summary="file 1", analysis_step="a", evidence_of=0)
        assert per_file.evidence_of == 0

    def test_known_negative_zero_is_not_falsy_none(self):
        """0 is a legitimate index (the first annotation in the run) and must
        not be conflated with "unset" — a naive `if ann.evidence_of:` guard
        anywhere downstream would silently drop evidence pointing at index 0.
        This pins the field's actual type contract (`int | None`, checked
        with `is not None`), not just that *some* value round-trips."""
        ann = SchemaAnalysisAnnotation(summary="s", analysis_step="a", evidence_of=0)
        assert ann.evidence_of is not None
        assert not (ann.evidence_of or False)  # 0 is falsy in Python...
        assert ann.evidence_of is not None  # ...but must not be mistaken for unset


class TestDataGrainInterval:
    """Egeria's DataGrainAnnotationProperties.interval is a Java `long`
    (DataGrainAnnotationProperties.java: `private long interval = 0L;`,
    OpenMetadataProperty.INTERVAL = DataType.LONG, "expressing in the units of the
    grain", example "60"). The live gate on 2026-10-14 sent "per-date" and got 400:
    "Cannot deserialize value of type `long` from String \"per-date\"" for every grain."""

    def test_a_whole_number_interval_is_sent_as_a_number(self):
        from resource_explorer.surveyors.survey_report import DataGrainAnnotation

        props = build_annotation_props(DataGrainAnnotation(summary="s", analysis_step="a", interval=60), "QN")
        assert props["interval"] == 60 and isinstance(props["interval"], int)

    def test_zero_is_sent_not_dropped(self):
        from resource_explorer.surveyors.survey_report import DataGrainAnnotation

        props = build_annotation_props(DataGrainAnnotation(summary="s", analysis_step="a", interval=0), "QN")
        assert props["interval"] == 0

    @pytest.mark.parametrize("bad", ["per-date", "60", 1.5, True])
    def test_a_non_integer_interval_is_refused_before_it_reaches_egeria(self, bad):
        from resource_explorer.surveyors.survey_report import DataGrainAnnotation

        with pytest.raises(TypeError, match="interval"):
            build_annotation_props(DataGrainAnnotation(summary="s", analysis_step="a", interval=bad), "QN")

    def test_the_period_word_rides_in_additional_properties(self):
        from resource_explorer.surveyors.survey_report import DataGrainAnnotation

        props = build_annotation_props(
            DataGrainAnnotation(summary="s", analysis_step="a", interval_name="monthly"), "QN")
        assert "interval" not in props
        assert props["additionalProperties"] == {"intervalName": "monthly"}


class TestFailedCreateLogsEgeriasWords:
    """pyegeria's CLIENT_ERROR_400 text names only the URL; Egeria's reason is in the
    response body (FFDCResponseBase.exceptionErrorMessage). Body below is the live one
    captured 2026-10-14 from a DataGrainAnnotation create on qs-view-server."""

    LIVE_BODY = {
        "class": "FFDCResponseBase", "relatedHTTPCode": 400,
        "exceptionClassName": "com.fasterxml.jackson.databind.exc.InvalidFormatException",
        "exceptionErrorMessage": "Cannot deserialize value of type `long` from String \"per-date\": "
                                 "not a valid `long` value",
        "exceptionSystemAction": "The system is unable to map the request to Egeria's implementation "
                                 "and returns with an error.",
        "exceptionUserAction": "Ensure JSON fields match the expected Java object structure and types.",
    }

    def _client_error(self):
        import json as _json

        import httpx
        from pyegeria.core._exceptions import PyegeriaClientException

        url = "https://localhost:9443/servers/qs-view-server/api/open-metadata/data-discovery/annotations"
        response = httpx.Response(400, json=self.LIVE_BODY, request=httpx.Request("POST", url))
        return PyegeriaClientException(response, {"class name": "BaseServerClient"},
                                       {"userid": "u", "reason": _json.dumps(self.LIVE_BODY)})

    def test_the_warning_carries_egerias_exception_error_message(self, caplog):
        from unittest.mock import create_autospec

        from pyegeria.omvs.data_discovery import DataDiscovery

        from resource_explorer.surveyors.annotation_props import publish_annotations
        from resource_explorer.surveyors.survey_report import DataGrainAnnotation

        discovery = create_autospec(DataDiscovery, instance=True, spec_set=True)
        discovery.create_annotation.side_effect = self._client_error()
        ann = DataGrainAnnotation(summary="s", analysis_step="a", grain_statement="one row per id")
        with caplog.at_level("WARNING", logger="resource_explorer.surveyors.annotation_props"):
            out = publish_annotations(discovery, lambda qn: "", [ann], "report-guid", "Annotation::t")
        assert out == [None]
        text = caplog.text
        assert "Cannot deserialize value of type `long` from String \"per-date\"" in text
        assert "InvalidFormatException" in text
        assert "400" in text

    def test_detail_reads_the_fields_pyegeria_fills(self):
        from resource_explorer.egeria_absence import egeria_error_detail

        detail = egeria_error_detail(self._client_error())
        assert detail.startswith("HTTP 400")
        assert "InvalidFormatException" in detail
        assert "not a valid `long` value" in detail

    def test_detail_is_empty_for_an_exception_with_no_egeria_body(self):
        from resource_explorer.egeria_absence import egeria_error_detail

        assert egeria_error_detail(RuntimeError("boom")) == ""
