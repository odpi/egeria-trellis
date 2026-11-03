"""The rules the question-catalog generator gained after REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22 §5, each
proved by making it fail on purpose, plus the end-to-end counts of the CSV as committed.

Every refusal and warning here is exercised on a row that SHOULD trip it and on a clean row that should not, so a
check that quietly stopped firing (green because it never ran) fails a test instead of passing one.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"
DR_SCRIPT = ROOT / "scripts" / "csv_to_dr_egeria_questions.py"
CSV = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
YAML_PATH = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load(SCRIPT, "csv_to_question_catalog_yaml_review_rules")


@pytest.fixture(scope="module")
def dr_gen():
    return _load(DR_SCRIPT, "csv_to_dr_egeria_questions_review_rules")


@pytest.fixture(scope="module")
def header() -> list[str]:
    with open(CSV, newline="", encoding="utf-8") as f:
        return csv.DictReader(f).fieldnames


def _row(header, qid="Q-0001", **over) -> dict:
    row = {c: "" for c in header}
    row.update({
        "Question ID": qid, "Question": f"Question text for {qid}?", "Funnel Stage": "Scouting",
        "Answering Analysis": "N/A — direct field (test)", "Resource Types": "repo", "Level": "resource",
        "Steward": "X", "Purposes": "Explore",
    })
    row.update(over)
    return row


# ---------------------------------------------------------------- rule 1: a bare step name fails the build
class TestABareStepNameFailsTheBuild:
    def test_a_note_naming_a_step_is_refused(self, gen, header):
        row = _row(header, **{"Resource Types": "database", "Answering Analysis":
                              "PARTIAL: the postgres_column_profile step reads pg_stats; data_class_match consumes it"})
        with pytest.raises(ValueError, match="postgres_column_profile"):
            gen.generate([row], known_purposes=["Explore"])

    def test_the_refusal_says_it_is_a_step_not_a_catalogue_id(self, gen, header):
        row = _row(header, **{"Resource Types": "database", "Answering Analysis":
                              "PARTIAL: schema_inventory covers part; postgres_nested_columns does the rest"})
        with pytest.raises(ValueError, match="step, not an analysis-catalog id"):
            gen.generate([row], known_purposes=["Explore"])

    def test_a_step_that_is_also_a_catalogue_id_passes(self, gen, header):
        # sql_analysis is both a step and (since 2026-10-22) a catalogue entry: naming it names an analysis.
        assert "sql_analysis" in gen.ALL_CATALOGUE_IDS
        row = _row(header, **{"Resource Types": "database", "Answering Analysis": "PARTIAL: sql_analysis parses view DDL"})
        out = yaml.safe_load(gen.generate([row], known_purposes=["Explore"]))
        assert out["database_questions"][0]["answering"]["kind"] == "partial"

    def test_a_step_inside_a_parenthetical_gloss_passes(self, gen, header):
        row = _row(header, **{"Resource Types": "database", "Answering Analysis":
                              "nested_column_profile (design §5.4, postgres_nested_columns — samples every column)"})
        out = yaml.safe_load(gen.generate([row], known_purposes=["Explore"]))
        assert out["database_questions"][0]["answering"]["kind"] == "analysis"

    def test_the_old_q0079_and_q0071_wording_would_have_failed(self, gen, header):
        for text in (
            "PARTIAL: the postgres_column_profile step (Phase 1 slice 10) reads pg_stats; data_class_match consumes it",
            "PARTIAL: the postgres_nested_columns survey step parses columns, but nothing resolves its result",
        ):
            row = _row(header, **{"Resource Types": "database", "Answering Analysis": text})
            with pytest.raises(ValueError):
                gen.generate([row], known_purposes=["Explore"])

    def test_the_committed_csv_names_no_bare_step(self, gen):
        # The CSV generates (this would raise otherwise); asserted directly so the failure names the rule.
        with open(CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                assert gen._bare_step_names(r["Answering Analysis"]) == [], r["Question ID"]


# ---------------------------------------------------------------- rule 2: a kind with no adapter fails the build
class TestAKindWithNoAdapterFailsTheBuild:
    @pytest.mark.parametrize("types", ["dataset", "model", "database;dataset", "repo;model"])
    def test_a_named_kind_with_no_adapter_is_refused(self, gen, header, types):
        with pytest.raises(ValueError, match="no adapter"):
            gen.generate([_row(header, **{"Resource Types": types})], known_purposes=["Explore"])

    def test_the_refusal_names_the_question(self, gen, header):
        with pytest.raises(ValueError, match="Q-0042"):
            gen.generate([_row(header, qid="Q-0042", **{"Resource Types": "dataset"})], known_purposes=["Explore"])

    def test_star_expands_to_the_kinds_that_have_an_adapter_only(self, gen, header):
        out = yaml.safe_load(gen.generate([_row(header, **{"Resource Types": "*"})], known_purposes=["Explore"]))
        assert [k for k in out if k.endswith("_questions")] == ["repo_questions", "database_questions", "filesystem_questions"]

    def test_a_kind_with_an_adapter_passes(self, gen, header):
        for t in ("repo", "database", "filesystem", "database;filesystem"):
            gen.generate([_row(header, **{"Resource Types": t})], known_purposes=["Explore"])

    def test_the_vocabulary_still_names_dataset_and_model(self):
        # Left in resource_types.py on purpose: its own tests and the generator's refusal read the names.
        from resource_explorer.resource_types import RESOURCE_TYPES, SURVEYED_RESOURCE_TYPES, parse_resource_types

        assert {"dataset", "model"} <= set(RESOURCE_TYPES)
        assert parse_resource_types("*") == list(SURVEYED_RESOURCE_TYPES)


# ---------------------------------------------------------------- rule 3: stage versus intent warns
class TestStageAgainstIntentWarns:
    CATALOG = {"database_analyses": [
        {"id": "schema_inventory", "intent": "scouting"},
        {"id": "schema_conventions", "intent": "analysis"},
        {"id": "preliminary_fit", "intent": "discovery"},
        {"id": "egeria_db_survey", "intent": "curate"},
    ]}

    @staticmethod
    def _entry(qid, stage, ids):
        return {"id": qid, "stage": stage, "answering": {"analysis_ids": ids}}

    def test_a_mismatch_warns_naming_both_stage_and_intent(self, gen):
        w = gen.stage_intent_warnings({"database": [self._entry("Q-0078", "Analysis", ["schema_inventory"])]}, self.CATALOG)
        assert len(w) == 1
        assert "Q-0078" in w[0] and "'Analysis'" in w[0] and "'scouting'" in w[0] and "schema_inventory" in w[0]

    def test_a_clean_row_does_not_warn(self, gen):
        assert gen.stage_intent_warnings(
            {"database": [self._entry("Q-0061", "Scouting", ["schema_inventory"])]}, self.CATALOG) == []

    def test_a_slash_stage_that_includes_the_intent_does_not_warn(self, gen):
        assert gen.stage_intent_warnings(
            {"database": [self._entry("Q-0082", "Discovery/Analysis", ["schema_conventions"])]}, self.CATALOG) == []

    def test_an_intent_with_no_funnel_stage_is_left_alone(self, gen):
        assert gen.stage_intent_warnings(
            {"database": [self._entry("Q-9999", "Scouting", ["egeria_db_survey"])]}, self.CATALOG) == []

    def test_the_same_pair_is_said_once_across_kinds(self, gen):
        cat = {"repo_analyses": [{"id": "a_b", "intent": "analysis"}], "database_analyses": [{"id": "a_b", "intent": "analysis"}]}
        entries = {t: [self._entry("Q-0001", "Scouting", ["a_b"])] for t in ("repo", "database")}
        assert len(gen.stage_intent_warnings(entries, cat)) == 1

    def test_generate_reports_it_in_the_warnings_list(self, gen, header):
        row = _row(header, **{"Resource Types": "database", "Funnel Stage": "Analysis", "Answering Analysis": "schema_inventory"})
        said: list[str] = []
        gen.generate([row], known_purposes=["Explore"], warnings=said)
        assert any("schema_inventory" in w and "'scouting'" in w for w in said)
        said.clear()
        gen.generate([_row(header, **{"Resource Types": "database", "Answering Analysis": "schema_inventory"})],
                     known_purposes=["Explore"], warnings=said)
        assert not any("does not include the intent" in w for w in said)


# ---------------------------------------------------------------- rule 4: an undeclared orphan warns
class TestAnOrphanAnalysisWarns:
    CATALOG = {"database_analyses": [
        {"id": "named_one"},
        {"id": "orphan_one"},
        {"id": "infra_one", "answers_questions": False},
    ]}

    def test_an_undeclared_orphan_warns_by_name(self, gen):
        by_type = {"database": [{"answering": {"analysis_ids": ["named_one"]}}]}
        w = gen.orphan_warnings(by_type, self.CATALOG)
        assert len(w) == 1 and "orphan_one" in w[0] and "answers_questions: false" in w[0]

    def test_an_infrastructure_entry_declaring_false_does_not_warn(self, gen):
        by_type = {"database": [{"answering": {"analysis_ids": ["named_one", "orphan_one"]}}]}
        assert gen.orphan_warnings(by_type, self.CATALOG) == []
        by_type = {"database": []}
        assert not any("infra_one" in w for w in gen.orphan_warnings(by_type, self.CATALOG))

    def test_naming_is_per_resource_type(self, gen):
        cat = {"repo_analyses": [{"id": "shared_id"}], "database_analyses": [{"id": "shared_id"}]}
        w = gen.orphan_warnings({"repo": [{"answering": {"analysis_ids": ["shared_id"]}}]}, cat)
        assert len(w) == 1 and "database_analyses" in w[0]

    def test_a_library_step_declaring_provided_by_questions_is_not_an_orphan(self, gen):
        cat = {"database_analyses": [
            {"id": "lib_step", "provided_by": {"questions": ["Q-0086"]}},
            {"id": "lib_step_empty", "provided_by": {"questions": []}}]}
        w = gen.orphan_warnings({"database": []}, cat)
        assert len(w) == 1 and "lib_step_empty" in w[0]

    def test_generate_lists_it_through_the_catalog_argument(self, gen, header):
        said: list[str] = []
        gen.generate([_row(header, **{"Resource Types": "database", "Answering Analysis": "schema_inventory"})],
                     known_purposes=["Explore"], warnings=said, catalog={"database_analyses": [
                         {"id": "schema_inventory", "intent": "scouting"}, {"id": "left_alone"},
                         {"id": "feeds_others", "answers_questions": False}]})
        orphans = [w for w in said if "named by no question" in w]
        assert len(orphans) == 1 and "left_alone" in orphans[0]

    def test_the_shipped_catalogue_declares_its_twelve_infrastructure_entries(self):
        cat = yaml.safe_load((ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml").read_text())
        declared = {e["id"] for k, s in cat.items() if k.endswith("_analyses") for e in s if e.get("answers_questions") is False}
        assert declared == {"repo_profile_refresh", "rag_ingestion", "website_ingestion", "sub_resource_survey",
                            "code_symbol_extraction", "manifest_parse", "egeria_publish", "egeria_db_survey",
                            "doc_source_ingestion", "external_data_scout", "external_data_profile",
                            "external_data_refresh"}   # + OD2, OD3, OD4 (open data has no questions yet)

    def test_the_committed_csv_leaves_only_known_orphans(self, gen):
        with open(CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        said: list[str] = []
        gen.generate(rows, warnings=said)
        orphans = {w.split()[1] for w in said if "named by no question" in w}
        # semantic_suggestions: a GAP note may not name a registered id, so Q-0087 cannot name it. The four
        # library steps are no longer orphans: their declarations name the questions they answer.
        assert orphans == {"semantic_suggestions"}, orphans


# ---------------------------------------------------------------- rule 5: retired and merged rows
class TestRetiredAndMergedRowsAreSkippedButTheirIdsStay:
    @staticmethod
    def _rows(header):
        return [
            _row(header, "Q-0001"),
            _row(header, "Q-0002", Status="retired 2026-10-22: launcher affordance (review §1.4)"),
            _row(header, "Q-0003", Status="merged into Q-0001 2026-10-22"),
            _row(header, "Q-0009", Status="merged into Q-0001 2026-10-22"),
        ]

    def test_skipped_rows_are_in_no_section(self, gen, header):
        out = yaml.safe_load(gen.generate(self._rows(header), known_purposes=["Explore"]))
        assert [e["id"] for e in out["repo_questions"]] == ["Q-0001"]

    def test_their_ids_are_reserved_in_the_yaml(self, gen, header):
        out = yaml.safe_load(gen.generate(self._rows(header), known_purposes=["Explore"]))
        assert out["reserved_question_ids"] == ["Q-0002", "Q-0003", "Q-0009"]

    def test_the_next_free_id_counts_them(self, gen, header):
        assert gen.next_question_id(self._rows(header)) == "Q-0010"

    def test_a_skipped_row_still_needs_a_well_formed_unique_id(self, gen, header):
        rows = self._rows(header) + [_row(header, "Q-0002")]
        with pytest.raises(ValueError, match="duplicate Question ID 'Q-0002'"):
            gen.generate(rows, known_purposes=["Explore"])

    def test_a_skipped_row_is_not_checked_for_an_adapter_or_a_step(self, gen, header):
        rows = [_row(header, "Q-0001"), _row(header, "Q-0002", Status="retired 2026-10-22: x",
                                             **{"Resource Types": "dataset"})]
        yaml.safe_load(gen.generate(rows, known_purposes=["Explore"]))

    def test_the_bare_retired_the_admin_pane_writes_keeps_its_old_behaviour(self, gen, header):
        rows = [_row(header, "Q-0001"), _row(header, "Q-0002", Status="Retired")]
        out = yaml.safe_load(gen.generate(rows, known_purposes=["Express"] and ["Explore"]))
        assert [(e["id"], e["retired"]) for e in out["repo_questions"]] == [("Q-0001", False), ("Q-0002", True)]
        assert "reserved_question_ids" not in out

    def test_no_reserved_key_when_nothing_is_skipped(self, gen, header):
        assert "reserved_question_ids" not in yaml.safe_load(gen.generate([_row(header)], known_purposes=["Explore"]))

    def test_the_dr_egeria_document_skips_them_too(self, dr_gen, header):
        doc = dr_gen.generate(self._rows(header), questions_glossary="User Questions", title="t")
        assert "Question text for Q-0001?" in doc
        for gone in ("Q-0002", "Q-0003", "Q-0009"):
            assert f"Question text for {gone}?" not in doc
        assert doc.count("## Create Glossary Term") == 1

    def test_one_definition_of_skipped_serves_both_generators(self):
        from resource_explorer.question_status import is_skipped_status

        for s in ("retired 2026-10-22: x", "Retired 2026-10-22: x", "merged into Q-0011 2026-10-22"):
            assert is_skipped_status(s), s
        for s in ("", None, "Retired", "inadequately answered today", "partly answered today"):
            assert not is_skipped_status(s), s

    def test_the_admin_writer_treats_a_dated_retirement_as_already_retired(self):
        from resource_explorer.surveyors import question_catalog_writer as qcw

        assert qcw._is_retired({"Status": "retired 2026-10-22: launcher affordance"})
        assert qcw._is_retired({"Status": "merged into Q-0011 2026-10-22"})
        assert qcw._is_retired({"Status": "Retired"})
        assert not qcw._is_retired({"Status": ""})


# ---------------------------------------------------------------- all_question_ids() still returns every id
class TestEveryIdSurvivesInTheReader:
    def test_all_question_ids_returns_retired_and_merged_ids(self):
        from resource_explorer.surveyors import question_catalog_reader as qcr

        qcr.clear_cache()
        try:
            ids = qcr.all_question_ids()
            with open(CSV, newline="", encoding="utf-8") as f:
                csv_ids = {r["Question ID"] for r in csv.DictReader(f)}
            assert ids == csv_ids
            for gone in ("Q-0039", "Q-0042", "Q-0009", "Q-0010", "Q-0012", "Q-0019", "Q-0020", "Q-0023",
                         "Q-0063", "Q-0074", "Q-0089"):
                assert gone in ids, gone
        finally:
            qcr.clear_cache()

    def test_a_skipped_question_is_in_no_list_a_person_sees(self):
        from resource_explorer.surveyors import question_catalog_reader as qcr

        qcr.clear_cache()
        try:
            for rt in ("repo", "database", "filesystem"):
                shown = {e["id"] for e in qcr.get_questions(rt)}
                assert not shown & {"Q-0039", "Q-0042", "Q-0009"}, rt
        finally:
            qcr.clear_cache()


# ---------------------------------------------------------------- end to end on the committed CSV
class TestTheCommittedCsvGeneratesTheNumbersInThePrBody:
    @pytest.fixture(scope="class")
    def generated(self, gen):
        with open(CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        said: list[str] = []
        out = yaml.safe_load(gen.generate(rows, warnings=said))
        return rows, out, said

    def test_row_counts(self, generated):
        rows, out, _ = generated
        # 2026-10-28: +4 rows (Q-0106 to Q-0109, Files build step 6); 2026-10-30: +3 (Q-0110 to Q-0112, CMP1)
        assert len(rows) == 112
        statuses = Counter("retired" if r["Status"].startswith("retired") else "merged" if r["Status"].startswith("merged")
                           else "active" for r in rows)
        assert statuses == {"active": 101, "retired": 2, "merged": 9}
        assert len(out["reserved_question_ids"]) == 11

    def test_active_questions_by_kind(self, generated):
        _, out, _ = generated
        assert {k: len(v) for k, v in out.items() if k.endswith("_questions")} == {
            "repo_questions": 52, "database_questions": 57, "filesystem_questions": 38}
        assert "dataset_questions" not in out and "model_questions" not in out

    def test_active_questions_by_stage(self, generated):
        _, out, _ = generated
        by_id = {e["id"]: e for k, v in out.items() if k.endswith("_questions") for e in v}
        assert len(by_id) == 101
        assert dict(Counter(e["stage"] for e in by_id.values())) == {
            "Analysis": 38, "Discovery": 26, "Scouting": 13, "Assessment": 14, "Enrichment": 5,
            "Analysis/Enrichment": 1, "Scouting/Assessment": 1, "Discovery/Enrichment": 2, "Discovery/Analysis": 1}

    def test_no_answer_is_unclassified(self, generated):
        _, out, _ = generated
        kinds = Counter(e["answering"]["kind"] for k, v in out.items() if k.endswith("_questions") for e in v)
        assert "unknown" not in kinds, kinds

    def test_the_new_rows_are_present_and_the_retired_ones_absent(self, generated):
        _, out, _ = generated
        every = {e["id"]: e for k, v in out.items() if k.endswith("_questions") for e in v}
        assert {"Q-0098", "Q-0099"} <= set(every)
        assert every["Q-0098"]["stage"] == "Enrichment"
        assert [e["id"] for e in out["database_questions"] if e["id"] == "Q-0099"] == ["Q-0099"]
        assert "Q-0099" not in {e["id"] for e in out["repo_questions"]}
        assert not {"Q-0039", "Q-0042"} & set(every)

    def test_the_gap_rows_for_unbuilt_file_analyses_parse_as_gaps(self, generated):
        _, out, _ = generated
        fs = {e["id"]: e for e in out["filesystem_questions"]}
        for qid, name in (("Q-0101", "file_profiles"), ("Q-0105", "folder_narratives")):
            assert fs[qid]["answering"]["kind"] == "gap", qid
            assert name in fs[qid]["answering"]["note"], qid
        # built in Files build steps 6 and 7 (2026-10-28): no longer gaps
        assert fs["Q-0102"]["answering"]["kind"] == "analysis"
        assert fs["Q-0102"]["answering"]["analysis_ids"] == ["fs_change_rates"]
        assert fs["Q-0104"]["answering"]["kind"] == "analysis"
        assert fs["Q-0104"]["answering"]["analysis_ids"] == ["fs_layout_signals"]

    def test_the_merge_targets_name_what_they_absorbed(self, generated):
        rows, _, _ = generated
        by = {r["Question ID"]: r for r in rows}
        for target, absorbed in (("Q-0011", ("Q-0009", "Q-0010")), ("Q-0030", ("Q-0012",)), ("Q-0085", ("Q-0089",)),
                                 ("Q-0088", ("Q-0063",)), ("Q-0082", ("Q-0074",)), ("Q-0098", ("Q-0019", "Q-0020", "Q-0023"))):
            for a in absorbed:
                assert a in by[target]["Catalog History"], (target, a)
                assert by[a]["Status"] == f"merged into {target} 2026-10-22", a

    def test_the_two_contributor_rows_declare_their_list_extent(self, generated):
        rows, _, _ = generated
        by = {r["Question ID"]: r for r in rows}
        for qid in ("Q-0003", "Q-0051"):
            assert "declares list extent: top contributors by commit share, with names" in by[qid]["Answering Analysis"]

    def test_find_and_integrate_are_on_the_twelve_rows_the_review_names(self, generated):
        rows, _, said = generated
        by = {r["Question ID"]: r for r in rows}
        for qid in ("Q-0007", "Q-0011", "Q-0070", "Q-0075", "Q-0076", "Q-0077"):
            assert "Find" in by[qid]["Purposes"].split(";"), qid
        for qid in ("Q-0013", "Q-0014", "Q-0022", "Q-0048", "Q-0053", "Q-0084"):
            assert "Integrate" in by[qid]["Purposes"].split(";"), qid
        # Attest and Remediate are older, unrelated warnings (they are not in the seed list); Find and Integrate are.
        assert not [w for w in said if "'Find'" in w or "'Integrate'" in w]

    def test_dataset_is_withdrawn_from_the_three_rows(self, generated):
        rows, _, _ = generated
        assert not [r["Question ID"] for r in rows if "dataset" in r["Resource Types"] or "model" in r["Resource Types"]]
        by = {r["Question ID"]: r for r in rows}
        assert by["Q-0011"]["Resource Types"] == "*"
        assert by["Q-0056"]["Resource Types"] == by["Q-0080"]["Resource Types"] == "database;filesystem"
