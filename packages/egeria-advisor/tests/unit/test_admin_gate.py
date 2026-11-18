"""EA's admin paths are behind the one shared, fail-closed admin gate (Phase 0 S1, 2026-11-10).

BRIEFS-EA-PHASE0-SAFETY §S1; the owner's decision #3: "one admin definition for RE and EA". Before this, every
`/api/admin/*` route (force re-index = `DROP TABLE ... CASCADE`, `git pull`, maintenance) and the feedback triage
reads and edits were open to any signed-in user.

Exercised through a real `TestClient`, as `test_login_required_middleware.py` does: what matters is the status a
caller sees. Nothing real runs: the re-index job thread, the feedback store and the status reads are faked, and
every data path points at `tmp_path`.
"""
from __future__ import annotations

import hmac
import json

import pytest
from fastapi.testclient import TestClient

from advisor import auth

ADMIN, OTHER, TOKEN = "garygeeke", "peterprofile", "s3cret-admin-token"
GATED = [
    ("get", "/api/admin/status"),
    ("post", "/api/admin/collections/{c}/reindex?force=true&confirm={c}"),
    ("post", "/api/admin/collections/{c}/reindex"),
    ("get", "/api/admin/jobs"),
    ("get", "/api/feedback/analysis"),
]
#: A-F1: triage moved to the trellis-feedback router, which checks admin itself (the same
#: `trellis_auth.require_admin` definition, through `advisor.auth.is_admin`).
LIBRARY_ADMIN = [
    ("get", "/api/feedback/v2/items"),
    ("get", "/api/feedback/v2/causes"),
    ("post", "/api/feedback/v2/triage"),
]


@pytest.fixture(scope="module")
def client():
    from advisor.web.app import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def collection():
    from advisor.collection_config import ALL_COLLECTIONS
    return next(iter(ALL_COLLECTIONS))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN", "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ",
                "TRELLIS_ADMIN_USERS", "TRELLIS_ADMIN_TOKEN", "ADVISOR_ADMIN_USERS", "ADVISOR_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path))
    auth.reset_policy_cache()
    yield
    auth.reset_policy_cache()


@pytest.fixture(autouse=True)
def _nothing_real_runs(monkeypatch, tmp_path):
    """No job thread, no Postgres, no git, no feedback file outside tmp_path."""
    from advisor.web import admin as admin_routes

    started = []

    class _NoThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            started.append(args)

        def start(self):
            pass

    monkeypatch.setattr(admin_routes.threading, "Thread", _NoThread)
    monkeypatch.setattr(admin_routes, "_vector_counts", lambda: {})
    monkeypatch.setattr(admin_routes, "_last_indexed", lambda: {})
    monkeypatch.setattr(admin_routes, "_repo_status", lambda path: {"exists": False})
    monkeypatch.setattr(admin_routes, "_system_health", lambda: {}, raising=False)
    monkeypatch.setattr(admin_routes, "_dre_templates_status", lambda: None, raising=False)

    class _Collector:
        # S4's collector interface (feedback hotfix): the vote route calls record_vote and reads its
        # query_metrics outcome; record_feedback is the CLI's bool wrapper.
        def record_vote(self, **kw):
            return {"query_metrics": "skipped"}

        def record_feedback(self, **kw):
            return True

        def get_feedback_stats(self):
            return {}

        def get_gap_analysis(self):
            return {}

        def get_routing_improvements(self):
            return {}

        def analysis(self):
            return {"stats": {}, "gaps": {}, "improvements": [], "count": 0, "unreadable_lines": []}

    import advisor.feedback_collector as fc
    monkeypatch.setattr(fc, "get_feedback_collector", lambda: _Collector())
    return started


def _bearer(user: str) -> dict:
    return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token='opaque-egeria-token')}"}


def _call(client, method, path, collection, headers=None):
    path = path.format(c=collection)
    kw = {"headers": headers or {}}
    if method == "patch":
        kw["json"] = {"triage_status": "known_issue"}
    return getattr(client, method)(path, **kw)


# ── who gets through ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", GATED)
def test_anonymous_is_stopped_by_login_first(client, collection, method, path):
    assert _call(client, method, path, collection).status_code == 401


