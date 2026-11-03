"""POST /api/automate/subscriptions must validate a resource against ITS OWN
entity type, not always the repo lookup.

Found live 2026-09-28: clicking "notify me" on a database resource card
failed with `"Repo 'laz_local_adventureworks' not found"`. Root cause was two
layers deep:

  1. The client (`re-api.js`'s `createSubscription`, wired from `app.js`'s
     `openNotifyDialog`) hardcoded `entity_type: 'repo'` in the POST body
     unconditionally -- fixed separately, see `tests/test_next_notify_
     subscription.py`.
  2. Even with a correct `entity_type`, this route's own validation
     (`web/routes/automate.py`'s `create_subscription`) only ever checked
     `if req.entity_type == "repo" and not registry.get(...)` -- for any
     OTHER entity_type it did nothing at all, so a nonexistent database or
     filesystem slug would have been silently accepted rather than 404ing.

This file covers the server-side half on its own: each entity type gets a
type-correct existence check and a type-correct 404 message, independent of
what the client happens to send. Fixture shape (`registry` + a `client` that
monkeypatches `automate.ProjectRegistry` to return it) matches
`test_automate_end_to_end.py`'s own `TestTheScheduledPrerequisiteIsVisible`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import (
    DatabaseEntity,
    FileSystemEntity,
    Project,
    ProjectRegistry,
)


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="p", display_name="P", github_url="u", description=""))
    reg.register_database(DatabaseEntity(
        slug="laz_local_adventureworks", display_name="AdventureWorks",
        db_type="postgresql", host="localhost", port=5432,
        database_name="adventureworks",
    ))
    reg.register_filesystem(FileSystemEntity(
        slug="local_fs", display_name="Local FS", local_mount_point="/data",
    ))
    return reg


@pytest.fixture
def client(registry, monkeypatch):
    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.web.routes.automate.ProjectRegistry",
                         lambda *a, **kw: registry)
    return TestClient(app)


class TestPerEntityTypeSuccess:
    def test_repo_subscription_succeeds_for_a_real_slug(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "repo", "entity_slug": "p", "analysis_id": "maturity",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["entity_type"] == "repo"

    def test_database_subscription_succeeds_for_a_real_slug(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "database", "entity_slug": "laz_local_adventureworks",
            "analysis_id": "schema_inventory",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["entity_type"] == "database"

    def test_filesystem_subscription_succeeds_for_a_real_slug(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "filesystem", "entity_slug": "local_fs",
            "analysis_id": "file_inventory",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["entity_type"] == "filesystem"


class TestPerEntityTypeNotFound:
    """The exact bug: a database slug must 404 against the DATABASE lookup
    with a database-flavored message, never the repo-flavored one a database
    slug will never match."""

    def test_repo_404_names_the_repo(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "repo", "entity_slug": "no_such_repo", "analysis_id": "maturity",
        })
        assert resp.status_code == 404
        assert "Repo 'no_such_repo' not found" in resp.json()["detail"]

    def test_database_404_names_the_database_not_a_repo(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "database", "entity_slug": "laz_local_adventureworks_typo",
            "analysis_id": "schema_inventory",
        })
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "Database 'laz_local_adventureworks_typo' not found" in detail
        assert "Repo" not in detail

    def test_a_real_repo_slug_sent_as_database_still_404s(self, client):
        # The exact live failure mode, inverted: before the fix, a database
        # slug sent as entity_type='repo' 404'd against registry.get(). Now
        # confirm the type-correct lookup is actually type-correct -- a repo
        # slug is not a database slug, even though a row named "p" exists.
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "database", "entity_slug": "p", "analysis_id": "schema_inventory",
        })
        assert resp.status_code == 404
        assert "Database 'p' not found" in resp.json()["detail"]

    def test_filesystem_404_names_the_filesystem(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "filesystem", "entity_slug": "no_such_fs",
            "analysis_id": "file_inventory",
        })
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "Filesystem 'no_such_fs' not found" in detail
        assert "Repo" not in detail


class TestUnknownEntityTypePassesThroughUnvalidated:
    """Same deliberate exception schedules.py's `_require_resource` documents:
    an entity_type this route's lookup table doesn't know is not rejected --
    rejecting it here would 404 a resource kind added elsewhere in the
    codebase before this dict was updated for it."""

    def test_an_unknown_entity_type_is_not_404d(self, client):
        resp = client.post("/api/automate/subscriptions", json={
            "entity_type": "survey_definition", "entity_slug": "whatever",
            "analysis_id": "x",
        })
        assert resp.status_code == 200, resp.text
