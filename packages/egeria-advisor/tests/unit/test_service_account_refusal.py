"""EA refuses to mint a session for a service account (2026-10-09).

Resource Explorer runs its background work as its own Egeria account and
refuses interactive sign-in as it. RE and EA share TRELLIS_JWT_SECRET and the
JWT has no audience, so EA could mint a session RE would accept. EA therefore
refuses, from configuration only (no RE import): `RE_DAEMON_USER_ID` and the
comma-separated `TRELLIS_SERVICE_ACCOUNTS`.

Every Egeria-touching function is replaced by one that fails the test if
called: the refusal must come before any Egeria call.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from advisor import auth

DETAIL = "this is a service account; sign in as yourself"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("RE_DAEMON_USER_ID", "TRELLIS_SERVICE_ACCOUNTS"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client(monkeypatch):
    from advisor.web.app import app
    return TestClient(app, raise_server_exceptions=False)


def _no_egeria(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Egeria was called before the refusal")
    monkeypatch.setattr("advisor.auth.login_with_password", boom)
    monkeypatch.setattr("advisor.auth.validate_egeria_token", boom)
    monkeypatch.setattr("advisor.auth.validate_egeria_credentials", boom)


# --- the predicate ---------------------------------------------------------

def test_unset_vars_refuse_nobody():
    assert not auth.is_service_account("resourceexplorernpa")
    assert not auth.is_service_account("")


def test_matching_is_trimmed_and_case_insensitive(monkeypatch):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "  ResourceExplorerNPA ")
    monkeypatch.setenv("TRELLIS_SERVICE_ACCOUNTS", "svc-a , Svc-B,,")
    for uid in ("resourceexplorernpa", " RESOURCEEXPLORERNPA", "svc-a", "SVC-B "):
        assert auth.is_service_account(uid), uid
    assert not auth.is_service_account("peterprofile")


# --- password path ---------------------------------------------------------

def test_password_login_refused_before_egeria(client, monkeypatch):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "resourceexplorernpa")
    _no_egeria(monkeypatch)
    r = client.post("/api/auth/login", json={"username": " ResourceExplorerNPA", "password": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == DETAIL


def test_password_login_refused_via_service_accounts_list(client, monkeypatch):
    monkeypatch.setenv("TRELLIS_SERVICE_ACCOUNTS", "other,svc-x")
    _no_egeria(monkeypatch)
    r = client.post("/api/auth/login", json={"username": "SVC-X", "password": "x"})
    assert r.status_code == 403


def test_normal_user_still_signs_in(client, monkeypatch):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "resourceexplorernpa")
    monkeypatch.setenv("TRELLIS_SERVICE_ACCOUNTS", "svc-x")
    monkeypatch.setattr("advisor.auth.login_with_password", lambda u, p: "egeria-tok")
    r = client.post("/api/auth/login", json={"username": "peterprofile", "password": "x"})
    assert r.status_code == 200
    assert r.json()["egeria_user"] == "peterprofile"


def test_unset_vars_do_not_block_login(client, monkeypatch):
    monkeypatch.setattr("advisor.auth.login_with_password", lambda u, p: "egeria-tok")
    r = client.post("/api/auth/login", json={"username": "resourceexplorernpa", "password": "x"})
    assert r.status_code == 200


# --- portal path -----------------------------------------------------------

def test_portal_token_for_service_account_refused_before_egeria(client, monkeypatch):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "resourceexplorernpa")
    _no_egeria(monkeypatch)
    monkeypatch.setattr(
        "advisor.auth.exchange_portal_token",
        lambda t: {"sub": "ResourceExplorerNPA", "egeria_token": "tok", "role": "admin"},
    )
    r = client.post("/api/auth/portal", json={"portal_token": "whatever"})
    assert r.status_code == 403
    assert r.json()["detail"] == DETAIL


def test_portal_token_for_normal_user_still_works(client, monkeypatch):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "resourceexplorernpa")
    monkeypatch.setattr(
        "advisor.auth.exchange_portal_token",
        lambda t: {"sub": "peterprofile", "egeria_token": "tok", "role": "user"},
    )
    monkeypatch.setattr("advisor.auth.validate_egeria_token", lambda t: True)
    r = client.post("/api/auth/portal", json={"portal_token": "whatever"})
    assert r.status_code == 200


# --- the mint itself (covers any path that reaches it, e.g. the CLI) -------

def test_create_access_token_refuses_a_service_account(monkeypatch):
    monkeypatch.setenv("TRELLIS_SERVICE_ACCOUNTS", "svc-x")
    with pytest.raises(HTTPException) as exc:
        auth.create_access_token(user_id="Svc-X", egeria_token="tok")
    assert exc.value.status_code == 403
    assert exc.value.detail == DETAIL


def test_create_access_token_unset_vars_mint(monkeypatch):
    assert auth.create_access_token(user_id="svc-x", egeria_token="tok")


def test_cli_login_refused_before_egeria(monkeypatch):
    from click.testing import CliRunner
    from advisor.cli.main import cli
    monkeypatch.setenv("RE_DAEMON_USER_ID", "resourceexplorernpa")
    _no_egeria(monkeypatch)
    result = CliRunner().invoke(cli, ["login", "--user", "resourceexplorernpa"], input="pw\n")
    assert result.exit_code != 0
    assert DETAIL in result.output
