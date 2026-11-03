"""Quality slice Q6 (docs/design-notes/DESIGN-QUALITY-Q6-RECENCY.md): Recency, the date branch of TestGen's
functional-type inference ported as data, Future_Date_1Y, and the `before_20yr_date_ct` measure.

The gate: the Recency check proposes on a dated column and nowhere else. Covered here: the inference in upstream's
order (mostly-historical vs future vs schedule, and the earlier and later statements that pre-empt or relabel), the
two selection rules, Recency's default threshold, the absent-versus-zero rule for the new measure (a profile from
before it existed is not_established, never "not a Future Date"), the measure and check SQL, and the DDL migration
on SQLite and on Postgres. Registries are a tmp SQLite file or the throwaway Postgres test schema (`pg_registry`);
no connection reaches a surveyed database (FakeConn) and nothing is written to Egeria.
"""
from __future__ import annotations

import sqlite3

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import functional_type as ft
from resource_explorer.surveyors.database.quality.propose import run_propose
from resource_explorer.surveyors.database.quality.run_checks import run_quality_checks
from resource_explorer.surveyors.database.quality.value_profile import (
    COLUMN_MEASURES,
    RELATIVE_TO_NOW_MEASURES,
    build_table_query,
    run_value_profile,
)
from tests.test_value_profile_quality_checks import BOUNDS, FakeConn, _connect

RUN = "2026-10-20T10:00:00+00:00"           # the profile's own instant: TestGen's run_date
PROPOSED_AT = "2026-10-20T11:00:00+00:00"   # the propose run: {AS_OF_DATE}


def _date(**kw):
    """A measured date column, 100 values, none historical or future, the latest 10 days before the run."""
    base = {"general_type": "D", "column_name": "placed", "record_ct": 100, "value_ct": 100,
            "distinct_value_ct": 90, "null_value_ct": 0, "zero_length_ct": None, "filled_value_ct": None,
            "min_date": "2024-03-07", "max_date": "2026-10-10", "future_date_ct": 0, "before_20yr_date_ct": 0,
            "sample_ratio": None, "state": "measured", "surveyed_at": RUN, "as_of_date": PROPOSED_AT}
    base.update(kw)
    return base


def _recency(**kw):
    return cat.PREDICATES["Recency"].evaluate(_date(**kw))


def _future_1y(**kw):
    return cat.PREDICATES["Future_Date_1Y"].evaluate(_date(**kw))


# ── pins ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_inference_port_is_pinned_to_the_vendored_date_statements():
    assert cat.sha256_text(ft.FUNCTIONAL_DATATYPE_SQL.read_text(encoding="utf-8")) == ft.VENDORED_SQL_SHA256, (
        "functional_datatype_date.sql changed: re-read functional_type.py against it, then update the pin")


def test_recency_s_default_is_pinned_to_the_expression_it_was_ported_from():
    recency = cat.load_catalogue()["Recency"]
    assert cat.DEFAULT_PORTS["Recency"][0] == cat.sha256_text(recency.default_parm_values)


def test_both_types_are_timeliness_and_keep_upstream_severity():
    catalogue = cat.load_catalogue()
    assert [catalogue[n].dimension for n in cat.Q6_TEST_TYPES] == ["Timeliness", "Timeliness"]
    assert cat.egeria_dimension(catalogue["Future_Date_1Y"].dimension) == "timeliness"
    assert (catalogue["Recency"].severity, catalogue["Future_Date_1Y"].severity) == ("warning", "error")
    assert all(catalogue[n].origin == "vendored" for n in cat.Q6_TEST_TYPES)


# ── the inference, in upstream's order ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("measures, label", [
    ({"before_20yr_date_ct": 80}, ft.HISTORICAL_DATE),          # mostly historical: the first WHEN
    ({"before_20yr_date_ct": 75}, ft.HISTORICAL_DATE),          # >= 75 %, inclusive
    ({"future_date_ct": 96}, ft.FUTURE_DATE),                   # >= 95 % future: Future, not Schedule
    ({"future_date_ct": 95}, ft.FUTURE_DATE),
    ({"future_date_ct": 60}, ft.SCHEDULE_DATE),                 # >= 50 %, < 95 %
    ({"future_date_ct": 50}, ft.SCHEDULE_DATE),
    ({"future_date_ct": 49}, ft.LATER_DATE_BRANCH),
    ({"before_20yr_date_ct": 74, "future_date_ct": 26}, ft.LATER_DATE_BRANCH),
    ({}, ft.LATER_DATE_BRANCH),
])
def test_the_date_case_decides_historical_then_future_then_schedule(measures, label):
    assert ft.date_case_label(_date(**measures)) == label


