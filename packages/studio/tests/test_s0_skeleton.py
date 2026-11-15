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
        self.user_pwd = "demo-default-from-settings"   # what pyegeria does with user_pwd=None

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
                                "user_id": "peterprofile", "user_pwd": studio_egeria.no_password()}]
    assert c.user_pwd is None


def test_settings_have_no_service_account(settings):
    fields = set(StudioSettings.model_fields)
    assert not {f for f in fields if "password" in f or f in ("user_id", "egeria_user")}


def test_origin_of_is_exact():
    assert origin_of("http://localhost:8880/") == "http://localhost:8880"
    assert origin_of("https://laz.example:8840/x?y") == "https://laz.example:8840"
    assert origin_of("localhost:8880") == ""



# ---- S0 read-back R1: no password on any client; a rejected token is a 401, never a second sign-in ----------

def _real_client(settings, monkeypatch):
    """A REAL EgeriaTech built by Studio, with pyegeria's settings carrying a demo default password."""
    from pyegeria.core.config import settings as pyeg_settings
    from pyegeria.core._base_server_client import BaseServerClient
    monkeypatch.setattr(pyeg_settings.User_Profile, "user_pwd", "secret-demo-default", raising=False)

    async def origin(self):          # a sub-client asks the platform for its origin when it's built: no network here
        return "test platform"
    monkeypatch.setattr(BaseServerClient, "async_get_platform_origin", origin)
    from trellis_studio.egeria import user_client
    return user_client({"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": fake_egeria_token()},
                       settings)


def test_no_password_on_the_client_or_any_sub_client(settings, monkeypatch):
    c = _real_client(settings, monkeypatch)
    assert c.user_pwd is None
    sub = c.classification_manager                 # built lazily, after the scrub was installed
    assert sub.user_pwd is None
    from trellis_studio.egeria import assert_no_password
    assert_no_password(c)
    c.close_session()


def test_a_rejected_token_is_401_with_no_second_sign_in(settings, monkeypatch):
    import asyncio
    import httpx
    c = _real_client(settings, monkeypatch)
    sub = c.classification_manager
    minted = []

    async def no_mint(*a, **k):
        minted.append(a)
        return "should-never-happen"
    monkeypatch.setattr(type(sub), "_async_create_egeria_bearer_token", no_mint)
    monkeypatch.setattr(type(c), "_async_create_egeria_bearer_token", no_mint, raising=False)
    sub.session = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    with pytest.raises(PyegeriaUnauthorizedException):
        asyncio.run(sub._async_get_elements_by_property_value("x", ["qualifiedName"], page_size=1))
    assert minted == [] and sub.user_pwd is None and c.user_pwd is None


