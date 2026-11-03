"""Dupe_Rows: duplicate rows in a table, with its own statement and its own per-table time budget.

DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md section 4, Q4 port order step 4. Ported from the vendored
`test_types/Dupe_Rows.yaml` (the Postgres entry of `test_templates`), `gen_query_tests/gen_Dupe_Rows.sql` and the
test type's own parameters (`groupby_names`, `skip_errors`).

- **Selection** is upstream's generation template: one test per profiled table, every column as the group key
  (`STRING_AGG(column_name ORDER BY position)`), `skip_errors` 0. Propose reads the stored inventory for the
  columns. The owner's ruling (2026-10-26, Q4 section 7 question 3) adds one thing upstream does not do: a column of
  `general_type` X (no equality, for example json) is left out of the key, and the check names it.
- **The run is a second execution path**, not an aggregate in the per-table profile SELECT: a QUERY type. The
  grouping subselect is the vendored template's, verbatim (`GROUPING_SQL`, pinned); RE wraps it in its own select list
  so the result states what it counted, which upstream's `result_measure` alone does not: the duplicate row groups,
  the rows in them, and the table's rows.
- **Its own budget**: 5 minutes per table (`ProfileBounds.dupe_rows_budget_seconds`). The statement's timeout is the
  budget, so Postgres cancels what is not done. A statement that did not finish is `not_measured · exceeded 5 min`,
  never a zero and never a pass; nothing partial is kept.
- **Absent is not zero**: "could not run" and "no duplicates" are different words. A table whose key has no column
  left (every column is X) is `not_established`, not a pass.

Upstream's result: `result_measure` is `SUM(record_ct)` over the duplicated groups (the rows in them, the first of each
included) and the test fails when `COUNT(*)` of the groups is above `skip_errors`. Both are kept: the rows are the
measure, the groups are what the threshold counts.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import replace

from resource_explorer.surveyors.database.quality.catalogue import (
    TEST_TYPES_DIR,
    ParameterError,
    TestType,
    as_number,
    postgres_template,
    quote_identifier,
    read_raw,
    sha256_text,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

TEST_TYPE = "Dupe_Rows"
#: The columns that cannot be a group key: no equality (json, xml, geometry, arrays: general_type X).
NO_EQUALITY = "X"
#: SHA-256 of the vendored Postgres template's grouping subselect, `FROM ( SELECT {GROUPBY_NAMES}, COUNT(*) ... ) test`,
#: which `build_statement` runs verbatim. A change upstream fails the pin test and forces a re-port.
GROUPING_SHA256 = "ca922d3a0ddca1038633fb97e402df70b31e6c41841c5e374ee1d8317607e4a4"

_GROUPING = re.compile(r"FROM \(\s*SELECT \{GROUPBY_NAMES\}.*?\)\s*test", re.DOTALL)


def grouping_template() -> str:
    """The vendored template's grouping subselect, as written upstream (placeholders unfilled)."""
    template = postgres_template(_raw())
    found = _GROUPING.search(template)
    if not found:
        raise ParameterError("Dupe_Rows's vendored template no longer has the grouping subselect this port runs")
    return found.group(0)


def _raw() -> dict:
    return read_raw(TEST_TYPES_DIR / f"{TEST_TYPE}.yaml")


def default_params(test_type: TestType, profile: dict) -> dict:
    """The parameters a proposal starts with: `gen_Dupe_Rows.sql`'s `groupby_names` (every column of the table, in
    position order) less the columns of general_type X, `skip_errors` 0, and the severity.

    `profile["table_columns"]` is the table's `[(column, general_type), ...]` in ordinal order, read by propose from the
    stored inventory. The columns left out are kept in `excluded_columns` so the result can name them."""
    columns = profile.get("table_columns")
    if not columns:
        raise ParameterError("the stored inventory lists no columns for this table, so there is no group key")
    key = [name for name, gtype in columns if gtype != NO_EQUALITY]
    left_out = [{"column": name, "general_type": gtype} for name, gtype in columns if gtype == NO_EQUALITY]
    if not key:
        raise ParameterError("every column of the table has general type X (no equality), so there is no column to "
                             "group by: " + ", ".join(c["column"] for c in left_out))
    return {"groupby_names": key, "skip_errors": 0, "excluded_columns": left_out, "severity": test_type.severity}


