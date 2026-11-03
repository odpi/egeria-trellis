"""A change of look writes one activity row, and Admin counts them (RULING-THEMES-PORTAL-DARK-LIGHT.md section 1).

The row goes through the existing activity log (`write_activity`): no table of its own, no DDL.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from resource_explorer.config import FeedbackConfig
from resource_explorer.look_stats import look_counts
from resource_explorer.registry import ProjectRegistry

ADMIN = {"X-Admin-Token": "tok"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path/'t.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", db_url)
    ProjectRegistry(database_url=db_url)._init_schema()
    import resource_explorer.web.routes.activity as activity_routes
    monkeypatch.setattr(activity_routes, "_registry", lambda: ProjectRegistry(database_url=db_url))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        _pinned_init(db_url), raising=True)
    monkeypatch.setattr("resource_explorer.web.routes.feedback.get_config",
                        lambda: SimpleNamespace(feedback=FeedbackConfig(admin_token="tok")))
    from resource_explorer.web.app import app
    return TestClient(app), db_url


def _pinned_init(db_url):
    real = ProjectRegistry.__init__

    def init(self, *a, **kw):
        kw.setdefault("database_url", db_url)
        return real(self, *a, **kw)
    return init


def _rows(db_url):
    return ProjectRegistry(database_url=db_url).list_activity(operation="look_change")


def test_a_change_writes_one_activity_row(client):
    c, db = client
    r = c.post("/api/activity/look-change", json={"look": "light", "previous": "dark"})
    assert r.status_code == 200
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["summary"] == "look changed to Light"
    assert '"previous": "dark"' in rows[0]["detail"]


def test_an_unknown_look_is_refused_and_writes_nothing(client):
    c, db = client
    assert c.post("/api/activity/look-change", json={"look": "neon"}).status_code == 400
    assert _rows(db) == []


def test_admin_counts_read_from_those_rows(client):
    c, _ = client
    for look, prev in (("light", "dark"), ("paper", "light"), ("dark", "paper"), ("paper", "dark")):
        assert c.post("/api/activity/look-change", json={"look": look, "previous": prev}).status_code == 200
    r = c.get("/api/feedback/look-counts", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["changes"] == 4
    # One unsigned person: their latest change landed on paper; they left dark twice and light and paper once each.
    assert body["looks"]["paper"] == {"people": 1, "switched_away": 1}
    assert body["looks"]["dark"] == {"people": 0, "switched_away": 2}
    assert body["looks"]["light"] == {"people": 0, "switched_away": 1}


def test_the_counts_are_admin_only(client):
    c, _ = client
    assert c.get("/api/feedback/look-counts").status_code == 403


def _row(ts, user, previous, look):
    import json
    return {"operation": "look_change", "ts": ts, "detail": json.dumps({"user": user, "previous": previous, "look": look})}


def test_people_are_counted_by_their_latest_look_and_arrivals_are_not_departures():
    rows = [
        _row("2026-10-20T10:00", "ann", "dark", "light"),
        _row("2026-10-20T11:00", "ann", "light", "paper"),
        _row("2026-10-20T09:00", "bob", "dark", "light"),
    ]
    out = look_counts(rows)
    assert out["looks"]["paper"] == {"people": 1, "switched_away": 0}
    assert out["looks"]["light"] == {"people": 1, "switched_away": 1}
    assert out["looks"]["dark"] == {"people": 0, "switched_away": 2}
    assert out["people_who_changed"] == 2 and out["changes"] == 3


def test_rows_that_are_not_look_changes_or_are_unreadable_are_ignored():
    rows = [{"operation": "survey", "ts": "x", "detail": "{}"},
            {"operation": "look_change", "ts": "x", "detail": "not json"},
            {"operation": "look_change", "ts": "x", "detail": '{"look": "neon"}'}]
    assert look_counts(rows)["changes"] == 0
