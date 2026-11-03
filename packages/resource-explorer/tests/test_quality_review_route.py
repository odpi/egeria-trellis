"""Quality-check review on a database's Curate page: the read route, the verdict write, and run_checks seeing it.

Real route, real registry (a temp SQLite), real propose and run-checks code. Faked only at the boundaries already
used by the value-profile tests: the surveyed Postgres (FakeConn), and the Egeria access check (zone_fakes).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.auth import create_access_token
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    BOUNDS,
    FakeConn,
    PROFILED_AT,
    SLUG,
    _connect,
    _orders_measure,
    _profile,
    _propose,
    entity,
    registry,
)
from resource_explorer.surveyors.database.quality.run_checks import NO_ACCEPTED_CHECKS, run_quality_checks

BASE = f"/api/quality-checks/{SLUG}"


@pytest.fixture(autouse=True)
def _curation_access_reads(monkeypatch):
    from tests.zone_fakes import install

    return install(monkeypatch)


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import quality_checks as routes

    monkeypatch.setattr(routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def as_user(u):
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


@pytest.fixture
def proposed(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    return registry


def _rows(client, **params):
    r = client.get(f"{BASE}/proposals", params={"schema": "sales", "table": "orders", **params})
    assert r.status_code == 200, r.text
    [g] = r.json()["groups"]
    return {x["check_key"]: x for x in g["rows"]}


# ── read ─────────────────────────────────────────────────────────────────────

def test_the_read_groups_by_table_with_counts_and_no_rows(client, proposed):
    body = client.get(f"{BASE}/proposals").json()
    assert body["has_proposals"] and body["groups"]
    assert all(g["rows"] is None for g in body["groups"])                      # rows only on request
    assert sum(g["counts"]["total"] for g in body["groups"]) == body["counts"]["total"] == body["shown"]["total"]
    assert body["counts"]["proposed"] + body["counts"]["not_established"] == body["counts"]["total"]
    keys = [(g["schema_name"], g["table_name"]) for g in body["groups"]]
    assert keys == sorted(keys) and ("sales", "orders") in keys
    assert body["surveyed_at"] and body["profile_surveyed_at"] == PROFILED_AT


def test_one_group_carries_its_rows_with_test_type_dimension_and_parameters(client, proposed):
    rows = _rows(client)
    required = rows["sales.orders.placed::Required"]
    assert required["test_type"] == "Required" and required["dimension"] == "completeness"
    assert required["status"] == "proposed" and required["selectable"] is True and required["verdict"] is None
    assert required["params"] and required["profile_surveyed_at"] == PROFILED_AT


def test_filters_narrow_rows_and_counts_but_the_options_stay_whole(client, proposed):
    whole = client.get(f"{BASE}/proposals").json()
    narrowed = client.get(f"{BASE}/proposals", params={"test_type": "Required"}).json()
    assert narrowed["shown"]["total"] < whole["shown"]["total"]
    assert narrowed["filters"] == whole["filters"]                              # the way back is never hidden
    assert all(set(g["counts"]) and g["counts"]["total"] > 0 for g in narrowed["groups"])
    rows = _rows(client, test_type="Required")
    assert {r["test_type"] for r in rows.values()} == {"Required"}
    by_dim = _rows(client, dimension="completeness")
    assert by_dim and {r["dimension"] for r in by_dim.values()} == {"completeness"}
    assert client.get(f"{BASE}/proposals", params={"state": "nonsense"}).status_code == 400


def test_a_not_established_row_shows_its_reason_and_has_no_selector(client, proposed):
    body = client.get(f"{BASE}/proposals", params={"state": "not_established"}).json()
    assert body["shown"]["total"] == body["counts"]["not_established"] > 0
    g = body["groups"][0]
    detail = client.get(f"{BASE}/proposals", params={"state": "not_established", "schema": g["schema_name"],
                                                     "table": g["table_name"]}).json()["groups"][0]["rows"]
    assert detail
    for r in detail:
        assert r["status"] == "not_established" and r["selectable"] is False and r["reason"]


def test_an_unknown_database_or_table_is_404(client, proposed):
    assert client.get("/api/quality-checks/nope/proposals").status_code == 404
    assert client.get(f"{BASE}/proposals", params={"schema": "x", "table": "y"}).status_code == 404


def test_no_proposals_yet_is_said_not_shown_as_zero_checks(client, registry):
    body = client.get(f"{BASE}/proposals").json()
    assert body["has_proposals"] is False and body["groups"] == [] and body["surveyed_at"] == ""


# ── write ────────────────────────────────────────────────────────────────────

def _press(client, key, verdict, user="steward"):
    return client.post(f"{BASE}/verdict", json={"check_key": key, "verdict": verdict}, headers=as_user(user))


def test_an_accept_is_recorded_as_the_signed_in_person_and_read_back(client, proposed, registry):
    key = "sales.orders.placed::Required"
    r = _press(client, key, "accepted", "steward")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True
    row = registry.get_quality_verdicts(SLUG)[key]
    assert (row["verdict"], row["decided_by"]) == ("accepted", "steward") and row["created_at"]
    assert row["proposal_id"] == _rows(client)[key]["id"]
    stored = next(p for p in proposed.query_detail_rows("database_quality_proposals", SLUG) if p["check_key"] == key)
    assert row["params_json"] == stored["params_json"] and row["params_json"]       # the proposal's own, not the caller's
    assert body["check"]["status"] == "accepted" and body["check"]["verdict"]["decided_by"] == "steward"
    assert _rows(client)[key]["status"] == "accepted"                           # the read agrees with the write


def test_the_person_comes_from_the_session_never_the_body(client, proposed, registry):
    key = "sales.orders.placed::Required"
    r = client.post(f"{BASE}/verdict", json={"check_key": key, "verdict": "accepted", "decided_by": "mallory",
                                             "params_json": {"x": 1}}, headers=as_user("steward"))
    assert r.status_code == 200
    row = registry.get_quality_verdicts(SLUG)[key]
    assert row["decided_by"] == "steward" and "x" not in row["params_json"]


def test_a_second_press_is_a_new_row_and_reject_is_a_verdict(client, proposed, registry):
    key = "sales.orders.placed::Required"
    assert _press(client, key, "accepted", "steward").status_code == 200
    assert _press(client, key, "rejected", "other").status_code == 200
    history = registry.list_quality_verdict_history(SLUG, key)
    assert [(h["verdict"], h["decided_by"]) for h in history] == [("rejected", "other"), ("accepted", "steward")]
    assert registry.get_quality_verdicts(SLUG)[key]["verdict"] == "rejected"
    assert _rows(client)[key]["status"] == "rejected"
    assert _press(client, key, "accepted", "steward").status_code == 200        # changing it again: a third row
    assert len(registry.list_quality_verdict_history(SLUG, key)) == 3


def test_unauthenticated_is_refused_and_writes_nothing(client, proposed, registry):
    key = "sales.orders.placed::Required"
    r = client.post(f"{BASE}/verdict", json={"check_key": key, "verdict": "accepted"})
    assert r.status_code == 401
    assert registry.get_quality_verdicts(SLUG) == {}


def test_a_not_established_check_and_an_unknown_one_cannot_be_decided(client, proposed, registry):
    ne = next(r for r in client.get(f"{BASE}/proposals", params={"state": "not_established"}).json()["groups"])
    rows = client.get(f"{BASE}/proposals", params={"state": "not_established", "schema": ne["schema_name"],
                                                   "table": ne["table_name"]}).json()["groups"][0]["rows"]
    r = _press(client, rows[0]["check_key"], "accepted")
    assert r.status_code == 409 and "not established" in r.json()["detail"]
    assert _press(client, "no.such::Key", "accepted").status_code == 404
    assert client.post(f"{BASE}/verdict", json={"check_key": "a", "verdict": "retired"},
                       headers=as_user("steward")).status_code == 422          # retire is not this route's job
    assert registry.get_quality_verdicts(SLUG) == {}


# ── run_checks sees exactly what the route recorded ──────────────────────────

def test_run_checks_sees_exactly_the_check_accepted_through_the_route(client, proposed, registry, entity):
    assert run_quality_checks(entity, registry, _connect(FakeConn()), bounds=BOUNDS)["skipped_by_design"] == NO_ACCEPTED_CHECKS
    _press(client, "sales.orders.placed::Required", "accepted")
    _press(client, "sales.orders.order_id::Unique", "rejected")                  # rejected: not run
    conn = FakeConn(checks={("sales", "orders"): {"record_ct": 1000, "measure": _orders_measure}})
    out = run_quality_checks(entity, registry, _connect(conn), bounds=BOUNDS, surveyed_at="2026-10-21T09:00:00+00:00",
                             ran_as="surveyor")
    rows = {r["check_key"]: r for r in registry.query_detail_rows("database_quality_results", SLUG)}
    assert set(rows) == {"sales.orders.placed::Required"}
    assert rows["sales.orders.placed::Required"]["status"] == "fail"             # 5 nulls measured against its threshold
    assert "skipped_by_design" not in out


# ── a check this profile cannot evaluate has no parameters to accept ─────────

KEY = "sales.orders.placed::Required"


def _rewrite(registry, key, *, state, params):
    """The latest batch as a re-profile that could not evaluate `key` would have left it (propose.py keeps the state
    `accepted` / `suppressed` of a check with a verdict, with empty params)."""
    rows = registry.query_detail_rows("database_quality_proposals", SLUG)
    at = rows[0]["surveyed_at"]
    for r in rows:
        r.pop("id", None)
        if r["check_key"] == key:
            r["state"], r["params_json"] = state, params
            r["reason"] = "accepted; this profile could not re-evaluate it" if state == "accepted" else "rejected earlier"
    registry.write_detail_rows("database_quality_proposals", SLUG, at, rows=rows)


def _history(registry, key=KEY):
    return registry.list_quality_verdict_history(SLUG, key)


@pytest.mark.parametrize("state", ["accepted", "suppressed"])
def test_accept_on_a_row_with_no_parameters_is_409_and_writes_nothing(client, proposed, registry, state):
    assert _press(client, KEY, "accepted").status_code == 200                   # a good accept, with parameters
    good = registry.get_quality_verdicts(SLUG)[KEY]
    _rewrite(registry, KEY, state=state, params={})
    r = _press(client, KEY, "accepted")
    assert r.status_code == 409 and "could not evaluate" in r.json()["detail"]
    assert len(_history(registry)) == 1                                         # no new verdict row
    assert registry.get_quality_verdicts(SLUG)[KEY]["params_json"] == good["params_json"] != {}


def test_accept_with_no_parameters_and_no_earlier_verdict_is_refused_too(client, proposed, registry):
    _rewrite(registry, KEY, state="suppressed", params={})
    assert _press(client, KEY, "accepted").status_code == 409
    assert _history(registry) == []


def test_reject_on_a_row_with_no_parameters_is_recorded(client, proposed, registry):
    assert _press(client, KEY, "accepted").status_code == 200
    _rewrite(registry, KEY, state="accepted", params={})
    r = _press(client, KEY, "rejected")
    assert r.status_code == 200 and r.json()["recorded"] is True
    assert registry.get_quality_verdicts(SLUG)[KEY]["verdict"] == "rejected"
    assert len(_history(registry)) == 2


def test_the_read_marks_such_a_row_not_acceptable_with_the_reason_and_keeps_the_verdict(client, proposed, registry):
    from resource_explorer.quality_review import CANNOT_ACCEPT_SENTENCE

    assert _press(client, KEY, "accepted").status_code == 200
    _rewrite(registry, KEY, state="accepted", params={})
    rows = _rows(client)
    row = rows[KEY]
    assert row["accept_blocked"] == CANNOT_ACCEPT_SENTENCE and row["selectable"] is True      # Reject is still offered
    assert row["status"] == "accepted" and row["verdict"]["verdict"] == "accepted"            # the verdict stays visible
    other = rows["sales.orders.order_id::Unique"]
    assert other["accept_blocked"] == ""
    ne = [r for r in rows.values() if r["status"] == "not_established"]
    assert ne and all(r["accept_blocked"] == "" and r["selectable"] is False for r in ne)


def test_a_403_from_the_access_check_writes_nothing(client, proposed, registry, monkeypatch):
    from resource_explorer.workflows import curate as wc

    monkeypatch.setattr(wc, "resource_access",
                        lambda *a, **k: wc.AccessDecision(False, "zone x does not grant steward update in Egeria", "zone"))
    r = _press(client, KEY, "accepted")
    assert r.status_code == 403 and "does not grant" in r.json()["detail"]
    assert registry.get_quality_verdicts(SLUG) == {} and _history(registry) == []
    assert _press(client, KEY, "rejected").status_code == 403
    assert _history(registry) == []


# ── the settings, readable on the Admin page (architect, 2026-10-24) ───────────────────────────────────────

def test_the_settings_are_read_with_their_value_source_and_meaning(client, monkeypatch):
    from resource_explorer import config
    from resource_explorer.web import admin_auth

    monkeypatch.setattr(admin_auth, "require_admin", lambda request: None)
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.setenv("VALUE_PROFILE_TABLE_BUDGET_SECONDS", "120")
    monkeypatch.delenv("VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE", raising=False)
    r = client.get("/api/quality-checks/settings")
    assert r.status_code == 200, r.text
    rows = {s["name"]: s for s in r.json()["settings"]}
    live = rows["live_write_table_share"]
    assert (live["value"], live["source"], live["setting"]) == (0.25, "default", "VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE")
    assert "this database is written live · see change rates" in live["means"]
    assert (rows["table_budget_seconds"]["value"], rows["table_budget_seconds"]["source"]) == (
        120, "set in the environment")
    assert all(s["means"] and s["unit"] for s in rows.values())


def test_the_settings_are_admin_only(client):
    assert client.get("/api/quality-checks/settings").status_code == 403
