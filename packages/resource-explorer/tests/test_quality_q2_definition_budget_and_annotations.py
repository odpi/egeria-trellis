"""Quality slice Q2 (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md 2.2, 2.6, 3 and the owner's 5 minutes per table), unit level:

- the DatabaseQualitySurvey definition: its three steps, their links, the reconciler's view of them, the generator;
- one QualityAnnotation per evaluated check, with Egeria's type and properties (QualityAnnotationProperties.java:
  qualityDimension, qualityDescription, qualityScore) and the ODCS dimension words;
- zero accepted checks ends survey-invalid with a sentence, never an empty completed report;
- "N of M annotations in Egeria" from what landed, including zero landed;
- the 5-minute per-table budget, on a fake clock that never sleeps: incomplete, no proposals, never zeros.

The Egeria boundary is faked only with `create_autospec(<real class>, spec_set=True)`, so every call binds the real
pyegeria signature. The route-level end to end is in test_quality_q2_survey_press_e2e.py.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from resource_explorer.surveyors.database.quality import run_checks
from resource_explorer.surveyors.database.quality.catalogue import (
    ODCS_DIMENSIONS,
    SLICE_1_TEST_TYPES,
    egeria_dimension,
    load_catalogue,
)
from resource_explorer.surveyors.database.quality.propose import run_propose
from resource_explorer.surveyors.database.quality.run_checks import (
    ANNOTATION_TYPE_NAME,
    NO_ACCEPTED_CHECKS,
    build_quality_annotations,
    run_quality_checks,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    COLUMNS_PER_QUERY,
    ProfileBounds,
    incomplete_reason,
    run_value_profile,
)
from tests.egeria_annotation_beans import ACCEPTED_FIELDS
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    BOUNDS,
    PROFILE,
    PROFILED_AT,
    SLUG,
    FakeConn,
    _accept,
    _by_key,
    _connect,
    _orders_measure,
    _profile,
    _propose,
    _rows,
    entity,
    registry,
)

PKG = Path(__file__).resolve().parent.parent
DEFS = PKG / "docs" / "dr-egeria" / "survey-definitions-database"
QUALITY = "DatabaseQualitySurvey"
STEPS = ["postgres_value_profile", "propose_quality_checks", "run_quality_checks"]


@pytest.fixture(autouse=True)
def _no_live_database(monkeypatch):
    """Nothing in this file may reach a database: psycopg2 itself refuses."""
    import psycopg2

    def refuse(*a, **k):
        raise AssertionError("a test opened a real PostgreSQL connection")

    monkeypatch.setattr(psycopg2, "connect", refuse)


# ── the definition: three steps, authored, reconciled ───────────────────────────────────────────────────────

def test_the_definition_has_the_three_steps_in_order_and_the_reconciler_agrees():
    from resource_explorer import engine_host as eh
    from resource_explorer.surveyors import survey_definition_reconciler as rec
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions

    doc = documented_definitions()[QUALITY]
    assert doc.resource_type == "database" and doc.survey_kind == "assessment"
    keys = [(doc.step_info.get(k, {}) or {}).get("re_analysis_step") for k in doc.steps]
    assert keys == STEPS
    # The links the document authors are exactly the chain First -> Next -> Next, each with guard Any.
    expected = rec.compute_expected_edges(QUALITY, STEPS)
    assert rec.expected_edges_from_document(QUALITY, doc) == expected and len(expected) == 2
    text = (DEFS / "database-survey-definition-quality.md").read_text()
    assert text.count("## Link First Process Step") == 1 and text.count("## Link Next Process Step") == 2
    # one executor per step, naming the engine and the request type of that step
    blocks = text.split("## Link Action to Action Executor")[1:]
    import re

    links = sorted((re.search(r"### Governance Action Type\n(\S+)", b).group(1),
                    re.search(r"### Governance Engine\n(\S+)", b).group(1),
                    re.search(r"### Request Type\n(\S+)", b).group(1)) for b in blocks)
    assert links == sorted((f"GovActionProcessStep::{QUALITY}::{k}", eh.ENGINE_QUALIFIED_NAME,
                            eh.request_type_for_step(k)) for k in STEPS)


def test_the_reconciler_finds_nothing_to_strip_on_the_authored_chain_and_flags_a_second_run_s_duplicates():
    """`reconcile_database_survey_definition_links.py` works from `compute_expected_edges` / the document: a live
    graph holding exactly the authored links is clean; the copy Dr.Egeria's non-idempotent Link Next Process Step
    makes on a second run is a duplicate to strip; a link that is not authored is stale."""
    from resource_explorer.surveyors import survey_definition_reconciler as rec

    qn = lambda k: f"GovActionProcessStep::{QUALITY}::{k}"          # noqa: E731
    live = [{"previousProcessStep": {"uniqueName": qn(a)}, "nextProcessStep": {"uniqueName": qn(b)},
             "nextProcessStepLinkGUID": f"l-{a}", "guard": "Any"} for a, b in zip(STEPS, STEPS[1:])]
    expected = rec.compute_expected_edges(QUALITY, STEPS)
    clean = rec.diff_links(live, expected, f"GovActionProcess::{QUALITY}")
    assert clean.to_remove == [] and clean.kept == 2
    again = rec.diff_links(live + [{**live[0], "nextProcessStepLinkGUID": "l-copy"}], expected, "p")
    assert [(r.reason, r.link_guid) for r in again.to_remove] == [("duplicate", "l-copy")]
    skipped = rec.diff_links(live + [{**live[0], "nextProcessStep": {"uniqueName": qn("postgres_value_profile")},
                                      "nextProcessStepLinkGUID": "l-wrong"}], expected, "p")
    assert [(r.reason, r.link_guid) for r in skipped.to_remove] == [("stale", "l-wrong")]


def test_the_committed_document_is_what_the_generator_writes():
    """`generate_database_survey_definition.py` run again changes nothing: the document, the CSV and the registry
    agree (the same hash-guarded generator the other three definitions come from)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_db_survey_def", PKG / "scripts" /
                                                  "generate_database_survey_definition.py")
    gen = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gen              # the generator's dataclasses resolve their module by name
    try:
        spec.loader.exec_module(gen)
    finally:
        sys.modules.pop(spec.name, None)
    from resource_explorer.surveyors.dr_egeria_survey_publisher import generate_survey_definition_markdown

    quality = next(s for s in gen.SPECS if s.survey_group == QUALITY)
    assert quality.step_keys == STEPS and quality.output_filename == "database-survey-definition-quality.md"
    markdown = generate_survey_definition_markdown(
        survey_group=quality.survey_group, survey_display_name=quality.survey_display_name,
        technology_type=gen.TECHNOLOGY_TYPE, description=quality.description, steps=gen.build_steps(quality.step_keys),
        survey_kind=quality.survey_kind,
        answers_questions=gen._answered_questions(quality.survey_group, quality.step_keys,
                                                  gen._build_step_key_to_questions()))
    assert markdown.count("## Link Element To Scope") == 3                  # Q-0079 (value_profile), Q-0096, Q-0097
    assert (DEFS / quality.output_filename).read_text() == markdown
    batch = json.loads((DEFS / "_batch.json").read_text())
    assert quality.output_filename in batch["files"]
    assert batch["files"][-1] == "database-survey-definition-analysis.md"       # the canary's document stays last
    provenance = json.loads((DEFS / ".generated_database.json").read_text())
    import hashlib

    assert provenance[quality.output_filename] == hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def test_the_engine_host_declares_the_two_unheld_steps_and_holds_nothing():
    from resource_explorer import engine_host as eh

    text = (PKG / "docs" / "dr-egeria" / "engine-host" / "steps" / "database-steps.md").read_text()
    for key in STEPS:
        assert f"ResourceExplorerSurvey::{eh.request_type_for_step(key)}" in text, key
    assert eh.HELD_FROM_DECLARATION == {}
    assert all(eh.out_of_scope_reason(k) == "" for k in STEPS)


