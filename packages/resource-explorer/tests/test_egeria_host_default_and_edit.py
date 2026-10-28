"""Egeria-visible host: one server-side default on every registration path, and a PATCH to change it.

2026-10-16: server 'local-docker' (localhost:5442) was registered with egeria_host='localhost' and its database
inherited it, so Egeria (in Docker) surveyed an address it could not reach. The single-database form already
defaulted a localhost host to host.docker.internal; the server paths did not, and nothing could change the value
afterwards. Only the HTTP boundary is faked (the registry is a temp SQLite, the write check is stubbed at the
route's call to `authorize_resource_write`); every test reads the value back through a GET.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, DatabaseServer, ProjectRegistry

DOCKER = "host.docker.internal"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "eh.db"))
    assert str(tmp_path) in r.database_url
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


@pytest.fixture
def checks(monkeypatch):
    """Every write check the routes ask for, as (entity_type, slug, write); `deny` slugs are refused 403."""
    seen: list = []
    deny: set = set()

    async def _authorize(registry, entity_type, slug, **kw):
        seen.append((entity_type, slug, kw.get("write")))
        if slug in deny:
            raise HTTPException(403, "not permitted on this resource")

    monkeypatch.setattr("resource_explorer.web.routes.curate.authorize_resource_write", _authorize)
    return seen, deny


# ── the one helper ────────────────────────────────────────────────────────────

def test_the_helper_defaults_only_a_local_host_with_no_value():
    from resource_explorer.egeria_host import default_egeria_host as d

    assert d("localhost", "") == DOCKER
    assert d("127.0.0.1", "  ") == DOCKER
    assert d("LocalHost", "") == DOCKER
    assert d("db.example.com", "") == ""
    assert d("localhost", "my-box") == "my-box"
    assert d("localhost", " my-box ") == "my-box"


# ── registration paths ────────────────────────────────────────────────────────

def _server_body(**kw):
    body = {"slug": "local-docker", "display_name": "Local Docker", "host": "localhost", "port": 5442,
            "db_user": "u", "db_password": "p"}
    body.update(kw)
    return body


def test_server_register_defaults_a_localhost_server(client):
    assert client.post("/api/db-servers/register", json=_server_body()).status_code == 200
    assert client.get("/api/db-servers/local-docker").json()["egeria_host"] == DOCKER


def test_server_register_keeps_an_explicit_value_and_a_remote_host_empty(client):
    client.post("/api/db-servers/register", json=_server_body(slug="a", egeria_host="my-box"))
    client.post("/api/db-servers/register", json=_server_body(slug="b", host="db.example.com"))
    assert client.get("/api/db-servers/a").json()["egeria_host"] == "my-box"
    assert client.get("/api/db-servers/b").json()["egeria_host"] == ""


def test_database_register_defaults_a_localhost_database(client):
    body = {"slug": "d1", "display_name": "D1", "db_type": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "d1"}
    assert client.post("/api/databases/register", json=body).status_code == 200
    assert client.get("/api/databases/d1").json()["egeria_host"] == DOCKER


def test_add_database_from_a_server_with_an_empty_value_gets_the_default(client, registry):
    # A server registered before the default existed (empty), on this machine's localhost.
    registry.register_server(DatabaseServer(slug="old", display_name="Old", host="localhost", port=5442,
                                            db_user="u", db_password="p", egeria_host=""))
    r = client.post("/api/db-servers/old/add-database", params={"database_name": "x"})
    assert r.status_code == 200
    assert client.get(f"/api/databases/{r.json()['slug']}").json()["egeria_host"] == DOCKER


def test_add_database_from_a_server_copies_the_servers_value(client, registry):
    registry.register_server(DatabaseServer(slug="srv", display_name="S", host="localhost", port=5442,
                                            db_user="u", db_password="p", egeria_host="my-box"))
    r = client.post("/api/db-servers/srv/add-database", params={"database_name": "x"})
    assert client.get(f"/api/databases/{r.json()['slug']}").json()["egeria_host"] == "my-box"


def test_csv_import_builder_uses_the_same_helper(registry):
    from resource_explorer.batch_io import build_database_entity

    server = DatabaseServer(slug="s", display_name="S", host="localhost", port=5442, db_user="u",
                            db_password="p", egeria_host="")
    assert build_database_entity(server, "x").egeria_host == DOCKER


# ── the edit: a database ──────────────────────────────────────────────────────

def _db(registry, slug="mydb", host="localhost", egeria_host="localhost", guid="", server_slug=""):
    registry.register_database(DatabaseEntity(
        slug=slug, display_name=slug, db_type="postgresql", host=host, port=5442, database_name=slug,
        db_user="u", db_password="secret", egeria_host=egeria_host, egeria_asset_guid=guid,
        server_slug=server_slug))


def test_patch_database_changes_only_the_host_and_the_next_read_shows_it(client, registry, checks):
    seen, _ = checks
    _db(registry)
    r = client.patch("/api/databases/mydb/egeria-host", json={"egeria_host": DOCKER})
    assert r.status_code == 200
    body = r.json()
    assert (body["previous"], body["egeria_host"], body["in_egeria"]) == ("localhost", DOCKER, False)
    got = client.get("/api/databases/mydb").json()
    assert got["egeria_host"] == DOCKER
    assert got["db_user"] == "u" and got["host"] == "localhost"
    assert registry.get_database("mydb").db_password == "secret"
    assert ("database", "mydb", "publish") in seen


def test_patch_database_registered_in_egeria_says_the_elements_keep_the_old_address(client, registry, checks):
    _db(registry, guid="g-1")
    body = client.patch("/api/databases/mydb/egeria-host", json={"egeria_host": DOCKER}).json()
    assert body["in_egeria"] is True
    assert "keep the old address" in body["note"]
    assert "registered again" in body["note"]
    assert "deleting" in body["note"].lower()
    assert client.get("/api/databases/mydb").json()["egeria_asset_guid"] == "g-1"


def test_patch_database_is_refused_without_the_write_check(client, registry, checks):
    _, deny = checks
    _db(registry)
    deny.add("mydb")
    assert client.patch("/api/databases/mydb/egeria-host", json={"egeria_host": DOCKER}).status_code == 403
    assert client.get("/api/databases/mydb").json()["egeria_host"] == "localhost"


def test_patch_database_unknown_and_malformed(client, registry, checks):
    _db(registry)
    assert client.patch("/api/databases/nope/egeria-host", json={"egeria_host": DOCKER}).status_code == 404
    assert client.patch("/api/databases/mydb/egeria-host", json={"egeria_host": "a b/c"}).status_code == 400
    assert client.get("/api/databases/mydb").json()["egeria_host"] == "localhost"


# ── the edit: a server, and carrying the value to its databases ───────────────

def _server_with_dbs(registry):
    registry.register_server(DatabaseServer(slug="srv", display_name="S", host="localhost", port=5442,
                                            db_user="u", db_password="p", egeria_host="localhost"))
    _db(registry, slug="inherits", server_slug="srv", egeria_host="localhost", guid="g-in")
    _db(registry, slug="override", server_slug="srv", egeria_host="my-box")
    _db(registry, slug="other", server_slug="", egeria_host="localhost")


def test_patch_server_changes_it_and_lists_who_could_carry_it(client, registry, checks):
    _server_with_dbs(registry)
    r = client.patch("/api/db-servers/srv/egeria-host", json={"egeria_host": DOCKER})
    assert r.status_code == 200
    body = r.json()
    assert (body["previous"], body["egeria_host"]) == ("localhost", DOCKER)
    assert [c["slug"] for c in body["carriable"]] == ["inherits"]       # the override and the unrelated one are not
    assert body["carriable"][0]["in_egeria"] is True
    assert body["carried"] == []
    assert client.get("/api/db-servers/srv").json()["egeria_host"] == DOCKER
    assert client.get("/api/databases/inherits").json()["egeria_host"] == "localhost"   # nothing carried silently


def test_patch_server_carries_only_databases_that_still_have_the_old_value(client, registry, checks):
    seen, _ = checks
    _server_with_dbs(registry)
    client.patch("/api/db-servers/srv/egeria-host", json={"egeria_host": DOCKER})
    r = client.patch("/api/db-servers/srv/egeria-host",
                     json={"egeria_host": DOCKER, "carry_databases_from": "localhost"})
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()["carried"]] == ["inherits"]
    assert client.get("/api/databases/inherits").json()["egeria_host"] == DOCKER
    assert client.get("/api/databases/override").json()["egeria_host"] == "my-box"
    assert client.get("/api/databases/other").json()["egeria_host"] == "localhost"
    assert ("database", "inherits", "publish") in seen
    assert "keep the old address" in r.json()["note"]                  # inherits is in Egeria


def test_patch_server_carry_refused_changes_nothing(client, registry, checks):
    _, deny = checks
    _server_with_dbs(registry)
    deny.add("inherits")
    r = client.patch("/api/db-servers/srv/egeria-host",
                     json={"egeria_host": DOCKER, "carry_databases_from": "localhost"})
    assert r.status_code == 403
    assert client.get("/api/db-servers/srv").json()["egeria_host"] == "localhost"
    assert client.get("/api/databases/inherits").json()["egeria_host"] == "localhost"


def test_patch_server_unknown(client, checks):
    assert client.patch("/api/db-servers/nope/egeria-host", json={"egeria_host": DOCKER}).status_code == 404
