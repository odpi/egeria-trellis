"""Login refuses a user whose Egeria token Egeria itself will not honour.

2026-10-16: the owner signed in as 'rover'. Egeria issued a bearer token (it mints one for any
user id) and the login returned 200, but every request made with that token then failed with
AUTHORIZATION_ERROR_401 "User not authorized received for user - `rover`". After the token is
obtained, login makes ONE cheap authorized read as the caller; a refusal ends the login (no
session), an unreachable Egeria follows the old behaviour (login accepted, the gap logged).
"""
from __future__ import annotations

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from tests.test_login_and_identity import _app_under, _auth_env, _no_caller  # noqa: F401
from tests.test_login_and_identity import _restore_shared_app  # noqa: F401

# The designer's words (REPLY-DESIGNER-RUNS-REGISTRATION.md, Q12), verbatim.
REFUSED_WORDS = ("Egeria accepted rover's password but refuses its requests. "
                 "Sign in as a user this Egeria server knows.")


def _token(sub: str) -> str:
    return jwt.encode({"iss": "self", "sub": sub, "exp": 9999999999}, "irrelevant", algorithm="HS256")


@pytest.fixture()
def client(monkeypatch):
    return TestClient(_app_under(monkeypatch, EXPLORER_REQUIRE_LOGIN="true"))


# ── the classifier ──────────────────────────────────────────────────────────

class _Boom(Exception):
    pass


@pytest.mark.parametrize("exc,expected", [
    (_Boom("AUTHORIZATION_ERROR_401 User not authorized received for user - `rover`"), "refused"),
    (_Boom("OPEN-METADATA-SECURITY-403-001 nope"), "refused"),
    (type("PyegeriaUnauthorizedException", (Exception,), {})("401"), "refused"),
    (type("PyegeriaConnectionException", (Exception,), {})("connect failed"), "unreachable"),
    (httpx.ConnectError("refused"), "unreachable"),
    (httpx.ReadTimeout("slow"), "unreachable"),
    (_Boom("something else broke"), "unreachable"),
])
def test_classify_read_failure(exc, expected):
    from resource_explorer.egeria_clients import classify_token_check_failure

    assert classify_token_check_failure(exc) == expected


# ── the route, with the check itself faked ──────────────────────────────────

def _login(client):
    return client.post("/api/auth/login", json={"username": "rover", "password": "pw"})


def _stub_token(monkeypatch):
    import resource_explorer.auth as re_auth

    monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: _token(u))


def test_refused_token_refuses_the_login_and_keeps_no_session(client, monkeypatch):
    _stub_token(monkeypatch)
    import resource_explorer.egeria_clients as ec

    monkeypatch.setattr(ec, "check_token_accepted", lambda user, token: "refused")
    r = _login(client)
    assert r.status_code == 401
    assert r.json()["detail"] == REFUSED_WORDS
    assert "access_token" not in r.json()


def test_unreachable_egeria_follows_old_behaviour_and_says_so(client, monkeypatch, caplog):
    _stub_token(monkeypatch)
    import resource_explorer.egeria_clients as ec

    monkeypatch.setattr(ec, "check_token_accepted", lambda user, token: "unreachable")
    with caplog.at_level("WARNING"):
        r = _login(client)
    assert r.status_code == 200
    assert "could not check with Egeria" in caplog.text
    assert r.json().get("check") == "could not check with Egeria"


# ── end to end: only the Egeria HTTP boundary is faked ──────────────────────

def _fake_egeria(monkeypatch, read):
    """Replace pyegeria's httpx AsyncClient with one on a MockTransport.

    `read(request)` answers the authorized read; POST .../api/token answers the token."""
    import pyegeria.core._base_server_client as bsc
    from resource_explorer import egeria_clients

    # The suite's no_real_egeria guard refuses real pyegeria classes. Here every byte goes to a
    # MockTransport, so the real class is exactly what the test is about: undo the guard.
    monkeypatch.setattr(egeria_clients, "_build", lambda cls, view_server, platform_url, user_id,
                        password: cls(view_server, platform_url, user_id, password))

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/token"):
            return httpx.Response(200, text=_token("rover"))
        if request.method == "GET":                  # the client's own /api/about probe
            return httpx.Response(200, json={})
        return read(request)

    real = httpx.AsyncClient

    class _Faked(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(bsc, "AsyncClient", _Faked)
    # pyegeria 6.2.13 posts the token request from _base_platform_client._post_for_bearer_token, with that
    # module's own AsyncClient: without this the token POST bypasses the MockTransport and reaches a real platform.
    import pyegeria.core._base_platform_client as bpc
    monkeypatch.setattr(bpc, "AsyncClient", _Faked)
    return seen


def test_e2e_token_ok_read_401_login_refused(client, monkeypatch):
    def read(request):
        return httpx.Response(401, json={
            "class": "VoidResponse", "relatedHTTPCode": 401,
            "exceptionErrorMessage": "AUTHORIZATION_ERROR_401 User not authorized received for user - `rover`"})

    seen = _fake_egeria(monkeypatch, read)
    r = _login(client)
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == REFUSED_WORDS
    assert any(not q.url.path.endswith("/api/token") for q in seen), "the read was made"
    # the read carried the token Egeria had just issued
    reads = [q for q in seen if q.method == "POST" and not q.url.path.endswith("/api/token")]
    assert reads[0].headers["authorization"] == f"Bearer {_token('rover')}"


def test_e2e_token_ok_read_ok_login_accepted(client, monkeypatch):
    def read(request):
        return httpx.Response(200, json={"class": "ElementsResponse", "relatedHTTPCode": 200,
                                         "elements": []})

    _fake_egeria(monkeypatch, read)
    r = _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["egeria_user"] == "rover"
    claims = jwt.decode(body["access_token"], "test-re-secret", algorithms=["HS256"])
    assert claims["egeria_token"] == _token("rover")


def test_e2e_read_cannot_connect_is_not_a_refusal(client, monkeypatch):
    def read(request):
        raise httpx.ConnectError("connection refused")

    _fake_egeria(monkeypatch, read)
    r = _login(client)
    assert r.status_code == 200, r.text
    assert r.json().get("check") == "could not check with Egeria"
