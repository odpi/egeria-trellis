"""Tests for the sub-resources routes — the "Select"/"Catalog" stages of
the repo scope-narrowing funnel (docs/repo-scope-narrowing-funnel.md,
D2/D3/D4): GET/{slug}/sub-resources, POST /{slug}/sub-resources/catalog,
DELETE /{slug}/sub-resources."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")


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


class TestListSubResources:
    def test_404_for_unknown_repo(self, client):
        resp = client.get("/api/projects/nope/sub-resources")
        assert resp.status_code == 404

    def test_empty_when_nothing_catalogued(self, client):
        resp = client.get("/api/projects/myproj/sub-resources")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_catalogued_rows(self, client, registry):
        registry.catalog_sub_resource("repo", "myproj", "docs", "folder")
        resp = client.get("/api/projects/myproj/sub-resources")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["locator"] == "docs"
        assert data[0]["kind"] == "folder"
        assert data[0]["egeria_guid"] == ""


def _survey(registry, rows=(("docs", "folder"), ("docs/SECURITY.md", "file"), ("README.md", "file"))):
    registry.upsert_finding("myproj", "repo_sub_resource_survey", [
        {"check_name": loc, "label": "worthy", "summary": "why", "detail": {"path": loc, "kind": kind}}
        for loc, kind in rows])


def _choose(registry, loc, kind, choice="include"):
    registry.append_resource_scope_event("repo", "myproj", locator=loc, kind=kind, choice=choice, author="peterprofile")


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})


class TestCatalogRoute:
    """Brief 2a: the press publishes what the selection RECORD says. A list in the request is not read, and the
    old publish_to_egeria=false sandbox flag is retired (a choice without a publish is the record itself)."""

    def test_404_for_unknown_repo(self, client):
        assert client.post("/api/projects/nope/sub-resources/catalog", json={}).status_code == 404

    def test_signed_out_is_401(self, client, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        assert client.post("/api/projects/myproj/sub-resources/catalog", json={}).status_code == 401

    def test_400_when_nothing_is_chosen_even_if_the_request_names_items(self, client, registry):
        _survey(registry)
        resp = client.post("/api/projects/myproj/sub-resources/catalog", json={
            "items": [{"locator": "docs", "kind": "folder"}], "publish_to_egeria": False})
        assert resp.status_code == 400 and "nothing selected" in resp.json()["detail"]
        assert registry.list_sub_resources("repo", "myproj") == []

    def test_409_when_the_repo_has_no_egeria_asset_yet_and_nothing_is_cataloged(self, client, registry):
        _survey(registry)
        _choose(registry, "docs", "folder")
        resp = client.post("/api/projects/myproj/sub-resources/catalog", json={})
        assert resp.status_code == 409
        assert registry.list_sub_resources("repo", "myproj") == []

    def test_publishes_exactly_the_chosen_items_plus_the_containers_with_proofs(self, client, registry):
        _survey(registry)
        registry.set_egeria_asset_guid("myproj", "repo-asset-guid")
        _choose(registry, "docs/SECURITY.md", "file")
        with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
            MockPub.return_value.publish_sub_resources.return_value = {"docs": "g-docs", "docs/SECURITY.md": "g-file"}
            MockPub.return_value._asset_maker.get_asset_by_guid.side_effect = lambda guid, **k: {"guid": guid}
            resp = client.post("/api/projects/myproj/sub-resources/catalog", json={
                "items": [{"locator": "README.md", "kind": "file"}]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["published"] == {"docs": "g-docs", "docs/SECURITY.md": "g-file"}
        assert (data["containers"], data["read_back"], data["sent"], data["failed"]) == (1, 2, 0, 0)
        assert set(data["cataloged"]) == {"docs/SECURITY.md", "docs"}      # README.md was in the request, not the record
        MockPub.return_value.publish_sub_resources.assert_called_once_with(
            "myproj", "https://github.com/test/myproj", "repo-asset-guid", ["docs", "docs/SECURITY.md"])
        proofs = [p for p in registry.list_catalogue_commit_proofs("myproj") if p["node_kind"] == "sub_resource"]
        assert sorted((p["table_name"], p["element_guid"]) for p in proofs) == [("docs", "g-docs"), ("docs/SECURITY.md", "g-file")]

    def test_ancestor_folders_are_cataloged_for_a_chosen_nested_file(self, client, registry):
        """D2's own guarantee, kept: NestedFile requires a FileFolder parent."""
        _survey(registry)
        registry.set_egeria_asset_guid("myproj", "repo-asset-guid")
        _choose(registry, "docs/SECURITY.md", "file")
        with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
            MockPub.return_value.publish_sub_resources.return_value = {}
            client.post("/api/projects/myproj/sub-resources/catalog", json={})
        rows = {r["locator"]: r["kind"] for r in registry.list_sub_resources("repo", "myproj")}
        assert rows == {"docs/SECURITY.md": "file", "docs": "folder"}

    def test_root_level_file_gets_the_synthetic_root_folder(self, client, registry):
        _survey(registry)
        registry.set_egeria_asset_guid("myproj", "repo-asset-guid")
        _choose(registry, "README.md", "file")
        with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
            MockPub.return_value.publish_sub_resources.return_value = {}
            resp = client.post("/api/projects/myproj/sub-resources/catalog", json={})
        assert set(resp.json()["cataloged"]) == {"README.md", ""}

    def test_republishing_never_disturbs_a_stored_guid(self, client, registry):
        _survey(registry)
        registry.set_egeria_asset_guid("myproj", "repo-asset-guid")
        registry.catalog_sub_resource("repo", "myproj", "docs", "folder")
        registry.set_sub_resource_egeria_guid("repo", "myproj", "docs", "already-published-guid")
        _choose(registry, "docs", "folder")
        with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as MockPub:
            MockPub.return_value.publish_sub_resources.return_value = {}
            client.post("/api/projects/myproj/sub-resources/catalog", json={})
        assert registry.list_sub_resources("repo", "myproj")[0]["egeria_guid"] == "already-published-guid"


class TestUncatalogRoute:
    def test_404_for_unknown_repo(self, client):
        resp = client.delete("/api/projects/nope/sub-resources", params={"locator": "docs"})
        assert resp.status_code == 404

    def test_removes_the_row(self, client, registry):
        registry.catalog_sub_resource("repo", "myproj", "docs", "folder")
        resp = client.delete("/api/projects/myproj/sub-resources", params={"locator": "docs"})
        assert resp.status_code == 200
        assert registry.list_sub_resources("repo", "myproj") == []

    def test_root_locator_is_representable_via_query_param(self, client, registry):
        registry.catalog_sub_resource("repo", "myproj", "", "folder")
        resp = client.delete("/api/projects/myproj/sub-resources", params={"locator": ""})
        assert resp.status_code == 200
        assert registry.list_sub_resources("repo", "myproj") == []
