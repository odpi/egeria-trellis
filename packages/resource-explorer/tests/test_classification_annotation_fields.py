"""Every annotation RE publishes reaches Egeria with nothing dropped: ClassificationAnnotation first.

Egeria 6.2's annotation beans ignore unknown fields. RE sent each ClassificationAnnotation's proposal as
`candidateClassifications`, which `ClassificationAnnotationProperties` does not have, so Egeria stored every one of
them with no proposal and returned a GUID (found 2026-10-15 on db_derived's db_classification; the same field is set
by every repo, database and file-system surveyor that proposes a classification, all through
`annotation_props.build_annotation_props`).

The fake here is `DataDiscovery` itself (`create_autospec(spec_set=True)`), and what it stores is what Egeria keeps
(tests/egeria_annotation_beans.py: the beans' own setters at V6.2). A test that reads the stored body therefore sees
what Egeria would hold, and any field Egeria would drop is recorded.
"""
from __future__ import annotations

import json
from unittest.mock import create_autospec

import pytest
from pyegeria.omvs.data_discovery import DataDiscovery

from resource_explorer.surveyors.annotation_props import (
    CANDIDATES_KEY,
    EGERIA_PROPERTIES_CLASSES,
    build_annotation_props,
    publish_annotations,
)
from resource_explorer.surveyors.egeria_reader import _parse_annotation
from resource_explorer.surveyors.survey_report import (
    AnnotationType,
    ClassificationAnnotation,
    DataClassAnnotation,
    DataGrainAnnotation,
    FingerprintAnnotation,
    QualityScoreAnnotation,
    RelationshipAnnotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
    ResourcePhysicalStatusAnnotation,
    ResourceProfileAnnotation,
    ResourceProfileLogAnnotation,
    SchemaAnalysisAnnotation,
)
from tests.egeria_annotation_beans import ACCEPTED_FIELDS, SOURCE, accepted_fields, egeria_keeps

_COMMON = {"summary": "s", "analysis_step": "step", "explanation": "why", "expression": "e", "confidence": 80,
           "json_properties": {"j": 1}, "content_status": "DRAFT"}

#: One annotation per type RE publishes, with every field RE can send set.
FULL = {
    AnnotationType.RESOURCE_MEASURE: ResourceMeasureAnnotation(
        **_COMMON, resource_properties={"n": 1}, additional_properties={"resultState": "measured"}),
    AnnotationType.CLASSIFICATION: ClassificationAnnotation(
        **_COMMON, candidate_classifications=["oltp", "warehouse"], additional_properties={"resultState": "m"}),
    AnnotationType.QUALITY_SCORE: QualityScoreAnnotation(**_COMMON, quality_scores={"completeness": 0.9}),
    AnnotationType.DATA_CLASS: DataClassAnnotation(**_COMMON, candidate_data_class_names=["email"]),
    AnnotationType.REQUEST_FOR_ACTION: RequestForActionAnnotation(
        **_COMMON, action_requested="review", action_target_name="t"),
    AnnotationType.SCHEMA_ANALYSIS: SchemaAnalysisAnnotation(**_COMMON, schema_name="public", schema_type="rel"),
    AnnotationType.RELATIONSHIP: RelationshipAnnotation(
        **_COMMON, related_entity_name="E", relationship_type_name="T"),
    AnnotationType.RESOURCE_PHYSICAL_STATUS: ResourcePhysicalStatusAnnotation(
        **_COMMON, physical_properties={"size": 3}),
    AnnotationType.DATA_GRAIN: DataGrainAnnotation(
        **_COMMON, grain_statement="one row per order", granularity_basis="order", interval=1,
        interval_name="per-day", candidate_data_grain_guids=["g"]),
    AnnotationType.FINGERPRINT: FingerprintAnnotation(**_COMMON, fingerprint_properties={"hash": "x"}),
    AnnotationType.RESOURCE_PROFILE: ResourceProfileAnnotation(
        **_COMMON, value_count={"a": 2}, additional_properties={"k": "v"}),
    AnnotationType.RESOURCE_PROFILE_LOG: ResourceProfileLogAnnotation(
        **_COMMON, log_file="f.csv", additional_properties={"k": "v"}),
}

#: Types where RE today sends a field Egeria 6.2 drops. Each fails strictly; fixing one turns its xfail into a
#: failure that says to remove it from here.
KNOWN_DROPS = {
    AnnotationType.RELATIONSHIP: ["relatedEntityName"],       # the bean has relatedEntityGUID
}


class StrictEgeria:
    """`DataDiscovery` as an autospec whose create_annotation stores only what Egeria's bean keeps."""

    def __init__(self):
        self.stored: dict[str, dict] = {}
        self.dropped: list[dict] = []
        self.discovery = create_autospec(DataDiscovery, spec_set=True, instance=True)
        self.discovery.create_annotation.side_effect = self._create

    def _create(self, body):
        kept, dropped = egeria_keeps(body["properties"])
        if dropped:
            self.dropped.append({"class": kept["class"], "fields": dropped})
        guid = f"guid-{len(self.stored)}"
        self.stored[guid] = kept
        return guid


