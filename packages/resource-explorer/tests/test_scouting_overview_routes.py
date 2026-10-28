"""Tests for the Scouting/Analysis boundary Phase A routes:
GET /{slug}/scouting-overview, POST /{slug}/scouting-scan,
POST /{slug}/analyses/{analysis_id}/run.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="A test repo.",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class TestScoutingOverview:
    def test_unknown_repo_returns_404(self, client):
        resp = client.get("/api/projects/not-a-real-repo/scouting-overview")
        assert resp.status_code == 404

    def test_returns_description_and_lifecycle_state_with_no_stats_yet(self, client):
        resp = client.get("/api/projects/myproj/scouting-overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "A test repo."
        assert data["last_surveyed_at"] == ""
        assert data["is_published"] is False
        assert data["stars"] == 0  # no project_stats row yet — defaults, not a crash

    def test_surfaces_project_stats_when_present(self, client, registry):
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_stats (project_slug, fetched_at, stars, forks, "
                "contributors_count, primary_language, last_pushed_at, repo_size_kb) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("myproj", "2026-08-01T00:00:00", 42, 7, 3, "Python", "2026-07-30T00:00:00", 1024),
            )
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["stars"] == 42
        assert data["primary_language"] == "Python"

    def test_reflects_survey_and_publish_state(self, client, registry):
        registry.update_project_surveyed_at("myproj")
        registry.set_egeria_asset_guid("myproj", "guid-1")
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["last_surveyed_at"] != ""
        assert data["is_published"] is True

    def test_last_published_at_empty_when_never_published(self, client):
        resp = client.get("/api/projects/myproj/scouting-overview")
        assert resp.json()["last_published_at"] == ""

    def test_last_published_at_reflects_most_recent_survey(self, client, registry):
        # is_published (a bare boolean, derived from whether egeria_asset_guid
        # is currently set) and last_published_at (a real timestamp, from
        # project_egeria_surveys) are deliberately separate fields — a
        # project can have one without necessarily reflecting the other in
        # sync, so both need their own explicit coverage.
        registry.record_egeria_survey("myproj", "2026-08-01T00:00:00", "guid-1")
        registry.record_egeria_survey("myproj", "2026-08-05T00:00:00", "guid-2")
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["last_published_at"] != ""
        # Newest surveyed_at's own published_at wins, not the oldest.
        first = registry.get_egeria_surveys("myproj")
        latest_by_surveyed_at = max(first, key=lambda s: s["surveyed_at"])
        assert data["last_published_at"] == latest_by_surveyed_at["published_at"]

    def test_last_profiled_at_empty_when_never_profiled(self, client):
        resp = client.get("/api/projects/myproj/scouting-overview")
        assert resp.json()["last_profiled_at"] == ""

    def test_last_profiled_at_reflects_a_profile_refresh(self, client, registry):
        registry.update_project_profiled_at("myproj")
        resp = client.get("/api/projects/myproj/scouting-overview")
        assert resp.json()["last_profiled_at"] != ""


class TestScoutingOverviewExtendedGitHubAttributes:
    """'display lifecycle state, homepage, security_and_analysis and
    get_deployments' — the four newly-surfaced Scouting-tier fields."""

    def test_defaults_with_no_stats_row(self, client):
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["lifecycle_state"] == "active"
        assert data["homepage"] == ""
        assert data["security_and_analysis"] == {}
        assert data["deployments_count"] == 0

    def _seed(self, registry, **overrides):
        cols = {
            "archived": 0, "disabled": 0, "is_fork": 0, "is_template": 0,
            "homepage": "", "security_and_analysis_json": "{}",
            "deployments_count": 0, "latest_deployment_at": "",
            "latest_deployment_environment": "", "latest_deployment_ref": "",
        }
        cols.update(overrides)
        with registry._conn() as conn:
            cols_sql = ", ".join(cols.keys())
            placeholders = ", ".join(["?"] * len(cols))
            conn.execute(
                f"INSERT INTO project_stats (project_slug, fetched_at, {cols_sql}) "
                f"VALUES (?, ?, {placeholders})",
                ("myproj", "2026-08-01T00:00:00", *cols.values()),
            )

    def test_archived_wins_lifecycle_state(self, client, registry):
        self._seed(registry, archived=1, is_fork=1)
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["lifecycle_state"] == "archived"

    def test_fork_lifecycle_state(self, client, registry):
        self._seed(registry, is_fork=1)
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["lifecycle_state"] == "fork"

    def test_homepage_and_security_and_analysis_surfaced(self, client, registry):
        self._seed(
            registry,
            homepage="https://example.com",
            security_and_analysis_json='{"secret_scanning": "enabled"}',
        )
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["homepage"] == "https://example.com"
        assert data["security_and_analysis"] == {"secret_scanning": "enabled"}

    def test_malformed_security_and_analysis_json_degrades_to_empty_dict(self, client, registry):
        self._seed(registry, security_and_analysis_json="not-json")
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["security_and_analysis"] == {}

    def test_deployments_surfaced(self, client, registry):
        self._seed(
            registry, deployments_count=3, latest_deployment_at="2026-08-01T00:00:00",
            latest_deployment_environment="prod", latest_deployment_ref="main",
        )
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["deployments_count"] == 3
        assert data["latest_deployment_environment"] == "prod"
        assert data["latest_deployment_ref"] == "main"

    def test_defaults_to_undecided_disposition(self, client):
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["disposition"] == "undecided"

    def test_reflects_a_set_disposition(self, client, registry):
        registry.set_disposition(
            "https://github.com/test/myproj", "investigating", reason="looks promising",
        )
        resp = client.get("/api/projects/myproj/scouting-overview")
        data = resp.json()
        assert data["disposition"] == "investigating"
        assert data["disposition_reason"] == "looks promising"


