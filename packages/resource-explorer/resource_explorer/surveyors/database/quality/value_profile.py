"""`postgres_value_profile`: measured (not estimated) per-column counts and date bounds, whole table.

DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §4. One aggregate SELECT per table, assembled from the vendored
measure lines (`configdata/quality/testgen/postgresql/profile_measures.sql`) with each column's blocks chosen by
its `general_type`. Results are stored in `database_value_profiles` under the catalogue's own column names.

No sampling in slice 1: a table is profiled whole, or not at all. There is NO size cap (owner, design 2.3): the one
bound is a time budget per TABLE (`config.ValueProfileSettings.table_budget_seconds`, 5 minutes, the total across its
chunks). Each chunk's statement timeout is whatever is left of that budget, so the budget is the effective bound: a
chunk that needs 100 s inside it is measured. Past the budget the whole table is `not_measured` with "profile
incomplete · exceeded 5 min · counts not measured", every measure NULL, so no check is proposed for it; an error
other than running out of time (a privilege error is `not_permitted`, anything else `not_measured` with Postgres's
words) is classified first, whatever the clock says. A view, materialized view or foreign table is
`not_applicable`. Every column the inventory names gets a row; a column that was not profiled has every measure
NULL, never zeros.

Profile 1b (DESIGN-QUALITY-PROFILE-1B.md) adds the whole-table numeric, length, boolean and date-days measures, still
in the same one SELECT per table. Two of them depend on the column's declared scale (upstream's `is_N_decimal`,
`is_N_exact_sum`), which the stored inventory does not keep, so one catalog read (`general_type.NUMERIC_CHARS_QUERY`)
beside the table facts supplies it. When that read fails, `fractional_sum` and `sum_value` are not taken for any
column and the batch says so (`numeric_chars`); every other measure is unaffected.

Each read runs with the session time zone set to UTC (`begin_read`, shared with the check run), so a
`timestamptz` date is printed, compared with `{RUN_DATE}` and baselined on one clock whatever the account's default.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache

from resource_explorer.surveyors.database.quality.catalogue import (
    POSTGRES_DIR,
    column_num,
    expand_templated_functions,
    quote_identifier,
)
from resource_explorer.surveyors.database.quality.general_type import (
    NUMERIC_CHARS_QUERY,
    exact_sum,
    is_n_decimal,
)
from resource_explorer.surveyors.database.quality.general_type import (
    general_type as derive_general_type,
)

log = logging.getLogger(__name__)

PROFILE_TABLE = "database_value_profiles"
FREQUENT_VALUES_TABLE = "database_value_profile_frequent_values"
ANALYSIS_STEP = "postgres_value_profile"
PROFILE_MEASURES_SQL = POSTGRES_DIR / "profile_measures.sql"

#: Profile 1b's measures (Q4 port order step 3), each in upstream's whole-table block, never sampled.
PROFILE_1B_MEASURES = ("before_5yr_date_ct", "date_days_present", "min_length", "max_length", "avg_length",
                       "zero_value_ct", "min_value", "max_value", "avg_value", "stdev_value", "fractional_sum",
                       "sum_value", "boolean_true_ct")
#: The measures a profile row carries, in the vendored names. `record_ct` is per table.
COLUMN_MEASURES = ("value_ct", "distinct_value_ct", "null_value_ct", "zero_length_ct", "filled_value_ct",
                   "min_date", "max_date", "future_date_ct", "before_20yr_date_ct") + PROFILE_1B_MEASURES \
    + ("distinct_std_value_ct", "min_text", "max_text")
#: Step 8's value hashes (value_hashes.py), exact decimal text. `hash_state` says whether they were asked for and read:
#: NULL (off, or a row from before step 8), "taken", "not_measured".
HASH_MEASURES = ("multiset_hash", "epoch_sum", "keyed_hash", "key_column_name", "hash_state", "distinct_value_hash",
                 "frequency_state")
#: Step 7's sampled text-shape measures (text_shape.py): a text column's, one sampled statement per column.
TEXT_SHAPE_MEASURES = ("sample_value_ct", "sample_pattern_value_ct", "lead_space_ct", "quoted_value_ct",
                       "includes_digit_ct", "upper_case_ct", "lower_case_ct", "non_alpha_ct", "non_printing_ct",
                       "numeric_ct", "date_ct", "embedded_space_ct", "avg_embedded_spaces", "std_pattern_match",
                       "distinct_pattern_ct", "frequent_patterns")
DATE_MEASURES = frozenset({"min_date", "max_date"})
#: Measures that are not counts: floats as the target returned them, never truncated to an integer.
FLOAT_MEASURES = frozenset({"avg_length", "min_value", "max_value", "avg_value", "stdev_value"})
#: NUMERIC sums kept as the exact decimal text the target printed (a float would round them; Match_Sum and
#: Match_Fractional_Sum compare exactly).
EXACT_MEASURES = frozenset({"fractional_sum", "sum_value"})
#: The counts the published annotation carries in `value_count`: slice 1's, unchanged by profile 1b. Profile 1b's
#: measures stay in RE's own rows (the checks read them there): publishing them is not part of this step, and
#: adding them would change every published measure's content hash, re-publishing every unchanged column once.
PUBLISHED_COUNTS = ("record_ct", "value_ct", "distinct_value_ct", "null_value_ct", "zero_length_ct",
                    "filled_value_ct")
#: Measures taken against the profile's own instant (`{RUN_DATE}`), so they move with the clock on a table nobody
#: wrote to. RE's own rows keep them (the Future_Date check reads `future_date_ct`, Recency's inference
#: `before_20yr_date_ct`), but the published annotation
#: does not carry them in `value_count`, and the profile publish reshape never hashes them (architect ruling
#: 2026-10-24): `profileDates` carries the max date and the as-of instead, so a reader can tell.
RELATIVE_TO_NOW_MEASURES = frozenset({"future_date_ct", "before_20yr_date_ct", "before_5yr_date_ct"})

#: Postgres's target-list limit is 1664 entries; columns are profiled in chunks well under it (since profile 1b a
#: column contributes at most ten measures, a decimal number: 150 x 10 + record_ct = 1501). Each chunk is its own scan.
COLUMNS_PER_QUERY = 150

_AS = re.compile(r"^(?P<expr>.*?)\s+AS\s+(?P<alias>[a-z0-9_]+),?\s*$", re.DOTALL)


@lru_cache(maxsize=1)
def measure_blocks() -> dict[str, tuple[tuple[str, str], ...]]:
    """{TG-IF flag: ((expression, measure name), ...)} from the vendored subset, each expression verbatim."""
    blocks: dict[str, list[tuple[str, str]]] = {}
    flag: str | None = None
    pending: list[str] = []
    for line in PROFILE_MEASURES_SQL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("-- TG-IF "):
            flag = stripped[len("-- TG-IF "):].strip()
            blocks.setdefault(flag, [])
            continue
        if stripped == "-- TG-ENDIF":
            if pending:
                raise ValueError(f"profile_measures.sql: unterminated measure in block {flag}: {pending}")
            flag = None
            continue
        if flag is None or not stripped or stripped.startswith("--"):
            continue
        pending.append(line)
        match = _AS.match("\n".join(pending).strip())
        if match and re.search(r"\bAS\s+[a-z0-9_]+,?\s*$", line):
            blocks[flag].append((match.group("expr").strip(), match.group("alias")))
            pending = []
    return {k: tuple(v) for k, v in blocks.items()}


@dataclass(frozen=True)
class ColumnChars:
    """What a column's profile depends on beyond its name: its general_type, and for a number the two flags upstream
    reads from the catalog (`is_N_decimal`, `is_N_exact_sum`) and whether it is money (read through ::NUMERIC).
    `numeric_chars_read` is False when the catalog read failed: the two flags are then unknown, not false."""

    general_type: str
    is_decimal: bool = False
    exact_sum: bool = False
    is_money: bool = False
    numeric_chars_read: bool = True


def flags_for(general_type: str | ColumnChars) -> list[str]:
    """The vendored blocks that apply to a column (upstream's flag meanings). A bare general_type means no numeric
    characteristics are known: neither decimal nor exact-summed."""
    chars = general_type if isinstance(general_type, ColumnChars) else ColumnChars(general_type)
    gtype = chars.general_type
    flags = ["is_type_A", "is_A_text"] if gtype == "A" else ["is_not_A"]
    if gtype not in ("A", "X"):
        flags.append("is_distinct_countable")
    if gtype == "D":
        flags.append("is_type_D")
    if gtype == "N":
        flags.append("is_type_N")
        if chars.is_decimal:
            flags.append("is_N_decimal")
        if chars.exact_sum:
            flags.append("is_N_exact_sum")
    if gtype == "B":
        flags.append("is_type_B")
    return flags


def read_numeric_chars(conn) -> dict[tuple[str, str, str], tuple[str, int | None]] | None:
    """{(schema, table, column): (data_type, numeric_scale)} for every numeric column, from
    `information_schema.columns` as upstream reads it; None when the catalog could not be read. Ends its own
    transaction."""
    try:
        rows = conn.execute_query_isolated(NUMERIC_CHARS_QUERY, None)
    except Exception as exc:
        log.warning("value profile: could not read numeric scales: %s", exc)
        return None
    finally:
        conn.end_transaction()
    return {(r["schema_name"], r["table_name"], r["column_name"]):
            (r.get("data_type") or "", None if r.get("numeric_scale") is None else int(r["numeric_scale"]))
            for r in rows}


def column_chars(schema: str, table: str, column: str, gtype: str,
                 numeric: dict[tuple[str, str, str], tuple[str, int | None]] | None) -> ColumnChars:
    """A column's ColumnChars from its general_type and the catalog read (`read_numeric_chars`; None = not read)."""
    if gtype != "N":
        return ColumnChars(gtype)
    if numeric is None or (schema, table, column) not in numeric:
        return ColumnChars(gtype, numeric_chars_read=False)
    data_type, scale = numeric[(schema, table, column)]
    return ColumnChars(gtype, is_decimal=is_n_decimal(gtype, data_type, scale),
                       exact_sum=exact_sum(gtype, data_type, scale), is_money=data_type.lower() == "money")


def run_date_text(surveyed_at: str) -> str:
    """The `{RUN_DATE}` literal: the profile's own instant as 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    try:
        instant = datetime.fromisoformat(surveyed_at)
    except ValueError:
        instant = datetime.now(UTC)
    if instant.tzinfo is not None:
        instant = instant.astimezone(UTC)
    return instant.strftime("%Y-%m-%d %H:%M:%S")


def as_of_text(surveyed_at: str) -> str:
    """The profile's own instant as ISO UTC with Z ('YYYY-MM-DDTHH:MM:SSZ'): the instant `{RUN_DATE}` measured
    against, published as `profileDates.asOf`."""
    return run_date_text(surveyed_at).replace(" ", "T") + "Z"


def _fill(expr: str, column: str, run_date: str, is_money: bool = False) -> str:
    # Upstream's templated functions are expanded in the vendored text first, so the name never passes through
    # the expansion. The template quotes "{COL_NAME}" itself, so the name goes in with only its double quotes
    # doubled.
    expr = expand_templated_functions(expr)
    return (expr.replace('"{COL_NAME}"', quote_identifier(column)).replace("{COL_NUM}", column_num(column, is_money))
            .replace("{RUN_DATE}", run_date))


def build_table_query(schema: str, table: str, columns: list[tuple[str, str | ColumnChars]], run_date: str
                      ) -> tuple[str, dict[str, tuple[int, str]]]:
    """One SELECT over `schema.table` for `columns` [(name, general_type or ColumnChars)]. Returns the SQL and
    {result alias: (column index, measure name)}; `record_ct` comes back under its own name."""
    blocks = measure_blocks()
    items = [f"{expr} AS record_ct" for expr, _ in blocks.get("is_table", ())]
    aliases: dict[str, tuple[int, str]] = {}
    for i, (name, chars) in enumerate(columns):
        chars = chars if isinstance(chars, ColumnChars) else ColumnChars(chars)
        for flag in flags_for(chars):
            for expr, measure in blocks.get(flag, ()):
                alias = f"c{i}__{measure}"
                items.append(f"{_fill(expr, name, run_date, chars.is_money)} AS {alias}")
                aliases[alias] = (i, measure)
    sql = "SELECT\n" + ",\n".join(items) + f"\nFROM {quote_identifier(schema)}.{quote_identifier(table)}"
    return sql, aliases


def _value(measure: str, value):
    if value is None:
        return None
    if measure in DATE_MEASURES:
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
    if measure in EXACT_MEASURES:
        return str(value)
    if measure in FLOAT_MEASURES:
        return float(value)
    if isinstance(value, Decimal):
        return int(value)
    return int(value) if isinstance(value, (int, float)) else value


def classify_error(exc: Exception) -> str:
    """not_permitted for a privilege error, not_measured for anything else (timeouts included)."""
    code = getattr(exc, "pgcode", None)
    text = str(exc).lower()
    if code == "42501" or "permission denied" in text:
        return "not_permitted"
    return "not_measured"


def error_text(exc: Exception) -> str:
    return " ".join(str(exc).split()) or type(exc).__name__


@dataclass
class ProfileBounds:
    #: The per-statement timeout of a QUALITY CHECK (`run_checks`). The value profile does not read it: its
    #: statements are bounded by what is left of `table_budget_seconds`.
    statement_timeout_seconds: int
    #: The whole-table time budget (owner, 2026-10-21: "5 minutes per table"): the total across a table's chunks,
    #: not a timeout on each chunk. A table that has not finished in this time is "profile incomplete".
    table_budget_seconds: float = 300.0
    #: Dupe_Rows' own per-table budget (owner, 2026-10-26, Q4 section 7 question 3: "its own per-table budget of 5
    #: minutes"). Separate from the profile's: a GROUP BY over every column does not fit inside it.
    dupe_rows_budget_seconds: float = 300.0
    #: The exact percentiles of a table's number columns, their own statement and budget (percentiles.py; owner,
    #: 2026-10-29). Not part of `table_budget_seconds`: running out leaves every other measure in place.
    percentile_budget_seconds: float = 300.0
    #: A table's text columns' sampled text-shape reads, one statement per column inside this one budget
    #: (text_shape.py; step 7). Not part of `table_budget_seconds`: running out leaves every other measure in place.
    text_shape_budget_seconds: float = 300.0
    #: A table's value hashes (value_hashes.py; step 8), off by default: the column statements and the keyed statement share
    #: this budget. Not part of `table_budget_seconds`.
    hash_budget_seconds: float = 300.0
    #: A table's eligible text columns' frequency analysis (frequent_values.py; step 8b), one statement per column in this one
    #: budget. Not part of `table_budget_seconds`.
    frequency_budget_seconds: float = 300.0


def incomplete_reason(budget_seconds: float) -> str:
    """The words a table stopped at its time budget carries (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md 2.3)."""
    return f"profile incomplete · exceeded {budget_seconds / 60:g} min · counts not measured"


def _empty_row(schema: str, table: str, column: str, gtype: str, state: str, reason: str, ran_as: str) -> dict:
    row = {"schema_name": schema, "table_name": table, "column_name": column, "general_type": gtype,
           "record_ct": None, "sample_ratio": None, "state": state, "reason": reason, "ran_as": ran_as}
    row.update({m: None for m in COLUMN_MEASURES})
    row.update(functional_data_type=None, functional_table_type=None, percentile_25=None, percentile_50=None,
               percentile_75=None)
    row.update({m: None for m in TEXT_SHAPE_MEASURES})
    row.update({m: None for m in HASH_MEASURES})
    return row


def _table_outcome(schema: str, table: str, state: str, reason: str) -> dict:
    # The names travel apart as well as dotted: a dot inside a name must not change the table's `item_key`.
    return {"table": f"{schema}.{table}", "schema_name": schema, "table_name": table, "state": state,
            "reason": reason}


@dataclass(frozen=True)
class TableFacts:
    """What the catalog says about one table before it is read: its row estimate (None when never analysed),
    whether it is a partitioned parent, and whether row-level security would filter what this account sees."""

    estimate: int | None
    partitioned: bool
    rls_filters: bool


#: One catalog read for every table. `rls_filters`: RLS is on and this account neither bypasses it (superuser,
#: BYPASSRLS) nor owns the table without FORCE ROW LEVEL SECURITY, so COUNT(*) would count only its rows.
TABLE_FACTS_SQL = (
    "SELECT n.nspname AS schema_name, c.relname AS table_name, c.reltuples::BIGINT AS reltuples, "
    "c.relkind AS relkind, "
    "(c.relrowsecurity AND NOT (r.rolsuper OR r.rolbypassrls) "
    "AND (c.relforcerowsecurity OR NOT pg_has_role(c.relowner, 'USAGE'))) AS rls_filters "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "CROSS JOIN (SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user) r "
    "WHERE c.relkind IN ('r', 'p')"
)


def read_table_facts(conn) -> dict[tuple[str, str], TableFacts] | None:
    """{(schema, table): TableFacts}, or None when the catalog could not be read (callers then judge from the
    stored inventory). Ends its own transaction."""
    try:
        rows = conn.execute_query_isolated(TABLE_FACTS_SQL, None)
    except Exception as exc:
        log.warning("value profile: could not read pg_class: %s", exc)
        return None
    finally:
        conn.end_transaction()
    facts = {}
    for r in rows:
        tuples = r.get("reltuples")
        facts[(r["schema_name"], r["table_name"])] = TableFacts(
            estimate=int(tuples) if tuples is not None and int(tuples) >= 0 else None,
            partitioned=r.get("relkind") == "p",
            rls_filters=bool(r.get("rls_filters")),
        )
    return facts


def refusal(info: dict, facts: TableFacts | None, catalog_read: bool) -> tuple[str, str] | None:
    """(state, reason) when a table must not be read, else None. Shared by the profile and the checks, so both
    refuse the same tables for the same reasons:

    - not a table (view, materialized view, foreign table) -> not_applicable;
    - a partitioned parent -> not_applicable (its partitions are read; reading the parent would scan them twice);
    - row-level security would filter this account's view -> not_permitted (a count of some rows is not a count);

    There is no size test: a large table is read whole, bounded by the time budget alone (owner, design 2.3)."""
    from resource_explorer.table_kinds import PARTITIONED_TABLE, is_table_type

    table_type = (info.get("table_type") or "").upper().replace("_", " ")
    if table_type and not is_table_type(table_type):
        return "not_applicable", f"a {table_type.lower()}, not a table: only tables are read"
    if table_type == PARTITIONED_TABLE or (facts is not None and facts.partitioned):
        return "not_applicable", "a partitioned parent: its partitions are read instead"
    if (facts is not None and facts.rls_filters) or (not catalog_read and info.get("has_row_security")):
        return "not_permitted", ("row-level security is on and this account does not bypass it, so its counts "
                                 "would cover only the rows it may see")
    return None


#: The session time zone every profile read and check run uses (profile 1b; DESIGN-QUALITY-B1A-BASELINE-MATCH.md §7
#: question 2). A `timestamptz` prints in the session's zone and `{RUN_DATE}` is UTC text, so both reads use UTC:
#: a date baseline taken by the profile and the measure the run compares with it are then the same clock.
SESSION_TIME_ZONE = "UTC"


def begin_read(conn, bounds: ProfileBounds) -> None:
    """Open one table's transaction: read-only, with the statement timeout and the session time zone set for this
    transaction only. Pair with `conn.end_transaction()`."""
    conn.execute_query("SET TRANSACTION READ ONLY", None)
    conn.execute_query(f"SET LOCAL statement_timeout = {int(bounds.statement_timeout_seconds) * 1000}", None)
    conn.execute_query(f"SET LOCAL TIME ZONE '{SESSION_TIME_ZONE}'", None)


def _take_percentiles(conn, schema: str, table: str, table_rows: list[dict], bounds: ProfileBounds, clock,
                      gaps: list[dict]) -> None:
    """Step 6: the exact percentiles of the table's measured number columns, in a statement of their own. A table that
    could not give them keeps every other measure; its number columns read NULL there and the gap is reported."""
    from resource_explorer.surveyors.database.quality import percentiles

    wanted = [r for r in table_rows if r["state"] == "measured" and r["general_type"] == "N" and r.get("value_ct")]
    if not wanted:
        return
    found, reason = percentiles.measure_table(conn, schema, table, [r["column_name"] for r in wanted], bounds, clock)
    for row in wanted:
        trio = found.get(row["column_name"])
        if trio:
            row["percentile_25"], row["percentile_50"], row["percentile_75"] = trio
    if reason:
        gaps.append({"schema_name": schema, "table_name": table, "reason": reason,
                     "columns": [r["column_name"] for r in wanted if r["column_name"] not in found]})


def _take_text_shape(conn, schema: str, table: str, table_rows: list[dict], bounds: ProfileBounds, clock,
                     gaps: list[dict]) -> None:
    """Step 7: the sampled text-shape measures of the table's measured text columns, one statement each inside the
    table's text-shape budget. A column that could not give them keeps every other measure and reads NULL there, the
    reason in the run summary; a column read on a sample carries the table's `sample_ratio`."""
    from resource_explorer.surveyors.database.quality import text_shape

    wanted = [r for r in table_rows if r["state"] == "measured" and r["general_type"] == "A" and r.get("value_ct")]
    if not wanted:
        return
    record_ct = wanted[0].get("record_ct")
    found, reasons = text_shape.measure_table(
        conn, schema, table, [(r["column_name"], r["value_ct"]) for r in wanted], record_ct, bounds, clock)
    for row in wanted:
        hit = found.get(row["column_name"])
        if hit:
            measures, sampling = hit
            row.update(measures)
            row["sample_ratio"] = sampling.sample_ratio if sampling else None
    if reasons:
        gaps.append({"schema_name": schema, "table_name": table, "reason": reasons[0][1],
                     "columns": [c for c, _ in reasons]})


def _take_frequent_values(conn, schema: str, table: str, table_rows: list[dict], catalog_types: dict,
                          bounds: ProfileBounds, clock, gaps: list[dict], child_rows: list[dict]) -> None:
    """Step 8b: the most frequent values of the table's eligible text columns (2 to 70 distinct values of at most 70
    characters), one statement each inside the table's frequency budget. A column that could not be read keeps
    `frequency_state` "not_measured" and no child row; one that was read is "read" (no child row then means only blank
    values). A column read on a sample carries the table's `sample_ratio`."""
    from resource_explorer.surveyors.database.quality import frequent_values

    wanted = [r for r in table_rows if frequent_values.eligible(r)]
    if not wanted:
        return
    record_ct = wanted[0].get("record_ct")
    found, reasons = frequent_values.measure_table(
        conn, schema, table, [(r["column_name"], catalog_types.get((schema, table, r["column_name"])), r.get("value_ct"))
                              for r in wanted], record_ct, bounds, clock)
    for row in wanted:
        hit = found.get(row["column_name"])
        if hit is None:
            row["frequency_state"] = frequent_values.NOT_MEASURED
            continue
        frequent, sampling = hit
        row["frequency_state"] = frequent_values.READ
        if sampling and row.get("sample_ratio") is None:
            row["sample_ratio"] = sampling.sample_ratio
        if frequent is None:
            continue
        row["distinct_value_hash"] = frequent.distinct_value_hash
        for rank, (value, ct) in enumerate(frequent.values, start=1):
            child_rows.append({"schema_name": schema, "table_name": table, "column_name": row["column_name"],
                               "value_rank": rank, "value": value, "value_ct": ct, "other_distinct_ct": None})
        if frequent.other:
            child_rows.append({"schema_name": schema, "table_name": table, "column_name": row["column_name"],
                               "value_rank": len(frequent.values) + 1, "value": None, "value_ct": frequent.other[1],
                               "other_distinct_ct": frequent.other[0]})
    if reasons:
        gaps.append({"schema_name": schema, "table_name": table, "reason": reasons[0][1],
                     "columns": [c for c, _ in reasons]})


def _take_hashes(conn, schema: str, table: str, table_rows: list[dict], catalog_types: dict, bounds: ProfileBounds, clock,
                 gaps: list[dict]) -> None:
    """Step 8 (only when asked): each measured text, number or date column's multiset hash, and a date column's epoch sum,
    in statements of their own inside the table's hash budget. A column that could not be read keeps NULL and
    `hash_state` "not_measured", the reason in the run summary; one that was read is "taken" (a NULL hash after that means
    no value was hashable, which is a value)."""
    from resource_explorer.surveyors.database.quality import value_hashes

    wanted = [r for r in table_rows if r["state"] == "measured" and r["general_type"] in value_hashes.HASHED_GENERAL_TYPES
              and r.get("value_ct")]
    if not wanted:
        return
    columns = [value_hashes.HashColumn(r["column_name"], r["general_type"],
                                       catalog_types.get((schema, table, r["column_name"])) or "") for r in wanted]
    found, reason = value_hashes.measure_table(conn, schema, table, columns, bounds, clock)
    for row in wanted:
        hit = found.get(row["column_name"])
        if hit is not None:
            row.update(multiset_hash=hit.get("multiset_hash"), epoch_sum=hit.get("epoch_sum"), hash_state="taken")
        else:
            row["hash_state"] = "not_measured"
    if reason:
        gaps.append({"schema_name": schema, "table_name": table, "reason": reason,
                     "columns": [r["column_name"] for r in wanted if r["column_name"] not in found]})


def _take_keyed_hashes(connect, rows: list[dict], catalog_types: dict, positions: dict, bounds: ProfileBounds, clock,
                       gaps: list[dict]) -> None:
    """Step 8: infer each table's key from the rows just measured, then take every other hashed column's keyed hash."""
    from resource_explorer.surveyors.database.quality import value_hashes

    keys = value_hashes.infer_key_columns(rows, catalog_types, positions)
    if not keys:
        return
    by_table: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_table.setdefault((row["schema_name"], row["table_name"]), []).append(row)
    with connect() as conn:
        for (schema, table), key_name in sorted(keys.items()):
            table_rows = by_table[(schema, table)]
            key_row = next(r for r in table_rows if r["column_name"] == key_name)
            key = value_hashes.HashColumn(key_name, key_row["general_type"],
                                          catalog_types.get((schema, table, key_name)) or "")
            targets = [r for r in table_rows if r.get("hash_state") == "taken" and r["column_name"] != key_name]
            for row in table_rows:
                if row.get("hash_state") == "taken":
                    row["key_column_name"] = key_name
            if not targets:
                continue
            columns = [value_hashes.HashColumn(r["column_name"], r["general_type"],
                                               catalog_types.get((schema, table, r["column_name"])) or "") for r in targets]
            found, reason = value_hashes.measure_keyed(conn, schema, table, key, columns, bounds, clock)
            for row in targets:
                row["keyed_hash"] = found.get(row["column_name"])
                if row["column_name"] not in found:
                    row["hash_state"] = "keyed_not_measured"      # its multiset hash was read; its keyed hash was not
            if reason:
                gaps.append({"schema_name": schema, "table_name": table, "reason": reason,
                             "columns": [r["column_name"] for r in targets if r["column_name"] not in found]})


def run_value_profile(
    db_entity, registry, connect: Callable[[], AbstractContextManager], *,
    bounds: ProfileBounds, surveyed_at: str | None = None, ran_as: str = "",
    clock: Callable[[], float] = time.monotonic, hashes: bool = False,
) -> dict:
    """Profile every table the stored inventory names; store and return the batch.

    `connect()` returns a context manager yielding a DatabaseConnection (the adapter passes
    `database_connection(db_entity, credentials)`; tests pass a fake). A connection failure raises: the step
    failed, and nothing is stored, so the run cannot read as "profiled, nothing found"."""
    slug = db_entity.slug
    surveyed_at = surveyed_at or datetime.now(UTC).isoformat()
    columns = registry.query_detail_rows("database_columns", slug)
    if not columns:
        return {
            "value_profile": {
                "surveyed_at": surveyed_at, "state": "not_established",
                "reason": "no stored schema inventory: run the schema step first; nothing was profiled",
                "column_count": 0,
            },
            "annotations": [],
        }
    tables = {(t["schema_name"], t["table_name"]): t for t in registry.query_detail_rows("database_tables", slug)}
    by_table: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for col in columns:
        key = (col["schema_name"], col["table_name"])
        by_table.setdefault(key, []).append(
            (col["column_name"], derive_general_type(col.get("base_type") or col.get("data_type"),
                                                     col.get("column_size"))))
    has_numbers = any(g == "N" for cols in by_table.values() for _, g in cols)

    run_date = run_date_text(surveyed_at)
    rows: list[dict] = []
    table_outcomes: list[dict] = []
    numeric: dict | None = {}
    percentile_gaps: list[dict] = []
    text_shape_gaps: list[dict] = []
    hash_gaps: list[dict] = []
    frequency_gaps: list[dict] = []
    frequent_rows: list[dict] = []
    catalog_types = {(c["schema_name"], c["table_name"], c["column_name"]): c.get("base_type") or c.get("data_type")
                     for c in columns}
    with connect() as conn:
        facts = read_table_facts(conn)
        if has_numbers:
            numeric = read_numeric_chars(conn)
        for (schema, table), cols in sorted(by_table.items()):
            info = tables.get((schema, table)) or {}
            refused = refusal(info, (facts or {}).get((schema, table)), facts is not None)
            if refused:
                state, reason = refused
                rows.extend(_empty_row(schema, table, c, g, state, reason, ran_as) for c, g in cols)
                table_outcomes.append(_table_outcome(schema, table, state, reason))
                continue
            failures: list[tuple[str, str]] = []
            table_rows: list[dict] = []
            # The per-table budget is the TOTAL across this table's chunks, and it is the only time bound: each
            # chunk's statement timeout is what is left of it (never a fixed per-statement figure that would cancel
            # a 61 s chunk with four minutes to spare).
            started, exceeded = clock(), False
            for start in range(0, len(cols), COLUMNS_PER_QUERY):
                remaining = bounds.table_budget_seconds - (clock() - started)
                if remaining <= 0:
                    exceeded = True
                    break
                chunk_bounds = replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining)))
                chunk = cols[start:start + COLUMNS_PER_QUERY]
                sql, aliases = build_table_query(
                    schema, table, [(c, column_chars(schema, table, c, g, numeric)) for c, g in chunk], run_date)
                try:
                    # One read-only transaction per query, ended at once: no lock outlives its table. Params None,
                    # so psycopg2 never reads a "%" in a name as a placeholder.
                    begin_read(conn, chunk_bounds)
                    result = conn.execute_query_isolated(sql, None)
                except Exception as exc:
                    state, reason = classify_error(exc), error_text(exc)
                    # The exception is classified FIRST: a privilege error that arrives after the budget is spent
                    # is still "not permitted", never relabelled "profile incomplete". Only an error that is not
                    # one of those, arriving with the budget spent, is the budget running out.
                    if state != "not_permitted" and clock() - started >= bounds.table_budget_seconds:
                        exceeded = True
                        break
                    failures.append((state, reason))
                    table_rows.extend(_empty_row(schema, table, c, g, state, reason, ran_as) for c, g in chunk)
                    continue
                finally:
                    conn.end_transaction()
                if not result:
                    reason = "the aggregate query returned no row"
                    failures.append(("not_measured", reason))
                    table_rows.extend(
                        _empty_row(schema, table, c, g, "not_measured", reason, ran_as) for c, g in chunk)
                    continue
                values = result[0]
                record_ct = _value("record_ct", values.get("record_ct"))
                chunk_rows = [_empty_row(schema, table, c, g, "measured", "", ran_as) for c, g in chunk]
                for row in chunk_rows:
                    row["record_ct"] = record_ct
                for alias, (i, measure) in aliases.items():
                    chunk_rows[i][measure] = _value(measure, values.get(alias))
                table_rows.extend(chunk_rows)
            if exceeded:
                # Incomplete means NOT measured, for every column: the counts of the chunks that did finish are
                # dropped with the rest, so nothing downstream proposes a check from half a table, and no zero is
                # ever written for a count that was not taken.
                reason = incomplete_reason(bounds.table_budget_seconds)
                rows.extend(_empty_row(schema, table, c, g, "not_measured", reason, ran_as) for c, g in cols)
                table_outcomes.append(_table_outcome(schema, table, "not_measured", reason))
                continue
            rows.extend(table_rows)
            _take_percentiles(conn, schema, table, table_rows, bounds, clock, percentile_gaps)
            _take_text_shape(conn, schema, table, table_rows, bounds, clock, text_shape_gaps)
            _take_frequent_values(conn, schema, table, table_rows, catalog_types, bounds, clock, frequency_gaps,
                                  frequent_rows)
            if hashes:
                _take_hashes(conn, schema, table, table_rows, catalog_types, bounds, clock, hash_gaps)
            if failures:
                state, reason = failures[0]
                table_outcomes.append(_table_outcome(schema, table, state, reason))

    # Q4 port order step 6: the functional-type inference, over what was just measured and the stored inventory. Pure and
    # zero-fetch; a column it cannot settle stays NULL, and so does a table whose record count has one measured run.
    from resource_explorer.surveyors.database.quality import functional_infer

    positions = {(c["schema_name"], c["table_name"], c["column_name"]): c.get("ordinal_position") or 0 for c in columns}
    functional_infer.label_rows(rows, catalog_types=catalog_types, scales=numeric,
                                history=registry.value_profile_record_counts(slug), positions=positions)
    if hashes:
        _take_keyed_hashes(connect, rows, catalog_types, positions, bounds, clock, hash_gaps)
    registry.write_detail_rows(PROFILE_TABLE, slug, surveyed_at, rows=rows)
    registry.write_detail_rows(FREQUENT_VALUES_TABLE, slug, surveyed_at, rows=frequent_rows)
    write_typed_rows(registry, slug, surveyed_at)
    states: dict[str, int] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    return {
        "value_profile": {
            "surveyed_at": surveyed_at,
            "state": "measured" if states.get("measured") else "not_measured",
            "table_count": len(by_table),
            "column_count": len(rows),
            "states": states,
            "tables_not_profiled": table_outcomes,
            "percentiles_not_measured": percentile_gaps,
            "text_shape_not_measured": text_shape_gaps,
            # "off" (the default): no hash was asked for. "on": hashes_not_measured says what could not be read.
            "hashes": "on" if hashes else "off",
            "hashes_not_measured": hash_gaps,
            "frequent_values_not_measured": frequency_gaps,
            "ran_as": ran_as,
            "bounds": {"table_budget_seconds": bounds.table_budget_seconds},
            # Whether the catalog read profile 1b's decimal and exact-sum measures depend on succeeded. "not_read":
            # fractional_sum and sum_value were not taken for any number column (NULL, never 0).
            "numeric_chars": "not_needed" if not has_numbers else ("read" if numeric is not None else "not_read"),
        },
        "annotations": build_annotations(rows, table_outcomes, as_of=as_of_text(surveyed_at)),
    }


