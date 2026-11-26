"""TR-104: repo_refresh_plan's fetch_cost declaration says what it really does.

It declared nothing, so it took StepInfo's default `fetch_cost="none"`, and every run on laz logged the step-cost
disagreement "declares fetch_cost='none' but opened N connection(s)". Measured read-only on laz's registry (step_runs,
2026-11-23T20:48Z): 703 recorded runs from 2026-09-24 to 2026-11-23, every one made exactly 2 API calls, all to
api.github.com:443 (`get_repo` + `get_latest_commit_sha`, the head commit the plan compares against); the crude socket
count was 1 to 25, median 3. The step needs the head commit, so it cannot stop fetching: the declaration becomes "api".

A consequence pinned here: a `max_fetch_cost="none"` run (bulk republish: "keeps it to stored data") no longer runs the
planner, which until now made two GitHub calls in a run that promised none.
"""
from __future__ import annotations

from resource_explorer.surveyors import step_cost_observer as sco
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
from resource_explorer.surveyors.sub_surveyors import refresh_plan as rp
from resource_explorer.surveyors.survey_orchestrator import _cost_orders

STEP = "repo_refresh_plan"


def test_the_planner_declares_api():
    assert STEP_REGISTRY[STEP].fetch_cost == "api"


def test_the_measured_runs_no_longer_disagree_with_the_declaration():
    info = STEP_REGISTRY[STEP]
    for connects in (1, 3, 25):                                                   # laz's min, median and max
        obs = sco.Observation(STEP, 1.7, connects, info.fetch_cost, info.compute_cost)
        assert sco._disagreement(obs) == ""


def test_the_head_lookup_is_the_two_github_calls_that_were_measured():
    calls = []

    class _Client:
        def get_repo(self, url):
            calls.append(("get_repo", url))
            return "repo"

        def get_latest_commit_sha(self, repo):
            calls.append(("get_latest_commit_sha", repo))
            return "abc123"

    class _Project:
        github_url = "https://github.com/odpi/egeria"

    assert rp.head_sha(_Project(), client=_Client()) == ("abc123", "")
    assert [c[0] for c in calls] == ["get_repo", "get_latest_commit_sha"]


def _selected(max_fetch_cost: str) -> set[str]:
    order, _compute = _cost_orders()
    ceiling = order.index(max_fetch_cost)
    return {k for k, v in STEP_REGISTRY.items() if order.index(v.fetch_cost) <= ceiling}


def test_a_zero_fetch_run_leaves_the_planner_out_and_an_api_run_keeps_it():
    assert STEP not in _selected("none")
    assert STEP in _selected("api")
