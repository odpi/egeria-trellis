"""The whole Survey Definition, sequenced by Prefect rather than by RE.

These run the real flow in-process — Prefect 3 starts an ephemeral server, so
no standing deployment is needed and the ordering being asserted is Prefect's
own, not a simulation of it.

`re_survey_flow` (the older one) runs ONE step. With only a per-step entry
point the step order had nowhere to live but RE's `while` loop, which is how
RE ended up being the sequencer.
"""
import logging
from unittest.mock import patch

import pytest

from resource_explorer.prefect import flows

pytestmark = pytest.mark.usefixtures("ephemeral_prefect")


@pytest.fixture(autouse=True, scope="module")
def _quiet_prefect_logs():
    """Prefect's own run logs are noise here and drown the assertions on failure.

    Scoped to this module: a bare `logging.disable` at import time silenced INFO
    for every test collected after this file, so a later test reading logs off a
    module-logger handler passed alone and failed in the full suite.
    """
    logging.disable(logging.INFO)
    yield
    logging.disable(logging.NOTSET)


def _plan(*rows):
    return [{"step_key": k, "qualified_name": f"QN::{k}",
             "depends_on": list(deps), "guarded_by": dict(guards)}
            for k, deps, guards in rows]


def _run(plan, step_fn):
    with patch.object(flows.run_surveyor_step_task, "fn", staticmethod(step_fn)):
        return flows.re_survey_definition_flow("repo", "demo", plan, {})


def test_a_linear_plan_runs_every_step_in_order():
    ran = []

    def step(entity_type, slug, step_name, runner_kwargs):
        ran.append(step_name)
        return {}

    report = _run(_plan(("a", [], {}), ("b", ["a"], {}), ("c", ["b"], {})), step)
    assert ran == ["a", "b", "c"]
    assert [r["status"] for r in report] == ["ok", "ok", "ok"]


def test_a_branch_runs_only_the_side_whose_guard_was_emitted():
    """Conditional execution, decided from the guard the upstream produced —
    which is why the planner cannot decide it and the flow must."""
    ran = []

    def step(entity_type, slug, step_name, runner_kwargs):
        ran.append(step_name)
        return {"guard": "needs_deep"} if step_name == "triage" else {}

    report = _run(_plan(
        ("triage", [], {}),
        ("deep", ["triage"], {"triage": "needs_deep"}),
        ("quick", ["triage"], {"triage": "good_enough"}),
    ), step)

    assert ran == ["triage", "deep"]
    by_key = {r["step_key"]: r for r in report}
    assert by_key["deep"]["status"] == "ok"
    assert by_key["quick"]["status"] == "skipped"
    assert "good_enough" in by_key["quick"]["detail"]


def test_a_branch_not_taken_is_skipped_never_ok():
    """"Skipped" and "ok" must not be the same word: a branch not taken and a
    branch that ran are different facts about the survey."""
    report = _run(_plan(("t", [], {}), ("x", ["t"], {"t": "never"})),
                  lambda **kw: {})
    assert {r["step_key"]: r["status"] for r in report} == {"t": "ok", "x": "skipped"}


def test_a_failed_step_does_not_take_its_dependents_down_with_it_silently():
    """A dependent must not run on a result that never existed, and must say
    why it did not run."""
    def step(entity_type, slug, step_name, runner_kwargs):
        if step_name == "a":
            raise RuntimeError("boom")
        return {}

    report = _run(_plan(("a", [], {}), ("b", ["a"], {})), step)
    by_key = {r["step_key"]: r for r in report}
    assert by_key["a"]["status"] == "error" and "boom" in by_key["a"]["detail"]
    assert by_key["b"]["status"] == "skipped"
    assert "upstream" in by_key["b"]["detail"]


def test_an_independent_branch_still_runs_when_a_sibling_fails():
    """Failure propagates along edges, not across the whole flow."""
    def step(entity_type, slug, step_name, runner_kwargs):
        if step_name == "bad":
            raise RuntimeError("boom")
        return {}

    report = _run(_plan(("root", [], {}), ("bad", ["root"], {}),
                        ("good", ["root"], {})), step)
    by_key = {r["step_key"]: r for r in report}
    assert by_key["bad"]["status"] == "error"
    assert by_key["good"]["status"] == "ok"


def test_a_join_waits_for_both_upstreams():
    ran = []

    def step(entity_type, slug, step_name, runner_kwargs):
        ran.append(step_name)
        return {}

    _run(_plan(("a", [], {}), ("b", ["a"], {}), ("c", ["a"], {}),
               ("join", ["b", "c"], {})), step)
    assert ran.index("join") > max(ran.index("b"), ran.index("c"))


def test_the_report_reads_in_plan_order_not_completion_order():
    """Prefect may finish independent tasks in any order; the report should
    still read the way the definition was authored."""
    report = _run(_plan(("a", [], {}), ("b", ["a"], {}), ("c", ["a"], {})),
                  lambda **kw: {})
    assert [r["step_key"] for r in report] == ["a", "b", "c"]


def test_a_planner_added_step_runs_through_prefect_and_is_marked_in_the_report():
    """Prefect dispatches by step key, so a step the planner added (one the
    definition never authored) schedules like any other and reports why."""
    ran = []

    def step(entity_type, slug, step_name, runner_kwargs):
        ran.append(step_name)
        return {}

    note = "added producer: needed for needs_thing; no fresh result"
    plan = _plan(("producer", [], {}), ("consumer", ["producer"], {}))
    plan[0].update(added_because=note, demanded_by="consumer")
    report = _run(plan, step)
    assert ran == ["producer", "consumer"]
    assert report[0]["added_because"] == note and report[0]["detail"] == note
    assert report[0]["auto_run"] is True and report[0]["demanded_by"] == "consumer"
    assert "added_because" not in report[1]
