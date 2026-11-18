"""`trellis_auth.admin`: one fail-closed admin gate for every Trellis app (owner's decision, 2026-11-10).

Same semantics as RE's `resource_explorer/web/admin_auth.require_admin` (minus RE's local-dev open flag), so RE can
move onto it mechanically in Phase 1.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from trellis_auth import AdminConfig, AdminPathMiddleware, is_admin_request, require_admin, resolve_admin
from trellis_auth import admin as admin_mod

USER = {"sub": "garygeeke"}
OTHER = {"sub": "peterprofile"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("TRELLIS_ADMIN_USERS", "TRELLIS_ADMIN_TOKEN", "APP_ADMIN_USERS", "APP_ADMIN_TOKEN"):
        monkeypatch.delenv(v, raising=False)


def _req(headers=None):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def _status(fn):
    try:
        fn()
        return 200, ""
    except HTTPException as exc:
        return exc.status_code, exc.detail


class TestResolve:
    def test_nothing_set_is_not_configured(self):
        assert resolve_admin("APP").configured is False

    def test_shared_then_app_specific_wins(self, monkeypatch):
        monkeypatch.setenv("TRELLIS_ADMIN_USERS", "a, b")
        assert resolve_admin("APP").users == {"a", "b"}
        monkeypatch.setenv("APP_ADMIN_USERS", "c")
        assert resolve_admin("APP").users == {"c"}

    def test_an_empty_app_value_does_not_mask_the_shared_one(self, monkeypatch):
        monkeypatch.setenv("TRELLIS_ADMIN_TOKEN", "t")
        monkeypatch.setenv("APP_ADMIN_TOKEN", "  ")
        assert resolve_admin("APP").token == "t"


class TestRequireAdmin:
    def test_anonymous_is_refused_even_with_the_token(self):
        admin = AdminConfig(token="t")
        assert _status(lambda: require_admin(_req({"X-Admin-Token": "t"}), user=None, admin=admin)) == \
            (403, "Sign in to do this")

    def test_an_anonymous_stand_in_is_anonymous(self):
        admin = AdminConfig(users=frozenset({"anonymous"}))
        assert _status(lambda: require_admin(_req(), user={"sub": "anonymous", "anonymous": True}, admin=admin))[0] == 403

    def test_a_listed_user_passes(self):
        assert _status(lambda: require_admin(_req(), user=USER, admin=AdminConfig(users=frozenset({"garygeeke"}))))[0] == 200

    def test_an_unlisted_user_is_refused(self):
        assert _status(lambda: require_admin(_req(), user=OTHER, admin=AdminConfig(users=frozenset({"garygeeke"})))) == \
            (403, "Admin credential required")

    def test_the_token_passes_a_signed_in_user(self):
        assert _status(lambda: require_admin(_req({"X-Admin-Token": "t"}), user=OTHER, admin=AdminConfig(token="t")))[0] == 200

    def test_a_wrong_token_is_refused(self):
        assert _status(lambda: require_admin(_req({"X-Admin-Token": "u"}), user=OTHER, admin=AdminConfig(token="t")))[0] == 403

    def test_nothing_configured_refuses_everyone(self):
        code, detail = _status(lambda: require_admin(_req(), user=USER, app_prefix="APP"))
        assert code == 403 and detail.startswith("no admin configured") and "APP_ADMIN_USERS" in detail

    def test_the_admin_user_header_is_never_read(self):
        admin = AdminConfig(users=frozenset({"garygeeke"}))
        assert not is_admin_request(_req({"X-Admin-User": "garygeeke"}), admin, OTHER)

    def test_the_token_is_compared_with_compare_digest(self, monkeypatch):
        calls = []
        real = admin_mod.hmac.compare_digest
        monkeypatch.setattr(admin_mod.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
        assert is_admin_request(_req({"X-Admin-Token": "t"}), AdminConfig(token="t"), OTHER)
        assert calls == [(b"t", b"t")]

    def test_reads_the_environment_each_call(self, monkeypatch):
        assert _status(lambda: require_admin(_req(), user=USER, app_prefix="APP"))[0] == 403
        monkeypatch.setenv("APP_ADMIN_USERS", "garygeeke")
        assert _status(lambda: require_admin(_req(), user=USER, app_prefix="APP"))[0] == 200


class TestMiddleware:
    def _client(self, user):
        async def ok(request):
            return PlainTextResponse("ok")
        app = Starlette(routes=[Route("/api/admin/x", ok, methods=["GET", "POST"]), Route("/api/fb", ok),
                                Route("/api/fb/3", ok, methods=["PATCH"]), Route("/api/fbx", ok),
                                Route("/open", ok)])
        app.add_middleware(AdminPathMiddleware, app_prefix="APP", current_user=lambda r: user,
                           prefixes=("/api/admin/",), exact=("/api/fb",))
        return TestClient(app)

    def test_gated_paths_refuse_a_non_admin(self, monkeypatch):
        monkeypatch.setenv("APP_ADMIN_USERS", "garygeeke")
        c = self._client(OTHER)
        assert c.get("/api/admin/x").status_code == 403
        assert c.post("/api/admin/x").status_code == 403
        assert c.get("/api/admin/not-a-route-yet").status_code == 403      # fail closed by prefix
        assert c.get("/api/fb").status_code == 403
        assert c.patch("/api/fb/3").status_code == 403                       # an exact path's sub-resource
        assert c.get("/api/admin/x").json()["error"] == "admin_required"

    def test_other_paths_pass_untouched(self, monkeypatch):
        monkeypatch.setenv("APP_ADMIN_USERS", "garygeeke")
        c = self._client(OTHER)
        assert c.get("/open").status_code == 200
        assert c.get("/api/fbx").status_code == 200                          # exact means exact, not a prefix

    def test_an_admin_gets_through(self, monkeypatch):
        monkeypatch.setenv("APP_ADMIN_USERS", "garygeeke")
        assert self._client(USER).get("/api/admin/x").status_code == 200

    def test_an_identity_that_cannot_be_read_is_refused(self, monkeypatch):
        monkeypatch.setenv("APP_ADMIN_USERS", "garygeeke")

        def boom(request):
            raise RuntimeError("bad token")
        async def ok(request):
            return PlainTextResponse("ok")
        app = Starlette(routes=[Route("/api/admin/x", ok)])
        app.add_middleware(AdminPathMiddleware, app_prefix="APP", current_user=boom, prefixes=("/api/admin/",))
        assert TestClient(app).get("/api/admin/x").status_code == 403
