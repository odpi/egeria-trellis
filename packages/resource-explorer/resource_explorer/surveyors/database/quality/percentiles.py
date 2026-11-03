"""The exact percentiles of a number column (port step 6, owner 2026-10-29: "add the columns").

TestGen's `functional_datatype.sql` decides 'Measurement Spike' from `percentile_25`, `percentile_50` and
`percentile_75`. Upstream takes them in a sub-select of its profile query (`is_N_exact_pctile`); the vendored text is
`configdata/quality/testgen/postgresql/percentiles.sql`, pinned here by SHA-256.

Resource Explorer runs them as a statement of their own per table, inside their own time budget
(`ProfileBounds.percentile_budget_seconds`, 5 minutes like Dupe_Rows'), not inside the profile's single scan: each is a
sort of the whole column, the most expensive thing the profile would do, and a table too big to sort must not lose its
other measures. A table that does not finish keeps every other measure and reads the percentiles NULL, with the reason
in the run summary (`percentiles_not_measured`): NULL is "not measured", the inference then falls back to the bound that
cannot decide, never to "no spike".
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import replace

from resource_explorer.surveyors.database.quality.catalogue import (
    POSTGRES_DIR,
    quote_identifier,
    sha256_text,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

PERCENTILES_SQL = POSTGRES_DIR / "percentiles.sql"
#: SHA-256 of the three PERCENTILE_CONT lines of the vendored file. A change upstream fails the pin test.
PERCENTILES_SHA256 = "09b197a70e66cee5642e206b9f3f98d1efe4e7a22134e154000c128a0e873a6c"
MEASURES = ("percentile_25", "percentile_50", "percentile_75")
#: Number columns per statement; each contributes three sorts to one scan.
COLUMNS_PER_STATEMENT = 100
_LINE = re.compile(r'PERCENTILE_CONT\(0\.\d+\) WITHIN GROUP \(ORDER BY "\{COL_NAME\}"::NUMERIC\) AS pct_(25|50|75)')


def vendored_lines() -> list[str]:
    return [ln for ln in PERCENTILES_SQL.read_text(encoding="utf-8").splitlines() if ln.lstrip().startswith("PERCENTILE_CONT")]


def pin_matches() -> bool:
    return sha256_text("\n".join(vendored_lines()) + "\n") == PERCENTILES_SHA256


def build_statement(schema: str, table: str, columns: list[str]) -> tuple[str, dict[str, tuple[str, int]]]:
    """(the SQL, {alias: (column, 0|1|2)}). Each vendored line is filled for one column, its alias made unique; the
    identifier is the inventory's, quoted, never free text."""
    lines = vendored_lines()
    if len(lines) != 3 or not all(_LINE.search(ln) for ln in lines):
        raise ValueError("percentiles.sql no longer has the three PERCENTILE_CONT lines this port runs")
    parts: list[str] = []
    aliases: dict[str, tuple[str, int]] = {}
    for i, column in enumerate(columns):
        for j, line in enumerate(lines):
            alias = f"c{i}_pct_{(25, 50, 75)[j]}"
            expr = line.strip().rstrip(",").replace("{COL_NAME}", column.replace('"', '""'))
            parts.append(re.sub(r"AS pct_\d+$", f"AS {alias}", expr))
            aliases[alias] = (column, j)
    return f"SELECT {', '.join(parts)} FROM {quote_identifier(schema)}.{quote_identifier(table)}", aliases


def measure_table(conn, schema: str, table: str, columns: list[str], bounds: ProfileBounds,
                  clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, tuple], str]:
    """({column: (p25, p50, p75)}, reason). The reason is empty when every column was measured; otherwise it says why
    the rest were not (budget, privilege, error), and those columns are absent from the answer. One read-only
    transaction per statement, ended at once; the table's budget is the total across its statements."""
    found: dict[str, tuple] = {}
    started = clock()
    budget = bounds.percentile_budget_seconds
    for start in range(0, len(columns), COLUMNS_PER_STATEMENT):
        remaining = budget - (clock() - started)
        if remaining <= 0:
            return found, f"exceeded {budget / 60:g} min · percentiles not measured"
        sql, aliases = build_statement(schema, table, columns[start:start + COLUMNS_PER_STATEMENT])
        try:
            begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining))))
            result = conn.execute_query_isolated(sql, None)
        except Exception as exc:  # noqa: BLE001 - any failure of this optional statement is reported, never raised
            state = classify_error(exc)
            if state != "not_permitted" and clock() - started >= budget:
                return found, f"exceeded {budget / 60:g} min · percentiles not measured"
            return found, f"percentiles not measured · {error_text(exc)}"
        finally:
            conn.end_transaction()
        if not result:
            return found, "percentiles not measured · the query returned no row"
        values = result[0]
        grouped: dict[str, list] = {}
        for alias, (column, j) in aliases.items():
            grouped.setdefault(column, [None, None, None])[j] = values.get(alias)
        for column, trio in grouped.items():
            found[column] = tuple(None if v is None else float(v) for v in trio)
    return found, ""
