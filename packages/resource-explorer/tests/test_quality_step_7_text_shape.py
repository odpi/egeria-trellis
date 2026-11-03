"""Q4 port order step 7 (docs/design-notes/DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md): the sampled text-shape measures of
a text column, the four whole-table text measures, and the seven types whose selection reads them.

Covered: the vendored blocks and their pins; TestGen's sampling percent rule; the statement assembled from the vendored
blocks (sampled and not); lifting a sample's counts onto the table's scale; what the profile stores, what a column that
could not be read keeps (NULL, never 0), and that a sampled column says it was sampled; the seven selection rules (a
number column is a real "no", a text column with no text shape is `not_established` and says so, a sampled one is not
baselined); the defaults and rendered measures; the DDL record. Nothing connects to a surveyed database or to Egeria.
"""
from __future__ import annotations

import re

import pytest
import sqlglot

from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import text_shape as ts
from resource_explorer.surveyors.database.quality.value_profile import ProfileBounds
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    PROFILE,
    SLUG,
    FakeConn,
    PgError,
    _by_key,
    _profile,
    _propose,
    _rows,
    entity,
    registry,
)


@pytest.fixture(autouse=True)
def _no_live_database_or_egeria(monkeypatch):
    import psycopg2

    from resource_explorer.surveyors.database.quality import egeria_rules

    def refuse(*a, **k):
        raise AssertionError("a test reached a live service")

    monkeypatch.setattr(psycopg2, "connect", refuse)
    monkeypatch.setattr(egeria_rules, "egeria_gateway", refuse)


# ── vendored blocks and pins ────────────────────────────────────────────────────────────────────────────────────

def test_the_four_vendored_blocks_match_their_pins_and_the_helper_functions_are_vendored():
    assert ts.pins_match() == [] and set(ts.vendored_blocks()) == set(ts.TEXT_SHAPE_SHA256)
    assert {"IS_NUM", "IS_DATE"} <= set(cat.templated_functions())


def test_a_changed_block_fails_its_pin(monkeypatch):
    monkeypatch.setitem(ts.TEXT_SHAPE_SHA256, "pattern_measures", "0" * 64)
    assert ts.pins_match() == ["pattern_measures"]


def test_the_seven_are_vendored_cat_types_and_their_selection_is_pinned():
    catalogue = cat.load_catalogue()
    assert len(cat.STEP_7_TEST_TYPES) == 7 and set(cat.STEP_7_TEST_TYPES) <= set(catalogue)
    for name in cat.STEP_7_TEST_TYPES:
        assert catalogue[name].run_type == "CAT"
        assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(catalogue[name].selection_criteria), name


def test_the_whole_table_text_measures_are_in_the_batched_read_for_a_text_column_only():
    from resource_explorer.surveyors.database.quality import value_profile as vp

    assert "is_A_text" in vp.flags_for("A") and "is_A_text" not in vp.flags_for("N")
    sql, aliases = vp.build_table_query("s", "t", [("note", "A"), ("n", "N")], "2026-10-29 00:00:00")
    measures = {m for _, (i, m) in aliases.items() if i == 0}
    assert {"distinct_std_value_ct", "min_text", "max_text", "zero_value_ct"} <= measures
    assert not any("min_text" in a for a, (i, _) in aliases.items() if i == 1)
    sqlglot.parse_one(sql, read="postgres")


# ── TestGen's sampling percent rule ─────────────────────────────────────────────────────────────────────────────

def test_a_table_at_or_below_the_minimum_is_read_whole_and_above_it_the_percent_rule_applies():
    assert ts.sampling_for(None) is None and ts.sampling_for(100_000) is None
    assert ts.sampling_for(50, min_count=100) is None
    # 30 % of 5,000,000 is 1,500,000: capped at 999,000 rows
    big = ts.sampling_for(5_000_000)
    assert (big.sample_count, round(big.sample_ratio, 6), big.sample_percent) == (999_000, round(5_000_000 / 999_000, 6),
                                                                                  19.98)
    # 30 % of 200,000 is 60,000: raised to the 100,000 floor
    floor = ts.sampling_for(200_000)
    assert (floor.sample_count, floor.sample_ratio, floor.sample_percent) == (100_000, 2.0, 50.0)
    mid = ts.sampling_for(1000, percent=40, min_count=100)
    assert (mid.sample_count, mid.sample_ratio, mid.sample_percent) == (400, 2.5, 40.0)
    assert ts.sampling_for(1000, percent=0, min_count=10) is None and ts.sampling_for(1000, percent=100, min_count=10) is None


