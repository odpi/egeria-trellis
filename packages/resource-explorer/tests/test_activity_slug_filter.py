"""GET /api/activity/ must honour ?entity_slug= (and ?entity_type=).

The route declared no entity_slug parameter, so FastAPI silently dropped it and the Next UI's "Runs on this
resource" dialog listed every resource's runs.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import ActivityEntry, ProjectRegistry


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path/'t.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", db_url)
    reg = ProjectRegistry(database_url=db_url)
    reg._init_schema()
    for eid, etype, slug in (("r-a", "repo", "a"), ("r-b", "repo", "b"), ("d-a", "database", "a")):
        reg.write_activity(ActivityEntry(id=eid, ts="2026-10-13T00:00:00", operation="survey", intent="assessment",
                                         entity_type=etype, entity_slug=slug))
    import resource_explorer.web.routes.activity as routes
    monkeypatch.setattr(routes, "_registry", lambda: ProjectRegistry(database_url=db_url))
    from resource_explorer.web.app import app
    return TestClient(app)


def _ids(resp):
    assert resp.status_code == 200
    return sorted(r["id"] for r in resp.json())


def test_without_a_slug_every_row_comes_back(client):
    assert _ids(client.get("/api/activity/")) == ["d-a", "r-a", "r-b"]


def test_entity_slug_narrows_to_that_slug(client):
    assert _ids(client.get("/api/activity/?entity_slug=a")) == ["d-a", "r-a"]


def test_entity_type_narrows_a_shared_slug_further(client):
    assert _ids(client.get("/api/activity/?entity_slug=a&entity_type=repo")) == ["r-a"]
    assert _ids(client.get("/api/activity/?entity_slug=a&entity_type=database")) == ["d-a"]
