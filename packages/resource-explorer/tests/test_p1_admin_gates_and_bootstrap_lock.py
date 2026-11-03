"""Parity P1 round 2: admin-only Egeria-writing routes, and one bootstrap heal at a time."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

ADMIN = "resource_explorer.web.admin_auth.is_admin_request"
CONFIGURED = "resource_explorer.web.admin_auth.admin_configured"
USER = "resource_explorer.auth.get_current_user"


@pytest.fixture()
def client():
    return TestClient(app)


def _admin(monkeypatch, value, configured=True, signed_in=True):
    """Set the caller: `value` is whether they present a valid admin credential; `configured` whether any admin is
    configured at all (FEEDBACK_ADMIN_TOKEN / FEEDBACK_ADMIN_USERS); `signed_in` whether the request is signed in."""
    monkeypatch.setattr(ADMIN, lambda *a, **k: value)
    monkeypatch.setattr(CONFIGURED, lambda *a, **k: configured)
    monkeypatch.setattr(USER, lambda *a, **k: {"sub": "dan"} if signed_in else None)


class TestBootstrapRun:
    def test_non_admin_is_refused_and_nothing_runs(self, client, monkeypatch):
        _admin(monkeypatch, False)
        with patch("resource_explorer.bootstrap.check_and_heal") as heal:
            res = client.post("/api/bootstrap/run", json={"force": False})
        assert res.status_code == 403
        heal.assert_not_called()

    def test_refused_with_409_while_a_heal_is_reinitializing(self, client, monkeypatch):
        _admin(monkeypatch, True)
        monkeypatch.setattr("resource_explorer.bootstrap._reinitializing", True)
        with patch("resource_explorer.bootstrap.check_and_heal") as heal:
            res = client.post("/api/bootstrap/run", json={})
        assert res.status_code == 409 and "already running" in res.json()["detail"]
        heal.assert_not_called()

    def test_refused_with_409_while_any_pass_holds_the_lock(self, client, monkeypatch):
        _admin(monkeypatch, True)
        from resource_explorer import bootstrap as route
        monkeypatch.setattr(route, "discover_batches", lambda d: pytest.fail("a refused run must not look at batches"))
        assert route._heal_lock.acquire(blocking=False)
        try:
            res = client.post("/api/bootstrap/run", json={})
            assert res.status_code == 409
        finally:
            route._heal_lock.release()

    def test_the_lock_is_released_after_a_run_and_after_a_failure(self, client, monkeypatch):
        _admin(monkeypatch, True)
        from resource_explorer import bootstrap as route
        with patch("resource_explorer.bootstrap.check_and_heal", return_value={"batches": {}}):
            assert client.post("/api/bootstrap/run", json={}).status_code == 200
        with patch("resource_explorer.bootstrap.check_and_heal", side_effect=RuntimeError("boom")):
            assert client.post("/api/bootstrap/run", json={}).status_code == 500
        assert route._heal_lock.acquire(blocking=False), "released"
        route._heal_lock.release()

    def test_two_concurrent_runs_heal_once(self, client, monkeypatch):
        _admin(monkeypatch, True)
        from resource_explorer import bootstrap as b
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow(docs_dir):
            calls.append(1); started.set(); release.wait(5)
            return []

        monkeypatch.setattr(b, "discover_batches", slow)
        results = []
        t = threading.Thread(target=lambda: results.append(client.post("/api/bootstrap/run", json={}).status_code))
        t.start()
        assert started.wait(5)
        second = client.post("/api/bootstrap/run", json={}).status_code
        release.set(); t.join(5)
        assert second == 409 and results == [200] and len(calls) == 1


class TestBulkResolveAdminOnly:
    def test_non_admin_bulk_resolve_is_refused_in_dry_run_too(self, client, monkeypatch):
        _admin(monkeypatch, False)
        with patch("resource_explorer.bulk_ops.resolve_all") as ra:
            res = client.post("/api/egeria/linkage/resolve-all",
                              json={"targets": [{"entity_type": "repo", "slug": "x"}], "action": "republish"})
        assert res.status_code == 403
        ra.assert_not_called()

    def test_admin_reaches_it(self, client, monkeypatch):
        _admin(monkeypatch, True)
        res = client.post("/api/egeria/linkage/resolve-all",
                          json={"targets": [], "action": "republish", "dry_run": True})
        assert res.status_code == 200

    def test_the_single_resource_resolve_stays_open_to_a_non_admin(self, client, monkeypatch):
        _admin(monkeypatch, False)
        res = client.post("/api/egeria/linkage/repo/no-such-slug/resolve", json={"action": "discard"})
        assert res.status_code == 404, "reaches its own 'no stale linkage' answer, not a 403"


class TestAdminStatus:
    @pytest.mark.parametrize("value", [True, False])
    def test_reports_the_request_credential(self, client, monkeypatch, value):
        _admin(monkeypatch, value)
        assert client.get("/api/egeria/admin-status").json()["admin"] is value


class TestOwnerRulingAdminOnlyWhenConfigured:
    """Owner ruling 2026-10-14 (replacing 2026-10-09's): unconfigured fails closed, unless the local-dev flag
    RE_ADMIN_OPEN_WHEN_UNCONFIGURED is set; anonymous never. Real-JWT coverage: tests/test_admin_auth_strict.py."""

    def test_unconfigured_refuses_a_signed_in_non_admin_without_the_dev_flag(self, client, monkeypatch):
        _admin(monkeypatch, False, configured=False, signed_in=True)
        from resource_explorer.config import get_config
        monkeypatch.setattr(get_config().feedback, "admin_open_when_unconfigured", False)
        res = client.post("/api/egeria/linkage/resolve-all", json={"targets": [], "action": "republish", "dry_run": True})
        assert res.status_code == 403 and "no admin configured" in res.json()["detail"]
        with patch("resource_explorer.bootstrap.check_and_heal") as heal:
            assert client.post("/api/bootstrap/run", json={}).status_code == 403
        heal.assert_not_called()

    def test_unconfigured_with_the_dev_flag_lets_a_signed_in_non_admin_retry_and_resolve_all(self, client, monkeypatch):
        _admin(monkeypatch, False, configured=False, signed_in=True)
        from resource_explorer.config import get_config
        monkeypatch.setattr(get_config().feedback, "admin_open_when_unconfigured", True)
        res = client.post("/api/egeria/linkage/resolve-all", json={"targets": [], "action": "republish", "dry_run": True})
        assert res.status_code == 200
        with patch("resource_explorer.bootstrap.check_and_heal", return_value={"batches": {}}):
            assert client.post("/api/bootstrap/run", json={}).status_code == 200

    def test_unconfigured_still_refuses_an_anonymous_caller(self, client, monkeypatch):
        _admin(monkeypatch, False, configured=False, signed_in=False)
        with patch("resource_explorer.bootstrap.check_and_heal") as heal:
            assert client.post("/api/bootstrap/run", json={}).status_code == 403
        heal.assert_not_called()
        assert client.post("/api/egeria/linkage/resolve-all", json={"targets": [], "action": "discard"}).status_code == 403

    def test_configured_refuses_a_signed_in_non_admin(self, client, monkeypatch):
        _admin(monkeypatch, False, configured=True, signed_in=True)
        assert client.post("/api/egeria/linkage/resolve-all", json={"targets": [], "action": "discard"}).status_code == 403

    def test_configured_allows_the_admin(self, client, monkeypatch):
        _admin(monkeypatch, True, configured=True, signed_in=True)
        assert client.post("/api/egeria/linkage/resolve-all", json={"targets": [], "action": "discard", "dry_run": True}).status_code == 200

    def test_admin_configured_reads_both_settings(self, monkeypatch):
        from types import SimpleNamespace
        from resource_explorer.web import admin_auth
        for tok, users, want in (("", [], False), ("t", [], True), ("", ["dan"], True)):
            cfg = SimpleNamespace(feedback=SimpleNamespace(admin_token=tok, admin_users=users))
            monkeypatch.setattr("resource_explorer.config.get_config", lambda cfg=cfg: cfg)
            assert admin_auth.admin_configured() is want

    def test_is_admin_request_is_unchanged_and_stays_fail_closed(self):
        from types import SimpleNamespace
        from resource_explorer.web.admin_auth import is_admin_request
        req = SimpleNamespace(headers={})
        assert is_admin_request(req, SimpleNamespace(admin_token="", admin_users=[])) is False

    def test_admin_status_reports_configured_and_admin(self, client, monkeypatch):
        from resource_explorer.config import get_config
        monkeypatch.setattr(get_config().feedback, "admin_open_when_unconfigured", False)
        _admin(monkeypatch, False, configured=False)
        assert client.get("/api/egeria/admin-status").json() == {"admin": False, "configured": False, "open": False}
        _admin(monkeypatch, True, configured=True)
        assert client.get("/api/egeria/admin-status").json() == {"admin": True, "configured": True, "open": False}


class TestCheckAndHealTakesTheLockItself:
    """The automatic loop calls check_and_heal directly; the lock must cover it, not only the manual route."""

    def test_a_second_pass_returns_running_without_touching_anything_and_keeps_the_flag(self, monkeypatch, tmp_path):
        from resource_explorer import bootstrap as b
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow_discover(docs_dir):
            calls.append(1); started.set(); release.wait(5)
            return []

        monkeypatch.setattr(b, "discover_batches", slow_discover)
        out = []
        t = threading.Thread(target=lambda: out.append(b.check_and_heal(tmp_path)))
        t.start()
        assert started.wait(5)
        b._reinitializing = True                  # what the winner sets once it is past discovery
        second = b.check_and_heal(tmp_path)
        assert second == {"batches": {}, "running": True}
        assert b._reinitializing is True, "the losing pass must not clear the winner's flag"
        assert len(calls) == 1
        release.set(); t.join(5)
        assert out == [{"batches": {}}]
        assert b._reinitializing is False

    def test_the_lock_is_released_when_a_pass_raises(self, monkeypatch, tmp_path):
        from resource_explorer import bootstrap as b
        monkeypatch.setattr(b, "discover_batches", lambda d: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(RuntimeError):
            b.check_and_heal(tmp_path)
        monkeypatch.setattr(b, "discover_batches", lambda d: [])
        assert b.check_and_heal(tmp_path) == {"batches": {}}

    def test_the_route_turns_a_running_result_into_409(self, client, monkeypatch):
        _admin(monkeypatch, True)
        with patch("resource_explorer.bootstrap.check_and_heal", return_value={"batches": {}, "running": True}):
            res = client.post("/api/bootstrap/run", json={})
        assert res.status_code == 409