# ── QualityAnnotation: Egeria's type and properties ─────────────────────────────────────────────────────────

def _results(registry, entity, checks=None):
    _accept(registry, "sales.orders.order_id::Unique", "Unique")
    _accept(registry, "sales.orders.placed::Required", "Required")
    _accept(registry, "sales.orders.note::Missing_Pct", "Missing_Pct", baseline_ct=1000, baseline_value_ct=900,
            threshold_value=2, severity="warning")
    _accept(registry, "sales.orders.placed::Min_Date", "Min_Date", baseline_value="2020-01-01")
    _accept(registry, "sales.empty.d::Missing_Pct", "Missing_Pct", baseline_ct=10, baseline_value_ct=9,
            threshold_value=2, severity="warning")        # an empty table: measure undefined -> not established
    conn = FakeConn(checks=checks or {("sales", "orders"): {"record_ct": 1000, "measure": _orders_measure},
                                      ("sales", "empty"): {"record_ct": 0, "measure": lambda sql: None}})
    return run_quality_checks(entity, registry, _connect(conn), bounds=BOUNDS, surveyed_at="2026-10-20T13:00:00+00:00",
                              ran_as="surveyor")


def test_one_quality_annotation_per_evaluated_check_whatever_its_outcome(registry, entity):
    """Design ruling A.2: EVERY evaluated check is a result. The state word is in additionalProperties (the bean has no
    status field: `annotationStatus` is on AnnotationReviewProperties only), with the reason sentence; a check that did
    not conclude carries NO qualityScore, because a 0 would read as failed."""
    _profile(registry, entity)
    out = _results(registry, entity)
    results = _by_key(registry, "database_quality_results")
    assert len(results) == 5
    annotations = out["quality_annotations"]
    assert [a.item_key for a in annotations] == sorted(results)          # one per check, in check-key order
    assert len({(a.check_name, a.item_key) for a in annotations}) == len(annotations)
    states = {a.item_key: (a.additional_properties["checkState"], a.quality_score) for a in annotations}
    assert states == {
        "sales.empty.d::Missing_Pct": ("not_established", None),      # the measure is undefined on an empty table
        "sales.orders.note::Missing_Pct": ("failed", 0),              # a warning: it did not hold
        "sales.orders.order_id::Unique": ("passed", 100),
        "sales.orders.placed::Min_Date": ("error", None),             # the probe could not run
        "sales.orders.placed::Required": ("failed", 0),
    }
    for a in annotations:
        # the reason sentence travels with the state, in the description and in the detail
        assert a.additional_properties["checkStateReason"] == results[a.item_key]["reason"]
        assert a.quality_description.startswith(a.additional_properties["checkState"] + ":")
        assert (a.quality_score is None) == (results[a.item_key]["status"] not in run_checks.CONCLUDED)
    # the record count the check read is the annotation's sampleSize where it is known
    assert {a.item_key: a.sample_size for a in annotations}["sales.orders.order_id::Unique"] == 1000
    assert {a.item_key: a.sample_size for a in annotations}["sales.empty.d::Missing_Pct"] == 0     # a real zero


