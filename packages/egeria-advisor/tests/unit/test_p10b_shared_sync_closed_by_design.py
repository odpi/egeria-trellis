"""D16 P10b, EA's half: CLOSED BY DESIGN (+ these tests). Architect's ruling, 2026-11-16
(RULING-D16-P10B-EA-CACHE-PREMISE-2026-11-16.md, option 2, on the owner's word "Close by design").

A user's private, zoned draft can't reach EA's shared report-spec cache, because of three barriers:
1. **Identity:** the sync reads Egeria as EA's service account, never as the signed-in admin who presses the
   button. That account can't see a draft zoned to someone else.
2. **Scope:** the sync reads only the configured consumer folder(s). An authoring draft is never filed into one.
3. **The zone skip:** pyegeria skips every zoned spec from a `shared=True` sync. That is tested in pyegeria,
   not here.

These tests pin barriers 1 and 2 on EA's side, on fakes: no Egeria, no database, no network. If one fails, the
design is no longer closed. Reopen P10b if EA ever syncs as a user, reads beyond its consumer folders, or gains a
per-user EA tier.
"""
from __future__ import annotations

import inspect
import json
import time

import pytest
from fastapi.testclient import TestClient

from advisor import auth
from advisor import egeria_spec_sync as ess

ADMIN = "erinoverview"                       # a signed-in EA admin, who can see her own zoned drafts
CALLER_EGERIA_TOKEN = "caller-egeria-token-must-not-reach-the-sync"
SERVICE_USER, SERVICE_PWD = "ea-service-account", "service-pw-fake"   # fakes, set on settings below
VIEW, PLATFORM = "fake-view-server", "https://fake-egeria:9443"


class _Seen:
    """What the sync was given: the connection, the client and the sync_report_specs call."""

    def __init__(self):
        self.conns, self.clients, self.calls = [], [], []

    def text(self) -> str:
        return json.dumps({"conns": self.conns, "clients": [vars(c) for c in self.clients],
                           "calls": [{"folders": f, "kwargs": k} for f, k in self.calls]}, default=str)


class _FakeClient:
    def __init__(self, conn):
        self.user_id = conn["user_id"]
        self.user_pwd = conn.get("user_pwd")
        self.token = conn.get("token")
        self.platform_url, self.view_server = conn["platform_url"], conn["view_server"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """A fake service account and platform, and no real client or sync. Both env ways of the gate
    (EGERIA_USER unset, or set to someone else) see the same settings, because these are set here."""
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN", "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ",
                "TRELLIS_ADMIN_USERS", "TRELLIS_ADMIN_TOKEN", "ADVISOR_ADMIN_USERS", "ADVISOR_ADMIN_TOKEN",
                ess.ADVISOR_FOLDERS_ENV, ess.PYEGERIA_FOLDERS_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path))
    monkeypatch.setenv(ess.CACHE_DIR_ENV, str(tmp_path / "cache"))
    auth.reset_policy_cache()
    ess._reset_for_tests()

    from advisor.config import settings
    monkeypatch.setattr(settings, "egeria_user", SERVICE_USER)
    monkeypatch.setattr(settings, "egeria_password", SERVICE_PWD)
    import advisor.mcp_config as mcp_config
    monkeypatch.setattr(mcp_config, "get_pyegeria_platform_config",
                        lambda *a, **k: {"view_server": VIEW, "platform_url": PLATFORM})

    seen = _Seen()

    def build(conn):
        seen.conns.append(dict(conn))
        c = _FakeClient(conn)
        seen.clients.append(c)
        return c
    monkeypatch.setattr(ess, "_build_client", build)

    from pyegeria.view import egeria_spec_cache
    from pyegeria.view.egeria_spec_cache import SyncReport

    def fake_sync(client, folders=None, **kwargs):
        seen.calls.append((list(folders or []), dict(kwargs)))
        return SyncReport(folders=list(folders or []))
    monkeypatch.setattr(egeria_spec_cache, "sync_report_specs", fake_sync)
    yield seen
    ess._reset_for_tests()
    auth.reset_policy_cache()


def _bearer(user: str) -> dict:
    return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token=CALLER_EGERIA_TOKEN)}"}


