"""E1: EA embedded in Trellis Studio (BRIEF-TRELLIS-STUDIO-V1-2026-11-14; owner decisions A and B).

- frame-ancestors from ADVISOR_EMBED_ORIGINS on every response (a 401 too);
- /api/embed/config is public and lists only real origins;
- decision A: a Studio session JWT (trellis-auth, signed with the box's TRELLIS_JWT_SECRET) is accepted by EA's own
  check, /api/embed/whoami; another secret or an expired one is not; a service account is refused;
- decision B: "Open in Studio" saves EA's elicited spec as a pyegeria.authoring SpecDraft, as the user, and answers
  only a draft reference; "Ask about" reads a draft by reference as the user;
- the page: no hard-coded postMessage origin, every listener checks the origin (and the source), no password form
  in embed mode.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

import trellis_auth
from advisor import auth

STATIC = Path(__file__).resolve().parents[2] / "advisor" / "web" / "static"


def _app_secret() -> str:
    """The secret EA's login middleware verifies with (fixed when the app was first imported)."""
    from advisor.web.app import app
    for m in app.user_middleware:
        cfg = (m.kwargs or {}).get("config")
        if cfg is not None and getattr(cfg, "jwt_secret", None):
            return cfg.jwt_secret
    raise AssertionError("EA's LoginRequiredMiddleware not found")


@pytest.fixture(scope="module")
def client():
    from advisor.web.app import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path, client):
    """The box-wide secret, as a deploy sets it: TRELLIS_JWT_SECRET only (no ADVISOR_JWT_SECRET), equal to what the
    running EA verifies with."""
    global SECRET
    SECRET = _app_secret()
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN", "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ",
                "ADVISOR_JWT_SECRET", "ADVISOR_EMBED_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRELLIS_JWT_SECRET", SECRET)
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path))
    auth.reset_policy_cache()
    yield
    auth.reset_policy_cache()


def test_ea_takes_the_box_wide_secret_when_it_has_none_of_its_own(monkeypatch):
    monkeypatch.setenv("TRELLIS_JWT_SECRET", "the-box-secret-0123456789abcdef0123")
    assert auth._jwt_secret() == "the-box-secret-0123456789abcdef0123"
    monkeypatch.setenv("ADVISOR_JWT_SECRET", "ea-only-secret-0123456789abcdef0123")
    assert auth._jwt_secret() == "ea-only-secret-0123456789abcdef0123"   # then a Studio JWT would NOT verify here


def _egeria_token(sub="peterprofile"):
    import base64, json
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    now = int(time.time())
    return f"{b({'alg': 'RS256'})}.{b({'sub': sub, 'iat': now, 'exp': now + 3600})}.sig"


SECRET = ""


def studio_jwt(sub="peterprofile", secret=None):
    """Exactly what Trellis Studio mints: trellis_auth.create_access_token under the box's shared secret."""
    return trellis_auth.create_access_token(sub, _egeria_token(sub),
                                            trellis_auth.AuthConfig(jwt_secret=secret or SECRET))


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---- framing --------------------------------------------------------------------------------------------

def test_frame_ancestors_on_every_response_including_a_401(client):
    for path in ("/", "/api/embed/config", "/api/reports"):
        r = client.get(path)
        csp = r.headers.get("content-security-policy", "")
        assert csp == "frame-ancestors 'self' http://localhost:8840 http://localhost:7840", (path, r.status_code, csp)
    assert client.get("/api/reports").status_code == 401


def test_embed_origins_from_env_and_junk_dropped(client, monkeypatch):
    from advisor.config import settings
    monkeypatch.setattr(settings, "advisor_embed_origins",
                        "https://studio.example:8840, javascript:alert(1), http://laz:8840/path, *, http://laz:7840")
    body = client.get("/api/embed/config").json()
    assert body == {"embed_origins": ["https://studio.example:8840", "http://laz:7840"]}
    assert client.get("/").headers["content-security-policy"] == \
        "frame-ancestors 'self' https://studio.example:8840 http://laz:7840"


def test_empty_list_means_only_self(client, monkeypatch):
    from advisor.config import settings
    monkeypatch.setattr(settings, "advisor_embed_origins", "")
    assert client.get("/").headers["content-security-policy"] == "frame-ancestors 'self'"


# ---- decision A: a Studio session JWT is valid at EA ------------------------------------------------------

def test_studio_jwt_is_accepted_by_eas_own_check(client):
    r = client.get("/api/embed/whoami", headers=_bearer(studio_jwt()))
    assert r.status_code == 200 and r.json()["user_id"] == "peterprofile"


