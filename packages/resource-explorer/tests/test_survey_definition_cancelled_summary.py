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
    result = {"steps": [dict(CANCELLED) for _ in range(n)], "errors": [], "cancelled_count": n}
    out, activity_id, status, kwargs = _record(result)
    assert (activity_id, status) == ("act-1", "ok")
    assert kwargs["summary"] == words
    assert out.status == "ok" and out.summary == words and out.errors == []
    stored = json.loads(kwargs["detail"])
    assert [s["status"] for s in stored["steps"]] == ["cancelled"] * n, "the cancelled step stays in the stored detail"
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