def test_the_app_answers_pyegerias_unauthorized_with_401(app, settings, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    import trellis_auth

    @app.get("/api/_test_refused")
    async def refused():
        raise PyegeriaUnauthorizedException(httpx.Response(401, request=httpx.Request("GET", "https://e.test/x")),
                                            context={"caller method": "test"})
    tok = trellis_auth.create_access_token("peterprofile", fake_egeria_token(), app.state.auth_config)
    r = TestClient(app).get("/api/_test_refused", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and "Sign in again" in r.json()["detail"]


def test_a_request_closes_its_client_and_checks_for_a_password(settings, monkeypatch):
    import asyncio
    from trellis_studio import authoring

    class C:
        user_pwd = None
        closed = False

        def close_session(self):
            C.closed = True
    monkeypatch.setattr(authoring, "user_client", lambda user, s: C())
    out = asyncio.run(authoring.in_user_thread({"user_id": "u"}, settings, lambda c: "done"))
    assert out == "done" and C.closed

    class Leaky(C):
        def close_session(self):
            Leaky.closed = True
    monkeypatch.setattr(authoring, "user_client", lambda user, s: Leaky())

    def put_one_back(c):
        c.user_pwd = "oops"
    from trellis_studio.egeria import PasswordOnClient
    with pytest.raises(PasswordOnClient):
        asyncio.run(authoring.in_user_thread({"user_id": "u"}, settings, put_one_back))
    assert Leaky.closed


# ---- S0 advisories: one secret per box; service accounts refused as EA refuses them ---------------------

def test_no_studio_only_jwt_secret(monkeypatch):
    monkeypatch.setenv("STUDIO_JWT_SECRET", "per-app-secret-0123456789abcdef0123")
    monkeypatch.setenv("TRELLIS_JWT_SECRET", "box-secret-0123456789abcdef0123")
    assert StudioSettings().jwt_secret == "box-secret-0123456789abcdef0123"


def test_service_accounts_are_refused_on_both_ways_in(client, monkeypatch):
    s = StudioSettings(jwt_secret=SECRET, portal_secret=PORTAL_SECRET, service_accounts_raw="EAService, other",
                       re_daemon_user_id="re-daemon")
    app = create_app(s)
    c = TestClient(app)
    monkeypatch.setattr(trellis_auth, "login_with_password", lambda *a: (_ for _ in ()).throw(AssertionError("no Egeria call")))
    monkeypatch.setattr(trellis_auth, "validate_egeria_token", lambda *a: (_ for _ in ()).throw(AssertionError("no Egeria call")))
    assert c.post("/api/auth/login", json={"username": "eaSERVICE", "password": "x"}).status_code == 403
    assert c.post("/api/auth/portal", json={"portal_token": _portal_token(sub="re-daemon")}).status_code == 403


# ---- spec-cache R-A (egeria-python 8dd9e087) and the NO_PASSWORD opt-out (7dd64417), feature-detected --------

def _has(module: str, name: str) -> bool:
    import importlib
    try:
        return hasattr(importlib.import_module(module), name)
    except ImportError:
        return False


def test_studio_declares_itself_multi_user_at_startup_when_pyegeria_can(settings, monkeypatch):
    monkeypatch.delenv("PYEGERIA_MULTI_USER", raising=False)
    if not _has("pyegeria.view.egeria_spec_cache", "declare_multi_user"):
        with TestClient(create_app(settings)) as c:        # older pyegeria: starts, undeclared, never an error
            assert c.get("/health").status_code == 200
        assert studio_egeria.declare_multi_user_process() is False
        return
    from pyegeria.view.egeria_spec_cache import declare_multi_user, is_multi_user
    declare_multi_user(False)
    try:
        app = create_app(settings)
        assert not is_multi_user()                         # building the app declares nothing...
        with TestClient(app):
            assert is_multi_user()                         # ...its startup does
    finally:
        declare_multi_user(False)


def test_without_declare_multi_user_studio_says_so(monkeypatch):
    from pyegeria.view import egeria_spec_cache
    monkeypatch.delattr(egeria_spec_cache, "declare_multi_user", raising=False)   # as on 6.2.41
    assert studio_egeria.declare_multi_user_process() is False


def test_clients_use_no_password_when_pyegeria_has_it(settings, monkeypatch):
    c = _real_client(settings, monkeypatch)
    sub = c.classification_manager
    if not _has("pyegeria.core._no_password", "NO_PASSWORD"):
        assert studio_egeria.no_password() is None
        assert c.user_pwd is None and sub.user_pwd is None          # _scrub's fallback
        return
    from pyegeria.core._no_password import NO_PASSWORD
    assert studio_egeria.no_password() is NO_PASSWORD
    assert c.use_default_password is False and getattr(sub, "use_default_password", False) is False
    assert c.user_pwd is None and sub.user_pwd is None


def test_in_user_thread_binds_spec_reads_to_the_user(settings, monkeypatch):
    import asyncio
    from trellis_studio import authoring
    _real_client(settings, monkeypatch)                            # the patches a real client needs
    user = {"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": fake_egeria_token()}
    if not _has("pyegeria.view.egeria_spec_cache", "as_client"):
        with studio_egeria.bound_to(object()):                    # older pyegeria: a no-op, never an error
            pass
        return
    from pyegeria.view.egeria_spec_cache import current_spec_identity
    seen = asyncio.run(authoring.in_user_thread(user, settings, lambda c: current_spec_identity()))
    assert seen and "peterprofile" in seen