@pytest.mark.parametrize("measures, label", [
    ({"record_ct": 0, "value_ct": 0, "future_date_ct": 0}, ft.TBD),
    ({"value_ct": 4, "future_date_ct": 4}, ft.TBD),                         # fewer than 5 values, all future
    ({"distinct_value_ct": 1, "future_date_ct": 100}, ft.CONSTANT),         # one value, every record: Constant
    ({"distinct_value_ct": 1, "value_ct": 70, "future_date_ct": 70}, ft.FUTURE_DATE),  # 70 % filled: not Constant
    ({"column_name": "due_sk", "future_date_ct": 100}, "ID-SK"),            # the masks come first
    ({"column_name": "Renewal_ID", "future_date_ct": 100}, "ID"),           # ILIKE: any case
    ({"column_name": "paid", "future_date_ct": 100}, "ID"),                 # `_` is one character: upstream too
    ({"column_name": "id", "future_date_ct": 100}, ft.FUTURE_DATE),         # '%_id' needs a character before
])
def test_earlier_statements_label_a_date_column_before_the_date_case(measures, label):
    assert ft.date_case_label(_date(**measures)) == label


def test_recency_excludes_only_a_future_or_schedule_label_that_survives_the_later_statements():
    assert ft.recency_excluded(_date(future_date_ct=96, max_date="2026-10-20 18:00:00")) is True
    assert ft.recency_excluded(_date(future_date_ct=60, max_date="2026-10-20 18:00:00")) is True
    assert ft.recency_excluded(_date(before_20yr_date_ct=80)) is False
    assert ft.recency_excluded(_date()) is False
    # 'Process ' || the label, by name: no longer 'Future Date'
    assert ft.recency_excluded(_date(column_name="update_date", future_date_ct=96)) is False
    assert ft.recency_excluded(_date(column_name="modified", future_date_ct=96)) is False
    # 'Period Week' / 'Period' by name, 'System User' and 'Process User' by name, whatever the type
    for name in ("week", "Period", "last_usr", "created_by"):
        assert ft.recency_excluded(_date(column_name=name, future_date_ct=96)) is False, name
    # an earlier statement took it: TBD, Constant, an ID
    assert ft.recency_excluded(_date(value_ct=4, future_date_ct=4)) is False
    assert ft.recency_excluded(_date(column_name="due_id", future_date_ct=96)) is False


def test_a_period_override_recency_cannot_decide_is_not_established():
    """Upstream's Period Month override (no IS NULL guard) also reads date_months_present and date_days_present,
    which RE does not measure: when its date-bound part holds, whether the label survives is unknown."""
    month_starts = _date(future_date_ct=96, min_date="2026-01-01", max_date="2026-11-01")
    assert ft.recency_excluded(month_starts) is None
    same_weekday = _date(future_date_ct=96, min_date="2026-10-18", max_date="2026-10-25")  # two Sundays
    assert ft.recency_excluded(same_weekday) is None
    # a timestamp is not a month start: the override cannot fire, so the label stands
    assert ft.recency_excluded(_date(future_date_ct=96, min_date="2026-01-01 08:00:00",
                                     max_date="2026-10-21 09:00:00")) is True


def test_an_unmeasured_input_makes_the_inference_unknown_never_a_label():
    assert ft.date_case_label(_date(before_20yr_date_ct=None)) is None
    assert ft.date_case_label(_date(future_date_ct=None)) is None
    assert ft.recency_excluded(_date(before_20yr_date_ct=None)) is None
    assert ft.date_case_label(_date(general_type="A")) is None
    # ...unless an earlier statement decides first, with what was measured
    assert ft.date_case_label(_date(value_ct=3, before_20yr_date_ct=None)) == ft.TBD


