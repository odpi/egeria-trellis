"""S0: sign-in, a 401 everywhere without a token, and no Egeria call without a user token."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import trellis_auth
from trellis_studio.app import create_app
from trellis_studio.config import StudioSettings, origin_of
from trellis_studio import egeria as studio_egeria

from conftest import PORTAL_SECRET, SECRET, fake_egeria_token


# ---- public things ---------------------------------------------------------------------------------------

def test_health_is_public_and_claims_nothing_about_egeria(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "egeria" not in str(body).lower()


def test_page_shell_and_static_are_public(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/studio.js").status_code == 200
    assert client.get("/static/trellis-ui/themes.css").status_code == 200


def test_policy_lists_portal_origins_and_nothing_secret(client):
    body = client.get("/api/auth/policy").json()
    assert body["portal_origins"] == ["http://localhost:8800"]
    assert SECRET not in str(body) and PORTAL_SECRET not in str(body)


# ---- 401 everywhere else ---------------------------------------------------------------------------------

def _private_routes(app):
    from starlette.routing import Route
    public = set(app.state.policy.public_paths)
    for r in app.routes:
        if isinstance(r, Route) and r.path not in public and not r.path.startswith("/static"):
            for m in sorted(r.methods - {"HEAD"}):
                yield m, r.path


def test_every_private_route_is_401_without_a_token(app, client):
    routes = list(_private_routes(app))
    assert ("GET", "/api/config") in routes and ("GET", "/api/auth/me") in routes
    for method, path in routes:
        r = client.request(method, path)
        assert r.status_code == 401, (method, path, r.status_code)


def test_wrong_secret_or_expired_jwt_is_401(client, app):
    bad = trellis_auth.create_access_token("peterprofile", fake_egeria_token(),
                                           trellis_auth.AuthConfig(jwt_secret="another-box-secret-0123456789abcdef0123"))
    assert client.get("/api/config", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    expired = jwt.encode({"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": fake_egeria_token(),
                          "iat": int(time.time()) - 100, "exp": int(time.time()) - 10}, SECRET, algorithm="HS256")
    assert client.get("/api/config", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_signed_in_config_gives_advisor_origin(authed):
    body = authed.get("/api/config").json()
    assert body["advisor_origin"] == "http://localhost:8880"
    assert body["public_origin"] == "http://localhost:8840"
    me = authed.get("/api/auth/me").json()
    assert me["user_id"] == "peterprofile" and "egeria_token" not in me


def test_no_secret_no_app():
    with pytest.raises(RuntimeError, match="TRELLIS_JWT_SECRET"):
        create_app(StudioSettings(jwt_secret=""))


# ---- sign-in ---------------------------------------------------------------------------------------------

def test_password_login_mints_a_jwt_that_verifies_with_the_shared_secret(client, monkeypatch):
    seen = {}

    def fake_login(user, pwd, cfg):
        seen["args"] = (user, pwd)
        return fake_egeria_token(user)

    monkeypatch.setattr(trellis_auth, "login_with_password", fake_login)
    r = client.post("/api/auth/login", json={"username": "peterprofile", "password": "pw"})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    claims = jwt.decode(tok, SECRET, algorithms=["HS256"])  # the same check EA applies (decision A)
    assert claims["sub"] == "peterprofile" and claims["egeria_token"]
    assert "password" not in claims and "pw" not in str(claims)
    assert seen["args"] == ("peterprofile", "pw")


def test_password_login_failure_is_401_in_words(client, monkeypatch):
    monkeypatch.setattr(trellis_auth, "login_with_password", lambda *a: None)
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 401 and "Egeria did not sign you in" in r.json()["detail"]


def _portal_token(**over):
    body = {"sub": "peterprofile", "role": "user", "display_name": "Peter Profile",
            "egeria_token": fake_egeria_token(), "exp": int(time.time()) + 60}
    body.update(over)
    return jwt.encode(body, PORTAL_SECRET, algorithm="HS256")


def test_portal_sso_checks_the_token_with_egeria_then_signs_in(client, monkeypatch):
    monkeypatch.setattr(trellis_auth, "validate_egeria_token", lambda tok, cfg: True)
    r = client.post("/api/auth/portal", json={"portal_token": _portal_token()})
    assert r.status_code == 200
    claims = jwt.decode(r.json()["access_token"], SECRET, algorithms=["HS256"])
    assert claims["display_name"] == "Peter Profile"


def test_portal_sso_refused_when_egeria_refuses(client, monkeypatch):
    monkeypatch.setattr(trellis_auth, "validate_egeria_token", lambda tok, cfg: False)
    r = client.post("/api/auth/portal", json={"portal_token": _portal_token()})
    assert r.status_code == 401


def test_portal_sso_wrong_secret_is_401(client, monkeypatch):
    monkeypatch.setattr(trellis_auth, "validate_egeria_token", lambda tok, cfg: True)
    forged = jwt.encode({"sub": "x", "egeria_token": fake_egeria_token(), "exp": int(time.time()) + 60},
                        "not-the-portal-secret-0123456789abcdef", algorithm="HS256")
    assert client.post("/api/auth/portal", json={"portal_token": forged}).status_code == 401


# ---- no Egeria call without a user token -----------------------------------------------------------------

class _Recorder:
    built = []

    def __init__(self, **kw):
        _Recorder.built.append(kw)
        self.token = None

    def set_bearer_token(self, t):
        self.token = t

    def create_egeria_bearer_token(self, *a, **k):  # the configured-credentials fallback Studio must never use
        raise AssertionError("Studio must never sign in with configured credentials")


def test_user_client_refuses_without_a_token_before_building_anything(settings, monkeypatch):
    _Recorder.built = []
    monkeypatch.setattr(studio_egeria, "_client_class", lambda: _Recorder)
    with pytest.raises(HTTPException) as e:
        studio_egeria.user_client({"sub": "peterprofile", "user_id": "peterprofile"}, settings)
    assert e.value.status_code == 401
    assert _Recorder.built == []


def test_user_client_runs_as_the_signed_in_user(settings, monkeypatch):
    _Recorder.built = []
    monkeypatch.setattr(studio_egeria, "_client_class", lambda: _Recorder)
    tok = fake_egeria_token()
    c = studio_egeria.user_client({"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": tok}, settings)
    assert c.token == tok
    assert _Recorder.built == [{"view_server": "qs-view-server", "platform_url": "https://egeria.test:9443",
                                "user_id": "peterprofile"}]


def test_settings_have_no_service_account(settings):
    fields = set(StudioSettings.model_fields)
    assert not {f for f in fields if "password" in f or f in ("user_id", "egeria_user")}


def test_origin_of_is_exact():
    assert origin_of("http://localhost:8880/") == "http://localhost:8880"
    assert origin_of("https://laz.example:8840/x?y") == "https://laz.example:8840"
    assert origin_of("localhost:8880") == ""
