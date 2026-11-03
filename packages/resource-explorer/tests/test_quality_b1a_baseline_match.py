"""Q4 port order step 2, B1a (docs/design-notes/DESIGN-QUALITY-B1A-BASELINE-MATCH.md): the six Table Comparison
types whose baseline is a measure the value profile already takes, proposed only for a table a person opted in, and
the TestGen-only import (Q4 §6.5, assigned here by §7 question 6).

Covered: the vendored entries and their pins (one is a generation template, not a fragment); each selection rule
in SQL's three-valued logic; the defaults, including upstream's TO_CHAR date baseline; the opt-in (stored with no new
table, append-only, latest wins, refused for a table not in the inventory) and that nothing is proposed without it;
drift against the accepted baseline; the run (counts, the text date comparison and how its measure is stored,
upstream's null_measure_fails); the import (only ported types, verdicts win, RE's own proposal wins, parameters
validated, carried forward by the next propose run, accepted and then run); and the route a table's card calls.
Registries are a tmp SQLite file; no connection reaches a surveyed database (FakeConn); nothing touches Egeria.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from resource_explorer import quality_review as review
from resource_explorer.auth import create_access_token
from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import table_comparison as tc
from resource_explorer.surveyors.database.quality import testgen_import as tg
from resource_explorer.surveyors.database.quality.run_checks import (
    build_quality_annotations,
    outcome_words,
)
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    PROFILE,
    PROFILED_AT,
    SLUG,
    FakeConn,
    _by_key,
    _profile,
    _propose,
    _run,
    entity,
    registry,
)

B1A = set(cat.B1A_TEST_TYPES)


def _p(name, **kw):
    return cat.PREDICATES[name].evaluate(kw)


# ── the vendored entries and their pins ─────────────────────────────────────────────────────────────────────────

def test_the_six_are_vendored_as_upstream_s_table_comparison_set():
    catalogue = cat.load_catalogue()
    assert B1A == {"Match_Row_Ct", "Match_Distinct_Ct", "Match_Null_Ct", "Match_Zero_Length_Ct", "Match_Min_Date",
                   "Match_Max_Date"}
    for name in B1A:
        t = catalogue[name]
        assert t.is_table_comparison and t.generation_sets == (cat.TABLE_COMPARISON,), name
        assert t.null_measure_fails and t.origin == "vendored" and t.severity == "error", name
        assert cat.egeria_dimension(t.dimension) == "consistency", name
        assert t.test_operator == "<>", name
    assert [n for n in B1A if catalogue[n].is_table_check] == ["Match_Row_Ct"]
    assert {n for n in B1A if catalogue[n].measure_is_text} == {"Match_Min_Date", "Match_Max_Date"}
    # The slice-1 and Q6 types are in no generation set: they stay proposed everywhere, as before.
    assert not any(catalogue[n].is_table_comparison for n in cat.SLICE_1_TEST_TYPES + cat.Q6_TEST_TYPES)


def test_match_row_ct_is_pinned_to_its_vendored_generation_template():
    t = cat.load_catalogue()["Match_Row_Ct"]
    assert (t.selection_criteria, t.generation_template) == ("TEMPLATE", "gen_Match_Row_Ct.sql")
    text = cat.selection_source(t)
    assert "s.record_ct             AS threshold_value" in text
    assert cat.PREDICATES["Match_Row_Ct"].fragment_sha256 == cat.sha256_text(text)
    assert ":Match_Row_Ct:generation_template:sha256:" in cat.catalogue_ref(t)


@pytest.mark.parametrize("name", ["Match_Min_Date", "Match_Max_Date"])
def test_the_date_baselines_are_pinned_to_upstream_s_to_char(name):
    t = cat.load_catalogue()[name]
    assert "TO_CHAR(" in t.default_parm_values
    sha, key, _ = cat.DEFAULT_PORTS[name]
    assert (sha, key) == (cat.sha256_text(t.default_parm_values), "baseline_value")


# ── selection, in SQL's three-valued logic ─────────────────────────────────────────────────────────────────────

def test_match_row_ct_selects_every_profiled_table_as_its_template_does():
    assert _p("Match_Row_Ct", record_ct=0) is True          # no condition upstream, an empty table included
    assert _p("Match_Row_Ct") is True                       # its default then reads NULL: not_established


@pytest.mark.parametrize("kw, expected", [
    ({"record_ct": 10, "general_type": "A"}, True),
    ({"record_ct": 10, "general_type": "X"}, False),         # no equality: not distinct-countable
    ({"record_ct": 0, "general_type": "N"}, False),
    ({"record_ct": None, "general_type": "N"}, None),
])
def test_match_distinct_ct(kw, expected):
    assert _p("Match_Distinct_Ct", **kw) is expected


@pytest.mark.parametrize("kw, expected", [
    ({"record_ct": 10, "general_type": "X", "state": "measured"}, True),   # query_error IS NULL: selected
    ({"record_ct": 10, "general_type": "X", "state": "not_measured"}, False),
    ({"record_ct": 10, "general_type": "A", "state": "not_measured"}, True),
    ({"record_ct": 0, "general_type": "A", "state": "measured"}, False),
    ({"record_ct": None, "general_type": "A", "state": "measured"}, None),
])
def test_match_null_ct_reads_a_row_that_is_not_measured_as_upstream_s_query_error(kw, expected):
    assert _p("Match_Null_Ct", **kw) is expected


@pytest.mark.parametrize("kw, expected", [
    ({"general_type": "A", "value_ct": 5, "sample_ratio": None}, True),
    ({"general_type": "A", "value_ct": 5, "sample_ratio": 0.3}, False),     # sampled: TestGen leaves it out
    ({"general_type": "A", "value_ct": 0, "sample_ratio": None}, False),
    ({"general_type": "N", "value_ct": 5, "sample_ratio": None}, False),
    ({"general_type": "A", "value_ct": None, "sample_ratio": None}, None),
])
def test_match_zero_length_ct(kw, expected):
    assert _p("Match_Zero_Length_Ct", **kw) is expected


@pytest.mark.parametrize("name", ["Match_Min_Date", "Match_Max_Date"])
def test_the_date_matches_need_a_date_column_with_values(name):
    assert _p(name, general_type="D", value_ct=3) is True
    assert _p(name, general_type="D", value_ct=0) is False
    assert _p(name, general_type="A", value_ct=3) is False
    assert _p(name, general_type="D", value_ct=None) is None


# ── defaults and validation ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, text", [
    ("2020-01-01", "2020-01-01 00:00:00"),                                  # a date column: its midnight
    ("2024-03-07 10:11:12.987654", "2024-03-07 10:11:12"),                  # TO_CHAR drops fractions, no rounding
    ("2024-03-07 23:59:59+05:30", "2024-03-07 23:59:59"),                   # the wall clock, not converted
    (None, None),
])
def test_to_char_seconds_is_upstream_s_text_of_a_stored_profile_date(stored, text):
    assert cat.to_char_seconds(stored) == text


def test_each_default_comes_from_the_profile_measure_upstream_names():
    catalogue = cat.load_catalogue()
    prof = {"record_ct": 100, "value_ct": 90, "distinct_value_ct": 40, "null_value_ct": 10, "zero_length_ct": 2,
            "min_date": "2020-01-02 03:04:05", "max_date": "2024-05-06"}
    got = {n: cat.default_params(catalogue[n], prof, 10) for n in B1A}
    assert got["Match_Row_Ct"]["threshold_value"] == 100
    assert got["Match_Distinct_Ct"]["baseline_unique_ct"] == 40
    assert got["Match_Null_Ct"]["baseline_ct"] == 10
    assert got["Match_Zero_Length_Ct"]["baseline_ct"] == 2
    assert got["Match_Min_Date"]["baseline_value"] == "2020-01-02 03:04:05"
    assert got["Match_Max_Date"]["baseline_value"] == "2024-05-06 00:00:00"
    assert {cat.threshold(catalogue[n], got[n]) for n in ("Match_Null_Ct", "Match_Zero_Length_Ct")} == {10, 2}
    with pytest.raises(cat.ParameterError, match="NULL in the profile"):
        cat.default_params(catalogue["Match_Row_Ct"], {"record_ct": None}, 10)


@pytest.mark.parametrize("value", ["2020-01-01", "2020-13-01 00:00:00", "2020-01-01 00:00:00'; DROP TABLE x; --",
                                   20200101, None])
def test_a_date_baseline_that_is_not_to_char_text_is_refused_quoting_it(value):
    t = cat.load_catalogue()["Match_Max_Date"]
    with pytest.raises(cat.ParameterError, match="YYYY-MM-DD HH:MM:SS"):
        cat.threshold(t, {"baseline_value": value})


def test_the_measures_render_verbatim_and_no_baseline_is_ever_put_into_sql():
    catalogue = cat.load_catalogue()
    params = {"baseline_ct": 5, "baseline_unique_ct": 7, "baseline_value": "2020-01-01 00:00:00", "threshold_value": 9}
    sql = {n: cat.render_measure(catalogue[n], "c", params, "2026-01-01 00:00:00") for n in B1A}
    assert sql["Match_Row_Ct"] == "COUNT(*)"
    assert sql["Match_Distinct_Ct"] == 'COUNT(DISTINCT "c")'
    assert sql["Match_Max_Date"] == """TO_CHAR(CAST(MAX("c") AS TIMESTAMP), 'YYYY-MM-DD HH24:MI:SS')"""
    assert not any("2020-01-01" in s or " 7" in s for s in sql.values())   # RE compares in Python


def test_a_text_measure_is_stored_as_seconds_and_read_back_as_its_text():
    n = cat.text_measure_number("2024-03-07 10:11:12")
    assert isinstance(n, float) and cat.text_measure_from_number(n) == "2024-03-07 10:11:12"
    assert cat.text_measure_number("not a date") is None


# ── the opt-in ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_nothing_is_proposed_for_a_table_nobody_opted_in(registry, entity):
    _profile(registry, entity)
    out = _propose(registry)["quality_proposals"]
    assert not any(r["test_type"] in B1A for r in registry.query_detail_rows("database_quality_proposals", SLUG))
    assert out["not_opted_in_ct"] > 0 and out["tables_opted_in"] == 0
    assert out["proposed_ct"] == 15         # slice 1's set, Dupe_Rows and Schema_Drift on the two profiled tables, Unique_Pct on two columns, Valid_Characters on the text one


def test_an_opt_in_is_append_only_latest_wins_and_refused_for_a_table_not_in_the_inventory(registry):
    tc.set_watch(registry, SLUG, "sales", "orders", True, by="steward", note="nightly load")
    assert set(tc.watched_tables(registry, SLUG)) == {("sales", "orders")}
    tc.set_watch(registry, SLUG, "sales", "orders", False, by="owner")
    assert tc.watched_tables(registry, SLUG) == {}
    trail = tc.history(registry, SLUG)[("sales", "orders")]
    assert [(e["action"], e["by"], e["note"]) for e in trail] == [("watch", "steward", "nightly load"),
                                                                  ("unwatch", "owner", "")]
    with pytest.raises(ValueError, match="not a table in the stored inventory"):
        tc.set_watch(registry, SLUG, "sales", "nope", True, by="steward")
    with pytest.raises(ValueError, match="person"):
        tc.set_watch(registry, SLUG, "sales", "orders", True, by="")
    # Nothing new in the schema: the opt-in lives in app_settings.
    assert len(registry.list_settings_with_prefix(f"quality_table_comparison::{SLUG}::")) == 2


def test_an_unreadable_entry_is_skipped_never_taken_as_an_opt_in(registry):
    registry.add_setting_once(f"quality_table_comparison::{SLUG}::00000000000000000001-x", "{not json")
    registry.add_setting_once(f"quality_table_comparison::{SLUG}::00000000000000000002-x",
                              '{"schema": "sales", "table": "orders", "action": "watch"}')     # no person
    assert tc.watched_tables(registry, SLUG) == {}


def _watch_orders(registry, entity):
    _profile(registry, entity)
    tc.set_watch(registry, SLUG, "sales", "orders", True, by="steward")
    out = _propose(registry)["quality_proposals"]
    return out, {k: r for k, r in _by_key(registry, "database_quality_proposals").items() if r["test_type"] in B1A}


def test_an_opted_in_table_gets_the_six_with_baselines_from_its_profile(registry, entity):
    out, rows = _watch_orders(registry, entity)
    proposed = {k: r["params_json"] for k, r in rows.items() if r["state"] == "proposed"}
    assert set(proposed) == {
        "sales.orders::Match_Row_Ct",
        "sales.orders.order_id::Match_Distinct_Ct", "sales.orders.order_id::Match_Null_Ct",
        "sales.orders.note::Match_Distinct_Ct", "sales.orders.note::Match_Null_Ct",
        "sales.orders.note::Match_Zero_Length_Ct",
        "sales.orders.placed::Match_Distinct_Ct", "sales.orders.placed::Match_Null_Ct",
        "sales.orders.placed::Match_Min_Date", "sales.orders.placed::Match_Max_Date",
        "sales.orders.payload::Match_Null_Ct",                     # jsonb (X): nulls yes, distinct no
    }
    assert proposed["sales.orders::Match_Row_Ct"]["threshold_value"] == 1000
    assert proposed["sales.orders.note::Match_Distinct_Ct"]["baseline_unique_ct"] == 40
    assert proposed["sales.orders.note::Match_Null_Ct"]["baseline_ct"] == 100
    assert proposed["sales.orders.payload::Match_Null_Ct"]["baseline_ct"] == 990
    assert proposed["sales.orders.note::Match_Zero_Length_Ct"]["baseline_ct"] == 0
    assert proposed["sales.orders.placed::Match_Min_Date"]["baseline_value"] == "2020-01-01 00:00:00"
    assert proposed["sales.orders.placed::Match_Max_Date"]["baseline_value"] == "2026-10-01 00:00:00"
    assert all(p["profile_surveyed_at"] == PROFILED_AT for p in proposed.values())
    assert all(r["origin"] == "vendored" for r in rows.values())
    # Only the opted-in table: the empty table and the others get none.
    assert {r["table_name"] for r in rows.values()} == {"orders"}
    assert out["tables_opted_in"] == 1


def test_accepted_checks_survive_an_opt_out_and_report_drift(registry, entity):
    _, rows = _watch_orders(registry, entity)
    row_ct = rows["sales.orders::Match_Row_Ct"]
    registry.record_quality_verdict(SLUG, row_ct["check_key"], "accepted", row_ct["params_json"],
                                    proposal_id=row_ct["id"], decided_by="steward")
    tc.set_watch(registry, SLUG, "sales", "orders", False, by="steward")
    grown = {**PROFILE, ("sales", "orders"): {**PROFILE["sales", "orders"], "record_ct": 1200}}
    _profile(registry, entity, FakeConn(profile=grown), at="2026-10-21T10:00:00+00:00")
    _propose(registry, at="2026-10-21T11:00:00+00:00")
    after = {k: r for k, r in _by_key(registry, "database_quality_proposals").items() if r["test_type"] in B1A}
    assert set(after) == {"sales.orders::Match_Row_Ct"}            # the rest went with the opt-out
    assert after["sales.orders::Match_Row_Ct"]["state"] == "accepted"
    assert "threshold_value 1000 -> 1200" in after["sales.orders::Match_Row_Ct"]["reason"]


# ── the run ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _accept(registry, key, **params):
    base = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None, "threshold_value": None,
            "severity": "error", "profile_surveyed_at": PROFILED_AT}
    base.update(params)
    registry.record_quality_verdict(SLUG, key, "accepted", base, decided_by="steward")


def _b1a_measure(sql):
    return {
        "COUNT(*)": 1000,
        'COUNT(DISTINCT "note")': 41,
        'SUM(CASE WHEN "note" IS NULL THEN 1 ELSE 0 END)': 100,
        """SUM(CASE WHEN "note" = '' THEN 1 ELSE 0 END)""": None,       # as Postgres answers an all-NULL SUM
        """TO_CHAR(CAST(MAX("placed") AS TIMESTAMP), 'YYYY-MM-DD HH24:MI:SS')""": "2026-10-02 00:00:00",
    }.get(sql) if not sql.startswith("TO_CHAR(CAST(CASE") else "2020-01-01 00:00:00"


def test_an_accepted_b1a_set_runs_in_one_select_and_fails_on_any_move(registry, entity):
    _accept(registry, "sales.orders::Match_Row_Ct", threshold_value=1000)
    _accept(registry, "sales.orders.note::Match_Distinct_Ct", baseline_unique_ct=40)
    _accept(registry, "sales.orders.note::Match_Null_Ct", baseline_ct=100)
    _accept(registry, "sales.orders.note::Match_Zero_Length_Ct", baseline_ct=0)
    _accept(registry, "sales.orders.placed::Match_Min_Date", baseline_value="2020-01-01 00:00:00")
    _accept(registry, "sales.orders.placed::Match_Max_Date", baseline_value="2026-10-01 00:00:00")
    out, rows, conn = _run(registry, entity, {("sales", "orders"): {"record_ct": 1000, "measure": _b1a_measure}})
    assert {k.split("::")[1]: r["status"] for k, r in rows.items()} == {
        "Match_Row_Ct": "pass", "Match_Distinct_Ct": "fail", "Match_Null_Ct": "pass",
        "Match_Zero_Length_Ct": "fail",                     # NULL measure, null_measure_fails: fail, as upstream
        "Match_Min_Date": "pass", "Match_Max_Date": "fail"}
    assert sum(s.startswith("SELECT COUNT(*)") for s in conn.statements) == 1
    distinct = rows["sales.orders.note::Match_Distinct_Ct"]
    assert (distinct["measure"], distinct["threshold_values_json"], distinct["comparison_operator"]) == \
        (41, [40], "mustBe")
    zero = rows["sales.orders.note::Match_Zero_Length_Ct"]
    assert zero["measure"] is None and "null_measure_fails" in zero["reason"] and zero["state"] == "measured"
    latest = rows["sales.orders.placed::Match_Max_Date"]
    assert latest["threshold_values_json"] == ["2026-10-01 00:00:00"]
    assert latest["measure"] == cat.text_measure_number("2026-10-02 00:00:00")
    assert "Latest date 2026-10-02 00:00:00 <> 2026-10-01 00:00:00" == latest["reason"]
    t = cat.load_catalogue()["Match_Max_Date"]
    assert outcome_words(latest, t) == "latest date 2026-10-02 00:00:00"
    [ann] = [a for a in build_quality_annotations([latest]) if True]
    assert ann.additional_properties["measureText"] == "2026-10-02 00:00:00"
    assert outcome_words(rows["sales.orders::Match_Row_Ct"], cat.load_catalogue()["Match_Row_Ct"]) == "row count 1000"
    assert out["quality_checks"]["status_counts"] == {"pass": 3, "fail": 3}


def test_a_date_baseline_that_does_not_validate_is_not_established_and_never_run(registry, entity):
    _accept(registry, "sales.orders.placed::Match_Max_Date", baseline_value="2026-10-01'; DROP TABLE x; --")
    _, rows, conn = _run(registry, entity, {("sales", "orders"): {"record_ct": 1000, "measure": _b1a_measure}})
    row = rows["sales.orders.placed::Match_Max_Date"]
    assert row["status"] == "not_established" and "DROP TABLE" in row["reason"]
    assert not any(s.startswith("SELECT COUNT(*)") for s in conn.statements)


# ── the TestGen-only import (§6.5) ───────────────────────────────────────────────────────────────────────────────

def _test(test_type, table="orders", column="", **kw):
    d = {"test_type": test_type, "schema_name": "sales", "table_name": table, "column_name": column or None,
         "severity": None, "test_active": "Y", "baseline_ct": None, "baseline_unique_ct": None,
         "baseline_value": None, "baseline_value_ct": None, "threshold_value": None}
    d.update(kw)
    d["check_key"] = cat.check_key("sales", table, column, test_type)
    return d


EXPORT = {
    "exported_at": "2026-10-27T08:00:00+00:00", "testgen_version": "5.134.0",
    "projects": [{"project_code": "COCO", "database": "coco_ods", "re_slug": SLUG, "state": "exported",
                  "table_groups": [{
                      "table_group": "sales", "schema": "sales", "profile_run_id": "p-1",
                      "profiled_at": "2026-10-20T09:00:00", "profile_state": "exported",
                      "profile": [{"schema_name": "sales", "table_name": "orders", "column_name": "note",
                                   "general_type": "A"}],
                      "test_suite": "sales-suite", "test_run_id": "r-9", "tested_at": "2026-10-20T09:10:00",
                      "tests_state": "exported",
                      "tests": [
                          _test("Match_Null_Ct", column="note", baseline_ct="100", id="td-1"),
                          _test("Match_Row_Ct", threshold_value="1000", severity="Warning", id="td-2"),
                          _test("Unique", column="order_id"),                        # RE proposes it too
                          _test("Match_Max_Date", column="placed", baseline_value="10/01/2026"),
                          _test("Table_Freshness"), _test("Table_Freshness", table="big"),   # not ported in RE
                          _test("Match_Null_Ct", column="order_id", test_active="N", baseline_ct="0"),
                          _test("Match_Distinct_Ct", column="note", baseline_unique_ct="40"),
                      ]}]}],
}


def _proposals(registry):
    return _by_key(registry, "database_quality_proposals")


def test_the_import_proposes_testgen_only_ported_tests_and_says_what_it_left_out(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    registry.record_quality_verdict(SLUG, "sales.orders.note::Match_Distinct_Ct", "rejected", {},
                                    decided_by="steward")
    before = _proposals(registry)
    out = tg.import_export(registry, SLUG, EXPORT, surveyed_at="2026-10-27T09:00:00+00:00")
    assert (out["imported_ct"], out["not_established_ct"], out["matched_re_ct"], out["rejected_ct"],
            out["inactive_ct"], out["unported"]) == (2, 1, 1, 1, 1, {"Table_Freshness": 2})
    rows = _proposals(registry)
    assert set(rows) - set(before) == {"sales.orders.note::Match_Null_Ct", "sales.orders::Match_Row_Ct",
                                       "sales.orders.placed::Match_Max_Date"}
    assert rows["sales.orders.order_id::Unique"]["origin"] == "vendored"       # RE's own row stands
    null = rows["sales.orders.note::Match_Null_Ct"]
    assert (null["origin"], null["state"], null["general_type"]) == ("testgen", "proposed", "A")
    assert null["params_json"]["baseline_ct"] == 100 and null["params_json"]["severity"] == "error"
    assert "test definition td-1" in null["reason"] and "test run r-9" in null["reason"]
    assert ":Match_Null_Ct:selection_criteria:" in null["catalogue_ref"]
    assert rows["sales.orders::Match_Row_Ct"]["params_json"]["severity"] == "warning"
    bad = rows["sales.orders.placed::Match_Max_Date"]
    assert bad["state"] == "not_established" and "10/01/2026" in bad["reason"]
    assert all(r["surveyed_at"] == "2026-10-27T09:00:00+00:00" for r in rows.values())


def test_a_dry_run_or_an_export_without_this_database_writes_nothing(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    batches = registry.list_detail_batches("database_quality_proposals", SLUG)
    assert tg.import_export(registry, SLUG, EXPORT, dry_run=True)["imported_ct"] == 3   # no rejection here
    other = copy.deepcopy(EXPORT)
    other["projects"][0]["re_slug"] = "elsewhere"
    with pytest.raises(tg.TestGenImportError, match="no project for"):
        tg.import_export(registry, SLUG, other)
    assert registry.list_detail_batches("database_quality_proposals", SLUG) == batches


def test_imported_proposals_are_carried_forward_accepted_and_run(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    tg.import_export(registry, SLUG, EXPORT, surveyed_at="2026-10-27T09:00:00+00:00")
    imported = _proposals(registry)["sales.orders.note::Match_Null_Ct"]
    # Accepted through the review, as a person in Curate would: the parameters are the proposal's own.
    out = review.record_verdict(registry, SLUG, imported["check_key"], "accepted", decided_by="steward")
    assert out["recorded"] and out["check"]["origin"] == "testgen"
    # The table is not opted in, so no rule re-derives it; the next propose run carries it forward.
    _propose(registry, at="2026-10-27T10:00:00+00:00")
    rows = _proposals(registry)
    assert rows["sales.orders.note::Match_Null_Ct"]["state"] == "accepted"
    assert rows["sales.orders::Match_Row_Ct"]["state"] == "proposed"
    assert rows["sales.orders.note::Match_Null_Ct"]["surveyed_at"] == "2026-10-27T10:00:00+00:00"
    _, results, _ = _run(registry, entity, {("sales", "orders"): {"record_ct": 1000, "measure": _b1a_measure}})
    assert results["sales.orders.note::Match_Null_Ct"]["status"] == "pass"
    assert results["sales.orders.note::Match_Null_Ct"]["baseline_profile_surveyed_at"] == "2026-10-20T09:00:00"


def test_re_s_own_proposal_replaces_a_carried_import_and_a_reimport_does_not_duplicate(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    tg.import_export(registry, SLUG, EXPORT, surveyed_at="2026-10-27T09:00:00+00:00")
    tg.import_export(registry, SLUG, EXPORT, surveyed_at="2026-10-27T09:30:00+00:00")
    assert sum(r["origin"] == "testgen" for r in _proposals(registry).values()) == 4     # one per key, not 8
    tc.set_watch(registry, SLUG, "sales", "orders", True, by="steward")
    _propose(registry, at="2026-10-27T10:00:00+00:00")
    rows = _proposals(registry)
    # RE now proposes these from its own profile, so RE's rows stand ...
    assert rows["sales.orders.note::Match_Null_Ct"]["origin"] == "vendored"
    assert rows["sales.orders::Match_Row_Ct"]["origin"] == "vendored"
    # ... including over an import whose parameters would not validate (RE's row has its own, valid ones).
    assert (rows["sales.orders.placed::Match_Max_Date"]["origin"],
            rows["sales.orders.placed::Match_Max_Date"]["state"]) == ("vendored", "proposed")
    assert not any(r["origin"] == "testgen" for r in rows.values())


def test_an_export_key_that_is_not_re_s_is_refused(registry, entity):
    bad = copy.deepcopy(EXPORT)
    bad["projects"][0]["table_groups"][0]["tests"][0]["check_key"] = "sales.orders.note::Something_Else"
    with pytest.raises(tg.TestGenImportError, match="not RE's"):
        tg.build_rows(bad, SLUG, latest=[], verdicts={})


# ── the table card's opt-in: review and route ──────────────────────────────────────────────────────────────────

def test_the_review_shows_each_table_s_opt_in_and_records_one_read_back(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    groups = {g["table_name"]: g for g in review.build_review(registry, SLUG)["groups"]}
    assert groups["orders"]["table_comparison"] == {"watched": False}
    out = review.set_table_comparison(registry, SLUG, "sales", "orders", True, decided_by="steward")
    assert out["recorded"] and out["table_comparison"]["watched"] and out["table_comparison"]["by"] == "steward"
    body = review.build_review(registry, SLUG)
    assert {g["table_name"]: g for g in body["groups"]}["orders"]["table_comparison"]["watched"]
    assert body["table_comparison"] == {"test_types": review.table_comparison_types(), "tables_opted_in": 1}
    assert review.table_comparison_types()[:6] == list(cat.B1A_TEST_TYPES)
    with pytest.raises(review.ReviewError) as exc:
        review.set_table_comparison(registry, SLUG, "sales", "nope", True, decided_by="steward")
    assert exc.value.status == 400


@pytest.fixture
def client(registry, monkeypatch):
    from tests.zone_fakes import install

    install(monkeypatch)
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import quality_checks as routes

    monkeypatch.setattr(routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def test_the_route_records_the_signed_in_person_and_refuses_nobody(client, registry, entity):
    url = f"/api/quality-checks/{SLUG}/table-comparison"
    body = {"schema_name": "sales", "table_name": "orders", "watch": True}
    assert client.post(url, json=body).status_code == 401
    assert tc.watched_tables(registry, SLUG) == {}
    r = client.post(url, json=body, headers={"Authorization": "Bearer " + create_access_token(user_id="steward",
                                                                                             egeria_token="t")})
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] and tc.watched_tables(registry, SLUG)[("sales", "orders")]["by"] == "steward"


def test_the_cli_import_reads_the_file_and_a_dry_run_writes_nothing(registry, entity, tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from resource_explorer.cli.main import app

    _profile(registry, entity)
    _propose(registry)
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    path = tmp_path / "export.json"
    path.write_text(json.dumps(EXPORT))
    batches = registry.list_detail_batches("database_quality_proposals", SLUG)
    out = CliRunner().invoke(app, ["database", "quality-import-testgen", SLUG, "--export", str(path), "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "imported 3" in out.output and "Table_Freshness (2)" in out.output and "dry run: nothing written" in out.output
    assert registry.list_detail_batches("database_quality_proposals", SLUG) == batches
    out = CliRunner().invoke(app, ["database", "quality-import-testgen", SLUG, "--export", str(path)])
    assert out.exit_code == 0 and "written as proposals batch" in out.output
    assert len(registry.list_detail_batches("database_quality_proposals", SLUG)) == len(batches) + 1
