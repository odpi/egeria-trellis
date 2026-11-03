"""Q4 port order steps 4 and 5 (docs/design-notes/DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md): Dupe_Rows, and the
four schema-drift checks.

Covered: the vendored entries and pins; selection; Dupe_Rows (its key, the general_type X exclusion and the way the result
names it, equivalence with TestGen's own statement on a fixture table, its own per-table budget with a faked clock);
the drift checks (a first survey is never "no drift", each drift case, the baseline checks, Schema_Conformance); and
that proposing and running them opens no connection except Dupe_Rows' own statement.
No connection reaches a surveyed database: Dupe_Rows' statement runs on a SQLite fixture, everything else reads the
registry (a tmp SQLite file). Nothing touches Egeria.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from resource_explorer import quality_review as review
from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import dupe_rows, schema_drift
from resource_explorer.surveyors.database.quality import table_comparison as tc
from resource_explorer.surveyors.database.quality.run_checks import run_quality_checks
from resource_explorer.surveyors.database.quality.value_profile import ProfileBounds
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    BOUNDS,
    INVENTORY_AT,
    SLUG,
    FakeConn,
    PgError,
    _by_key,
    _profile,
    _propose,
    entity,
    registry,
)

STEP_4_5 = cat.STEP_4_TEST_TYPES + cat.STEP_5_TEST_TYPES


def _p(name, **kw):
    return cat.PREDICATES[name].evaluate(kw)


# ── the vendored entries and their pins ─────────────────────────────────────────────────────────────────────────

def test_the_five_are_vendored_with_the_run_type_upstream_gives_them():
    catalogue = cat.load_catalogue()
    assert STEP_4_5 == ("Dupe_Rows", "Match_Column_Ct", "Match_Column_Type", "Schema_Conformance", "Schema_Drift")
    assert {n: catalogue[n].run_type for n in STEP_4_5} == {
        "Dupe_Rows": "QUERY", "Match_Column_Ct": "METADATA", "Match_Column_Type": "METADATA",
        "Schema_Conformance": "METADATA", "Schema_Drift": "METADATA"}
    # Dupe_Rows is in TestGen's Standard set, Schema_Drift in its Monitor set: neither is opt-in. The two Match_ types are
    # in the Table Comparison set, so they are, as every Match_ type before them.
    assert not catalogue["Dupe_Rows"].is_table_comparison and not catalogue["Schema_Drift"].is_table_comparison
    assert catalogue["Match_Column_Ct"].is_table_comparison and catalogue["Match_Column_Type"].is_table_comparison
    # Schema_Drift is `tablegroup` scope upstream; RE keeps one check per table.
    assert catalogue["Schema_Drift"].is_table_check and not catalogue["Match_Column_Type"].is_table_check
    assert catalogue["Schema_Conformance"].manual_only and not any(
        catalogue[n].manual_only for n in cat.PORTED_TEST_TYPES if n != "Schema_Conformance")


def test_the_dupe_rows_statement_is_pinned_to_the_vendored_grouping_subselect():
    assert dupe_rows.pin_matches()
    assert "HAVING COUNT(*) > 1" in dupe_rows.grouping_template()


@pytest.mark.parametrize("name", ["Dupe_Rows", "Match_Column_Ct", "Schema_Drift"])
def test_a_template_type_is_pinned_to_its_generation_template(name):
    t = cat.load_catalogue()[name]
    assert t.selection_criteria == "TEMPLATE" and t.generation_template == f"gen_{name}.sql"
    assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(cat.selection_source(t))
    assert f":{name}:generation_template:sha256:" in cat.catalogue_ref(t)


def test_a_drift_check_is_not_published_as_a_rule_yet_and_says_why():
    t = cat.load_catalogue()["Schema_Drift"]
    with pytest.raises(cat.ParameterError, match="not published as a DataQualityRule yet"):
        cat.published_threshold(t, {"severity": "error"})


# ── selection, in SQL's three-valued logic ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Dupe_Rows", "Match_Column_Ct", "Schema_Drift"])
def test_a_table_template_selects_every_profiled_table_and_leaves_an_unprofiled_one_unknown(name):
    assert _p(name, state="measured", record_ct=0) is True          # an empty table is selected, as upstream
    assert _p(name, state="not_measured") is None                   # an incomplete table: no proposal (2026-10-21)


@pytest.mark.parametrize("kw, expected", [
    ({"general_type": "A", "state": "measured"}, True),
    ({"general_type": "X", "state": "measured"}, True),              # query_error IS NULL: selected
    ({"general_type": "X", "state": "not_measured"}, False),         # X with a query_error: left out, as upstream
    ({"general_type": "A", "state": "not_measured"}, None),          # an incomplete table: no proposal
    ({"general_type": None, "state": "measured"}, False),            # general_type IS NOT NULL fails
    ({"general_type": "", "state": "measured"}, False),
])
def test_match_column_type(kw, expected):
    assert _p("Match_Column_Type", **kw) is expected


def test_schema_conformance_has_no_selection_rule_and_is_never_proposed(registry, entity):
    assert _p("Schema_Conformance", state="measured") is False
    _profile(registry, entity)
    _propose(registry)
    assert not any(r["test_type"] == "Schema_Conformance" for r in registry.query_detail_rows(
        "database_quality_proposals", SLUG))


# ── Dupe_Rows: the key ──────────────────────────────────────────────────────────────────────────────────────────

def test_the_key_is_every_column_but_those_of_general_type_x_and_they_are_named():
    t = cat.load_catalogue()["Dupe_Rows"]
    params = dupe_rows.default_params(t, {"table_columns": [("order_id", "N"), ("note", "A"), ("payload", "X")]})
    assert params == {"groupby_names": ["order_id", "note"], "skip_errors": 0, "severity": "error",
                      "excluded_columns": [{"column": "payload", "general_type": "X"}]}


def test_a_table_of_only_x_columns_has_no_key_and_is_not_established():
    t = cat.load_catalogue()["Dupe_Rows"]
    with pytest.raises(cat.ParameterError, match="every column of the table has general type X"):
        dupe_rows.default_params(t, {"table_columns": [("a", "X"), ("b", "X")]})
    with pytest.raises(cat.ParameterError, match="no columns"):
        dupe_rows.default_params(t, {})


def test_propose_builds_the_key_from_the_inventory_in_column_order(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    orders = rows["sales.orders::Dupe_Rows"]
    assert orders["state"] == "proposed" and orders["origin"] == "vendored"
    assert orders["params_json"]["groupby_names"] == ["order_id", "note", "placed"]   # payload (jsonb) is X
    assert orders["params_json"]["excluded_columns"] == [{"column": "payload", "general_type": "X"}]
    assert ":Dupe_Rows:generation_template:sha256:" in orders["catalogue_ref"]
    # An incomplete table gets no proposal: said, not hidden.
    assert rows["sales.big::Dupe_Rows"]["state"] == "not_established"
    assert "not profiled" in rows["sales.big::Dupe_Rows"]["reason"]


def test_the_statement_runs_the_vendored_subselect_over_quoted_inventory_names():
    sql = dupe_rows.build_statement("sa\"les", "or ders", ["id", 'we"ird'])
    assert 'FROM "sa""les"."or ders"' in sql
    assert 'SELECT "id", "we""ird", COUNT(*) as record_ct' in sql and 'GROUP BY "id", "we""ird"' in sql
    assert "HAVING COUNT(*) > 1" in sql and "WHERE 1=1" in sql


# ── Dupe_Rows: equivalence with TestGen's own statement on a fixture ────────────────────────────────────────────

class SqliteConn:
    """A DatabaseConnection over an in-memory SQLite database with the fixture attached as schema `sales`. The statements
    are plain SQL (no Postgres-only syntax), so TestGen's own and RE's run on the same data. Records what it was asked."""

    def __init__(self, rows, *, advance=None, error=None):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH ':memory:' AS sales")
        self.db.execute("CREATE TABLE sales.orders (order_id INTEGER, note TEXT, placed TEXT)")
        self.db.executemany("INSERT INTO sales.orders VALUES (?, ?, ?)", rows)
        self.statements, self.advance, self.error = [], advance, error

    def execute_query(self, sql, params=()):
        self.statements.append(sql)
        return []

    def end_transaction(self):
        self.statements.append("<end transaction>")

    def execute_query_isolated(self, sql, params=()):
        self.statements.append(sql)
        if self.advance:
            self.advance()
        if self.error:
            raise self.error
        cur = self.db.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


#: 9 rows: (1,a,x) x3 and (2,b,y) x2 are duplicates (2 groups, 5 rows); three rows are unique. A NULL is not equal to a NULL
#: in a unique constraint but GROUP BY puts NULLs together, as in Postgres: (NULL,NULL,NULL) x2 is a third group.
ROWS = [(1, "a", "x")] * 3 + [(2, "b", "y")] * 2 + [(3, "c", "z"), (4, "d", "w")] + [(None, None, None)] * 2


def _upstream_statement(key):
    """TestGen's own Postgres template for Dupe_Rows, placeholders filled the way its runner does, with the vendored text."""
    from resource_explorer.surveyors.database.quality.catalogue import (
        TEST_TYPES_DIR,
        postgres_template,
        read_raw,
    )

    text = postgres_template(read_raw(TEST_TYPES_DIR / "Dupe_Rows.yaml"))
    names = ", ".join(f'"{c}"' for c in key)
    for placeholder, value in (("{TEST_TYPE}", "Dupe_Rows"), ("{TEST_DEFINITION_ID}", "d"), ("{TEST_SUITE_ID}", "s"),
                               ("{TEST_RUN_ID}", "r"), ("{RUN_DATE}", "2026-10-29"), ("{SCHEMA_NAME}", "sales"),
                               ("{TABLE_NAME}", "orders"), ("{COLUMN_NAME_NO_QUOTES}", ",".join(key)),
                               ("{SKIP_ERRORS}", "0"), ("{INPUT_PARAMETERS}", ""), ("{GROUPBY_NAMES}", names),
                               ("{QUOTE}", '"'), ("{SUBSET_CONDITION}", "1=1"), ("{VARCHAR_TYPE}", "TEXT")):
        text = text.replace(placeholder, value)
    return text


