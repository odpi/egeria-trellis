"""Q4 port order step 8 (docs/design-notes/DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md): value hashes, off by default.

Two tiers. The default tier runs everywhere and touches no database: the SQL builders parse as Postgres, the pins hold, the key
inference ranks as upstream's SQL does, the profile takes hashes only when asked and says why when it could not. The
`RE_HASH_TEST_PG_DSN` tier runs the generated SQL on a real Postgres the caller provides (a private throwaway cluster, never the
shared registry) and compares every hash with the reference implementation below, which restates TestGen's `value_hash.py`
(SHA-256-pinned in `value_hashes.UPSTREAM_SHA256`).
"""
from __future__ import annotations

import calendar
import hashlib
import os
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal

import pytest
import sqlglot

from resource_explorer.surveyors.database.quality import value_hashes as vh

# ── the reference (TestGen's value_hash.py, restated) ────────────────────────────────────────────────────────────

_WIDE = Context(prec=80, rounding=ROUND_HALF_UP)
_BOUND = Decimal("99999999999999999999999999999999.9999995")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _wall(value):
    if not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def ref_canonical(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int | Decimal | float) and not isinstance(value, bool):
        number = Decimal(value)
        if isinstance(value, float):
            number = number.quantize(Decimal("1e-9"), context=_WIDE)
        return str(int(number.quantize(Decimal("1e-6"), context=_WIDE).scaleb(6, context=_WIDE)))
    w = _wall(value)
    return f"{w.year:04d}-{w:%m-%d %H:%M:%S}"


def ref_hashable(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float):
        return value == value and abs(value) < 1e29
    if isinstance(value, Decimal):
        return value.is_finite() and value.copy_abs() < _BOUND
    return True


def ref_hash(text: str) -> int:
    return int(_md5(text)[:14], 16)


def ref_multiset(values):
    hashes = [ref_hash(ref_canonical(v)) for v in values if ref_hashable(v)]
    return sum(hashes) if hashes else None


def ref_keyed(rows):
    hashes = [ref_hash(_md5(ref_canonical(k)) + _md5(ref_canonical(v))) for k, v in rows
              if ref_hashable(v) and ref_hashable(k)]
    return sum(hashes) if hashes else None


def ref_epoch(values):
    seconds = [calendar.timegm(_wall(v).timetuple()) for v in values if v is not None]
    return sum(seconds) if seconds else None


# ── default tier: nothing connects ──────────────────────────────────────────────────────────────────────────────

def _col(name, gtype, db_type=""):
    return vh.HashColumn(name, gtype, db_type)


@pytest.mark.parametrize("column", [
    _col("t", "A", "text"), _col("c", "A", "character(10)"), _col("n", "N", "integer"), _col("d", "N", "numeric(12,2)"),
    _col("f", "N", "double precision"), _col("m", "N", "money"), _col("dt", "D", "date"), _col("ts", "D", "timestamp"),
    _col("tz", "D", "timestamp with time zone"), _col('we"ird', "A", "text")])
def test_every_hash_term_parses_as_postgres(column):
    key = _col("id", "N", "integer")
    for sql in (f"SELECT SUM({vh.multiset_hash_term(column)}) FROM t", f"SELECT SUM({vh.keyed_hash_term(key, column)}) FROM t"):
        sqlglot.parse_one(sql, read="postgres")
    if column.general_type == "D":
        sqlglot.parse_one(f"SELECT SUM({vh.epoch_sum_term(column)}) FROM t", read="postgres")


def test_the_column_statement_takes_an_epoch_sum_only_for_a_date_column_and_quotes_its_names():
    sql, aliases = vh.build_column_statement("s", 't"x', [_col("a", "A", "text"), _col("d", "D", "date")])
    assert aliases == {"c0__multiset_hash": ("a", "multiset_hash"), "c1__multiset_hash": ("d", "multiset_hash"),
                       "c1__epoch_sum": ("d", "epoch_sum")}
    assert 'FROM "s"."t""x"' in sql and sql.count("SUM(") == 3
    sqlglot.parse_one(sql, read="postgres")


