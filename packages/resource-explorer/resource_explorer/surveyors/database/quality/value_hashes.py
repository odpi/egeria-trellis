"""Value hashes of a column (port step 8, Q4 §5 row 8: families B2 and `Match_Epoch_Sum`), off by default.

TestGen's Table Comparison tests compare two copies of a table without moving rows: the profile stores, per column, the SUM
of a 56-bit hash of every value (`multiset_hash`), for a date column also the SUM of its whole seconds since 1970
(`epoch_sum`), and, for each table that has a key column, the SUM of a hash of the (key, value) pair per other column
(`keyed_hash`). A later run takes the same sum and the check fails when it differs. Upstream builds the SQL in its flavor
services (`flavor_service.py`, `redshift_flavor_service.py`, `postgresql_flavor_service.py`) and pins what it must
produce in a reference implementation (`value_hash.py`); this module is Resource Explorer's own Postgres port of the
SQL builders, written against that reference and checked against it on a real Postgres (tests/test_quality_step_8_hashes.py,
the `RE_HASH_TEST_PG_DSN` tier). The four upstream files are pinned by SHA-256 (`UPSTREAM_SHA256`) and compared with the
clone by `scripts/sync_quality_catalog.py`, so a change upstream fails until this port is re-read.

A hash is an exact whole number far past 64 bits (a sum of 56-bit hashes over a billion rows), so it is stored and compared
as exact decimal text, never a float and never a BIGINT.

**Off by default**, as TestGen's table comparison is (`profile_table_comparison`). When off, every hash column is NULL and the
seven hash types read `not_established`, "value hashes were not taken", never a silent "no". When on, the hashes are
whole-table reads, each statement under its own time budget (`ProfileBounds.hash_budget_seconds`), so a table that cannot be
hashed keeps every other measure and reads the hash columns NULL with the reason in the run summary (`hashes_not_measured`).
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from resource_explorer.surveyors.database.quality.catalogue import quote_identifier
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

#: SHA-256 of the upstream source this port was written from (TestGen 5.134.0, commit 3606859c), by path under `testgen/`.
UPSTREAM_SHA256 = {
    "common/value_hash.py": "56718e74f9eeb3cf49ab4da329ca8e18cfb8f418e58f42e9ee787a62b40e0227",
    "common/database/flavor/flavor_service.py": "4066da2efac87fe6292a1bb9acaab05beb0ec3c4990ff1990ddf2bb193450d55",
    "common/database/flavor/redshift_flavor_service.py": "c7db28b56c3db172086d6c6711e856d7498a971cb426c55161c9bf18af19ece1",
    "common/database/flavor/postgresql_flavor_service.py": "a9144cdb881c6fdd2aadc19918e516742757d6d27d373297c46e9fe01e08073c",
    "template/profiling/infer_key_columns.sql": "becfcdd74067a44d62361969126d2ebcfdbfdbf5743c05c87ae34a8350337599",
}

FLOAT_TYPES = frozenset({"double precision", "real", "double", "float", "float4", "float8"})
DOUBLE_TYPE = "DOUBLE PRECISION"
WIDE_DECIMAL = "NUMERIC(38,0)"
VARCHAR = "VARCHAR(1000)"
#: The least magnitude whose canonical number, rounded to six places, reaches 10^32 (upstream `DECIMAL_BOUND`).
DECIMAL_BOUND = Decimal("99999999999999999999999999999999.9999995")
#: Columns whose hashes are taken, by general type (`hashed_types` upstream, for a flavor that hashes values).
HASHED_GENERAL_TYPES = ("A", "N", "D")
COLUMNS_PER_STATEMENT = 40


@dataclass(frozen=True)
class HashColumn:
    """What the hash SQL needs to know of a column: its name, general_type and catalog type (`db_data_type`)."""

    name: str
    general_type: str
    db_data_type: str = ""

    @property
    def base_type(self) -> str:
        return base_type_name(self.db_data_type)

    @property
    def is_float(self) -> bool:
        return self.base_type in FLOAT_TYPES


def base_type_name(type_name: str | None) -> str:
    """A catalog type name lowercased, with any precision or length removed."""
    return re.sub(r"\s*\([^)]*\)", "", type_name or "").strip().lower()


# ── the SQL builders (upstream flavor_service.py, redshift_flavor_service.py, postgresql_flavor_service.py) ─────

def _exact_float_canonical(ref: str) -> str:
    """A binary float's canonical number from its exact value (upstream `exact_float_canonical`, Postgres quotient
    `DIV(a, b)`): the magnitude splits exactly into a multiple of 2^40, a whole remainder and a fraction, the fraction into
    two 45-bit integers, so integer arithmetic sees the exact binary value; rounded half away from zero to nine places,
    then six. Exact below 10^29 and at least 2^-38."""
    def exact(expr: str) -> str:
        return f"CAST(CAST({expr} AS BIGINT) AS NUMERIC)"

    def power(exponent: int) -> str:
        return f"CAST({2**exponent} AS {DOUBLE_TYPE})"

    magnitude = f"ABS(CAST({ref} AS {DOUBLE_TYPE}))"
    high = f"FLOOR({magnitude} / {power(40)})"
    low = f"({magnitude} - {high} * {power(40)})"
    fraction_high = f"FLOOR(({low} - FLOOR({low})) * {power(45)})"
    fraction_low = f"((({low} - FLOOR({low})) * {power(45)} - {fraction_high}) * {power(45)})"
    whole = f"({exact(high)} * {2**40} + {exact(f'FLOOR({low})')})"
    fraction_bits = f"({exact(fraction_high)} * {2**45} + {exact(fraction_low)})"
    nanos = f"DIV({fraction_bits} * 1000000000 + {2**89}, {2**90})"
    micros = f"{whole} * 1000000 + DIV({nanos} + 500, 1000)"
    return f"(CASE WHEN {ref} < 0 THEN -1 ELSE 1 END) * ({micros})"


def _text_value(ref: str, column_type: str | None) -> str:
    # CHAR values are stored padded with spaces to the declared length (upstream redshift `text_value`).
    if column_type and re.fullmatch(r"(char|character|bpchar)(\(\d+\))?", column_type.lower().strip()):
        return f"RTRIM({ref})"
    return ref


def _canonical_number(ref: str, is_float: bool) -> str:
    if is_float:
        return f"CAST({_exact_float_canonical(ref)} AS {VARCHAR})"
    exact = f"CAST({ref} AS NUMERIC)"        # through NUMERIC, so a money value rounds too
    rounded = f"CAST(ROUND({exact}, 6) AS DECIMAL(38,6))"
    magnitude = f"ABS({rounded})"
    whole = f"CAST(FLOOR({magnitude}) AS {WIDE_DECIMAL}) * 1000000"
    fraction = f"CAST(({magnitude} - FLOOR({magnitude})) * 1000000 AS {WIDE_DECIMAL})"
    sign = f"CAST(SIGN({rounded}) AS {WIDE_DECIMAL})"
    return f"CAST({sign} * ({whole} + {fraction}) AS {VARCHAR})"


def _utc_timestamp(ref: str, column: HashColumn) -> str:
    if column.base_type.endswith("with time zone"):
        return f"({ref} AT TIME ZONE 'UTC')"
    return f"CAST({ref} AS TIMESTAMP)"


def canonical_text(ref: str, column: HashColumn) -> str:
    """The SQL for a value's canonical text: text as read; a number rounded half away from zero to six places, times 10^6, as
    integer text (a float first to nine places); a date or timestamp as `YYYY-MM-DD HH24:MI:SS` in UTC, truncated."""
    if column.general_type == "N":
        return _canonical_number(ref, column.is_float)
    if column.general_type == "D":
        return f"TO_CHAR({_utc_timestamp(ref, column)}, 'YYYY-MM-DD HH24:MI:SS')"
    return _text_value(ref, column.db_data_type)


def _md5_hex(text: str) -> str:
    return f"MD5({text})"


def _hash_integer(text: str) -> str:
    """The first 56 bits of the MD5 of `text`, as NUMERIC(38,0) (56 bits fit a signed 64-bit integer as a positive value)."""
    return f"CAST(CAST(CAST('x' || SUBSTRING({_md5_hex(text)}, 1, 14) AS BIT(56)) AS BIGINT) AS {WIDE_DECIMAL})"


def hashable_condition(ref: str, column: HashColumn) -> str:
    """The condition under which a value contributes: not NULL, and a number only when finite and below 10^29 in magnitude for a
    float, 10^32 at six places for another number (an infinite or NaN value fails the same comparison)."""
    if column.general_type == "N" and column.is_float:
        return f"{ref} IS NOT NULL AND ABS({ref}) < 1E29"
    if column.general_type == "N":
        return f"{ref} IS NOT NULL AND ABS(CAST({ref} AS NUMERIC)) < {DECIMAL_BOUND}"
    if column.general_type == "D":
        return f"{ref} IS NOT NULL AND ISFINITE({ref})"
    return f"{ref} IS NOT NULL"


def multiset_hash_term(column: HashColumn) -> str:
    ref = quote_identifier(column.name)
    return f"CASE WHEN {hashable_condition(ref, column)} THEN {_hash_integer(canonical_text(ref, column))} END"


def epoch_sum_term(column: HashColumn) -> str:
    ref = quote_identifier(column.name)
    seconds = f"CAST(FLOOR(EXTRACT(EPOCH FROM {_utc_timestamp(ref, column)})) AS {WIDE_DECIMAL})"
    return f"CASE WHEN {hashable_condition(ref, column)} THEN {seconds} END"


def keyed_hash_term(key: HashColumn, column: HashColumn) -> str:
    """One row's term in a column's keyed hash: the hash of the key's MD5 hex followed by the value's (both fixed width)."""
    key_ref, ref = quote_identifier(key.name), quote_identifier(column.name)
    key_hex = _md5_hex(canonical_text(key_ref, key))
    value_hex = _md5_hex(canonical_text(ref, column))
    condition = f"{hashable_condition(ref, column)} AND {hashable_condition(key_ref, key)}"
    return f"CASE WHEN {condition} THEN {_hash_integer(f'{key_hex} || {value_hex}')} END"


# ── what is measured ───────────────────────────────────────────────────────────────────────────────────────────

def _scalar(value) -> str | None:
    """A SUM as exact decimal text (an int or Decimal; never a float)."""
    if value is None:
        return None
    return str(int(value)) if not isinstance(value, float) else str(int(Decimal(value)))


def build_column_statement(schema: str, table: str, columns: list[HashColumn]) -> tuple[str, dict[str, tuple[str, str]]]:
    """One SELECT of the multiset hash (and, for a date column, the epoch sum) of each column: (sql, {alias: (column, measure)})."""
    items, aliases = [], {}
    for i, column in enumerate(columns):
        items.append(f"SUM({multiset_hash_term(column)}) AS c{i}__multiset_hash")
        aliases[f"c{i}__multiset_hash"] = (column.name, "multiset_hash")
        if column.general_type == "D":
            items.append(f"SUM({epoch_sum_term(column)}) AS c{i}__epoch_sum")
            aliases[f"c{i}__epoch_sum"] = (column.name, "epoch_sum")
    return f"SELECT {', '.join(items)} FROM {quote_identifier(schema)}.{quote_identifier(table)}", aliases


def build_keyed_statement(schema: str, table: str, key: HashColumn, columns: list[HashColumn]
                          ) -> tuple[str, dict[str, str]]:
    items = [f"SUM({keyed_hash_term(key, c)}) AS k{i}" for i, c in enumerate(columns)]
    aliases = {f"k{i}": c.name for i, c in enumerate(columns)}
    return f"SELECT {', '.join(items)} FROM {quote_identifier(schema)}.{quote_identifier(table)}", aliases


def infer_key_columns(rows: list[dict], catalog_types: dict[tuple[str, str, str], str | None],
                      positions: dict[tuple[str, str, str], int]) -> dict[tuple[str, str], str]:
    """Each table's key (upstream `infer_key_columns.sql`): among its hashed columns, the first-ranked with a distinct value in
    every row (`record_ct = value_ct = distinct_value_ct`, no NULL) and a multiset hash; not a binary float; text at most 100
    characters; a date only if declared DATE; a number only if declared with a scale of at most 6 or, with no scale, holding no
    fraction. Rank: an ID functional type (natural before surrogate), then number, date, text, then shorter average length,
    ordinal position, name. {(schema, table): column}."""
    best: dict[tuple[str, str], tuple] = {}
    for row in rows:
        if row.get("state") != "measured" or row.get("multiset_hash") is None:
            continue
        ct = row.get("record_ct")
        if not (ct and row.get("value_ct") == ct and row.get("null_value_ct") == 0 and row.get("distinct_value_ct") == ct):
            continue
        key = (row["schema_name"], row["table_name"], row["column_name"])
        declared = catalog_types.get(key) or ""
        base = base_type_name(declared)
        gtype = row["general_type"]
        if base in FLOAT_TYPES:
            continue
        if gtype == "A" and (row.get("max_length") is None or row["max_length"] > 100):
            continue
        if gtype == "D" and base != "date":
            continue
        fractional = row.get("fractional_sum")
        has_fraction = fractional is not None and Decimal(str(fractional)) != 0
        if gtype == "N":
            scale = re.search(r",\s*(\d+)\s*\)", declared)
            if (int(scale.group(1)) > 6) if scale else has_fraction:
                continue
        fdt = row.get("functional_data_type") or ""
        id_rank = 1 if re.fullmatch(r"ID.*SK", fdt) else 0 if fdt.startswith("ID") else 2
        type_rank = 0 if gtype == "N" and not has_fraction else 1 if gtype == "D" else 2 if gtype == "A" else 3
        rank = (id_rank, type_rank, math.inf if row.get("avg_length") is None else row["avg_length"],
                positions.get(key, 0), row["column_name"])
        table = (row["schema_name"], row["table_name"])
        if table not in best or rank < best[table][0]:
            best[table] = (rank, row["column_name"])
    return {t: v[1] for t, v in best.items()}


def measure_table(conn, schema: str, table: str, columns: list[HashColumn], bounds: ProfileBounds,
                  clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, dict[str, str | None]], str]:
    """({column: {"multiset_hash": text, "epoch_sum": text}}, reason): each statement a read-only transaction ended at once,
    all inside `bounds.hash_budget_seconds`. The reason is empty when every column was read."""
    found: dict[str, dict[str, str | None]] = {}
    started, budget = clock(), bounds.hash_budget_seconds
    for start in range(0, len(columns), COLUMNS_PER_STATEMENT):
        remaining = budget - (clock() - started)
        if remaining <= 0:
            return found, f"exceeded {budget / 60:g} min · hashes not measured"
        sql, aliases = build_column_statement(schema, table, columns[start:start + COLUMNS_PER_STATEMENT])
        try:
            begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining))))
            result = conn.execute_query_isolated(sql, None)
        except Exception as exc:  # noqa: BLE001 - any failure of this optional statement is reported, never raised
            if classify_error(exc) != "not_permitted" and clock() - started >= budget:
                return found, f"exceeded {budget / 60:g} min · hashes not measured"
            return found, f"hashes not measured · {error_text(exc)}"
        finally:
            conn.end_transaction()
        if not result:
            return found, "hashes not measured · the query returned no row"
        for alias, (column, measure) in aliases.items():
            found.setdefault(column, {})[measure] = _scalar(result[0].get(alias))
    return found, ""


def measure_keyed(conn, schema: str, table: str, key: HashColumn, columns: list[HashColumn], bounds: ProfileBounds,
                  clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, str | None], str]:
    """({column: keyed hash text}, reason) for a table whose key was inferred, as `measure_table`."""
    found: dict[str, str | None] = {}
    started, budget = clock(), bounds.hash_budget_seconds
    for start in range(0, len(columns), COLUMNS_PER_STATEMENT):
        remaining = budget - (clock() - started)
        if remaining <= 0:
            return found, f"exceeded {budget / 60:g} min · keyed hashes not measured"
        sql, aliases = build_keyed_statement(schema, table, key, columns[start:start + COLUMNS_PER_STATEMENT])
        try:
            begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining))))
            result = conn.execute_query_isolated(sql, None)
        except Exception as exc:  # noqa: BLE001 - any failure of this optional statement is reported, never raised
            if classify_error(exc) != "not_permitted" and clock() - started >= budget:
                return found, f"exceeded {budget / 60:g} min · keyed hashes not measured"
            return found, f"keyed hashes not measured · {error_text(exc)}"
        finally:
            conn.end_transaction()
        if not result:
            return found, "keyed hashes not measured · the query returned no row"
        for alias, column in aliases.items():
            found[column] = _scalar(result[0].get(alias))
    return found, ""
