"""Q4 port step 8b (docs/design-notes/DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md): TestGen's frequency analysis of a
short text column, its child table, and the three QUERY types that compare a column's baseline text.

Default tier: nothing connects. The `RE_HASH_TEST_PG_DSN` tier runs the vendored statements on a real Postgres the caller
provides (a private throwaway cluster, never the shared registry).
"""
from __future__ import annotations

import os
import re

import psycopg2 as _psycopg2
import pytest
import sqlglot

from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import frequent_values as fv
from resource_explorer.surveyors.database.quality import query_types as qt
from resource_explorer.surveyors.database.quality import text_shape as ts
from resource_explorer.surveyors.database.quality.value_profile import ProfileBounds
from tests.test_quality_step_7_text_shape import PROFILE_WITH_TEXT, TextConn
from tests.test_value_profile_quality_checks import (  # noqa: F401
    SLUG,
    PgError,
    _by_key,
    _profile,
    _propose,
    _rows,
    entity,
    registry,
)


_REAL_CONNECT = _psycopg2.connect          # captured before the autouse guard below replaces it


@pytest.fixture(autouse=True)
def _no_live_database_or_egeria(monkeypatch):
    import psycopg2

    from resource_explorer.surveyors.database.quality import egeria_rules

    def refuse(*a, **k):
        raise AssertionError("a test reached a live service")

    monkeypatch.setattr(psycopg2, "connect", refuse)
    monkeypatch.setattr(egeria_rules, "egeria_gateway", refuse)


# ── the vendored statement ──────────────────────────────────────────────────────────────────────────────────────

def test_the_secondary_profiling_query_is_vendored_unchanged_and_pinned():
    assert fv.pin_matches()


def test_a_column_is_eligible_with_two_to_seventy_distinct_values_of_at_most_seventy_characters():
    base = dict(state="measured", general_type="A", distinct_value_ct=10, max_length=20)
    assert fv.eligible(base) and fv.eligible({**base, "distinct_value_ct": 2}) and fv.eligible({**base, "distinct_value_ct": 70})
    assert not fv.eligible({**base, "distinct_value_ct": 1}) and not fv.eligible({**base, "distinct_value_ct": 71})
    assert not fv.eligible({**base, "max_length": 71}) and not fv.eligible({**base, "general_type": "N"})
    assert not fv.eligible({**base, "state": "not_measured"}) and not fv.eligible({**base, "distinct_value_ct": None})


def test_the_statement_keeps_upstreams_sampling_block_only_when_sampling_and_quotes_its_names():
    sampled = fv.build_statement("s", 't"x', 'we"ird', "character(4)", ts.sampling_for(1000, percent=30, min_count=100))
    assert "TABLESAMPLE BERNOULLI (30.0) REPEATABLE (64)" in sampled and 'FROM "s"."t""x"' in sampled
    assert 'RTRIM("we""ird")' in sampled                       # a CHAR column is right-trimmed, as upstream
    whole = fv.build_statement("s", "t", "c", "text", None)
    assert "TABLESAMPLE" not in whole and "-- TG-" not in whole
    for sql in (sampled, whole):
        assert not re.search(r"\{(COL_VALUE|COL_NAME|DATA_SCHEMA|DATA_TABLE|SAMPLE_PERCENT_CALC)\}", sql)
        sqlglot.parse_one(sql, read="postgres")


def _raw(value, ct, rank, other=None, sample=100, digest="h"):
    return {"value": value, "value_ct": ct, "value_rank": rank, "other_distinct_ct": other, "sample_value_ct": sample,
            "distinct_value_hash": digest}


def test_the_rows_assemble_into_ranked_values_and_an_other_entry():
    rows = [_raw("a", 50, 1), _raw("b", 30, 2), _raw(None, 20, 11, other=4)]
    got = fv.assemble(rows, 100, False)
    assert got.values == (("a", 50), ("b", 30)) and got.other == (4, 20) and got.distinct_value_hash == "h"
    assert got.as_json() == {"values": [{"value": "a", "ct": 50}, {"value": "b", "ct": 30}],
                             "other": {"distinct_ct": 4, "ct": 20}}
    assert fv.assemble([], 100, False) is None                    # a column of blanks: nothing to keep