def test_re_s_statement_counts_what_testgen_s_counts_on_a_fixture():
    key = ["order_id", "note", "placed"]
    upstream = SqliteConn(ROWS).execute_query_isolated(_upstream_statement(key))[0]
    mine = SqliteConn(ROWS).execute_query_isolated(dupe_rows.build_statement("sales", "orders", key))[0]
    # TestGen: result_measure = rows in duplicate groups, result_code from the group count.
    assert upstream["result_measure"] == mine["rows_in_groups"] == 7
    assert (mine["group_ct"], mine["table_ct"]) == (3, len(ROWS) == 9 and 9)
    assert upstream["result_code"] == (0 if mine["group_ct"] > 0 else 1)
    assert "Duplicate records: 7" in upstream["result_message"]


def test_a_table_with_no_duplicates_counts_zero_groups_not_a_missing_answer():
    conn = SqliteConn([(1, "a", "x"), (2, "b", "y")])
    row = {}
    dupe_rows.run_table(conn, "sales", "orders", _check(row), BOUNDS, clock=lambda: 0.0)
    assert (row["status"], row["measure"], row["record_ct"]) == ("pass", 0, 2)
    assert row["reason"].startswith("no duplicate rows: 0 duplicate row groups in 2 rows")


def _check(row, key=("order_id", "note", "placed"), skip=0, left_out=()):
    return {"row": row, "key": list(key), "skip": skip, "left_out": list(left_out), "fail_status": "fail"}