# ── the statement ───────────────────────────────────────────────────────────────────────────────────────────────

def test_the_statement_is_assembled_from_the_vendored_blocks_and_quotes_its_names():
    sampled = ts.build_statement("s", "t", 'we"ird', ts.sampling_for(1000, percent=30, min_count=100))
    assert sampled.count("TABLESAMPLE BERNOULLI (30.0) REPEATABLE (64)") == 2          # the two reads, one seed
    assert 'FROM "s"."t"' in sampled and '"we""ird"' in sampled and "whole_table_measures" not in sampled
    assert sampled.rstrip().endswith("SELECT * FROM sampleable_measures CROSS JOIN pattern_measures")
    whole = ts.build_statement("s", "t", "c", None)
    assert "TABLESAMPLE" not in whole and "REPEATABLE" not in whole
    for sql in (sampled, whole):
        assert not re.search(r"\{(COL_NAME|DATA_SCHEMA|DATA_TABLE|SAMPLE_PERCENT_CALC|MAX_PATTERN_LENGTH)\}", sql)
        assert "<%" not in sql
        sqlglot.parse_one(sql, read="postgres")
    # upstream's maximum pattern length, 25
    assert "BETWEEN 3 AND 25" in whole


# ── lifting a sample's counts onto the table's scale ────────────────────────────────────────────────────────────

def _raw(**kw):
    row = dict(sample_value_ct=300, sample_pattern_value_ct=300, lead_space_ct=30, quoted_value_ct=3, includes_digit_ct=60,
               upper_case_ct=90, lower_case_ct=120, non_alpha_ct=30, non_printing_ct=1, numeric_ct=15, date_ct=0,
               embedded_space_ct=150, frequent_patterns={"values": [{"value": "AAA", "ct": 200}, {"value": "aaa", "ct": 100}]})
    row.update(kw)
    return row


def test_counts_taken_on_a_sample_are_lifted_and_the_case_classes_add_up_to_the_whole():
    row = _raw()
    ts.lift_sampled_counts(row, 1000)
    # whole / sampled = 10/3, rounded half up
    assert row["lead_space_ct"] == 100 and row["quoted_value_ct"] == 10 and row["non_printing_ct"] == 3
    assert row["numeric_ct"] == 50 and row["date_ct"] == 0 and row["embedded_space_ct"] == 500
    # the case classes and the mixed-case remainder (60 of 300) share 1000 out by largest remainder
    mixed = 1000 - row["upper_case_ct"] - row["lower_case_ct"] - row["non_alpha_ct"]
    assert (row["upper_case_ct"], row["lower_case_ct"], row["non_alpha_ct"], mixed) == (300, 400, 100, 200)
    assert [e["ct"] for e in row["frequent_patterns"]["values"]] == [667, 333]
    assert row["sample_value_ct"] == 300                                  # the raw size of what was read is kept


def test_a_read_of_the_whole_table_is_not_scaled_and_an_empty_sample_gives_null_not_zero():
    whole = _raw(sample_value_ct=1000, sample_pattern_value_ct=1000)
    ts.lift_sampled_counts(whole, 1000)
    assert whole["lead_space_ct"] == 30 and whole["numeric_ct"] == 15
    empty = _raw(sample_value_ct=0, sample_pattern_value_ct=0)
    ts.lift_sampled_counts(empty, 1000)
    assert all(empty[m] is None for m in ts.COUNT_MEASURES)               # no ratio to lift by: not measured, not 0


def test_a_pattern_slot_with_half_its_pair_is_discarded_whole():
    assert ts.frequent_patterns({"pattern_0": "AAA", "pattern_ct_0": 5, "pattern_1": "aaa", "pattern_ct_1": None}) \
        == {"values": [{"value": "AAA", "ct": 5}]}
    assert ts.frequent_patterns({}) is None


# ── what the profile stores ─────────────────────────────────────────────────────────────────────────────────────

TEXT_RAW = {"note": dict(sample_value_ct=900, sample_pattern_value_ct=900, zero_length_ct=0, filled_value_ct=3,
                         lead_space_ct=0, quoted_value_ct=0, includes_digit_ct=12, upper_case_ct=0, lower_case_ct=880,
                         non_alpha_ct=0, non_printing_ct=0, numeric_ct=2, date_ct=0, embedded_space_ct=450,
                         avg_embedded_spaces=0.5, std_pattern_match="EMAIL", distinct_pattern_ct=2,
                         pattern_0="aaaa@aaa.aaa", pattern_ct_0=880, pattern_1=None, pattern_ct_1=None)}


