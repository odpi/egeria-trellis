"""W58: an analysis that answers two questions gives each its own headline.

`schema_conventions` answers Q-0088 (documentation) and Q-0090 (well-modelled) with different measures: the analysis
catalog's `headline_by_question` names, per question, the checks whose own sentences headline it; the shared sentence
(every gap, or the clean pass) stays the fallback for Q-0083 and any other question. One stored run, two questions,
two headlines — read through the same FactLayer the Questions tab uses. Temp SQLite registry, no Egeria.
"""
from __future__ import annotations

import pytest

from tests.test_db_derived_step import NOW, _column, _store, _table, registry  # noqa: F401 - the fixture

SLUG = "coco_ods"


def _question(qid: str) -> dict:
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    return next(q for q in get_questions(resource_type="database") if q["id"] == qid)


def _headline(registry):
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_ANALYSIS_HEADLINE_MAP

    return DATABASE_ANALYSIS_HEADLINE_MAP["schema_conventions"](registry, SLUG)


def test_the_catalog_declares_the_map_and_the_entry_carries_it():
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    entry = next(e for e in _load()["database"] if e.id == "schema_conventions")
    assert entry.headline_by_question == {"Q-0088": ["tables_without_comment"],
                                         "Q-0090": ["tables_without_primary_key", "tables_without_foreign_key"]}
    declared = {m.name for m in entry.declaration.measures}
    assert {n for names in entry.headline_by_question.values() for n in names} <= declared, \
        "every named check is a declared measure of the analysis"
    for qid in entry.headline_by_question:
        assert "schema_conventions" in _question(qid)["answering"]["analysis_ids"], qid


def test_two_questions_read_different_headlines_from_one_run(registry):
    # One table with no comment and a primary key; one with a comment and no key: both halves have a gap to say.
    tables = [_table("orders"), _table("customer", desc="People who buy things")]
    columns = [_column("orders", "order_id", pk=True, desc="Key"), _column("customer", "customer_id", pk=False)]
    _store(registry, SLUG, NOW, tables=tables, columns=columns)
    head = _headline(registry)
    assert head["by_question"]["Q-0088"] == "1 of 2 base tables have no comment; 1 of 2 columns do."
    assert head["by_question"]["Q-0090"] == ("1 of 2 base tables declare no primary key. 2 of 2 base tables declare no "
                                             "foreign key. Whether that is a fault depends on the kind of database — see "
                                             "db_relationship_graph's verdict.")
    assert head["by_question"]["Q-0088"] != head["by_question"]["Q-0090"]
    # The shared sentence (every gap) is the label, and the third question's answer.
    assert "declare no primary key" in head["label"] and "have no comment" in head["label"]

    from resource_explorer.facts import FactLayer

    layer = FactLayer(registry, resource_type="database")
    answers = {qid: layer.answer(SLUG, _question(qid)) for qid in ("Q-0088", "Q-0090", "Q-0083")}
    fact = {qid: next(f for f in env.facts if f.analysis_id == "schema_conventions") for qid, env in answers.items()}
    assert fact["Q-0088"].headline == head["by_question"]["Q-0088"]
    assert fact["Q-0090"].headline == head["by_question"]["Q-0090"]
    assert fact["Q-0083"].headline == head["label"], "a question the map does not name keeps the shared sentence"
    assert fact["Q-0088"].headline != fact["Q-0090"].headline


def test_a_check_the_run_did_not_produce_is_said_not_left_out(registry):
    from resource_explorer.surveyors.database.survey_definition_adapter import _checks_headline_by_question

    out = _checks_headline_by_question("schema_conventions", {"tables_without_comment": {"explanation": "x."}})
    assert out["Q-0088"] == "x."
    assert out["Q-0090"] == ("tables_without_primary_key: not measured on this run. "
                             "tables_without_foreign_key: not measured on this run.")
    assert _checks_headline_by_question("no_such_analysis", {}) == {}


def test_an_unverified_check_says_so_in_its_own_question(registry):
    """Keys never captured: Q-0090's headline is the check's own 'could not run … NOT a finding' words, never the
    comment half's sentence or a confident count."""
    from resource_explorer.registry import SOURCE_EGERIA

    _store(registry, SLUG, NOW, tables=[_table("a", desc="d")], columns=[_column("a", "x", keys_captured=False, desc="k")],
           source=SOURCE_EGERIA)
    head = _headline(registry)
    assert "NOT a finding that keys are missing" in head["by_question"]["Q-0090"]
    assert "comment" not in head["by_question"]["Q-0090"]
    assert head["by_question"]["Q-0088"] == "0 of 1 base tables have no comment; 1 of 1 columns do."