def test_a_char_column_is_right_trimmed_before_it_is_hashed_and_a_float_goes_through_the_exact_split():
    assert "RTRIM(" in vh.multiset_hash_term(_col("c", "A", "character(10)"))
    assert "RTRIM(" not in vh.multiset_hash_term(_col("t", "A", "text"))
    assert "DIV(" in vh.multiset_hash_term(_col("f", "N", "real")) and "DIV(" not in vh.multiset_hash_term(_col("n", "N", "integer"))
    assert "AT TIME ZONE 'UTC'" in vh.epoch_sum_term(_col("tz", "D", "timestamp with time zone"))


def test_the_upstream_sources_this_port_was_written_from_are_pinned():
    assert set(vh.UPSTREAM_SHA256) == {"common/value_hash.py", "common/database/flavor/flavor_service.py",
                                       "common/database/flavor/redshift_flavor_service.py",
                                       "common/database/flavor/postgresql_flavor_service.py",
                                       "template/profiling/infer_key_columns.sql"}
    assert all(len(v) == 64 and set(v) <= set("0123456789abcdef") for v in vh.UPSTREAM_SHA256.values())


def _kr(table="t", column="c", gtype="N", **kw):
    row = dict(schema_name="s", table_name=table, column_name=column, general_type=gtype, state="measured", record_ct=100,
               value_ct=100, null_value_ct=0, distinct_value_ct=100, multiset_hash="5", max_length=None, avg_length=None,
               fractional_sum=None, functional_data_type=None)
    row.update(kw)
    return row


def test_a_key_is_the_first_ranked_column_distinct_in_every_row_and_hashed():
    types = {("s", "t", "a"): "text", ("s", "t", "id"): "integer", ("s", "t", "d"): "date", ("s", "t", "f"): "double precision"}
    pos = {("s", "t", "a"): 1, ("s", "t", "id"): 2, ("s", "t", "d"): 3, ("s", "t", "f"): 4}
    rows = [_kr(column="a", gtype="A", max_length=10, avg_length=5.0), _kr(column="id"), _kr(column="d", gtype="D"),
            _kr(column="f")]
    # a number outranks a date outranks text; a binary float is excluded
    assert vh.infer_key_columns(rows, types, pos) == {("s", "t"): "id"}
    assert vh.infer_key_columns(rows[:1] + rows[2:3], types, pos) == {("s", "t"): "d"}
    # an ID functional type outranks the type order; a surrogate ranks after a natural one
    rows[0]["functional_data_type"] = "ID"
    assert vh.infer_key_columns(rows, types, pos) == {("s", "t"): "a"}
    rows[0]["functional_data_type"] = "ID-SK"
    assert vh.infer_key_columns(rows, types, pos) == {("s", "t"): "a"}          # a surrogate still outranks a non-ID
    rows[1]["functional_data_type"] = "ID"
    assert vh.infer_key_columns(rows, types, pos) == {("s", "t"): "id"}         # ... but ranks after a natural ID


@pytest.mark.parametrize("override", [dict(distinct_value_ct=99), dict(null_value_ct=1), dict(multiset_hash=None),
                                      dict(state="not_measured"), dict(record_ct=0, value_ct=0, distinct_value_ct=0)])
def test_a_column_that_is_not_distinct_everywhere_or_not_hashed_is_not_a_key(override):
    assert vh.infer_key_columns([_kr(**override)], {("s", "t", "c"): "integer"}, {}) == {}


def test_long_text_a_non_date_time_and_a_wide_scale_number_are_not_keys():
    types = {("s", "t", "a"): "text", ("s", "t", "ts"): "timestamp", ("s", "t", "n"): "numeric(12,8)", ("s", "t", "m"): "numeric"}
    assert vh.infer_key_columns([_kr(column="a", gtype="A", max_length=101)], types, {}) == {}
    assert vh.infer_key_columns([_kr(column="ts", gtype="D")], types, {}) == {}
    assert vh.infer_key_columns([_kr(column="n")], types, {}) == {}
    assert vh.infer_key_columns([_kr(column="m", fractional_sum="2.5")], types, {}) == {}      # no scale, holds fractions
    assert vh.infer_key_columns([_kr(column="m", fractional_sum="0")], types, {}) == {("s", "t"): "m"}


