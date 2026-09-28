"""`_get_table_row_stats()` distinguishes a genuinely-measured zero from a
table `pg_stat_user_tables` has never actually tracked (2026-09-26).

Trigger: `coco_pharma`'s "How big is this database" answer dropped from
3,526 total rows (7 tables measured) to 0 (all zero, 53+ tables "measured")
after a fresh local survey. Traced live: every affected table showed
`n_live_tup = 0` with `last_analyze`/`last_autoanalyze` BOTH `NULL` —
`n_live_tup` is maintained by incremental DML tracking, but a plain SQL
dump/restore carries table DATA, not `pg_stat_user_tables`'s runtime
counters, so a freshly-restored table reads `n_live_tup = 0`
indistinguishably from one that is genuinely empty. Reported downstream as
"measured, 0 rows" — a confident wrong answer design §5.1a exists to
prevent, now caught in this reader specifically.

No live Postgres is used — `_get_table_row_stats()` is exercised against a
real `PostgreSQLConnection` with `execute_query` swapped for canned rows.
"""
from __future__ import annotations

from resource_explorer.surveyors.database.connection import PostgreSQLConnection


class _FakeRowStatsConnection(PostgreSQLConnection):
    def __init__(self, rows: list[dict]):
        super().__init__(host="localhost", port=5432, database="x", user="u", password="p")
        self._rows = rows

    def execute_query(self, query, params=()):
        return list(self._rows)


class TestNeverAnalyzedZeroIsNotAMeasurement:
    def test_zero_live_tuples_with_no_analyze_history_is_none(self):
        conn = _FakeRowStatsConnection([
            {"schemaname": "coco_ods", "tablename": "orders", "row_count": 0,
             "last_analyzed": None, "last_vacuumed": None, "pending_changes": 0},
        ])
        result = conn._get_table_row_stats()
        assert result[0]["row_count"] is None

    def test_zero_live_tuples_with_real_analyze_history_is_a_real_zero(self):
        """A table that HAS been analyzed and genuinely has zero rows is a
        real measurement, not an absence -- must not be swept into the same
        None bucket as a never-tracked table."""
        conn = _FakeRowStatsConnection([
            {"schemaname": "public", "tablename": "empty_staging", "row_count": 0,
             "last_analyzed": "2026-09-20 10:00:00+00", "last_vacuumed": None,
             "pending_changes": 0},
        ])
        result = conn._get_table_row_stats()
        assert result[0]["row_count"] == 0

    def test_a_nonzero_count_is_trusted_regardless_of_analyze_history(self):
        """n_live_tup is maintained by DML tracking independent of ANALYZE --
        a real nonzero count should never be discarded just because ANALYZE
        has never run."""
        conn = _FakeRowStatsConnection([
            {"schemaname": "coco_sus", "tablename": "unit_units", "row_count": 3195,
             "last_analyzed": None, "last_vacuumed": None, "pending_changes": 0},
        ])
        result = conn._get_table_row_stats()
        assert result[0]["row_count"] == 3195

    def test_mixed_tables_are_each_judged_independently(self):
        conn = _FakeRowStatsConnection([
            {"schemaname": "s", "tablename": "never_tracked", "row_count": 0,
             "last_analyzed": None, "last_vacuumed": None, "pending_changes": 0},
            {"schemaname": "s", "tablename": "real_zero", "row_count": 0,
             "last_analyzed": "2026-09-20 10:00:00+00", "last_vacuumed": None,
             "pending_changes": 0},
            {"schemaname": "s", "tablename": "real_nonzero", "row_count": 42,
             "last_analyzed": None, "last_vacuumed": None, "pending_changes": 0},
        ])
        result = conn._get_table_row_stats()
        by_name = {r["tablename"]: r["row_count"] for r in result}
        assert by_name == {"never_tracked": None, "real_zero": 0, "real_nonzero": 42}
