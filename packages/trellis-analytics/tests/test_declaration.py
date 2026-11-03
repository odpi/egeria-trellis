"""The declaration (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE §1/§2/§4): what it accepts, what it
refuses and in what words, how a step manifest carries it, and that every library step declares one in full."""
import importlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from trellis_analytics.declaration import (
    ABSENCE_STATES,
    CONFIDENCE_FORMS,
    FULL_FIELDS,
    RESULT_KINDS,
    Declaration,
    refusal,
)
from trellis_analytics.manifest import StepManifest

STEPS = Path(__file__).resolve().parents[1] / "examples" / "steps"
LIBRARY_STEPS = ("database_staleness", "hub_tables", "change_rates", "statistics_freshness")

ABSENCE = {s: f"{s.replace('_', ' ')}: a sentence" for s in ABSENCE_STATES}
MEASURE = {"name": "s", "unit": "tables", "as_of": "asOf", "value_field": "count"}
EXTENT = {"name": "rows", "fields": ["scope", "count"], "rows": 10, "order": "by count, most first",
          "truncation": "{shown} of {total} tables shown", "token_cost": 200, "citation": "{analysis} · {scope}"}


def minimum(**over):
    return {"result_kind": "measure", "measures": [MEASURE], "absence": ABSENCE, **over}


def full(**over):
    return minimum(**{"extents": [EXTENT], "confidence": {"form": "measured"}, "answers": ["Q-0001"],
                      "scope": "resource", **over})


def refused(body) -> str:
    with pytest.raises(ValidationError) as exc:
        Declaration.model_validate(body)
    return refusal(exc.value)


def test_the_vocabularies_are_the_design_notes():
    assert RESULT_KINDS == ("measure", "series", "distribution", "list", "graph", "finding", "proposal", "table")
    assert ABSENCE_STATES == ("not_measured", "not_permitted", "not_applicable", "measured_nothing")
    assert CONFIDENCE_FORMS == ("measured", "estimated", "inferred", "judged", "generated")


def test_the_minimum_runs_and_says_what_a_full_declaration_still_needs():
    d = Declaration.model_validate(minimum())
    assert d.missing() == list(FULL_FIELDS) and not d.is_complete()
    assert Declaration.model_validate(full()).missing() == []


def test_answers_empty_is_a_declaration_and_absent_is_not():
    """[] says "answers no question, by design"; None says nobody has said yet."""
    assert "answers" not in Declaration.model_validate(full(answers=[])).missing()
    assert "answers" in Declaration.model_validate({k: v for k, v in full().items() if k != "answers"}).missing()


@pytest.mark.parametrize("body, words", [
    (minimum(result_kind="chart"), "result_kind 'chart' is not one of the 8 kinds"),
    (minimum(result_kind="table"), "result_kind 'table' declares no result_kind_note"),
    (minimum(measures=[]), "declares no measures"),
    (minimum(measures=[{**MEASURE, "unit": " "}]), "measure 's' declares no unit"),
    (minimum(measures=[{**MEASURE, "as_of": ""}]), "measure 's' declares no as_of"),
    (minimum(measures=[MEASURE, MEASURE]), "measure s is declared more than once"),
    (minimum(measures=[{**MEASURE, "annotation": {"type": ""}}]), "annotation type is empty"),
    (minimum(absence={**ABSENCE, "not_permitted": ""}), "absence sentence for not_permitted is empty"),
    (minimum(absence={k: v for k, v in ABSENCE.items() if k != "measured_nothing"}),
     "absence.measured_nothing is required"),
    (minimum(absence={**ABSENCE, "never_run": "x"}), "absence.never_run is not a declaration field"),
    (minimum(colour="blue"), "colour is not a declaration field"),
    (minimum(scope="table"), "scope 'table' is not one of the levels"),
    (minimum(extents=[{**EXTENT, "truncation": "some rows shown"}]), "lacks {shown}, {total}"),
    (minimum(extents=[{**EXTENT, "fields": []}]), "extent 'rows' declares no fields"),
    (minimum(extents=[{**EXTENT, "token_cost": 0}]), "extent 'rows' declares token_cost 0"),
    (minimum(confidence={"form": "guessed"}), "confidence form 'guessed' is not one of the five forms"),
    (minimum(confidence={"form": "inferred", "score": "confidence", "calibrated": False}),
     "confidence form 'inferred' declares no basis"),
    (minimum(confidence={"form": "inferred", "basis": "rules", "method": "the rules that matched"}), "does not say whether its score is calibrated"),
    (minimum(confidence={"form": "estimated"}), "confidence form 'estimated' declares no basis and no method"),
    (minimum(confidence={"form": "measured", "score": "confidence"}), "confidence form 'measured' declares a score"),
    (minimum(confidence={"form": "estimated", "method": "a sample", "calibrated": True}),
     "only an inferred score is calibrated or not"),
    (minimum(confidence={"form": "generated", "method": "a model"},
             measures=[{**MEASURE, "annotation": {"type": "ResourceMeasureAnnotation"}}]),
     "a generated answer is not published"),
])
def test_a_refusal_names_the_field_and_what_was_refused(body, words):
    assert words in refused(body)


