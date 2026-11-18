"""EA Phase 4 A-I1: identity hardening (BRIEFS-EA-TRACK-A-PHASE2-5-2026-11-11 §A-I1).

1. Anonymous mode never sends "anonymous" to Egeria.
2. /api/auth/defaults never shows the service account, which joins the refusal list.
3. Portal SSO refuses a token Egeria rejects. The shapes are laz's (fixtures/egeria_live/sso_token_check.json).
4. SS-4: plans, drafts and session transcripts are per user, with an admin view of all.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import advisor.auth as auth

SSO = json.loads((Path(__file__).resolve().parent / "fixtures" / "egeria_live" / "sso_token_check.json").read_text())


@pytest.fixture(autouse=True)
def pinned_service_account(monkeypatch):
    """EA's service account is a fixed test id: a developer's shell may export EGERIA_USER (it is the
    account EA's own settings read), which would otherwise make that person's id a service account here."""
    from advisor.config import settings
    monkeypatch.setattr(settings, "daemon_user_id", "ea-service")
    monkeypatch.delenv("TRELLIS_SERVICE_ACCOUNTS", raising=False)
    monkeypatch.delenv("RE_DAEMON_USER_ID", raising=False)


def _unauthorized():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    return PyegeriaUnauthorizedException(None, None, {"reason": SSO["rejected_garbage"]["text"],
                                                      "relatedHTTPCode": 401})


def _unreachable():
    from pyegeria.core._exceptions import PyegeriaConnectionException
    return PyegeriaConnectionException({"reason": SSO["unreachable"]["text"]})


def _not_found():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    return PyegeriaNotFoundException(None, None, {"reason": "No profile for this user", "relatedHTTPCode": 404})


@pytest.fixture
def anonymous_mode(monkeypatch):
    monkeypatch.setattr(auth, "_auth_enabled", lambda: False)


# ── 1. anonymous never reaches Egeria as "anonymous" ─────────────────────────────

class TestAnonymous:
    def test_the_stand_in_user_has_no_token(self, anonymous_mode):
        request = SimpleNamespace(headers={})
        assert auth.get_current_user(request)["user_id"] == "anonymous"     # the stand-in, for the UI
        assert auth.get_egeria_token(request) is None

    @pytest.mark.parametrize("uid", ["anonymous", "Anonymous"])
    def test_anonymous_resolves_to_no_identity_never_the_service_account(self, uid):
        out = auth.resolve_egeria_credentials({"user_id": uid, "password": "", "token": ""})
        assert out == {"user_id": "", "password": "", "token": ""}
        from advisor.egeria_signin import lacks_credential
        assert lacks_credential(out["token"], out["password"])            # every live site refuses this

    def test_apply_token_refuses_an_anonymous_client(self, monkeypatch):
        called = []
        monkeypatch.setattr(auth, "_trellis_apply_token", lambda c, t: called.append(t))
        with pytest.raises(PermissionError, match="Sign in to read from Egeria"):
            auth.apply_token(SimpleNamespace(user_id="anonymous"), "")
        auth.apply_token(SimpleNamespace(user_id="erinoverview"), "tok")
        assert called == ["tok"]

    def test_egeria_context_stays_offline_for_anonymous(self, anonymous_mode, monkeypatch):
        import advisor.mcp_config
        from advisor.egeria_context import NOT_SIGNED_IN, EgeriaContext
        monkeypatch.setattr(advisor.mcp_config, "get_pyegeria_platform_config",
                            lambda path=None: {"view_server": "qs-view-server", "platform_url": "https://x"})
        ctx = EgeriaContext(egeria_credentials=auth.get_egeria_credentials(SimpleNamespace(headers={})))
        assert ctx._load_connection()["user_id"] == ""
        assert ctx._make_client(object) is None and ctx.unavailable["state"] == NOT_SIGNED_IN

    def test_dr_egeria_refuses_an_anonymous_run(self, anonymous_mode):
        from advisor.agents.dr_egeria_agent import DrEgeriaActionAgent, DrEgeriaAuthError
        agent = DrEgeriaActionAgent.__new__(DrEgeriaActionAgent)
        agent._egeria_conn = {"url": "https://x", "server_name": "s"}
        creds = auth.get_egeria_credentials(SimpleNamespace(headers={}))
        with pytest.raises(DrEgeriaAuthError):
            agent._get_egeria_conn(egeria_credentials=creds)

    def test_query_routes_treat_anonymous_as_not_signed_in(self):
        src = Path(__file__).resolve().parents[2].joinpath("advisor/web/query.py").read_text()
        assert src.count("egeria_authenticated = current_user is not None and not is_anonymous(current_user)") == 2
        assert "egeria_authenticated = current_user is not None\n" not in src


# ── 2. the service account is never offered, and never signs in ──────────────────

class TestServiceAccount:
    def test_eas_own_service_account_is_refused(self, monkeypatch):
        from advisor.config import settings
        monkeypatch.setattr(settings, "daemon_user_id", "peterprofile")
        assert "peterprofile" in auth.service_account_ids()
        assert auth.is_service_account("PeterProfile")
        with pytest.raises(HTTPException) as e:
            auth.create_access_token("peterprofile", "tok")
        assert e.value.status_code == 403 and e.value.detail == auth.SERVICE_ACCOUNT_DETAIL

    def test_password_login_refuses_it_before_egeria(self, monkeypatch):
        from advisor.config import settings
        from advisor.web.auth import LoginRequest, auth_login
        monkeypatch.setattr(settings, "daemon_user_id", "peterprofile")
        monkeypatch.setattr(auth, "_login_with_password", lambda *a: pytest.fail("Egeria was called"))
        with pytest.raises(HTTPException) as e:
            asyncio.run(auth_login(LoginRequest(username="peterprofile", password="x")))
        assert e.value.status_code == 403

    def test_defaults_never_name_it(self, monkeypatch):
        from advisor.config import settings
        from advisor.web.auth import auth_defaults
        monkeypatch.setattr(settings, "daemon_user_id", "peterprofile")
        monkeypatch.setattr(settings, "advisor_login_default_user", "")
        assert asyncio.run(auth_defaults()) == {"username": ""}
        monkeypatch.setattr(settings, "advisor_login_default_user", "erinoverview")
        assert asyncio.run(auth_defaults()) == {"username": "erinoverview"}
        monkeypatch.setattr(settings, "advisor_login_default_user", "peterprofile")   # misconfigured
        assert asyncio.run(auth_defaults()) == {"username": ""}


# ── 3. Portal SSO ────────────────────────────────────────────────────────────────

def _jwt(exp: int) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return b({"alg": "HS256"}) + "." + b({"sub": "erinoverview", "exp": exp}) + ".c2ln"


@pytest.fixture
def egeria(monkeypatch):
    """EgeriaTech whose get_my_profile does what `answer` says (return a profile, or raise)."""
    import pyegeria.egeria_tech_client as etc
    state = {"answer": {"profile": "erin"}}

    class Fake:
        def __init__(self, **kw):
            pass

        def get_my_profile(self):
            a = state["answer"]
            if isinstance(a, BaseException):
                raise a
            return a

    monkeypatch.setattr(etc, "EgeriaTech", Fake)
    monkeypatch.setattr(auth, "apply_token", lambda client, token: None)
    monkeypatch.setattr(auth, "_validation_config", lambda: SimpleNamespace(
        egeria_view_server="qs-view-server", egeria_platform_url="https://localhost:9443",
        egeria_service_account_user="svc"))
    return state


class TestPortalTokenCheck:
    @pytest.mark.parametrize("answer, verdict, words", [
        ({"profile": "erin"}, auth.PORTAL_TOKEN_ACCEPTED, ""),
        (_not_found(), auth.PORTAL_TOKEN_ACCEPTED, ""),        # authenticated; the person has no profile
        (_unauthorized(), auth.PORTAL_TOKEN_REJECTED, "Egeria rejected its token (HTTP 401)"),
        (_unreachable(), auth.PORTAL_TOKEN_UNREACHABLE, "PyegeriaConnectionException"),
        (RuntimeError("boom"), auth.PORTAL_TOKEN_REJECTED, "could not be verified (RuntimeError)"),
    ])
    def test_verdicts(self, egeria, answer, verdict, words):
        egeria["answer"] = answer
        got = auth.check_portal_egeria_token(_jwt(4102444800))
        assert got[0] == verdict and words in got[1]

    def test_missing_or_expired_is_rejected_without_a_call(self, egeria):
        egeria["answer"] = AssertionError("Egeria must not be called")
        assert auth.check_portal_egeria_token("")[0] == auth.PORTAL_TOKEN_REJECTED
        verdict, reason = auth.check_portal_egeria_token(_jwt(1))
        assert verdict == auth.PORTAL_TOKEN_REJECTED and reason == "its Egeria token has expired"

    def test_the_live_shapes_are_the_ones_classified(self):
        assert SSO["rejected_garbage"]["__error__"] == "PyegeriaUnauthorizedException"
        assert SSO["rejected_forged_jwt"]["related_http_code"] == 401
        assert SSO["unreachable"]["__error__"] == "PyegeriaConnectionException"
        assert SSO["valid_erinoverview"]["ok"] is True


# ── 3a. N1: Egeria failing is "unreachable", not "rejected" ──────────────────────
# Each exception is the one pyegeria's own _async_make_request raises: a real MyProfile client calls
# get_my_profile, and only its HTTP session is replaced. The bodies use the FFDC field names Egeria
# sends (as in laz's 401 capture and pyegeria's tests/micro-tests/test_issue_142_*); the exception class
# is Egeria's frameworks.openmetadata.ffdc.PropertyServerException and the message id is a real
# OMAG-REPOSITORY-HANDLER-500 id from Egeria's source.

EGERIA_500_WRAPPED = {
    "class": "PersonalProfileResponse", "relatedHTTPCode": 500,
    "exceptionClassName": "org.odpi.openmetadata.frameworks.openmetadata.ffdc.PropertyServerException",
    "exceptionErrorMessageId": "OMAG-REPOSITORY-HANDLER-500-001",
    "exceptionErrorMessage": "OMAG-REPOSITORY-HANDLER-500-001 An unexpected error occurred in the repository",
}
SPRING_503 = {"timestamp": "2026-11-11T12:00:00.000+00:00", "status": 503, "error": "Service Unavailable",
              "path": "/servers/qs-view-server/api/open-metadata/my-profile"}


def _raised_by_pyegeria(monkeypatch, answer):
    """Run pyegeria's real get_my_profile with the session answering `answer` (an httpx.Response
    builder, or an exception for the transport to raise) and return what pyegeria raised."""
    import httpx
    from pyegeria.omvs.my_profile import MyProfile
    monkeypatch.setattr(MyProfile, "check_connection", lambda self: "test")
    client = MyProfile("qs-view-server", "https://localhost:1", "erinoverview")

    def handler(request):
        if isinstance(answer, BaseException):
            raise answer
        return answer(request)

    client.session = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # pinned: used on every loop
    with pytest.raises(Exception) as caught:
        client.get_my_profile()
    return caught.value


class TestEgeriaFailingIsUnreachable:
    @pytest.mark.parametrize("answer, words", [
        (lambda req: __import__("httpx").Response(200, json=EGERIA_500_WRAPPED, request=req),
         "Egeria failed while checking it (HTTP 500)"),
        (lambda req: __import__("httpx").Response(500, text="Internal Server Error", request=req),
         "Egeria failed while checking it (HTTP 500)"),
        (lambda req: __import__("httpx").Response(503, json=SPRING_503, request=req),
         "Egeria failed while checking it (HTTP 503)"),
    ], ids=["wrapped-500", "http-500", "http-503"])
    def test_5xx(self, monkeypatch, egeria, answer, words):
        exc = _raised_by_pyegeria(monkeypatch, answer)
        egeria["answer"] = exc
        assert auth.check_portal_egeria_token(_jwt(4102444800)) == (auth.PORTAL_TOKEN_UNREACHABLE, words)

    @pytest.mark.parametrize("error", ["ReadError", "RemoteProtocolError"])
    def test_a_connection_dropped_mid_answer(self, monkeypatch, egeria, error):
        import httpx
        exc = _raised_by_pyegeria(monkeypatch, getattr(httpx, error)("connection reset by peer"))
        assert type(exc).__name__ == "PyegeriaClientException"  # not PyegeriaConnectionException
        egeria["answer"] = exc
        assert auth.check_portal_egeria_token(_jwt(4102444800)) == (
            auth.PORTAL_TOKEN_UNREACHABLE, f"the connection to Egeria failed ({error})")

    def test_the_pyegeria_shapes(self, monkeypatch):
        """Which attribute carries the code differs by path; pin both, so a pyegeria change shows here."""
        import httpx
        wrapped = _raised_by_pyegeria(monkeypatch, lambda r: httpx.Response(200, json=EGERIA_500_WRAPPED, request=r))
        assert type(wrapped).__name__ == "PyegeriaAPIException" and wrapped.related_http_code == 500
        bare = _raised_by_pyegeria(monkeypatch, lambda r: httpx.Response(500, text="x", request=r))
        assert type(bare).__name__ == "PyegeriaClientException" and bare.http_status_code == 500
        assert getattr(bare, "related_http_code", None) is None

    @pytest.mark.parametrize("status, body", [
        (200, {"class": "VoidResponse", "relatedHTTPCode": 401}),
        (403, "Forbidden"),
        (400, "Bad Request"),
    ], ids=["wrapped-401", "http-403", "http-400"])
    def test_a_4xx_still_refuses(self, monkeypatch, egeria, status, body):
        import httpx
        kw = {"json": body} if isinstance(body, dict) else {"text": body}
        egeria["answer"] = _raised_by_pyegeria(monkeypatch, lambda r: httpx.Response(status, request=r, **kw))
        assert auth.check_portal_egeria_token(_jwt(4102444800))[0] == auth.PORTAL_TOKEN_REJECTED

    def test_a_404_from_pyegeria_is_accepted(self, monkeypatch, egeria):
        import httpx
        egeria["answer"] = _raised_by_pyegeria(monkeypatch, lambda r: httpx.Response(
            200, json={"class": "PersonalProfileResponse", "relatedHTTPCode": 404}, request=r))
        assert auth.check_portal_egeria_token(_jwt(4102444800)) == (auth.PORTAL_TOKEN_ACCEPTED, "")


class TestPortalRoute:
    def _sign_in(self, monkeypatch, verdict):
        from advisor.web.auth import PortalTokenRequest, auth_portal
        monkeypatch.setattr(auth, "exchange_portal_token",
                            lambda t: {"sub": "erinoverview", "egeria_token": "tok", "role": "user"})
        monkeypatch.setattr(auth, "check_portal_egeria_token", lambda tok: verdict)
        monkeypatch.setattr(auth, "create_access_token", lambda **kw: "session-jwt")
        return asyncio.run(auth_portal(PortalTokenRequest(portal_token="p")))

    def test_a_rejected_token_is_refused(self, monkeypatch):
        with pytest.raises(HTTPException) as e:
            self._sign_in(monkeypatch, (auth.PORTAL_TOKEN_REJECTED, "Egeria rejected its token (HTTP 401)"))
        assert e.value.status_code == 401
        assert "Egeria did not accept the Portal's sign-in for 'erinoverview'" in e.value.detail

    def test_an_accepted_token_signs_in(self, monkeypatch):
        assert self._sign_in(monkeypatch, (auth.PORTAL_TOKEN_ACCEPTED, ""))["access_token"] == "session-jwt"

    def test_egeria_unreachable_still_signs_in(self, monkeypatch):
        assert self._sign_in(monkeypatch, (auth.PORTAL_TOKEN_UNREACHABLE, "x"))["access_token"] == "session-jwt"


# ── 4. SS-4: per-user plans, drafts and transcripts, with an admin view ───────────

@pytest.fixture
def as_user(monkeypatch):
    """Requests carry X-User (and X-Admin: 1 for an admin); patched into advisor.auth."""
    def current(request):
        uid = request.headers.get("x-user")
        return {"user_id": uid, "sub": uid, "role": "user", "egeria_token": "t"} if uid else None
    monkeypatch.setattr(auth, "get_current_user", current)
    monkeypatch.setattr(auth, "is_admin", lambda request: request.headers.get("x-admin") == "1")


@pytest.fixture
def plans(tmp_path, monkeypatch, as_user):
    import advisor.governance_docs as gd
    from advisor.web.plans import router
    paths = {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")}
    monkeypatch.setattr(gd, "_load_paths", lambda: paths)
    dm = gd.DocumentManager()
    monkeypatch.setattr(gd, "_doc_manager", dm)
    app = FastAPI()
    app.include_router(router)
    doc = dm.import_document("# Erin's plan\n\n## Command Sequence\n", user_id="erin")
    return TestClient(app), dm, doc


ERIN, PETER, ADMIN = {"x-user": "erin"}, {"x-user": "peter"}, {"x-user": "root", "x-admin": "1"}


class TestPlans:
    @pytest.mark.parametrize("method, path, body", [
        ("get", "/api/plans/{d}", None),
        ("get", "/api/plans/{d}/export", None),
        ("get", "/api/plans/{d}/report-export", None),
        ("put", "/api/plans/{d}", {"content": "# hijacked"}),
        ("post", "/api/plans/{d}/execute", {}),
        ("post", "/api/plans/{d}/validate", None),
        ("post", "/api/plans/{d}/retry", None),
        ("post", "/api/plans/{d}/rerun", None),
        ("post", "/api/plans/{d}/recover", None),
        ("get", "/api/plans/{d}/versions", None),
        ("post", "/api/plans/{d}/versions/v1.md/restore", None),
        ("post", "/api/plans/{d}/fork", {"title": "mine now"}),
        ("post", "/api/plans/{d}/save-as", {"title": "mine now"}),
        ("post", "/api/plans/{d}/save-as-template", {"name": "t"}),
        ("delete", "/api/plans/{d}", None),
        ("post", "/api/plans/{d}/restore-trash", None),
        ("delete", "/api/plans/{d}/purge", None),
    ])
    def test_another_users_plan_is_not_found_on_every_route(self, plans, method, path, body):
        client, dm, doc = plans
        before = dm.load(doc)
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(path.format(d=doc), headers=PETER, **kw)
        assert r.status_code == 404, (path, r.status_code, r.text)
        assert r.json()["detail"] == f"Plan {doc!r} not found"
        assert dm.load(doc) == before                                      # nothing was changed

    def test_the_owner_and_an_admin_can_read_it(self, plans):
        client, _, doc = plans
        assert client.get(f"/api/plans/{doc}", headers=ERIN).status_code == 200
        assert client.get(f"/api/plans/{doc}/export", headers=ERIN).status_code == 200
        assert client.get(f"/api/plans/{doc}", headers=ADMIN).status_code == 200

    def test_lists_are_per_user_with_an_admin_view(self, plans):
        client, _, doc = plans
        ids = lambda h: {p["doc_id"] for p in client.get("/api/plans", headers=h).json()["inbox"]}  # noqa: E731
        assert doc in ids(ERIN) and doc not in ids(PETER) and doc in ids(ADMIN)


class TestDrafts:
    def test_drafts_use_the_admin_view(self, as_user, monkeypatch):
        import advisor.governance_draft as gdr
        from advisor.web.drafts import router
        seen = []
        monkeypatch.setattr(gdr, "list_visible_drafts", lambda user_id=None, role=None: seen.append((user_id, role)) or [])
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        client.get("/api/drafts", headers=PETER)
        client.get("/api/drafts", headers=ADMIN)
        assert seen == [("peter", "user"), ("root", "admin")]


class TestSessions:
    @pytest.fixture
    def sessions(self, tmp_path, monkeypatch, as_user):
        import advisor.session_logger as sl
        from advisor.request_context import reset_current_user, set_current_user
        from advisor.web.sessions import router
        monkeypatch.setattr(sl, "_sessions_path", lambda: tmp_path)
        log = sl.SessionLogger()
        monkeypatch.setattr(sl, "_logger", log)
        tok = set_current_user("erin")
        log.log_turn("s-erin", role="user", content="my plan")
        reset_current_user(tok)
        (tmp_path / "s-legacy.jsonl").write_text(json.dumps({"ts": "t", "role": "user", "content": "old"}) + "\n")
        app = FastAPI()
        app.include_router(router)
        return TestClient(app), log

    def test_entries_record_their_owner(self, sessions):
        _, log = sessions
        assert log.load_session("s-erin")[0]["owner"] == "erin"

    def test_a_user_sees_only_their_own(self, sessions):
        client, _ = sessions
        ids = lambda h: {s["session_id"] for s in client.get("/api/sessions", headers=h).json()["sessions"]}  # noqa: E731
        assert ids(ERIN) == {"s-erin"}
        assert ids(PETER) == set()
        assert ids(ADMIN) == {"s-erin", "s-legacy"}         # unowned transcripts: admins only
        assert client.get("/api/sessions/s-erin", headers=PETER).status_code == 404
        assert client.get("/api/sessions/s-legacy", headers=ERIN).status_code == 404
        assert client.get("/api/sessions/s-erin", headers=ERIN).json()["entries"][0]["content"] == "my plan"
        assert client.get("/api/sessions/s-legacy", headers=ADMIN).status_code == 200

    def test_anonymous_sees_none(self, sessions):
        client, _ = sessions
        assert client.get("/api/sessions").json()["sessions"] == []