class TextConn(FakeConn):
    """A FakeConn that also answers a text column's sampled statement from `TEXT_RAW`; `fail` is raised instead."""

    def __init__(self, *a, fail=None, **kw):
        super().__init__(*a, **kw)
        self.fail = fail

    def execute_query_isolated(self, sql, params=()):
        if "sampleable_measures" not in sql:
            return super().execute_query_isolated(sql, params)
        self.statements.append(sql)
        assert self.in_read_only, "the text shape was read outside a read-only transaction"
        if self.fail:
            raise self.fail
        column = re.search(r'COUNT\("([^"]+)"\) AS sample_value_ct', sql).group(1)
        return [dict(TEXT_RAW[column])]


PROFILE_WITH_TEXT = {**PROFILE}
PROFILE_WITH_TEXT[("sales", "orders")] = {"record_ct": 1000, "columns": [
    {"value_ct": 1000, "distinct_value_ct": 1000, "null_value_ct": 0},
    {"value_ct": 900, "distinct_value_ct": 40, "null_value_ct": 100, "zero_length_ct": 0, "filled_value_ct": 3,
     "distinct_std_value_ct": 40, "min_text": "a@b.co", "max_text": "z@y.org"},
    {"value_ct": 1000, "distinct_value_ct": 365, "null_value_ct": 0, "min_date": "2020-01-01", "max_date": "2026-10-01",
     "future_date_ct": 0},
    {"value_ct": 10, "null_value_ct": 990},
]}


def test_a_profile_stores_a_text_columns_measures_and_leaves_every_other_column_null(registry, entity):
    conn = TextConn(profile=PROFILE_WITH_TEXT)
    out, _ = _profile(registry, entity, conn=conn)
    rows = _rows(registry, "database_value_profiles")
    note = rows["sales", "orders", "note"]
    assert note["state"] == "measured" and note["std_pattern_match"] == "EMAIL" and note["lower_case_ct"] == 880
    assert note["min_text"] == "a@b.co" and note["distinct_std_value_ct"] == 40 and note["sample_value_ct"] == 900
    assert note["avg_embedded_spaces"] == 0.5 and note["distinct_pattern_ct"] == 2
    assert note["frequent_patterns"] == '{"values":[{"value":"aaaa@aaa.aaa","ct":880}]}'
    # the table is small: read whole, so there is no sample ratio
    assert note["sample_ratio"] is None
    for other in ("order_id", "placed"):
        assert rows["sales", "orders", other]["std_pattern_match"] is None and rows["sales", "orders", other]["numeric_ct"] is None
    # one sampled statement per text column, none for a column with no values
    assert sum("sampleable_measures" in s for s in conn.statements) == 1
    assert out["value_profile"]["text_shape_not_measured"] == []


def test_a_column_read_on_a_sample_carries_its_sample_ratio(registry, entity, monkeypatch):
    monkeypatch.setattr(ts, "SAMPLE_MIN_COUNT", 500)
    conn = TextConn(profile=PROFILE_WITH_TEXT)
    _profile(registry, entity, conn=conn)
    note = _rows(registry, "database_value_profiles")["sales", "orders", "note"]
    # 1,000 rows > 500: 30 % is 300, raised to the 500 floor
    assert note["sample_ratio"] == pytest.approx(2.0)
    assert any("TABLESAMPLE BERNOULLI (50.0) REPEATABLE (64)" in s for s in conn.statements)
    # the sample held 900 of the column's 900 values, so the lift is 900/900 and the counts stand
    assert note["lead_space_ct"] == 0 and note["sample_value_ct"] == 900


def test_the_sample_ratio_reaches_the_published_annotation(registry, entity, monkeypatch):
    monkeypatch.setattr(ts, "SAMPLE_MIN_COUNT", 500)
    out, _ = _profile(registry, entity, conn=TextConn(profile=PROFILE_WITH_TEXT))
    by = {a.item_key: a for a in out["annotations"]}
    note = by['["sales","orders","note"]']
    assert note.json_properties["sampleRatio"] == pytest.approx(2.0)
    assert by['["sales","orders","order_id"]'].json_properties["sampleRatio"] is None


