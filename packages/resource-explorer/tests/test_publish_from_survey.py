"""Brief section 1 (project owner, 2026-10-07): publish publishes the survey the person decided on.

"We shouldn't have to survey again before publishing." A publish never calls the orchestrator; the
Curate commit with its re-survey box unchecked never calls it; with the box checked it runs exactly
the stale steps; no survey blocks both with the sentence; the proof row names `surveyed_at`; "Re-survey
now" runs the survey and nothing else. Fakes record every call; nothing reaches Egeria.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer import repo_publish as rp
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors import survey_snapshot as ss
from resource_explorer.surveyors.survey_report import (
    ClassificationAnnotation, DataClassAnnotation, RequestForActionAnnotation, ResourceMeasureAnnotation)


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")

SENTENCE = "no survey to publish yet · run the first survey"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj"))
    return r


def _ann(cls=ClassificationAnnotation, **kw):
    base = dict(summary="Python repo", analysis_step="repo_language", check_name="language", item_key="py",
                confidence=90, json_properties={"files": 12, "when": datetime(2026, 10, 1)})
    base.update(kw)
    return cls(**base)


def _keep(registry, step="repo_language", at=None, anns=None):
    at = at or datetime.utcnow().isoformat()
    ss.record_step(registry, "myproj", step, at, anns if anns is not None else [_ann()])
    return at


class FakePublisher:
    """Records the publish; caches the asset GUID like the real one."""
    instances: list = []

    def __init__(self, registry=None, **kw):
        self.registry = registry
        self.published = []
        self.report_reused = False
        FakePublisher.instances.append(self)

    def publish(self, result, **kw):
        self.published.append(result)
        self.registry.set_egeria_asset_guid("myproj", "asset-1")
        return "rep-1"

    def get_survey_reports_by_guid(self, guid):
        return [{"guid": "rep-1", "qualified_name": "SurveyReport::x", "annotation_count": len(self.published[-1].annotations)}]


@pytest.fixture
def world(registry, monkeypatch):
    """A fake publisher, and an orchestrator that records being called."""
    FakePublisher.instances = []
    monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", FakePublisher)
    orch = MagicMock()
    orch.return_value.run.side_effect = lambda slug, steps=None, **k: MagicMock(
        errors=[], snapshot_error="", annotations=[], surveyed_at=datetime.utcnow())
    monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator", orch)
    return orch


# ── the kept survey ────────────────────────────────────────────────────────

def test_a_step_round_trips_with_its_class_fields_and_an_empty_step_is_kept_as_ran(registry):
    at = _keep(registry, "repo_language", anns=[_ann(), _ann(DataClassAnnotation, summary="dep",
               candidate_data_class_names=["pypi"]), _ann(RequestForActionAnnotation, action_requested="add CI")])
    ss.record_step(registry, "myproj", "repo_security", at, [])      # ran, found nothing
    snap = ss.latest(registry, "myproj")
    assert snap.step_count == 2 and snap.annotation_count == 3 and snap.surveyed_at == at
    result = ss.to_result(registry.get("myproj"), snap)
    kinds = sorted(type(a).__name__ for a in result.annotations)
    assert kinds == ["ClassificationAnnotation", "DataClassAnnotation", "RequestForActionAnnotation"]
    dc = next(a for a in result.annotations if isinstance(a, DataClassAnnotation))
    assert dc.candidate_data_class_names == ["pypi"]
    assert result.steps_run == ["repo_language", "repo_security"]       # the empty step DID run
    assert ss.latest(registry, "other") is None


def test_the_orchestrator_keeps_each_completed_step_and_a_failed_step_keeps_its_old_result(registry):
    from tests.test_survey_orchestrator import _patch_all_surveyors
    registry.add(Project(slug="o", display_name="O", github_url="https://github.com/t/o"))
    mocks, patchers = _patch_all_surveyors()
    try:
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator
        mocks["repo_language"].return_value.run.return_value = [_ann()]
        mocks["repo_health"].return_value.run.return_value = []
        with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
            SurveyOrchestrator(registry).run("o", steps=["repo_language", "repo_health"])
            snap = ss.latest(registry, "o")
            assert set(snap.steps) == {"repo_language", "repo_health"}
            assert snap.steps["repo_health"]["annotations"] == []
            mocks["repo_language"].return_value.run.side_effect = RuntimeError("boom")
            out = SurveyOrchestrator(registry).run("o", steps=["repo_language"])
            assert "repo_language" in out.step_errors
            assert len(ss.latest(registry, "o").steps["repo_language"]["annotations"]) == 1, "an absence overwrote a result"
            # a scoped run is not the repository's survey
            before = ss.latest(registry, "o").surveyed_at
            mocks["repo_language"].return_value.run.side_effect = None
            mocks["repo_language"].return_value.run.return_value = [_ann(summary="scoped")]
            SurveyOrchestrator(registry).run("o", steps=["repo_language"], scope_locator="sub/dir")
            assert ss.latest(registry, "o").steps["repo_language"]["annotations"][0]["summary"] == "Python repo"
            assert ss.latest(registry, "o").surveyed_at == before
    finally:
        for p in patchers:
            p.stop()


# ── publish never surveys ──────────────────────────────────────────────────

def test_a_publish_sends_the_kept_survey_and_never_calls_the_orchestrator(registry, world):
    at = _keep(registry)
    out = rp.publish_report(registry, "myproj", "dan", without_project=True)
    assert out["ok"] is True
    world.assert_not_called()
    world.return_value.run.assert_not_called()
    sent = FakePublisher.instances[0].published[0]
    assert len(sent.annotations) == 1 and sent.surveyed_at.isoformat() == at
    assert sent.steps_run == ["repo_language"]


def test_the_proof_row_names_surveyed_at_and_reused(registry, world):
    at = _keep(registry)
    rp.publish_report(registry, "myproj", "dan", without_project=True)
    proof = [p for p in registry.list_catalogue_commit_proofs("myproj") if p["proof"] == rp.P_REPORT][-1]
    assert proof["detail"]["surveyed_at"] == at and proof["detail"]["reused"] is False
    assert proof["detail"]["annotation_count"] == 1 and proof["detail"]["steps"] == 1
    row = rp.publish_state(registry, "myproj")["row"]
    assert row["word"] == "published" and row["surveyed_at"] == at


def test_no_survey_blocks_the_publish_with_the_sentence_and_sends_nothing(registry, world):
    out = rp.publish_report(registry, "myproj", "dan", without_project=True)
    assert out == {"gate": "no_survey", "sentence": SENTENCE, "sent": {}}, "not in Egeria: nothing else sent either"
    assert FakePublisher.instances == [] and registry.list_catalogue_commit_proofs("myproj") == []
    world.return_value.run.assert_not_called()


def test_the_state_says_which_survey_it_would_publish_and_its_age(registry, world):
    assert rp.publish_state(registry, "myproj")["survey"] == {"exists": False, "surveyed_before_kept": False, "sentence": SENTENCE}
    old = (datetime.utcnow() - timedelta(hours=5)).isoformat()
    _keep(registry, "repo_language", at=old, anns=[_ann(), _ann()])
    _keep(registry, "repo_health", at=old, anns=[])
    s = rp.publish_state(registry, "myproj")["survey"]
    assert s["exists"] and s["annotations"] == 2 and s["steps"] == 2 and s["surveyed_at"] == old
    assert 5 * 3600 - 60 < s["age_seconds"] < 5 * 3600 + 60


def test_the_stale_steps_are_named_not_run(registry, world, monkeypatch):
    _keep(registry, "repo_language")
    _keep(registry, "repo_health")
    monkeypatch.setattr("resource_explorer.workflows.curate_commit._resurvey_plan",
                        lambda reg, slug: (["repo_health", "repo_other"], "note"))
    s = rp.publish_state(registry, "myproj")["survey"]
    assert s["stale"] == ["repo_health"] and s["stale_steps"] == 1       # a step with no kept result is not "stale"
    world.return_value.run.assert_not_called()


def test_resurvey_runs_the_survey_and_publishes_nothing(registry, world):
    _keep(registry)
    out = rp.resurvey(registry, "myproj", "dan")
    world.return_value.run.assert_called_once_with("myproj", steps=None)
    assert out["ok"] is True and out["survey"]["exists"]
    assert FakePublisher.instances == [], "a re-survey must not publish"
    assert registry.list_catalogue_commit_proofs("myproj") == []


def test_resurvey_reports_a_survey_that_could_not_be_kept(registry, world):
    world.return_value.run.side_effect = lambda slug, steps=None, **k: MagicMock(
        errors=[], snapshot_error="the survey could not be kept for publishing (x): disk", annotations=[])
    out = rp.resurvey(registry, "myproj", "dan")
    assert out["ok"] is False and "could not be kept" in out["errors"][0]


# ── the routes ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr("resource_explorer.web.routes.egeria.get_current_user", lambda request: {"user_id": "dan"})
    from resource_explorer.web.app import app
    return TestClient(app)


def test_the_publish_route_answers_409_with_the_sentence_when_nothing_was_surveyed(client, world):
    r = client.post("/api/egeria/myproj/publish-report", json={"without_project": True})
    assert r.status_code == 409 and r.json()["detail"] == SENTENCE
    world.return_value.run.assert_not_called()


def test_the_resurvey_route_runs_only_the_survey(client, registry, world):
    r = client.post("/api/egeria/myproj/resurvey", json={"steps": ["repo_health"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    world.return_value.run.assert_called_once_with("myproj", steps=["repo_health"])
    assert FakePublisher.instances == []


def test_publish_then_state_read_through_the_route_shows_the_kept_survey(client, registry, world):
    at = _keep(registry)
    assert client.post("/api/egeria/myproj/publish-report", json={"without_project": True}).status_code == 200
    state = client.get("/api/egeria/myproj/publish-state").json()
    assert state["survey"]["surveyed_at"] == at and state["row"]["word"] == "published"
    world.return_value.run.assert_not_called()


def test_the_commit_route_is_blocked_with_the_sentence_when_nothing_was_surveyed(client, registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
    monkeypatch.setattr("resource_explorer.curate_plan.build_plan", lambda reg, slug: {
        "in_population": True, "disposition": "using", "what_it_is": [{"kind": "Endpoint", "candidate": True}],
        "writes": {"contained": {"data_files": 0}}})
    r = client.post("/api/projects/myproj/curate/commit", json={"confirm": ["Endpoint"]})
    assert r.status_code == 409 and r.json()["detail"] == SENTENCE
    assert registry.list_runs() == [], "a blocked commit queued a run"
    from resource_explorer.curate_plan import Curations
    assert Curations(registry).for_resource("repo", "myproj") == [], "a blocked commit left a record"


BEFORE = "surveyed before 10-07 · nothing kept to publish · re-survey to publish"


def test_never_surveyed_keeps_the_first_survey_sentence(registry):
    st = rp.survey_state(registry, "myproj")
    assert st == {"exists": False, "surveyed_before_kept": False, "sentence": SENTENCE}


def test_surveyed_before_kept_surveys_says_so_from_the_same_column_the_header_reads(registry, world):
    registry.update_project_surveyed_at("myproj")        # projects.last_surveyed_at: the header's "surveyed <ago>"
    assert registry.get("myproj").last_surveyed_at
    st = rp.survey_state(registry, "myproj")
    assert st == {"exists": False, "surveyed_before_kept": True, "sentence": BEFORE}
    out = rp.publish_report(registry, "myproj", "dan", without_project=True)
    assert out == {"gate": "no_survey", "sentence": BEFORE, "sent": {}}
    assert FakePublisher.instances == []


def test_a_kept_survey_is_neither_sentence(registry):
    registry.update_project_surveyed_at("myproj")
    _keep(registry)
    st = rp.survey_state(registry, "myproj")
    assert st["exists"] is True and "sentence" not in st and "surveyed_before_kept" not in st
