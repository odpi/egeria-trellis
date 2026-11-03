"""Q4 port order step 3, profile 1b then B1b (docs/design-notes/DESIGN-QUALITY-PROFILE-1B.md): thirteen whole-table
measures in the value profile, the twelve test types that read them, `database_quality_results.measure_text`, and the
UTC session time zone for both reads.

Covered: the vendored entries and every pin (selection, defaults, conditions, the two numeric CASEs); upstream's
is_decimal / logical_type / exact-sum rules and the catalog read that feeds them (and what happens when it fails);
the measures the profile takes per general_type, stored exactly (sums as decimal text, floats as floats) and NOT
published; each selection rule in SQL's three-valued logic, including a profile from before 1b reading
not_established; the defaults; the run (NOT BETWEEN, exact decimals, Match_Sum's count, money's ::NUMERIC,
measure_text); proposals end to end (opt-in for Table Comparison, none needed for Daily_Record_Ct); and the migration.
Registries are a tmp SQLite file; no connection reaches a surveyed database (FakeConn); nothing touches Egeria.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import general_type as gt
from resource_explorer.surveyors.database.quality import table_comparison as tc
from resource_explorer.surveyors.database.quality import value_profile as vp
from resource_explorer.surveyors.database.quality.run_checks import (
    _select,
    build_quality_annotations,
    outcome_words,
)
from tests.test_value_profile_quality_checks import (
    INVENTORY_AT,
    PROFILE,
    PROFILED_AT,
    SLUG,
    FakeConn,
    _by_key,
    _profile,
    _propose,
    _rows,
    _run,
    entity,
    registry,
)

B1B = set(cat.B1B_TEST_TYPES)
TABLE_COMPARISON_B1B = B1B - {"Daily_Record_Ct"}


def _p(name, **kw):
    return cat.PREDICATES[name].evaluate(kw)


# ── the vendored entries and their pins ─────────────────────────────────────────────────────────────────────────

def test_the_twelve_are_vendored_and_match_epoch_sum_is_left_for_the_hashes():
    catalogue = cat.load_catalogue()
    assert B1B == {"Match_Min_Val", "Match_Max_Val", "Match_Sum", "Match_Stdev", "Match_Zero_Ct",
                   "Match_Fractional_Sum", "Match_Min_Length", "Match_Max_Length", "Match_Total_Length",
                   "Match_True_Ct", "Match_Distinct_Date_Ct", "Daily_Record_Ct"}
    # upstream takes epoch_sum only for a hashed column: Match_Epoch_Sum arrived with the hashes (port step 8)
    assert "Match_Epoch_Sum" in cat.STEP_8_TEST_TYPES and "Match_Epoch_Sum" not in cat.B1B_TEST_TYPES
    for name in TABLE_COMPARISON_B1B:
        t = catalogue[name]
        assert t.is_table_comparison and t.null_measure_fails and t.severity == "error", name
        assert cat.egeria_dimension(t.dimension) == "consistency", name
        assert not t.is_table_check, name
    daily = catalogue["Daily_Record_Ct"]
    assert not daily.is_table_comparison and daily.generation_sets == ("Standard",)   # proposed without an opt-in
    assert daily.severity == "warning" and cat.egeria_dimension(daily.dimension) == "completeness"
    assert {n for n in B1B if catalogue[n].measure_is_exact} == {"Match_Sum", "Match_Fractional_Sum"}
    assert {n for n in B1B if catalogue[n].test_operator == "NOT BETWEEN"} == {"Match_Sum", "Match_Stdev"}
    assert cat.MUST_BE["NOT BETWEEN"] == "mustBeBetween"


@pytest.mark.parametrize("name", sorted(B1B))
def test_each_selection_rule_is_pinned_to_its_vendored_fragment(name):
    t = cat.load_catalogue()[name]
    assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(t.selection_criteria)


@pytest.mark.parametrize("name", sorted(cat.CONDITION_PORTS))
def test_each_expression_condition_is_pinned_and_a_changed_one_is_refused(name):
    t = cat.load_catalogue()[name]
    sha, _ = cat.CONDITION_PORTS[name]
    assert sha == cat.sha256_text(t.test_condition)
    from dataclasses import replace
    changed = replace(t, test_condition=t.test_condition + " ")
    with pytest.raises(cat.ParameterError, match="test_condition changed"):
        cat.threshold(changed, {"baseline_value": 1, "baseline_sum": "1", "baseline_sd": 1, "upper_tolerance": 0})


@pytest.mark.parametrize("name", ["Match_Sum", "Match_Total_Length"])
def test_the_expression_defaults_are_pinned(name):
    t = cat.load_catalogue()[name]
    assert cat.DEFAULT_PORTS[name][0] == cat.sha256_text(t.default_parm_values)


def test_the_numeric_cases_are_pinned_and_ported_term_for_term():
    assert gt.numeric_cases_match_pins()
    # is_decimal: numeric -> COALESCE(scale, 1) > 0; money -> TRUE; else scale > 0 (NULL stays NULL)
    assert gt.is_decimal("numeric", 2) is True and gt.is_decimal("numeric", 0) is False
    assert gt.is_decimal("numeric", None) is True                  # an unconstrained numeric may hold fractions
    assert gt.is_decimal("money", None) is True
    assert gt.is_decimal("integer", 0) is False and gt.is_decimal("double precision", None) is None
    # logical_type 'integer': the integer types, or numeric with scale 0
    assert gt.logical_type_is_integer("bigint", 0) and gt.logical_type_is_integer("numeric", 0)
    assert not gt.logical_type_is_integer("numeric", 2) and not gt.logical_type_is_integer("real", None)
    # exact sum: integer or decimal; a double or real column is neither
    assert gt.exact_sum("N", "integer", 0) and gt.exact_sum("N", "numeric", 4) and gt.exact_sum("N", "money", None)
    assert not gt.exact_sum("N", "double precision", None) and not gt.exact_sum("A", "integer", 0)
    assert gt.is_n_decimal("N", "numeric", 2) and not gt.is_n_decimal("N", "integer", 0)


# ── the profile ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_flags_follow_upstream_and_money_is_read_through_numeric():
    assert vp.flags_for("N") == ["is_not_A", "is_distinct_countable", "is_type_N"]
    assert vp.flags_for(vp.ColumnChars("N", is_decimal=True, exact_sum=True)) == \
        ["is_not_A", "is_distinct_countable", "is_type_N", "is_N_decimal", "is_N_exact_sum"]
    assert vp.flags_for("B") == ["is_not_A", "is_distinct_countable", "is_type_B"]
    sql, aliases = vp.build_table_query("s", "t", [("amt", vp.ColumnChars("N", True, True, is_money=True))],
                                        "2026-10-28 00:00:00")
    assert 'MIN("amt"::NUMERIC) AS c0__min_value' in sql and 'MOD("amt"::NUMERIC, 1)' in sql
    assert 'SUM( 1 - ABS(SIGN("amt"::NUMERIC)) )::BIGINT AS c0__zero_value_ct' in sql
    assert {m for _, m in aliases.values()} >= {"fractional_sum", "sum_value", "stdev_value"}
    # A chunk of the widest columns stays under Postgres's 1664 target-list entries.
    widest = [(f"c{i}", vp.ColumnChars("N", True, True)) for i in range(vp.COLUMNS_PER_QUERY)]
    assert len(vp.build_table_query("s", "t", widest, "2026-10-28 00:00:00")[1]) + 1 < 1664


def test_every_new_statement_parses_as_postgres():
    import sqlglot

    cols = [("a", "A"), ("n", vp.ColumnChars("N", True, True)), ("m", vp.ColumnChars("N", True, True, True)),
            ("d", "D"), ("b", "B")]
    sqlglot.parse_one(vp.build_table_query("sales", "orders", cols, "2026-10-28 00:00:00")[0], read="postgres")
    sqlglot.parse_one(gt.NUMERIC_CHARS_QUERY, read="postgres")
    checks = []
    for name in sorted(B1B):
        t = cat.load_catalogue()[name]
        checks.append({"sql": cat.render_measure(t, "col", {}, "2026-10-28 00:00:00", is_money=name == "Match_Min_Val"),
                       "extra_sql": 'COUNT("col")' if name in cat.CONDITION_AGGREGATES else None})
    sqlglot.parse_one(_select("sales", "orders", checks), read="postgres")


#: A table with every 1b general type: a decimal, a double, a money, a text, a boolean and a date column.
NUMERIC = {("sales", "acct", "amount"): ("numeric", 2), ("sales", "acct", "score"): ("double precision", None),
           ("sales", "acct", "fee"): ("money", None)}
ACCT_COLUMNS = [("amount", "numeric"), ("score", "double precision"), ("fee", "money"), ("code", "text"),
                ("active", "boolean"), ("opened", "date")]
ACCT_PROFILE = {"record_ct": 40, "columns": [
    {"value_ct": 40, "distinct_value_ct": 38, "null_value_ct": 0, "zero_value_ct": 2, "min_value": Decimal("-5.25"),
     "max_value": Decimal("900.10"), "avg_value": 120.5, "stdev_value": 33.25, "fractional_sum": Decimal("7.35000"),
     "sum_value": Decimal("4820.00000000")},
    {"value_ct": 40, "distinct_value_ct": 40, "null_value_ct": 0, "zero_value_ct": 0, "min_value": 0.5,
     "max_value": 9.75, "avg_value": 4.0, "stdev_value": 2.0},
    {"value_ct": 40, "distinct_value_ct": 3, "null_value_ct": 0, "zero_value_ct": 0, "min_value": Decimal("1.00"),
     "max_value": Decimal("3.00"), "avg_value": 2.0, "stdev_value": 0.5, "fractional_sum": Decimal("0.00000"),
     "sum_value": Decimal("80.00000000")},
    {"value_ct": 36, "distinct_value_ct": 36, "null_value_ct": 4, "zero_length_ct": 1, "filled_value_ct": 0,
     "min_length": 3, "max_length": 12, "avg_length": 7.0},
    {"value_ct": 40, "distinct_value_ct": 2, "null_value_ct": 0, "boolean_true_ct": 31},
    {"value_ct": 40, "distinct_value_ct": 40, "null_value_ct": 0, "min_date": "2026-01-01",
     "max_date": "2026-02-09", "future_date_ct": 0, "before_20yr_date_ct": 0, "before_5yr_date_ct": 0,
     "date_days_present": 40},
]}


def _add_acct(registry):
    tables = registry.query_detail_rows("database_tables", SLUG)
    cols = registry.query_detail_rows("database_columns", SLUG)
    tables.append({"schema_name": "sales", "table_name": "acct", "table_type": "BASE TABLE", "row_count": 40})
    cols += [{"schema_name": "sales", "table_name": "acct", "column_name": c, "ordinal_position": i,
              "data_type": t, "base_type": t} for i, (c, t) in enumerate(ACCT_COLUMNS, start=1)]
    registry.write_detail_rows("database_tables", SLUG, "2026-10-20T09:30:00+00:00", rows=tables)
    registry.write_detail_rows("database_columns", SLUG, "2026-10-20T09:30:00+00:00", rows=cols)


def _profile_acct(registry, entity, **conn_kw):
    _add_acct(registry)
    conn = FakeConn(profile={**PROFILE, ("sales", "acct"): ACCT_PROFILE}, numeric=conn_kw.pop("numeric", NUMERIC),
                    **conn_kw)
    out, _ = _profile(registry, entity, conn)
    return out, conn, {c: r for (s, t, c), r in _rows(registry, "database_value_profiles").items() if t == "acct"}


def test_the_profile_takes_the_1b_measures_exactly_and_reads_the_scale_from_the_catalog(registry, entity):
    out, conn, rows = _profile_acct(registry, entity)
    assert out["value_profile"]["numeric_chars"] == "read"
    assert sum("FROM information_schema.columns" in s for s in conn.statements) == 1     # one read for the database
    amount, score, fee = rows["amount"], rows["score"], rows["fee"]
    assert amount["sum_value"] == "4820.00000000" and amount["fractional_sum"] == "7.35000"   # exact text
    assert (amount["min_value"], amount["max_value"]) == (-5.25, 900.1) and amount["zero_value_ct"] == 2
    assert isinstance(amount["avg_value"], float) and amount["stdev_value"] == 33.25
    # A double is neither decimal nor integer: upstream takes no fractional or exact sum for it.
    assert score["sum_value"] is None and score["fractional_sum"] is None and score["min_value"] == 0.5
    assert fee["sum_value"] == "80.00000000" and fee["fractional_sum"] == "0.00000"
    assert (rows["code"]["min_length"], rows["code"]["max_length"], rows["code"]["avg_length"]) == (3, 12, 7.0)
    assert rows["active"]["boolean_true_ct"] == 31
    assert (rows["opened"]["date_days_present"], rows["opened"]["before_5yr_date_ct"]) == (40, 0)
    # Each statement for acct ran in a read-only transaction with the session on UTC.
    for i, s in enumerate(conn.statements):
        if s.startswith("SELECT\n") and '"sales"."acct"' in s:
            assert conn.statements[i - 1] == "SET LOCAL TIME ZONE 'UTC'"
            assert 'MIN("fee"::NUMERIC) AS' in s and 'MIN("amount") AS' in s


def test_a_failed_scale_read_takes_no_exact_sums_and_says_so(registry, entity):
    from tests.test_value_profile_quality_checks import PgError

    out, _, rows = _profile_acct(registry, entity, numeric={}, numeric_error=PgError("permission denied"))
    assert out["value_profile"]["numeric_chars"] == "not_read"
    assert rows["amount"]["sum_value"] is None and rows["amount"]["fractional_sum"] is None   # NULL, never 0
    assert rows["amount"]["min_value"] == -5.25 and rows["amount"]["state"] == "measured"    # the rest is unaffected


def test_the_1b_measures_are_not_published(registry, entity):
    out, _, _ = _profile_acct(registry, entity)
    for ann in out["annotations"]:
        counts = getattr(ann, "value_count", None) or {}
        assert set(counts) <= set(vp.PUBLISHED_COUNTS), counts
    assert "before_5yr_date_ct" in vp.RELATIVE_TO_NOW_MEASURES


# ── selection, in SQL's three-valued logic ─────────────────────────────────────────────────────────────────────

N = {"general_type": "N", "value_ct": 10, "distinct_value_ct": 5, "min_value": -2.0, "max_value": 8.0,
     "avg_value": 3.0, "stdev_value": 1.5, "sum_value": "30.00000000", "fractional_sum": "0.00000", "state": "measured"}


@pytest.mark.parametrize("name", ["Match_Min_Val", "Match_Max_Val", "Match_Zero_Ct"])
def test_the_number_matches_need_a_number_with_values(name):
    assert _p(name, **N) is True
    assert _p(name, **{**N, "value_ct": 0}) is False
    assert _p(name, **{**N, "general_type": "A"}) is False
    assert _p(name, **{**N, "value_ct": None}) is None


def test_a_profile_from_before_1b_is_not_established_never_a_silent_no():
    old = {k: v for k, v in N.items() if k not in vp.PROFILE_1B_MEASURES}
    for name in ("Match_Min_Val", "Match_Sum", "Match_Stdev", "Match_Fractional_Sum"):
        assert _p(name, **old) is None, name
        assert "min_value" in cat.not_established_inputs(name, old), name
    old_date = {"general_type": "D", "value_ct": 4, "min_date": "2026-01-01", "max_date": "2026-01-04",
                "state": "measured"}
    assert _p("Match_Distinct_Date_Ct", **old_date) is None
    assert _p("Match_Min_Length", general_type="A", value_ct=3, state="measured") is None
    # A column that was not profiled at all is unknown too, not "no".
    assert _p("Match_Fractional_Sum", general_type="N", state="not_measured") is None


def test_match_sum_reads_greatest_ignoring_nulls_and_its_or_as_sql_does():
    assert _p("Match_Sum", **N) is True
    # no exact sum (a double column): selected only when the mean is not negligible beside the extremes
    assert _p("Match_Sum", **{**N, "sum_value": None, "avg_value": 1e-3, "max_value": 8.0}) is True
    assert _p("Match_Sum", **{**N, "sum_value": None, "avg_value": 1e-6}) is False
    assert _p("Match_Sum", **{**N, "max_value": 1e29, "value_ct": 100}) is False          # would overflow 1e30
    # GREATEST ignores a NULL argument, as Postgres does
    assert cat._greatest(None, 3) == 3 and cat._greatest(None, None) is None
    assert _p("Match_Sum", **{**N, "sum_value": None, "avg_value": None}) is None


def test_match_stdev_and_fractional_sum_read_their_measure_with_is_not_null():
    assert _p("Match_Stdev", **N) is True
    assert _p("Match_Stdev", **{**N, "value_ct": 1}) is False
    assert _p("Match_Stdev", **{**N, "distinct_value_ct": 1}) is False
    assert _p("Match_Stdev", **{**N, "stdev_value": None}) is False                       # profiled, undefined
    assert _p("Match_Fractional_Sum", **N) is True
    assert _p("Match_Fractional_Sum", **{**N, "fractional_sum": None}) is False           # not a decimal column


def test_the_length_and_true_count_matches():
    a = {"general_type": "A", "value_ct": 3, "min_length": 1, "sample_ratio": None, "state": "measured"}
    assert _p("Match_Min_Length", **a) is _p("Match_Max_Length", **a) is _p("Match_Total_Length", **a) is True
    assert _p("Match_Total_Length", **{**a, "sample_ratio": 0.2}) is False
    b = {"general_type": "B", "value_ct": 3, "boolean_true_ct": 0, "state": "measured"}
    assert _p("Match_True_Ct", **b) is True and _p("Match_True_Ct", **{**b, "value_ct": 0}) is False


def _daily(**kw):
    row = {"general_type": "D", "date_days_present": 40, "min_date": "2026-01-01", "max_date": "2026-02-09",
           "future_date_ct": 0, "value_ct": 400, "state": "measured"}
    row.update(kw)
    return _p("Daily_Record_Ct", **row)


def test_daily_record_ct_needs_every_day_of_the_span_present():
    assert _daily() is True                                        # 1 Jan to 9 Feb is 40 days, all 40 present
    assert _daily(date_days_present=39) is False                   # a gap
    assert _daily(date_days_present=21, max_date="2026-01-21") is False   # needs more than 21 days
    assert _daily(future_date_ct=301) is False                     # mostly future: a schedule, not a record
    assert _daily(min_date="2026-01-01 23:59:59", max_date="2026-02-09 00:00:01") is True   # whole calendar days
    assert _daily(date_days_present=None) is None


# ── defaults ────────────────────────────────────────────────────────────────────────────────────────────────────

def _defaults(name, profile):
    return cat.default_params(cat.load_catalogue()[name], profile, 10)


def test_each_default_comes_from_the_measure_upstream_names():
    row = {**N, "zero_value_ct": 2, "boolean_true_ct": 7, "min_length": 2, "max_length": 9, "date_days_present": 40}
    assert _defaults("Match_Min_Val", row)["baseline_value"] == -2.0
    assert _defaults("Match_Max_Val", row)["baseline_value"] == 8.0
    assert _defaults("Match_Zero_Ct", row)["baseline_ct"] == 2
    assert _defaults("Match_Stdev", row).items() >= {"baseline_sd": 1.5, "upper_tolerance": 1e-9}.items()
    assert _defaults("Match_Fractional_Sum", row)["baseline_sum"] == "0.00000"
    assert _defaults("Match_True_Ct", row)["baseline_ct"] == 7
    assert _defaults("Match_Min_Length", row)["baseline_value"] == 2
    assert _defaults("Match_Distinct_Date_Ct", row)["baseline_ct"] == 40
    assert _defaults("Daily_Record_Ct", row)["threshold_value"] == 0
    # Earlier types keep the params shape they were accepted with: no 1b keys appear on them.
    assert "baseline_sum" not in _defaults("Match_Null_Ct", {**row, "null_value_ct": 0})


@pytest.mark.parametrize("sum_value, avg, baseline, tolerance", [
    ("4820.00000000", 120.5, "4820", "0"),                        # RTRIM zeros, then the point
    ("0.00000000", 0.0, "0", "0"),
    ("-12.50000000", 1.0, "-12.5", "0"),
    (None, 120.5, repr(120.5 * 10), "0.000000001"),               # no exact sum: avg * value_ct, with a tolerance
])
def test_match_sum_defaults_keep_the_exact_sum_as_text(sum_value, avg, baseline, tolerance):
    got = _defaults("Match_Sum", {**N, "sum_value": sum_value, "avg_value": avg})
    assert (got["baseline_sum"], got["upper_tolerance"]) == (baseline, tolerance)


def test_match_total_length_is_the_mean_over_non_empty_values_times_their_count():
    row = {"general_type": "A", "value_ct": 10, "zero_length_ct": 2, "avg_length": 5.25}
    assert _defaults("Match_Total_Length", row)["baseline_sum"] == 42          # 5.25 * 8
    assert _defaults("Match_Total_Length", {**row, "avg_length": 2.5625})["baseline_sum"] == 20   # 20.5 to even
    with pytest.raises(cat.ParameterError):
        _defaults("Match_Total_Length", {**row, "value_ct": None})


# ── conditions and the run ──────────────────────────────────────────────────────────────────────────────────────

def test_match_sum_bounds_follow_upstream_s_numeric_arithmetic():
    exact = cat.SumTolerance(Decimal(4820), Decimal(0)).between(40)
    assert (exact.low, exact.high) == (Decimal("4820.00000000"), Decimal("4820.00000000"))
    loose = cat.SumTolerance(Decimal("1205.0"), Decimal("0.000000001")).between(10)
    # |1205 * 1e-9| + 1 * 10 * 5e-9 = 1.205e-6 + 5e-8 = 0.000001255 -> NUMERIC(38,8) 0.00000126 (half away from zero)
    assert (loose.low, loose.high) == (Decimal("1204.99999874"), Decimal("1205.00000126"))
    stdev = cat.threshold(cat.load_catalogue()["Match_Stdev"], {"baseline_sd": 2.0, "upper_tolerance": 0.5})
    assert (stdev.low, stdev.high) == (1.0, 3.0)
    frac = cat.threshold(cat.load_catalogue()["Match_Fractional_Sum"], {"baseline_sum": "7.349995"})
    assert frac == Decimal("7.35000")


@pytest.mark.parametrize("value", [None, "", "1; DROP TABLE x", "nan", True, [1]])
def test_an_exact_baseline_that_is_not_a_number_is_refused_quoting_it(value):
    with pytest.raises(cat.ParameterError, match="baseline_sum must be"):
        cat.threshold(cat.load_catalogue()["Match_Sum"], {"baseline_sum": value, "upper_tolerance": "0"})


def _accept(registry, key, **params):
    base = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None, "threshold_value": None,
            "severity": "error", "profile_surveyed_at": PROFILED_AT}
    base.update(params)
    registry.record_quality_verdict(SLUG, key, "accepted", base, decided_by="steward")


def _acct_measure(sql):
    return {
        'SUM(CAST("amount" AS NUMERIC(38,8)))': Decimal("4820.00000001"),
        'COUNT("amount")': 40,
        'SUM(CAST("fee" AS NUMERIC(38,8)))': Decimal("80.00000000"),
        'COUNT("fee")': 40,
        'STDDEV(CAST("score"::NUMERIC AS FLOAT))': 2.5,
        'SUM(CAST(ROUND(CAST(ABS(MOD("amount", 1)) AS NUMERIC(38,8)), 5) AS NUMERIC(38,5)))': Decimal("7.35000"),
        'CAST(CAST(MIN("fee"::NUMERIC) AS VARCHAR) AS DOUBLE PRECISION)': 1.0,
        'MAX(LENGTH(CAST("code" AS TEXT)))': 13,
        'SUM(CAST("active" AS INTEGER))': 31,
        "DATE(MAX(\"opened\")) - DATE(MIN(\"opened\"))+1-COUNT(DISTINCT CAST(\"opened\" AS DATE))": 0,
    }.get(sql)


def test_an_accepted_b1b_set_runs_exactly_in_one_select(registry, entity):
    _add_acct(registry)
    _accept(registry, "sales.acct.amount::Match_Sum", baseline_sum="4820", upper_tolerance="0")
    _accept(registry, "sales.acct.fee::Match_Sum", baseline_sum="80", upper_tolerance="0")
    _accept(registry, "sales.acct.score::Match_Stdev", baseline_sd=2.0, upper_tolerance=0.5)
    _accept(registry, "sales.acct.amount::Match_Fractional_Sum", baseline_sum="7.349995")
    _accept(registry, "sales.acct.fee::Match_Min_Val", baseline_value=1.0)
    _accept(registry, "sales.acct.code::Match_Max_Length", baseline_value=12)
    _accept(registry, "sales.acct.active::Match_True_Ct", baseline_ct=31)
    _accept(registry, "sales.acct.opened::Daily_Record_Ct", threshold_value=0, severity="warning")
    out, rows, conn = _run(registry, entity, {("sales", "acct"): {"record_ct": 40, "measure": _acct_measure}})
    assert {k: r["status"] for k, r in rows.items()} == {
        "sales.acct.amount::Match_Sum": "fail",              # one hundred-millionth off: an exact match fails
        "sales.acct.fee::Match_Sum": "pass",
        "sales.acct.score::Match_Stdev": "pass",              # 2.5 inside 1.0 .. 3.0
        "sales.acct.amount::Match_Fractional_Sum": "pass",    # ROUND(7.349995, 5) = 7.35000
        "sales.acct.fee::Match_Min_Val": "pass",
        "sales.acct.code::Match_Max_Length": "fail",
        "sales.acct.active::Match_True_Ct": "pass",
        "sales.acct.opened::Daily_Record_Ct": "pass"}
    assert sum(s.startswith("SELECT COUNT(*)") for s in conn.statements) == 1
    amount = rows["sales.acct.amount::Match_Sum"]
    assert amount["measure_text"] == "4820.00000001" and amount["measure"] == 4820.00000001
    assert amount["threshold_values_json"] == ["4820.00000000", "4820.00000000"]
    assert amount["comparison_operator"] == "mustBeBetween"
    assert amount["reason"] == "Column sum 4820.00000001 NOT BETWEEN 4820.00000000 AND 4820.00000000"
    assert outcome_words(amount, cat.load_catalogue()["Match_Sum"]) == "column sum 4820.00000001"
    [ann] = build_quality_annotations([amount])
    assert ann.additional_properties["measureText"] == "4820.00000001"
    assert "NOT BETWEEN 4820.00000000 AND 4820.00000000" in ann.quality_description
    assert rows["sales.acct.score::Match_Stdev"]["threshold_values_json"] == [1.0, 3.0]
    assert rows["sales.acct.score::Match_Stdev"]["measure_text"] is None          # a float needs no text
    assert outcome_words(rows["sales.acct.code::Match_Max_Length"],
                         cat.load_catalogue()["Match_Max_Length"]) == "maximum length 13"
    assert outcome_words(rows["sales.acct.active::Match_True_Ct"],
                         cat.load_catalogue()["Match_True_Ct"]) == "31 true values of 40"
    select = next(s for s in conn.statements if s.startswith("SELECT COUNT(*)"))
    assert 'CAST(CAST(MIN("fee"::NUMERIC) AS VARCHAR) AS DOUBLE PRECISION)' in select      # money, as upstream
    assert 'COUNT("amount") AS q' in select
    assert out["quality_checks"]["status_counts"] == {"pass": 6, "fail": 2}


def test_match_sum_with_no_count_back_is_an_error_not_a_pass(registry, entity):
    _add_acct(registry)
    _accept(registry, "sales.acct.amount::Match_Sum", baseline_sum="4820", upper_tolerance="0")
    measure = lambda sql: None if sql.startswith("COUNT(") else _acct_measure(sql)
    _, rows, _ = _run(registry, entity, {("sales", "acct"): {"record_ct": 40, "measure": measure}})
    row = rows["sales.acct.amount::Match_Sum"]
    assert row["status"] == "error" and row["threshold_values_json"] is None


# ── proposals end to end ────────────────────────────────────────────────────────────────────────────────────────

def test_an_opted_in_table_gets_b1b_and_daily_record_ct_needs_no_opt_in(registry, entity):
    _profile_acct(registry, entity)
    _propose(registry)
    before = {k: r for k, r in _by_key(registry, "database_quality_proposals").items() if r["test_type"] in B1B}
    assert {k for k, r in before.items() if r["state"] == "proposed"} == {"sales.acct.opened::Daily_Record_Ct"}
    tc.set_watch(registry, SLUG, "sales", "acct", True, by="steward")
    _propose(registry, at="2026-10-20T12:00:00+00:00")
    rows = {k: r for k, r in _by_key(registry, "database_quality_proposals").items() if r["test_type"] in B1B}
    proposed = {k: r["params_json"] for k, r in rows.items() if r["state"] == "proposed"}
    assert set(proposed) == {
        "sales.acct.amount::Match_Min_Val", "sales.acct.amount::Match_Max_Val", "sales.acct.amount::Match_Sum",
        "sales.acct.amount::Match_Stdev", "sales.acct.amount::Match_Zero_Ct", "sales.acct.amount::Match_Fractional_Sum",
        "sales.acct.score::Match_Min_Val", "sales.acct.score::Match_Max_Val", "sales.acct.score::Match_Sum",
        "sales.acct.score::Match_Stdev", "sales.acct.score::Match_Zero_Ct",
        "sales.acct.fee::Match_Min_Val", "sales.acct.fee::Match_Max_Val", "sales.acct.fee::Match_Sum",
        "sales.acct.fee::Match_Stdev", "sales.acct.fee::Match_Zero_Ct", "sales.acct.fee::Match_Fractional_Sum",
        "sales.acct.code::Match_Min_Length", "sales.acct.code::Match_Max_Length", "sales.acct.code::Match_Total_Length",
        "sales.acct.active::Match_True_Ct", "sales.acct.opened::Match_Distinct_Date_Ct",
        "sales.acct.opened::Daily_Record_Ct",
    }
    assert proposed["sales.acct.amount::Match_Sum"].items() >= {"baseline_sum": "4820", "upper_tolerance": "0"}.items()
    assert proposed["sales.acct.score::Match_Sum"]["upper_tolerance"] == "0.000000001"   # a double: no exact sum
    assert proposed["sales.acct.amount::Match_Fractional_Sum"]["baseline_sum"] == "7.35000"
    assert proposed["sales.acct.code::Match_Total_Length"]["baseline_sum"] == 245            # 7.0 * (36 - 1)
    assert proposed["sales.acct.opened::Match_Distinct_Date_Ct"]["baseline_ct"] == 40


def test_drift_names_the_new_baseline_keys(registry, entity):
    from resource_explorer.surveyors.database.quality.propose import _drift

    assert "baseline_sum 4820 -> 4821" in _drift({"baseline_sum": "4820"}, {"baseline_sum": "4821"})
    assert "baseline_sd 2.0 -> 2.5" in _drift({"baseline_sd": 2.0}, {"baseline_sd": 2.5})


# ── the migration ───────────────────────────────────────────────────────────────────────────────────────────────

#: Profile 1b's own columns: not Q6's before_20yr_date_ct, and not step 6's (the inference and the percentiles, which have
#: their own record, docs/ddl-value-profile-step6.md, and test, tests/test_quality_step_6_functional_type.py).
_STEP_6_COLUMNS = {"functional_data_type", "functional_table_type", "percentile_25", "percentile_50", "percentile_75"}
#: ... and not step 7's (the text-shape measures: docs/ddl-value-profile-step7.md, tests/test_quality_step_7_text_shape.py).
_STEP_7_COLUMNS = {"distinct_std_value_ct", "min_text", "max_text", "sample_value_ct", "sample_pattern_value_ct",
                   "lead_space_ct", "quoted_value_ct", "includes_digit_ct", "upper_case_ct", "lower_case_ct",
                   "non_alpha_ct", "non_printing_ct", "numeric_ct", "date_ct", "embedded_space_ct",
                   "avg_embedded_spaces", "std_pattern_match", "distinct_pattern_ct", "frequent_patterns"}
#: ... and not step 8's (value hashes: docs/ddl-value-profile-step8.md, tests/test_quality_step_8_hashes.py).
_STEP_8_COLUMNS = {"multiset_hash", "epoch_sum", "keyed_hash", "key_column_name", "hash_state", "distinct_value_hash",
                   "frequency_state"}
NEW_COLUMNS = {(t, c): sql_type for t, c, sql_type in ProjectRegistry.VALUE_PROFILE_COLUMNS
               if (t, c) != ("database_value_profiles", "before_20yr_date_ct")
               and c not in _STEP_6_COLUMNS | _STEP_7_COLUMNS | _STEP_8_COLUMNS}


def _decls(reg, table):
    with reg._conn() as conn:
        return {r["name"]: r["type"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_the_ddl_is_fourteen_nullable_columns_and_the_create_table_matches_it(tmp_path):
    assert len(NEW_COLUMNS) == 14
    assert {c for t, c in NEW_COLUMNS if t == "database_value_profiles"} == set(vp.PROFILE_1B_MEASURES)
    assert NEW_COLUMNS["database_quality_results", "measure_text"] == "TEXT"
    reg = ProjectRegistry(db_path=str(tmp_path / "fresh.db"))
    for (table, column), sql_type in NEW_COLUMNS.items():
        assert _decls(reg, table)[column] == sql_type, column
    assert _decls(ProjectRegistry(db_path=str(tmp_path / "fresh.db")), "database_value_profiles") == \
        _decls(reg, "database_value_profiles")                                   # a second init is a no-op


def test_an_existing_registry_gains_the_columns_and_old_rows_read_not_measured(tmp_path):
    path = tmp_path / "old.db"
    reg = ProjectRegistry(db_path=str(path))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="1b", db_type="postgresql", host="localhost",
                                         port=5442, database_name="x"))
    reg.write_detail_rows("database_value_profiles", SLUG, INVENTORY_AT, rows=[{
        "schema_name": "s", "table_name": "t", "column_name": "n", "general_type": "N", "record_ct": 5,
        "value_ct": 5, "state": "measured"}])
    con = sqlite3.connect(path)
    for table, column in NEW_COLUMNS:                                               # a pre-1b registry
        con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    con.commit()
    con.close()
    again = ProjectRegistry(db_path=str(path))                                       # the migration runs
    for (table, column), sql_type in NEW_COLUMNS.items():
        assert _decls(again, table)[column] == sql_type, column
    (row,) = again.query_detail_rows("database_value_profiles", SLUG)
    assert row["value_ct"] == 5 and all(row[m] is None for m in vp.PROFILE_1B_MEASURES)   # NULL, never 0
    # and the old row proposes nothing by accident: its B1b rules are not_established
    assert _p("Match_Min_Val", **row) is None


#: information_schema's word for each declared type.
_PG_TYPE = {"BIGINT": "bigint", "DOUBLE PRECISION": "double precision", "TEXT": "text"}


def test_the_migration_adds_nullable_columns_on_postgres_and_is_idempotent(pg_registry, monkeypatch):
    """The Postgres tier (skipped when no pgvector Postgres is reachable): on the throwaway test schema only, the
    fourteen columns are dropped, re-added by a fresh init with their declared types, nullable and with no default,
    and a second init changes nothing."""
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url                         # the throwaway schema only
    with pg_registry._conn() as conn:
        for table, column in NEW_COLUMNS:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    monkeypatch.setattr(PR, "_pg_schema_ready", set())                                  # forget the per-process init
    again = PR(database_url=pg_registry.database_url)

    def described():
        with again._conn() as conn:
            rows = conn.execute(
                "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name IN ('database_value_profiles', 'database_quality_results')").fetchall()
        return {(r["table_name"], r["column_name"]): (r["data_type"], r["is_nullable"], r["column_default"])
                for r in rows if (r["table_name"], r["column_name"]) in NEW_COLUMNS}

    before = described()
    assert before == {key: (_PG_TYPE[sql_type], "YES", None) for key, sql_type in NEW_COLUMNS.items()}
    monkeypatch.setattr(PR, "_pg_schema_ready", set())
    PR(database_url=pg_registry.database_url)
    assert described() == before


# ── with Q5: a B1b check published as a DataQualityRule ────────────────────────────────────────────────────────

def test_a_b1b_rule_publishes_the_baseline_it_runs_with_and_an_egeria_edit_goes_back_into_it():
    from resource_explorer.surveyors.database.quality.egeria_rules import rule_properties
    from resource_explorer.surveyors.database.quality.run_checks import apply_rule

    catalogue = cat.load_catalogue()
    stdev = catalogue["Match_Stdev"]
    params = {"baseline_sd": 2.0, "upper_tolerance": 0.5, "severity": "error"}
    props = rule_properties("sales.acct.score::Match_Stdev", stdev, params)
    assert props["comparisonOperator"] == "mustBeBetween" and props["thresholdValues"] == ["2.0"]
    assert props["additionalProperties"]["upperTolerance"] == "0.5"
    msum = rule_properties("sales.acct.amount::Match_Sum", catalogue["Match_Sum"],
                           {"baseline_sum": "4820", "upper_tolerance": "0", "severity": "error"})
    assert msum["thresholdValues"] == ["4820"]                       # the baseline, never a run-dependent bound
    # A non-B1b type publishes what it did before Q5 and 1b met: the condition's own value.
    assert rule_properties("s.t.c::Match_Null_Ct", catalogue["Match_Null_Ct"],
                           {"baseline_ct": 7, "severity": "error"})["thresholdValues"] == ["7"]
    # Egeria wins: an edited threshold goes back into the baseline the condition is built around.
    row: dict = {}
    merged, refused = apply_rule({"source": "egeria", "properties": {"thresholdValues": ["2.4"],
                                                                     "comparisonOperator": "mustBeBetween"}},
                                 stdev, params, row)
    assert refused == "" and merged["baseline_sd"] == "2.4" and merged["upper_tolerance"] == 0.5
    assert cat.threshold(stdev, merged) == cat.Between(2.4 - 1.2, 2.4 + 1.2)
    assert {cat.threshold_key(catalogue[n]) for n in ("Match_Min_Val", "Match_Max_Val")} == {"baseline_value"}
    assert cat.threshold_key(catalogue["Match_Fractional_Sum"]) == "baseline_sum"
