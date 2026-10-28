"""Database Understanding chart routes, rebuilt on structured rows.

Each test fails against the pre-2026-10-01 routes (which read the survey_data
blob and turned "not measured" into zero) and passes against
`resource_explorer.db_chart_data`. Fixtures are SQLite with deliberately
useless blobs, so a route that reads the blob cannot pass.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import (
    DatabaseEntity,
    ProjectRegistry,
    SECTION_COLUMNS,
    SECTION_SCHEMAS,
    SECTION_TABLES,
    STATE_NOT_MEASURED,
    STATE_NOT_PERMITTED,
)

T1 = "2026-09-28T09:00:00"
T2 = "2026-09-28T14:02:00"       # same day as T1
T3 = "2026-09-29T08:00:00"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(
        slug="db1", display_name="DB1", db_type="postgresql",
        host="localhost", port=5442, database_name="db1"))
    r.register_database(DatabaseEntity(
        slug="never", display_name="Never", db_type="postgresql",
        host="localhost", port=5442, database_name="never"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(
            self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def survey(registry, at, *, source="local", blob=None, counts=(9, 9, 9)):
    """A survey row whose blob and summary counts are deliberately NOT the
    source of truth (counts 9/9/9 never match the structured rows)."""
    with registry._conn() as conn:
        conn.execute(
            """INSERT INTO database_surveys
               (database_slug, surveyed_at, egeria_report_guid, schema_count,
                table_count, column_count, survey_data, source)
               VALUES ('db1', ?, '', ?, ?, ?, ?, ?)""",
            (at, *counts, json.dumps(blob if blob is not None else {"unused": True}), source))


def tables(registry, at, rows, source="local"):
    registry.write_detail_rows(
        "database_tables", "db1", at, source=source,
        rows=[{"schema_name": s, "table_name": t, "row_count": rc,
               "size_bytes": sz, "table_type": ty}
              for (s, t, rc, sz, ty) in rows],
        coverage_section=SECTION_TABLES)


def columns(registry, at, rows, source="local"):
    registry.write_detail_rows(
        "database_columns", "db1", at, source=source,
        rows=[{"schema_name": s, "table_name": t, "column_name": c, "data_type": ty}
              for (s, t, c, ty) in rows],
        coverage_section=SECTION_COLUMNS)


def get(client, path):
    r = client.get(f"/api/stats/databases/db1/{path}")
    assert r.status_code == 200, r.text
    return r.json()


BT = "BASE TABLE"


# ── table_sizes ──────────────────────────────────────────────────────────────


class TestTableSizes:
    def test_null_row_count_is_not_ranked_as_zero_and_is_counted(self, registry, client):
        survey(registry, T1)
        tables(registry, T1, [
            ("public", "big", 1000, 2_097_152, BT),
            ("public", "small", 5, 1024, BT),
            ("public", "never_analyzed", None, 8192, BT),
            ("public", "also_never", None, None, BT),
        ])
        body = get(client, "table_sizes")
        assert body["tables"] == ["public.big", "public.small"]
        assert 0 not in body["row_counts"] and None not in body["row_counts"]
        assert body["not_established_count"] == 2
        assert body["not_established_tables"] == ["public.also_never", "public.never_analyzed"]
        assert body["state"] == "partial"
        assert "row_counts_not_established" in body["reasons"]

    def test_established_zero_is_still_zero(self, registry, client):
        """Known negative: a table that really has 0 rows is ranked, as 0."""
        survey(registry, T1)
        tables(registry, T1, [("public", "empty", 0, 0, BT), ("public", "a", 3, 0, BT)])
        body = get(client, "table_sizes")
        assert body["tables"] == ["public.a", "public.empty"]
        assert body["row_counts"] == [3, 0]
        assert body["not_established_count"] == 0
        assert body["state"] == "measured"

    def test_nothing_established_is_not_measured_not_all_zero(self, registry, client):
        survey(registry, T1)
        tables(registry, T1, [("public", "a", None, 100, BT), ("public", "b", None, 200, BT)])
        body = get(client, "table_sizes")
        assert body["state"] == "not_measured"
        assert body["tables"] == [] and body["not_established_count"] == 2
        # the size measure is a second, explicitly chosen view
        size = get(client, "table_sizes?measure=size")
        assert size["measure"] == "size" and size["tables"] == ["public.b", "public.a"]

    def test_views_are_not_reported_as_row_counts_not_established(self, registry, client):
        survey(registry, T1)
        tables(registry, T1, [("public", "t", 4, 1, BT), ("public", "v", None, None, "VIEW")])
        body = get(client, "table_sizes")
        assert body["not_established_count"] == 0 and body["views_excluded"] == 1
        assert body["state"] == "measured"

    def test_limit_truncation_is_reported_separately(self, registry, client):
        survey(registry, T1)
        tables(registry, T1, [("s", f"t{i}", i, 1, BT) for i in range(5)])
        body = get(client, "table_sizes?limit=2")
        assert len(body["tables"]) == 2 and body["truncated_by_limit"] == 3

    def test_names_its_run_and_does_not_read_the_blob(self, registry, client):
        survey(registry, T1, blob={"schema_info": {"schemas": [
            {"name": "x", "tables": [{"name": "ghost", "row_count": 10**9}]}]}})
        tables(registry, T1, [("public", "real", 7, 1, BT)])
        body = get(client, "table_sizes")
        assert body["tables"] == ["public.real"]
        assert body["run"]["surveyed_at"] == T1 and body["run"]["source"] == "local"
        assert body["run"]["run_id"] is not None
        assert body["figure"]["data"][0]["y"] == ["public.real"]

    def test_never_surveyed_is_not_measured_and_200(self, registry, client):
        r = client.get("/api/stats/databases/never/table_sizes")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "not_measured" and body["run"] is None
        assert body["tables"] == [] and body["figure"] is None

    def test_unknown_database_is_404(self, client):
        assert client.get("/api/stats/databases/nope/table_sizes").status_code == 404

    def test_run_without_structured_rows_is_not_zero(self, registry, client):
        survey(registry, T1, blob={"schema_info": {"schemas": [
            {"name": "x", "tables": [{"name": "t", "row_count": 5}]}]}})
        body = get(client, "table_sizes")
        assert body["state"] == "not_measured"
        assert body["reasons"] == ["not_materialized"]
        assert body["run"]["surveyed_at"] == T1

    def test_not_permitted_run_says_why(self, registry, client):
        survey(registry, T1)
        registry.write_detail_rows(
            "database_tables", "db1", T1, rows=[], coverage_section=SECTION_TABLES,
            coverage_state=STATE_NOT_PERMITTED, coverage_detail="no USAGE")
        body = get(client, "table_sizes")
        assert body["state"] == "not_measured" and body["reasons"] == ["not_permitted"]

    def test_credential_scope_partial_mark_is_carried(self, registry, client):
        cap = {"schema_total": 5, "schema_visible": 3, "relation_total": 26,
               "relation_select": 3, "connected_as": "re_user",
               "by_schema": {"public": {"usage_granted": True, "table_total": 3, "table_select": 3},
                             "ods": {"usage_granted": True, "table_total": 23, "table_select": 0}}}
        survey(registry, T1, blob={"credential_capability": cap})
        tables(registry, T1, [("public", "a", 1, 1, BT)])
        body = get(client, "table_sizes")
        assert body["state"] == "partial" and "credential_scope" in body["reasons"]
        assert body["scope"]["partial"] and body["scope"]["connected_as"] == "re_user"
        assert body["scope"]["by_container"]["ods"]["state"] == "structure_only"


# ── column_types ─────────────────────────────────────────────────────────────


class TestColumnTypes:
    def test_missing_type_is_not_a_bucket(self, registry, client):
        survey(registry, T1)
        columns(registry, T1, [("p", "t", "a", "integer"), ("p", "t", "b", "integer"),
                               ("p", "t", "c", ""), ("p", "t", "d", None)])
        body = get(client, "column_types")
        assert body["types"] == ["integer"] and body["counts"] == [2]
        assert "unknown" not in body["types"] and "" not in body["types"]
        assert body["type_not_recorded"] == 2
        assert body["state"] == "partial" and "type_not_recorded" in body["reasons"]
        assert body["run"]["surveyed_at"] == T1

    def test_a_real_type_literally_named_unknown_is_still_real(self, registry, client):
        """Known negative: the bucket is only wrong when it is invented."""
        survey(registry, T1)
        columns(registry, T1, [("p", "t", "a", "unknown")])
        body = get(client, "column_types")
        assert body["types"] == ["unknown"] and body["type_not_recorded"] == 0
        assert body["state"] == "measured"

    def test_columns_not_measured(self, registry, client):
        survey(registry, T1)
        assert get(client, "column_types")["state"] == "not_measured"

    def test_never_surveyed(self, client):
        body = client.get("/api/stats/databases/never/column_types").json()
        assert body["state"] == "not_measured" and body["types"] == []


# ── schema_distribution ──────────────────────────────────────────────────────


class TestSchemaDistribution:
    def test_counts_come_from_rows_and_columns_unmeasured_is_null(self, registry, client):
        survey(registry, T1)
        tables(registry, T1, [("a", "t1", 1, 1, BT), ("a", "t2", 1, 1, BT), ("b", "t3", 1, 1, BT)])
        body = get(client, "schema_distribution")
        assert body["schemas"] == ["a", "b"] and body["table_counts"] == [2, 1]
        assert body["column_counts"] == [None, None]       # not 0
        assert body["state"] == "partial"
        columns(registry, T1, [("a", "t1", "x", "int"), ("a", "t1", "y", "int")])
        body = get(client, "schema_distribution")
        assert body["column_counts"] == [2, 0] and body["state"] == "measured"
        assert body["run"]["surveyed_at"] == T1

    def test_never_surveyed(self, client):
        body = client.get("/api/stats/databases/never/schema_distribution").json()
        assert body["state"] == "not_measured" and body["schemas"] == []


# ── survey_history ───────────────────────────────────────────────────────────


class TestSurveyHistory:
    def test_two_runs_one_day_are_two_points(self, registry, client):
        survey(registry, T1)
        survey(registry, T2)
        tables(registry, T1, [("p", "a", 1, 1, BT)])
        tables(registry, T2, [("p", "a", 1, 1, BT), ("p", "b", 1, 1, BT)])
        body = get(client, "survey_history")
        assert body["dates"] == [T1, T2]
        assert body["table_counts"] == [1, 2]
        assert len(body["points"]) == 2
        assert body["points"][1]["run"]["surveyed_at"] == T2

    def test_run_without_table_measurement_is_a_gap_not_a_zero(self, registry, client):
        survey(registry, T1)
        survey(registry, T2)       # blob run: no structured rows, counts 9/9/9
        survey(registry, T3)
        tables(registry, T1, [("p", "a", 1, 1, BT)])
        tables(registry, T3, [("p", "a", 1, 1, BT)])
        body = get(client, "survey_history")
        assert body["table_counts"] == [1, None, 1]
        assert 0 not in body["table_counts"] and 9 not in body["table_counts"]
        assert body["points"][1]["states"]["tables"] == "not_materialized"
        assert body["state"] == "partial"
        assert "runs_without_table_measurement" in body["reasons"]

    def test_measured_empty_run_is_a_real_zero(self, registry, client):
        """Known negative: a run that measured and found no tables IS zero."""
        survey(registry, T1)
        registry.write_detail_rows("database_tables", "db1", T1, rows=[],
                                   coverage_section=SECTION_TABLES)
        registry.write_detail_rows("database_schemas", "db1", T1, rows=[],
                                   coverage_section=SECTION_SCHEMAS)
        body = get(client, "survey_history")
        assert body["table_counts"] == [0] and body["schema_counts"] == [0]
        assert body["state"] == "measured"

    def test_no_run_measured_tables(self, registry, client):
        survey(registry, T1)
        body = get(client, "survey_history")
        assert body["state"] == "not_measured"
        assert body["table_counts"] == [None] and body["figure"] is None

    def test_never_surveyed_is_not_measured_not_zeros(self, client):
        body = client.get("/api/stats/databases/never/survey_history").json()
        assert body["state"] == "not_measured" and body["run"] is None
        assert body["dates"] == [] and body["points"] == []

    def test_names_latest_run_and_points_at_the_diff_route(self, registry, client):
        survey(registry, T1)
        survey(registry, T3)
        tables(registry, T1, [("p", "a", 1, 1, BT)])
        tables(registry, T3, [("p", "a", 1, 1, BT)])
        body = get(client, "survey_history")
        assert body["run"]["surveyed_at"] == T3
        assert body["diff_route"] == "/api/databases/db1/diff"


# ── table_growth ─────────────────────────────────────────────────────────────


class TestTableGrowth:
    def test_rows_per_table_over_runs_with_gaps_not_zeros(self, registry, client):
        for at in (T1, T2, T3):
            survey(registry, at)
        tables(registry, T1, [("p", "a", 10, 1, BT), ("p", "b", None, 1, BT)])
        tables(registry, T2, [("p", "a", None, 1, BT), ("p", "b", None, 1, BT)])
        tables(registry, T3, [("p", "a", 30, 1, BT), ("p", "b", 5, 1, BT)])
        body = get(client, "table_growth")
        series = {s["table"]: s["row_counts"] for s in body["series"]}
        assert series["p.a"] == [10, None, 30]
        assert series["p.b"] == [None, None, 5]
        assert body["state"] == "partial"
        assert [r["surveyed_at"] for r in body["runs"]] == [T1, T2, T3]


# ── the diff route shares section_state and is unchanged ────────────────────


def test_diff_route_still_reports_not_materialized(registry, client):
    survey(registry, T1)
    survey(registry, T3)
    tables(registry, T3, [("p", "a", 1, 1, BT)])
    body = client.get("/api/databases/db1/diff").json()
    assert body["table_diff_state"] == "not_comparable"
    assert body["prev_table_state"] == "not_materialized"


# ── Classic consumer ─────────────────────────────────────────────────────────


def test_classic_asks_for_size_when_no_row_count_is_established():
    from pathlib import Path
    import resource_explorer
    html = (Path(resource_explorer.__file__).parent / "web/static/index.html").read_text()
    assert "table_sizes?measure=size" in html
    assert "not_established_count" in html
