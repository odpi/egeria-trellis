"""Tests for doc_source_probe.probe() — the read-only reachability check,
BRIEF-DATABASE-DOCUMENTATION-SOURCES.md slice 1.

Mocks `httpx.Client` rather than hitting the network: a fake client/response
pair implementing exactly the surface `probe()` uses (`.stream()` as a
context manager, `.status_code`, `.headers`, `.url`, `.iter_bytes()`), so
these tests pin the FOUR-STATE mapping without depending on any external
host's availability — the same reason the gate step separately does a live
check against real URLs (see DOC-SOURCES-DECLARE-AND-PROBE-IMPLEMENTED.md).
"""
from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from resource_explorer import doc_source_probe as m


class _FakeResponse:
    def __init__(self, status_code, url, headers=None, body=b""):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self._body = body

    def iter_bytes(self):
        # Chunked, like a real stream — a single giant chunk would defeat
        # the cap check in test_body_read_is_capped below, since probe()
        # only checks the running total between chunks.
        chunk_size = 4096
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


class _FakeClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def stream(self, method, url):
        if self._raise_exc:
            raise self._raise_exc
        yield self._response


def _patch_client(monkeypatch, response=None, raise_exc=None):
    monkeypatch.setattr(m.httpx, "Client",
                         lambda **kw: _FakeClient(response=response, raise_exc=raise_exc))


def test_reachable_2xx(monkeypatch):
    resp = _FakeResponse(200, "https://example.com/docs",
                          headers={"content-type": "text/html", "content-length": "11"},
                          body=b"<title>Docs</title>")
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/docs")

    assert result.state == m.REACHABLE
    assert result.status_code == 200
    assert result.title == "Docs"
    assert result.byte_count == 11


def test_needs_sign_in_on_401(monkeypatch):
    resp = _FakeResponse(401, "https://example.com/private")
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/private")

    assert result.state == m.NEEDS_SIGN_IN
    assert result.status_code == 401


def test_needs_sign_in_on_403(monkeypatch):
    resp = _FakeResponse(403, "https://example.com/private")
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/private")

    assert result.state == m.NEEDS_SIGN_IN


def test_needs_sign_in_on_login_redirect(monkeypatch):
    # A 200 that landed somewhere other than the requested URL, on a page
    # that looks like a login wall — the "redirected to a login page" case
    # a bare status-code check would miss.
    resp = _FakeResponse(200, "https://example.com/login?next=/private",
                          headers={"content-type": "text/html"})
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/private")

    assert result.state == m.NEEDS_SIGN_IN


# ── Sign-in false-positive fix (round 5, 2026-09-29) ─────────────────────────
# Live false positive: a real 200 page ("Pragmatic Data Research Ltd –
# Supp…", no redirect, no password form) was classified `needs_sign_in`.
# Design's rule, exact wording: only a 401/403, a redirect whose TARGET PATH
# looks like a login page, or a 200 body with an actual password input
# counts. A login LINK or KEYWORD present as ordinary page text must not.

def test_reachable_on_200_with_a_login_keyword_and_link_in_the_body(monkeypatch):
    # The exact false-positive shape: an ordinary page that happens to
    # mention/link to sign-in somewhere (e.g. a header "Log in" link) but is
    # not itself a login wall and did not redirect anywhere.
    body = (
        b"<title>Pragmatic Data Research Ltd \xe2\x80\x93 Support</title>"
        b"<body><a href=\"/login\">Log in</a> to manage your account. "
        b"See our sign-in help page for details.</body>"
    )
    resp = _FakeResponse(200, "https://example.com/support",
                          headers={"content-type": "text/html"}, body=body)
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/support")

    assert result.state == m.REACHABLE
    assert result.status_code == 200


def test_needs_sign_in_on_200_with_an_actual_password_form(monkeypatch):
    body = (
        b"<title>Sign in</title><form>"
        b"<input type=\"text\" name=\"user\">"
        b"<input type=\"password\" name=\"pw\"></form>"
    )
    resp = _FakeResponse(200, "https://example.com/account",
                          headers={"content-type": "text/html"}, body=body)
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/account")

    assert result.state == m.NEEDS_SIGN_IN
    assert result.status_code == 200


def test_reachable_on_redirect_whose_target_path_merely_contains_auth_as_a_substring(monkeypatch):
    # The removed "auth" marker used to match anywhere in the whole URL —
    # including an unrelated word/param like "authorize"/"oauth_callback".
    # Only an actual login-path marker (login/signin/sso/...) or a known IdP
    # host should trigger now.
    resp = _FakeResponse(200, "https://example.com/oauth_callback?status=authorized",
                          headers={"content-type": "text/html"})
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/docs")

    assert result.state == m.REACHABLE


def test_needs_sign_in_on_redirect_to_a_known_identity_provider_host(monkeypatch):
    resp = _FakeResponse(200, "https://accounts.google.com/o/oauth2/v2/auth?client_id=x",
                          headers={"content-type": "text/html"})
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/docs")

    assert result.state == m.NEEDS_SIGN_IN


def test_not_found_on_404(monkeypatch):
    resp = _FakeResponse(404, "https://example.com/gone")
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/gone")

    assert result.state == m.NOT_FOUND
    assert result.status_code == 404


def test_blocked_on_5xx(monkeypatch):
    resp = _FakeResponse(503, "https://example.com/down")
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/down")

    assert result.state == m.BLOCKED
    assert result.status_code == 503
    assert "503" in result.error


def test_timed_out_is_its_own_state_not_blocked(monkeypatch):
    _patch_client(monkeypatch, raise_exc=httpx.TimeoutException("timed out"))

    result = m.probe("https://example.com/slow")

    assert result.state == m.TIMED_OUT
    assert result.state != m.BLOCKED
    assert result.status_code is None
    assert "timed out" in result.error


def test_unreachable_on_connection_error(monkeypatch):
    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("connection refused"))

    result = m.probe("https://nowhere.invalid/")

    assert result.state == m.UNREACHABLE
    assert result.status_code is None


def test_never_raises_on_unexpected_error(monkeypatch):
    _patch_client(monkeypatch, raise_exc=RuntimeError("boom"))

    result = m.probe("https://example.com/")

    assert result.state == m.UNREACHABLE
    assert "boom" in result.error


def test_body_read_is_capped(monkeypatch):
    huge = b"x" * (m._MAX_BODY_BYTES * 2)
    resp = _FakeResponse(200, "https://example.com/big", body=huge)
    _patch_client(monkeypatch, response=resp)

    result = m.probe("https://example.com/big")

    # No content-length header here, so byte_count falls back to what was
    # actually read — which must have stopped at the cap.
    assert result.byte_count <= m._MAX_BODY_BYTES