# ── real-Postgres tier ──────────────────────────────────────────────────────────────────────────────────────────

DSN = os.environ.get("RE_HASH_TEST_PG_DSN")
pg = pytest.mark.skipif(not DSN, reason="RE_HASH_TEST_PG_DSN (a private throwaway Postgres) is not set")


@pytest.fixture
def pgconn():
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS hash_t")
    cur.execute("""CREATE TABLE hash_t (id integer, txt text, ch char(8), n integer, nd numeric(12,2), big numeric, f double precision,
                   r real, m money, d date, ts timestamp, tz timestamptz)""")
    yield conn
    cur.execute("DROP TABLE IF EXISTS hash_t")
    conn.close()


def _insert(conn, rows):
    cur = conn.cursor()
    cur.executemany("INSERT INTO hash_t VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)


def _run(conn, sql):
    import psycopg2.extras

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql)
    return cur.fetchall()[0]


@pg
def test_the_generated_sql_gives_the_reference_hash_for_every_kind_of_value(pgconn):
    utc = UTC
    rows = [
        (1, "alpha", "ab", -7, Decimal("12.35"), Decimal("1234567890123456789.1234565"), 0.1, 0.5, "$1.23", date(2020, 2, 29),
         datetime(2020, 1, 2, 3, 4, 5), datetime(2020, 1, 2, 3, 4, 5, tzinfo=utc)),
        (2, "Ünïcode ✓", "x ", 0, Decimal("-0.01"), Decimal("-1.5"), -2.5e-9, -1.25, "-$99.99", date(1999, 12, 31),
         datetime(1999, 12, 31, 23, 59, 59), datetime(1999, 12, 31, 23, 59, 59, tzinfo=utc)),
        (3, "", None, 2147483647, Decimal("0"), Decimal("0"), 1e28, 3.4e30, None, date(1, 1, 1), datetime(1, 1, 1), None),
        (4, None, "   ", None, None, None, None, None, None, None, None, None),
        (5, "alpha", "ab", -7, Decimal("99999999.99"), Decimal("1E31"), -123456789.123456789, 16777216.0, "$0.00",
         date(2100, 6, 30), datetime(2100, 6, 30, 12, 0, 0, 999999), datetime(2100, 6, 30, 12, 0, 0, tzinfo=utc)),
    ]
    _insert(pgconn, rows)
    cols = {"txt": ("A", "text", 1), "ch": ("A", "character(8)", 2), "n": ("N", "integer", 3), "nd": ("N", "numeric(12,2)", 4),
            "big": ("N", "numeric", 5), "f": ("N", "double precision", 6), "r": ("N", "real", 7), "m": ("N", "money", 8),
            "d": ("D", "date", 9), "ts": ("D", "timestamp without time zone", 10),
            "tz": ("D", "timestamp with time zone", 11)}
    for name, (gtype, db_type, idx) in cols.items():
        column = vh.HashColumn(name, gtype, db_type)
        values = [r[idx] for r in rows]
        if name == "ch":
            values = [v.rstrip(" ") if v is not None else None for v in values]
        if name == "m":
            values = [None if v is None else Decimal(v.replace("$", "")) for v in values]
        if name == "r":
            values = [None if v is None else float(v) for v in values]
        sql, aliases = vh.build_column_statement("public", "hash_t", [column])
        got = _run(pgconn, sql)
        assert vh._scalar(got["c0__multiset_hash"]) == (None if ref_multiset(values) is None else str(ref_multiset(values))), name
        if gtype == "D":
            assert vh._scalar(got["c0__epoch_sum"]) == str(ref_epoch(values)), name


