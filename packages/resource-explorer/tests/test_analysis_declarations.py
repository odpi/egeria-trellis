"""Declared extensibility, slice 1 (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE §1): every database and
filesystem analysis in analysis_catalog.yaml declares its results in full, the reader validates a declaration by the
same model a library step's manifest uses, and a refused declaration names what was refused without taking the entry
or the catalog down with it."""
import collections
from pathlib import Path

import pytest
import yaml

from resource_explorer.surveyors import analysis_catalog_reader as reader

CONFIG = Path(reader.__file__).resolve().parent.parent / "configdata"
DECLARED_TYPES = ("database", "filesystem")


@pytest.fixture(autouse=True)
def _fresh_catalog():
    reader.clear_cache()
    yield
    reader.clear_cache()


def _entries():
    loaded = reader._load()
    return [e for t in DECLARED_TYPES for e in loaded[t]]


def test_no_declaration_in_the_catalog_is_refused():
    assert reader.declaration_refusals() == []


def test_every_database_and_filesystem_analysis_declares_in_full():
    incomplete = {e.id: (e.declaration.missing() if e.declaration else ["declaration"]) for e in _entries()
                  if not e.declaration or e.declaration.missing()}
    assert incomplete == {}, f"declare these fields: {incomplete}"
    assert len(_entries()) >= 36


#: The member- or field-scoped tables declared before B1 that still owe part of a table result, and what each owes
#: (Architect ruling, 2026-11-02: reported by `missing()` for one release, closed by convert-as-touched). On main
#: before B1 there was one, value_profile (field scope, bare columns, a hand extent); this branch's commit 3 converted
#: it, so none remains. A NEW member or field table may not join this list: it declares in full.
PRE_B1_TABLE_LACKS: dict[str, list[str]] = {}


def test_only_the_pre_b1_member_and_field_tables_owe_anything_and_exactly_what_they_owe():
    owing = {e.id: [m for m in e.declaration.missing() if m.startswith("table.")]
             for t in reader._load().values() for e in t
             if e.declaration is not None and e.declaration.table is not None
             and e.declaration.scope in ("member", "field")}
    assert {k: v for k, v in owing.items() if v} == PRE_B1_TABLE_LACKS
    assert owing["value_profile"] == [] and next(
        e for e in _entries() if e.id == "value_profile").declaration.is_table_result()


def test_every_declared_measure_has_a_unit_and_an_as_of_and_every_absence_a_sentence():
    for e in _entries():
        for m in e.declaration.measures:
            assert m.unit.strip() and m.as_of.strip() and m.value_field.strip(), (e.id, m.name)
        for s in ("not_measured", "not_permitted", "not_applicable", "nothing_found"):
            assert e.declaration.absence.sentence(s).strip(), (e.id, s)


def test_a_measure_writes_as_one_of_the_entrys_own_annotation_types():
    for e in _entries():
        for m in e.declaration.measures:
            if m.annotation:
                assert m.annotation.type in e.annotation_types, (e.id, m.name, m.annotation.type)


def _question_binding() -> dict[str, set[str]]:
    q = yaml.safe_load((CONFIG / "question_catalog.yaml").read_text())
    bound = collections.defaultdict(set)
    for section in ("repo_questions", "database_questions", "filesystem_questions"):
        for item in q.get(section) or []:
            if item.get("retired"):
                continue
            for a in (item.get("answering") or {}).get("analysis_ids") or []:
                bound[a].add(item["id"])
    return bound


def test_answers_are_the_question_catalogs_binding_not_a_second_list():
    """A built-in analysis's answers are the questions whose analysis_ids name it; a library step's are its
    manifest's questions (provided_by.questions). Either way, one list: a declaration that disagrees fails here."""
    bound = _question_binding()
    for e in _entries():
        expected = set(e.provided_by.get("questions") or []) if e.provided_by else bound.get(e.id, set())
        assert set(e.declaration.answers) == expected, e.id


def test_infrastructure_answers_no_question_by_declaration():
    raw = {e["id"]: e for t in ("database_analyses", "filesystem_analyses")
           for e in yaml.safe_load((CONFIG / "analysis_catalog.yaml").read_text())[t]}
    for e in _entries():
        if raw[e.id].get("answers_questions") is False:
            assert e.declaration.answers == [], e.id