def test_on_a_sample_the_counts_are_lifted_and_the_other_distinct_count_is_not():
    rows = [_raw("a", 50, 1, sample=100), _raw(None, 20, 2, other=4, sample=100)]
    got = fv.assemble(rows, 1000, True)
    assert got.values == (("a", 500),) and got.other == (4, 200)


def test_the_baseline_text_is_upstreams_string_agg():
    assert fv.baseline_text({"values": [{"value": "red", "ct": 3}, {"value": "blue", "ct": 2}]}) == "red (3), blue (2)"
    assert fv.baseline_text(None) is None and fv.baseline_text({"values": []}) is None


# ── what the profile stores ─────────────────────────────────────────────────────────────────────────────────────

def _with_lengths(profile):
    """PROFILE_WITH_TEXT with profile 1b's length measures on the text column, which the frequency analysis' eligibility reads."""
    out = {**profile}
    spec = out["sales", "orders"]
    columns = [dict(c) for c in spec["columns"]]
    columns[1].update(max_length=12, min_length=3, avg_length=8.0)
    out["sales", "orders"] = {**spec, "columns": columns}
    return out


PROFILE_8B = _with_lengths(PROFILE_WITH_TEXT)


class FreqConn(TextConn):
    """A TextConn that also answers the frequency statement: `values` are (value, ct) most frequent first."""

    def __init__(self, *a, values=None, freq_fail=None, **kw):
        super().__init__(*a, **kw)
        self.values, self.freq_fail = values or [("red", 30), ("blue", 10)], freq_fail

    def execute_query_isolated(self, sql, params=()):
        if "ranked_vals" not in sql:
            return super().execute_query_isolated(sql, params)
        self.statements.append(sql)
        assert self.in_read_only, "the frequent values were read outside a read-only transaction"
        if self.freq_fail:
            raise self.freq_fail
        total = sum(c for _, c in self.values)
        return [{"value": v, "value_ct": c, "value_rank": i, "other_distinct_ct": None, "sample_value_ct": total,
                 "distinct_value_hash": "abc123"} for i, (v, c) in enumerate(self.values, start=1)]


def test_a_profile_stores_an_eligible_columns_frequent_values_in_the_child_table(registry, entity):
    conn = FreqConn(profile=PROFILE_8B)
    out, _ = _profile(registry, entity, conn=conn)
    note = _rows(registry, "database_value_profiles")["sales", "orders", "note"]       # 40 distinct values, text
    assert note["frequency_state"] == "read" and note["distinct_value_hash"] == "abc123"
    children = registry.query_detail_rows("database_value_profile_frequent_values", SLUG)
    assert [(r["column_name"], r["value_rank"], r["value"], r["value_ct"]) for r in children] == \
        [("note", 1, "red", 30), ("note", 2, "blue", 10)]
    assert sum("ranked_vals" in s for s in conn.statements) == 1            # only the eligible column
    assert out["value_profile"]["frequent_values_not_measured"] == []
    order_id = _rows(registry, "database_value_profiles")["sales", "orders", "order_id"]
    assert order_id["frequency_state"] is None and order_id["distinct_value_hash"] is None


def test_a_column_whose_frequency_analysis_failed_says_so_and_has_no_child_rows(registry, entity):
    conn = FreqConn(profile=PROFILE_8B, freq_fail=PgError("canceling statement due to statement timeout", "57014"))
    out, _ = _profile(registry, entity, conn=conn)
    note = _rows(registry, "database_value_profiles")["sales", "orders", "note"]
    assert note["frequency_state"] == "not_measured" and note["value_ct"] == 900
    assert registry.query_detail_rows("database_value_profile_frequent_values", SLUG) == []
    (gap,) = out["value_profile"]["frequent_values_not_measured"]
    assert gap["columns"] == ["note"] and "frequent values not measured" in gap["reason"]


