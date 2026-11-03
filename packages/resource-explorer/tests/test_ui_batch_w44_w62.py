"""UI batch W44–W62 / F49–F50 (2026-11-03 read-backs): one test or more per grade, words as served."""
from __future__ import annotations

import pytest

from resource_explorer.facts import Fact, FactLayer
from resource_explorer.surveyors.database import db_derived
from resource_explorer.surveyors.result_status import NEVER_RUN, NO_READER


# --- W54: an unrun row's answer line is one sentence, whatever its facts' states -----------------------------------

@pytest.mark.parametrize("states", [(NEVER_RUN, NEVER_RUN), (NEVER_RUN, NO_READER)])
def test_w54_unrun_answer_line_is_one_sentence(monkeypatch, states):
    layer = FactLayer()
    monkeypatch.setattr(layer, "facts", lambda slug, ids, level=None: [
        Fact(aid, st, can_run=[aid]) for aid, st in zip(["postgres_column_profile", "postgres_nested_columns"], states)])
    q = {"id": "Q-1", "question": "q", "answering": {"kind": "analysis",
         "analysis_ids": ["postgres_column_profile", "postgres_nested_columns"]}}
    env = layer.answer("slug", q)
    assert env.answerable is False
    assert env.blocked_reason.startswith("Nothing has been measured for this yet.")
    assert "Not yet run" not in env.blocked_reason   # the row's source line says WHEN, once (F23)


def test_w54_a_known_count_of_databases_is_not_database_s():
    assert db_derived._databases(1) == "database"
    assert db_derived._databases(11) == "databases"
    src = open(db_derived.__file__, encoding="utf-8").read()
    assert "database(s)" not in src.replace('"database(s)" beside', "")