def test_the_result_states_groups_rows_and_the_table_s_rows_and_names_what_it_left_out():
    row = {}
    left_out = [{"column": "payload", "general_type": "X"}]
    dupe_rows.run_table(SqliteConn(ROWS), "sales", "orders", _check(row, left_out=left_out), BOUNDS, clock=lambda: 0.0)
    assert (row["status"], row["measure"], row["record_ct"]) == ("fail", 7, 9)
    assert "3 duplicate row groups holding 7 of 9 rows" in row["reason"]
    assert "left out of the group key, no equality: payload (general type X)" in row["reason"]


def test_the_threshold_counts_groups_not_rows():
    row = {}
    dupe_rows.run_table(SqliteConn(ROWS), "sales", "orders", _check(row, skip=3), BOUNDS, clock=lambda: 0.0)
    assert row["status"] == "pass" and "(fails when duplicate row groups > 3)" in row["reason"]


# ── Dupe_Rows: its own budget ───────────────────────────────────────────────────────────────────────────────────

def test_a_read_that_runs_out_of_its_budget_is_not_measured_never_a_zero_or_a_pass():
    ticks = iter([0.0, 301.0])
    conn = SqliteConn(ROWS, error=PgError("canceling statement due to statement timeout", "57014"))
    row = {}
    dupe_rows.run_table(conn, "sales", "orders", _check(row), BOUNDS, clock=lambda: next(ticks))
    assert row["status"] == "not_measured" and row["reason"] == "exceeded 5 min · duplicates not counted"
    assert row.get("measure") is None and row.get("record_ct") is None
    assert "SET LOCAL statement_timeout = 300000" in " ".join(conn.statements)    # the budget IS the timeout