@pg
def test_the_keyed_hash_matches_the_reference_and_skips_a_null_key_or_value(pgconn):
    rows = [(i, f"v{i}", None, i * 3, None, None, None, None, None, None, None, None) for i in range(1, 6)]
    rows.append((None, "orphan", None, 9, None, None, None, None, None, None, None, None))
    rows.append((7, None, None, None, None, None, None, None, None, None, None, None))
    _insert(pgconn, rows)
    key = vh.HashColumn("id", "N", "integer")
    for column, idx, gtype in (("txt", 1, "A"), ("n", 3, "N")):
        hc = vh.HashColumn(column, gtype, "text" if gtype == "A" else "integer")
        sql, aliases = vh.build_keyed_statement("public", "hash_t", key, [hc])
        want = ref_keyed([(r[0], r[idx]) for r in rows])
        assert vh._scalar(_run(pgconn, sql)["k0"]) == str(want), column


# ── the profile and the checks, over fakes (default tier) ───────────────────────────────────────────────────────

from decimal import Decimal as _D  # noqa: E402

from resource_explorer.surveyors.database.quality import catalogue as cat  # noqa: E402
from resource_explorer.surveyors.database.quality.value_profile import ProfileBounds  # noqa: E402
from tests.test_quality_step_7_text_shape import PROFILE_WITH_TEXT, TextConn  # noqa: E402,F401
from tests.test_value_profile_quality_checks import (  # noqa: E402,F401
    SLUG,
    PgError,
    _by_key,
    _profile,
    _propose,
    _rows,
    entity,
    registry,
)

BIG = 123456789012345678901234567890       # far past 64 bits: a hash is exact decimal text


class HashConn(TextConn):
    """A TextConn that also answers the hash statements: the column statement from `hashes`, the keyed one from `keyed`."""

    def __init__(self, *a, hashes=None, keyed=None, hash_fail=None, keyed_fail=None, **kw):
        super().__init__(*a, **kw)
        self.hashes, self.keyed, self.hash_fail, self.keyed_fail = hashes or {}, keyed or {}, hash_fail, keyed_fail

    def execute_query_isolated(self, sql, params=()):
        import re

        if "AS k0" in sql and "multiset_hash" not in sql:
            self.statements.append(sql)
            if self.keyed_fail:
                raise self.keyed_fail
            names = re.findall(r'"(\w+)"\) AS k(\d+)|AS k(\d+)', sql)
            order = [c for c in self.keyed_order]
            return [{f"k{i}": self.keyed.get(c) for i, c in enumerate(order)}]
        if "__multiset_hash" not in sql:
            return super().execute_query_isolated(sql, params)
        self.statements.append(sql)
        assert self.in_read_only, "the hashes were read outside a read-only transaction"
        if self.hash_fail:
            raise self.hash_fail
        row = {}
        for alias, i, measure in re.findall(r"AS (c(\d+)__(multiset_hash|epoch_sum))", sql):
            row[alias] = self.hashes.get((int(i), measure))
        return [row]


def _hash_conn(**kw):
    conn = HashConn(profile=PROFILE_WITH_TEXT, **kw)
    conn.keyed_order = ["note", "placed"]
    return conn


# orders' columns in hashing order: order_id(N), note(A), placed(D)
HASHES = {(0, "multiset_hash"): _D(BIG), (1, "multiset_hash"): _D(7), (2, "multiset_hash"): _D(99), (2, "epoch_sum"): _D(1_700_000_000)}


def test_value_hashes_are_off_by_default_and_the_profile_says_so(registry, entity):
    conn = _hash_conn(hashes=HASHES)
    out, _ = _profile(registry, entity, conn=conn)
    assert not any("__multiset_hash" in s for s in conn.statements)
    row = _rows(registry, "database_value_profiles")["sales", "orders", "order_id"]
    assert all(row[m] is None for m in ("multiset_hash", "epoch_sum", "keyed_hash", "key_column_name", "hash_state"))
    assert out["value_profile"]["hashes"] == "off" and out["value_profile"]["hashes_not_measured"] == []


def test_the_default_setting_is_off():
    from resource_explorer.config import ValueProfileSettings

    assert ValueProfileSettings().value_hashes is False


def _profile_with_hashes(registry, entity, conn):
    from resource_explorer.surveyors.database.quality.value_profile import run_value_profile
    from tests.test_value_profile_quality_checks import BOUNDS, PROFILED_AT, _connect

    return run_value_profile(entity, registry, _connect(conn), bounds=BOUNDS, surveyed_at=PROFILED_AT, ran_as="surveyor",
                             clock=lambda: 0.0, hashes=True), conn


