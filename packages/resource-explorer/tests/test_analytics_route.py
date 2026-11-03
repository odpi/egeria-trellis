"""Curate's By-analysis tab reads the Analytic Library's curation_coverage through one read-only route.

Temp SQLite throughout; the real registry is never opened. One source: the routine's undecided / chosen
counts are the Contents tab's own (`build_scope_view(...)["counts"]`), because both count `resolve_node_set`."""
from __future__ import annotations

import builtins

import pytest
from fastapi.testclient import TestClient

from resource_explorer import catalogue_scope as scope
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.web.routes import analytics


@pytest.fixture
def reg(tmp_path):
    db = str(tmp_path / "t.db")
    assert db.startswith(str(tmp_path))
    r = ProjectRegistry(db_path=db)
    r.register_database(DatabaseEntity(slug="demo", display_name="demo", db_type="postgresql", host="localhost",
                                       port=5442, database_name="demo"))
    r.write_detail_rows("database_tables", "demo", "2026-10-01T00:00:00Z", rows=[
        {"schema_name": s, "table_name": "t", "table_type": "BASE TABLE", "row_count": 1, "column_count": 1}
        for s in ("sales", "hr", "ops", "fin")])
    return r


@pytest.fixture
def client(reg, monkeypatch):
    monkeypatch.setattr(analytics, "registry_url", lambda: reg.database_url)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    return TestClient(app)


def test_route_returns_the_routine_result(client):
    body = client.get("/api/analytics/curation-coverage/database/demo").json()
    assert body["envelope"]["result_state"] in ("measured", "nothing_found")
    assert body["known_schemas"] == 4
    assert [g["key"] for g in body["groups"]] == ["published", "not_yet", "blocked"]


def test_undecided_count_is_the_contents_tabs_count(client, reg):
    scope.set_node_choice(reg, "demo", "tester", schema="sales", choice="catalogue")
    scope.set_node_choice(reg, "demo", "tester", schema="hr", choice="leave_out")
    contents = scope.build_scope_view(reg, "demo")["counts"]
    body = client.get("/api/analytics/curation-coverage/database/demo").json()
    ny = {l["key"]: l["count"] for g in body["groups"] if g["key"] == "not_yet" for l in g["lines"]}
    assert contents["schemas_undecided"] == ny["undecided"] == 2
    assert contents["schemas_catalogue"] == ny["chosen"] + ny["chosen_confirmed"] == 1
    assert contents["schemas_leave_out"] == ny["left_out"] + ny["left_out_confirmed"] == 1


def test_library_not_installed_is_not_applicable_never_an_error(client, monkeypatch):
    real = builtins.__import__

    def deny(name, *a, **kw):
        if name.startswith("trellis_analytics"):
            raise ImportError("No module named 'trellis_analytics'")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", deny)
    r = client.get("/api/analytics/curation-coverage/database/demo")
    assert r.status_code == 200
    body = r.json()
    assert body["envelope"]["result_state"] == "not_applicable"
    assert "not installed" in body["envelope"]["result_state_reason"]
    assert body["groups"] == []


def test_unreadable_registry_is_not_established(client, monkeypatch):
    monkeypatch.setattr(analytics, "registry_url", lambda: "sqlite:////nonexistent-dir/none.db")
    r = client.get("/api/analytics/curation-coverage/database/demo")
    assert r.status_code == 200
    assert r.json()["envelope"]["result_state"] == "not_established"


# --- the Portal tile route -------------------------------------------------------------------------------
# An ORDINARY authenticated route (no allowlist entry, no in-route token handling). The Portal's flow:
#   1. POST /api/auth/portal {"portal_token": <SSO JWT>} -> {"access_token": <RE session JWT>, ...}
#   2. GET /api/analytics/curation-coverage-tile/{type}/{slug}  Authorization: Bearer <access_token>

import time

import jwt as _jwt

TILE = "/api/analytics/curation-coverage-tile"
SECRET = "portal-test-secret"


@pytest.fixture
def tclient(reg, monkeypatch):
    """Anonymous-read is ON here on purpose: the tile route must still refuse an anonymous GET, which is the
    one mode in which the login gate lets an unauthenticated read through."""
    monkeypatch.setattr(analytics, "registry_url", lambda: reg.database_url)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setenv("TRELLIS_PORTAL_SECRET", SECRET)
    from resource_explorer import auth
    auth.reset_auth_secrets_cache()
    auth.reset_policy_cache()
    from resource_explorer.web.app import app
    yield TestClient(app)
    auth.reset_auth_secrets_cache()
    auth.reset_policy_cache()


def _portal_jwt(secret=SECRET, exp=3600, **over):
    claims = {"sub": "erin", "role": "user", "display_name": "Erin", "egeria_token": "tok",
              "iat": int(time.time()), "exp": int(time.time()) + exp, **over}
    return _jwt.encode(claims, secret, algorithm="HS256")


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def _session(tclient, **over):
    r = tclient.post("/api/auth/portal", json={"portal_token": _portal_jwt(**over)})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_no_analytics_path_is_on_the_public_allowlist():
    from resource_explorer.auth import RE_PUBLIC_PATHS, get_policy
    assert not [p for p in RE_PUBLIC_PATHS if p.startswith("/api/analytics")]
    assert not get_policy().is_public(f"{TILE}/database/demo")