class TestScoutingScan:
    """The route itself is fire-and-forget (docs/survey-results-dashboard-plan.md-
    adjacent fix — a real, confirmed slowness bug meant Scouting Scan could
    block the HTTP request for 10+ minutes with zero visibility). It now
    only has to: 404 on an unknown repo, and otherwise start a background
    thread and return an activity_id immediately. The actual scan logic
    (_run_scouting_scan_sync) is unit-tested directly below, decoupled from
    HTTP/threading — same coverage the old synchronous route test had."""

    def test_unknown_repo_returns_404(self, client):
        resp = client.post("/api/projects/not-a-real-repo/scouting-scan")
        assert resp.status_code == 404

    def test_starts_a_background_scan_and_returns_immediately(self, client, registry):
        with patch("resource_explorer.web.routes.projects._run_scouting_scan_background"):
            resp = client.post("/api/projects/myproj/scouting-scan")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["slug"] == "myproj"
        entry = registry.get_activity(data["activity_id"])
        assert entry is not None
        assert entry["status"] == "running"
        assert entry["entity_slug"] == "myproj"


class TestRunScoutingScanSync:
    """Unit coverage for the actual scan logic, decoupled from the HTTP
    route/background thread — same cases the old synchronous route test
    covered before the fire-and-forget conversion."""

    def test_dispatches_to_run_survey_definition_with_coarse_scout_ref_and_fast_flag(self, registry):
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_COARSE_SCOUT_SURVEY_DEFINITION_QN,
        )
        from resource_explorer.web.routes.projects import _run_scouting_scan_sync

        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value={"errors": []},
        ) as mock_run:
            result = _run_scouting_scan_sync("myproj", registry)

        assert result.status == "ok"
        _, kwargs = mock_run.call_args
        assert kwargs["survey_definition_ref"] == REPO_COARSE_SCOUT_SURVEY_DEFINITION_QN
        # fast=True is the actual slowness fix — Coarse Scout must skip
        # StatsFetcher's N+1 per-commit diff-stats calls.
        assert kwargs["fast"] is True

    def test_errors_are_surfaced_not_raised(self, registry):
        from resource_explorer.web.routes.projects import _run_scouting_scan_sync

        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value={"errors": ["step failed"]},
        ):
            result = _run_scouting_scan_sync("myproj", registry)

        assert result.status == "error"


class TestScoutingScanMissingSurveyDefinitionFallback:
    """A dev Egeria database reset wipes out the 'Repo Coarse Scout' Survey
    Definition — a real, recurring event, not a one-off mistake. Without a
    fallback this hard-blocks Scouting Scan entirely, including the
    git-stats refresh HealthSurveyor does at scan time."""

    def test_falls_back_to_local_steps_when_survey_definition_missing(self, registry):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )
        from resource_explorer.web.routes.projects import _run_scouting_scan_sync

        fake_survey_result = MagicMock(errors=[])
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            side_effect=SurveyDefinitionExecutorError("No Survey Definition found matching '...'"),
        ), patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch:
            MockOrch.return_value.run.return_value = fake_survey_result
            result = _run_scouting_scan_sync("myproj", registry)

        assert result.status == "ok"
        assert "ran locally" in result.message
        MockOrch.return_value.run.assert_called_once_with(
            "myproj", steps=["repo_health", "repo_language", "repo_homepage"], fast=True,
        )

    def test_fallback_step_errors_are_surfaced(self, registry):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )
        from resource_explorer.web.routes.projects import _run_scouting_scan_sync

        fake_survey_result = MagicMock(errors=["repo_health failed: rate limited"])
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            side_effect=SurveyDefinitionExecutorError("not found"),
        ), patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch:
            MockOrch.return_value.run.return_value = fake_survey_result
            result = _run_scouting_scan_sync("myproj", registry)

        assert result.status == "error"
        assert "rate limited" in result.error

    def test_fallback_itself_failing_reports_both_errors(self, registry):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )
        from resource_explorer.web.routes.projects import _run_scouting_scan_sync

        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            side_effect=SurveyDefinitionExecutorError("not found"),
        ), patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch:
            MockOrch.return_value.run.side_effect = RuntimeError("clone missing")
            result = _run_scouting_scan_sync("myproj", registry)

        assert result.status == "error"
        assert "not found" in result.error
        assert "clone missing" in result.error