def test_a_check_that_did_not_conclude_sends_no_quality_score_to_egeria(registry, entity):
    from resource_explorer.surveyors.annotation_props import build_annotation_props

    _profile(registry, entity)
    by_key = {a.item_key: a for a in _results(registry, entity)["quality_annotations"]}
    empty = build_annotation_props(by_key["sales.empty.d::Missing_Pct"], "Annotation::q::1")
    assert "qualityScore" not in empty                    # absent, not 0: the bean's int would read it as 0
    assert empty["qualityDimension"] == "completeness" and empty["sampleSize"] == 0
    assert empty["additionalProperties"]["checkState"] == "not_established"
    assert empty["confidence"] == 0
    errored = build_annotation_props(by_key["sales.orders.placed::Min_Date"], "Annotation::q::2")
    assert "qualityScore" not in errored and "sampleSize" not in errored and errored["qualityDimension"] == "accuracy"
    held = build_annotation_props(by_key["sales.orders.order_id::Unique"], "Annotation::q::3")
    assert held["qualityScore"] == 100 and held["sampleSize"] == 1000
    for props in (empty, errored, held):
        assert set(props) <= ACCEPTED_FIELDS["QualityAnnotationProperties"]
    assert "sampleSize" in ACCEPTED_FIELDS["QualityAnnotationProperties"]


def test_a_quality_annotation_is_egerias_quality_annotation_with_its_three_fields(registry, entity):
    from resource_explorer.surveyors.annotation_props import EGERIA_PROPERTIES_CLASSES, build_annotation_props
    from resource_explorer.surveyors.survey_report import AnnotationType

    _profile(registry, entity)
    annotations = {a.item_key: a for a in _results(registry, entity)["quality_annotations"]}
    assert EGERIA_PROPERTIES_CLASSES[AnnotationType.QUALITY_SCORE] == "QualityAnnotationProperties"

    unique = build_annotation_props(annotations["sales.orders.order_id::Unique"], "Annotation::q::1")
    assert unique["class"] == "QualityAnnotationProperties" and unique["annotationType"] == ANNOTATION_TYPE_NAME
    assert unique["analysisStep"] == "run_quality_checks"
    assert (unique["qualityDimension"], unique["qualityScore"]) == ("uniqueness", 100)       # it held
    assert unique["qualityDescription"].startswith("passed:") and "Severity error" in unique["qualityDescription"]
    required = build_annotation_props(annotations["sales.orders.placed::Required"], "Annotation::q::2")
    assert (required["qualityDimension"], required["qualityScore"]) == ("completeness", 0)   # it did not
    assert required["qualityDescription"].startswith("failed:")
    warn = build_annotation_props(annotations["sales.orders.note::Missing_Pct"], "Annotation::q::3")
    assert (warn["qualityDimension"], warn["qualityScore"]) == ("completeness", 0)
    assert "Severity warning" in warn["qualityDescription"]

    # the check's own detail rides in additionalProperties (map<string,string>), the bean keeps it
    detail = unique["additionalProperties"]
    assert detail["testType"] == "Unique" and detail["status"] == "pass" and detail["ruleSource"] == "unpublished"
    assert detail["checkState"] == "passed"
    assert detail["checkKey"] == "sales.orders.order_id::Unique" and detail["measure"] == "0.0"
    assert all(isinstance(v, str) for v in detail.values())
    # no field is one Egeria's QualityAnnotationProperties would drop without saying so
    for body in (unique, required, warn):
        assert set(body) <= ACCEPTED_FIELDS["QualityAnnotationProperties"], set(body) - ACCEPTED_FIELDS[
            "QualityAnnotationProperties"]
    assert {"qualityDimension", "qualityDescription", "qualityScore"} <= ACCEPTED_FIELDS[
        "QualityAnnotationProperties"]


def test_the_dimension_words_are_odcs_and_the_testgen_names_map_as_the_owner_ruled():
    from resource_explorer.surveyors.database.quality.catalogue import EGERIA_DIMENSION

    assert egeria_dimension("Validity") == "accuracy" and egeria_dimension("Recency") == "timeliness"
    assert egeria_dimension("Completeness") == "completeness" and egeria_dimension("Uniqueness") == "uniqueness"
    assert egeria_dimension("Timeliness") == "timeliness"
    assert egeria_dimension("Nonsense") == "" and egeria_dimension("") == ""
    # Validity is accuracy by the owner's ruling of 2026-10-20 (DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §11 item 9)
    assert EGERIA_DIMENSION == {"Validity": "accuracy", "Recency": "timeliness"}
    # the seven RE ports, by the INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md "Egeria dimension" column, with Validity as
    # accuracy (the owner's ruling; the inventory says Conformity)
    catalogue = load_catalogue()
    assert {t: egeria_dimension(catalogue[t].dimension).capitalize() for t in SLICE_1_TEST_TYPES} == {
        "Missing_Pct": "Completeness", "Required": "Completeness", "Required_No_Placeholder": "Completeness",
        "Row_Ct_Pct": "Completeness", "Unique": "Uniqueness", "Future_Date": "Timeliness", "Min_Date": "Accuracy"}
    # and every word is one of Egeria's DataContractQualityDimension values (the Java enum, read from source)
    java = (Path(__file__).resolve().parents[4] / "egeria" / "open-metadata-implementation" / "frameworks" /
            "open-integration-framework" / "src" / "main" / "java" / "org" / "odpi" / "openmetadata" / "frameworks" /
            "integration" / "bitol" / "odcs" / "DataContractQualityDimension.java")
    if java.exists():
        import re

        assert set(re.findall(r'\(\s*"([a-z]+)"\s*,', java.read_text())) == set(ODCS_DIMENSIONS)
    else:
        assert ODCS_DIMENSIONS == {"accuracy", "completeness", "conformity", "consistency", "coverage",
                                   "timeliness", "uniqueness"}