def _publish(annotations: list) -> StrictEgeria:
    egeria = StrictEgeria()
    publish_annotations(egeria.discovery, lambda qn: "", annotations, "report-guid", "Annotation::test::t")
    return egeria


# ── the table the fake trusts ─────────────────────────────────────────────────

def test_the_accepted_fields_come_from_egeria_6_2_for_every_class_re_publishes():
    assert SOURCE["egeria_ref"] == "V6.2"
    assert set(EGERIA_PROPERTIES_CLASSES.values()) <= set(ACCEPTED_FIELDS)
    # ClassificationAnnotation's own two, as the typedef has them, and not the field RE used to send.
    own = accepted_fields("ClassificationAnnotationProperties") - accepted_fields("DataClassAnnotationProperties")
    assert own == {"classificationName", "properties"}
    assert "candidateClassifications" not in accepted_fields("ClassificationAnnotationProperties")


def test_a_class_egeria_has_no_bean_for_is_refused():
    with pytest.raises(ValueError, match="no annotation properties bean"):
        egeria_keeps({"class": "RequestForActionAnnotationProperties"})


# ── every type RE publishes ───────────────────────────────────────────────────

@pytest.mark.parametrize("atype", [
    pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=f"RE sends {KNOWN_DROPS[t]}, Egeria drops it"))
    if t in KNOWN_DROPS else t
    for t in EGERIA_PROPERTIES_CLASSES], ids=lambda t: t.value)
def test_egeria_keeps_every_field_re_sends(atype):
    egeria = _publish([FULL[atype]])
    assert egeria.dropped == []


@pytest.mark.parametrize("atype", list(KNOWN_DROPS), ids=lambda t: t.value)
def test_the_known_drops_are_exactly_these(atype):
    egeria = _publish([FULL[atype]])
    assert [d["fields"] for d in egeria.dropped] == [KNOWN_DROPS[atype]]


# ── ClassificationAnnotation in Egeria's fields ───────────────────────────────

def test_a_ranked_proposal_is_published_as_classification_name_and_properties():
    props = build_annotation_props(FULL[AnnotationType.CLASSIFICATION], "QN")
    assert props["classificationName"] == "oltp"
    assert json.loads(props["properties"][CANDIDATES_KEY]) == ["oltp", "warehouse"]
    assert "candidateClassifications" not in props


def test_a_single_proposal_names_it_and_sends_no_list():
    props = build_annotation_props(ClassificationAnnotation(summary="s", analysis_step="a",
                                                            candidate_classifications=["Apache-2.0"]), "QN")
    assert props["classificationName"] == "Apache-2.0" and "properties" not in props


def test_a_proposal_of_nothing_sends_neither_field():
    props = build_annotation_props(ClassificationAnnotation(summary="s", analysis_step="a"), "QN")
    assert "classificationName" not in props and "properties" not in props


def test_a_library_step_proposal_keeps_its_name_and_values():
    ann = ClassificationAnnotation(summary="s", analysis_step="hub_tables", classification_name="hub table",
                                   classification_properties={"Foreign Keys In": 3})
    props = build_annotation_props(ann, "QN")
    assert props["classificationName"] == "hub table" and props["properties"] == {"Foreign Keys In": "3"}


def test_db_derived_classification_reaches_egeria_whole_and_reads_back():
    """The live bug, end to end: db_derived's db_classification through the shared publish loop into a strict
    DataDiscovery, then read back the way egeria_reader reads an annotation."""
    from resource_explorer.surveyors.database.db_derived import (
        STATE_MEASURED,
        _classification_annotations,
    )

    anns = _classification_annotations({"state": STATE_MEASURED, "kind": "oltp", "confidence": 70,
                                        "explanation": "x", "ranked": ["oltp", "warehouse", "staging"],
                                        "scores": {}, "coverage": None, "signals_used": [], "signals_missing": []})
    egeria = _publish(anns)
    assert egeria.dropped == []
    (stored,) = egeria.stored.values()
    assert stored["class"] == "ClassificationAnnotationProperties"
    assert stored["classificationName"] == "oltp"
    back = _parse_annotation({"guid": "g", "type": {"typeName": "ClassificationAnnotation"}}, stored)
    assert back["subtype_data"]["classificationName"] == "oltp"
    assert back["subtype_data"]["candidateClassifications"] == ["oltp", "warehouse", "staging"]


def test_a_single_name_reads_back_as_its_one_candidate():
    back = _parse_annotation({"guid": "g", "type": {"typeName": "ClassificationAnnotation"}},
                             {"class": "ClassificationAnnotationProperties", "classificationName": "Apache-2.0"})
    assert back["subtype_data"]["candidateClassifications"] == ["Apache-2.0"]


# ── review round 2 ────────────────────────────────────────────────────────────

def _pre_fix_body(**props) -> dict:
    """An outbox payload as RE enqueued it before 2026-10-15: the proposal in candidateClassifications."""
    return {"class": "NewElementRequestBody", "parentGUID": "report-guid",
            "parentRelationshipTypeName": "ReportedAnnotation", "isOwnAnchor": False, "anchorGUID": "report-guid",
            "properties": {"class": "ClassificationAnnotationProperties", "qualifiedName": "Annotation::x::0",
                           "annotationType": "db_classification", "summary": "s", "analysisStep": "db_derived",
                           "confidence": 70, **props}}