def test_a_refusal_reads_as_sentences_not_as_pydantic_framing():
    words = refused(minimum(result_kind="chart"))
    assert "validation error" not in words and "Value error" not in words
    assert words.startswith("result_kind ")


def test_a_basis_that_is_a_field_path_must_come_with_words():
    refusal = refused(minimum(confidence={"form": "estimated", "basis": "tables.row_count_is_estimate"}))
    assert "names a result field and no method" in refusal and "tables.row_count_is_estimate" in refusal
    ok = Declaration.model_validate(full(confidence={"form": "estimated", "basis": "tables.row_count_is_estimate",
                                                     "method": "PostgreSQL's row-count estimate"}))
    assert ok.confidence.method


def test_an_estimate_may_declare_its_method_instead_of_a_field():
    d = Declaration.model_validate(full(confidence={"form": "estimated", "method": "n_live_tup, an estimate"}))
    assert d.confidence.basis == "" and d.confidence.method


# ── the declaration on a step manifest ──────────────────────────────────────

BASE = dict(name="s", function="a.b", source="ods", version="1", resource_types=["database"], questions=["Q-0001"])


def test_a_step_declaration_takes_its_answers_from_the_manifests_questions():
    m = StepManifest(**BASE, declaration=minimum())
    assert m.declaration.answers == ["Q-0001"]


def test_a_step_declaration_may_not_name_other_questions():
    with pytest.raises(ValueError, match="one list of questions, not two"):
        StepManifest(**BASE, declaration=minimum(answers=["Q-0002"]))


def test_every_measure_the_step_reports_is_declared_and_no_other():
    with pytest.raises(ValueError, match="declaration gives no unit or as-of for measure o"):
        StepManifest(**BASE, measures=["s", "o"], declaration=minimum())
    with pytest.raises(ValueError, match="declares measure x, which the step does not report"):
        StepManifest(**BASE, declaration=minimum(measures=[MEASURE, {**MEASURE, "name": "x"}]))


