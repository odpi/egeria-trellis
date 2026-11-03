"""Credential change safety (design session 2026-10-03).

Every connect test here is STUBBED at the lowest function
(`PostgreSQLConnection.connect`); the real psycopg2.connect raises if reached.
Every registry is a temp SQLite file created inside the test. Users/passwords
are obviously fake and never printed.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import PostgreSQLConnection

NEW_PW = "fake-pw-NEW-9f3"
OLD_PW = "fake-pw-OLD-1a2"


@pytest.fixture(autouse=True)
def _guards(monkeypatch):
    import psycopg2

    def _no_driver(*a, **k):
        raise AssertionError("real psycopg2.connect reached")

    monkeypatch.setattr(psycopg2, "connect", _no_driver)
    monkeypatch.delenv("EGERIA_SECRETS_STORE_LOCAL_PATH", raising=False)


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "t.db"
    r = ProjectRegistry(db_path=str(path))
    # registry is a sqlite file under tmp_path, never the shared Postgres
    assert not getattr(r, "is_postgres", False)
    assert str(path).startswith(str(tmp_path))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="db.example.invalid", port=5999, database_name="appdb",
        db_user="fake_old_user", db_password=OLD_PW,
    ))
    r._test_path = str(path)
    return r


@pytest.fixture
def calls(monkeypatch):
    class _Seen(list):
        pass

    seen = _Seen()

    def stub(self):
        seen.append((self.host, self.port, self.database, self.user))
        if stub.exc:
            raise stub.exc
    stub.exc = None
    monkeypatch.setattr(PostgreSQLConnection, "connect", stub)
    seen.stub = stub
    return seen


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _row(registry):
    c = sqlite3.connect(registry._test_path)
    c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT * FROM databases WHERE slug='mydb'").fetchone())
    finally:
        c.close()


def _activity(registry):
    return [a for a in registry.list_activity(entity_slug="mydb")
            if a["operation"] == "credential_change"]


AUTH_FAIL = Exception(
    f'connection to server at "db.example.invalid", port 5999 failed: FATAL:  '
    f'password authentication failed for user "fake_new_user" (password was {NEW_PW})')
REFUSED = Exception(
    f'connection to server at "db.example.invalid", port 5999 failed: Connection refused '
    f'(tried {NEW_PW})')


# ── PATCH route ───────────────────────────────────────────────────────────────

def test_failed_connect_stores_nothing_and_leaks_nothing(client, registry, calls, tmp_path, monkeypatch):
    secrets = tmp_path / "om.omsecrets"
    monkeypatch.setenv("EGERIA_SECRETS_STORE_LOCAL_PATH", str(secrets))
    before = _row(registry)
    calls.stub.exc = AUTH_FAIL
    r = client.patch("/api/databases/mydb/credentials",
                     json={"db_user": "fake_new_user", "db_password": NEW_PW})
    assert r.status_code == 400
    assert NEW_PW not in r.text
    assert "refused this credential" in r.json()["detail"]
    # stub was called with the registry row's host/port and the supplied user
    assert calls == [("db.example.invalid", 5999, "appdb", "fake_new_user")]
    assert _row(registry) == before
    assert _activity(registry) == []
    assert not secrets.exists()


def test_unreachable_is_refused_with_cannot_reach_wording(client, registry, calls):
    before = _row(registry)
    calls.stub.exc = REFUSED
    r = client.patch("/api/databases/mydb/credentials",
                     json={"db_user": "fake_new_user", "db_password": NEW_PW})
    assert r.status_code == 400
    assert "could not reach db.example.invalid:5999" in r.json()["detail"]
    assert "refused this credential" not in r.json()["detail"]
    assert NEW_PW not in r.text
    assert _row(registry) == before
    assert _activity(registry) == []


def test_successful_change_logs_one_row_and_stamps_time(client, registry, calls):
    assert _row(registry)["credential_changed_at"] is None
    r = client.patch("/api/databases/mydb/credentials",
                     json={"db_user": "fake_new_user", "db_password": NEW_PW})
    assert r.status_code == 200
    assert r.json()["credential_changed_at"] == _row(registry)["credential_changed_at"]
    assert _row(registry)["credential_changed_at"].endswith("+00:00")
    assert calls == [("db.example.invalid", 5999, "appdb", "fake_new_user")]
    rows = _activity(registry)
    assert len(rows) == 1
    assert rows[0]["entity_slug"] == "mydb" and rows[0]["status"] == "ok"
    assert "fake_new_user" in rows[0]["summary"]
    assert "mydb" in rows[0]["summary"]
    assert NEW_PW not in json.dumps(rows, default=str)
    assert OLD_PW not in json.dumps(rows, default=str)


def test_no_bypass_field_exists():
    from resource_explorer.web.routes.databases import DatabaseCredentialsUpdate
    assert "skip_connect_test" not in DatabaseCredentialsUpdate.model_fields


# ── registry ──────────────────────────────────────────────────────────────────

def test_unchanged_values_write_no_misleading_row(registry):
    registry.update_database_credentials("mydb", "fake_old_user", OLD_PW)
    assert _activity(registry) == []
    assert _row(registry)["credential_changed_at"] is None
    # but the row is now encrypted (the lazy re-encrypt still does its job)
    assert _row(registry)["db_password"] != OLD_PW


def test_lazy_reencrypt_through_read_writes_no_row(registry):
    # the stored value is plaintext (written by register in a test without key?)
    registry.get_database("mydb")
    registry.get_database("mydb")
    assert _activity(registry) == []


def test_changed_user_same_password_is_a_change(registry):
    registry.update_database_credentials("mydb", "fake_other_user", OLD_PW)
    assert len(_activity(registry)) == 1


def test_migration_is_idempotent_on_sqlite(tmp_path):
    path = str(tmp_path / "m.db")
    for _ in range(2):
        ProjectRegistry(db_path=path)
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(databases)")]
    assert cols.count("credential_changed_at") == 1


def test_postgres_migration_sql_without_a_connection():
    executed = []

    class _Rows:
        def __init__(self, rows): self._rows = rows
        def fetchall(self): return self._rows

    class _PgConn:
        is_postgres = True
        def __init__(self, existing): self.existing = existing
        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))
            if "information_schema.columns" in sql:
                return _Rows([{"column_name": c} for c in self.existing])
            return _Rows([])

    reg = ProjectRegistry.__new__(ProjectRegistry)
    reg._add_credential_changed_at_column(_PgConn({"slug", "db_user"}))
    assert executed[0][1] == ("databases",)
    assert executed[1][0] == "ALTER TABLE databases ADD COLUMN credential_changed_at TEXT DEFAULT NULL"
    executed.clear()
    reg._add_credential_changed_at_column(_PgConn({"slug", "credential_changed_at"}))
    assert len(executed) == 1 and "information_schema" in executed[0][0]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _invoke(registry, monkeypatch, args, password):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.cli.main import app
    return CliRunner().invoke(app, args, input=password + "\n")


def test_cli_failed_connect_exits_nonzero_and_stores_nothing(registry, calls, monkeypatch):
    before = _row(registry)
    calls.stub.exc = AUTH_FAIL
    res = _invoke(registry, monkeypatch,
                  ["database", "update-credentials", "mydb", "--user", "fake_new_user"], NEW_PW)
    assert res.exit_code != 0
    assert NEW_PW not in res.output
    assert "refused this credential" in " ".join(res.output.split())
    assert calls == [("db.example.invalid", 5999, "appdb", "fake_new_user")]
    assert _row(registry) == before
    assert _activity(registry) == []


def test_cli_unreachable_says_could_not_reach(registry, calls, monkeypatch):
    calls.stub.exc = REFUSED
    res = _invoke(registry, monkeypatch,
                  ["database", "update-credentials", "mydb", "--user", "fake_new_user"], NEW_PW)
    assert res.exit_code != 0
    assert "could not reach db.example.invalid:5999" in " ".join(res.output.split())
    assert NEW_PW not in res.output


def test_cli_success_prompts_for_password_and_logs(registry, calls, monkeypatch):
    res = _invoke(registry, monkeypatch,
                  ["database", "update-credentials", "mydb", "--user", "fake_new_user"], NEW_PW)
    assert res.exit_code == 0, res.output
    assert NEW_PW not in res.output
    assert len(_activity(registry)) == 1


def test_cli_has_no_skip_flag_and_no_literal_example():
    import inspect

    from resource_explorer.cli import main
    doc = main.database_update_credentials.__doc__
    assert "surveyor" not in doc and "secret" not in doc and "--password" not in doc
    assert "--user <role>" in doc
    assert "skip" not in inspect.signature(main.database_update_credentials).parameters
