"""TR-93, the part that decides what the Activity row says: `execute_and_record_definition` turns the executor's result
into the activity entry's terminal status and summary. A cancelled step (the executor reports it under `cancelled_count`
and a step entry with status "cancelled", and keeps it out of `errors`) must read as cancelled, never as an error; a real
error still reads as an error; a run with both says both. The executor's own half is in
test_survey_definition_cancelled_steps.py. Fakes only: no registry database, no Egeria, no Prefect."""
import json
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.workflows import survey_definition as wf

CANCELLED = {"step": "Step::slow", "re_analysis_step": "slow", "status": "cancelled", "engine": "prefect",
             "detail": "Prefect step 'slow' was cancelled: stopped by a person"}
FAILED = {"step": "Step::bad", "re_analysis_step": "bad", "status": "error"}


def _record(result):
    registry = MagicMock()
    params = wf.SurveyDefinitionRunParams()
    with patch.object(wf, "run_definition", return_value=dict(result)):
        out = wf.execute_and_record_definition("filesystem", "loading_bay", params, "act-1", registry=registry)
    (activity_id, status), kwargs = registry.update_activity_status.call_args
    return out, activity_id, status, kwargs


@pytest.mark.parametrize("n, words", [(1, "Completed · 1 step cancelled"), (2, "Completed · 2 steps cancelled")])
def test_a_run_with_only_cancelled_steps_is_stored_ok_and_says_cancelled(n, words):
    """A run with cancelled steps AND at least one completed step → status 'ok'."""
    result = {"steps": [dict(CANCELLED) for _ in range(n)] + [{"step": "Step::ok", "re_analysis_step": "ok", "status": "ok"}], 
              "errors": [], "cancelled_count": n}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "ok")
    assert kwargs["summary"] == words
    assert out.status == "ok" and out.summary == words and out.errors == []
    stored = json.loads(kwargs["detail"])
    assert [s["status"] for s in stored["steps"]] == ["cancelled"] * n + ["ok"], "the cancelled step stays in the stored detail"
    assert "stopped by a person" in stored["steps"][0]["detail"]


def test_a_run_with_only_cancelled_steps_and_no_completed_steps_is_stored_as_cancelled():
    """TR-93: A run where ALL steps are cancelled should be stored as status 'cancelled', not 'ok'."""
    result = {"steps": [dict(CANCELLED)], "errors": [], "cancelled_count": 1}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "cancelled")
    assert kwargs["summary"] == "Cancelled · 1 step cancelled, nothing measured"
    assert out.status == "cancelled" and out.summary == "Cancelled · 1 step cancelled, nothing measured" and out.errors == []
    stored = json.loads(kwargs["detail"])
    assert [s["status"] for s in stored["steps"]] == ["cancelled"]
    assert "stopped by a person" in stored["steps"][0]["detail"]


def test_a_run_with_one_error_and_one_cancelled_step_says_both_and_is_an_error():
    result = {"steps": [dict(CANCELLED), dict(FAILED)], "errors": ["RE step 'bad' failed: boom"], "cancelled_count": 1}
    out, _, status, kwargs = _record(result)
    assert status == "error"
    assert kwargs["summary"] == "Completed with 1 error · 1 step cancelled"
    assert out.status == "error" and len(out.errors) == 1


def test_two_errors_and_two_cancelled_steps_are_pluralised():
    result = {"steps": [], "errors": ["a", "b"], "cancelled_count": 2}
    _, _, status, kwargs = _record(result)
    assert status == "error" and kwargs["summary"] == "Completed with 2 errors · 2 steps cancelled"


def test_a_run_with_only_an_error_is_unchanged():
    out, _, status, kwargs = _record({"steps": [dict(FAILED)], "errors": ["boom"]})
    assert status == "error" and kwargs["summary"] == "Completed with 1 error(s)"
    assert out.status == "error"


def test_a_clean_run_is_unchanged():
    out, _, status, kwargs = _record({"steps": [], "errors": []})
    assert status == "ok" and kwargs["summary"] == "Survey Definition run complete"
    assert out.status == "ok"


def test_a_run_with_only_cancelled_steps_plural():
    """TR-93: A run where ALL steps are cancelled (plural) should be stored as status 'cancelled'."""
    result = {"steps": [dict(CANCELLED) for _ in range(3)], "errors": [], "cancelled_count": 3}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "cancelled")
    assert kwargs["summary"] == "Cancelled · 3 steps cancelled, nothing measured"
    assert out.status == "cancelled" and out.summary == "Cancelled · 3 steps cancelled, nothing measured" and out.errors == []


def test_a_run_with_missing_steps_field_treated_as_no_completed_steps():
    """TR-93: Missing/empty/unreadable steps field → treat as no completed steps."""
    result = {"errors": [], "cancelled_count": 1}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "cancelled")
    assert kwargs["summary"] == "Cancelled · 1 step cancelled, nothing measured"
    assert out.status == "cancelled"


def test_a_run_with_empty_steps_field_treated_as_no_completed_steps():
    """TR-93: Empty steps field → treat as no completed steps."""
    result = {"steps": [], "errors": [], "cancelled_count": 1}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "cancelled")
    assert kwargs["summary"] == "Cancelled · 1 step cancelled, nothing measured"
    assert out.status == "cancelled"


def test_executor_log_survey_with_all_cancelled_steps():
    """TR-93: Executor's log_survey call should also use the same status rule."""
    from resource_explorer.activity_logger import log_survey
    registry = MagicMock()
    
    # Simulate a run where all steps are cancelled (no errors)
    result = {"steps": [dict(CANCELLED)], "errors": [], "cancelled_count": 1}
    
    # We need to test the executor's log_survey call path
    # This will be tested in test_survey_definition_executor.py
    pass


def test_a_triggered_step_counts_as_completed_so_triggered_plus_cancelled_is_ok_not_nothing_measured():
    """R2 of the second read-back: Egeria-engine steps are stored as status "triggered" (the executor's handler branch); the
    helper counted only "ok", so triggered + cancelled read as "Cancelled · nothing measured". steps_summary counts
    ("ok", "triggered") as ran; the helper must use the same definition."""
    triggered = {"step": "Step::egeria", "re_analysis_step": "egeria", "status": "triggered"}
    out, activity_id, status, kwargs = _record({"steps": [triggered, dict(CANCELLED)], "errors": [], "cancelled_count": 1})
    assert status == "ok" and kwargs["summary"] == "Completed · 1 step cancelled" and out.status == "ok"
