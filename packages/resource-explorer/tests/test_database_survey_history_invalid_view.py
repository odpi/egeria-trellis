"""The database survey history's "show invalid" view (2026-09-30).

The false-zero hotfix marked bad survey rows invalid instead of deleting them,
but only `ProjectRegistry.get_database_surveys(include_invalid=True)` could see
them: no route or UI passed the flag, so a mark-don't-delete repair was, to a
person, indistinguishable from a hidden deletion. These tests cover the route
parameter and the classic UI's rendering (real node execution of the extracted
render function, not string greps) for the four states:

  (a) toggle off      -> invalid row absent
  (b) toggle on       -> invalid row present and muted
  (c) invalid row     -> shows its reason text
  (d) invalid row     -> shows when it was MARKED, not only when it was surveyed
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

SLUG = "coco"
REAL = {"schema_info": {"schemas": [{"name": "s"}], "total_tables": 61, "total_columns": 479}}
EMPTY = {"schema_info": {}, "statistics": {}}
REASON = "false-zero: publish row with empty schema_info"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(
        slug=SLUG, display_name=SLUG, db_type="postgresql", host="localhost",
        port=5432, database_name=SLUG, db_user="u", db_password="p"))
    r.record_database_survey(SLUG, 8, 61, 479, REAL, source="local",
                             surveyed_at="2026-09-28T03:00:00")
    r.record_database_survey(SLUG, 0, 0, 0, EMPTY, source="egeria-published",
                             surveyed_at="2026-09-30T12:17:19")
    r.mark_database_survey_invalid(SLUG, "2026-09-30T12:17:19", "egeria-published", REASON)
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", r.__dict__) or None)
    return r


# ── route ────────────────────────────────────────────────────────────────────


def test_route_excludes_invalid_by_default(registry):
    from resource_explorer.web.app import app
    rows = TestClient(app).get(f"/api/databases/{SLUG}/surveys").json()
    assert [r["surveyed_at"] for r in rows] == ["2026-09-28T03:00:00"]


def test_route_include_invalid_returns_the_marked_row_with_reason_and_time(registry):
    from resource_explorer.web.app import app
    rows = TestClient(app).get(f"/api/databases/{SLUG}/surveys?include_invalid=true").json()
    assert [r["surveyed_at"] for r in rows] == ["2026-09-30T12:17:19", "2026-09-28T03:00:00"]
    bad = rows[0]
    assert bad["invalid_reason"] == REASON
    assert bad["invalid_at"] and bad["invalid_at"] != bad["surveyed_at"]
    assert not rows[1].get("invalid_at")


# ── classic UI: real execution of the extracted render function ──────────────

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

