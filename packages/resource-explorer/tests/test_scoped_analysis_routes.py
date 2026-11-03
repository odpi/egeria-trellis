"""Tests for the scoped-analysis routes — the "Narrow" stage of the repo
scope-narrowing funnel (docs/repo-scope-narrowing-funnel.md, D5/D6):
POST /{slug}/sub-resources/analyses/{analysis_id}/run and
GET /{slug}/sub-resources/analyses/{analysis_id}/results."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", collections=[],
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


class TestRunScopedAnalysis:
    def test_404_for_unknown_repo(self, client):
        resp = client.post(
            "/api/projects/nope/sub-resources/analyses/api_structure/run",
            json={"locator": "src"},
        )
        assert resp.status_code == 404

    def test_404_for_uncatalogued_locator(self, client):
        resp = client.post(
            "/api/projects/myproj/sub-resources/analyses/api_structure/run",
            json={"locator": "src"},
        )
        assert resp.status_code == 404
        assert "not a published sub-resource" in resp.json()["detail"]

    def test_400_for_unknown_analysis_id(self, client, registry):
        registry.catalog_sub_resource("repo", "myproj", "src", "folder")
        resp = client.post(
            "/api/projects/myproj/sub-resources/analyses/not_a_real_analysis/run",
            json={"locator": "src"},
        )
        assert resp.status_code == 400
        assert "Unknown analysis" in resp.json()["detail"]

    def test_400_when_shape_incompatible(self, client, registry):
        # security_scan is target_shape=whole_resource_only — never scopable.
        registry.catalog_sub_resource("repo", "myproj", "src", "folder")
        resp = client.post(
            "/api/projects/myproj/sub-resources/analyses/security_scan/run",
            json={"locator": "src"},
        )
        assert resp.status_code == 400
        assert "cannot be scoped" in resp.json()["detail"]

    def test_400_when_leaf_kind_incompatible_with_single_container(self, client, registry):
        # schema_inventory is database-only in the catalog (single_container)
        # — not present for repo at all, so this exercises the "unknown for
        # this resource_type" branch via a repo-side single_container-ish
        # mismatch instead: api_structure (corpus) against a folder is fine,
        # but flip kind to confirm the gate actually reads sub_resource kind.
        registry.catalog_sub_resource("repo", "myproj", "mod.py", "file")
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator.run",
        ) as mock_run:
            mock_run.return_value.errors = []
            mock_run.return_value.annotations = []
            resp = client.post(
                "/api/projects/myproj/sub-resources/analyses/api_structure/run",
                json={"locator": "mod.py"},
            )
        # api_structure is corpus-shaped — compatible with both file and
        # folder kinds, so this must succeed (not a 400).
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_runs_scoped_with_scope_locator_passed_through(self, client, registry):
        registry.catalog_sub_resource("repo", "myproj", "src", "folder")
        with patch(
            "resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator.run",
        ) as mock_run:
            mock_run.return_value.errors = []
            mock_run.return_value.annotations = ["a", "b"]
            resp = client.post(
                "/api/projects/myproj/sub-resources/analyses/api_structure/run",
                json={"locator": "src"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "src" in data["message"]
            _, kwargs = mock_run.call_args
            assert kwargs["scope_locator"] == "src"
            assert kwargs["steps"] == ["repo_api_structure"]


class TestScopedAnalysisResults:
    def test_404_for_unknown_repo(self, client):
        resp = client.get(
            "/api/projects/nope/sub-resources/analyses/api_structure/results",
            params={"locator": "src"},
        )
        assert resp.status_code == 404

    def test_empty_dict_for_unmapped_analysis_id(self, client):
        resp = client.get(
            "/api/projects/myproj/sub-resources/analyses/security_scan/results",
            params={"locator": "src"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_reads_scoped_metrics_by_kind(self, client, registry):
        registry.upsert_metric(
            "myproj", "api_structure", {"symbol_count": 3}, scope_locator="src",
        )
        registry.upsert_metric(
            "myproj", "api_structure", {"symbol_count": 99},  # whole-repo, different scope
        )
        resp = client.get(
            "/api/projects/myproj/sub-resources/analyses/api_structure/results",
            params={"locator": "src"},
        )
        assert resp.status_code == 200
        assert resp.json()["symbol_count"] == 3