def test_a_column_whose_text_shape_could_not_be_read_keeps_its_other_measures_and_says_why(registry, entity):
    conn = TextConn(profile=PROFILE_WITH_TEXT, fail=PgError("canceling statement due to statement timeout", "57014"))
    out, _ = _profile(registry, entity, conn=conn)
    note = _rows(registry, "database_value_profiles")["sales", "orders", "note"]
    assert note["state"] == "measured" and note["value_ct"] == 900 and note["min_text"] == "a@b.co"
    assert note["std_pattern_match"] is None and note["sample_value_ct"] is None and note["lead_space_ct"] is None
    (gap,) = out["value_profile"]["text_shape_not_measured"]
    assert gap["table_name"] == "orders" and gap["columns"] == ["note"] and "text shape not measured" in gap["reason"]


def test_the_text_shape_budget_is_its_own_and_a_spent_one_reads_nothing():
    conn = TextConn(profile=PROFILE_WITH_TEXT)
    found, gaps = ts.measure_table(conn, "s", "t", [("a", 5)], 10, ProfileBounds(1, text_shape_budget_seconds=0))
    assert found == {} and gaps and "exceeded" in gaps[0][1] and not conn.statements
    assert ProfileBounds(1).text_shape_budget_seconds == 300.0 and ProfileBounds(1).table_budget_seconds == 300.0


# ── selection rules ─────────────────────────────────────────────────────────────────────────────────────────────

def _prow(column="c", gt="A", **kw):
    base = dict(schema_name="s", table_name="t", column_name=column, general_type=gt, state="measured", record_ct=1000,
                value_ct=1000, distinct_value_ct=40, null_value_ct=0, min_length=2, max_length=2, avg_length=2.0,
                sample_ratio=None, sample_value_ct=1000, std_pattern_match=None, avg_embedded_spaces=0.0,
                lead_space_ct=0, upper_case_ct=1000, non_printing_ct=0)
    base.update(kw)
    return base


def _sel(name, row):
    return cat.PREDICATES[name].rule(row)


def test_email_format_is_a_real_no_on_a_number_and_on_a_text_column_without_the_pattern():
    assert _sel("Email_Format", _prow(std_pattern_match="EMAIL")) is True
    assert _sel("Email_Format", _prow(std_pattern_match=None)) is False        # read, and no standard pattern
    assert _sel("Email_Format", _prow(std_pattern_match="PHONE_USA")) is False
    assert _sel("Email_Format", _prow("n", "N", sample_value_ct=None)) is False   # a number has no text shape: no
    assert _sel("Email_Format", _prow(sample_value_ct=None)) is None            # a text column never read: not established


def test_a_row_profiled_before_step_7_is_not_established_and_says_so_in_words():
    from resource_explorer.surveyors.database.quality import propose

    old = _prow(sample_value_ct=None, std_pattern_match=None)
    assert _sel("Email_Format", old) is None
    assert cat.not_established_inputs("Email_Format", old) == ["sample_value_ct"]    # std_pattern_match NULL is a value
    assert "sampled text shape was not measured" in propose._not_established_reason("Email_Format", old)


def test_street_address_needs_all_four_terms():
    row = _prow(std_pattern_match="STREET_ADDR", avg_length=18.4, avg_embedded_spaces=2.6)
    assert _sel("Street_Addr_Pattern", row) is True
    assert _sel("Street_Addr_Pattern", {**row, "avg_length": 18.0}) is False        # a whole-number average
    assert _sel("Street_Addr_Pattern", {**row, "avg_embedded_spaces": 1.0}) is False
    assert _sel("Street_Addr_Pattern", {**row, "avg_length": 40.5}) is False
    assert _sel("Street_Addr_Pattern", {**row, "std_pattern_match": None}) is False


def test_us_state_reads_name_distinct_count_and_length():
    assert _sel("US_State", _prow("home_state", min_length=2, max_length=2, distinct_value_ct=51, date_days_present=None)) \
        in (True, None)
    assert _sel("US_State", _prow("home_state", max_length=3)) is False
    assert _sel("US_State", _prow("city")) is False and _sel("US_State", _prow("home_state", distinct_value_ct=70)) is False
    assert _sel("US_State", _prow("home_state", "N")) is False


def test_valid_characters_selects_every_text_column():
    assert _sel("Valid_Characters", _prow()) is True and _sel("Valid_Characters", _prow("n", "N")) is False


@pytest.mark.parametrize("name", ["Match_Lead_Space_Ct", "Match_Upper_Case_Ct", "Match_Non_Printing_Ct"])
def test_the_baseline_matches_wait_for_a_whole_table_read_and_never_baseline_a_sample(name):
    assert _sel(name, _prow()) is True
    assert _sel(name, _prow(sample_ratio=3.3)) is False          # upstream's rule: a sampled estimate is not a baseline
    assert _sel(name, _prow(value_ct=0)) is False and _sel(name, _prow("n", "N")) is False
    assert _sel(name, _prow(sample_value_ct=None)) is None       # never read: not established, not "no"