@pytest.mark.parametrize("method,path", GATED)
def test_a_signed_in_non_admin_gets_403(client, collection, monkeypatch, method, path):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    r = _call(client, method, path, collection, _bearer(OTHER))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Admin credential required"


@pytest.mark.parametrize("method,path", GATED)
def test_a_configured_admin_user_gets_through(client, collection, monkeypatch, method, path):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", f"someone, {ADMIN}")
    r = _call(client, method, path, collection, _bearer(ADMIN))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("method,path", GATED)
def test_the_admin_token_gets_a_signed_in_user_through(client, collection, monkeypatch, method, path):
    monkeypatch.setenv("ADVISOR_ADMIN_TOKEN", TOKEN)
    r = _call(client, method, path, collection, {**_bearer(OTHER), "X-Admin-Token": TOKEN})
    assert r.status_code == 200, r.text


def test_a_wrong_token_is_403(client, collection, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_TOKEN", TOKEN)
    r = client.get("/api/admin/status", headers={**_bearer(OTHER), "X-Admin-Token": TOKEN + "x"})
    assert r.status_code == 403


def test_the_shared_trellis_setting_works_and_the_app_one_wins(client, monkeypatch):
    monkeypatch.setenv("TRELLIS_ADMIN_USERS", OTHER)
    assert client.get("/api/admin/status", headers=_bearer(OTHER)).status_code == 200
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert client.get("/api/admin/status", headers=_bearer(OTHER)).status_code == 403


@pytest.mark.parametrize("method,path", GATED)
def test_nothing_configured_is_403_for_everyone(client, collection, method, path):
    r = _call(client, method, path, collection, _bearer(ADMIN))
    assert r.status_code == 403
    assert r.json()["detail"].startswith("no admin configured")


def test_a_client_sent_admin_user_header_is_ignored(client, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert client.get("/api/admin/status", headers={**_bearer(OTHER), "X-Admin-User": ADMIN}).status_code == 403


def test_a_route_added_under_the_prefix_is_gated_by_default(client, monkeypatch):
    """Fail closed by prefix: a path no route serves yet still answers 403, not 404, to a non-admin."""
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert client.post("/api/admin/some-future-action", headers=_bearer(OTHER)).status_code == 403


def test_anonymous_mode_is_not_an_admin(monkeypatch):
    """EA's anonymous stand-in user (login off, a dev override) is never a signed-in person, even if "anonymous"
    is listed. Checked on the gate itself: the login middleware's policy is fixed when the app is built."""
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from trellis_auth import require_admin

    monkeypatch.setenv("ADVISOR_ADMIN_USERS", "anonymous")
    stand_in = {"sub": "anonymous", "user_id": "anonymous", "anonymous": True}
    req = Request({"type": "http", "method": "GET", "path": "/api/admin/status", "query_string": b"", "headers": []})
    with pytest.raises(HTTPException) as exc:
        require_admin(req, app_prefix=auth.ADMIN_ENV_PREFIX, user=stand_in)
    assert exc.value.status_code == 403 and exc.value.detail == "Sign in to do this"


# ── the trellis-feedback router (A-F1) ──────────────────────────────────────────────────────────────────

def _library_call(client, method, path, headers):
    kw = {"headers": headers}
    if method == "post":
        kw["json"] = {"ref_id": "no-such-item", "status": "triaged"}
    return getattr(client, method)(path, **kw)


@pytest.mark.parametrize("method,path", LIBRARY_ADMIN)
def test_library_triage_routes_need_login_then_admin(client, monkeypatch, method, path):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert _library_call(client, method, path, {}).status_code == 401
    r = _library_call(client, method, path, _bearer(OTHER))
    assert r.status_code == 403 and r.json()["detail"] == "Admin only", r.text
    r = _library_call(client, method, path, _bearer(ADMIN))
    # through: 200, or 404 for triage of an item that does not exist -- never 401/403
    assert r.status_code in (200, 404), r.text


def test_retired_line_index_routes_are_gone(client, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert client.get("/api/feedback/extended", headers=_bearer(ADMIN)).status_code in (404, 405)
    assert client.patch("/api/feedback/extended/0", headers=_bearer(ADMIN),
                        json={"triage_status": "known_issue"}).status_code in (404, 405)


# ── what stays open ──────────────────────────────────────────────────────────────────────────────────────

def test_voting_stays_open_to_every_signed_in_user(client, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    r = client.post("/api/feedback", headers=_bearer(OTHER),
                    json={"query": "q", "vote": 1, "query_type": "general", "response_text": "a"})
    assert r.status_code == 200, r.text


def test_the_admin_page_itself_is_not_gated(client, monkeypatch):
    """The static page; every call it makes is gated."""
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert client.get("/admin", headers=_bearer(OTHER)).status_code != 403


# ── force re-index needs the typed name ──────────────────────────────────────────────────────────────────

def test_force_without_confirm_is_refused_and_starts_nothing(client, collection, monkeypatch, _nothing_real_runs):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    r = client.post(f"/api/admin/collections/{collection}/reindex?force=true", headers=_bearer(ADMIN))
    assert r.status_code == 400 and f"confirm={collection}" in r.json()["detail"]
    r = client.post(f"/api/admin/collections/{collection}/reindex?force=true&confirm=other",
                    headers=_bearer(ADMIN))
    assert r.status_code == 400
    assert _nothing_real_runs == []


def test_force_with_confirm_starts_the_force_job(client, collection, monkeypatch, _nothing_real_runs):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    r = client.post(f"/api/admin/collections/{collection}/reindex?force=true&confirm={collection}",
                    headers=_bearer(ADMIN))
    assert r.status_code == 200 and r.json()["description"] == f"Force re-index: {collection}"
    job, cmd = _nothing_real_runs[-1]
    assert "--force" in cmd


def test_an_incremental_reindex_needs_no_confirm(client, collection, monkeypatch, _nothing_real_runs):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    r = client.post(f"/api/admin/collections/{collection}/reindex", headers=_bearer(ADMIN))
    assert r.status_code == 200
    assert "--force" not in _nothing_real_runs[-1][1]


# ── the helper and the page ──────────────────────────────────────────────────────────────────────────────

def test_is_admin_is_the_same_gate(monkeypatch):
    from starlette.requests import Request

    def request(headers):
        return Request({"type": "http", "method": "GET", "path": "/api/auth/me", "query_string": b"",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    assert auth.is_admin(request(_bearer(ADMIN))) is True
    assert auth.is_admin(request(_bearer(OTHER))) is False
    assert auth.is_admin(request({})) is False


def test_auth_me_reports_is_admin(client, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    me = client.get("/api/auth/me", headers=_bearer(ADMIN)).json()
    assert me["authenticated"] is True and me["is_admin"] is True and me["user_id"] == ADMIN
    assert client.get("/api/auth/me", headers=_bearer(OTHER)).json()["is_admin"] is False
    monkeypatch.setenv("ADVISOR_ADMIN_TOKEN", TOKEN)
    assert client.get("/api/auth/me", headers={**_bearer(OTHER), "X-Admin-Token": TOKEN}).json()["is_admin"] is True


def test_the_token_is_compared_in_constant_time(monkeypatch, client):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr("trellis_auth.admin.hmac.compare_digest", lambda a, b: calls.append(1) or real(a, b))
    monkeypatch.setenv("ADVISOR_ADMIN_TOKEN", TOKEN)
    assert client.get("/api/admin/status", headers={**_bearer(OTHER), "X-Admin-Token": TOKEN}).status_code == 200
    assert calls


def test_admin_html_escapes_and_sends_the_token():
    from advisor.web.shared import _STATIC

    html = (_STATIC / "admin.html").read_text()
    assert "function _esc(" in html and "'ea_admin_token'" in html and "'X-Admin-Token'" in html
    assert "Admin only: sign in as an admin, or set the admin token" in html
    assert "&confirm=" in html
    # The old unescaped interpolations are gone.
    for raw in ("${c.name}</td>", "onclick=\"reindex('${c.name}'", "${r.last_commit_msg || '—'}", "${g.query}</div>",
                "${job.description}</span>", "${s.user || '—'}", "Error loading transcript: ${err.message}"):
        assert raw not in html, raw
