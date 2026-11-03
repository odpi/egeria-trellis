"""The three QUERY test types of Table Comparison that compare a text column's baseline text (port step 8b):
Match_Frequent_Values, Match_Frequent_Patterns and Match_Distinct_Hash.

Upstream's `run_type` QUERY means the test is a statement of its own, not an aggregate in the per-table SELECT: each type's
Postgres `test_templates` entry returns one row with the measured text (`result_signal`) and `result_code` (1 matches the
baseline, 0 differs, NULL no baseline). The vendored template is run as it stands; Resource Explorer fills its placeholders
(identifiers quoted from the inventory, the baseline as a string literal, the TestGen run bookkeeping columns as constants
RE does not use) and reads `result_code`. Like Dupe_Rows it runs in its own read-only transaction inside its own budget
(`ProfileBounds.dupe_rows_budget_seconds`, the per-table budget of the QUERY checks), and a cancelled or timed-out read is
`not_measured · exceeded N min`, never a pass.

Not applied to these three: an edit made to the published rule in Egeria. The baseline is long text and the rule's
threshold field is a number, so a threshold edited in Egeria has nowhere to go; the check says "baseline as accepted".
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import replace

from resource_explorer.surveyors.database.quality.catalogue import (
    TEST_TYPES_DIR,
    ParameterError,
    quote_identifier,
    read_raw,
    string_literal,
)
from resource_explorer.surveyors.database.quality.value_hashes import _text_value
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

TYPES = ("Match_Frequent_Values", "Match_Frequent_Patterns", "Match_Distinct_Hash")
#: The baseline is stored and compared as text; a longer one is not a frequent-values baseline (upstream's selection caps it at
#: 1000 characters, and `result_signal` is `LEFT(measure, 1000)`).
MAX_BASELINE = 1000


def template(test_type: str) -> str:
    """The vendored Postgres `test_templates` entry of a QUERY type, verbatim."""
    for entry in read_raw(TEST_TYPES_DIR / f"{test_type}.yaml").get("test_templates") or []:
        if entry.get("sql_flavor") == "postgresql":
            return entry["template"]
    raise ParameterError(f"{test_type} has no Postgres template in the vendored catalog")


def _literal_content(text: str) -> str:
    return text.replace("'", "''")


def build_statement(test_type: str, schema: str, table: str, column: str, db_type: str | None, baseline: str | None,
                    run_date: str) -> str:
    """The vendored template with every placeholder filled. An identifier is quoted (`quote_identifier`), the baseline a
    string literal or NULL, the rest inert constants."""
    if baseline is not None and (not isinstance(baseline, str) or len(baseline) > MAX_BASELINE):
        raise ParameterError(f"baseline_value must be text of at most {MAX_BASELINE} characters")
    text = template(test_type)
    ref = quote_identifier(column)
    fills = {
        "{QUOTE}{SCHEMA_NAME}{QUOTE}": quote_identifier(schema), "{QUOTE}{TABLE_NAME}{QUOTE}": quote_identifier(table),
        "{COLUMN_VALUE}": _text_value(ref, db_type), "{COLUMN_NAME}": ref,
        "{BASELINE_VALUE_LITERAL}": "NULL" if baseline is None else string_literal(baseline),
        "{COLUMN_NAME_NO_QUOTES}": _literal_content(column), "{SCHEMA_NAME}": _literal_content(schema),
        "{TABLE_NAME}": _literal_content(table), "{TEST_TYPE}": test_type, "{RUN_DATE}": run_date,
        "{TEST_DEFINITION_ID}": "", "{TEST_SUITE_ID}": "", "{TEST_RUN_ID}": "", "{INPUT_PARAMETERS}": "",
        "{SKIP_ERRORS}": "0",
    }
    # Longest keys first: "{QUOTE}{SCHEMA_NAME}{QUOTE}" before "{SCHEMA_NAME}".
    for key in sorted(fills, key=len, reverse=True):
        text = text.replace(key, fills[key])
    if "{" in text and any(token in text for token in ("{QUOTE}", "{COLUMN", "{BASELINE", "{SCHEMA", "{TABLE", "{TEST_")):
        raise ParameterError(f"{test_type}: a placeholder of the vendored template was left unfilled")
    return text


def exceeded_reason(budget_seconds: float) -> str:
    return f"exceeded {budget_seconds / 60:g} min · not compared"


def run_table(conn, schema: str, table: str, check: dict, bounds: ProfileBounds,
              clock: Callable[[], float] = time.monotonic, *, run_date: str = "") -> None:
    """Run one QUERY check in its own read-only transaction inside its own budget and conclude its row."""
    row, test_type = check["row"], check["type"]
    budget = bounds.dupe_rows_budget_seconds
    started = clock()
    if budget <= 0:
        row.update(status="not_measured", reason=exceeded_reason(budget))
        return
    try:
        sql = build_statement(test_type.test_type, schema, table, check["column"], check.get("db_type"),
                              check["baseline"], run_date)
    except ParameterError as exc:
        row.update(status="not_established", reason=str(exc))
        return
    try:
        begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(budget))))
        result = conn.execute_query_isolated(sql, None)
    except Exception as exc:  # noqa: BLE001 - classified below
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
        row.update(status="error", reason="the comparison query returned no row")
        return
    values = result[0]
    code, signal = values.get("result_code"), values.get("result_signal")
    row["measure_text"] = None if signal is None else str(signal)
    if code is None:
        row.update(status="not_established", reason="baseline is missing")
    elif int(code) == 1:
        row.update(status="pass", measure=1.0, reason=f"{values.get('result_message') or 'matches baseline'}")
    else:
        row.update(status=check["fail_status"], measure=0.0,
                   reason=f"{values.get('result_message') or 'differs from baseline'} "
                          f"(now: {row['measure_text'] if row['measure_text'] is not None else 'no values'})")