def test_any_error_that_arrives_with_the_budget_spent_is_the_budget_running_out():
    ticks = iter([0.0, 400.0])
    row = {}
    dupe_rows.run_table(SqliteConn(ROWS, error=PgError("server closed the connection")), "sales", "orders",
                        _check(row), BOUNDS, clock=lambda: next(ticks))
    assert row["status"] == "not_measured" and row["reason"].startswith("exceeded 5 min")


def test_an_error_inside_the_budget_is_an_error_and_a_privilege_error_is_not_permitted():
    row = {}
    dupe_rows.run_table(SqliteConn(ROWS, error=PgError("relation is broken")), "sales", "orders", _check(row), BOUNDS,
                        clock=lambda: 1.0)
    assert (row["status"], row["reason"]) == ("error", "relation is broken")
    row = {}
    dupe_rows.run_table(SqliteConn(ROWS, error=PgError("permission denied for table orders", "42501")), "sales",
                        "orders", _check(row), BOUNDS, clock=lambda: 1000.0)
    assert row["status"] == "not_permitted"           # classified first, whatever the clock says


def test_the_budget_is_its_own_setting_and_a_zero_budget_reads_nothing():
    conn = SqliteConn(ROWS)
    row = {}
    dupe_rows.run_table(conn, "sales", "orders", _check(row), ProfileBounds(60, dupe_rows_budget_seconds=0),
                        clock=lambda: 0.0)
    assert row["status"] == "not_measured" and conn.statements == []
    assert ProfileBounds(60).dupe_rows_budget_seconds == 300.0 == ProfileBounds(60).table_budget_seconds
    row = {}
    dupe_rows.run_table(SqliteConn(ROWS), "sales", "orders", _check(row), ProfileBounds(60, 300.0, 90.0), clock=lambda: 0.0)
    assert row["status"] == "fail"


def test_the_message_names_the_minutes_it_was_given():
    assert dupe_rows.exceeded_reason(90.0) == "exceeded 1.5 min · duplicates not counted"


# ── Dupe_Rows through the run step ──────────────────────────────────────────────────────────────────────────────

class DupeConn(FakeConn):
    """The shared fake, answering Dupe_Rows' statement from canned counts and recording it."""

    def __init__(self, groups, *, error=None, tick=None, **kw):
        super().__init__(**kw)
        self.groups, self.error, self.tick = groups, error, tick

    def execute_query_isolated(self, sql, params=()):
        if "AS rows_in_groups" in sql:
            assert self.in_read_only
            self.statements.append(sql)
            if self.tick:
                self.tick()
            if self.error:
                raise self.error
            g, r, t = self.groups
            return [{"rows_in_groups": r, "group_ct": g, "table_ct": t}]
        return super().execute_query_isolated(sql, params)


def _accept_dupe(registry, key="sales.orders::Dupe_Rows", **params):
    base = {"groupby_names": ["order_id", "note", "placed"], "skip_errors": 0,
            "excluded_columns": [{"column": "payload", "general_type": "X"}], "severity": "error",
            "profile_surveyed_at": "p"}
    base.update(params)
    registry.record_quality_verdict(SLUG, key, "accepted", base, decided_by="steward")


def _run_with(registry, entity, conn, clock=None, at="2026-10-20T13:00:00+00:00"):
    @contextmanager
    def connect():
        yield conn
    kw = {"clock": clock} if clock else {}
    out = run_quality_checks(entity, registry, connect, bounds=BOUNDS, surveyed_at=at, ran_as="surveyor", **kw)
    return out, _by_key(registry, "database_quality_results")


def test_an_accepted_dupe_rows_runs_in_its_own_statement_not_in_the_per_table_select(registry, entity):
    _accept_dupe(registry)
    conn = DupeConn((2, 5, 1000))
    out, rows = _run_with(registry, entity, conn)
    r = rows["sales.orders::Dupe_Rows"]
    assert (r["status"], r["measure"], r["record_ct"]) == ("fail", 5, 1000)
    assert (r["comparison_operator"], r["threshold_values_json"], r["severity"]) == ("mustBeLessOrEqualTo", [0], "error")
    assert "2 duplicate row groups holding 5 of 1000 rows" in r["reason"] and "payload" in r["reason"]
    assert not any(s.startswith("SELECT COUNT(*) AS record_ct") for s in conn.statements)
    assert sum("AS rows_in_groups" in s for s in conn.statements) == 1
    assert 'GROUP BY "order_id", "note", "placed"' in " ".join(conn.statements)
    assert "payload" not in " ".join(s for s in conn.statements if "rows_in_groups" in s)
    note = [a for a in out["quality_annotations"] if a.item_key == "sales.orders::Dupe_Rows"][0]
    assert "5 duplicate records of 1000" in note.summary


