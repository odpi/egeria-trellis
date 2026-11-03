"""walk3: a By-analysis board carries `result_state`, derived from stored proof rows.

Before, a board with no results read "survey ran 2d ago · no result for this step" in its header and "Not run yet."
in its body at once (coco_pharma, Data Class Match / Reference Data Match): both came from `has_results` being false
beside a `last_surveyed_at`. Real registry rows throughout (a per-test sqlite registry), the real results map and the
real analysis catalog; nothing here fakes a boolean.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import ActivityEntry, DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.column_profile_step import column_match_rows
from resource_explorer.workflows import analysis as analysis_mod

AFTER_STORE = "2026-10-20T19:00:00+00:00"
BEFORE_STORE = "2026-10-10T09:00:00+00:00"


@pytest.fixture
def make(tmp_path, monkeypatch):
    for v in ("REGISTRY_DATABASE_URL", "METRICS_DATABASE_URL", "FEEDBACK_DATABASE_URL"):
        monkeypatch.setenv(v, f"sqlite:///{tmp_path}/{v}.db")

    def _make(surveyed_at: str = ""):
        r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
        r.register_database(DatabaseEntity(
            slug="mydb", display_name="My DB", db_type="postgresql", host="localhost", port=5432,
            database_name="mydb", last_surveyed_at=surveyed_at))
        return r
    return _make


def _survey(reg, ts, steps):
    reg.write_activity(ActivityEntry(
        id=f"s-{ts}", ts=ts, operation="survey", intent="discovery", entity_type="database", entity_slug="mydb",
        detail=json.dumps({"surveyed_at": ts, "steps": [
            {"step": f"P::{k}", "re_analysis_step": k, "status": st} for k, st in steps]})))


def _board(reg, analysis_id, stage="analysis"):
    out = analysis_mod.build_survey_results(
        reg, "database", "mydb", stage=stage, include_empty=True, board_id=analysis_id)["dashboards"]
    return out[0]


def _match(question, verdict="match"):
    return {"schema": "s", "table": "t", "column": "c", "question": question, "verdict": verdict, "established": True}


def test_never_surveyed(make):
    assert _board(make(""), "data_class_match")["result_state"] == "never_surveyed"


def test_not_requested_when_the_latest_survey_did_not_execute_the_step(make):
    reg = make(AFTER_STORE)
    _survey(reg, AFTER_STORE, [("postgres_schema_and_stats", "ok")])
    b = _board(reg, "data_class_match")
    assert b["has_results"] is False and b["result_state"] == "not_requested"


def test_ran_no_rows_when_the_step_executed_after_the_store_began(make):
    reg = make(AFTER_STORE)
    _survey(reg, AFTER_STORE, [("postgres_column_profile", "ok")])
    assert _board(reg, "data_class_match")["result_state"] == "ran_no_rows"


def test_predates_storage_when_the_step_executed_before_the_store_began(make):
    reg = make(BEFORE_STORE)
    _survey(reg, BEFORE_STORE, [("postgres_column_profile", "ok")])
    assert _board(reg, "data_class_match")["result_state"] == "predates_storage"


def test_has_results_from_real_rows(make):
    reg = make(AFTER_STORE)
    reg.write_detail_rows("database_column_matches", "mydb", AFTER_STORE,
                          rows=column_match_rows([_match("data_class_match")]))
    assert _board(reg, "data_class_match")["result_state"] == "has_results"


def test_unattributed_when_the_survey_records_no_step_list(make):
    reg = make(AFTER_STORE)
    _survey(reg, AFTER_STORE, [])
    assert _board(reg, "data_class_match")["result_state"] == "survey_unattributed"


def test_reference_match_ran_with_zero_rows_is_ran_no_rows_from_the_coverage_row(make):
    """2026-10-20 14:22, coco_pharma: the step ran and stored data_class rows; no column passed the n_distinct gate, so
    there are no reference rows. The reader returns {} (which read as "never run"); the coverage row says it ran."""
    reg = make("2026-10-20T19:22:19+00:00")
    reg.write_detail_rows("database_column_matches", "mydb", "2026-10-20T19:22:19+00:00",
                          rows=column_match_rows([_match("data_class_match")]),
                          coverage_section="column_matches")
    ref = _board(reg, "reference_data_match")
    assert ref["has_results"] is False
    assert ref["result_state"] == "ran_no_rows"
    assert _board(reg, "data_class_match")["result_state"] == "has_results"
    # and the same verdict from the persisted fast path
    assert _board(reg, "reference_data_match")["result_state"] == "ran_no_rows"


def test_header_and_body_never_contradict_the_state_is_one_field(make):
    reg = make(AFTER_STORE)
    _survey(reg, AFTER_STORE, [("postgres_schema_and_stats", "ok")])
    b = _board(reg, "reference_data_match")
    assert b["result_state"] in analysis_mod.RESULT_STATES and b["result_state"] != "never_surveyed"


def test_a_step_with_no_results_view_is_its_own_state_not_a_missing_board(make):
    reg = make(AFTER_STORE)
    _survey(reg, AFTER_STORE, [("postgres_schema_and_stats", "ok")])
    b = _board(reg, "nested_column_profile")
    assert b["result_state"] == "no_results_view" and b["has_results"] is False
