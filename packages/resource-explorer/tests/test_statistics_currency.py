"""S4b: statistics currency, derived once from stored rows.

The pure derivation is tested directly; the route and the ranked-tables read
are tested through a SQLite registry whose survey blobs are deliberately
useless, so only the structured rows can produce the answer.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import (
    DatabaseEntity, ProjectRegistry, SECTION_SCHEMAS, SECTION_TABLES, SECTION_TABLE_ACTIVITY,
)
from resource_explorer.statistics_currency import (
    CURRENT, NEVER, NEVER_RESET_UNKNOWN, NOT_COLLECTED, RESET, STALE, STALE_DAYS,
    derive_statistics_currency, table_currency,
)

AS_OF = "2026-10-09T10:00:00"


def act(**kw):
    return {"schema_name": "public", "table_name": "t", "last_analyze": "", "last_autoanalyze": "",
            "stats_reset": None, **kw}


class TestTableCurrency:
    def test_analysed_recently_is_current_and_ages_from_the_survey(self):
        r = table_currency(act(last_autoanalyze="2026-10-06T10:00:00+00:00"), AS_OF)
        assert (r["state"], r["age_days"]) == (CURRENT, 3)

    def test_the_later_of_manual_and_auto_analyse_counts(self):
        r = table_currency(act(last_analyze="2026-08-01T00:00:00", last_autoanalyze="2026-10-08T10:00:00"), AS_OF)
        assert (r["state"], r["age_days"]) == (CURRENT, 1)

    def test_older_than_the_threshold_is_stale(self):
        r = table_currency(act(last_analyze="2026-08-29T10:00:00"), AS_OF)
        assert (r["state"], r["age_days"]) == (STALE, 41)
        assert table_currency(act(last_analyze="2026-09-09T10:00:00"), AS_OF)["state"] == CURRENT   # exactly 30d

    def test_never_analysed_and_counters_never_reset_is_never(self):
        assert table_currency(act(stats_reset="never"), AS_OF)["state"] == NEVER

    def test_counters_reset_means_never_is_unknowable(self):
        r = table_currency(act(stats_reset="2026-10-02T08:00:00"), AS_OF)
        assert r["state"] == RESET and r["age_days"] is None

    def test_an_analyse_time_older_than_the_reset_is_reset_not_current(self):
        r = table_currency(act(last_analyze="2026-09-01T00:00:00", stats_reset="2026-10-02T08:00:00"), AS_OF)
        assert r["state"] == RESET

    def test_unread_reset_time_is_not_called_never(self):
        assert table_currency(act(stats_reset=None), AS_OF)["state"] == NEVER_RESET_UNKNOWN

    def test_no_activity_row_is_not_collected_never_never(self):
        assert table_currency(None, AS_OF)["state"] == NOT_COLLECTED

    def test_threshold_is_stated(self):
        assert STALE_DAYS == 30


TABLES = [
    {"schema_name": "sales", "table_name": "a", "table_type": "BASE TABLE"},
    {"schema_name": "sales", "table_name": "b", "table_type": "BASE TABLE"},
    {"schema_name": "sales", "table_name": "c", "table_type": "BASE TABLE"},
    {"schema_name": "sales", "table_name": "v", "table_type": "VIEW"},
    {"schema_name": "hr", "table_name": "d", "table_type": "BASE TABLE"},
]


class TestRollups:
    def derive(self, rows):
        return derive_statistics_currency(TABLES, rows, AS_OF, schemas=["sales", "hr", "scratch"])

    def rows(self):
        mk = lambda s, t, **kw: {"schema_name": s, "table_name": t, "last_analyze": "", "last_autoanalyze": "",
                                  "stats_reset": "2026-10-02T08:00:00", **kw}
        return [mk("sales", "a", last_analyze="2026-08-29T10:00:00", stats_reset="2026-08-01T00:00:00"),  # 41d
                mk("sales", "b", last_analyze="2026-10-08T10:00:00", stats_reset="2026-10-02T08:00:00"),
                mk("sales", "c"),                                          # reset
                mk("hr", "d", last_analyze="2026-10-09T09:00:00")]

    def test_schema_rollup_counts_and_oldest(self):
        d = self.derive(self.rows())
        sales = next(s for s in d["schemas"] if s["schema"] == "sales")
        assert (sales["total"], sales["stale"], sales["reset_tables"], sales["never"]) == (3, 1, 1, 0)
        assert sales["oldest_days"] == 41

    def test_views_are_not_tables_to_analyse(self):
        assert "sales.v" not in self.derive(self.rows())["tables"]

    def test_empty_schema_is_a_row_not_a_zero_of_zero(self):
        scratch = next(s for s in self.derive(self.rows())["schemas"] if s["schema"] == "scratch")
        assert scratch["empty"] and scratch["total"] == 0 and scratch["oldest_days"] is None

    def test_database_line_counts_stale_and_never_but_not_reset(self):
        db = self.derive(self.rows())["database"]
        assert (db["total"], db["behind"], db["reset_tables"]) == (4, 1, 1)
        assert db["counters_reset"] == {"state": "reset_at", "at": "2026-10-02T08:00:00"}   # the latest reset

    def test_unknown_reset_at_database_level(self):
        rows = [dict(r, stats_reset=None) for r in self.rows()]
        assert self.derive(rows)["database"]["counters_reset"]["state"] == "unknown"

    def test_never_reset_at_database_level(self):
        rows = [dict(r, stats_reset="never") for r in self.rows()]
        d = self.derive(rows)["database"]
        assert d["counters_reset"]["state"] == "never_reset" and d["never"] == 1


# ── the route's read ─────────────────────────────────────────────────────────

T1, T2 = "2026-10-01T10:00:00", "2026-10-09T10:00:00"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="db1", display_name="DB1", db_type="postgresql",
                                       host="localhost", port=5442, database_name="db1"))
    return r


def survey(registry, at):
    with registry._conn() as conn:
        conn.execute(
            "INSERT INTO database_surveys (database_slug, surveyed_at, egeria_report_guid, schema_count, "
            "table_count, column_count, survey_data, source) VALUES ('db1', ?, '', 9, 9, 9, ?, 'local')",
            (at, json.dumps({"unused": True})))


def load(registry, at, activity):
    registry.write_detail_rows("database_schemas", "db1", at, rows=[{"schema_name": "public"}],
                               coverage_section=SECTION_SCHEMAS)
    registry.write_detail_rows("database_tables", "db1", at, coverage_section=SECTION_TABLES, rows=[
        {"schema_name": "public", "table_name": n, "row_count": 5, "size_bytes": 1, "table_type": "BASE TABLE"}
        for n in ("a", "b")])
    if activity is not None:
        registry.write_detail_rows("database_table_activity", "db1", at, coverage_section=SECTION_TABLE_ACTIVITY,
                                   rows=[{"schema_name": "public", "table_name": n, **kw} for n, kw in activity])


class TestRead:
    def test_latest_and_history_from_stored_rows(self, registry):
        from resource_explorer import db_chart_data
        survey(registry, T1)
        load(registry, T1, [("a", {"last_analyze": "2026-09-30T10:00:00", "stats_reset": "never"}),
                            ("b", {"stats_reset": "never"})])
        survey(registry, T2)
        load(registry, T2, [("a", {"last_analyze": "2026-08-01T10:00:00", "stats_reset": "2026-10-05T00:00:00"}),
                            ("b", {"stats_reset": "2026-10-05T00:00:00"})])
        out = db_chart_data.statistics_currency(registry, "db1")
        assert out["state"] == "measured"
        d = out["currency"]["database"]
        assert (d["total"], d["reset_tables"], d["counters_reset"]["state"]) == (2, 2, "reset_at")
        first, last = out["history"]
        assert (first["never"], first["stale"]) == (1, 0)
        assert last["reset_tables"] == 2

    def test_a_survey_without_activity_is_unmeasured_not_zero(self, registry):
        from resource_explorer import db_chart_data
        survey(registry, T1)
        load(registry, T1, None)
        out = db_chart_data.statistics_currency(registry, "db1")
        assert out["state"] == "not_measured" and out["currency"] is None
        assert "no_table_activity" in out["reasons"]

    def test_an_earlier_run_without_activity_is_a_gap_in_history(self, registry):
        from resource_explorer import db_chart_data
        survey(registry, T1)
        load(registry, T1, None)
        survey(registry, T2)
        load(registry, T2, [("a", {"stats_reset": "never"}), ("b", {"stats_reset": "never"})])
        hist = db_chart_data.statistics_currency(registry, "db1")["history"]
        assert [p["measured"] for p in hist] == [False, True]
        assert "never" not in hist[0]

    def test_never_surveyed(self, registry):
        from resource_explorer import db_chart_data
        assert db_chart_data.statistics_currency(registry, "db1")["reasons"] == ["never_surveyed"]

    def test_ranked_tables_carry_the_same_derivation(self, registry):
        from resource_explorer import db_chart_data
        survey(registry, T2)
        load(registry, T2, [("a", {"last_analyze": "2026-10-06T10:00:00", "stats_reset": "never"})])
        ranked = db_chart_data.table_sizes(registry, "db1")["ranked"]
        by = {r["name"]: r["statistics"]["state"] for r in ranked}
        assert by == {"public.a": CURRENT, "public.b": NOT_COLLECTED}


class TestRound2:
    def test_partitioned_parent_is_labelled_and_in_no_count(self):
        tables = [{"schema_name": "p", "table_name": "parent", "table_type": "BASE TABLE", "relkind": "p"},
                  {"schema_name": "p", "table_name": "part1", "table_type": "BASE TABLE"}]
        rows = [{"schema_name": "p", "table_name": "part1", "last_analyze": "2026-10-08T10:00:00", "stats_reset": "never"}]
        d = derive_statistics_currency(tables, rows, AS_OF, schemas=["p"])
        assert d["tables"]["p.parent"]["state"] == "partitioned"
        assert d["database"]["total"] == 1 and d["database"]["never"] == 0 and d["database"]["partitioned"] == 1
        assert d["schemas"][0]["total"] == 1

    def test_materialised_views_are_analysed_views_and_foreign_tables_are_not(self):
        tables = [{"schema_name": "s", "table_name": n, "table_type": t} for n, t in
                  [("m", "MATERIALIZED VIEW"), ("v", "VIEW"), ("f", "FOREIGN"), ("b", "BASE TABLE"), ("u", "")]]
        d = derive_statistics_currency(tables, [], AS_OF)
        assert sorted(d["tables"]) == ["s.b", "s.m", "s.u"]

    def test_every_result_names_its_survey(self):
        assert table_currency(None, AS_OF)["as_of"] == AS_OF
        assert table_currency(act(stats_reset="never"), AS_OF)["as_of"] == AS_OF