def test_no_duplicates_is_a_pass_with_its_own_words(registry, entity):
    _accept_dupe(registry)
    _, rows = _run_with(registry, entity, DupeConn((0, 0, 1000)))
    assert rows["sales.orders::Dupe_Rows"]["status"] == "pass"
    assert rows["sales.orders::Dupe_Rows"]["reason"].startswith("no duplicate rows")


def test_the_run_budget_is_its_own_and_exhausting_it_leaves_not_measured(registry, entity):
    _accept_dupe(registry)
    now = [0.0]
    conn = DupeConn((0, 0, 0), error=PgError("canceling statement due to statement timeout", "57014"),
                    tick=lambda: now.__setitem__(0, 305.0))
    out, rows = _run_with(registry, entity, conn, clock=lambda: now[0])
    r = rows["sales.orders::Dupe_Rows"]
    assert (r["status"], r["state"], r["measure"]) == ("not_measured", "not_measured", None)
    assert r["reason"] == "exceeded 5 min · duplicates not counted"
    note = [a for a in out["quality_annotations"] if a.item_key == "sales.orders::Dupe_Rows"][0]
    assert "not_measured" in note.summary and note.quality_score is None


def test_a_group_key_column_that_left_the_inventory_is_not_established_and_runs_nothing(registry, entity):
    _accept_dupe(registry, groupby_names=["order_id", "gone"])
    conn = DupeConn((0, 0, 0))
    _, rows = _run_with(registry, entity, conn)
    r = rows["sales.orders::Dupe_Rows"]
    assert r["status"] == "not_established" and "gone of the group key is no longer in inventory" in r["reason"]
    assert not any("rows_in_groups" in s for s in conn.statements)


@pytest.mark.parametrize("params, message", [
    ({"groupby_names": []}, "groupby_names must be a list"),
    ({"groupby_names": "order_id"}, "groupby_names must be a list"),
    ({"skip_errors": "many"}, "skip_errors must be a number"),
    ({"skip_errors": -1}, "at least 0"),
    ({"severity": "loud"}, "severity must be one of"),
])
def test_a_parameter_that_does_not_validate_is_not_established_quoting_it(registry, entity, params, message):
    _accept_dupe(registry, **params)
    conn = DupeConn((0, 0, 0))
    _, rows = _run_with(registry, entity, conn)
    assert rows["sales.orders::Dupe_Rows"]["status"] == "not_established"
    assert message in rows["sales.orders::Dupe_Rows"]["reason"]
    assert not any("rows_in_groups" in s for s in conn.statements)


def test_a_view_is_not_read_for_duplicates(registry, entity):
    registry.record_quality_verdict(SLUG, "sales.v_orders::Dupe_Rows", "accepted", {
        "groupby_names": ["order_id"], "skip_errors": 0, "excluded_columns": [], "severity": "error"},
        decided_by="steward")
    conn = DupeConn((0, 0, 0), facts={("sales", "v_orders"): {"relkind": "v"}})
    _, rows = _run_with(registry, entity, conn)
    assert rows["sales.v_orders::Dupe_Rows"]["status"] == "not_measured"
    assert not any("rows_in_groups" in s for s in conn.statements)


# ── the drift checks: a registry of surveys, no connection ──────────────────────────────────────────────────────

FIRST, SECOND, THIRD = "2026-10-20T09:30:00+00:00", "2026-10-21T09:00:00+00:00", "2026-10-22T09:00:00+00:00"