def test_datediff_day_is_whole_days_of_the_interval_toward_zero():
    """TestGen's metadata datediff('DAY', a, b) is DATE_PART('day', b - a), not a difference of dates."""
    a = ft.as_timestamp("2026-10-19 23:00:00")
    assert ft.datediff_day(a, ft.as_timestamp("2026-10-20 22:59:59")) == 0
    assert ft.datediff_day(a, ft.as_timestamp("2026-10-20 23:00:00")) == 1
    assert ft.datediff_day(ft.as_timestamp("2026-10-21 05:00:00"), a) == -1
    assert ft.as_timestamp("2026-10-20T10:00:00+02:00") == ft.as_timestamp("2026-10-20 08:00:00")


# ── selection ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_recency_selects_a_recent_dated_column():
    assert _recency() is True
    assert _recency(max_date="2026-08-19 10:00:00") is True                # 62 whole days before the run
    assert _recency(max_date="2026-10-21 09:59:59") is True                # inside run_date + 1 day


@pytest.mark.parametrize("kw", [
    {"general_type": "A"}, {"general_type": "N"},                          # not a date
    {"max_date": "2026-08-18 10:00:00"},                                   # 63 whole days stale
    {"max_date": "2026-10-21 10:00:01"},                                   # past run_date + 1 day
    {"column_name": "filedate"}, {"column_name": "file_date"},
    {"future_date_ct": 96, "max_date": "2026-10-20 18:00:00"},             # a Future Date
    {"future_date_ct": 60, "max_date": "2026-10-20 18:00:00"},             # a Schedule Date
])
def test_recency_selects_nothing_else(kw):
    assert _recency(**kw) is False


def test_a_future_date_named_as_a_process_timestamp_is_selected_as_upstream_selects_it():
    assert _recency(column_name="update_date", future_date_ct=96, max_date="2026-10-20 18:00:00") is True


def test_recency_on_a_profile_from_before_the_measure_is_not_established():
    assert _recency(before_20yr_date_ct=None) is None
    assert "before_20yr_date_ct" in cat.not_established_inputs("Recency", _date(before_20yr_date_ct=None))
    # ...but a column the other terms already reject is a plain no
    assert _recency(before_20yr_date_ct=None, max_date="2025-01-01") is False
    assert _recency(before_20yr_date_ct=None, general_type="A") is False


def test_future_date_1y_reads_only_general_type_future_date_ct_and_max_date():
    assert cat.PREDICATES["Future_Date_1Y"].inputs == ("general_type", "future_date_ct", "max_date", "as_of_date")
    assert _future_1y(future_date_ct=3, max_date="2027-03-01") is True
    assert _future_1y(future_date_ct=3, max_date="2027-10-20 00:00:00") is True   # as-of date + 365 days
    assert _future_1y(future_date_ct=3, max_date="2027-10-20 00:00:01") is False
    assert _future_1y(future_date_ct=0, max_date="2026-10-01") is False
    assert _future_1y(general_type="A", future_date_ct=3, max_date="2027-01-01") is False
    assert _future_1y(future_date_ct=None) is None


@pytest.mark.parametrize("max_date, threshold", [
    ("2026-10-21 08:00:00", 3),     # d = 0 (the interval is negative but under a day): 0 + 3
    ("2026-10-18 10:00:00", 5),     # d = 2: d + 3
    ("2026-10-15 10:00:00", 12),    # d = 5: d + 7
    ("2026-10-10 10:00:00", 14),    # d = 10: ceil(10 / 7) * 7
    ("2026-09-10 10:00:00", 60),    # d = 40: ceil(40 / 30) * 30
])
def test_recency_s_default_threshold_follows_the_vendored_case(max_date, threshold):
    params = cat.default_params(cat.load_catalogue()["Recency"], _date(max_date=max_date), 10)
    assert params["threshold_value"] == threshold and params["severity"] == "warning"


# ── the measure and the check SQL ─────────────────────────────────────────────────────────────────────────────