def test_publishing_sends_the_real_create_annotation_signature_and_egerias_fields(registry, entity):
    """The fake binds `DataDiscovery.create_annotation`'s real signature (autospec, spec_set); what is sent is the
    body the bean would keep in full."""
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.annotation_props import publish_annotations

    _profile(registry, entity)
    annotations = _results(registry, entity)["quality_annotations"]
    discovery = create_autospec(DataDiscovery, spec_set=True, instance=True)
    discovery.create_annotation.side_effect = lambda body=None, **_: f"guid-{len(discovery.create_annotation.mock_calls)}"
    guids = publish_annotations(discovery, lambda name: "", annotations, "report-1", "Annotation::PostgreSQL::coco_ods::x")
    assert guids == [f"guid-{i}" for i in range(1, 6)]            # all five evaluated checks, none dropped
    sent = [c.kwargs["body"] for c in discovery.create_annotation.call_args_list]
    # EXACT, in check-key order: empty.d::Missing_Pct, orders.note::Missing_Pct, orders.order_id::Unique,
    # orders.placed::Min_Date, orders.placed::Required
    assert [b["properties"]["qualityDimension"] for b in sent] == [
        "completeness", "completeness", "uniqueness", "accuracy", "completeness"]
    assert [b["properties"].get("qualityScore") for b in sent] == [None, 0, 100, None, 0]
    for body in sent:
        assert body["class"] == "NewElementRequestBody" and body["parentGUID"] == "report-1"
        assert body["parentRelationshipTypeName"] == "ReportedAnnotation" and body["anchorGUID"] == "report-1"
        props = body["properties"]
        assert props["class"] == "QualityAnnotationProperties" and not set(props) - ACCEPTED_FIELDS[props["class"]]
        assert props["qualifiedName"].startswith("Annotation::PostgreSQL::coco_ods::x::quality_check::")
    real = inspect.signature(DataDiscovery.create_annotation)
    assert inspect.signature(discovery.create_annotation) == real.replace(parameters=list(real.parameters.values())[1:])


def test_a_replay_with_the_same_surveyed_at_creates_no_duplicate_quality_annotations(registry, entity):
    """L4: publishing the same run twice (a crash between Egeria recording the write and RE recording success) meets
    the names already there: `publish_annotations` adopts them, so the second publish creates nothing and every
    evaluated check is still one element."""
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.annotation_props import publish_annotations

    _profile(registry, entity)
    annotations = _results(registry, entity)["quality_annotations"]
    held: dict[str, str] = {}
    discovery = create_autospec(DataDiscovery, spec_set=True, instance=True)

    def create(body=None, **_):
        name = body["properties"]["qualifiedName"]
        assert name not in held, f"a duplicate create for {name}"
        held[name] = f"guid-{len(held) + 1}"
        return held[name]

    discovery.create_annotation.side_effect = create
    prefix = "Annotation::PostgreSQL::coco_ods::2026-10-21T08:00:00"
    first = publish_annotations(discovery, lambda name: held.get(name, ""), annotations, "report-1", prefix)
    again = publish_annotations(discovery, lambda name: held.get(name, ""), annotations, "report-1", prefix)
    assert len(held) == len(annotations) == 5 and discovery.create_annotation.call_count == 5
    assert again == first                                  # the same elements, found by name


# ── what a run of propose_quality_checks leaves on the database (design ruling A.1) ─────────────────────────

def test_proposals_leave_one_request_for_action_with_the_count_the_run_and_the_link(registry, entity, monkeypatch):
    from resource_explorer.surveyors.annotation_props import build_annotation_props

    _profile(registry, entity)
    out = run_propose(registry, SLUG, row_ct_pct_threshold=10.0, surveyed_at="2026-10-21T08:00:00+00:00",
                      review_link="https://re.example/next?type=database&worklist=coco_ods")
    [ann] = out["annotations"]
    proposed = out["quality_proposals"]["proposed_ct"]
    assert proposed > 0 and type(ann).__name__ == "RequestForActionAnnotation"
    assert ann.action_requested == f"review {proposed} proposed quality checks in Resource Explorer"
    props = build_annotation_props(ann, "Annotation::q::1")
    assert props["class"] == "RequestForActionProperties"
    assert props["actionRequested"] == f"review {proposed} proposed quality checks in Resource Explorer"
    assert props["additionalProperties"] == {
        "runId": "2026-10-21T08:00:00+00:00", "proposedChecks": str(proposed),
        "columnsProfiled": str(sum(1 for r in registry.query_detail_rows("database_value_profiles", SLUG)
                                   if r["state"] == "measured")),
        "acceptedChecks": "0", "suppressedChecks": "0",
        "notEstablishedChecks": str(out["quality_proposals"]["not_established_ct"]),
        "reviewLink": "https://re.example/next?type=database&worklist=coco_ods",
        # the batch's proposed checks, digested: the request is re-linked only when the same checks are proposed
        "proposedChecksDigest": hashlib.sha256("\n".join(sorted(
            r["check_key"] for r in registry.query_detail_rows("database_quality_proposals", SLUG)
            if r["state"] == "proposed")).encode()).hexdigest()}
    assert set(props) <= ACCEPTED_FIELDS["RequestForActionProperties"]