class TestScoutingScanBackground:
    """The record-the-outcome wrapper: on success, writes the workflow result's
    status/message onto the activity entry the route created; on an
    unexpected crash (not a survey-step error — a genuine bug), still
    resolves the entry to 'error' rather than leaving it 'running' forever.

    Now patched at `workflows.scouting.run_scouting_scan` rather than at the
    route module's `_run_scouting_scan_sync`: the workflow moved out of the
    route in step 2b, and the queue worker calls it directly — patching the
    route's own thin adapter would leave the path the queue actually takes
    untested."""

    def test_writes_ok_status_and_summary_onto_the_activity_entry(self, registry):
        from resource_explorer.activity_logger import log_survey
        from resource_explorer.web.routes.projects import _run_scouting_scan_background
        from resource_explorer.workflows.scouting import ScoutingScanResult

        activity_id = log_survey(
            registry, entity_type="repo", entity_slug="myproj", entity_name="My Project",
            entity_location="https://github.com/test/myproj", intent="scouting",
            status="running", summary="Scouting Scan running…",
        )

        with patch(
            "resource_explorer.workflows.scouting.run_scouting_scan",
            return_value=ScoutingScanResult(status="ok", slug="myproj", message="done"),
        ), patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            _run_scouting_scan_background("myproj", activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "ok"
        assert entry["summary"] == "done"

    def test_unexpected_crash_still_resolves_the_entry_to_error(self, registry):
        from resource_explorer.activity_logger import log_survey
        from resource_explorer.web.routes.projects import _run_scouting_scan_background

        activity_id = log_survey(
            registry, entity_type="repo", entity_slug="myproj", entity_name="My Project",
            entity_location="https://github.com/test/myproj", intent="scouting",
            status="running", summary="Scouting Scan running…",
        )

        with patch(
            "resource_explorer.workflows.scouting.run_scouting_scan",
            side_effect=RuntimeError("boom"),
        ), patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            _run_scouting_scan_background("myproj", activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "error"
        assert "boom" in entry["summary"]


class TestRunSingleAnalysis:
    """The route itself only validates and starts a background thread now
    (2026-08-30 — see docs/Backlog.md "the analysis-card Run gives no prompt
    and no progress for slow work"), mirroring run_scouting_scan/
    _run_scouting_scan_background exactly. Validation (404/400) stays
    synchronous and is tested through the route; the actual dispatch logic
    is tested by calling _run_single_analysis_background directly —
    through the live route it would race a `with patch(...)` block's exit
    against a real daemon thread, same reason TestScoutingScanBackground
    below tests its background function directly rather than through
    run_scouting_scan."""

    def test_unknown_repo_returns_404(self, client):
        resp = client.post("/api/projects/not-a-real-repo/analyses/security_scan/run")
        assert resp.status_code == 404

    def test_unmapped_analysis_id_returns_400(self, client):
        resp = client.post("/api/projects/myproj/analyses/not_a_real_analysis/run")
        assert resp.status_code == 400

    def test_publish_action_id_is_rejected_not_run(self, client):
        # egeria_publish is intentionally absent from REPO_ANALYSIS_STEP_MAP
        resp = client.post("/api/projects/myproj/analyses/egeria_publish/run")
        assert resp.status_code == 400

    def test_starts_a_background_run_and_returns_its_activity_id(self, client, registry):
        with patch("resource_explorer.web.routes.projects._run_single_analysis_background"):
            resp = client.post("/api/projects/myproj/analyses/security_scan/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["activity_id"]

        # The route's own synchronous half: a 'running' row exists before the
        # (here, patched-away) background thread would ever touch it.
        entry = registry.get_activity(data["activity_id"])
        assert entry["status"] == "running"
        assert entry["operation"] == "analysis_run"


class TestRunSingleAnalysisBackground:
    """_run_single_analysis_background/_run_single_analysis_sync: the
    dispatch and terminal-status logic run_single_analysis's route hands off
    to a daemon thread."""

    def test_dispatches_with_only_the_mapped_steps(self, registry):
        from resource_explorer.activity_logger import log_analysis_run
        from resource_explorer.web.routes.projects import _run_single_analysis_background

        activity_id = log_analysis_run(
            registry, "repo", "myproj", "My Project", "running",
            "Running 'security_scan' on myproj…", "security_scan",
        )
        fake_result = MagicMock(errors=[], annotations=["a", "b"])
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            MockOrch.return_value.run.return_value = fake_result
            _run_single_analysis_background("myproj", "security_scan", activity_id)

        MockOrch.return_value.run.assert_called_once_with("myproj", steps=["repo_security"])
        entry = registry.get_activity(activity_id)
        assert entry["status"] == "ok"

    def test_ingest_action_id_dispatches_to_incremental_indexer_not_survey_orchestrator(self, registry):
        # rag_ingestion (action:"ingest") isn't a SurveyOrchestrator step —
        # it re-embeds content into pgvector via IncrementalIndexer.
        from resource_explorer.activity_logger import log_analysis_run
        from resource_explorer.web.routes.projects import _run_single_analysis_background

        activity_id = log_analysis_run(
            registry, "repo", "myproj", "My Project", "running",
            "Running 'rag_ingestion' on myproj…", "rag_ingestion",
        )
        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer") as MockIndexer, \
             patch("resource_explorer.query_cache.QueryCache"), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            _run_single_analysis_background("myproj", "rag_ingestion", activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "ok"
        import json
        assert json.loads(entry["detail"])["analysis_id"] == "rag_ingestion"
        MockIndexer.return_value.refresh.assert_called_once()
        MockOrch.assert_not_called()

    def test_ingest_failure_is_surfaced_as_error_not_a_crash(self, registry):
        from resource_explorer.activity_logger import log_analysis_run
        from resource_explorer.web.routes.projects import _run_single_analysis_background

        activity_id = log_analysis_run(
            registry, "repo", "myproj", "My Project", "running",
            "Running 'rag_ingestion' on myproj…", "rag_ingestion",
        )
        with patch(
            "resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
            side_effect=RuntimeError("clone missing"),
        ), patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            _run_single_analysis_background("myproj", "rag_ingestion", activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "error"
        assert "clone missing" in entry["summary"]


class TestScoutingStatsUnread:
    """PI-050/051: the tiles say "not read" for a stat GitHub was never asked for, never 0."""

    def test_no_stats_row_lists_every_stat_as_unread(self, client):
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert set(data["stats_unread"]) == {
            "primary_language", "stars", "forks", "contributors", "last_pushed_at",
            "repo_size_kb", "security_and_analysis", "deployments_count"}

    def test_a_measured_zero_is_not_unread(self, client, registry):
        """Stars/forks 0 is a measurement (the fetch writes NULL when it did not read them)."""
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_stats (project_slug, fetched_at, stars, forks, "
                "contributors_count, primary_language, last_pushed_at, repo_size_kb, "
                "security_and_analysis_json, deployments_count) "
                "VALUES (?, ?, 0, 0, 1, 'Python', '2026-07-30T00:00:00', 2048, ?, 3)",
                ("myproj", "2026-08-01T00:00:00", '{"secret_scanning": "enabled"}'),
            )
        data = client.get("/api/projects/myproj/scouting-overview").json()
        assert data["stars"] == 0 and "stars" not in data["stats_unread"]
        assert data["stats_unread"] == []

    def test_default_zero_columns_prove_nothing(self, client, registry):
        """repo_size_kb and deployments_count are migrated in DEFAULT 0, so 0 cannot be told from never
        fetched. It reads "not read"; a non-zero value proves the fetch."""
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_stats (project_slug, fetched_at, stars, forks, repo_size_kb, deployments_count) "
                "VALUES (?, ?, 5, 1, 0, 0)", ("myproj", "2026-08-01T00:00:00"))
        unread = client.get("/api/projects/myproj/scouting-overview").json()["stats_unread"]
        assert "repo_size_kb" in unread and "deployments_count" in unread
        assert "stars" not in unread and "forks" not in unread

    def test_a_null_column_is_unread(self, client, registry):
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_stats (project_slug, fetched_at, stars, primary_language) "
                "VALUES (?, ?, NULL, 'Go')", ("myproj", "2026-08-01T00:00:00"))
        unread = client.get("/api/projects/myproj/scouting-overview").json()["stats_unread"]
        assert "stars" in unread and "primary_language" not in unread
