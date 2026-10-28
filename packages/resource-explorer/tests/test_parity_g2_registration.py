"""Parity slice G2, part 1: register one database (test-then-register, PI-015)
and the omsecrets read-back after a credential change (PI-021).

Credential safety is the point of these tests. Every fake secret below is an
obviously fake value, and each test that touches one asserts it never reaches a
response body, a log line, an activity row or an error sentence. Nothing here
opens a real connection: `database_connection` is replaced by a fake probe and
`psycopg2.connect` raises if reached; the registry is a temp sqlite file.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

FAKE_PW = "test-password-not-real"
FAKE_PW_2 = "test-password-not-real-2"


@pytest.fixture(autouse=True)
def _guards(monkeypatch):
    import psycopg2

    def _no_driver(*a, **k):
        raise AssertionError("real psycopg2.connect reached")

    monkeypatch.setattr(psycopg2, "connect", _no_driver)
    monkeypatch.delenv("EGERIA_SECRETS_STORE_LOCAL_PATH", raising=False)


class _FakeConn:
    def __init__(self, user, tables):
        self.user, self.tables = user, tables

    def execute_query(self, query, params=()):
        if "current_user" in query:
            return [{"who": self.user}]
        return [{"n": self.tables}]


@pytest.fixture
def probe(monkeypatch):
    """A fake connection probe. `probe.fail` is an exception to raise on
    connect; `probe.seen` records (host, port, database, user) only."""
    from resource_explorer.surveyors.database import connection

    class _P:
        fail = None
        tables = 12
        seen: list = []

    p = _P()
    p.seen = []

    @contextmanager
    def fake(db_entity, credentials, connect_timeout=None):
        p.seen.append((db_entity.host, db_entity.port, db_entity.database_name, credentials["user"]))
        if p.fail:
            raise p.fail
        yield _FakeConn(credentials["user"], p.tables)

    monkeypatch.setattr(connection, "database_connection", fake)
    return p


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    assert not getattr(r, "is_postgres", False)
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _body(**over):
    b = {"host": "db.example.invalid", "port": 5999, "database_name": "appdb",
         "db_type": "postgresql", "db_user": "scout_ro", "db_password": FAKE_PW}
    b.update(over)
    return b


def _registration(**over):
    b = {"slug": "appdb", "display_name": "App DB", "db_type": "postgresql",
         "host": "db.example.invalid", "port": 5999, "database_name": "appdb",
         "db_user": "scout_ro", "db_password": FAKE_PW, "group_slug": ""}
    b.update(over)
    return b


def _everything_stored(registry) -> str:
    """Every activity row, serialised: the grep target for a leaked secret."""
    return json.dumps(registry.list_activity(limit=1000), default=str)


# ── PI-015: test connection, nothing saved ──────────────────────────────────

def test_connection_test_passes_with_a_sentence_and_saves_nothing(client, registry, probe, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/databases/_test-connection", json=_body())
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["sentence"] == "Connected to db.example.invalid:5999/appdb as scout_ro · 12 table(s) readable with this credential."
    assert probe.seen == [("db.example.invalid", 5999, "appdb", "scout_ro")]
    assert registry.list_databases() == []             # a test registers nothing
    assert registry.list_activity(limit=50) == []      # and leaves no row
    assert FAKE_PW not in r.text
    assert FAKE_PW not in caplog.text


def test_connection_test_refusal_is_a_sentence_and_scrubs_the_password(client, registry, probe, caplog):
    caplog.set_level(logging.DEBUG)
    probe.fail = RuntimeError(f'FATAL: password authentication failed for user "scout_ro" (pw={FAKE_PW})')
    r = client.post("/api/databases/_test-connection", json=_body())
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "error"
    assert d["sentence"].startswith("Not registered: the server at db.example.invalid:5999 refused this credential")
    assert FAKE_PW not in r.text
    assert FAKE_PW not in caplog.text
    assert registry.list_databases() == []


def test_connection_test_unreachable_is_said_differently(client, probe):
    probe.fail = OSError("timeout expired")
    d = client.post("/api/databases/_test-connection", json=_body()).json()
    assert d["status"] == "error"
    assert d["sentence"].startswith("Not registered: could not reach db.example.invalid:5999")


def test_connection_test_needs_a_user_and_a_password(client, probe):
    d = client.post("/api/databases/_test-connection", json=_body(db_password="")).json()
    assert d["status"] == "error"
    assert "user and a password are both required" in d["sentence"]
    assert probe.seen == []                             # nothing was dialled


# ── PI-015: register writes one proof row naming who and when ───────────────

def test_register_leaves_a_row_that_names_who_and_when_and_no_secret(client, registry, probe, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "dan")
    r = client.post("/api/databases/register", json=_registration())
    assert r.status_code == 200
    assert registry.get_database("appdb") is not None
    rows = registry.list_activity(entity_type="database", entity_slug="appdb", operation="register")
    assert len(rows) == 1
    assert rows[0]["summary"] == "Registered database appdb"
    assert FAKE_PW not in _everything_stored(registry)
    assert FAKE_PW not in r.text
    assert FAKE_PW not in caplog.text

    reg = client.get("/api/databases/appdb/registration").json()
    assert reg["registered_by"] == "dan"
    assert reg["registered_at"]                        # the registry row's own stamp
    assert FAKE_PW not in json.dumps(reg)


def test_registration_read_says_who_isnt_recorded_not_a_guess(client, registry, probe):
    registry.register_database(DatabaseEntity(
        slug="old", display_name="Old", db_type="postgresql", host="h", port=1, database_name="d"))
    reg = client.get("/api/databases/old/registration").json()
    assert reg["registered_by"] == ""                  # no register row exists: unknown, not "you"
    assert client.get("/api/databases/nope/registration").status_code == 404


# ── PI-021: the omsecrets read-back ─────────────────────────────────────────

def _point_omsecrets_at(monkeypatch, path):
    from resource_explorer import omsecrets_store
    monkeypatch.setattr(omsecrets_store, "local_path", lambda: str(path) if path else "")


def test_changing_a_credential_then_reading_the_drift_check_says_in_sync(client, registry, probe, monkeypatch, tmp_path):
    _point_omsecrets_at(monkeypatch, tmp_path / "re.omsecrets")
    registry.register_database(DatabaseEntity(
        slug="appdb", display_name="App", db_type="postgresql", host="db.example.invalid", port=5999,
        database_name="appdb", db_user="old_user", db_password=FAKE_PW))
    r = client.patch("/api/databases/appdb/credentials", json={"db_user": "new_user", "db_password": FAKE_PW_2})
    assert r.status_code == 200
    assert r.json()["credential_changed_at"]
    assert FAKE_PW_2 not in r.text
    d = client.get("/api/databases/appdb/credential-drift").json()
    assert d == {"slug": "appdb", "collection_name": "appdb::PostgreSQL Secret", "in_registry": True,
                 "in_omsecrets": True, "omsecrets_configured": True, "in_sync": True}
    rows = registry.list_activity(entity_slug="appdb", operation="credential_change")
    assert len(rows) == 1 and "new_user" in rows[0]["summary"]
    assert FAKE_PW not in _everything_stored(registry) and FAKE_PW_2 not in _everything_stored(registry)


def test_a_refused_credential_changes_nothing_and_the_drift_stays_what_it_was(client, registry, probe, monkeypatch, tmp_path):
    _point_omsecrets_at(monkeypatch, tmp_path / "re.omsecrets")
    registry.register_database(DatabaseEntity(
        slug="appdb", display_name="App", db_type="postgresql", host="db.example.invalid", port=5999,
        database_name="appdb", db_user="old_user", db_password=FAKE_PW))
    probe.fail = RuntimeError(f"FATAL: password authentication failed {FAKE_PW_2}")
    r = client.patch("/api/databases/appdb/credentials", json={"db_user": "new_user", "db_password": FAKE_PW_2})
    assert r.status_code == 400
    assert FAKE_PW_2 not in r.text
    assert registry.get_database("appdb").db_user == "old_user"
    assert registry.list_activity(operation="credential_change") == []
    d = client.get("/api/databases/appdb/credential-drift").json()
    assert d["in_registry"] is True and d["in_omsecrets"] is False and d["in_sync"] is False


def test_drift_check_says_not_checked_when_no_secrets_path_is_set(client, registry, monkeypatch):
    _point_omsecrets_at(monkeypatch, "")
    registry.register_database(DatabaseEntity(
        slug="appdb", display_name="App", db_type="postgresql", host="h", port=1, database_name="d",
        db_user="u", db_password=FAKE_PW))
    d = client.get("/api/databases/appdb/credential-drift").json()
    assert d["omsecrets_configured"] is False and d["in_sync"] is None   # None is "not checked", never "in sync"
    assert client.get("/api/databases/nope/credential-drift").status_code == 404