def test_the_frequency_budget_is_its_own():
    found, gaps = fv.measure_table(FreqConn(profile=PROFILE_8B), "s", "t", [("a", "text", 5)], 10,
                                   ProfileBounds(1, frequency_budget_seconds=0))
    assert found == {} and gaps and "exceeded" in gaps[0][1] and ProfileBounds(1).frequency_budget_seconds == 300.0


# ── selection and defaults ──────────────────────────────────────────────────────────────────────────────────────

def _prow(**kw):
    base = dict(state="measured", general_type="A", value_ct=900, record_ct=1000, distinct_value_ct=40, max_length=10,
                sample_ratio=None, frequency_state="read", sample_value_ct=900, distinct_value_hash="abc",
                frequent_values={"values": [{"value": "red", "ct": 3}]}, frequent_patterns='{"values":[{"value":"aaa","ct":3}]}')
    base.update(kw)
    return base


@pytest.mark.parametrize("name", cat.STEP_8B_TEST_TYPES)
def test_the_three_are_vendored_query_types_with_pinned_selection(name):
    t = cat.load_catalogue()[name]
    assert t.run_type == "QUERY" and t.is_table_comparison
    assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(t.selection_criteria)


@pytest.mark.parametrize("name", cat.STEP_8B_TEST_TYPES)
def test_a_read_eligible_column_is_selected_and_a_sampled_or_non_text_one_is_a_real_no(name):
    rule = cat.PREDICATES[name].rule
    assert rule(_prow()) is True
    assert rule(_prow(sample_ratio=2.0)) is False and rule(_prow(general_type="N")) is False


def test_a_column_with_too_many_values_for_the_frequency_analysis_has_no_frequent_values_and_no_test():
    for name in ("Match_Frequent_Values", "Match_Distinct_Hash"):
        assert cat.PREDICATES[name].rule(_prow(distinct_value_ct=500, frequency_state=None, frequent_values=None)) is False


def test_a_column_that_should_have_been_read_but_was_not_is_not_established_never_a_silent_no():
    rule = cat.PREDICATES["Match_Frequent_Values"].rule
    assert rule(_prow(frequency_state=None, frequent_values=None)) is None
    assert rule(_prow(frequency_state="not_measured", frequent_values=None)) is None
    assert cat.PREDICATES["Match_Frequent_Patterns"].rule(_prow(sample_value_ct=None)) is None


def test_the_default_baselines_are_upstreams():
    values = cat.default_params(cat.load_catalogue()["Match_Frequent_Values"],
                                {"frequent_values": {"values": [{"value": "red", "ct": 3}, {"value": "blue", "ct": 2}]}}, 10)
    assert values["baseline_value"] == "red (3), blue (2)"
    patterns = cat.default_params(cat.load_catalogue()["Match_Frequent_Patterns"],
                                  {"frequent_patterns": '{"values":[{"value":"aaa","ct":3}]}'}, 10)
    assert patterns["baseline_value"] == "aaa (3)"
    assert cat.default_params(cat.load_catalogue()["Match_Distinct_Hash"], {"distinct_value_hash": "abc"}, 10)["baseline_value"] \
        == "abc"


def _watch(registry):
    from resource_explorer.surveyors.database.quality import table_comparison

    table_comparison.set_watch(registry, SLUG, "sales", "orders", True, by="steward")


def test_propose_reads_the_child_table(registry, entity):
    _watch(registry)
    _profile(registry, entity, conn=FreqConn(profile=PROFILE_8B))
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    assert rows["sales.orders.note::Match_Frequent_Values"]["state"] == "proposed"
    assert rows["sales.orders.note::Match_Frequent_Values"]["params_json"]["baseline_value"] == "red (30), blue (10)"
    assert rows["sales.orders.note::Match_Distinct_Hash"]["params_json"]["baseline_value"] == "abc123"
    assert rows["sales.orders.note::Match_Frequent_Patterns"]["state"] == "proposed"


# ── running a QUERY check ───────────────────────────────────────────────────────────────────────────────────────