def test_a_measures_type_is_one_fact_not_two_declarations():
    """F10 (2026-10-29): the manifest's type and the measure's `annotation` derive from one another."""
    two = [MEASURE, {**MEASURE, "name": "p"}]
    kw = dict(measures=["s", "p"], measure_annotation_types={"p": "ClassificationAnnotation"})
    # the manifest names the type, the measure does not: the measure's annotation is filled from the manifest
    m = StepManifest(**BASE, **kw, declaration=minimum(measures=two))
    assert {x.name: x.annotation.type for x in m.declaration.measures if x.annotation} == {"p": "ClassificationAnnotation"}
    # the measure names the type, the manifest does not: the manifest follows the measure
    m = StepManifest(**BASE, measures=["s", "p"], declaration=minimum(measures=[
        MEASURE, {**MEASURE, "name": "p", "annotation": {"type": "ClassificationAnnotation"}}]))
    assert m.annotation_type_for("p") == "ClassificationAnnotation" and m.annotation_type_for("s") == "ResourceMeasureAnnotation"
    # the primary measure's type moves only the primary: the other measure keeps what it inherited
    m = StepManifest(**BASE, measures=["s", "p"], declaration=minimum(measures=[
        {**MEASURE, "annotation": {"type": "ResourceProfileAnnotation"}}, {**MEASURE, "name": "p"}]))
    assert m.annotation_type == "ResourceProfileAnnotation" and m.annotation_type_for("p") == "ResourceMeasureAnnotation"
    # both written and agreeing: fine
    StepManifest(**BASE, **kw, declaration=minimum(measures=[
        MEASURE, {**MEASURE, "name": "p", "annotation": {"type": "ClassificationAnnotation"}}]))


def test_a_contradiction_between_the_two_names_both_fields():
    kw = dict(measures=["s", "p"], measure_annotation_types={"p": "ClassificationAnnotation"})
    with pytest.raises(ValueError) as exc:
        StepManifest(**BASE, **kw, declaration=minimum(measures=[
            MEASURE, {**MEASURE, "name": "p", "annotation": {"type": "DataClassAnnotation"}}]))
    msg = str(exc.value)
    assert "declaration.measures[p].annotation.type" in msg and "measure_annotation_types['p']" in msg
    with pytest.raises(ValueError, match=r"manifest's annotation_type"):
        StepManifest(**BASE, annotation_type="ResourceMeasureAnnotation", declaration=minimum(measures=[
            {**MEASURE, "annotation": {"type": "ResourceProfileAnnotation"}}]))


def test_a_step_that_names_its_default_type_reads_as_that_type_not_generic():
    """F11's cause: a library step that wrote annotation_type and no measure annotation showed 'generic'."""
    m = StepManifest(**BASE, annotation_type="ResourceMeasureAnnotation", declaration=minimum())
    assert m.declaration.measures[0].annotation.type == "ResourceMeasureAnnotation"
    assert StepManifest(**BASE, declaration=minimum()).declaration.measures[0].annotation is None


# ── questions a step brings (owner, 2026-10-29: a namespaced id in the manifest) ────────────────────────────

def test_a_step_may_declare_a_new_question_under_its_own_prefix():
    m = StepManifest(**BASE, question_defs=[{"id": "s:Q1", "question": "  Which tables\n churn?  "}])
    assert m.question_defs[0].question == "Which tables churn?"
    assert m.questions == ["Q-0001", "s:Q1"]            # the one list of what the step answers


def test_a_step_cannot_take_an_id_outside_its_prefix_or_declare_one_twice():
    with pytest.raises(ValueError, match="under the step's own prefix"):
        StepManifest(**BASE, question_defs=[{"id": "Q-0200", "question": "x?"}])
    with pytest.raises(ValueError, match="under the step's own prefix"):
        StepManifest(**BASE, question_defs=[{"id": "other:Q1", "question": "x?"}])
    with pytest.raises(ValueError, match="declared twice"):
        StepManifest(**BASE, question_defs=[{"id": "s:Q1", "question": "a?"}, {"id": "s:Q1", "question": "b?"}])


def test_a_declared_question_is_not_warned_about_as_unknown():
    from trellis_analytics.manifest import question_warnings

    m = StepManifest(**BASE, question_defs=[{"id": "s:Q1", "question": "x?"}])
    assert question_warnings(m, {"Q-0001"}) == []


def test_a_manifest_without_a_declaration_still_loads():
    assert StepManifest(**BASE).declaration is None


@pytest.mark.parametrize("name", LIBRARY_STEPS)
def test_every_library_step_declares_in_full(monkeypatch, name):
    monkeypatch.syspath_prepend(str(STEPS / name / "src"))
    for mod in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        monkeypatch.delitem(sys.modules, mod)
    m = importlib.import_module(f"{name}.manifest").MANIFEST
    d = m.declaration
    assert d is not None and d.missing() == [], name
    assert [x.name for x in d.measures] == m.measure_names()
    assert d.answers == m.questions