def test_the_new_measure_is_relative_to_now_and_expanded_before_any_name_is_filled():
    import sqlglot

    assert "before_20yr_date_ct" in COLUMN_MEASURES and "before_20yr_date_ct" in RELATIVE_TO_NOW_MEASURES
    weird = 'a;b%>"c'
    sql, aliases = build_table_query("s", "t", [(weird, "D"), ("n", "N")], "2026-10-20 10:00:00")
    assert "<%" not in sql and "%>" not in sql.replace(cat.quote_identifier(weird), "")
    assert "c0__before_20yr_date_ct" in aliases and "c1__before_20yr_date_ct" not in aliases  # dates only
    assert "DATE_PART('year', '2026-10-20 10:00:00'::TIMESTAMP)" in sql
    assert f"DATE_PART('month', {cat.quote_identifier(weird)}::TIMESTAMP)" in sql
    sqlglot.parse_one(sql, read="postgres")


def test_both_checks_render_from_the_vendored_postgres_measure():
    import sqlglot

    catalogue = cat.load_catalogue()
    recency = cat.render_measure(catalogue["Recency"], "placed", {"threshold_value": 14}, "2026-10-26 09:00:00")
    assert recency == "DATE('2026-10-26 09:00:00'::DATE) - DATE(MAX(\"placed\"))"
    one_year = cat.render_measure(catalogue["Future_Date_1Y"], "placed", {"threshold_value": 0}, "2026-10-26 09:00:00")
    assert one_year == "SUM(GREATEST(0, SIGN(\"placed\"::DATE - ('2026-10-26 09:00:00'::DATE+365))))"
    for sql in (recency, one_year):
        sqlglot.parse_one(f"SELECT {sql} FROM t", read="postgres")


def test_an_unknown_templated_function_is_refused():
    with pytest.raises(cat.ParameterError, match="NO_SUCH"):
        cat.expand_templated_functions("<%NO_SUCH;x%>")
    with pytest.raises(cat.ParameterError, match="argument 2"):
        cat.expand_templated_functions("<%DATEDIFF_DAY;x%>")


# ── end to end over a fake connection: profile -> propose -> run ─────────────────────────────────────────────

SLUG = "q6_db"
COLUMNS = [("placed", "date"), ("note", "text"), ("qty", "integer"), ("legacy_dt", "date"),
           ("ship_due", "timestamp without time zone"), ("stale", "date")]
MEASURES = {"record_ct": 100, "columns": [
    {"value_ct": 100, "distinct_value_ct": 90, "null_value_ct": 0, "min_date": "2024-01-03",
     "max_date": "2026-10-10", "future_date_ct": 0, "before_20yr_date_ct": 0},
    {"value_ct": 100, "distinct_value_ct": 40, "null_value_ct": 0, "zero_length_ct": 0, "filled_value_ct": 0},
    {"value_ct": 100, "distinct_value_ct": 50, "null_value_ct": 0},
    # mostly historical, with a recent latest value: 'Historical Date' is not a label Recency excludes, so it is
    # selected, as upstream selects it
    {"value_ct": 100, "distinct_value_ct": 90, "null_value_ct": 0, "min_date": "1960-02-03",
     "max_date": "2026-10-01", "future_date_ct": 0, "before_20yr_date_ct": 80},
    # a schedule: 60 % future, the latest within a year
    {"value_ct": 100, "distinct_value_ct": 90, "null_value_ct": 0, "min_date": "2026-06-03 08:00:00",
     "max_date": "2027-02-03 08:00:00", "future_date_ct": 60, "before_20yr_date_ct": 0},
    {"value_ct": 100, "distinct_value_ct": 90, "null_value_ct": 0, "min_date": "2019-01-03",
     "max_date": "2025-01-03", "future_date_ct": 0, "before_20yr_date_ct": 0},
]}


@pytest.fixture
def q6(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "q6.db"))
    assert str(tmp_path) in reg.database_url
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Q6", db_type="postgresql", host="localhost",
                                         port=5442, database_name="q6"))
    reg.write_detail_rows("database_tables", SLUG, "2026-10-20T09:00:00+00:00", rows=[
        {"schema_name": "sales", "table_name": "orders", "table_type": "BASE TABLE", "row_count": 100}])
    reg.write_detail_rows("database_columns", SLUG, "2026-10-20T09:00:00+00:00", rows=[
        {"schema_name": "sales", "table_name": "orders", "column_name": n, "ordinal_position": i, "data_type": t,
         "base_type": t} for i, (n, t) in enumerate(COLUMNS, start=1)])
    return reg