def test_each_template_is_filled_and_parses_as_postgres():
    for name in cat.STEP_8B_TEST_TYPES:
        sql = qt.build_statement(name, "s", 't"x', 'we"ird', "character(4)", "it's (3)", "2026-10-29 00:00:00")
        assert "'it''s (3)'" in sql and '"s"."t""x"' in sql
        sqlglot.parse_one(sql, read="postgres")
    with pytest.raises(cat.ParameterError, match="at most"):
        qt.build_statement("Match_Distinct_Hash", "s", "t", "c", "text", "x" * 1001, "2026-10-29 00:00:00")


class QConn:
    def __init__(self, result=None, fail=None):
        self.result, self.fail, self.statements = result, fail, []

    def execute_query(self, sql, params=None):
        self.statements.append(sql)

    def execute_query_isolated(self, sql, params=None):
        self.statements.append(sql)
        if self.fail:
            raise self.fail
        return self.result

    def end_transaction(self):
        self.statements.append("<end>")


def _check(baseline="red (3)"):
    return {"row": {"measure": None, "measure_text": None}, "type": cat.load_catalogue()["Match_Frequent_Values"],
            "column": "kind", "db_type": "text", "baseline": baseline, "fail_status": "fail"}


@pytest.mark.parametrize("code,status", [(1, "pass"), (0, "fail"), (None, "not_established")])
def test_the_result_code_is_the_status(code, status):
    check = _check()
    qt.run_table(QConn([{"result_code": code, "result_signal": "red (3)", "result_message": "m"}]), "s", "t", check,
                 ProfileBounds(1))
    assert check["row"]["status"] == status and check["row"]["measure_text"] == "red (3)"


def test_a_cancelled_comparison_is_not_measured_never_a_pass():
    check = _check()
    qt.run_table(QConn(fail=PgError("canceling statement due to statement timeout", "57014")), "s", "t", check, ProfileBounds(1))
    assert check["row"]["status"] == "not_measured" and "exceeded" in check["row"]["reason"]
    spent = _check()
    qt.run_table(QConn(), "s", "t", spent, ProfileBounds(1, dupe_rows_budget_seconds=0))
    assert spent["row"]["status"] == "not_measured"


# ── the real-Postgres tier ──────────────────────────────────────────────────────────────────────────────────────

DSN = os.environ.get("RE_HASH_TEST_PG_DSN")


@pytest.mark.skipif(not DSN, reason="RE_HASH_TEST_PG_DSN (a private throwaway Postgres) is not set")
def test_the_vendored_statements_run_on_postgres_and_agree_with_each_other():
    import psycopg2
    import psycopg2.extras

    conn = _REAL_CONNECT(DSN)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS freq_t")
        cur.execute("CREATE TABLE freq_t (kind text)")
        cur.executemany("INSERT INTO freq_t VALUES (%s)", [(v,) for v in ["red"] * 5 + ["blue"] * 3 + ["green"] * 2 + [None, ""]])
        conn.commit()

        def run(sql):
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            c.execute(sql)
            out = c.fetchall()
            conn.rollback()
            return out

        got = fv.assemble(run(fv.build_statement("public", "freq_t", "kind", "text", None)), 10, False)
        assert got.values == (("red", 5), ("blue", 3), ("green", 2)) and got.other is None
        signal = run(qt.build_statement("Match_Frequent_Values", "public", "freq_t", "kind", "text", None, "2026-10-29"))[0]
        assert signal["result_signal"] == fv.baseline_text(got.as_json()) and signal["result_code"] is None
        matched = run(qt.build_statement("Match_Frequent_Values", "public", "freq_t", "kind", "text",
                                         "red (5), blue (3), green (2)", "2026-10-29"))[0]
        assert matched["result_code"] == 1
        digest = run(qt.build_statement("Match_Distinct_Hash", "public", "freq_t", "kind", "text", None, "2026-10-29"))[0]
        assert digest["result_signal"] == got.distinct_value_hash
        cur.execute("DROP TABLE freq_t")
        conn.commit()
    finally:
        conn.close()