def test_another_secret_or_expired_is_refused(client):
    assert client.get("/api/embed/whoami", headers=_bearer(studio_jwt(secret="some-other-box-secret-0123456789"))).status_code == 401
    expired = jwt.encode({"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": _egeria_token(),
                          "iat": int(time.time()) - 60, "exp": int(time.time()) - 5}, SECRET, algorithm="HS256")
    assert client.get("/api/embed/whoami", headers=_bearer(expired)).status_code == 401
    assert client.get("/api/embed/whoami").status_code == 401


def test_service_account_is_refused(client, monkeypatch):
    monkeypatch.setattr(auth, "is_service_account", lambda uid: uid == "easervice")
    assert client.get("/api/embed/whoami", headers=_bearer(studio_jwt("easervice"))).status_code == 403


# ---- decision B: Open in Studio saves a SpecDraft as the user ---------------------------------------------

EA_DRAFT = {"draft_id": "d-1", "answers": {"Heading": "Open glossaries", "Description": "Glossaries in use."},
            "target_type": "Glossary", "action_function": "GlossaryManager.find_glossaries", "columns": []}


class _Client:
    def __init__(self, uid):
        self.user_id = uid


@pytest.fixture
def store(monkeypatch):
    import pyegeria.authoring as a
    from advisor.web import embed
    state = {"saved": [], "load": None, "built_for": []}

    class Store:
        def __init__(self, client, run=None, **kw):
            self.client = client

        async def async_save(self, uid, draft):
            state["saved"].append((uid, self.client.user_id, draft))
            return draft.qualified_name

        async def async_load(self, uid, kind, name):
            return state["load"]

    def fake_client(request):
        creds = auth.get_egeria_credentials(request)
        state["built_for"].append(creds["user_id"])
        return _Client(creds["user_id"])

    monkeypatch.setattr(a, "EgeriaDraftStore", Store)
    monkeypatch.setattr(a, "inprocess_runner", lambda c: (lambda md, d: {"success": True}))
    monkeypatch.setattr(embed, "_user_client", fake_client)
    return state


