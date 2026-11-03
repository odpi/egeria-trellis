"""Quality-screens read item 10 (2026-10-30): a run's activity line read "2 of 3 step(s) ran", leaving the third step
unexplained and the plural in parentheses. It now counts every step under what happened to it, by its stored status."""
from resource_explorer.surveyors import result_status
from resource_explorer.surveyors.survey_definition_executor import steps_summary


def _steps(*statuses):
    return [{"step": f"s{i}", "status": s} for i, s in enumerate(statuses)]


def test_a_step_declined_by_design_is_said_as_such():
    assert steps_summary(_steps("ok", "ok", result_status.SKIPPED_BY_DESIGN), []) == "3 steps: 2 ran, 1 skipped by design"


def test_all_ran_and_one_step():
    assert steps_summary(_steps("ok", "triggered"), []) == "2 steps: all ran"
    assert steps_summary(_steps("ok"), []) == "1 step: ran"


def test_failures_and_errors_by_number_never_parenthetical():
    assert steps_summary(_steps("ok", "error", "cancelled"), ["boom"]) == "3 steps: 1 ran, 1 failed, 1 cancelled, 1 error"
    out = steps_summary(_steps("error", "error"), ["a", "b"])
    assert out == "2 steps: 2 failed, 2 errors" and "(s)" not in out


def test_absence_is_a_sentence():
    assert steps_summary([], []) == "no steps"
    assert steps_summary([{"step": "x"}], []) == "1 step: status not recorded"
    assert steps_summary(_steps("survey_invalid"), []) == "1 step: survey invalid"
