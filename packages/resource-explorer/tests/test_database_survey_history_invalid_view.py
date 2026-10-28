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

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

INDEX = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "index.html"
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

def _fn(html: str, name: str, kw: str = "function") -> str:
    start = html.index(f"{kw} {name}(")
    depth, i = 1, html.index("{", start) + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        i += 1
    return html[start:i]


def _render(rows: list[dict]) -> str:
    html = INDEX.read_text()
    js = (_fn(html, "_esc") + "\n" + _fn(html, "_dbSurveyHistoryTableHtml")
          + f"\nprocess.stdout.write(_dbSurveyHistoryTableHtml({json.dumps(rows)}));")
    return subprocess.run(["node", "-e", js], capture_output=True, text=True,
                          check=True, timeout=30).stdout


VALID = {"surveyed_at": "2026-09-28T03:00:00", "schema_count": 8, "table_count": 61,
         "column_count": 479, "source": "local"}
INVALID = {"surveyed_at": "2026-09-30T12:17:19", "schema_count": 0, "table_count": 0,
           "column_count": 0, "source": "egeria-published",
           "invalid_at": "2026-09-30T14:02:11.123", "invalid_reason": REASON}

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_toggle_is_wired_to_the_include_invalid_fetch():
    html = INDEX.read_text()
    assert 'id="db-show-invalid-toggle"' in html
    assert "toggleDbInvalidSurveys(this.checked)" in html
    assert "/surveys?include_invalid=true" in _fn(html, "toggleDbInvalidSurveys", "async function")


@needs_node
def test_a_toggle_off_shows_no_invalid_row():
    """Off = the default response (valid rows only): no invalid marker, no
    Invalid column, exactly today's table."""
    out = _render([VALID, dict(VALID, surveyed_at="2026-09-27T03:00:00")])
    assert "data-invalid" not in out and "opacity-50" not in out
    assert ">Invalid<" not in out and "12:17" not in out


@needs_node
def test_b_toggle_on_shows_the_invalid_row_muted():
    out = _render([INVALID, VALID])
    rows = out.split("<tr class=")[1:]                    # header row is a bare <tr>
    assert len(rows) == 2
    assert "data-invalid" in rows[0] and "opacity-50" in rows[0]
    assert "2026-09-30 12:17 UTC" in rows[0]
    assert "data-invalid" not in rows[1] and "opacity-50" not in rows[1]


@needs_node
def test_c_invalid_row_shows_its_reason_text():
    assert REASON in _render([INVALID, VALID])


@needs_node
def test_c2_missing_reason_is_said_not_blank():
    out = _render([dict(INVALID, invalid_reason=""), VALID])
    assert "(no reason recorded)" in out


@needs_node
def test_d_invalid_row_shows_when_it_was_marked_not_only_when_surveyed():
    out = _render([INVALID, VALID])
    assert "marked invalid" in out
    assert "2026-09-30 14:02 UTC" in out                  # invalid_at
    assert "2026-09-30 12:17 UTC" in out                  # surveyed_at, still there
