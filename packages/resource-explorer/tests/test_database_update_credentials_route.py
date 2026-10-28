"""Tests for PATCH /api/databases/{slug}/credentials — the supported way to
repoint an already-registered database's stored db_user/db_password (e.g.
from a narrow-access role to a broader one) without losing its registration
history (slug, egeria_asset_guid, survey history)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture(autouse=True)
def _stub_connect(monkeypatch):
    """The route now connect-tests before saving: stub the lowest connect
    helper (succeeds) and make the real driver raise if ever reached."""
    import psycopg2

    from resource_explorer.surveyors.database.connection import PostgreSQLConnection

    def _no_driver(*a, **k):
        raise AssertionError("real psycopg2.connect reached")

    monkeypatch.setattr(psycopg2, "connect", _no_driver)
    monkeypatch.setattr(PostgreSQLConnection, "connect", lambda self: None)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
        db_user="egeria_user", db_password="old-secret",
        egeria_asset_guid="guid-123",
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


class TestUpdateDatabaseCredentialsRoute:
    def test_404_for_unknown_database(self, client):
        resp = client.patch(
            "/api/databases/nope/credentials",
            json={"db_user": "surveyor", "db_password": "secret"},
        )
        assert resp.status_code == 404

    def test_updates_credentials_and_returns_summary(self, client, registry):
        resp = client.patch(
            "/api/databases/mydb/credentials",
            json={"db_user": "surveyor", "db_password": "new-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "mydb"
        assert data["db_user"] == "surveyor"
        # Password is never exposed on the summary — same convention as
        # DatabaseSummary elsewhere in this router.
        assert "db_password" not in data
        # Registration history untouched.
        assert data["egeria_asset_guid"] == "guid-123"

        updated = registry.get_database("mydb")
        assert updated.db_user == "surveyor"
        assert updated.db_password == "new-secret"
        assert updated.egeria_asset_guid == "guid-123"