def validated(params: dict, known_columns: set[str]) -> tuple[list[str], int, list[dict]]:
    """(key columns, skip_errors, excluded columns) from the accepted parameters, or ParameterError quoting what is
    wrong. Every key column must be in the stored inventory: nothing read from a person or from Egeria is put in the
    statement unless it names a column RE holds."""
    key = params.get("groupby_names")
    if not isinstance(key, list) or not key or not all(isinstance(c, str) and c for c in key):
        raise ParameterError(f"groupby_names must be a list of column names, got {key!r}")
    gone = [c for c in key if c not in known_columns]
    if gone:
        raise ParameterError(f"column {', '.join(gone)} of the group key is no longer in inventory")
    skip = as_number("skip_errors", params.get("skip_errors", 0))
    if skip < 0 or int(skip) != skip:
        raise ParameterError(f"skip_errors must be a whole number of at least 0, got {skip!r}")
    left_out = params.get("excluded_columns") or []
    if not isinstance(left_out, list):
        raise ParameterError(f"excluded_columns must be a list, got {left_out!r}")
    return key, int(skip), [c for c in left_out if isinstance(c, dict) and c.get("column")]


def build_statement(schema: str, table: str, key: list[str]) -> str:
    """The run statement: RE's select list over the vendored grouping subselect. Identifiers are quoted; the key
    is the inventory's column names, never free text. `{SUBSET_CONDITION}` is `1=1` (no subset)."""
    names = ", ".join(quote_identifier(c) for c in key)
    table_ref = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    grouping = (grouping_template().replace("{GROUPBY_NAMES}", names).replace("{QUOTE}", '"')
                .replace("{SCHEMA_NAME}", schema.replace('"', '""')).replace("{TABLE_NAME}", table.replace('"', '""'))
                .replace("{SUBSET_CONDITION}", "1=1"))
    return ("SELECT COALESCE(SUM(record_ct), 0) AS rows_in_groups, COUNT(*) AS group_ct, "
            f"(SELECT COUNT(*) FROM {table_ref}) AS table_ct {grouping}")


def exceeded_reason(budget_seconds: float) -> str:
    """The words a Dupe_Rows read that ran out of its budget carries. The state is `not_measured`."""
    return f"exceeded {budget_seconds / 60:g} min · duplicates not counted"


def _left_out_words(left_out: list[dict]) -> str:
    if not left_out:
        return ""
    names = ", ".join(f"{c['column']} (general type {c.get('general_type') or NO_EQUALITY})" for c in left_out)
    return f"; left out of the group key, no equality: {names}"


def _groups(n: int) -> str:
    return f"{n} duplicate row group{'' if n == 1 else 's'}"


def conclude(row: dict, *, groups: int, rows_in_groups: int, table_rows: int, skip: int, left_out: list[dict],
             fail_status: str) -> None:
    """Fill a result row from what was counted. `fail_status` is the status a failing check gets at its severity."""
    row["record_ct"], row["measure"] = int(table_rows), int(rows_in_groups)
    held = groups <= skip
    held_words = (f"no duplicate rows: {_groups(groups)} in {table_rows} rows" if groups == 0 else
                  f"{_groups(groups)} holding {rows_in_groups} of {table_rows} rows")
    row["status"] = "pass" if held else fail_status
    row["reason"] = (f"{held_words} (fails when duplicate row groups > {skip})" + _left_out_words(left_out))


def run_table(conn, schema: str, table: str, check: dict, bounds: ProfileBounds,
              clock: Callable[[], float] = time.monotonic) -> None:
    """Run one Dupe_Rows check in its own read-only transaction, inside its own budget, and conclude its row.

    The statement timeout is the budget, so Postgres cancels what is not done. Whatever ends the statement with
    the budget spent (a cancellation, or any error that arrives after it) is `not_measured · exceeded N min`; an
    error that is a privilege error is `not_permitted` first, as the profile classifies."""
    row = check["row"]
    budget = bounds.dupe_rows_budget_seconds
    started = clock()
    if budget <= 0:
        row.update(status="not_measured", reason=exceeded_reason(budget))
        return
    sql = build_statement(schema, table, check["key"])
    try:
        # One transaction for this one statement, ended at once so no lock outlives it. Params None, so psycopg2 never
        # reads a "%" in a name as a parameter marker.
        begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(budget))))
        result = conn.execute_query_isolated(sql, None)
    except Exception as exc:
        state = classify_error(exc)
        cancelled = getattr(exc, "pgcode", None) == "57014" or "statement timeout" in str(exc).lower()
        if state != "not_permitted" and (cancelled or clock() - started >= budget):
            row.update(status="not_measured", reason=exceeded_reason(budget))
        elif state == "not_permitted":
            row.update(status="not_permitted", reason=error_text(exc))
        else:
            row.update(status="error", reason=error_text(exc))
        return
    finally:
        conn.end_transaction()
    if not result:
        row.update(status="error", reason="the duplicate-rows query returned no row")
        return
    values = result[0]
    conclude(row, groups=int(values["group_ct"]), rows_in_groups=int(values["rows_in_groups"]),
             table_rows=int(values["table_ct"]), skip=check["skip"], left_out=check["left_out"],
             fail_status=check["fail_status"])


def pin_matches() -> bool:
    return sha256_text(grouping_template()) == GROUPING_SHA256
