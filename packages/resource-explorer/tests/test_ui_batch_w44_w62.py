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


# --- W55: a per-card Run → says the stage it was pressed on beside the intent it declares ---------------------------

def test_w55_the_pressed_on_stage_reaches_the_one_analysis_surveys_activity_row(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.run_queue import _handle_database_analysis_run
    from resource_explorer.surveyors.survey_definition_executor import get_adapter
    from tests.test_database_library_step_run import _fake_library_step

    registry = ProjectRegistry(db_path=str(tmp_path / "w55.db"))
    registry.register_database(DatabaseEntity(slug="nocreds", display_name="No Creds", db_type="postgresql",
                                              host="localhost", port=5432, database_name="nocreds"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setitem(get_adapter("database").re_analysis_steps, "database_staleness", _fake_library_step([]))
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/databases/nocreds/analyses/database_staleness/run?pressed_on=4%20Analysis")
    assert resp.status_code == 200, resp.text
    [queued] = registry.list_runs(state="queued")
    target = json.loads(queued["target"]) if isinstance(queued["target"], str) else queued["target"]
    assert target["pressed_on"] == "4 Analysis"

    assert _handle_database_analysis_run(target, resp.json()["activity_id"]).state == "succeeded"
    wrappers = [a for a in registry.list_activity(limit=50)
                if str(json.loads(a.get("detail") or "{}").get("survey_definition_ref", "")).startswith("synthetic::")]
    assert [json.loads(w["detail"])["pressed_on"] for w in wrappers] == ["4 Analysis"]
    assert wrappers[0]["intent"] == "discovery"     # the declared intent stays; the stage is said beside it


def test_w55_no_pressed_on_outside_a_per_card_handler():
    from resource_explorer.activity_logger import PRESSED_ON, pressed_on

    assert PRESSED_ON.get() == ""
    with pressed_on("4 Analysis"):
        assert PRESSED_ON.get() == "4 Analysis"
    assert PRESSED_ON.get() == ""


# --- W56: a card whose last run was its own Run → says so, not "via survey" ---------------------------------------

def test_w56_a_per_card_run_is_named_on_the_card_and_a_real_survey_keeps_its_word(tmp_path):
    import json

    from resource_explorer.activity_logger import log_survey
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.workflows.stage_page import _last_run_info

    registry = ProjectRegistry(db_path=str(tmp_path / "w56.db"))
    registry.register_database(DatabaseEntity(slug="d", display_name="D", db_type="postgresql",
                                              host="localhost", port=5432, database_name="d"))
    step = lambda key: {"step": f"x::{key}", "re_analysis_step": key, "status": "ok"}  # noqa: E731
    wrapper_id = log_survey(registry, "database", "d", "D", "", intent="discovery", status="ok", summary="s",
                            detail=json.dumps({"survey_definition_ref": "synthetic::database::d::statistics_freshness",
                                               "steps": [step("statistics_freshness")]}))
    log_survey(registry, "database", "d", "D", "", intent="discovery", status="ok", summary="s",
               detail=json.dumps({"survey_definition_ref": "RE::db-survey", "steps": [step("change_rates")]}))

    card = _last_run_info(registry, "d", "statistics_freshness", entity_type="database")
    assert card["last_run_via"] == "per_card" and card["last_run_survey_id"] == wrapper_id
    other = _last_run_info(registry, "d", "change_rates", entity_type="database")
    assert other["last_run_via"] == "survey" and other["last_run_survey_id"] == ""


# --- W57: a basis whose numerator counts a part of the whole names what it counts ----------------------------------

def test_w57_a_state_count_is_never_read_as_n_measured():
    from trellis_analytics.declaration import BasisDeclaration, MeasureDeclaration

    from resource_explorer import declared_words

    basis = BasisDeclaration(denominator_field="Tables", denominator_label="tables")
    subset = MeasureDeclaration(name="statistics_freshness_summary", unit="tables never analyzed or stale", as_of="asOf",
                                value_field="Needs Attention", basis=basis)
    said, _ = declared_words.measure_sentence(subset, {"Needs Attention": 215, "Tables": 266})
    assert said == "215 tables never analyzed or stale of 266 tables (81% of the tables)"
    # A numerator in the whole's own words keeps the short form.
    whole = MeasureDeclaration(name="profiled_columns", unit="columns", as_of="asOf", value_field="n",
                               basis=BasisDeclaration(denominator_field="t", denominator_label="columns"))
    assert declared_words.measure_sentence(whole, {"n": 456, "t": 1236})[0] == "456 of 1,236 columns (37%)"