# ── the owner's answers of 2026-10-28: per-measure confidence, not built ────

def test_a_measure_may_carry_its_own_confidence_and_the_headline_is_the_weakest():
    body = minimum(measures=[{**MEASURE, "confidence": {"form": "measured"}},
                             {**MEASURE, "name": "e", "confidence": {"form": "estimated", "method": "a sample"}}])
    d = Declaration.model_validate(body)
    assert d.confidence.form == "estimated"
    assert d.confidence_of(d.measures[0]).form == "measured"


def test_a_measure_without_its_own_confidence_inherits_the_declarations():
    d = Declaration.model_validate(minimum(confidence={"form": "estimated", "method": "a sample"},
                                           measures=[{**MEASURE, "confidence": {"form": "measured"}},
                                                     {**MEASURE, "name": "e"}]))
    assert d.confidence_of(d.measures[1]).form == "estimated"


@pytest.mark.parametrize("body, words", [
    (minimum(confidence={"form": "measured"},
             measures=[{**MEASURE, "confidence": {"form": "estimated", "method": "a sample"}}]),
     "confidence form 'measured' is not the weakest of its measures' ('estimated')"),
    (minimum(measures=[{**MEASURE, "confidence": {"form": "measured"}}, {**MEASURE, "name": "e"}]),
     "the measures without one have no confidence to inherit"),
    (minimum(measures=[{**MEASURE, "confidence": {"form": "generated", "method": "a model"},
                        "annotation": {"type": "ResourceMeasureAnnotation"}}]),
     "a generated answer is not published"),
])
def test_per_measure_confidence_refusals_name_what_was_refused(body, words):
    assert words in refused(body)


def test_built_is_the_default_and_a_planned_analysis_says_so():
    assert Declaration.model_validate(minimum()).built is True
    assert Declaration.model_validate(minimum(built=False)).built is False


# ── sort keys (project owner, 2026-10-28): the order said once ─────────────

def _ext(**over):
    return Declaration.model_validate(minimum(extents=[{**EXTENT, **over}])).extents[0]


def test_sort_keys_derive_the_order_words():
    x = _ext(order="", sort=["count desc", "scope asc"])
    assert x.sort_keys() == [("count", True), ("scope", False)]
    assert x.order_words() == "by count, descending, then scope, ascending"
    assert _ext().order_words() == "by count, most first"


@pytest.mark.parametrize("over, words", [
    ({"sort": ["count desc"]}, "declares both sort and order: the order is said in one place"),
    ({"order": "", "sort": []}, "declares no order: declare sort keys, or the order in words"),
    ({"order": "", "sort": ["count downwards"]}, "sort key 'count downwards' is not '<field> asc' or '<field> desc'"),
    ({"order": "", "sort": ["rows desc"]}, "sorts by 'rows', which is not one of its fields"),
])
def test_sort_refusals_name_what_was_refused(over, words):
    assert words in refused(minimum(extents=[{**EXTENT, **over}]))


# ── capped, basis and categories (brief 2026-10-30, Part A) ────────────────────────────────────────────────────────

def _measure(**over):
    return {**MEASURE, **over}


def test_a_basis_without_words_is_refused_and_the_refusal_names_the_measure():
    said = refused(minimum(measures=[_measure(basis={"denominator_field": "column_count", "denominator_label": " "})]))
    assert "declares no words for the whole" in said


def test_a_basis_on_a_fraction_is_refused_because_the_denominator_is_in_the_unit():
    said = refused(minimum(measures=[_measure(unit="fraction of rows (0-1)",
                                              basis={"denominator_field": "n", "denominator_label": "rows"})]))
    assert "measure 's'" in said and "the denominator is in the unit" in said


