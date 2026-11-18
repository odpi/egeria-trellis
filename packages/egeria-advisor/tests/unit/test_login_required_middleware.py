"""EA enforces the shared login policy (2026-09-04).

The middleware itself is tested in `packages/trellis-auth/tests/test_policy.py`.
What is tested here is EA's *wiring*: that a representative private route is
actually behind it, that the public routes the login flow needs are actually in
front of it, and that `_auth_enabled`/`_anonymous_rag_mode` are derived from the
policy rather than being the independent switches they used to be.

The app is exercised through a real `TestClient` rather than by asserting on
`app.user_middleware`: what matters is the status code a caller sees, and a
middleware present but installed in the wrong order would pass the structural
check while failing this one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from advisor import auth


@pytest.fixture(scope="module")
def client():
    from advisor.web.app import app
    # `raise_server_exceptions=False` so a route that 500s on this machine (no
    # Egeria, no Postgres) still reports its status rather than exploding — the
    # assertions here are all about 401-vs-not, and a 500 proves the request
    # got past the gate, which is the thing being distinguished.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN",
                "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ"):
        monkeypatch.delenv(var, raising=False)
    auth.reset_policy_cache()
    yield
    auth.reset_policy_cache()


def _bearer() -> dict:
    """A valid app JWT for the running app, minted through EA's own adapter.

    Through the adapter, not hand-rolled: the app's middleware verifies with
    `_base_config()`'s secret, which on a machine with no `ADVISOR_JWT_SECRET`
    is derived from the hostname. Minting the same way is what makes this test
    independent of how that secret got resolved.
    """
    token = auth.create_access_token(user_id="peterprofile", egeria_token="opaque-egeria-token")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# A representative private route
# ---------------------------------------------------------------------------

def test_api_plans_401s_without_a_token(client):
    r = client.get("/api/plans")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["error"] == "login_required"


def test_api_plans_is_reachable_with_a_valid_token(client):
    r = client.get("/api/plans", headers=_bearer())
    assert r.status_code != 401


def test_a_write_to_a_private_route_401s_without_a_token(client):
    r = client.post("/api/query", json={"query": "what is a governance zone?"})
    assert r.status_code == 401


def test_an_unknown_private_path_401s_rather_than_404ing(client):
    """A route added tomorrow is protected by default — the allowlist decides.

    404 here would mean the gate ran *after* routing and a new endpoint would
    be public until someone remembered to protect it.
    """
    r = client.get("/api/something-invented-later")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# The public paths the login flow itself needs
# ---------------------------------------------------------------------------

def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_the_portal_exchange_route_is_public(client):
    """Portal-embedded use keeps working: the exchange route must not 401.

    A bad token is a 400/401/503 *from the route's own logic*; what this pins
    is that the request reaches the handler at all — a gate in front of it
    would make Portal SSO impossible, since the Portal's caller has no app JWT
    yet, which is the entire point of the exchange.
    """
    r = client.post("/api/auth/portal", json={"portal_token": "not-a-real-token"})
    assert r.json().get("error") != "login_required"


def test_the_login_route_is_public(client):
    r = client.post("/api/auth/login", json={"username": "", "password": ""})
    assert r.status_code == 400          # the route's own validation, not the gate


def test_the_spa_shell_and_its_assets_are_public(client):
    """The shell must load in order to *show* the login form."""
    assert client.get("/").status_code == 200
    assert client.get("/api/auth/defaults").status_code == 200


def test_the_policy_route_is_public_and_reports_login_required(client):
    r = client.get("/api/auth/policy")
    assert r.status_code == 200
    assert r.json() == {"login_required": True, "anonymous_read": False}


# ---------------------------------------------------------------------------
# The switches are derived, not independent
# ---------------------------------------------------------------------------

def test_auth_enabled_and_anonymous_rag_mode_come_from_the_policy(monkeypatch):
    assert auth._auth_enabled() is True
    assert auth._anonymous_rag_mode() is False

    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    auth.reset_policy_cache()
    assert auth._anonymous_rag_mode() is True
    assert auth._auth_enabled() is True       # anonymous read is not "auth off"


def test_the_advisor_specific_var_overrides_the_shared_one(monkeypatch):
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setenv("ADVISOR_ANONYMOUS_READ", "false")
    auth.reset_policy_cache()
    assert auth._anonymous_rag_mode() is False


def test_retired_yaml_keys_are_ignored_and_warned_about(monkeypatch):
    """`auth.enabled: false` in advisor.yaml must NOT reopen a deployment."""
    warnings: list[str] = []
    monkeypatch.setattr(auth, "_auth_cfg", lambda: {"enabled": False, "anonymous_rag_mode": True})
    monkeypatch.setattr(auth.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg)))
    auth.reset_policy_cache()

    assert auth._auth_enabled() is True
    assert auth._anonymous_rag_mode() is False
    assert any("retired on 2026-09-04" in w for w in warnings)


def test_ea_declares_its_own_extra_public_paths():
    policy = auth.get_policy()
    for path in ("/", "/api/auth/me", "/api/auth/defaults", "/api/auth/policy"):
        assert policy.is_public(path), path
    assert not policy.is_public("/api/plans")
