"""Parity slice G3: the read-only server pieces behind the database report
(PI-037 slim history, PI-032 column default, PI-036 ranked tables with their
activity, PI-040 views).

Everything here reads; nothing writes to Egeria or the shared registry. Every
fixture is its own temp SQLite.
"""
from __future__ import annotations

import json
import os

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry, SECTION_TABLES

SLUG = "g3db"
T1 = "2026-10-03T09:00:00"
T2 = "2026-10-06T14:02:00"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(
        slug=SLUG, display_name=SLUG, db_type="postgresql", host="localhost",
        port=5432, database_name=SLUG))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(
            self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    return TestClient(app)


def _survey(registry, at, blob, source="local"):
    registry.record_database_survey(SLUG, 1, 2, 3, blob, source=source, surveyed_at=at)


# ── PI-037: the history table must not drag every survey blob along ─────────

def test_slim_history_omits_the_blobs_but_keeps_invalid_reason(registry, client):
    _survey(registry, T1, {"schema_info": {"schemas": [{"name": "s"}]}})
    _survey(registry, T2, {"schema_info": {}}, source="egeria-published")
    registry.mark_database_survey_invalid(SLUG, T2, "egeria-published", "false-zero")
    full = client.get(f"/api/databases/{SLUG}/surveys?include_invalid=true").json()
    slim = client.get(f"/api/databases/{SLUG}/surveys?include_invalid=true&slim=true").json()
    assert all("survey_data" in r for r in full)
    assert [r["surveyed_at"] for r in slim] == [T2, T1]
    assert all("survey_data" not in r for r in slim)
    assert slim[0]["invalid_reason"] == "false-zero" and slim[0]["invalid_at"]


def test_slim_applies_to_the_default_valid_only_list_too(registry, client):
    _survey(registry, T1, {"schema_info": {}})
    rows = client.get(f"/api/databases/{SLUG}/surveys?slim=true").json()
    assert len(rows) == 1 and "survey_data" not in rows[0]


# ── PI-040: views ───────────────────────────────────────────────────────────

VIEW = {"schema": "sales", "name": "v_orders", "definition": "SELECT 1",
        "dependencies": ["sales.orders"],
        "complexity": {"complexity_score": 12, "portability_rating": 100, "node_count": 9,
                       "max_depth": 3, "join_count": 1, "cte_count": 0},
        "lineage": {"total": {"name": "total", "source": "sales.orders", "downstream": []}}}


def test_views_route_reads_the_latest_valid_surveys_views(registry, client):
    _survey(registry, T1, {"views": [VIEW]})
    body = client.get(f"/api/databases/{SLUG}/views").json()
    assert body["state"] == "measured"
    assert body["run"]["surveyed_at"] == T1 and body["run"]["source"] == "local"
    assert body["views"][0]["name"] == "v_orders"
    assert body["views"][0]["dependencies"] == ["sales.orders"]


def test_views_route_says_not_measured_when_the_survey_has_no_views_key(registry, client):
    _survey(registry, T1, {"schema_info": {}})
    body = client.get(f"/api/databases/{SLUG}/views").json()
    assert body["state"] == "not_measured" and body["views"] == []
    assert "sql_analysis" in body["reason"]


def test_views_route_never_surveyed_is_its_own_state(registry, client):
    body = client.get(f"/api/databases/{SLUG}/views").json()
    assert body["state"] == "never_surveyed" and body["run"] is None


def test_views_route_404s_an_unknown_database(client):
    assert client.get("/api/databases/nope/views").status_code == 404


def test_views_route_survives_an_unparseable_blob_and_says_so(registry, client):
    with registry._conn() as conn:
        conn.execute(
            """INSERT INTO database_surveys (database_slug, surveyed_at, egeria_report_guid,
               schema_count, table_count, column_count, survey_data, source)
               VALUES (?, ?, '', 1, 1, 1, '{not json', 'local')""", (SLUG, T1))
    body = client.get(f"/api/databases/{SLUG}/views").json()
    assert body["state"] == "not_measured" and "could not be read" in body["reason"]


# ── PI-032: the column row carries its default ──────────────────────────────

def test_schema_inventory_column_carries_default_value(registry):
    from resource_explorer.surveyors.database.survey_definition_adapter import schema_inventory_tree
    registry.write_detail_rows("database_tables", SLUG, T1, rows=[
        {"schema_name": "public", "table_name": "t", "table_type": "BASE TABLE",
         "row_count": 1, "size_bytes": 1, "state": "measured", "column_count": 2}])
    registry.write_detail_rows("database_columns", SLUG, T1, rows=[
        {"schema_name": "public", "table_name": "t", "column_name": "a", "ordinal_position": 1,
         "data_type": "integer", "base_type": "integer", "column_default": "nextval('t_a_seq')",
         "state": "measured"},
        {"schema_name": "public", "table_name": "t", "column_name": "b", "ordinal_position": 2,
         "data_type": "text", "base_type": "text", "column_default": "", "state": "measured"}])
    cols = {c["name"]: c for c in
            schema_inventory_tree(registry, SLUG)["schemas"][0]["tables"][0]["columns"]}
    assert cols["a"]["default"] == "nextval('t_a_seq')"
    assert cols["b"]["default"] == ""


# ── PI-036: ranked tables carry their own activity, or say it was not collected ──

def _tables(registry, at, rows):
    registry.write_detail_rows("database_tables", SLUG, at, rows=[
        {"schema_name": s, "table_name": t, "row_count": rc, "size_bytes": sz,
         "table_type": "BASE TABLE", "state": "measured"} for s, t, rc, sz in rows],
        coverage_section=SECTION_TABLES)


def test_ranked_rows_carry_last_analyzed_and_pending_from_the_activity_rows(registry, client):
    _survey(registry, T1, {"x": 1})
    _tables(registry, T1, [("s", "big", 500, 2_097_152), ("s", "small", 5, 1024)])
    registry.write_detail_rows("database_table_activity", SLUG, T1, rows=[
        {"schema_name": "s", "table_name": "big", "last_analyze": "2026-10-02T01:00:00",
         "last_autoanalyze": "", "pending_changes": 42, "state": "measured"}])
    body = client.get(f"/api/stats/databases/{SLUG}/table_sizes?measure=rows").json()
    ranked = {r["name"]: r for r in body["ranked"]}
    assert [r["name"] for r in body["ranked"]] == ["s.big", "s.small"]
    assert ranked["s.big"]["last_analyzed"] == "2026-10-02T01:00:00"
    assert ranked["s.big"]["pending_changes"] == 42
    assert ranked["s.big"]["activity_state"] == "measured"
    # no activity row for this table: absence is stated, never a zero or a date
    assert ranked["s.small"]["last_analyzed"] is None
    assert ranked["s.small"]["pending_changes"] is None
    assert ranked["s.small"]["activity_state"] == "not_collected"
    assert ranked["s.big"]["row_count"] == 500 and ranked["s.big"]["size_bytes"] == 2_097_152


def test_a_recorded_zero_pending_stays_zero(registry, client):
    _survey(registry, T1, {"x": 1})
    _tables(registry, T1, [("s", "t", 5, 1024)])
    registry.write_detail_rows("database_table_activity", SLUG, T1, rows=[
        {"schema_name": "s", "table_name": "t", "last_analyze": "", "last_autoanalyze": "2026-10-02T03:00:00",
         "pending_changes": 0, "state": "measured"}])
    r = client.get(f"/api/stats/databases/{SLUG}/table_sizes").json()["ranked"][0]
    assert r["pending_changes"] == 0 and r["last_analyzed"] == "2026-10-02T03:00:00"