def test_the_review_link_is_left_out_when_re_has_no_public_url_never_guessed(registry, entity, monkeypatch):
    from resource_explorer.surveyors.database.survey_definition_adapter import _ADAPTER

    monkeypatch.delenv("RE_PUBLIC_URL", raising=False)
    _profile(registry, entity)
    out = _ADAPTER.re_analysis_steps["propose_quality_checks"](entity, registry, scan_surveyed_at="2026-10-21T08:00:00")
    [ann] = out["annotations"]
    assert "reviewLink" not in ann.additional_properties and "localhost" not in ann.explanation
    monkeypatch.setenv("RE_PUBLIC_URL", "https://re.example/")
    out = _ADAPTER.re_analysis_steps["propose_quality_checks"](entity, registry, scan_surveyed_at="2026-10-21T09:00:00")
    assert out["annotations"][0].additional_properties["reviewLink"] == "https://re.example/next?type=database&worklist=coco_ods"


def test_nothing_proposed_leaves_one_measure_in_the_designs_words(registry, entity):
    from resource_explorer.surveyors.annotation_props import build_annotation_props
    from resource_explorer.surveyors.database.quality.propose import build_proposal_annotation

    ann = build_proposal_annotation(SLUG, run_id="2026-10-21T08:00:00", proposed_ct=0, columns_profiled=12)
    assert type(ann).__name__ == "ResourceMeasureAnnotation"
    assert ann.summary == "0 checks proposed · 12 columns profiled · no selection rule matched"
    props = build_annotation_props(ann, "Annotation::q::1")
    assert props["class"] == "ResourceMeasureAnnotationProperties" and set(props) <= ACCEPTED_FIELDS[props["class"]]
    assert props["resourceProperties"]["proposedChecks"] == "0" and props["resourceProperties"]["columnsProfiled"] == "12"
    assert props["additionalProperties"]["resultState"] == "measured"
    # when a rule did leave a check standing, "no selection rule matched" would be false, and is not said
    kept = build_proposal_annotation(SLUG, run_id="r", proposed_ct=0, columns_profiled=12, accepted_ct=3,
                                     suppressed_ct=1, not_established_ct=2)
    assert kept.summary == "0 checks proposed · 12 columns profiled · no new check to propose (3 accepted, " \
        "1 suppressed) · 2 not established"


def test_the_proposal_step_never_returns_an_empty_report(registry, entity):
    """Whatever the profile held, a propose run that stored a batch returns exactly one annotation."""
    _profile(registry, entity)
    for at in ("2026-10-21T08:00:00+00:00", "2026-10-21T09:00:00+00:00"):
        out = run_propose(registry, SLUG, row_ct_pct_threshold=10.0, surveyed_at=at)
        assert len(out["annotations"]) == 1


# ── the definition names no question the catalog has not asked ──────────────────────────────────────────────

def test_two_catalog_questions_name_the_quality_analysis_and_the_definition_links_to_exactly_those():
    """The definition's "Link Element To Scope" blocks come from the question catalog's `answering.analysis_ids`.
    Q-0096 (what checks should this database have) and Q-0097 (which hold, which fail) name `quality_checks`, so the
    DatabaseQualitySurvey document carries exactly their two links, found by `find_candidate_process_guids_by_questions`
    from the document alone (the one Egeria call, resolving the process GUID, is faked)."""
    from unittest.mock import patch

    from resource_explorer.surveyors.question_catalog_reader import get_questions
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    named = {e["id"]: e for e in get_questions(resource_type="database")
             if {"value_profile", "quality_checks"} & set(e["answering"]["analysis_ids"])}
    # Q-0079 names value_profile since 2026-10-22 (review §2; it named the postgres_column_profile step before), and
    # postgres_value_profile is this definition's first step, so the definition now links to it too.
    assert sorted(named) == ["Q-0079", "Q-0096", "Q-0097"]
    assert all(e["stage"] == "Analysis" and not e["retired"] for e in named.values())
    questions = [e["question"] for e in named.values()]
    text = (DEFS / "database-survey-definition-quality.md").read_text()
    assert text.count("## Link Element To Scope") == 3
    for q in questions:
        assert f"### Scope Reference\n{q}\n" in text and "### Target Element\nDatabase Quality Survey\n" in text

    doc = documented_definitions()["DatabaseQualitySurvey"]
    assert sorted(doc.scoped_by) == sorted(questions)
    with patch.object(SurveyDefinitionReader, "_process_guid_cached", return_value="g-quality"):
        found = SurveyDefinitionReader.find_candidate_process_guids_by_questions(
            SurveyDefinitionReader.__new__(SurveyDefinitionReader), questions, "PostgreSQL Database")
    [quality] = [c for c in found if c["qualified_name"] == "GovActionProcess::DatabaseQualitySurvey"]
    assert sorted(quality["matched_questions"]) == sorted(questions)


# ── zero accepted checks: survey-invalid with a sentence, never an empty completed report ───────────────────