def test_asked_for_the_profile_stores_exact_hashes_the_inferred_key_and_keyed_hashes(registry, entity):
    conn = _hash_conn(hashes=HASHES, keyed={"note": _D(5), "placed": _D(6)})
    out, _ = _profile_with_hashes(registry, entity, conn)
    rows = _rows(registry, "database_value_profiles")
    order_id, note, placed = (rows["sales", "orders", c] for c in ("order_id", "note", "placed"))
    assert order_id["multiset_hash"] == str(BIG) and order_id["hash_state"] == "taken"       # exact text, past 64 bits
    assert note["multiset_hash"] == "7" and placed["epoch_sum"] == "1700000000"
    assert order_id["epoch_sum"] is None                                                       # only a date has one
    # order_id is distinct in every row, with a hash: the table's key; every other hashed column is keyed by it
    assert {order_id["key_column_name"], note["key_column_name"], placed["key_column_name"]} == {"order_id"}
    assert order_id["keyed_hash"] is None and note["keyed_hash"] == "5" and placed["keyed_hash"] == "6"
    assert rows["sales", "orders", "payload"]["hash_state"] is None                            # jsonb is not hashed
    assert out["value_profile"]["hashes"] == "on" and out["value_profile"]["hashes_not_measured"] == []
    assert sum("__multiset_hash" in s for s in conn.statements) == 1


def test_a_table_that_could_not_be_hashed_keeps_its_other_measures_and_says_why(registry, entity):
    conn = _hash_conn(hash_fail=PgError("canceling statement due to statement timeout", "57014"))
    out, _ = _profile_with_hashes(registry, entity, conn)
    order_id = _rows(registry, "database_value_profiles")["sales", "orders", "order_id"]
    assert order_id["value_ct"] == 1000 and order_id["multiset_hash"] is None and order_id["hash_state"] == "not_measured"
    (gap,) = out["value_profile"]["hashes_not_measured"]
    assert gap["table_name"] == "orders" and "hashes not measured" in gap["reason"] and "order_id" in gap["columns"]


def test_the_hash_budget_is_its_own():
    conn = _hash_conn()
    found, reason = vh.measure_table(conn, "s", "t", [_col("a", "A", "text")], ProfileBounds(1, hash_budget_seconds=0))
    assert found == {} and "exceeded" in reason and not conn.statements
    assert ProfileBounds(1).hash_budget_seconds == 300.0


def _watch(registry):
    from resource_explorer.surveyors.database.quality import table_comparison

    table_comparison.set_watch(registry, SLUG, "sales", "orders", True, by="steward")


def test_with_hashes_off_a_hash_type_is_not_established_and_says_how_to_turn_them_on(registry, entity):
    _watch(registry)
    _profile(registry, entity, conn=_hash_conn(hashes=HASHES))
    _propose(registry)
    row = _by_key(registry, "database_quality_proposals")["sales.orders.note::Match_Hash_Text"]
    assert row["state"] == "not_established" and "VALUE_PROFILE_HASHES" in row["reason"]
    # a column of another general type is a real no, not a flood of not_established
    assert "sales.orders.note::Match_Hash_Num" not in _by_key(registry, "database_quality_proposals")


def test_with_hashes_on_the_types_propose_with_the_stored_hash_as_the_baseline(registry, entity):
    _watch(registry)
    _profile_with_hashes(registry, entity, _hash_conn(hashes=HASHES, keyed={"note": _D(5), "placed": _D(6)}))
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    assert rows["sales.orders.note::Match_Hash_Text"]["state"] == "proposed"
    assert rows["sales.orders.note::Match_Hash_Text"]["params_json"]["baseline_value"] == "7"
    assert rows["sales.orders.order_id::Match_Hash_Num"]["params_json"]["baseline_value"] == str(BIG)
    keyed = rows["sales.orders.note::Match_Keyed_Hash_Text"]["params_json"]
    assert keyed["baseline_value"] == "5" and keyed["key_column_name"] == "order_id"
    assert rows["sales.orders.placed::Match_Epoch_Sum"]["params_json"]["baseline_sum"] == "1700000000"
    # the key is not keyed against itself: upstream's NULL keyed_hash is a "no"
    assert "sales.orders.order_id::Match_Keyed_Hash_Num" not in rows