def test_a_basis_on_a_count_and_a_capped_field_are_accepted_and_read_back():
    d = Declaration.model_validate(minimum(measures=[_measure(
        unit="columns", basis={"denominator_field": "column_count", "denominator_label": "columns"},
        capped_field="paging.capped")]))
    assert d.measures[0].basis.denominator_label == "columns" and d.measures[0].capped_field == "paging.capped"


def test_overlapping_categories_with_a_stated_total_are_refused():
    said = refused(minimum(result_kind="graph", categories={"exclusive": False, "sums_to": "total"}))
    assert "overlapping categories cannot sum to a total" in said


def test_a_graph_or_distribution_without_categories_is_refused_with_the_sentence_naming_what_to_add():
    for kind in ("graph", "distribution"):
        said = refused(minimum(result_kind=kind))
        assert f"result_kind '{kind}' declares no categories: add categories with exclusive" in said


def test_categories_on_another_kind_are_refused():
    assert "categories belong to graph and distribution" in refused(minimum(categories={"exclusive": True}))


# ── declared words for the non-numeric kinds (Part B) ──────────────────────────────────────────────────────────────

FINDING = {"what_field": "summary", "where_field": "scope"}


def test_a_finding_kind_without_what_field_is_refused_with_the_sentence():
    assert "declares no finding block: say what_field" in refused(
        minimum(result_kind="finding", measures=[]))
    assert "a finding declares no what_field" in refused(
        minimum(result_kind="finding", measures=[], finding={"what_field": " ", "where_field": "scope"}))


def test_the_four_non_numeric_kinds_need_no_measures_and_the_numeric_ones_still_do():
    blocks = {"finding": ("finding", FINDING),
              "proposal": ("proposal", {"candidate_field": "c", "decided_by": "curator"}),
              "list": ("listing", {"item_fields": ["a"], "order": "largest first", "key_field": "a"}),
              "table": ("table", {"columns": ["a", "b"], "key_fields": ["a"]})}
    for kind, (attr, block) in blocks.items():
        extra = {"result_kind_note": "none fits"} if kind == "table" else {}
        assert Declaration.model_validate(minimum(result_kind=kind, measures=[], **{attr: block}, **extra))
    assert "declares no measures" in refused(minimum(measures=[]))


def test_a_block_on_a_kind_it_does_not_belong_to_is_refused():
    assert "declares a finding block and result_kind 'measure'" in refused(minimum(finding=FINDING))


def test_a_finding_with_a_severity_field_lists_its_severities():
    assert "list the words it may hold" in refused(minimum(
        result_kind="finding", finding={**FINDING, "severity_field": "sev"}))


def test_a_proposal_starts_as_proposed():
    assert "first decision state is not 'proposed'" in refused(minimum(
        result_kind="proposal", proposal={"candidate_field": "c", "decided_by": "curator",
                                          "decision_states": ["accepted"]}))


def _load_step(name):
    mp = pytest.MonkeyPatch()
    mp.syspath_prepend(str(STEPS / name / "src"))
    for mod in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        mp.delitem(sys.modules, mod)
    try:
        return importlib.import_module(f"{name}.manifest").MANIFEST
    finally:
        mp.undo()


def test_automatic_is_refused_for_a_proposal_whatever_the_annotation_type():
    hub = _load_step("hub_tables")
    for atype in ("ClassificationAnnotation", "ResourceMeasureAnnotation"):
        body = hub.model_dump()
        body["declaration"]["proposal"]["decided_by"] = "automatic"
        if atype != "ClassificationAnnotation":
            body["annotation_type"] = atype
            body["measure_annotation_types"] = {}
            body["declaration"]["measures"][1]["annotation"] = None
        with pytest.raises(ValidationError) as exc:
            StepManifest.model_validate(body)
        assert "proposals are decided by a person in Curate (ruling 2026-10-15); no automatic path exists" in refusal(exc.value)
    body = hub.model_dump()
    assert body["declaration"]["proposal"]["decided_by"] == "curator" and StepManifest.model_validate(body)
