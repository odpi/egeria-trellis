"""TR-95: the Survey Definition run route checks `start_action`, for every entity type.

The individual Run routes refuse a caller who may not start actions on the resource
(`require_resource_write(..., "start_action")`). The definition run route checked only `publish`, and only when a
Project was assigned, so the same caller could start the same work by running a definition. Owner's ruling
(2026-11-20): "Check start_action, all types".
"""
from __future__ import annotations

import types
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

BODY = {"survey_definition_ref": "RepoFullSurvey"}
TYPES = ("repo", "database", "filesystem")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "tr95.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj", description="A test repo."))

    def _factory(*a, **k):
        return r

    _factory.RUN_STATES = ProjectRegistry.RUN_STATES
    _factory.RUN_KINDS = ProjectRegistry.RUN_KINDS
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", _factory)
    return r


@pytest.fixture
def client(registry):
    from resource_explorer.web.app import app

    with patch("resource_explorer.workflows.survey_definition.run_definition"):
        yield TestClient(app)


def _decider(monkeypatch, *, deny: set[str]):
    """Replace the access decision; record each (entity_type, slug, write) asked."""
    asked: list[tuple[str, str, str]] = []

    def decide(reg, entity_type, slug, write, **kw):
        asked.append((entity_type, slug, write))
        if write in deny:
            return types.SimpleNamespace(allowed=False, reason=f"you may not {write} on this resource")
        return types.SimpleNamespace(allowed=True, reason="")

    monkeypatch.setattr("resource_explorer.workflows.curate.resource_access", decide)
    return asked


def _nothing_enqueued(registry):
    assert registry.list_runs(kind="survey_definition_run", limit=1000) == []
    assert registry.list_activity(limit=1000) == []


@pytest.mark.parametrize("entity_type", TYPES)
def test_a_caller_denied_start_action_gets_403_and_nothing_is_enqueued(client, registry, monkeypatch, entity_type):
    asked = _decider(monkeypatch, deny={"start_action"})
    resp = client.post(f"/api/survey-definitions/{entity_type}/myproj/run", json=BODY)
    assert resp.status_code == 403, resp.text
    assert "start_action" in resp.json()["detail"]
    assert (entity_type, "myproj", "start_action") in asked
    _nothing_enqueued(registry)


def test_a_denied_caller_is_refused_on_the_password_override_path_too(client, registry, monkeypatch):
    _decider(monkeypatch, deny={"start_action"})
    resp = client.post("/api/survey-definitions/database/myproj/run",
                       json={**BODY, "db_user": "u", "db_pwd": "p"})
    assert resp.status_code == 403, resp.text
    _nothing_enqueued(registry)


def test_a_permitted_caller_still_gets_the_normal_response(client, registry, monkeypatch):
    asked = _decider(monkeypatch, deny=set())
    resp = client.post("/api/survey-definitions/repo/myproj/run", json=BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("activity_id")
    assert [a[2] for a in asked] == ["start_action"]      # no project assigned: no publish check
    assert len(registry.list_runs(kind="survey_definition_run", limit=1000)) == 1


def test_with_a_project_assigned_the_publish_check_still_applies(client, registry, monkeypatch):
    asked = _decider(monkeypatch, deny={"publish"})
    monkeypatch.setattr(ProjectRegistry, "has_assigned_egeria_project", lambda self, *a: True)
    resp = client.post("/api/survey-definitions/repo/myproj/run", json=BODY)
    assert resp.status_code == 403, resp.text
    assert "publish" in resp.json()["detail"]
    assert [a[2] for a in asked] == ["start_action", "publish"]
    _nothing_enqueued(registry)


def test_the_quality_batch_refusal_denies_a_caller_without_start_action(registry, monkeypatch):
    from resource_explorer.workflows.egeria_work_access import definition_batch_refusal

    asked = _decider(monkeypatch, deny={"start_action"})
    why = definition_batch_refusal(registry, "database", "mydb")
    assert why.startswith("not permitted") and "start_action" in why
    assert [a[2] for a in asked] == ["start_action"]


def test_the_quality_batch_refusal_still_applies_publish_after_start_action(registry, monkeypatch):
    asked = _decider(monkeypatch, deny={"publish"})
    monkeypatch.setattr(ProjectRegistry, "has_assigned_egeria_project", lambda self, *a: True)
    why = definition_batch_refusal_for(registry)
    assert "publish" in why
    assert [a[2] for a in asked] == ["start_action", "publish"]


def definition_batch_refusal_for(registry):
    from resource_explorer.workflows.egeria_work_access import definition_batch_refusal

    return definition_batch_refusal(registry, "database", "mydb")
