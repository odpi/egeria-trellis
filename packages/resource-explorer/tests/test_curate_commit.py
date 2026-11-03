"""curate_commit.execute_curation's `publish_asset` step (2026-09-20 freshness
gate) -- pressing Catalogue used to always run
`SurveyOrchestrator(...).run(slug, steps=None)`, a full unconditional
re-survey every time. See curate_commit.py's module docstring for the
project owner's framing this responds to. These tests cover the decision
logic in `_resurvey_plan` plus its wiring into `execute_curation`: only
previously-run, currently-stale analyses are re-surveyed; never-run
analyses are excluded even when technically "stale"; an all-fresh repo
still gets its asset created if one doesn't exist yet; a repo with no run
history at all still gets one full survey (nothing to be selective about
on a genuine first catalogue).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.activity_logger import log_analysis_run
from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.workflows.curate_commit import STEPS, _resurvey_plan, execute_curation


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    r.set_project_context(
        "repo", "p", status="linked",
        egeria_project_guid="proj-guid", egeria_project_qualified_name="Project::p",
    )
    return r


def _seed_run(registry, analysis_id: str, *, status: str = "succeeded", ago_seconds: int = 0):
    """Write one analysis_run activity row, same shape
    log_analysis_run/get_analysis_last_run use -- ts is stamped 'now' by the
    logger, so a fresh run is the default; `ago_seconds` back-dates it in the
    activity_log table directly to simulate a stale one."""
    activity_id = log_analysis_run(
        registry, "repo", "p", "P repo", status, f"ran {analysis_id}", analysis_id,
    )
    if ago_seconds:
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(seconds=ago_seconds)).isoformat()
        with registry._conn() as conn:
            conn.execute("UPDATE activity_log SET ts = ? WHERE id = ?", (ts, activity_id))
    return activity_id


def _fake_survey_result(slug="p", n_annotations=0):
    from resource_explorer.surveyors.survey_report import SurveyResult
    result = SurveyResult(resource_slug=slug, project_display_name="P repo",
                          github_url="https://github.com/x/p")
    result.annotations = [MagicMock() for _ in range(n_annotations)]
    return result


def _make_curation(registry, slug="p"):
    return Curations(registry).create(
        "repo", slug, author="peterprofile", selection={}, manifest={}, steps=list(STEPS),
    )["id"]


FRESH_SECONDS = 60  # well inside any reasonable RunsConfig.freshness_seconds
STALE_SECONDS = 60 * 60 * 24 * 365  # a year ago -- stale under any config


class TestResurveyPlan:
    def test_no_history_at_all_runs_full_survey(self, registry):
        steps, note = _resurvey_plan(registry, "p")
        assert steps is None
        assert "no run history" in note

    def test_mixed_stale_fresh_and_never_run_only_returns_stale_steps(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
            {"fresh_one": ["fresh_step"], "stale_one": ["stale_step_a", "stale_step_b"]},
        )
        _seed_run(registry, "fresh_one", ago_seconds=FRESH_SECONDS)
        _seed_run(registry, "stale_one", ago_seconds=STALE_SECONDS)
        # "never_run" has no row at all -- must never appear in the output,
        # even though an analysis with no last-run is trivially "not fresh".

        steps, note = _resurvey_plan(registry, "p")

        assert steps == ["stale_step_a", "stale_step_b"]
        assert "fresh_step" not in (steps or [])
        assert "1 of 2" in note or "1" in note  # 1 fresh, 1 stale, out of 2 with history

    def test_never_run_analyses_are_named_in_the_note_not_silently_dropped(self, registry, monkeypatch):
        """Designer review, 2026-09-20: the never-run set must be a visible
        state in the message, not rendered as absence -- otherwise a newly
        added analysis silently never reaches an existing, already-catalogued
        repository through Catalogue. Still never started (the ruling is
        unchanged); only the reporting was the gap."""
        monkeypatch.setattr(
            "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
            {"fresh_one": ["fresh_step"], "never_run": ["never_run_step"]},
        )
        _seed_run(registry, "fresh_one", ago_seconds=FRESH_SECONDS)

        steps, note = _resurvey_plan(registry, "p")

        assert steps == []
        assert "never_run_step" not in steps
        assert "1 analysis not yet run here and not started" in note

    def test_all_fresh_returns_empty_step_list(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
            {"fresh_one": ["fresh_step"]},
        )
        _seed_run(registry, "fresh_one", ago_seconds=FRESH_SECONDS)

        steps, note = _resurvey_plan(registry, "p")

        assert steps == []
        assert "already fresh" in note

    def test_error_last_run_counts_as_history_but_not_fresh(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
            {"flaky": ["flaky_step"]},
        )
        _seed_run(registry, "flaky", status="error", ago_seconds=FRESH_SECONDS)

        steps, note = _resurvey_plan(registry, "p")

        # Recorded moments ago, but an error run is never fresh -- eligible
        # for re-run even though its last attempt failed.
        assert steps == ["flaky_step"]


def _keep_survey(registry, steps=("stale_step", "fresh_step"), at="2026-10-07T01:00:00"):
    """A kept survey (brief section 1): the commit publishes THIS, it does not run one."""
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    for step in steps:
        survey_snapshot.record_step(registry, "p", step, at, [
            ClassificationAnnotation(summary=f"{step} result", analysis_step=step, check_name=step, item_key=step)])
    return at


def _publisher(report_guid="report-guid-1"):
    pub = MagicMock()
    pub.return_value.publish.return_value = report_guid
    pub.return_value.report_reused = False
    pub.return_value.get_survey_reports_by_guid.return_value = [
        {"guid": report_guid, "qualified_name": "SurveyReport::x", "annotation_count": 2}]
    return pub


def _seed_stale_and_fresh(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
        {"fresh_one": ["fresh_step"], "stale_one": ["stale_step"]},
    )
    _seed_run(registry, "fresh_one", ago_seconds=FRESH_SECONDS)
    _seed_run(registry, "stale_one", ago_seconds=STALE_SECONDS)


def _make_commit(registry, **selection):
    return Curations(registry).create(
        "repo", "p", author="peterprofile", selection=selection, manifest={}, steps=list(STEPS))["id"]


class TestExecuteCurationPublishAssetStep:
    """Brief section 1: the commit publishes the survey already kept. The re-survey box is off by default."""

    def test_unchecked_never_calls_the_orchestrator_and_publishes_the_kept_survey(self, registry, monkeypatch):
        _seed_stale_and_fresh(registry, monkeypatch)
        at = _keep_survey(registry)
        cid = _make_commit(registry)
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", _publisher()) as MockPub:
            MockPub.return_value.publish.side_effect = lambda r, **k: (
                registry.set_egeria_asset_guid("p", "asset-guid-1") or "report-guid-1")
            rec = execute_curation(registry, cid)
        MockOrch.assert_not_called()
        MockOrch.return_value.run.assert_not_called()
        sent = MockPub.return_value.publish.call_args[0][0]
        assert sorted(a.analysis_step for a in sent.annotations) == ["fresh_step", "stale_step"]
        step = next(s for s in rec["steps"] if s["name"] == "publish_asset")
        assert step["state"] == "done"
        assert "published · from the survey of 2026-10-07" in step["detail"]
        proof = [p for p in registry.list_catalogue_commit_proofs("p") if p["proof"] == "report_published"][-1]
        assert proof["detail"]["surveyed_at"] == at

    def test_checked_runs_exactly_the_stale_steps_then_publishes(self, registry, monkeypatch):
        _seed_stale_and_fresh(registry, monkeypatch)
        _keep_survey(registry)
        cid = _make_commit(registry, resurvey_stale=True)
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", _publisher()) as MockPub:
            MockOrch.return_value.run.return_value = MagicMock(errors=[], snapshot_error="")
            rec = execute_curation(registry, cid)
        MockOrch.return_value.run.assert_called_once_with("p", steps=["stale_step"])
        MockPub.return_value.publish.assert_called_once()
        step = next(s for s in rec["steps"] if s["name"] == "publish_asset")
        assert step["state"] == "done" and "re-surveyed 1 stale step(s) first" in step["detail"]

    def test_checked_with_nothing_stale_runs_nothing(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.surveyors.repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS",
            {"fresh_one": ["fresh_step"]})
        _seed_run(registry, "fresh_one", ago_seconds=FRESH_SECONDS)
        _keep_survey(registry, steps=("fresh_step",))
        cid = _make_commit(registry, resurvey_stale=True)
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", _publisher()):
            rec = execute_curation(registry, cid)
        MockOrch.return_value.run.assert_not_called()
        assert "nothing was stale" in next(s for s in rec["steps"] if s["name"] == "publish_asset")["detail"]

    def test_no_survey_blocks_the_commit_with_the_sentence_and_runs_nothing(self, registry):
        for selection in ({}, {"resurvey_stale": True}):
            cid = _make_commit(registry, **selection)
            with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
                 patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
                rec = execute_curation(registry, cid)
            MockOrch.return_value.run.assert_not_called()
            MockPub.return_value.publish.assert_not_called()
            step = next(s for s in rec["steps"] if s["name"] == "publish_asset")
            assert step["state"] == "failed" and "no survey to publish yet · run the first survey" in step["detail"]

    def test_a_repo_surveyed_before_kept_surveys_is_blocked_with_the_other_sentence(self, registry):
        registry.update_project_surveyed_at("p")
        cid = _make_commit(registry)
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
            rec = execute_curation(registry, cid)
        MockOrch.return_value.run.assert_not_called()
        MockPub.return_value.publish.assert_not_called()
        step = next(s for s in rec["steps"] if s["name"] == "publish_asset")
        assert step["state"] == "failed" and "surveyed before 10-07 · nothing kept to publish" in step["detail"]
        assert "first survey" not in step["detail"]
