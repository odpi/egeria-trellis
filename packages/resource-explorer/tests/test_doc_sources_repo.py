"""E2 (2026-09-29): documentation sources for the `repo` resource kind.

Same block, same three facts, same signing as database/filesystem. Repos
carry no per-entity Egeria credentials, so `resolve_entity_for_doc_source`
hands back a `RepoEgeriaConnectionView` built from the environment; these
tests pin that view (and that it never leaks the password), the routes, and
the publish hook.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from resource_explorer.doc_source_egeria import RepoEgeriaConnectionView, resolve_entity_for_doc_source
from resource_explorer.doc_source_probe import ProbeResult
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="amundsen", display_name="Amundsen", github_url="https://github.com/amundsen-io/amundsen"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                        lambda url: ProbeResult(state="reachable", status_code=200, elapsed_ms=5))
    monkeypatch.setattr("resource_explorer.web.routes.doc_sources.get_current_user",
                        lambda request: {"user_id": "dan"})
    from resource_explorer.web.app import app
    return TestClient(app)


def test_repo_add_list_recheck_remove(client):
    add = client.post("/api/doc-sources/repo/amundsen", json={"url": "https://docs.example/a", "label": "A"})
    assert add.status_code == 200, add.text
    row = add.json()
    assert row["added_by"] == "dan" and row["added_at"]
    assert row["probe_state"] == "reachable"
    listed = client.get("/api/doc-sources/repo/amundsen").json()
    assert [s["id"] for s in listed["sources"]] == [row["id"]]
    assert client.post(f"/api/doc-sources/repo/amundsen/{row['id']}/recheck").status_code == 200
    assert client.delete(f"/api/doc-sources/repo/amundsen/{row['id']}").status_code == 200
    assert client.get("/api/doc-sources/repo/amundsen").json()["sources"] == []


def test_unpublished_repo_source_reads_local_only(client):
    client.post("/api/doc-sources/repo/amundsen", json={"url": "https://docs.example/a"})
    src = client.get("/api/doc-sources/repo/amundsen").json()["sources"][0]
    assert src["egeria_state"] == "local_only"
    assert src["egeria_external_ref_guid"] in ("", None)


def test_unknown_repo_is_404(client):
    assert client.post("/api/doc-sources/repo/nope", json={"url": "https://x"}).status_code == 404


def test_resolver_returns_connection_view_with_asset_guid(registry, monkeypatch):
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "hunter2-secret")
    registry.set_egeria_asset_guid("amundsen", "guid-123")
    view = resolve_entity_for_doc_source(registry, "repo", "amundsen")
    assert isinstance(view, RepoEgeriaConnectionView)
    assert view.egeria_asset_guid == "guid-123"
    assert view.display_name == "Amundsen"
    # Brief I: the view carries no credential any more; who acts is the factory's business.
    assert not hasattr(view, "egeria_password") and not hasattr(view, "egeria_user")
    assert resolve_entity_for_doc_source(registry, "repo", "nope") is None


def test_connection_view_never_leaks_password(registry, monkeypatch, caplog):
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "hunter2-secret")
    view = resolve_entity_for_doc_source(registry, "repo", "amundsen")
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("x").info("entity=%s / %r", view, view)
    assert "hunter2-secret" not in repr(view)
    assert "hunter2-secret" not in str(view)
    assert "hunter2-secret" not in caplog.text
    assert "hunter2-secret" not in f"{view!r}{view!s}"


def test_repo_publish_hook_publishes_local_sources(registry, monkeypatch):
    """The publisher path must call publish_local_doc_sources for a repo --
    the recurring 'configured for two kinds, never called for the third'
    pattern."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    calls = []
    monkeypatch.setattr("resource_explorer.web.routes.doc_sources.publish_local_doc_sources",
                        lambda et, slug, guid, registry=None: calls.append((et, slug, guid)))
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._registry = registry
    pub._publish_local_doc_sources("amundsen", "asset-guid-1")
    assert calls == [("repo", "amundsen", "asset-guid-1")]
    calls.clear()
    pub._publish_local_doc_sources("amundsen", "")  # no asset guid -> no-op
    assert calls == []


def test_publish_call_site_exists_in_repo_publish_path():
    import inspect
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    src = inspect.getsource(EgeriaPublisher.publish)
    assert "_publish_local_doc_sources(result.resource_slug, asset_guid)" in src