def test_with_no_accepted_check_the_step_declines_with_the_sentence_and_runs_nothing(registry, entity):
    _profile(registry, entity)
    conn = FakeConn()
    out = run_quality_checks(entity, registry, _connect(conn), bounds=BOUNDS, surveyed_at="2026-10-20T13:00:00+00:00")
    assert out["skipped_by_design"] == NO_ACCEPTED_CHECKS and NO_ACCEPTED_CHECKS.startswith("no accepted checks")
    assert "quality_annotations" not in out and conn.statements == []
    assert registry.query_detail_rows("database_quality_results", SLUG) == []
    # a rejected check is not an accepted one
    registry.record_quality_verdict(SLUG, "sales.orders.order_id::Unique", "rejected", {})
    assert run_quality_checks(entity, registry, _connect(conn), bounds=BOUNDS)["skipped_by_design"]


def test_the_executor_ends_the_step_skipped_with_the_sentence_and_publishes_nothing(registry, entity, monkeypatch):
    """The step's output is not a result: no publish is attempted for it, and its entry says why."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    _profile(registry, entity)
    published: list = []

    def publisher(entity_, outputs, surveyed_at, registry_):
        published.append(outputs)
        return "report"

    monkeypatch.setattr(db, "database_connection", None, raising=False)
    from resource_explorer.surveyors.database import connection

    monkeypatch.setattr(connection, "database_connection", lambda *a, **k: _connect(FakeConn())())
    ran = SurveyDefinitionExecutor(registry).run_synthetic_step(
        entity_type="database", slug=SLUG, re_analysis_step="run_quality_checks", executes_at="resource-explorer",
        engine_override="resource-explorer", publisher=publisher, db_user="u", db_pwd="p")
    [step] = [s for s in ran["steps"] if s["re_analysis_step"] == "run_quality_checks"]
    assert step["status"] == "skipped_by_design" and step["detail"] == NO_ACCEPTED_CHECKS
    assert published == []


# ── "N of M annotations in Egeria", from what landed ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("landed,word,published", [
    (3, "published to Egeria · 3 of 3 annotations in Egeria", True),
    (2, "publish failed · 2 of 3 annotations in Egeria", False),
    (0, "publish failed · 0 of 3 annotations in Egeria", False),
    (None, "publish failed · annotations in Egeria could not be counted", False),
])
def test_the_run_says_n_of_m_from_the_read_back_and_the_flag_agrees_with_the_words(
        registry, entity, monkeypatch, landed, word, published):
    """L1: the stored flag and the words are derived from the one read-back (`landed`, counted from the report's own
    reportedAnnotations), so a partial landing is never `published: True` beside 'publish failed'."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep

    _profile(registry, entity)
    _accept(registry, "sales.orders.order_id::Unique", "Unique")
    _accept(registry, "sales.orders.placed::Required", "Required")
    _accept(registry, "sales.orders.note::Missing_Pct", "Missing_Pct", baseline_ct=1000, baseline_value_ct=900,
            threshold_value=2, severity="warning")
    from resource_explorer.surveyors.database import connection

    monkeypatch.setattr(connection, "database_connection", lambda *a, **k: _connect(FakeConn(
        checks={("sales", "orders"): {"record_ct": 1000, "measure": _orders_measure}}))())
    monkeypatch.setattr(registry, "has_assigned_egeria_project", lambda *a, **k: True)

    def fake_publish(entity_, outputs, surveyed_at, registry_, publish_out=None):
        built = [a for o in outputs for a in (o.get("quality_annotations") or [])]
        assert len(built) == 3
        publish_out["quality"] = {"built": len(built), "landed": landed, "links": {}, "governance": {}}
        return "report-guid-1"

    monkeypatch.setattr(db._ADAPTER, "publish", fake_publish)
    survey_def = SurveyDefinition(process_guid="p", display_name="Database Quality Survey",
                                  qualified_name=f"GovActionProcess::{QUALITY}", supported_technology_type=None,
                                  steps=[SurveyStep(guid="s", display_name="run", qualified_name="s::run",
                                                    executes_at="resource-explorer",
                                                    re_analysis_step="run_quality_checks")])
    from resource_explorer.surveyors import survey_definition_executor as ex

    monkeypatch.setattr(ex, "person_write_refusal", lambda *a, **k: "", raising=False)
    monkeypatch.setattr("resource_explorer.workflows.curate.person_write_refusal", lambda *a, **k: "")
    result = SurveyDefinitionExecutor(registry)._execute(
        entity_type="database", slug=SLUG, entity=entity, survey_def=survey_def, process_guid="p", process_qn="qn",
        publish="wait", engine_override="resource-explorer", runner_kwargs={"db_user": "u", "db_pwd": "p"})
    assert result["publish_state"] == word
    assert result["published"] is published
    if landed != 3:
        assert any("did not all reach Egeria" in e for e in result["errors"])
    else:
        assert not result["errors"]