def _profile(reg, measures=None):
    conn = FakeConn(profile={("sales", "orders"): measures or MEASURES})
    run_value_profile(reg.get_database(SLUG), reg, _connect(conn), bounds=BOUNDS, surveyed_at=RUN,
                      ran_as="surveyor", clock=lambda: 0.0)
    return {r["column_name"]: r for r in reg.query_detail_rows("database_value_profiles", SLUG)}, conn


def _states(reg, test_type):
    run_propose(reg, SLUG, row_ct_pct_threshold=10, surveyed_at=PROPOSED_AT)
    return {r["column_name"]: r["state"] for r in reg.query_detail_rows("database_quality_proposals", SLUG)
            if r["test_type"] == test_type}


def test_the_eighth_check_proposes_on_a_dated_column_and_nowhere_else(q6):
    rows, conn = _profile(q6)
    assert rows["placed"]["before_20yr_date_ct"] == 0                       # measured: a real zero
    assert rows["note"]["before_20yr_date_ct"] is None                      # not a date: not measured
    profile_sql = next(s for s in conn.statements if s.startswith("SELECT\n"))
    assert "AS c0__before_20yr_date_ct" in profile_sql and "AS c1__before_20yr_date_ct" not in profile_sql
    # ship_due is a Schedule Date (excluded) and stale is 290 days old: dated, and still not selected
    assert _states(q6, "Recency") == {"placed": "proposed", "legacy_dt": "proposed"}
    proposal = next(r for r in q6.query_detail_rows("database_quality_proposals", SLUG)
                    if r["check_key"] == "sales.orders.placed::Recency")
    assert proposal["params_json"]["threshold_value"] == 14 and "Recency:selection_criteria" in proposal["catalogue_ref"]
    assert _states(q6, "Future_Date_1Y") == {"ship_due": "proposed"}


def test_a_profile_from_before_the_column_existed_is_not_established_not_unselected(q6):
    old = {"record_ct": 100, "columns": [{**c} for c in MEASURES["columns"]]}
    for column in old["columns"]:
        column.pop("before_20yr_date_ct", None)                             # what an old profile row holds
    rows, _ = _profile(q6, old)
    assert rows["placed"]["before_20yr_date_ct"] is None
    states = _states(q6, "Recency")
    # the stale column and the future-dated one are still a plain no: their other terms decide
    assert states == {"placed": "not_established", "legacy_dt": "not_established"}
    reason = next(r["reason"] for r in q6.query_detail_rows("database_quality_proposals", SLUG)
                  if r["check_key"] == "sales.orders.placed::Recency")
    assert "before_20yr_date_ct" in reason and "did not measure" in reason


def test_an_empty_table_leaves_recency_undefined_not_unselected(q6):
    empty = {"record_ct": 0, "columns": [
        {"value_ct": 0, "distinct_value_ct": 0, "null_value_ct": None, "min_date": None, "max_date": None,
         "future_date_ct": None, "before_20yr_date_ct": None} if t.startswith(("date", "timestamp")) else
        {"value_ct": 0, "distinct_value_ct": 0, "null_value_ct": None, "zero_length_ct": None,
         "filled_value_ct": None} for _, t in COLUMNS]}
    rows, _ = _profile(q6, empty)
    assert rows["placed"]["before_20yr_date_ct"] is None                    # SUM over no rows: undefined
    assert set(_states(q6, "Recency").values()) == {"not_established"}
    reason = next(r["reason"] for r in q6.query_detail_rows("database_quality_proposals", SLUG)
                  if r["check_key"] == "sales.orders.placed::Recency")
    assert reason.startswith("the table was empty when profiled")


def test_a_column_with_no_values_says_so_rather_than_not_measured(q6):
    measures = {"record_ct": 100, "columns": [{**c} for c in MEASURES["columns"]]}
    measures["columns"][0] = {"value_ct": 0, "distinct_value_ct": 0, "null_value_ct": 100, "min_date": None,
                              "max_date": None, "future_date_ct": 0, "before_20yr_date_ct": 0}
    _profile(q6, measures)
    _states(q6, "Recency")
    row = next(r for r in q6.query_detail_rows("database_quality_proposals", SLUG)
               if r["check_key"] == "sales.orders.placed::Recency")
    assert row["state"] == "not_established"
    assert row["reason"] == "the column had no values when profiled, so max_date is undefined"


