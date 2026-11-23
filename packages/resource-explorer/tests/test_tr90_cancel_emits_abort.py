"""TR-90: a cancelled step's OpenLineage run event is ABORT, not FAIL.

A step whose flow run was cancelled stops with `run_cancellation.RunCancelled` (TR-62b), and TR-93 counts it as cancelled,
not as an error. Its run event said FAIL with an errorMessage, so a lineage consumer read a person's cancel as a broken job.
OpenLineage's ABORT is "the job was stopped abnormally": that is what happened.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from resource_explorer.observability import openlineage
from resource_explorer.surveyors import step_cost_observer as sco
from resource_explorer.surveyors.run_cancellation import RunCancelled


@pytest.fixture(autouse=True)
def _file_transport(tmp_path, monkeypatch):
    for k in ("RE_OPENLINEAGE_URL", "RE_OPENLINEAGE_TOKEN", "RE_OPENLINEAGE_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))


def _events(path):
    return [json.loads(line) for line in (path / "openlineage-events.jsonl").read_text().splitlines()]


def _run_raising(exc, step="s1"):
    with pytest.raises(type(exc)):
        with sco.observe(step, "none", "low", entity_type="repo", slug="r", job_family="analysis"):
            raise exc


def test_a_cancelled_step_emits_start_then_abort(tmp_path):
    _run_raising(RunCancelled("fr-1", "keeping the survey snapshot"))
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "ABORT"]


def test_the_abort_closes_the_same_run_with_an_end_time_and_no_error_facet(tmp_path):
    _run_raising(RunCancelled("fr-1", "starting step s1"))
    start, abort = _events(tmp_path)
    assert start["run"]["runId"] == abort["run"]["runId"]
    assert abort["run"]["facets"]["nominalTime"]["nominalEndTime"]
    assert "errorMessage" not in abort["run"]["facets"]          # a cancel is not an error (TR-93)


def test_an_asyncio_cancel_is_an_abort_too(tmp_path):
    _run_raising(asyncio.CancelledError())
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "ABORT"]


def test_a_nested_step_cancelled_under_its_demander_aborts_both(tmp_path):
    with pytest.raises(RunCancelled):
        with sco.observe("outer", "none", "low", entity_type="repo", slug="r"):
            with sco.observe("inner", "none", "low", entity_type="repo", slug="r"):
                raise RunCancelled("fr-2", "recording the work row of inner")
    assert [(e["job"]["name"], e["eventType"]) for e in _events(tmp_path)] == [
        ("analysis.outer", "START"), ("analysis.inner", "START"),
        ("analysis.inner", "ABORT"), ("analysis.outer", "ABORT")]


def test_an_ordinary_error_is_still_fail(tmp_path):
    _run_raising(ValueError("boom"))
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "FAIL"]


def test_a_noted_failure_is_still_fail(tmp_path):
    with sco.observe("s1", "none", "low", entity_type="repo", slug="r"):
        openlineage.note_failure("surveyor raised: nope")
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "FAIL"]


def test_a_clean_step_is_still_complete(tmp_path):
    with sco.observe("s1", "none", "low", entity_type="repo", slug="r"):
        pass
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "COMPLETE"]


def test_the_step_row_still_counts_both_events_as_delivered(tmp_path):
    with pytest.raises(RunCancelled):
        with sco.observe("s1", "none", "low", entity_type="repo", slug="r") as out:
            raise RunCancelled("fr-3", "starting step s1")
    assert out[0].openlineage["emitted"] == 2 and out[0].openlineage["failed"] == 0