def test_a_table_kind_says_why_none_of_the_others_fits():
    for e in _entries():
        if e.declaration.result_kind == "table":
            assert e.declaration.result_kind_note.strip(), e.id


def test_to_dict_carries_the_declaration():
    entry = next(e for e in _entries() if e.id == "db_change_rates")
    d = entry.to_dict()
    assert d["declaration"]["result_kind"] == "list"
    assert d["declaration"]["measures"][0]["unit"] == "tables"
    assert "declaration_refused" not in d


# ── the reader's refusals ──────────────────────────────────────────────────

ABSENCE = {"not_measured": "a", "not_permitted": "b", "not_applicable": "c", "nothing_found": "d"}


def _raw(declaration, **over):
    return {"id": "x_analysis", "name": "X", "annotation_types": ["ResourceMeasureAnnotation"],
            "declaration": declaration, **over}


def _good(**over):
    return {"result_kind": "measure", "absence": ABSENCE,
            "measures": [{"name": "n", "unit": "tables", "as_of": "surveyed_at", "value_field": "n"}], **over}


def test_no_declaration_is_absent_not_refused():
    assert reader.check_declaration({"id": "x"}) == (None, "")


def test_a_valid_declaration_is_accepted():
    declared, refused = reader.check_declaration(_raw(_good()))
    assert refused == "" and declared.result_kind == "measure"


@pytest.mark.parametrize("declaration, words", [
    (_good(result_kind="chart"), "result_kind 'chart' is not one of the 8 kinds"),
    (_good(absence={**ABSENCE, "not_permitted": ""}), "absence sentence for not_permitted is empty"),
    (_good(colour="blue"), "colour is not a declaration field"),
    (["not", "a", "mapping"], "declaration is a list, not a mapping of declaration fields"),
    (_good(measures=[{"name": "n", "unit": "tables", "as_of": "surveyed_at", "value_field": "n",
                      "annotation": {"type": "DataClassAnnotation"}}]),
     "measure n (DataClassAnnotation) writes as an annotation type the entry does not list in annotation_types"),
])
def test_a_refusal_names_the_entry_and_what_was_refused(declaration, words):
    declared, refused = reader.check_declaration(_raw(declaration))
    assert declared is None
    assert refused.startswith("analysis catalog entry 'x_analysis': ")
    assert words in refused


def test_a_refused_declaration_leaves_the_entry_listed_and_saying_why(caplog):
    entry = reader._entry_from_yaml(_raw(_good(result_kind="chart"), description="d"))
    assert entry.declaration is None and "result_kind 'chart'" in entry.declaration_refused
    d = entry.to_dict()
    assert d["id"] == "x_analysis" and "declaration" not in d
    assert d["declaration_refused"] == entry.declaration_refused
    assert any("result_kind 'chart'" in r.getMessage() for r in caplog.records)


# ── the owner's answers of 2026-10-28 ───────────────────────────────────────

def test_hand_written_answers_are_refused_they_are_generated():
    """One list, not two: a built-in entry's answers come from the question catalogue, never from the entry."""
    declared, refused = reader.check_declaration(_raw(_good(answers=["Q-0001"])))
    assert declared is None
    assert "answers is written by hand; they are generated from the question catalog's analysis_ids" in refused


def test_a_built_in_entrys_answers_are_generated_from_the_question_catalogue():
    declared, refused = reader.check_declaration(_raw(_good(), id="db_change_rates"))
    assert refused == "" and declared.answers == list(reader.question_binding()["db_change_rates"])
    declared, _ = reader.check_declaration(_raw(_good(), id="x", answers_questions=False))
    assert declared.answers == []


def test_no_hand_written_entry_in_the_catalog_carries_answers():
    """The staleness check the equality test became: the file holds no second list to drift."""
    raw = yaml.safe_load((CONFIG / "analysis_catalog.yaml").read_text())
    written = [e["id"] for t in ("database_analyses", "filesystem_analyses") for e in raw[t]
               if not e.get("provided_by") and "answers" in (e.get("declaration") or {})]
    assert written == []


def test_the_not_built_analyses_are_declared_as_planned():
    built = {e.id: e.declaration.built for e in _entries()}
    assert {k for k, v in built.items() if not v} == {"doc_source_ingestion", "doc_evidence_check",
                                                      "semantic_suggestions"}