def test_links_and_ownership_are_said_in_the_words_and_a_missing_element_is_not_a_failure(
        registry, entity, monkeypatch):
    from resource_explorer.surveyors.database.egeria_database_surveyor import element_link_words

    assert element_link_words(None) == "" and element_link_words({"of": 0}) == ""
    # An element Egeria does not hold yet is NEUTRAL: the checks belong to the report, and the words say so.
    assert element_link_words({"of": 3, "checks": 3, "no_element": 3, "no_element_columns": 3}) == \
        "3 checks on the report · 3 columns not yet cataloged in Egeria, so not linked to them"
    assert element_link_words({"of": 3, "checks": 3, "linked": 2, "no_element": 1, "no_element_columns": 1}) == \
        "3 checks on the report · linked to 2 of 3 elements · 1 column not yet cataloged in Egeria, so not linked to it"
    assert element_link_words({"of": 2, "checks": 2, "no_element": 2, "no_element_tables": 1,
                               "no_element_columns": 1}) == \
        "2 checks on the report · 1 column and 1 table not yet cataloged in Egeria, so not linked to them"
    assert element_link_words({"of": 1, "checks": 1, "no_element": 1, "no_element_tables": 1}) == \
        "1 check on the report · 1 table not yet cataloged in Egeria, so not linked to it"
    # a look-up that could not be made, or a link that failed, is NOT about the catalogue and is still said
    assert element_link_words({"of": 3, "checks": 3, "linked": 1, "already_linked": 1, "not_checked": 1}) == \
        "3 checks on the report · linked to 2 of 3 elements · 1 could not be checked"
    assert element_link_words({"of": 2, "checks": 2, "failed": 2}) == "2 checks on the report · 2 could not be linked"
    # a check with no table to name at all is still a check on the report
    assert element_link_words({"of": 0, "checks": 1}) == "1 check on the report"


# ── the 5-minute per-table budget (owner, 2026-10-21): incomplete, no proposals, never zeros ─────────────────

class _Clock:
    """A fake monotonic clock: it moves only when the fake connection says a statement took that long."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _SlowConn(FakeConn):
    """Each profile statement on the wide table takes `seconds` on the clock, unless Postgres would have cancelled it
    first: it honours the `SET LOCAL statement_timeout` it was given, so a chunk that needs longer than that timeout
    advances the clock by the timeout and raises, as the real server does."""

    def __init__(self, clock, *, seconds, **kw):
        super().__init__(**kw)
        self.clock, self.seconds = clock, seconds
        self.chunks = 0
        self.timeouts: list[int] = []
        self._timeout = None

    def execute_query(self, sql, params=()):
        if sql.startswith("SET LOCAL statement_timeout"):
            self._timeout = int(sql.rsplit("=", 1)[1]) // 1000
            self.timeouts.append(self._timeout)
        return super().execute_query(sql, params)

    def execute_query_isolated(self, sql, params=()):
        if '"sales"."wide"' in sql and "FROM pg_class" not in sql:
            self.chunks += 1
            if self._timeout is not None and self.seconds > self._timeout:
                self.clock.now += self._timeout
                raise TimeoutError("canceling statement due to statement timeout")
            self.clock.now += self.seconds
            return [{"record_ct": 1000, **{alias: 1 for alias in __import__("re").findall(r"AS (c\d+__[a-z_]+)", sql)}}]
        return super().execute_query_isolated(sql, params)


def _wide_registry(registry, columns=450):
    """`sales.wide`: 450 integer columns, so the profile reads it as three chunks of at most COLUMNS_PER_QUERY (150)."""
    tables = registry.query_detail_rows("database_tables", SLUG)
    cols = registry.query_detail_rows("database_columns", SLUG)
    tables.append({"schema_name": "sales", "table_name": "wide", "table_type": "BASE TABLE", "row_count": 1000})
    cols += [{"schema_name": "sales", "table_name": "wide", "column_name": f"c{i}", "ordinal_position": i,
              "data_type": "integer", "base_type": "integer"} for i in range(columns)]
    registry.write_detail_rows("database_tables", SLUG, "2026-10-20T09:30:00+00:00", rows=tables)
    registry.write_detail_rows("database_columns", SLUG, "2026-10-20T09:30:00+00:00", rows=cols)


def _wide_profile(registry, entity, conn, clock, bounds):
    return run_value_profile(entity, registry, _connect(conn), bounds=bounds, surveyed_at=PROFILED_AT,
                             ran_as="surveyor", clock=clock)


def test_a_table_that_runs_past_five_minutes_is_incomplete_with_every_count_not_measured(registry, entity):
    _wide_registry(registry)
    clock = _Clock()
    conn = _SlowConn(clock, seconds=130, profile={k: v for k, v in PROFILE.items()}, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=600, table_budget_seconds=300)
    out = _wide_profile(registry, entity, conn, clock, bounds)
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    # Chunks 1 and 2 took 260 s and finished; the third started with 40 s left, ran into it and was cancelled: the
    # whole table is incomplete, including the 300 columns whose counts had been read.
    assert len(wide) == 450
    assert {r["state"] for r in wide} == {"not_measured"}
    assert {r["reason"] for r in wide} == {"profile incomplete · exceeded 5 min · counts not measured"}
    assert all(r[m] is None for r in wide for m in ("record_ct", "value_ct", "distinct_value_ct", "null_value_ct"))
    assert incomplete_reason(300) == "profile incomplete · exceeded 5 min · counts not measured"
    [outcome] = [o for o in out["value_profile"]["tables_not_profiled"] if o["table"] == "sales.wide"]
    assert outcome["state"] == "not_measured" and "exceeded 5 min" in outcome["reason"]
    assert out["value_profile"]["bounds"]["table_budget_seconds"] == 300
    # the other tables are unaffected: the budget is per table
    orders = _rows(registry, "database_value_profiles")["sales", "orders", "order_id"]
    assert orders["state"] == "measured" and orders["record_ct"] == 1000


def test_the_budget_is_the_total_per_table_not_a_timeout_on_each_chunk(registry, entity):
    """Three chunks at 130 s each: each is far inside the 600 s statement timeout, yet the table exceeds its 300 s
    budget. Every chunk's statement timeout is capped at what is left of the table's budget (300, 170, 40 s)."""
    _wide_registry(registry)
    clock = _Clock()
    conn = _SlowConn(clock, seconds=130, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=600, table_budget_seconds=300)
    _wide_profile(registry, entity, conn, clock, bounds)
    wide_timeouts = conn.timeouts[-3:]
    assert conn.chunks == 3 and wide_timeouts == [300, 170, 40]
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    assert {r["state"] for r in wide} == {"not_measured"}
    assert {r["reason"] for r in wide} == {"profile incomplete · exceeded 5 min · counts not measured"}


def test_a_table_whose_budget_is_spent_between_chunks_starts_no_more_and_is_incomplete(registry, entity):
    _wide_registry(registry)
    clock = _Clock()
    conn = _SlowConn(clock, seconds=150, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=600, table_budget_seconds=300)
    _wide_profile(registry, entity, conn, clock, bounds)
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    assert {r["reason"] for r in wide} == {"profile incomplete · exceeded 5 min · counts not measured"}
    assert conn.chunks == 2                        # exactly 300 s were spent: the third chunk was never started


def test_a_chunk_that_takes_100_seconds_inside_the_budget_is_measured(registry, entity):
    """H2: the 5-minute budget is the EFFECTIVE bound. Slice 1's fixed 60 s statement timeout would have cancelled a
    100 s chunk with 200 s of the table's budget left; now each chunk may take what is left of the budget."""
    _wide_registry(registry, columns=COLUMNS_PER_QUERY)   # one chunk
    clock = _Clock()
    conn = _SlowConn(clock, seconds=100, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=60, table_budget_seconds=300)      # the old 60 s setting present
    _wide_profile(registry, entity, conn, clock, bounds)
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    assert len(wide) == COLUMNS_PER_QUERY and {r["state"] for r in wide} == {"measured"}
    # the chunk took 100 s (the clock then also moves for the exact percentiles, step 6, a statement of their own)
    assert wide[0]["record_ct"] == 1000 and clock.now >= 100
    assert conn.timeouts[0] == 300                    # the chunk was given the whole budget, not 60 s


