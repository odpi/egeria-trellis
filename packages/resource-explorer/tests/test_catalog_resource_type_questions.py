"""RS-W1-3: `catalog` is a resource type of the question catalog, and the Egeria Overview's catalog-wide questions
are its rows.

What these pin, each from the side that would fail quietly:

- the vocabulary: `catalog` is a name, not a surveyed, registered-only or member kind, and `*` does not reach it;
- the two catalog rules both generators share (`catalog` stands alone; only a catalog row has no Funnel Stage);
- the third known-id source (Portal routines), with its GAP and PARTIAL guards;
- the heal boundary: catalog rows are never in the document the `questions` batch executes, and their own document
  sits in a folder the bootstrap does not treat as a batch;
- the committed catalog document is what the CSV generates, and links only Perspectives the foundations create;
- resource-scoped readers ignore the catalog section.
"""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from resource_explorer.resource_types import (
    ALL_RESOURCE_TYPES_MARKER,
    CATALOG_RESOURCE_TYPE,
    MEMBER_RESOURCE_TYPES,
    QUESTION_ONLY_RESOURCE_TYPES,
    REGISTERED_ONLY_RESOURCE_TYPES,
    RESOURCE_TYPES,
    SURVEYED_RESOURCE_TYPES,
    check_catalog_row,
    parse_resource_types,
)

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
YAML_SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"
DR_SCRIPT = ROOT / "scripts" / "csv_to_dr_egeria_questions.py"
DOCS = ROOT / "docs" / "dr-egeria"
CATALOG_DOC = DOCS / "questions-catalog" / "catalog-questions.md"
QUESTIONS_DOC = DOCS / "questions" / "scouting-questions.md"
QUESTION_CATALOG = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"
PORTAL_ROUTINES = ROOT / "resource_explorer" / "configdata" / "portal_routines.yaml"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load(YAML_SCRIPT, "csv_to_question_catalog_yaml_catalog_type")