def _wait_done(limit_s: float = 5.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < limit_s:
        st = ess.sync_status()
        if not st["running"]:
            return st
        time.sleep(0.02)
    raise AssertionError("the background sync did not finish")


# ── barrier 1: identity ────────────────────────────────────────────────────────────────────────────────

def test_an_admins_sync_reads_as_the_service_account_never_as_the_admin(_isolated, monkeypatch):
    """(a) POST /api/admin/specs/sync as a signed-in admin: the client is built from _service_connection(),
    and the admin's identity and Egeria token never reach sync_report_specs."""
    seen = _isolated
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    from advisor.web.app import app
    built_from = []
    real = ess._service_connection
    monkeypatch.setattr(ess, "_service_connection", lambda: built_from.append(1) or real())

    r = TestClient(app, raise_server_exceptions=False).post("/api/admin/specs/sync", headers=_bearer(ADMIN))
    assert r.status_code == 200, r.text
    st = _wait_done()
    assert st["state"] == "ok", st

    assert built_from == [1]                                     # the connection came from _service_connection()
    assert len(seen.calls) == 1 and len(seen.clients) == 1
    assert seen.conns[0] == {"view_server": VIEW, "platform_url": PLATFORM, "user_id": SERVICE_USER,
                             "user_pwd": SERVICE_PWD, "token": ""}
    assert seen.clients[0].user_id == SERVICE_USER and not seen.clients[0].token
    assert st["egeria_held"]["identity"] == SERVICE_USER         # what the status reports it read as
    text = seen.text()
    assert ADMIN not in text and CALLER_EGERIA_TOKEN not in text  # nothing of the caller reached the sync


def test_a_non_admin_cannot_start_the_sync(_isolated, monkeypatch):
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    from advisor.web.app import app
    r = TestClient(app, raise_server_exceptions=False).post("/api/admin/specs/sync", headers=_bearer("peterprofile"))
    assert r.status_code == 403 and not _isolated.calls


def test_the_sync_route_takes_no_caller_input_at_all():
    """Structural: the route and start_sync have no request, credential or folder parameter, so no caller's
    identity or scope can be passed in. A change that adds one reopens P10b."""
    from advisor.web.reports import start_spec_sync
    assert list(inspect.signature(start_spec_sync).parameters) == []
    assert list(inspect.signature(ess.start_sync).parameters) == ["client_factory"]
    assert list(inspect.signature(ess._service_connection).parameters) == []


def test_the_service_connection_ignores_any_signed_in_user(_isolated):
    """_service_connection() reads with no creds, so it falls back to the service account whoever is signed in."""
    conn = ess._service_connection()
    assert conn["user_id"] == SERVICE_USER and conn["user_pwd"] == SERVICE_PWD and conn["token"] == ""


# ── barrier 2: scope (shared, consumer folders only) ──────────────────────────────────────────────────

def test_sync_now_is_shared_and_reads_only_the_default_consumer_folder(_isolated):
    """(b) No folders configured: the one default consumer folder, as a shared sync."""
    ess.sync_now()
    (folders, kwargs), = _isolated.calls
    assert folders == [ess.DEFAULT_FOLDER] == ["ReportSpecFolder::Consumer::EgeriaAdvisor"]
    assert kwargs["shared"] is True
    assert set(kwargs) <= {"shared", "recursive"}      # nothing that widens what is read, or asks as a user


@pytest.mark.parametrize("var", [ess.ADVISOR_FOLDERS_ENV, ess.PYEGERIA_FOLDERS_ENV])
def test_sync_now_reads_exactly_the_configured_folders(_isolated, monkeypatch, var):
    monkeypatch.setenv(var, "ReportSpecFolder::Consumer::EgeriaAdvisor, ReportSpecFolder::Consumer::Other")
    ess.sync_now()
    (folders, kwargs), = _isolated.calls
    assert folders == ["ReportSpecFolder::Consumer::EgeriaAdvisor", "ReportSpecFolder::Consumer::Other"]
    assert kwargs["shared"] is True


def test_no_folder_configured_reads_no_folder(_isolated, monkeypatch):
    monkeypatch.setenv(ess.ADVISOR_FOLDERS_ENV, "")
    ess.sync_now()
    (folders, kwargs), = _isolated.calls
    assert folders == [] and kwargs["shared"] is True


def test_the_admin_sync_is_shared_and_folder_scoped_too(_isolated, monkeypatch):
    """The same holds on the admin path, not only for a direct sync_now()."""
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    from advisor.web.app import app
    assert TestClient(app, raise_server_exceptions=False).post(
        "/api/admin/specs/sync", headers=_bearer(ADMIN)).status_code == 200
    _wait_done()
    (folders, kwargs), = _isolated.calls
    assert folders == [ess.DEFAULT_FOLDER] and kwargs["shared"] is True
