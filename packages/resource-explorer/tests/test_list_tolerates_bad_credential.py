"""One database row whose stored password cannot be decrypted must not take
down every list (incident 2026-10-02: two fake rows encrypted under a test key
made `list_databases()` raise for EVERY database).

Contract: list reads return the row with every non-secret field intact and
`credential_status == "unreadable"`; single-row reads that may CONNECT refuse
for that database only; no password, ciphertext or exception text escapes.

SAFETY: every registry here is a temp SQLite file created inside the test and
passed to the constructor explicitly. The fixture below fails closed if the
registry it is about to use is anything else (the shared Postgres at
localhost:5442 must never be opened by a test).
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from resource_explorer import credential_crypto, registry as registry_mod
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

GOOD_KEY = "test-key-A-not-a-real-key"
OTHER_KEY = "test-key-B-not-a-real-key"
PW_GOOD = "good-fake-pw-1"
PW_BAD = "bad-fake-pw-zzz"
PW_GOOD2 = "good-fake-pw-2"


def _mk(slug, pw, name=None):
    return DatabaseEntity(
        slug=slug, display_name=name or slug.upper(), db_type="postgresql",
        host="db.example.invalid", port=5432, database_name=slug,
        db_user="fake_user", db_password=pw, description="kept",
    )


def _assert_temp_sqlite(reg, tmp_path):
    assert reg.database_url.startswith("sqlite:///"), reg.database_url
    assert str(tmp_path) in reg.database_url, reg.database_url
    assert "5442" not in reg.database_url and "postgres" not in reg.database_url


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("PGVECTOR_PORT", "1")
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/guard.db")
    getattr(registry_mod, "_UNREADABLE_LOGGED", set()).clear()
    monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", GOOD_KEY)
    credential_crypto.reset_credential_key_cache()
    r = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    _assert_temp_sqlite(r, tmp_path)
    r.register_database(_mk("good_one", PW_GOOD, "A good one"))
    # the bad row: encrypted under a DIFFERENT key
    monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", OTHER_KEY)
    credential_crypto.reset_credential_key_cache()
    r.register_database(_mk("bad_one", PW_BAD, "B bad one"))
    monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", GOOD_KEY)
    credential_crypto.reset_credential_key_cache()
    r.register_database(_mk("good_two", PW_GOOD2, "C good two"))
    r.register_database(_mk("no_pw", "", "D no password"))
    yield r
    credential_crypto.reset_credential_key_cache()
    getattr(registry_mod, "_UNREADABLE_LOGGED", set()).clear()


@pytest.fixture
def client(reg, tmp_path, monkeypatch):
    _assert_temp_sqlite(reg, tmp_path)
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(self, "__dict__", reg.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _no_leak(blob):
    s = str(blob)
    for needle in (PW_GOOD, PW_BAD, PW_GOOD2, "enc:v1:", "RE_DB_CREDENTIAL_KEY",
                   "TRELLIS_DB_CREDENTIAL_KEY", "could not be decrypted", "InvalidToken"):
        assert needle not in s, f"leaked {needle!r}"


def test_list_returns_every_row_and_marks_only_the_bad_one(reg):
    rows = reg.list_databases()
    assert {d.slug for d in rows} == {"good_one", "bad_one", "good_two", "no_pw"}
    by = {d.slug: d for d in rows}
    assert by["bad_one"].credential_status == "unreadable"
    assert by["bad_one"].db_password == ""
    assert by["bad_one"].display_name == "B bad one"           # non-secret fields intact
    assert by["bad_one"].db_user == "fake_user" and by["bad_one"].description == "kept"
    assert by["good_one"].credential_status == "ok" and by["good_one"].db_password == PW_GOOD
    assert by["good_two"].db_password == PW_GOOD2
    assert by["no_pw"].credential_status == "none"
    _no_leak([(d.slug, d.credential_status, d.db_user, d.description) for d in rows])


def test_other_list_shapes_tolerate_too(reg):
    reg.set_database_group("bad_one", "g1")
    assert len(reg.list_databases(db_type="postgresql")) == 4
    assert {d.slug for d in reg.list_databases_in_group("g1")} == {"bad_one"}


def test_get_database_refuses_for_the_bad_slug_only(reg):
    with pytest.raises(ValueError) as ei:
        reg.get_database("bad_one")
    assert type(ei.value).__name__ == "CredentialUnreadableError"
    assert str(ei.value) == "credential unreadable: re-enter credentials for bad_one"
    _no_leak(str(ei.value))
    assert reg.get_database("good_one").db_password == PW_GOOD
    assert reg.get_database("nope") is None


def test_allow_unreadable_returns_marker_not_secret(reg):
    d = reg.get_database("bad_one", allow_unreadable=True)
    assert d.credential_status == "unreadable" and d.db_password == ""
    assert reg.database_exists("bad_one") is True


def test_reentering_credentials_heals_the_row(reg):
    reg.update_database_credentials("bad_one", "fake_user", "fresh-fake-pw")
    d = reg.get_database("bad_one")
    assert d.credential_status == "ok" and d.db_password == "fresh-fake-pw"


def test_warning_logged_once_per_slug_with_no_secret(reg, caplog):
    with caplog.at_level(logging.WARNING, logger="resource_explorer.registry"):
        reg.list_databases()
        reg.list_databases()
        reg.get_database("bad_one", allow_unreadable=True)
    msgs = [r.getMessage() for r in caplog.records if "unreadable" in r.getMessage()]
    assert len(msgs) == 1, msgs
    assert "bad_one" in msgs[0] and "ValueError" in msgs[0]
    _no_leak(msgs)
    assert not any(r.exc_info for r in caplog.records if "unreadable" in r.getMessage())


def test_api_list_carries_marker_and_no_password(client):
    resp = client.get("/api/databases/")
    assert resp.status_code == 200
    rows = {r["slug"]: r for r in resp.json()}
    assert set(rows) == {"good_one", "bad_one", "good_two", "no_pw"}
    assert rows["bad_one"]["credential_status"] == "unreadable"
    assert rows["bad_one"]["credential_reason"] == "credential unreadable · re-enter credentials"
    assert rows["good_one"]["credential_status"] == "ok" and rows["good_one"]["credential_reason"] == ""
    assert rows["no_pw"]["credential_status"] == "none"
    assert all("db_password" not in r for r in rows.values())
    _no_leak(resp.text)
    one = client.get("/api/databases/bad_one")
    assert one.status_code == 200 and one.json()["credential_status"] == "unreadable"
    _no_leak(one.text)


def test_survey_route_refuses_for_the_bad_database_and_never_connects(client, reg, monkeypatch):
    import psycopg2

    def boom(*a, **k):
        raise AssertionError("connected")
    monkeypatch.setattr(psycopg2, "connect", boom)
    resp = client.post("/api/databases/bad_one/survey", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "credential unreadable: re-enter credentials for bad_one"
    _no_leak(resp.text)
    assert reg.get_database("bad_one", allow_unreadable=True).status.value == "active"  # not flipped to indexing
    # the good database is unaffected by the refusal (it passes the credential gate; no password -> 400 not 409)
    assert client.post("/api/databases/no_pw/survey", json={}).status_code == 400


def test_credentials_route_lets_the_user_re_enter(client, monkeypatch):
    import psycopg2

    from resource_explorer.surveyors.database.connection import PostgreSQLConnection

    def _no_driver(*a, **k):
        raise AssertionError("real psycopg2.connect reached")

    monkeypatch.setattr(psycopg2, "connect", _no_driver)
    monkeypatch.setattr(PostgreSQLConnection, "connect", lambda self: None)
    r = client.patch("/api/databases/bad_one/credentials",
                     json={"db_user": "fake_user", "db_password": "fresh-fake-pw"})
    assert r.status_code == 200 and r.json()["credential_status"] == "ok"


def test_find_candidates_mark_the_registered_unreadable_row(reg):
    from resource_explorer.web.routes.db_servers import _build_candidates
    listed = [{"name": "bad_one"}, {"name": "good_one"}, {"name": "fresh"}]
    cands = _build_candidates(reg, "db.example.invalid", 5432, listed, None, None)
    by = {c.name: c for c in cands}
    assert by["bad_one"].is_registered and by["bad_one"].credential_status == "unreadable"
    assert by["good_one"].credential_status == "ok"
    assert by["fresh"].credential_status is None and not by["fresh"].is_registered


def test_scheduler_database_pass_fails_only_the_bad_database(reg):
    from resource_explorer import scheduler
    name, host, errors = scheduler._run_db_survey("bad_one", "db_schema_inventory", reg)
    assert errors == ["credential unreadable: re-enter credentials for bad_one"]
    # a good row does not hit the credential refusal
    _n, _h, errs = scheduler._run_db_survey("nope", "x", reg)
    assert "not found" in errs[0]
