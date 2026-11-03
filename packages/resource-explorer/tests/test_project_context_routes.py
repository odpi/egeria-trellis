"""Tests for POST /api/project-context/{entity_type}/{slug} — the Egeria Project context picker's
backend (Discovery-tier Part 5). (The GET and the candidate search were Classic's; Next reads the answer
through the publish gate and the investigation's Egeria Project.)
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class TestSetProjectContext:
    def test_personal_just_exploring(self, client, registry):
        resp = client.post("/api/project-context/repo/myproj", json={"status": "personal"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "personal"

        assert registry.get_project_context("repo", "myproj")["status"] == "personal"

    def test_declined_is_explicit_not_same_as_unset(self, client):
        resp = client.post("/api/project-context/repo/myproj", json={"status": "declined"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "declined"

    def test_deferred_requires_free_text_name(self, client):
        resp = client.post("/api/project-context/repo/myproj", json={"status": "deferred"})
        assert resp.status_code == 400

        resp2 = client.post(
            "/api/project-context/repo/myproj",
            json={"status": "deferred", "free_text_name": "Q3 Migration"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["free_text_name"] == "Q3 Migration"

    def test_linked_requires_egeria_project_guid(self, client):
        resp = client.post("/api/project-context/repo/myproj", json={"status": "linked"})
        assert resp.status_code == 400

        resp2 = client.post(
            "/api/project-context/repo/myproj",
            json={
                "status": "linked",
                "egeria_project_guid": "guid-1",
                "egeria_project_qualified_name": "Project::Foo::1.0",
            },
        )
        assert resp2.status_code == 200
        assert resp2.json()["egeria_project_guid"] == "guid-1"

    def test_invalid_status_rejected(self, client):
        resp = client.post("/api/project-context/repo/myproj", json={"status": "bogus"})
        assert resp.status_code == 400

    def test_entity_types_are_independent(self, client, registry):
        client.post("/api/project-context/repo/myproj", json={"status": "personal"})
        client.post("/api/project-context/database/myproj", json={"status": "declined"})
        assert registry.get_project_context("repo", "myproj")["status"] == "personal"
        assert registry.get_project_context("database", "myproj")["status"] == "declined"