def test_an_accepted_recency_check_runs_and_warns_past_its_threshold(q6):
    _profile(q6)
    _states(q6, "Recency")
    proposal = next(r for r in q6.query_detail_rows("database_quality_proposals", SLUG)
                    if r["check_key"] == "sales.orders.placed::Recency")
    q6.record_quality_verdict(SLUG, proposal["check_key"], "accepted", proposal["params_json"],
                              proposal_id=proposal["id"], decided_by="peterprofile")
    conn = FakeConn(checks={("sales", "orders"): {"record_ct": 100, "measure": lambda sql: 20}})
    out = run_quality_checks(q6.get_database(SLUG), q6, _connect(conn), bounds=BOUNDS,
                             surveyed_at="2026-10-26T09:00:00+00:00", ran_as="surveyor")
    (result,) = q6.query_detail_rows("database_quality_results", SLUG)
    assert (result["status"], result["measure"]) == ("warning", 20)        # Recency's severity is Warning
    assert "DATE('2026-10-26 09:00:00'::DATE) - DATE(MAX(\"placed\"))" in conn.statements[-2]
    (annotation,) = out["quality_annotations"]
    assert annotation.quality_dimension == "timeliness"
    assert "(days before test 20)" in annotation.summary                    # a figure, not "20 ... of 100"


# ── the DDL migration ─────────────────────────────────────────────────────────────────────────────────────────

def _cols(reg) -> set[str]:
    with reg._conn() as conn:
        return reg._get_table_columns(conn, "database_value_profiles")


def test_fresh_sqlite_has_the_column_and_a_second_init_is_a_no_op(tmp_path):
    path = str(tmp_path / "twice.db")
    first, second = _cols(ProjectRegistry(db_path=path)), _cols(ProjectRegistry(db_path=path))
    assert "before_20yr_date_ct" in first and first == second


def test_an_existing_sqlite_registry_gains_the_column_and_its_rows_read_not_measured(tmp_path):
    path = tmp_path / "old.db"
    reg = ProjectRegistry(db_path=str(path))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Q6", db_type="postgresql", host="localhost",
                                         port=5442, database_name="q6"))
    reg.write_detail_rows("database_value_profiles", SLUG, RUN, rows=[{
        "schema_name": "s", "table_name": "t", "column_name": "d", "general_type": "D", "record_ct": 5,
        "value_ct": 5, "future_date_ct": 0, "before_20yr_date_ct": 0, "state": "measured"}])
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE database_value_profiles DROP COLUMN before_20yr_date_ct")   # a pre-Q6 registry
    con.commit()
    con.close()

    again = ProjectRegistry(db_path=str(path))                                           # the migration runs
    assert "before_20yr_date_ct" in _cols(again)
    (row,) = again.query_detail_rows("database_value_profiles", SLUG)
    assert row["future_date_ct"] == 0 and row["before_20yr_date_ct"] is None             # NULL, never 0
    with again._conn() as conn:
        (decl,) = [r["type"] for r in conn.execute("PRAGMA table_info(database_value_profiles)").fetchall()
                   if r["name"] == "before_20yr_date_ct"]
    assert decl == "BIGINT"


def test_the_migration_adds_a_nullable_bigint_on_postgres_and_is_idempotent(pg_registry, monkeypatch):
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url                         # the throwaway schema only
    with pg_registry._conn() as conn:
        conn.execute("ALTER TABLE database_value_profiles DROP COLUMN IF EXISTS before_20yr_date_ct")
    assert "before_20yr_date_ct" not in _cols(pg_registry)
    monkeypatch.setattr(PR, "_pg_schema_ready", set())                                  # forget the per-process init
    again = PR(database_url=pg_registry.database_url)
    with again._conn() as conn:
        (row,) = conn.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'database_value_profiles' "
            "AND column_name = 'before_20yr_date_ct'").fetchall()
    assert (row["data_type"], row["is_nullable"], row["column_default"]) == ("bigint", "YES", None)
    before = _cols(again)
    monkeypatch.setattr(PR, "_pg_schema_ready", set())
    assert _cols(PR(database_url=pg_registry.database_url)) == before