def _survey(registry, at, *, drop=(), add=(), retype=None, tables_gone=(), tables_new=None):
    """Record a survey at `at`: the fixture inventory with columns dropped, added or retyped, whole tables gone, or new
    tables {(schema, table): [(column, type)]}."""
    rows = []
    for c in registry.query_detail_rows("database_columns", SLUG, INVENTORY_AT):
        key = (c["schema_name"], c["table_name"], c["column_name"])
        if key in drop or (key[0], key[1]) in tables_gone:
            continue
        row = dict(c)
        if retype and key in retype:
            row["data_type"] = row["base_type"] = retype[key]
        rows.append(row)
    for i, (s, t, name, data_type) in enumerate(add, start=50):
        rows.append({"schema_name": s, "table_name": t, "column_name": name, "ordinal_position": i,
                     "data_type": data_type, "base_type": data_type, "state": "measured"})
    for (s, t), cols in (tables_new or {}).items():
        for i, (name, data_type) in enumerate(cols, start=1):
            rows.append({"schema_name": s, "table_name": t, "column_name": name, "ordinal_position": i,
                         "data_type": data_type, "base_type": data_type, "state": "measured"})
    tables = sorted({(r["schema_name"], r["table_name"]) for r in rows})
    registry.record_database_survey(slug=SLUG, schema_count=1, table_count=len(tables), column_count=len(rows),
                                    survey_data={}, source="local", surveyed_at=at)
    registry.write_detail_rows("database_columns", SLUG, at, source="local", rows=rows)
    registry.write_detail_rows("database_tables", SLUG, at, source="local", rows=[
        {"schema_name": s, "table_name": t, "table_type": "BASE TABLE", "row_count": 1, "state": "measured"}
        for s, t in tables])


def _accepted(registry, key, **params):
    base = {"severity": "error"}
    base.update(params)
    registry.record_quality_verdict(SLUG, key, "accepted", base, decided_by="steward")


class NoConn:
    """The drift checks open no connection: this raises if one is asked for."""

    def __call__(self):
        raise AssertionError("a drift check opened a connection")


def _run_drift(registry, entity, at="2026-10-23T00:00:00+00:00"):
    out = run_quality_checks(entity, registry, NoConn(), bounds=BOUNDS, surveyed_at=at, ran_as="surveyor")
    return out, _by_key(registry, "database_quality_results")


def _drift(registry, entity, table="orders"):
    _accepted(registry, f"sales.{table}::Schema_Drift")
    return _run_drift(registry, entity)[1][f"sales.{table}::Schema_Drift"]


def test_a_first_survey_answers_not_established_never_no_drift(registry, entity):
    _survey(registry, FIRST)
    r = _drift(registry, entity)
    assert (r["status"], r["state"], r["measure"]) == ("not_established", "not_measured", None)
    assert r["reason"] == schema_drift.WORDS["first_survey"] and "not a finding that the schema is unchanged" in r["reason"]


def test_no_survey_at_all_is_not_established_too(registry, entity):
    r = _drift(registry, entity)
    assert r["status"] == "not_established" and r["reason"] == schema_drift.WORDS["no_survey"]


def test_two_surveys_with_nothing_changed_is_a_pass_that_says_what_it_compared(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND)
    r = _drift(registry, entity)
    assert (r["status"], r["measure"]) == ("pass", 0)
    assert r["reason"] == f"No column was added, dropped or retyped between the surveys of {FIRST} and {SECOND}."


def test_a_column_added_dropped_and_retyped_each_fail(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND, drop={("sales", "orders", "note")}, add=[("sales", "orders", "extra", "text")],
            retype={("sales", "orders", "order_id"): "bigint"})
    r = _drift(registry, entity)
    assert (r["status"], r["measure"]) == ("fail", 3)
    assert f"1 column added since {FIRST}: extra." in r["reason"]
    assert f"1 column dropped since {FIRST}: note." in r["reason"]
    assert f"1 column retyped since {FIRST}: order_id (integer → bigint)." in r["reason"]


def test_a_table_added_and_a_table_dropped(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND, tables_gone={("sales", "empty")}, tables_new={("sales", "fresh"): [("a", "text"), ("b", "date")]})
    _accepted(registry, "sales.fresh::Schema_Drift")
    _accepted(registry, "sales.empty::Schema_Drift")
    _accepted(registry, "sales.nowhere::Schema_Drift")
    rows = _run_drift(registry, entity)[1]
    assert rows["sales.fresh::Schema_Drift"]["status"] == "fail"
    assert rows["sales.fresh::Schema_Drift"]["reason"] == f"Table added since {FIRST}: it has 2 columns."
    assert rows["sales.empty::Schema_Drift"]["reason"] == f"Table dropped since {FIRST}: it had 1 column."
    assert rows["sales.empty::Schema_Drift"]["measure"] == 1
    assert rows["sales.nowhere::Schema_Drift"]["status"] == "not_established"
    assert "neither of the two newest surveys" in rows["sales.nowhere::Schema_Drift"]["reason"]


def test_only_the_two_newest_surveys_are_compared(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND, drop={("sales", "orders", "note")})
    _survey(registry, THIRD, drop={("sales", "orders", "note")})
    r = _drift(registry, entity)
    assert r["status"] == "pass" and SECOND in r["reason"] and THIRD in r["reason"]


