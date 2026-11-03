"""Frequent values of a short text column (port step 8b: TestGen's frequency analysis).

TestGen reads the most frequent values of each text column that has 2 to 70 distinct values and values of at most 70
characters (`secondary_profiling_columns.sql`), with the same `TABLESAMPLE BERNOULLI (p) REPEATABLE (64)` rule as the
text shape. The statement is the vendored `postgresql/project_secondary_profiling_query.sql`, byte-identical to upstream
(pinned by SHA-256 and checked by `scripts/sync_quality_catalog.py`); its sampling block is selected by upstream's own
`-- TG-IF do_sample_bool` marker.

What it returns, one row per top-10 value ordered by rank, and one trailing row with a NULL value carrying the combined
count and distinct count of every value past the tenth. Resource Explorer stores them in the child table
`database_value_profile_frequent_values` (one row per value, the NULL-value row as the "other" row) beside the profile, with
the column's `distinct_value_hash` (an MD5 of its sorted distinct values, only when there are at most 70) and a
`frequency_state` on the profile row:

- NULL: the column was not eligible, or the row is from before the analysis existed;
- `read`: the statement ran. No child row after that means the column held only blank values;
- `not_measured`: it failed or ran out of its budget (`ProfileBounds.frequency_budget_seconds`, 5 minutes). The run
  summary's `frequent_values_not_measured` names the column and the reason.

On a sample the counts are lifted onto the table's scale as TestGen lifts them (`calibrate_frequent_values`): each count by
the column's whole value count over the sample's value count, rounded half up; the number of distinct values the "other" row
covers stays as observed. A lifted count is an estimate and the column carries its `sample_ratio`.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, localcontext

from resource_explorer.surveyors.database.quality.catalogue import POSTGRES_DIR, quote_identifier, sha256_text
from resource_explorer.surveyors.database.quality.text_shape import TableSampling, sampling_for
from resource_explorer.surveyors.database.quality.value_hashes import _text_value
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

SECONDARY_SQL = POSTGRES_DIR / "project_secondary_profiling_query.sql"
#: SHA-256 of the vendored file's bytes. The file is unchanged from upstream, so this is also upstream's.
SECONDARY_SHA256 = "eacf53973707a23a5e9ff78e6dae64059be11e201a4ec65311593c6fdb565408"

TOP_VALUES = 10
MAX_DISTINCT = 70
MAX_LENGTH = 70
#: The state words stored on the profile row.
READ, NOT_MEASURED = "read", "not_measured"


def eligible(row: dict) -> bool:
    """Upstream's `secondary_profiling_columns.sql`: a measured text column with 2 to 70 distinct values of at most 70
    characters. Decided from what the column pass measured, so a column pass that failed leaves it ineligible."""
    return (row.get("state") == "measured" and row.get("general_type") == "A"
            and row.get("distinct_value_ct") is not None and 2 <= row["distinct_value_ct"] <= MAX_DISTINCT
            and row.get("max_length") is not None and row["max_length"] <= MAX_LENGTH)


def pin_matches() -> bool:
    return sha256_text(SECONDARY_SQL.read_text(encoding="utf-8")) == SECONDARY_SHA256


def build_statement(schema: str, table: str, column: str, db_type: str | None, sampling: TableSampling | None) -> str:
    """The vendored statement for one column: the sampling block kept or dropped by upstream's marker, every placeholder
    filled (identifiers quoted from the inventory; `{SAMPLE_PERCENT_CALC}` a number RE computed)."""
    out, keep = [], True
    for line in SECONDARY_SQL.read_text(encoding="utf-8").splitlines():
        marker = line.strip()
        if marker == "-- TG-IF do_sample_bool":
            keep = sampling is not None
            continue
        if marker == "-- TG-ENDIF":
            keep = True
            continue
        if keep:
            out.append(line)
    text = "\n".join(out).rstrip().rstrip(";")
    ref = quote_identifier(column)
    text = (text.replace('"{DATA_SCHEMA}"."{DATA_TABLE}"', f"{quote_identifier(schema)}.{quote_identifier(table)}")
            .replace("{COL_VALUE}", _text_value(ref, db_type)).replace('"{COL_NAME}"', ref))
    if sampling:
        text = text.replace("{SAMPLE_PERCENT_CALC}", repr(float(sampling.sample_percent)))
    if re.search(r"\{(COL_VALUE|COL_NAME|DATA_SCHEMA|DATA_TABLE|SAMPLE_PERCENT_CALC)\}", text):
        raise ValueError("project_secondary_profiling_query.sql: a placeholder was left unfilled")
    return text


@dataclass(frozen=True)
class Frequent:
    """One column's frequency analysis: the top values `[(value, ct)]` most frequent first, the "other" row
    `(distinct values, ct)` when the column held more than ten, and the distinct-value hash."""

    values: tuple[tuple[str, int], ...]
    other: tuple[int, int] | None
    distinct_value_hash: str | None

    def as_json(self) -> dict:
        out: dict = {"values": [{"value": v, "ct": c} for v, c in self.values]}
        if self.other:
            out["other"] = {"distinct_ct": self.other[0], "ct": self.other[1]}
        return out


def _lift(count, whole: Decimal, sampled: Decimal) -> int | None:
    try:
        number = Decimal(str(count))
    except Exception:  # noqa: BLE001 - a count that is not a number stays unknown
        return None
    return int((number * whole / sampled).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def assemble(rows: list[dict], whole_value_ct: int | None, sampled: bool) -> Frequent | None:
    """TestGen's `build_frequent_values` then, on a sample, `calibrate_frequent_values`. None when the statement returned no
    value (a column of blanks)."""
    if not rows:
        return None
    ranked = sorted((r for r in rows if r.get("value") is not None), key=lambda r: r["value_rank"])
    other_row = next((r for r in rows if r.get("value") is None), None)
    values = [(str(r["value"]).replace("\x00", ""), int(r["value_ct"])) for r in ranked]
    other = None if other_row is None else (int(other_row["other_distinct_ct"]), int(other_row["value_ct"]))
    if sampled and whole_value_ct and rows[0].get("sample_value_ct"):
        whole, seen = Decimal(whole_value_ct), Decimal(str(rows[0]["sample_value_ct"]))
        if whole != seen and seen:
            with localcontext() as context:
                context.prec = 60
                values = [(v, _lift(c, whole, seen)) for v, c in values]
                other = None if other is None else (other[0], _lift(other[1], whole, seen))
    return Frequent(tuple(values), other, rows[0].get("distinct_value_hash"))


def measure_table(conn, schema: str, table: str, columns: list[tuple[str, str | None, int | None]], record_ct: int | None,
                  bounds: ProfileBounds, clock: Callable[[], float] = time.monotonic
                  ) -> tuple[dict[str, tuple[Frequent | None, TableSampling | None]], list[tuple[str, str]]]:
    """({column: (frequency, sampling)}, [(column, reason)]) for a table's eligible columns `[(name, catalog type, value_ct)]`,
    each its own statement inside `bounds.frequency_budget_seconds`."""
    found: dict[str, tuple[Frequent | None, TableSampling | None]] = {}
    gaps: list[tuple[str, str]] = []
    started, budget = clock(), bounds.frequency_budget_seconds
    sampling = sampling_for(record_ct)
    for index, (column, db_type, value_ct) in enumerate(columns):
        remaining = budget - (clock() - started)
        if remaining <= 0:
            reason = f"exceeded {budget / 60:g} min · frequent values not measured"
            gaps.extend((c, reason) for c, _, _ in columns[index:])
            break
        try:
            sql = build_statement(schema, table, column, db_type, sampling)
            try:
                begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining))))
                rows = conn.execute_query_isolated(sql, None)
            finally:
                conn.end_transaction()
            found[column] = (assemble(list(rows or []), value_ct, sampling is not None), sampling)
        except Exception as exc:  # noqa: BLE001 - any failure of this optional statement is reported, never raised
            if classify_error(exc) != "not_permitted" and clock() - started >= budget:
                reason = f"exceeded {budget / 60:g} min · frequent values not measured"
                gaps.extend((c, reason) for c, _, _ in columns[index:])
                break
            gaps.append((column, f"frequent values not measured · {error_text(exc)}"))
    return found, gaps


def baseline_text(frequent: dict | None, *, limit: int | None = None) -> str | None:
    """`value (ct), value (ct), ...` in rank order: the default baseline of Match_Frequent_Values and Match_Frequent_Patterns
    (upstream's `STRING_AGG(value || ' (' || ct || ')', ', ' ORDER BY rank)`). None when there is no entry."""
    entries = (frequent or {}).get("values") or []
    if not entries:
        return None
    text = ", ".join(f"{e['value']} ({e['ct']})" for e in entries)
    return text if limit is None or len(text) <= limit else text