def test_open_in_studio_saves_a_spec_draft_as_the_user(client, store, monkeypatch):
    import advisor.report_draft as rd
    monkeypatch.setattr(rd, "resolve_report_draft", lambda draft_id, user_id=None, role=None:
                        (None, EA_DRAFT) if (draft_id, user_id) == ("d-1", "peterprofile") else None)
    r = client.post("/api/studio/open-draft", json={"draft_id": "d-1", "label": "Open Glossaries"},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft_ref"] == {"kind": "spec", "name": "Open Glossaries"}
    assert body["qualified_name"] == "ReportSpec::Open Glossaries"
    uid, client_user, draft = store["saved"][0]
    assert uid == client_user == "peterprofile"
    assert draft.kind == "spec" and draft.format_set.heading == "Open glossaries"
    assert draft.format_set.action.function == "GlossaryManager.find_glossaries"
    assert draft.derived_from == "ea:report-draft:d-1"


def test_open_in_studio_someone_elses_draft_is_404(client, store, monkeypatch):
    import advisor.report_draft as rd
    monkeypatch.setattr(rd, "resolve_report_draft", lambda *a, **k: None)
    r = client.post("/api/studio/open-draft", json={"draft_id": "d-9", "label": "x"}, headers=_bearer(studio_jwt()))
    assert r.status_code == 404 and store["saved"] == []


def test_open_in_studio_refusal_is_pyegerias_words(client, monkeypatch):
    import pyegeria.authoring as a
    import advisor.report_draft as rd
    from advisor.web import embed
    words = "Couldn't check whether user id 'peterprofile' is also a governance zone's name; nothing was saved."

    class Store:
        def __init__(self, *a, **k):
            pass

        async def async_load(self, uid, kind, name):
            return None

        async def async_save(self, uid, draft):
            raise a.AuthoringRefused(words)
    monkeypatch.setattr(a, "EgeriaDraftStore", Store)
    monkeypatch.setattr(a, "inprocess_runner", lambda c: None)
    monkeypatch.setattr(embed, "_user_client", lambda request: _Client("peterprofile"))
    monkeypatch.setattr(rd, "resolve_report_draft", lambda *a, **k: (None, EA_DRAFT))
    r = client.post("/api/studio/open-draft", json={"draft_id": "d-1", "label": "x"}, headers=_bearer(studio_jwt()))
    assert r.status_code == 409 and r.json()["detail"] == words


def test_no_egeria_token_no_client(monkeypatch):
    from fastapi import HTTPException
    from advisor.web import embed
    monkeypatch.setattr(auth, "get_egeria_credentials", lambda request: {"user_id": "peterprofile", "password": "", "token": ""})
    built = []
    import pyegeria.egeria_tech_client as etc
    monkeypatch.setattr(etc, "EgeriaTech", lambda **kw: built.append(kw))
    with pytest.raises(HTTPException) as e:
        embed._user_client(object())
    assert e.value.status_code == 401 and built == []


def test_open_in_studio_without_a_session_is_401(client):
    assert client.post("/api/studio/open-draft", json={"draft_id": "d-1", "label": "x"}).status_code == 401


# ---- Ask about: a draft by reference, read as the user ----------------------------------------------------

def test_draft_context_reads_by_reference(client, store):
    import pyegeria.authoring as a
    store["load"] = a.new_from(template="find-spec", new_label="Open Glossaries")
    r = client.post("/api/studio/draft-context", json={"draft_ref": {"kind": "spec", "name": "Open Glossaries"}},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 200 and "Open Glossaries" in r.json()["markdown"]
    assert store["built_for"] == ["peterprofile"]


def test_draft_context_missing_and_bad_ref(client, store):
    r = client.post("/api/studio/draft-context", json={"draft_ref": {"kind": "spec", "name": "Nope"}},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 404
    r = client.post("/api/studio/draft-context", json={"draft_ref": {"kind": "table", "name": "x"}},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 400


# ---- the page --------------------------------------------------------------------------------------------

def test_no_hard_coded_postmessage_origin():
    js = (STATIC / "auth.js").read_text()
    assert "localhost:8885" not in js
    assert "const allowed = [...embedOrigins, window.location.origin];" in js


def test_trellis_auth_needs_parent_and_listed_origin():
    js = (STATIC / "auth.js").read_text()
    assert "event.source !== window.parent || !embedOrigins.includes(event.origin)" in js
    # every postMessage names a target origin, never '*'
    assert not re.search(r"postMessage\([^)]*['\"]\*['\"]\s*\)", js + (STATIC / "embed.js").read_text())


def test_embed_mode_removes_the_password_form():
    js = (STATIC / "auth.js").read_text()
    assert "document.getElementById('login-overlay')?.remove();" in js
    assert "if (EMBED) { showEmbedSignIn(" in js
    assert "Sign in to Studio to use EA here." in js


def test_embed_mode_never_reuses_a_stored_session():
    js = (STATIC / "auth.js").read_text()
    branch = js[js.index("if (EMBED) {\n      // No password form"):js.index("postToParent({ type: 'ea.ready' });")]
    assert "clearToken();" in branch and "isAuthenticated()" not in branch


def test_embed_listener_checks_source_and_the_accepted_origin():
    js = (STATIC / "embed.js").read_text()
    assert "if (event.source !== window.parent) return;" in js
    assert "if (!parent || event.origin !== parent) return;" in js


def test_index_loads_embed_js_after_auth():
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/auth.js") < html.index("/static/embed.js")



# ---- E1 read-back R1: no password on any client; a rejected token is a 401; clients closed ----------------

def _real_client(monkeypatch):
    from pyegeria.core.config import settings as pyeg_settings
    from pyegeria.core._base_server_client import BaseServerClient
    from advisor.web import embed
    monkeypatch.setattr(pyeg_settings.User_Profile, "user_pwd", "secret-demo-default", raising=False)

    async def origin(self):          # a sub-client asks the platform for its origin when built: no network here
        return "test platform"
    monkeypatch.setattr(BaseServerClient, "async_get_platform_origin", origin)
    monkeypatch.setattr(auth, "get_egeria_credentials",
                        lambda request: {"user_id": "peterprofile", "password": "", "token": _egeria_token()})
    import advisor.mcp_config as mc
    monkeypatch.setattr(mc, "get_pyegeria_platform_config",
                        lambda *a, **k: {"view_server": "qs-view-server", "platform_url": "https://egeria.test:9443"})
    return embed._user_client(object())


def test_embed_client_holds_no_password_anywhere(monkeypatch):
    c = _real_client(monkeypatch)
    assert c.user_pwd is None and c.classification_manager.user_pwd is None


def test_a_rejected_token_is_401_with_no_second_sign_in(monkeypatch):
    import asyncio
    import httpx
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    from advisor.web import embed
    c = _real_client(monkeypatch)
    sub = c.classification_manager
    minted = []

    async def no_mint(*a, **k):
        minted.append(a)
        return "never"
    monkeypatch.setattr(type(sub), "_async_create_egeria_bearer_token", no_mint)
    sub.session = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    with pytest.raises(PyegeriaUnauthorizedException) as e:
        asyncio.run(sub._async_get_elements_by_property_value("x", ["qualifiedName"], page_size=1))
    assert minted == [] and sub.user_pwd is None
    mapped = embed._authoring_error(e.value, "test")
    assert mapped.status_code == 401 and mapped.detail == "Sign in to Studio to use EA here."


def test_each_request_closes_its_client(client, monkeypatch):
    import pyegeria.authoring as a
    from advisor.web import embed
    closed = []

    class C(_Client):
        user_pwd = None

        def close_session(self):
            closed.append(self.user_id)

    class Store:
        def __init__(self, *args, **kw):
            pass

        async def async_load(self, uid, kind, name):
            return a.new_from(template="find-spec", new_label=name)
    monkeypatch.setattr(a, "EgeriaDraftStore", Store)
    monkeypatch.setattr(embed, "_user_client", lambda request: C("peterprofile"))
    r = client.post("/api/studio/draft-context", json={"draft_ref": {"kind": "spec", "name": "X"}},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 200 and closed == ["peterprofile"]


# ---- never overwrite: a repeat "Open in Studio" with a name the user already has --------------------------

def test_open_in_studio_never_overwrites_an_existing_draft(client, store, monkeypatch):
    import pyegeria.authoring as a
    import advisor.report_draft as rd
    monkeypatch.setattr(rd, "resolve_report_draft", lambda *args, **kw: (None, EA_DRAFT))
    store["load"] = a.new_from(template="find-spec", new_label="Open Glossaries")   # already in their zone
    r = client.post("/api/studio/open-draft", json={"draft_id": "d-1", "label": "Open Glossaries"},
                    headers=_bearer(studio_jwt()))
    assert r.status_code == 409 and "already have a Studio draft named 'Open Glossaries'" in r.json()["detail"]
    assert store["saved"] == []


def test_open_in_studio_unreadable_check_saves_nothing(client, store, monkeypatch):
    import pyegeria.authoring as a
    import advisor.report_draft as rd
    monkeypatch.setattr(rd, "resolve_report_draft", lambda *args, **kw: (None, EA_DRAFT))

    class Store:
        def __init__(self, *args, **kw):
            pass

        async def async_load(self, uid, kind, name):
            raise a.AuthoringRefused("Couldn't confirm whether Egeria holds a report spec named 'x'.")

        async def async_save(self, uid, draft):
            raise AssertionError("must not save")
    monkeypatch.setattr(a, "EgeriaDraftStore", Store)
    r = client.post("/api/studio/open-draft", json={"draft_id": "d-1", "label": "x"}, headers=_bearer(studio_jwt()))
    assert r.status_code == 409 and "Couldn't confirm" in r.json()["detail"]


# ---- spec-cache R-A (egeria-python 8dd9e087) and the NO_PASSWORD opt-out (7dd64417), feature-detected --------

def _has(module: str, name: str) -> bool:
    import importlib
    try:
        return hasattr(importlib.import_module(module), name)
    except ImportError:
        return False


def test_ea_declares_itself_multi_user_at_startup_when_pyegeria_can(monkeypatch):
    import inspect
    from advisor.web import app as ea_app
    src = inspect.getsource(ea_app._startup)
    assert src.index("declare_multi_user_process()") < src.index("PgVectorStore")   # before any spec read
    if not _has("pyegeria.view.egeria_spec_cache", "declare_multi_user"):
        assert ea_app.declare_multi_user_process() is False          # older pyegeria: said, never an error
        return
    from pyegeria.view.egeria_spec_cache import declare_multi_user, is_multi_user
    monkeypatch.delenv("PYEGERIA_MULTI_USER", raising=False)
    try:
        assert ea_app.declare_multi_user_process() is True and is_multi_user()
    finally:
        declare_multi_user(False)


def test_without_declare_multi_user_startup_says_so(monkeypatch):
    from pyegeria.view import egeria_spec_cache
    from advisor.web import app as ea_app
    monkeypatch.delattr(egeria_spec_cache, "declare_multi_user", raising=False)   # as on 6.2.41
    assert ea_app.declare_multi_user_process() is False


def test_embed_clients_use_no_password_when_pyegeria_has_it(monkeypatch):
    from advisor.web import embed
    c = _real_client(monkeypatch)
    if not _has("pyegeria.core._no_password", "NO_PASSWORD"):
        assert embed.no_password() is None
        assert c.user_pwd is None and c.classification_manager.user_pwd is None   # _scrub's fallback
        return
    from pyegeria.core._no_password import NO_PASSWORD
    assert embed.no_password() is NO_PASSWORD
    sub = c.classification_manager
    assert c.use_default_password is False and getattr(sub, "use_default_password", False) is False
    assert c.user_pwd is None and sub.user_pwd is None


def test_as_user_binds_spec_reads_to_the_user(monkeypatch):
    from advisor.web import embed
    _real_client(monkeypatch)
    if not _has("pyegeria.view.egeria_spec_cache", "as_client"):
        with embed.bound_to(object()):     # older pyegeria: a no-op, never an error
            pass
        return
    from pyegeria.view.egeria_spec_cache import current_spec_identity
    seen = embed._as_user(object(), lambda c: current_spec_identity())
    assert seen and "peterprofile" in seen