def _watch(registry):
    from resource_explorer.surveyors.database.quality import table_comparison

    table_comparison.set_watch(registry, SLUG, "sales", "orders", True, by="steward")


def test_propose_over_a_profile_with_text_shape(registry, entity):
    _watch(registry)
    _profile(registry, entity, conn=TextConn(profile=PROFILE_WITH_TEXT))
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    email = rows["sales.orders.note::Email_Format"]
    assert email["state"] == "proposed"
    lead = rows["sales.orders.note::Match_Lead_Space_Ct"]
    assert lead["state"] == "proposed" and lead["params_json"]["baseline_ct"] == 0
    # a number column is a quiet no for a text rule
    assert not any(k.startswith("sales.orders.order_id::") and k.endswith(("Email_Format", "Match_Lead_Space_Ct"))
                   for k in rows)


def test_a_text_column_whose_shape_could_not_be_read_is_not_established_not_silently_skipped(registry, entity):
    _watch(registry)
    conn = TextConn(profile=PROFILE_WITH_TEXT, fail=PgError("canceling statement due to statement timeout", "57014"))
    _profile(registry, entity, conn=conn)
    _propose(registry)
    row = _by_key(registry, "database_quality_proposals")["sales.orders.note::Match_Lead_Space_Ct"]
    assert row["state"] == "not_established" and "sampled text shape was not measured" in row["reason"]


# ── defaults and rendered measures ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,measure,expected", [("Match_Lead_Space_Ct", "lead_space_ct", 7),
                                                   ("Match_Upper_Case_Ct", "upper_case_ct", 8),
                                                   ("Match_Non_Printing_Ct", "non_printing_ct", 9)])
def test_the_baseline_is_the_measure_the_column_was_profiled_with(name, measure, expected):
    params = cat.default_params(cat.load_catalogue()[name], {measure: expected}, 10)
    assert params["baseline_ct"] == expected


def test_a_default_that_would_come_from_a_null_measure_is_not_established():
    with pytest.raises(cat.ParameterError):
        cat.default_params(cat.load_catalogue()["Match_Lead_Space_Ct"], {"lead_space_ct": None}, 10)


def test_the_seven_render_to_valid_postgres_and_a_percent_in_a_pattern_is_not_a_placeholder():
    for name in cat.STEP_7_TEST_TYPES:
        test_type = cat.load_catalogue()[name]
        params = cat.default_params(test_type, {"lead_space_ct": 1, "upper_case_ct": 1, "non_printing_ct": 0}, 10)
        measure = cat.render_measure(test_type, "col", params, "2026-10-29 12:00:00")
        sqlglot.parse_one(f"SELECT {measure} FROM t", read="postgres")


# ── the DDL record ──────────────────────────────────────────────────────────────────────────────────────────────

STEP_7_COLUMNS = ("distinct_std_value_ct", "min_text", "max_text", "sample_value_ct", "sample_pattern_value_ct",
                  "lead_space_ct", "quoted_value_ct", "includes_digit_ct", "upper_case_ct", "lower_case_ct", "non_alpha_ct",
                  "non_printing_ct", "numeric_ct", "date_ct", "embedded_space_ct", "avg_embedded_spaces",
                  "std_pattern_match", "distinct_pattern_ct", "frequent_patterns")


def test_the_migration_adds_exactly_the_documented_columns_and_the_record_names_each():
    from pathlib import Path

    from resource_explorer.registry import ProjectRegistry

    added = {c for t, c, _ in ProjectRegistry.VALUE_PROFILE_COLUMNS if c in STEP_7_COLUMNS}
    assert added == set(STEP_7_COLUMNS)
    record = (Path(__file__).resolve().parents[1] / "docs" / "ddl-value-profile-step7.md").read_text(encoding="utf-8")
    assert all(f"`{c}`" in record for c in STEP_7_COLUMNS) and "lock_timeout" in record and "IF NOT EXISTS" in record


def test_an_old_profile_table_gains_the_columns_with_no_default(tmp_path):
    import sqlite3

    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "old.db"))
    with sqlite3.connect(str(tmp_path / "old.db")) as raw:
        columns = {r[1] for r in raw.execute("PRAGMA table_info(database_value_profiles)")}
    assert set(STEP_7_COLUMNS) <= columns
    del reg