def test_a_survey_marked_invalid_is_not_a_baseline(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND, drop={("sales", "orders", "note")})
    assert registry.mark_database_survey_invalid(SLUG, SECOND, "local", "a bad run") == 1
    r = _drift(registry, entity)
    assert r["status"] == "not_established"          # one valid survey left: a first survey again


def test_a_dotted_table_name_does_not_leak_another_table_s_columns(registry, entity):
    _survey(registry, FIRST, tables_new={("sales", "a.b"): [("c", "text")], ("sales", "a"): [("b.c", "text")]})
    _survey(registry, SECOND, tables_new={("sales", "a.b"): [("c", "text")], ("sales", "a"): [("b.c", "text")]},
            add=[("sales", "a", "z", "text"), ("sales", "a.b", "b.c", "text")])
    _accepted(registry, "sales.a::Schema_Drift")
    rows = _run_drift(registry, entity)[1]
    # Table `a.b` gained a column that dots to the same name as one of table `a`'s ("sales.a.b.c"): it is `a.b`'s, not `a`'s.
    assert (rows["sales.a::Schema_Drift"]["status"], rows["sales.a::Schema_Drift"]["measure"]) == ("fail", 1)



# ── Match_Column_Ct and Match_Column_Type: baseline against the newest survey ───────────────────────────────────

def test_propose_takes_the_baselines_from_the_newest_survey_only_for_an_opted_in_table(registry, entity):
    _survey(registry, FIRST)
    _profile(registry, entity)
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    assert not any(k.endswith(("::Match_Column_Ct", "::Match_Column_Type")) for k in rows)       # not opted in
    tc.set_watch(registry, SLUG, "sales", "orders", True, by="steward")
    _propose(registry, at="2026-10-20T12:00:00+00:00")
    rows = _by_key(registry, "database_quality_proposals")
    ct = rows["sales.orders::Match_Column_Ct"]
    assert ct["state"] == "proposed" and ct["params_json"]["threshold_value"] == 4
    assert ct["params_json"]["baseline_survey_at"] == FIRST
    typ = rows["sales.orders.note::Match_Column_Type"]
    assert (typ["state"], typ["params_json"]["baseline_value"], typ["params_json"]["baseline_survey_at"]) == (
        "proposed", "A", FIRST)
    assert rows["sales.orders.payload::Match_Column_Type"]["params_json"]["baseline_value"] == "X"   # no query_error


def test_without_a_stored_survey_the_baselines_are_not_established_not_guessed(registry, entity):
    _profile(registry, entity)
    tc.set_watch(registry, SLUG, "sales", "orders", True, by="steward")
    _propose(registry)
    ct = _by_key(registry, "database_quality_proposals")["sales.orders::Match_Column_Ct"]
    assert ct["state"] == "not_established" and "no stored survey lists this database's columns" in ct["reason"]


def test_no_survey_since_the_baseline_is_not_established_never_a_pass(registry, entity):
    _survey(registry, FIRST)
    _accepted(registry, "sales.orders::Match_Column_Ct", threshold_value=4, baseline_survey_at=FIRST)
    _accepted(registry, "sales.orders.note::Match_Column_Type", baseline_value="A", baseline_survey_at=FIRST)
    rows = _run_drift(registry, entity)[1]
    for key in ("sales.orders::Match_Column_Ct", "sales.orders.note::Match_Column_Type"):
        assert rows[key]["status"] == "not_established", key
        assert rows[key]["reason"] == schema_drift.WORDS["no_newer_survey"].format(baseline=FIRST), key


def test_match_column_ct_holds_changes_and_loses_the_table(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND)
    _accepted(registry, "sales.orders::Match_Column_Ct", threshold_value=4, baseline_survey_at=FIRST)
    _accepted(registry, "sales.big::Match_Column_Ct", threshold_value=1, baseline_survey_at=FIRST)
    _accepted(registry, "sales.empty::Match_Column_Ct", threshold_value=1, baseline_survey_at=FIRST, severity="warning")
    _survey(registry, THIRD, add=[("sales", "big", "extra", "text")], tables_gone={("sales", "empty")})
    rows = _run_drift(registry, entity)[1]
    held = rows["sales.orders::Match_Column_Ct"]
    assert (held["status"], held["measure"], held["comparison_operator"], held["threshold_values_json"]) == (
        "pass", 4, "mustBe", [4])
    assert held["reason"] == f"4 columns, as at the baseline; compared with the survey of {THIRD}."
    assert rows["sales.big::Match_Column_Ct"]["status"] == "fail"
    assert rows["sales.big::Match_Column_Ct"]["reason"] == f"2 columns in the survey of {THIRD}, 1 at the baseline."
    gone = rows["sales.empty::Match_Column_Ct"]
    assert (gone["status"], gone["measure"]) == ("warning", 0)
    assert gone["reason"] == f"The table is not in the survey of {THIRD} (0 columns); 1 at the baseline."