def test_a_privilege_error_after_the_budget_is_spent_is_still_not_permitted_not_incomplete(registry, entity):
    """L2: the exception is classified before the table is labelled 'profile incomplete'."""
    from tests.test_value_profile_quality_checks import PgError

    _wide_registry(registry, columns=COLUMNS_PER_QUERY)
    clock = _Clock()

    class Denied(_SlowConn):
        def execute_query_isolated(self, sql, params=()):
            if '"sales"."wide"' in sql and "FROM pg_class" not in sql:
                self.clock.now += 400                # the budget is gone when Postgres refuses
                raise PgError("permission denied for table wide", "42501")
            return super().execute_query_isolated(sql, params)

    conn = Denied(clock, seconds=1, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=60, table_budget_seconds=300)
    out = _wide_profile(registry, entity, conn, clock, bounds)
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    assert {r["state"] for r in wide} == {"not_permitted"}
    assert all("permission denied" in r["reason"] and "incomplete" not in r["reason"] for r in wide)
    [outcome] = [o for o in out["value_profile"]["tables_not_profiled"] if o["table"] == "sales.wide"]
    assert outcome["state"] == "not_permitted"


def test_a_table_that_finishes_inside_its_budget_is_measured(registry, entity):
    _wide_registry(registry)
    clock = _Clock()
    conn = _SlowConn(clock, seconds=60, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=60, table_budget_seconds=300)
    _wide_profile(registry, entity, conn, clock, bounds)
    wide = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["table_name"] == "wide"]
    assert len(wide) == 450 and {r["state"] for r in wide} == {"measured"} and wide[0]["record_ct"] == 1000


def test_an_incomplete_table_gets_no_proposals_and_no_zero_ever(registry, entity):
    _wide_registry(registry)
    clock = _Clock()
    conn = _SlowConn(clock, seconds=200, profile=PROFILE, reltuples={("sales", "wide"): 1000})
    bounds = ProfileBounds(statement_timeout_seconds=600, table_budget_seconds=300)
    _wide_profile(registry, entity, conn, clock, bounds)
    _propose(registry)
    rows = [r for r in registry.query_detail_rows("database_quality_proposals", SLUG) if r["table_name"] == "wide"]
    assert rows, "the table is accounted for"
    assert {r["state"] for r in rows} == {"not_established"}          # said, not proposed: never a proposal
    assert not any(r["state"] == "proposed" for r in rows)
    assert all("not profiled (not_measured" in r["reason"] and "exceeded 5 min" in r["reason"] for r in rows)
    # and the rest of the database still proposes
    assert any(r["state"] == "proposed" for r in registry.query_detail_rows("database_quality_proposals", SLUG))


def test_the_budget_is_a_setting_with_the_owner_s_five_minutes_and_there_is_no_size_cap():
    from resource_explorer.config import ValueProfileSettings

    assert ValueProfileSettings().table_budget_seconds == 300
    assert ProfileBounds(1).table_budget_seconds == 300
    assert "max_table_rows" not in ValueProfileSettings.model_fields        # L5: no size cap
    assert not hasattr(ProfileBounds(1), "max_table_rows")


def test_profile_never_sleeps_on_its_own_clock(registry, entity, monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: pytest.fail("the profile must never sleep"))
    _profile(registry, entity)