def _without_as_of(tile: dict) -> dict:
    """A tile minus the per-call timestamp: `manifest.as_of` and the `provenance` sentence that quotes it."""
    out = dict(tile)
    out["manifest"] = {k: v for k, v in (tile.get("manifest") or {}).items() if k != "as_of"}
    out["provenance"] = (tile.get("provenance") or "").split(" · as of ")[0]
    return out


def test_portal_flow_end_to_end_runs_as_the_token_subject(tclient, reg, monkeypatch):
    from resource_explorer.a2a_auth import current_caller
    from trellis_analytics.portal import curation_coverage_tile
    seen = []
    real = analytics.curation_coverage_tile_for

    def spy(*a, **kw):
        c = current_caller.get()
        seen.append((c.user_id, c.egeria_token) if c else None)
        return real(*a, **kw)
    monkeypatch.setattr(analytics, "curation_coverage_tile_for", spy)

    session = _session(tclient)                                   # step 1: exchange
    r = tclient.get(f"{TILE}/database/demo", headers=_bearer(session))   # step 2: read
    assert r.status_code == 200
    # Same tile, one source -- but `as_of` is stamped per call, so two calls can straddle a second boundary
    # (CI flake on #651: 13:10:21Z vs 13:10:22Z). Compare everything except the per-call timestamp.
    assert _without_as_of(r.json()) == _without_as_of(curation_coverage_tile("demo", reg.database_url, "database"))
    assert r.json()["state"] in ("measured", "nothing_found")
    assert seen == [("erin", "tok")]                              # the route ran as the Portal user


def test_tile_route_without_auth_is_401_even_with_anonymous_read(tclient):
    from resource_explorer.auth import get_policy
    assert get_policy().anonymous_read is True                    # the mode under test really is on
    assert tclient.get(f"{TILE}/database/demo").status_code == 401
    # ...and that mode does let other anonymous reads through, so the 401 above is the route's own doing.
    assert tclient.get("/api/auth/me").status_code == 200


def test_a_portal_jwt_sent_straight_to_the_tile_route_is_401(tclient):
    assert tclient.get(f"{TILE}/database/demo", headers=_bearer(_portal_jwt())).status_code == 401


def test_forged_or_expired_session_token_is_401(tclient):
    from resource_explorer.auth import create_access_token
    assert tclient.get(f"{TILE}/database/demo", headers=_bearer("not.a.jwt")).status_code == 401
    forged = _jwt.encode({"sub": "erin", "egeria_token": "tok", "exp": int(time.time()) + 600}, "wrong", algorithm="HS256")
    assert tclient.get(f"{TILE}/database/demo", headers=_bearer(forged)).status_code == 401
    good = create_access_token("erin", "tok")
    claims = _jwt.decode(good, options={"verify_signature": False})
    from resource_explorer.auth import auth_config
    expired = _jwt.encode({**claims, "exp": int(time.time()) - 60}, auth_config().jwt_secret, algorithm="HS256")
    assert tclient.get(f"{TILE}/database/demo", headers=_bearer(expired)).status_code == 401


def test_daemon_subject_is_403(tclient, monkeypatch):
    from resource_explorer import egeria_clients
    from resource_explorer.auth import create_access_token
    monkeypatch.setattr(egeria_clients, "_daemon_credential", lambda: ("svc-daemon", "x"))
    r = tclient.post("/api/auth/portal", json={"portal_token": _portal_jwt(sub="svc-daemon")})
    assert r.status_code == 403                                   # refused at the exchange
    session = create_access_token("svc-daemon", "tok")            # a pre-existing daemon session
    assert tclient.get(f"{TILE}/database/demo", headers=_bearer(session)).status_code == 403


def test_tile_route_unsurveyed_slug_is_not_established_not_zeros(tclient):
    r = tclient.get(f"{TILE}/database/never-surveyed", headers=_bearer(_session(tclient)))
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "not_established" and body["state_reason"]
    assert "Published" not in body            # no chart of zeros


def test_tile_route_unknown_entity_type_is_404(tclient):
    assert tclient.get(f"{TILE}/spreadsheet/demo", headers=_bearer(_session(tclient))).status_code == 404


def test_missing_library_is_not_established_not_not_applicable(tclient, monkeypatch):
    real = builtins.__import__

    def deny(name, *a, **kw):
        if name.startswith("trellis_analytics"):
            raise ImportError("No module named 'trellis_analytics'", name="trellis_analytics")
        return real(name, *a, **kw)
    session = _session(tclient)
    monkeypatch.setattr(builtins, "__import__", deny)
    r = tclient.get(f"{TILE}/database/demo", headers=_bearer(session))
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "not_established"
    assert body["state_reason"] == "the Analytic Library is not installed on this RE"


def test_a_transitive_import_error_surfaces_it_is_not_a_missing_library():
    def broken(*a, **kw):
        raise ImportError("No module named 'psycopg2'", name="psycopg2")
    import sys
    import types
    fake = types.ModuleType("trellis_analytics.portal")
    fake.__getattr__ = lambda n: broken()
    sys.modules["trellis_analytics.portal"], saved = fake, sys.modules.get("trellis_analytics.portal")
    try:
        with pytest.raises(ImportError, match="psycopg2"):
            analytics.curation_coverage_tile_for("database", "demo", "sqlite:///x")
    finally:
        if saved is not None:
            sys.modules["trellis_analytics.portal"] = saved
        else:
            del sys.modules["trellis_analytics.portal"]