#: The catalogue id whose declaration is this step's table result (B1).
ANALYSIS_ID = "value_profile"


def typed_row(table, stored: dict) -> dict:
    """One stored profile row as the declared table's row: the three names as the key, then each declared column by
    its native name. Nothing is computed or renamed beyond the key, so the typed read and the native read compare field
    for field."""
    row = {"schema": stored["schema_name"], "table": stored["table_name"], "column": stored["column_name"]}
    row.update({n: stored.get(n) for n in table.column_names() if n not in table.key_fields})
    return row


def write_typed_rows(registry, slug: str, surveyed_at: str) -> int:
    """B1: this run's rows as the declared table result (`analysis_table_rows`), every row, in the order the registry
    reads the native rows back (so the typed table and the native read agree on order on SQLite and Postgres alike).
    The native rows stay where they are: the checks, the proposals and the publish reshape read them there
    (convert-as-touched moves only what the Value profile tab reads). Returns how many rows the run has."""
    from resource_explorer.declared_context import declarations
    from resource_explorer.surveyors.result_materializer import write_table_rows

    dec = declarations("database")[ANALYSIS_ID]
    stored = registry.query_detail_rows(PROFILE_TABLE, slug, surveyed_at=surveyed_at)
    run = {"resource_kind": "database", "slug": slug, "analysis_id": ANALYSIS_ID, "run_id": surveyed_at}
    return write_table_rows(registry, run, dec, [typed_row(dec.table, r) for r in stored])