@pytest.mark.parametrize(("candidates", "name", "listed"), [
    (["oltp", "warehouse"], "oltp", ["oltp", "warehouse"]),
    (["Apache-2.0"], "Apache-2.0", None),
])
def test_an_outbox_row_enqueued_before_the_fix_drains_in_egerias_fields(candidates, name, listed):
    from resource_explorer.egeria_outbox import OutboxClients, apply_element

    egeria = StrictEgeria()
    row = {"id": 1, "egeria_guid": None, "qualified_name": "Annotation::x::0", "element_kind": "annotation",
           "payload_json": json.dumps(_pre_fix_body(candidateClassifications=candidates))}
    assert apply_element(row, OutboxClients(discovery=egeria.discovery), lambda qn: "") == "guid-0"
    assert egeria.dropped == []
    stored = egeria.stored["guid-0"]
    assert stored["classificationName"] == name
    got = stored.get("properties", {}).get(CANDIDATES_KEY)
    assert (json.loads(got) if got else None) == listed


def test_an_outbox_row_already_in_egerias_fields_drains_unchanged():
    from resource_explorer.egeria_outbox import OutboxClients, apply_element

    egeria = StrictEgeria()
    body = _pre_fix_body(classificationName="oltp")
    row = {"id": 1, "egeria_guid": None, "qualified_name": "Annotation::x::0", "element_kind": "annotation",
           "payload_json": json.dumps(body)}
    apply_element(row, OutboxClients(discovery=egeria.discovery), lambda qn: "")
    egeria.discovery.create_annotation.assert_called_once_with(body=body)


def test_a_malformed_properties_value_does_not_break_the_read():
    back = _parse_annotation({"guid": "g", "type": {"typeName": "ClassificationAnnotation"}},
                             {"class": "ClassificationAnnotationProperties", "classificationName": "oltp",
                              "properties": "not a map"})
    assert back["subtype_data"]["candidateClassifications"] == ["oltp"]


def test_an_explicit_name_is_first_in_the_published_list():
    ann = ClassificationAnnotation(summary="s", analysis_step="a", classification_name="hub table",
                                   candidate_classifications=["staging", "hub table", "fact"])
    props = build_annotation_props(ann, "QN")
    assert props["classificationName"] == "hub table"
    assert json.loads(props["properties"][CANDIDATES_KEY]) == ["hub table", "staging", "fact"]
    back = _parse_annotation({"guid": "g", "type": {"typeName": "ClassificationAnnotation"}}, props)
    assert back["subtype_data"]["candidateClassifications"][0] == "hub table"


def test_an_explicit_name_not_among_the_candidates_is_added_first():
    ann = ClassificationAnnotation(summary="s", analysis_step="a", classification_name="hub table",
                                   candidate_classifications=["staging"])
    assert json.loads(build_annotation_props(ann, "QN")["properties"][CANDIDATES_KEY]) == ["hub table", "staging"]


def test_the_generator_names_fields_as_jackson_legacy_mangling_does():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "scripts" / "extract_egeria_annotation_fields.py"
    spec = importlib.util.spec_from_file_location("extract_egeria_annotation_fields", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert [mod._jackson_name(s) for s in ("QualifiedName", "URL", "URLPath", "X")] == \
        ["qualifiedName", "url", "urlpath", "x"]


# ── QualityScoreAnnotation: the scores survive Egeria ─────────────────────────

def test_quality_scores_reach_egeria_and_read_back():
    """`QualityAnnotationProperties` has an int `qualityScore`, not a `qualityScores` map, and ignores unknown
    fields, so the map RE sent was stored as nothing. The scores now ride in `jsonProperties` (a field every
    annotation bean keeps) and read back under the same `subtype_data["qualityScores"]` key the readers use."""
    ann = QualityScoreAnnotation(summary="s", analysis_step="SQLViewAnalysis",
                                 quality_scores={"complexity_score": 12.0, "join_count": 3.0})
    egeria = _publish([ann])
    assert egeria.dropped == []
    (stored,) = egeria.stored.values()
    back = _parse_annotation({"guid": "g", "type": {"typeName": "QualityAnnotation"}}, stored)
    # Numbers, not strings: the materializer's `_numeric_values` deliberately ignores numeric-looking strings.
    assert back["subtype_data"]["qualityScores"] == {"complexity_score": 12.0, "join_count": 3.0}


def test_quality_scores_do_not_overwrite_the_annotations_own_json_properties():
    ann = QualityScoreAnnotation(summary="s", analysis_step="a", quality_scores={"foss_scorecard": 7.5},
                                 json_properties={"outcome": "recovered", "checks_unknown": 2})
    egeria = _publish([ann])
    (stored,) = egeria.stored.values()
    carried = json.loads(stored["jsonProperties"])
    assert carried["outcome"] == "recovered" and carried["checks_unknown"] == 2
    assert carried["qualityScores"] == {"foss_scorecard": 7.5}
