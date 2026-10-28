"""Tests for Phase B's per-analysis results/trend routes:
GET /{slug}/analyses/{analysis_id}/results, GET /{slug}/analyses/{analysis_id}/trend.
"""
from __future__ import annotations

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


_ALL_5_ANALYSIS_IDS = [
    "dependency_analysis", "data_file_profiling", "security_scan",
    "documentation_coverage", "api_structure",
]


class TestResultsRoute:
    def test_unknown_repo_returns_404(self, client):
        resp = client.get("/api/projects/not-a-real-repo/analyses/security_scan/results")
        assert resp.status_code == 404

    def test_unmapped_analysis_id_returns_400(self, client):
        resp = client.get("/api/projects/myproj/analyses/not_a_real_analysis/results")
        assert resp.status_code == 400

    def test_repository_health_now_has_a_results_view(self, client):
        """It did not, and that was the bug: HealthSurveyor produced only
        in-memory annotations, so `repository_health` had no reader, its results
        were permanently null, and the Survey Results card naming it could never
        populate however often the survey ran."""
        resp = client.get("/api/projects/myproj/analyses/repository_health/results")
        assert resp.status_code == 200

    def test_an_id_with_no_results_view_still_400s(self, client):
        """The guard itself still works — not every analysis has a results
        view (egeria_publish is an action, not an analysis)."""
        resp = client.get("/api/projects/myproj/analyses/egeria_publish/results")
        assert resp.status_code == 400

    @pytest.mark.parametrize("analysis_id", _ALL_5_ANALYSIS_IDS)
    def test_empty_state_returns_200_not_error(self, client, analysis_id):
        # No survey has run yet for any of these — must return a clean empty
        # shape, not a crash.
        resp = client.get(f"/api/projects/myproj/analyses/{analysis_id}/results")
        assert resp.status_code == 200

    def test_dependency_results_grouped_by_ecosystem(self, client, registry):
        registry.upsert_dependencies("myproj", [
            {"dep_name": "flask", "dep_version": "1.0", "dep_type": "runtime", "ecosystem": "python", "source_file": "requirements.txt"},
            {"dep_name": "left-pad", "dep_version": "1.0", "dep_type": "runtime", "ecosystem": "npm", "source_file": "package.json"},
        ])
        resp = client.get("/api/projects/myproj/analyses/dependency_analysis/results")
        data = resp.json()
        assert data["total"] == 2
        assert set(data["by_ecosystem"].keys()) == {"python", "npm"}

    def test_security_results_reflect_latest_findings(self, client, registry):
        # security_scan reads the generic project_analysis_findings table
        # (kind="security_hygiene") now — analysis-kind extensibility redesign.
        registry.upsert_finding("myproj", "security_hygiene", [
            {"check_name": "license", "label": "pass", "summary": "License: MIT"},
        ])
        resp = client.get("/api/projects/myproj/analyses/security_scan/results")
        data = resp.json()
        assert data["gap_count"] == 0
        assert data["findings"][0]["check_name"] == "license"


class TestTrendRoute:
    def test_unknown_repo_returns_404(self, client):
        resp = client.get("/api/projects/not-a-real-repo/analyses/security_scan/trend?entity_type=repo")
        assert resp.status_code == 404

    def test_unmapped_analysis_id_returns_400(self, client):
        resp = client.get("/api/projects/myproj/analyses/not_a_real_analysis/trend?entity_type=repo")
        assert resp.status_code == 400

    @pytest.mark.parametrize("analysis_id", _ALL_5_ANALYSIS_IDS)
    def test_no_history_returns_empty_runs_not_error(self, client, analysis_id):
        resp = client.get(f"/api/projects/myproj/analyses/{analysis_id}/trend?entity_type=repo")
        assert resp.status_code == 200
        assert resp.json() == {"runs": []}

    def test_dependency_trend_reflects_two_runs(self, client, registry):
        registry.upsert_dependencies("myproj", [{"dep_name": "a", "dep_version": "", "dep_type": "runtime", "ecosystem": "python", "source_file": ""}])
        registry.upsert_dependencies("myproj", [
            {"dep_name": "a", "dep_version": "", "dep_type": "runtime", "ecosystem": "python", "source_file": ""},
            {"dep_name": "b", "dep_version": "", "dep_type": "runtime", "ecosystem": "python", "source_file": ""},
        ])
        resp = client.get("/api/projects/myproj/analyses/dependency_analysis/trend?entity_type=repo")
        runs = resp.json()["runs"]
        assert len(runs) == 2
        assert runs[0]["value"] == 1
        assert runs[1]["value"] == 2

    def test_security_trend_tracks_gap_count(self, client, registry):
        registry.upsert_finding(
            "myproj", "security_hygiene",
            [{"check_name": "license", "label": "gap", "summary": ""}], surveyed_at="2026-01-01T00:00:00",
        )
        registry.upsert_finding(
            "myproj", "security_hygiene",
            [{"check_name": "license", "label": "pass", "summary": ""}], surveyed_at="2026-01-02T00:00:00",
        )
        resp = client.get("/api/projects/myproj/analyses/security_scan/trend?entity_type=repo")
        runs = resp.json()["runs"]
        assert [r["value"] for r in runs] == [1, 0]