def test_the_hash_conditions_are_exact_whole_numbers_pinned_to_the_vendored_text():
    catalogue = cat.load_catalogue()
    for name in cat.STEP_8_TEST_TYPES:
        t = catalogue[name]
        assert t.is_table_comparison and t.null_measure_fails and t.measure_is_exact and t.test_operator == "<>"
        assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(t.selection_criteria), name
    limit = cat.threshold(catalogue["Match_Hash_Text"], {"baseline_value": str(BIG)})
    assert limit == _D(BIG) and cat.fails(catalogue["Match_Hash_Text"], _D(BIG + 1), limit)
    assert not cat.fails(catalogue["Match_Hash_Text"], _D(BIG), limit)
    with pytest.raises(cat.ParameterError, match="whole number"):
        cat.threshold(catalogue["Match_Hash_Text"], {"baseline_value": "1.5"})
    assert cat.threshold(catalogue["Match_Epoch_Sum"], {"baseline_sum": "1700000000"}) == _D(1_700_000_000)


def test_a_hash_type_cannot_be_rendered_without_the_columns_type_or_the_key():
    catalogue = cat.load_catalogue()
    with pytest.raises(cat.ParameterError, match="type was not given"):
        cat.render_measure(catalogue["Match_Hash_Text"], "c", {"baseline_value": "1"}, "2026-10-29 00:00:00")
    with pytest.raises(cat.ParameterError, match="key column"):
        cat.render_measure(catalogue["Match_Keyed_Hash_Text"], "c", {"baseline_value": "1"}, "2026-10-29 00:00:00",
                           hash_column=_col("c", "A", "text"))
    sql = cat.render_measure(catalogue["Match_Epoch_Sum"], "d", {"baseline_sum": "1"}, "2026-10-29 00:00:00",
                             hash_column=_col("d", "D", "date"))
    assert sql.startswith("SUM(CASE WHEN") and "EXTRACT(EPOCH" in sql


STEP_8_COLUMNS = ("multiset_hash", "epoch_sum", "keyed_hash", "key_column_name", "hash_state", "distinct_value_hash",
                  "frequency_state")


def test_the_step_8_ddl_is_seven_nullable_columns_and_the_record_names_each():
    from pathlib import Path

    from resource_explorer.registry import ProjectRegistry

    assert {c for t, c, _ in ProjectRegistry.VALUE_PROFILE_COLUMNS if c in STEP_8_COLUMNS} == set(STEP_8_COLUMNS)
    record = (Path(__file__).resolve().parents[1] / "docs" / "ddl-value-profile-step8.md").read_text(encoding="utf-8")
    assert all(f"`{c}`" in record for c in STEP_8_COLUMNS) and "lock_timeout" in record and "IF NOT EXISTS" in record
    assert "database_value_profile_frequent_values" in record


def test_a_keyed_hash_that_could_not_be_read_leaves_the_column_not_established_for_the_keyed_types(registry, entity):
    conn = _hash_conn(hashes=HASHES, keyed_fail=PgError("canceling statement due to statement timeout", "57014"))
    out, _ = _profile_with_hashes(registry, entity, conn)
    note = _rows(registry, "database_value_profiles")["sales", "orders", "note"]
    assert note["multiset_hash"] == "7" and note["keyed_hash"] is None and note["hash_state"] == "keyed_not_measured"
    assert out["value_profile"]["hashes_not_measured"] and "keyed hashes not measured" in out["value_profile"]["hashes_not_measured"][0]["reason"]
    row = {**note, "general_type": "A"}
    assert cat.PREDICATES["Match_Keyed_Hash_Text"].rule(row) is None          # not established, never a silent no
    assert cat.PREDICATES["Match_Hash_Text"].rule(row) is True                # the multiset hash is fine