def measure_key(schema: str, table: str, column: str) -> str:
    """A column measure's `item_key`: the compact JSON array `["schema","table","column"]`, never dotted text (a
    dot inside a name would make two different columns one key). The profile publish reshape keys its proof rows
    on it."""
    return json.dumps([schema, table, column], separators=(",", ":"))


def table_key(schema: str, table: str) -> str:
    """A not-profiled table's `item_key`: the compact JSON array `["schema","table"]`, as `measure_key` is for a
    column. The profile publish reshape re-links an unchanged one by it."""
    return json.dumps([schema, table], separators=(",", ":"))


def _outcome_key(outcome: dict) -> str:
    schema, table = outcome.get("schema_name"), outcome.get("table_name")
    if schema is None or table is None:
        schema, _, table = outcome["table"].partition(".")
    return table_key(schema, table)


def build_annotations(rows: list[dict], table_outcomes: list[dict], *, as_of: str = "") -> list:
    """One ResourceProfileAnnotation per measured column (counts in `value_count`, date bounds and the
    general_type in `json_properties`), and one ResourceMeasureAnnotation per table not profiled, carrying why.

    A relative-to-now measure (`RELATIVE_TO_NOW_MEASURES`) is left out of `value_count`; a date column's
    `profileDates` carries `asOf` (the instant it was measured against) beside its bounds."""
    from resource_explorer.surveyors.survey_report import (
        ResourceMeasureAnnotation,
        ResourceProfileAnnotation,
    )

    out: list = []
    for row in rows:
        if row["state"] != "measured":
            continue
        path = f"{row['schema_name']}.{row['table_name']}.{row['column_name']}"
        key = measure_key(row["schema_name"], row["table_name"], row["column_name"])
        counts = {k: row[k] for k in PUBLISHED_COUNTS if row.get(k) is not None}
        dates = {k: row.get(k) for k in DATE_MEASURES if row.get(k) is not None}
        if as_of and row.get("general_type") == "D":
            dates["asOf"] = as_of
        out.append(ResourceProfileAnnotation(
            summary=f"Value profile: {path} ({row['record_ct']:,} rows, {row.get('value_ct') or 0:,} with a value)"
            if row.get("record_ct") is not None else f"Value profile: {path}",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="Value Profile",
            check_name="value_profile",
            item_key=key,
            label="measured",
            value_count=counts,
            json_properties={
                "generalType": row["general_type"],
                "profileDates": dates,
                "sampleRatio": row.get("sample_ratio"),
                "ranAs": row.get("ran_as", ""),
            },
        ))
    for outcome in table_outcomes:
        out.append(ResourceMeasureAnnotation(
            summary=f"Value profile: {outcome['table']} not profiled ({outcome['state']})",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="Value Profile",
            check_name="value_profile",
            item_key=_outcome_key(outcome),
            label=outcome["state"],
            confidence=0,
            explanation=outcome["reason"],
            resource_properties={"state": outcome["state"], "reason": outcome["reason"]},
        ))
    return out
