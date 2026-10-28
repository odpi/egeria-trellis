"""Tests for phase-scoped Egeria publish: POST /api/egeria/{slug}/publish's
optional `steps` field, threaded into SurveyOrchestrator.run(steps=...).

steps=None (the default/omitted case) must remain byte-for-byte the existing
full-survey behavior — this is the regression guard for that. A non-empty
steps list scopes both the survey and the published SurveyReport to just
those step keys (the mechanism the Scouting "Profile" tab's phase-scoped
publish buttons rely on).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture(autouse=True)
def _curation_access_open(monkeypatch):
    """Brief Z round 2: the publish route checks curation access first; here no zone is in use."""
    from tests.zone_fakes import permit_publish_routes

    return permit_publish_routes(monkeypatch)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
    ))
    # Egeria Project context (Part 5) gates the publish route — every test
    # in this file exercises what happens *after* that gate, so give it a
    # deliberate answer up front. TestPublishGate below covers the gate
    # itself (unset -> 428, and each of the 4 deliberate statuses -> proceeds).
    r.set_project_context("repo", "myproj", "personal")
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _fake_result():
    result = MagicMock()
    result.annotations = ["a", "b"]
    result.errors = []
    result.surveyed_at.isoformat.return_value = "2026-08-09T00:00:00"
    return result


class TestPublishStepsParam:
    def test_omitted_steps_defaults_to_full_survey(self, client):
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher"
        ) as MockPub:
            MockOrch.return_value.run.return_value = _fake_result()
            MockPub.return_value.publish.return_value = "report-guid-1"
            resp = client.post("/api/egeria/myproj/publish", json={})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        MockOrch.return_value.run.assert_called_once_with("myproj", steps=None)

    def test_no_body_also_defaults_to_full_survey(self, client):
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher"
        ) as MockPub:
            MockOrch.return_value.run.return_value = _fake_result()
            MockPub.return_value.publish.return_value = "report-guid-1"
            resp = client.post("/api/egeria/myproj/publish")

        assert resp.status_code == 200
        MockOrch.return_value.run.assert_called_once_with("myproj", steps=None)

    def test_explicit_steps_scope_the_survey(self, client):
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher"
        ) as MockPub:
            MockOrch.return_value.run.return_value = _fake_result()
            MockPub.return_value.publish.return_value = "report-guid-2"
            resp = client.post("/api/egeria/myproj/publish", json={"steps": ["repo_health"]})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        MockOrch.return_value.run.assert_called_once_with("myproj", steps=["repo_health"])

    def test_zone_names_still_pass_through_alongside_steps(self, client):
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher"
        ) as MockPub:
            MockOrch.return_value.run.return_value = _fake_result()
            MockPub.return_value.publish.return_value = "report-guid-3"
            resp = client.post(
                "/api/egeria/myproj/publish",
                json={"steps": ["repo_language", "repo_file_classification", "repo_file_structure", "repo_data_profiling"],
                      "zone_names": ["data-lake"]},
            )

        assert resp.status_code == 200
        MockOrch.return_value.run.assert_called_once_with(
            "myproj",
            steps=["repo_language", "repo_file_classification", "repo_file_structure", "repo_data_profiling"],
        )


class TestPublishGate:
    """Discovery-tier Part 5 — Egeria Project context gate. The publish
    route is the single funnel for every repo publish surface, so it's
    gated once here rather than per-frontend-button (see egeria.py's own
    comment at the gate)."""

    def test_unset_status_blocks_with_428(self, client, registry):
        registry.set_project_context("repo", "myproj", "unset")
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            resp = client.post("/api/egeria/myproj/publish", json={})

        assert resp.status_code == 428
        body = resp.json()
        assert body["detail"] == "egeria_project_context_required"
        assert body["entity_type"] == "repo"
        assert body["entity_slug"] == "myproj"
        MockOrch.return_value.run.assert_not_called()

    def test_no_context_row_at_all_also_blocks(self, client, registry):
        # Distinct from an explicit status="unset" row — never having
        # answered at all must gate identically, not silently pass through.
        with registry._conn() as conn:
            conn.execute(
                "DELETE FROM entity_egeria_project_context WHERE entity_type='repo' AND entity_slug='myproj'"
            )
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            resp = client.post("/api/egeria/myproj/publish", json={})

        assert resp.status_code == 428
        MockOrch.return_value.run.assert_not_called()

    @pytest.mark.parametrize("status", ["personal", "linked", "deferred", "declined"])
    def test_any_deliberate_status_proceeds(self, client, registry, status):
        registry.set_project_context("repo", "myproj", status)
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator"
        ) as MockOrch, patch(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher"
        ) as MockPub:
            MockOrch.return_value.run.return_value = _fake_result()
            MockPub.return_value.publish.return_value = "report-guid-gate"
            resp = client.post("/api/egeria/myproj/publish", json={})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        MockOrch.return_value.run.assert_called_once()