@pytest.fixture(scope="module")
def dr_gen():
    return _load(DR_SCRIPT, "csv_to_dr_egeria_questions_catalog_type")


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with open(CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _row(header, qid="Q-0001", **over) -> dict:
    row = {c: "" for c in header}
    row.update({"Question ID": qid, "Question": f"Catalog question {qid}?", "Funnel Stage": "",
                "Answering Analysis": "governed_coverage", "Answering Mechanism": "Portal tile",
                "Resource Types": "catalog", "Level": "resource", "Governance": "X"})
    row.update(over)
    return row


class TestTheVocabulary:
    def test_catalog_is_a_name_and_in_no_operational_set(self):
        assert CATALOG_RESOURCE_TYPE == "catalog"
        assert CATALOG_RESOURCE_TYPE in RESOURCE_TYPES
        assert CATALOG_RESOURCE_TYPE in QUESTION_ONLY_RESOURCE_TYPES
        for operational in (SURVEYED_RESOURCE_TYPES, REGISTERED_ONLY_RESOURCE_TYPES, MEMBER_RESOURCE_TYPES):
            assert CATALOG_RESOURCE_TYPE not in operational

    def test_star_still_means_the_three_surveyed_kinds(self):
        assert parse_resource_types(ALL_RESOURCE_TYPES_MARKER) == ["repo", "database", "filesystem"]

    def test_catalog_parses_to_itself(self):
        assert parse_resource_types("catalog") == ["catalog"]

    def test_batch_import_still_refuses_a_catalog_row(self):
        from resource_explorer import batch_io

        assert CATALOG_RESOURCE_TYPE not in batch_io.RESOURCE_TYPES


class TestTheCatalogRowRules:
    def test_a_catalog_row_with_no_stage_is_fine(self):
        check_catalog_row("catalog", "", "Q-1")

    def test_catalog_must_stand_alone(self):
        with pytest.raises(ValueError, match="must stand alone"):
            check_catalog_row("catalog;repo", "", "Q-1")

    def test_a_catalog_row_may_not_have_a_stage(self):
        with pytest.raises(ValueError, match="has no funnel stage"):
            check_catalog_row("catalog", "Scouting", "Q-1")

    @pytest.mark.parametrize("types", ["repo", "*", "database;filesystem", ""])
    def test_an_empty_stage_on_any_other_row_is_an_error(self, types):
        with pytest.raises(ValueError, match="no Funnel Stage"):
            check_catalog_row(types, "  ", "Q-1")

    def test_both_generators_refuse_an_empty_stage_on_a_resource_row(self, gen, dr_gen, rows):
        bad = dict(rows[0], **{"Funnel Stage": ""})
        with pytest.raises(ValueError, match="no Funnel Stage"):
            gen.generate([bad])
        with pytest.raises(ValueError, match="no Funnel Stage"):
            dr_gen.generate([bad], questions_glossary="User Questions", title="t")


class TestPortalRoutines:
    def test_the_list_is_the_registrys_24_and_well_formed(self, gen):
        raw = yaml.safe_load(PORTAL_ROUTINES.read_text())["routines"]
        assert len(raw) == 24
        for r in raw:
            assert r["name"] and r["module"] == "pyegeria.view.overview_metrics" and r["purpose"]
        assert gen.KNOWN_PORTAL_ROUTINES == [r["name"] for r in raw]
        assert {"governed_coverage", "semantic_grounding", "context_readiness_funnel", "ai_ready_assets",
                "asset_composition"} <= set(gen.KNOWN_PORTAL_ROUTINES)

    def test_a_routine_list_is_kind_portal(self, gen):
        a = gen._parse_catalog_answering("usage_context_counts + contextualised_coverage (gloss)")
        assert a["kind"] == "portal"
        assert a["portal_routines"] == ["usage_context_counts", "contextualised_coverage"]
        assert a["analysis_ids"] == [] and a["checks"] == []

    def test_an_unknown_name_stays_unknown(self, gen):
        assert gen._parse_catalog_answering("governed_coverage_v2")["kind"] == "unknown"

    def test_a_gap_naming_a_routine_is_refused(self, gen):
        with pytest.raises(ValueError, match="names a Portal routine that exists"):
            gen._parse_catalog_answering("GAP: governed_coverage does not do this")

    def test_a_partial_naming_no_routine_is_refused(self, gen):
        with pytest.raises(ValueError, match="names no known Portal routine"):
            gen._parse_catalog_answering("PARTIAL: something answers part of it")

    def test_routines_are_not_read_on_a_resource_row(self, gen):
        # A repository question's prose naming a routine is not an answer by it: the routine is estate-wide.
        a = gen._parse_answering("N/A — direct field; see governed_coverage")
        assert "portal_routines" not in a


class TestTheGeneratedYaml:
    def test_a_catalog_section_with_no_unknowns_and_no_stage(self, gen, rows):
        doc = yaml.safe_load(gen.generate(rows))
        section = doc["catalog_questions"]
        catalog_rows = [r for r in rows if r["Resource Types"].strip() == "catalog"]
        assert len(section) == len(catalog_rows) == 40
        for e in section:
            assert e["stage"] == ""
            assert e["answering"]["kind"] in {"portal", "partial", "gap"}, e["id"]
            if e["answering"]["kind"] in {"portal", "partial"}:
                assert e["answering"]["portal_routines"], e["id"]
            assert e["answering"]["analysis_ids"] == []

    def test_catalog_questions_appear_in_no_resource_section(self, gen, rows):
        doc = yaml.safe_load(gen.generate(rows))
        catalog_ids = {e["id"] for e in doc["catalog_questions"]}
        for key, section in doc.items():
            if key.endswith("_questions") and key != "catalog_questions":
                assert not catalog_ids & {e["id"] for e in section}, key

    def test_resource_rows_carry_no_portal_routines_key(self):
        doc = yaml.safe_load(QUESTION_CATALOG.read_text())
        for key, section in doc.items():
            if key.endswith("_questions") and key != "catalog_questions":
                assert all("portal_routines" not in e["answering"] for e in section), key


class TestTheReader:
    def test_resource_readers_do_not_see_catalog_questions(self):
        from resource_explorer.surveyors import question_catalog_reader as qcr

        qcr.clear_cache()
        catalog_ids = {q["id"] for q in qcr.get_questions("catalog")}
        assert len(catalog_ids) == 40
        for rt in SURVEYED_RESOURCE_TYPES:
            assert not catalog_ids & {q["id"] for q in qcr.get_questions(rt)}
        q = qcr.find_question_by_id("Q-0116")
        assert q["answering"]["kind"] == "portal" and q["answering"]["portal_routines"] == ["governed_coverage"]

    def test_a_resource_question_dict_is_unchanged_in_shape(self):
        from resource_explorer.surveyors import question_catalog_reader as qcr

        qcr.clear_cache()
        q = qcr.get_questions("repo")[0]
        assert set(q["answering"]) == {"kind", "analysis_ids", "checks", "note"}


class TestTheHealBoundary:
    def test_the_catalog_folder_is_not_a_batch(self):
        from resource_explorer.bootstrap import BATCH_MANIFEST_FILE, discover_batches

        assert CATALOG_DOC.is_file()
        assert not (CATALOG_DOC.parent / BATCH_MANIFEST_FILE).exists(), (
            "questions-catalog/ must have no _batch.json: the bootstrap heal would execute it with no further word, "
            "and its Perspective links have no reconciler")
        batches = discover_batches()
        assert "questions-catalog" not in {b.batch_id for b in batches}
        for b in batches:
            assert "catalog-questions.md" not in b.files, b.batch_id

    def test_the_questions_batch_document_holds_no_catalog_question(self, rows):
        text = QUESTIONS_DOC.read_text(encoding="utf-8")
        for r in rows:
            if r["Resource Types"].strip() == "catalog":
                assert r["Question"] not in text, r["Question ID"]
        assert "Catalog-wide question" not in text


class TestTheCatalogDocument:
    def test_the_committed_document_is_what_its_csv_generates(self, tmp_path):
        out = tmp_path / "catalog-questions.md"
        r = subprocess.run([sys.executable, str(DR_SCRIPT), str(CSV), "--resource-type", "catalog",
                            "--output", str(out)], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        assert CATALOG_DOC.read_text(encoding="utf-8") == out.read_text(encoding="utf-8"), (
            "docs/dr-egeria/questions-catalog/catalog-questions.md does not match what the CSV generates. Run\n"
            f"  python scripts/csv_to_dr_egeria_questions.py {CSV.relative_to(ROOT)} --resource-type catalog "
            f"--output {CATALOG_DOC.relative_to(ROOT)}\n-- never the document by hand.")

    def test_no_stage_scope_and_the_catalog_usage(self, rows):
        text = CATALOG_DOC.read_text(encoding="utf-8")
        assert "## Link Element To Scope" not in text
        assert text.count("### Usage\nCatalog-wide question (Egeria Overview).") == 40
        assert text.count("## Create Glossary Term") == 40

    def test_every_linked_perspective_exists_in_the_foundations(self):
        defined = {line.strip() for doc in (DOCS / "foundations").glob("*.md")
                   for line in doc.read_text(encoding="utf-8").splitlines()
                   if line.strip().startswith("Perspective::")}
        assert defined
        linked = set()
        for block in CATALOG_DOC.read_text(encoding="utf-8").split("\n___\n"):
            if "## Link Perspective to Question" in block:
                linked.add(block.split("### Perspective Name", 1)[1].strip().splitlines()[0].strip())
        assert linked and not (linked - defined), sorted(linked - defined)
        assert not linked & {"Perspective::Data Owner", "Perspective::Admin"}

    def test_the_resource_type_option_takes_only_catalog(self, dr_gen, rows):
        with pytest.raises(ValueError, match="may only be 'catalog'"):
            dr_gen.generate(rows, questions_glossary="User Questions", title="t", resource_type="repo")