def test_match_column_type_reads_the_general_type_not_the_database_type(registry, entity):
    _survey(registry, FIRST)
    _accepted(registry, "sales.orders.order_id::Match_Column_Type", baseline_value="N", baseline_survey_at=FIRST)
    _accepted(registry, "sales.orders.note::Match_Column_Type", baseline_value="N", baseline_survey_at=FIRST)
    _accepted(registry, "sales.orders.placed::Match_Column_Type", baseline_value="D", baseline_survey_at=FIRST)
    _survey(registry, SECOND, retype={("sales", "orders", "order_id"): "bigint"}, drop={("sales", "orders", "placed")})
    rows = _run_drift(registry, entity)[1]
    same = rows["sales.orders.order_id::Match_Column_Type"]
    assert (same["status"], same["measure"], same["measure_text"]) == ("pass", 1, "N")     # integer -> bigint: still N
    assert "Schema_Drift's to report" in same["reason"]
    changed = rows["sales.orders.note::Match_Column_Type"]
    assert (changed["status"], changed["measure"], changed["measure_text"]) == ("fail", 0, "A")
    assert changed["reason"] == f"General type A in the survey of {SECOND}, N at the baseline."
    gone = rows["sales.orders.placed::Match_Column_Type"]
    assert gone["status"] == "fail" and f"The column is not in the survey of {SECOND}" in gone["reason"]


def test_a_baseline_that_does_not_say_where_it_came_from_cannot_be_compared(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND)
    _accepted(registry, "sales.orders::Match_Column_Ct", threshold_value=4)
    r = _run_drift(registry, entity)[1]["sales.orders::Match_Column_Ct"]
    assert r["status"] == "not_established" and r["reason"] == schema_drift.WORDS["no_baseline"]


def test_a_bad_baseline_is_not_established_quoting_it(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND)
    _accepted(registry, "sales.orders::Match_Column_Ct", threshold_value="four", baseline_survey_at=FIRST)
    _accepted(registry, "sales.orders.note::Match_Column_Type", baseline_value="", baseline_survey_at=FIRST)
    rows = _run_drift(registry, entity)[1]
    assert "threshold_value must be a number, got 'four'" in rows["sales.orders::Match_Column_Ct"]["reason"]
    assert "baseline_value must be a general type" in rows["sales.orders.note::Match_Column_Type"]["reason"]


# ── Schema_Conformance ──────────────────────────────────────────────────────────────────────────────────────────

def test_schema_conformance_never_concludes_because_there_is_no_declared_schema(registry, entity):
    _survey(registry, FIRST)
    _survey(registry, SECOND)
    _accepted(registry, "sales.orders::Schema_Conformance")
    r = _run_drift(registry, entity)[1]["sales.orders::Schema_Conformance"]
    assert (r["status"], r["state"]) == ("not_established", "not_measured")
    assert r["reason"] == schema_drift.WORDS["no_declared_schema"]


# ── the drift annotations say the state in words, a first survey carries no score ──────────────────────────────

def test_a_drift_result_is_published_with_its_state_word_and_no_score_when_not_established(registry, entity):
    _survey(registry, FIRST)
    _accepted(registry, "sales.orders::Schema_Drift")
    out, _ = _run_drift(registry, entity)
    note = out["quality_annotations"][0]
    assert note.quality_score is None and "not_established" in note.summary


def test_the_drift_checks_and_dupe_rows_appear_in_curate_review(registry, entity):
    _survey(registry, FIRST)
    _profile(registry, entity)
    _propose(registry)
    group = review.build_review(registry, SLUG, schema="sales", table="orders")
    rows = group["groups"][0]["rows"]
    assert {"Dupe_Rows", "Schema_Drift"} <= {r["test_type"] for r in rows}
    assert {r["test_type"]: r["dimension"] for r in rows}["Dupe_Rows"] == "uniqueness"


def test_the_opt_in_list_names_the_two_new_table_comparison_types():
    assert {"Match_Column_Ct", "Match_Column_Type"} <= set(review.table_comparison_types())
    assert "Dupe_Rows" not in review.table_comparison_types()
