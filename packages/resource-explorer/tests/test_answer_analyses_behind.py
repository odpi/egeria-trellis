"""Walk 2026-10-12: "Is this repository actively maintained?" is answered from the resource's own state, so its
envelope carries one fact (foss_scorecard). The chat listed repository_health, which the catalog also puts behind
the question, as "not run" while Enrichment showed "Health 85/100 · 4d ago". The envelope now reports the analyses
behind the question, each read by FactLayer.fact (the function Enrichment's bulk read uses)."""
from __future__ import annotations

import json

import pytest

from resource_explorer.facts import FactLayer
from resource_explorer.registry import ActivityEntry, Project, ProjectRegistry
from resource_explorer.surveyors.question_catalog_reader import get_questions

QUESTION = "Is this repository actively maintained?"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    r.add(Project(slug="m", display_name="m", github_url="https://github.com/x/m"))
    return r


def _question():
    return next(q for q in get_questions("repo") if q["question"] == QUESTION)


def test_an_analysis_that_ran_is_reported_as_run_beside_the_state_fact(registry):
    registry.write_activity(ActivityEntry(
        id="a1", ts="2026-10-08T10:00:00", operation="analysis_run", intent="scouting", entity_type="repo",
        entity_slug="m", detail=json.dumps({"analysis_id": "repository_health"})))
    layer = FactLayer(registry=registry)
    env = layer.answer("m", _question())
    assert [f.analysis_id for f in env.facts] == ["foss_scorecard"], "the premise: facts holds only the state fact"
    behind = {b["analysis_id"]: b for b in env.as_dict()["analyses_behind"]}
    assert behind["repository_health"]["last_run_at"] == "2026-10-08T10:00:00"
    assert behind["repository_health"]["last_run_at"] == layer.fact("m", "repository_health").last_run_at
    assert behind["foss_scorecard"]["last_run_at"] == ""


def test_an_analysis_that_never_ran_is_not_reported_as_run(registry):
    env = FactLayer(registry=registry).answer("m", _question())
    assert all(not b["last_run_at"] and not b["is_known"] for b in env.analyses_behind)
