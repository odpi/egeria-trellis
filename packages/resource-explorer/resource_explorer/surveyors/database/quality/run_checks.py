"""`run_quality_checks`: execute the accepted checks with the vendored Postgres SQL.

DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §6. The input is the accepted verdicts (their parameters as accepted);
the SQL always comes from the vendored catalogue, keyed by test type, with every parameter validated by type
(`catalogue.render_measure`). Checks are batched one aggregate SELECT per table; if a batch errors, each of its
checks is re-run alone so one bad check cannot hide the others. Results go to `database_quality_results`.

**TestGen's NULL-means-pass trap is not copied.** TestGen computes `CASE WHEN measure op condition THEN fail
ELSE pass END`, so a NULL measure (Missing_Pct on an empty table, a zero baseline) falls through to pass. Here the
measure itself is read and mapped:

    NULL measure, or division by zero          -> not_established ("measure undefined ..."), except for a type
                                                  whose catalogue entry sets `null_measure_fails` (the B1a and
                                                  B1b `Match_*` types): fail at its severity, as upstream, since an
                                                  emptied table or column departs from the baseline
    query error or timeout                     -> error, with Postgres's words
    privilege error, or row-level security     -> not_permitted
      that filters this account
    fails, severity error / warning / info     -> fail / warning / logged
    satisfies the condition                    -> pass
    a partitioned parent or not a table        -> not_applicable (`value_profile.refusal`, shared with the profile;
                                                  there is no size cap: a large table is read whole)
    parameter that does not validate           -> not_established, quoting the value
    column or table gone from the inventory    -> not_established ("... no longer in inventory")

**Egeria.** Accepted checks are not yet published as DataQualityRules (Publish is a later change), so every
check runs from its verdict row and is labelled `rule_source = unpublished`. Each check that was evaluated
is also returned as one `QualityCheckAnnotation` (`quality_annotations`; Egeria's QualityAnnotation), which the
database publish turns into annotations on the run's SurveyReport. The outcome is a state word on every one; only a
check that reached a conclusion has a `qualityScore` (a check that did not conclude is published too, with none, so
a 0 never reads as failed). When Publish lands, the rule read
from Egeria overrides the verdict's threshold, operator and severity here (design §6's table).

**Text and exact measures.** Match_Min_Date and Match_Max_Date measure `TO_CHAR(..., 'YYYY-MM-DD HH24:MI:SS')` and
compare it with the baseline text, as upstream; the comparison is on the text. Match_Sum and Match_Fractional_Sum
measure an exact NUMERIC and compare it as a Decimal, never through a float. Either way the value as compared is
stored in `measure_text` (profile 1b), and `measure` holds it as a number for charts: a date as seconds from
1970-01-01 of the same wall clock (`catalogue.text_measure_number`), a sum as its nearest float
(DESIGN-QUALITY-B1A-BASELINE-MATCH.md §4, DESIGN-QUALITY-PROFILE-1B.md).

**NOT BETWEEN.** Match_Sum and Match_Stdev fail outside a range (`catalogue.Between`). Match_Sum's range reads
`COUNT(column)` (its tolerance counts values), which is measured beside the sum in the same SELECT.

The baseline is the last ACCEPTED one, never the current profile, so a scheduled run of this step on its own is
valid; each result records the profile run it was measured against. Each table is read in its own read-only
transaction, ended at once, so no lock outlives its table.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal

from resource_explorer.surveyors.database.quality import dupe_rows, query_types, schema_drift
from resource_explorer.surveyors.database.quality.catalogue import (
    CONDITION_AGGREGATES,
    MUST_BE,
    RUN_METADATA,
    RUN_QUERY,
    Between,
    ParameterError,
    SumTolerance,
    as_number,
    egeria_dimension,
    fails,
    is_money_type,
    load_catalogue,
    original_dimension,
    quote_identifier,
    render_measure,
    text_measure_from_number,
    text_measure_number,
    threshold,
    threshold_key,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
    read_table_facts,
    refusal,
    run_date_text,
)

RESULTS_TABLE = "database_quality_results"
ANALYSIS_STEP = "run_quality_checks"

#: severity -> the status a failing check gets (TestGen's Fail / Warning / Log).
FAIL_STATUS = {"error": "fail", "warning": "warning", "info": "logged"}
#: Statuses that reached a conclusion; only these will become QualityAnnotations when publishing lands.
CONCLUDED = frozenset({"pass", "warning", "fail", "logged"})
#: A table the shared refusal rules will not read: its state -> the check's status.
_REFUSED_STATUS = {"not_applicable": "not_measured", "not_measured": "not_measured", "not_permitted": "not_permitted"}
RULE_UNPUBLISHED = "unpublished"
UNDEFINED = "measure undefined (empty table / zero baseline)"
#: The reason a NULL measure fails a type whose catalog entry sets `null_measure_fails`.
UNDEFINED_FAILS = ("measure undefined (the table or column has no values to measure); {test_type} fails on an "
                   "undefined measure, as its catalog entry says (null_measure_fails)")
#: The sentence a run with no accepted check ends with (survey-invalid). Words of the design: "no accepted checks".
NO_ACCEPTED_CHECKS = "no accepted checks: accept proposals in Curate first; nothing was run"
#: What Egeria's annotation says this result is (its `annotationType`).
ANNOTATION_TYPE_NAME = "Database Quality Check"


def _resolve(registry, key: str, verdict: dict) -> tuple[str, str, str, str] | None:
    """(schema, table, column, test_type) for an accepted check: from the proposal it accepted, else parsed from
    the key when that is unambiguous. None when it cannot be told."""
    proposal = registry.get_quality_proposal(verdict.get("proposal_id"))
    if proposal and proposal.get("check_key") == key and proposal.get("database_slug") == verdict.get("database_slug"):
        return (proposal["schema_name"], proposal["table_name"], proposal.get("column_name") or "",
                proposal["test_type"])
    path, sep, test_type = key.rpartition("::")
    parts = path.split(".")
    if not sep or len(parts) not in (2, 3):
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else "", test_type


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value) if isinstance(value, (int, float)) else value


def apply_rule(rule: dict, test_type, params: dict, row: dict) -> tuple[dict, str]:
    """A published rule's say over one check (design §6 of the value-profile note: Egeria wins). `rule` is one entry
    of `egeria_rules.rules_for_run`. Returns (the parameters to run with, a refusal sentence or ""), and labels the
    row with where its rule came from.

        egeria       the rule's severity and threshold override the verdict's; an operator other than the one the
                     vendored SQL tests, a rule RE did not write (checkType sql, an expression) or a DEPRECATED
                     rule is not run
        local_cache  Egeria could not be read: the verdict's values, labelled so
        missing      the rule is gone from Egeria: not run, "rule not found in Egeria" """
    row.update(rule_guid=rule.get("rule_guid", ""), rule_source=rule.get("source", RULE_UNPUBLISHED),
               rule_note=rule.get("note", ""))
    source = rule.get("source")
    if source == "missing":
        return params, "rule not found in Egeria"
    if source != "egeria":
        return params, ""
    props = rule.get("properties") or {}
    if props.get("contentStatus") == "DEPRECATED":
        return params, "rule deprecated in Egeria"
    if props.get("checkType") == "sql" or props.get("expression"):
        return params, "rule changed in Egeria to one Resource Explorer did not write (sql or an expression): not run"
    want = MUST_BE.get(test_type.test_operator, test_type.test_operator)
    if props.get("comparisonOperator") not in (None, "", want):
        return params, (f"operator edited in Egeria to {props['comparisonOperator']}; the vendored check tests only "
                        f"{want}")
    merged = dict(params)
    if props.get("severity"):
        merged["severity"] = props["severity"]
    values = props.get("thresholdValues") or []
    if values:
        try:
            merged[threshold_key(test_type)] = values[0]
        except ParameterError as exc:
            return params, str(exc)
    return merged, ""


def _limit_json(limit):
    """A condition's limit as `threshold_values_json` holds it: a range as its two bounds, an exact decimal as its text
    (so no digit is lost to a float), anything else as it is."""
    if isinstance(limit, Between):
        return [_limit_json(limit.low), _limit_json(limit.high)]
    if isinstance(limit, Decimal):
        return format(limit, "f")
    return limit


def run_quality_checks(
    db_entity, registry, connect: Callable[[], AbstractContextManager], *,
    bounds: ProfileBounds, surveyed_at: str | None = None, ran_as: str = "", rules: dict | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """`rules`: `egeria_rules.rules_for_run` for the checks with a published rule (None: none read, every check
    runs from its verdict, `rule_source = unpublished`)."""
    slug = db_entity.slug
    surveyed_at = surveyed_at or datetime.now(UTC).isoformat()
    accepted = {k: v for k, v in registry.get_quality_verdicts(slug).items() if v.get("verdict") == "accepted"}
    if not accepted:
        # Nothing to run is not a run that found nothing: the step is declined (survey-invalid, never an empty
        # completed report) with this sentence. `skipped_by_design` is what the executor reads.
        return {"quality_checks": {
            "surveyed_at": surveyed_at, "state": "not_established", "reason": NO_ACCEPTED_CHECKS,
        }, "skipped_by_design": NO_ACCEPTED_CHECKS}

    catalogue = load_catalogue()
    columns = registry.query_detail_rows("database_columns", slug)
    column_set = {(c["schema_name"], c["table_name"], c["column_name"]) for c in columns}
    money = {(c["schema_name"], c["table_name"], c["column_name"]) for c in columns
             if is_money_type(c.get("base_type") or c.get("data_type"))}
    table_set = {(c["schema_name"], c["table_name"]) for c in columns}
    catalog_types = {(c["schema_name"], c["table_name"], c["column_name"]): c.get("base_type") or c.get("data_type")
                     for c in columns}
    tables = {(t["schema_name"], t["table_name"]): t for t in registry.query_detail_rows("database_tables", slug)}
    run_date = run_date_text(surveyed_at)

    results: list[dict] = []
    runnable: dict[tuple[str, str], list[dict]] = {}
    dupe_runnable: dict[tuple[str, str], list[dict]] = {}
    query_runnable: dict[tuple[str, str], list[dict]] = {}
    drift_checks: list[tuple[dict, object, dict]] = []

    for key, verdict in sorted(accepted.items()):
        params = verdict.get("params_json")
        row = {"check_key": key, "schema_name": "", "table_name": "", "column_name": "", "test_type": "",
               "measure": None, "measure_text": None, "record_ct": None, "status": "not_established", "reason": "",
               "severity": "", "comparison_operator": "", "threshold_values_json": None,
               "params_json": params if isinstance(params, dict) else None,
               "baseline_profile_surveyed_at": (params or {}).get("profile_surveyed_at", "")
               if isinstance(params, dict) else "",
               "rule_source": RULE_UNPUBLISHED, "rule_guid": "", "ran_as": ran_as, "state": "not_measured"}
        results.append(row)
        resolved = _resolve(registry, key, verdict)
        if resolved is None:
            row["reason"] = f"cannot tell which column {key!r} names"
            continue
        schema, table, column, type_name = resolved
        row.update(schema_name=schema, table_name=table, column_name=column, test_type=type_name)
        test_type = catalogue.get(type_name)
        if test_type is None:
            row["reason"] = f"{type_name!r} is not a test type in the vendored catalog"
            continue
        if test_type.is_table_check == bool(column):
            scope = "a table" if test_type.is_table_check else "a column"
            row["reason"] = f"{type_name} is {scope} check, but {key!r} does not name {scope}"
            continue
        if not isinstance(params, dict):
            row["reason"] = f"the accepted parameters are not a JSON object: {params!r}"
            continue
        if test_type.run_type == RUN_METADATA:
            # The drift checks compare surveys, so a table or column that is gone from the newest one is their
            # finding, not "no longer in inventory". No connection is used.
            drift_checks.append((row, test_type, params))
            continue
        if (schema, table) not in table_set:
            row["reason"] = "table no longer in inventory"
            continue
        if test_type.run_type == RUN_QUERY and type_name in query_types.TYPES:
            severity = params.get("severity")
            if severity not in FAIL_STATUS:
                row["reason"] = f"severity must be one of {sorted(FAIL_STATUS)}, got {severity!r}"
                continue
            if (schema, table, column) not in column_set:
                row["reason"] = "column no longer in inventory"
                continue
            baseline = params.get("baseline_value")
            row.update(severity=severity, comparison_operator=MUST_BE["<>"])
            query_runnable.setdefault((schema, table), []).append(
                {"row": row, "type": test_type, "column": column, "db_type": catalog_types.get((schema, table, column)),
                 "baseline": baseline if isinstance(baseline, str) else None, "fail_status": FAIL_STATUS[severity]})
            continue
        if test_type.run_type == RUN_QUERY:
            severity = params.get("severity")
            if severity not in FAIL_STATUS:
                row["reason"] = f"severity must be one of {sorted(FAIL_STATUS)}, got {severity!r}"
                continue
            known = {c for (s_, t_, c) in column_set if (s_, t_) == (schema, table)}
            try:
                key_columns, skip, left_out = dupe_rows.validated(params, known)
            except ParameterError as exc:
                row["reason"] = str(exc)
                continue
            row.update(severity=severity, comparison_operator=MUST_BE[">"], threshold_values_json=[skip])
            dupe_runnable.setdefault((schema, table), []).append(
                {"row": row, "key": key_columns, "skip": skip, "left_out": left_out,
                 "fail_status": FAIL_STATUS[severity]})
            continue
        if column and (schema, table, column) not in column_set:
            row["reason"] = "column no longer in inventory"
            continue
        rule = (rules or {}).get(key)
        if rule:
            params, refused = apply_rule(rule, test_type, params, row)
            if refused:
                row["reason"] = refused
                continue
        severity = params.get("severity")
        if severity not in FAIL_STATUS:
            row["reason"] = f"severity must be one of {sorted(FAIL_STATUS)}, got {severity!r}"
            continue
        row["severity"] = severity
        row["comparison_operator"] = MUST_BE.get(test_type.test_operator, test_type.test_operator)
        try:
            limit = threshold(test_type, params)
            # Match_Sum's bounds read the run's COUNT, so they are recorded only once the run has them: until then
            # NULL, never the baseline and tolerance, which would read as bounds.
            row["threshold_values_json"] = (None if isinstance(limit, SumTolerance) else
                                            _limit_json(limit) if isinstance(limit, Between) else [_limit_json(limit)])
            if "{BASELINE_CT}" in test_type.measure and as_number("baseline_ct", params.get("baseline_ct")) == 0:
                row["reason"] = f"{UNDEFINED}: baseline_ct is 0"
                continue
            is_money = (schema, table, column) in money
            hash_column, key_column = _hash_columns(test_type, schema, table, column, params, catalog_types)
            sql = render_measure(test_type, column, params, run_date, is_money=is_money, hash_column=hash_column,
                                 key_column=key_column)
            # A run-time aggregate the condition reads (Match_Sum's COUNT), from the vendored text, filled the same way.
            extra = CONDITION_AGGREGATES.get(type_name)
            extra_sql = extra.replace("{COLUMN_NAME}", quote_identifier(column)) if extra else None
        except ParameterError as exc:
            row["reason"] = str(exc)
            continue
        runnable.setdefault((schema, table), []).append(
            {"row": row, "sql": sql, "type": test_type, "limit": limit, "extra_sql": extra_sql})

    if drift_checks:
        drift = schema_drift.Drift(registry, slug)
        for row, test_type, params in drift_checks:
            schema_drift.evaluate(drift, row, test_type, params, FAIL_STATUS)

    if runnable or dupe_runnable or query_runnable:
        with connect() as conn:
            facts = read_table_facts(conn)
            for (schema, table), checks in sorted(runnable.items()):
                refused = refusal(tables.get((schema, table)) or {}, (facts or {}).get((schema, table)),
                                  facts is not None)
                if refused:
                    for check in checks:
                        check["row"].update(status=_REFUSED_STATUS[refused[0]], reason=refused[1])
                    continue
                _run_table(conn, schema, table, checks, bounds)
            for (schema, table), checks in sorted(query_runnable.items()):
                refused = refusal(tables.get((schema, table)) or {}, (facts or {}).get((schema, table)),
                                  facts is not None)
                for check in checks:
                    if refused:
                        check["row"].update(status=_REFUSED_STATUS[refused[0]], reason=refused[1])
                    else:
                        query_types.run_table(conn, schema, table, check, bounds, clock, run_date=run_date)
            for (schema, table), checks in sorted(dupe_runnable.items()):
                refused = refusal(tables.get((schema, table)) or {}, (facts or {}).get((schema, table)),
                                  facts is not None)
                for check in checks:
                    if refused:
                        check["row"].update(status=_REFUSED_STATUS[refused[0]], reason=refused[1])
                    else:
                        dupe_rows.run_table(conn, schema, table, check, bounds, clock)

    for row in results:
        if row["status"] in CONCLUDED:
            row["state"] = "measured"
    registry.write_detail_rows(RESULTS_TABLE, slug, surveyed_at, rows=results)
    counts: dict[str, int] = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"quality_checks": {
        "surveyed_at": surveyed_at,
        "state": "measured" if any(r["status"] in CONCLUDED for r in results) else "not_established",
        "check_count": len(results),
        "status_counts": counts,
        "ran_as": ran_as,
    }, "quality_annotations": build_quality_annotations(results, catalogue)}


#: A check's result status -> the state word its QualityAnnotation carries (design: passed, failed, not_established,
#: not_measured, not_permitted, error). A warning or a logged result is a check that did not hold, at its severity.
CHECK_STATE = {"pass": "passed", "warning": "failed", "fail": "failed", "logged": "failed",
               "not_established": "not_established", "not_measured": "not_measured",
               "not_permitted": "not_permitted", "error": "error"}


#: Measure units that count things in the table, so "N <uom> of <records>" reads true ("0 future dates of 66").
#: Every other unit (a difference measure, a percent of baseline, Recency's days before the test, Match_Row_Ct's row
#: count, which IS the record count) is a figure of its own: "<uom> <value>".
_MEASURE_IS_NOT_A_COUNT = ("measure", "percent", "days before", "row count", "minimum ", "maximum ", "sum",
                          "deviation", "length", "missing dates")


def outcome_words(row: dict, test_type) -> str:
    """What the check found, in the words of its test type's own unit: "0 future dates of 66" (a count over the
    records it read), "difference measure 4.5" (a figure with no record count in it). "" when the check reached no
    measure (the state word and the reason say why). Pure, for every test type."""
    value = row.get("measure")
    if value is None or test_type is None:
        return ""
    uom = (test_type.measure_uom or "measure").strip().lower()
    if row.get("measure_text"):
        return f"{uom} {row['measure_text']}"
    if getattr(test_type, "measure_is_text", False) and isinstance(value, (int, float)):
        return f"{uom} {text_measure_from_number(value)}"
    figure = _figure(value)
    records = row.get("record_ct")
    if isinstance(records, int) and not any(w in uom for w in _MEASURE_IS_NOT_A_COUNT):
        return f"{figure} {uom} of {records}"
    return f"{uom} {figure}"


def check_summary(row: dict, test_type, path: str, state: str, *, reason_limit: int = 120) -> str:
    """A check's summary names the check (its test type key), the target and the outcome:
    "Future_Date on lot_status.quar_start: passed (0 future dates of 66)". A check with no measure gives its
    reason instead, shortened: "Unique on sales.orders.id: error (permission denied ...)"."""
    found = outcome_words(row, test_type)
    if not found:
        reason = " ".join(str(row.get("reason") or "").split())
        found = reason if len(reason) <= reason_limit else reason[: reason_limit - 1].rstrip() + "…"
    key = row.get("test_type") or "Quality check"
    return f"{key} on {path}: {state}" + (f" ({found})" if found else "")


def build_quality_annotations(results: list[dict], catalogue: dict | None = None) -> list:
    """One QualityCheckAnnotation (Egeria `QualityAnnotation`) per EVALUATED check, whatever its outcome, in
    check-key order. Pure: no registry, no Egeria.

    The outcome is a state word (`CHECK_STATE`) in `additionalProperties.checkState`, with the reason sentence beside
    it. Only a check that reached a conclusion carries a `qualityScore`: the pass rate of one check in percent (100
    held, 0 did not), as Egeria's own producer scores it. A check that did not conclude has NO score, because 0
    would read as failed."""
    from resource_explorer.surveyors.survey_report import QualityCheckAnnotation

    catalogue = catalogue or load_catalogue()
    out: list = []
    for row in results:
        state = CHECK_STATE.get(row["status"], "error")
        concluded = row["status"] in CONCLUDED
        test_type = catalogue.get(row["test_type"]) if row["test_type"] else None
        path = ".".join(p for p in (row["schema_name"], row["table_name"], row["column_name"]) if p) or row["check_key"]
        limits = row.get("threshold_values_json") or [None]
        limit = f"{limits[0]} AND {limits[1]}" if test_type is not None and test_type.test_operator == "NOT BETWEEN" \
            and len(limits) == 2 else limits[0]
        reason = row["reason"] or "no reason recorded"
        if concluded and test_type is not None:
            description = (f"{state}: {reason}. Severity {row['severity']}; "
                           f"the check fails when {test_type.measure_uom or 'the measure'} "
                           f"{test_type.test_operator} {limit}.")
        else:
            description = f"{state}: {reason}"
        detail = {
            "checkKey": row["check_key"], "checkState": state, "checkStateReason": reason,
            "testType": row["test_type"], "schema": row["schema_name"],
            "table": row["table_name"], "column": row["column_name"], "status": row["status"],
            "severity": row["severity"], "measure": row["measure"], "recordCount": row["record_ct"],
            "comparisonOperator": row["comparison_operator"], "threshold": limit,
            "ruleSource": row["rule_source"], "ruleGuid": row["rule_guid"],
            "baselineProfileSurveyedAt": row["baseline_profile_surveyed_at"], "ranAs": row["ran_as"],
            "originalDimension": original_dimension(test_type.dimension) if test_type is not None else "",
            "ruleNote": row.get("rule_note") or "",
        }
        if row.get("measure_text"):
            detail["measureText"] = row["measure_text"]
        elif test_type is not None and test_type.measure_is_text and isinstance(row["measure"], (int, float)):
            detail["measureText"] = text_measure_from_number(row["measure"])
        out.append(QualityCheckAnnotation(
            summary=check_summary(row, test_type, path, state),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name=ANNOTATION_TYPE_NAME,
            check_name="quality_check",
            item_key=row["check_key"],
            label=state,
            confidence=100 if concluded else 0,
            explanation=(f"Run by Resource Explorer from the vendored TestGen test type {row['test_type']} "
                         f"({test_type.name_short}); the measure was read from the database, not estimated."
                         if concluded and test_type is not None else
                         f"Evaluated by Resource Explorer, and not concluded ({state}): {reason}"),
            quality_dimension=egeria_dimension(test_type.dimension) if test_type is not None else "",
            quality_description=description,
            quality_score=(100 if row["status"] == "pass" else 0) if concluded else None,
            sample_size=row["record_ct"] if isinstance(row["record_ct"], int) else None,
            schema_name=row["schema_name"], table_name=row["table_name"], column_name=row["column_name"],
            additional_properties={k: v for k, v in detail.items() if v not in (None, "")},
        ))
    return out


def _hash_columns(test_type, schema: str, table: str, column: str, params: dict, catalog_types: dict):
    """(the column, the key) as `HashColumn`s for a value-hash type (port step 8), else (None, None). The general type is
    derived from the stored catalog type exactly as the profile derived it, so the run hashes what the profile hashed."""
    if not any(p in test_type.measure for p in ("{MULTISET_HASH_TERM}", "{EPOCH_SUM_TERM}", "{KEYED_HASH_TERM}")):
        return None, None
    from resource_explorer.surveyors.database.quality import value_hashes
    from resource_explorer.surveyors.database.quality.general_type import general_type as derive_general_type

    def hash_column(name: str):
        declared = catalog_types.get((schema, table, name))
        return value_hashes.HashColumn(name, derive_general_type(declared, None), declared or "")

    key_name = params.get("key_column_name")
    if "{KEYED_HASH_TERM}" in test_type.measure and (not isinstance(key_name, str) or
                                                     (schema, table, key_name) not in catalog_types):
        raise ParameterError(f"the keyed hash needs the table's key column, got {key_name!r}")
    return hash_column(column), hash_column(key_name) if "{KEYED_HASH_TERM}" in test_type.measure else None


def _select(schema: str, table: str, checks: list[dict | str]) -> str:
    """One SELECT: the record count, each check's measure as q<i>, and a condition's extra aggregate as q<i>_x. A
    check may be given as its measure SQL alone."""
    items = ["COUNT(*) AS record_ct"]
    for i, check in enumerate(checks):
        check = {"sql": check} if isinstance(check, str) else check
        items.append(f"{check['sql']} AS q{i}")
        if check.get("extra_sql"):
            items.append(f"{check['extra_sql']} AS q{i}_x")
    return "SELECT " + ", ".join(items) + f" FROM {quote_identifier(schema)}.{quote_identifier(table)}"


def _figure(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _conclude(check: dict, measure, record_ct, extra=None) -> None:
    row = check["row"]
    row["record_ct"] = int(record_ct) if record_ct is not None else None
    test_type = check["type"]
    value = measure if test_type.measure_is_exact or test_type.measure_is_text else _number(measure)
    if value is None:
        if test_type.null_measure_fails:
            row.update(status=FAIL_STATUS[row["severity"]], reason=UNDEFINED_FAILS.format(test_type=test_type.test_type))
        else:
            row.update(status="not_established", reason=UNDEFINED)
        return
    limit = check["limit"]
    if test_type.measure_is_text:
        value = str(value)
        row["measure"], row["measure_text"] = text_measure_number(value), value
    elif test_type.measure_is_exact:
        value = value if isinstance(value, Decimal) else Decimal(str(value))
        row["measure"], row["measure_text"] = float(value), format(value, "f")
    else:
        row["measure"] = value
    if isinstance(limit, SumTolerance):
        if extra is None:
            row.update(status="error", reason="the count Match_Sum's tolerance reads was not returned")
            return
        limit = limit.between(int(extra))
        row["threshold_values_json"] = _limit_json(limit)
    uom = test_type.measure_uom or "measure"
    if fails(test_type, value, limit):
        row["status"] = FAIL_STATUS[row["severity"]]
        row["reason"] = f"{uom} {_figure(value)} {test_type.test_operator} {_figure(limit)}"
    else:
        row["status"] = "pass"
        row["reason"] = f"{uom} {_figure(value)} (fails when {test_type.test_operator} {_figure(limit)})"


def _failed(check: dict, exc: Exception) -> None:
    text = error_text(exc)
    if "division by zero" in text.lower():
        check["row"].update(status="not_established", reason=f"{UNDEFINED}: {text}")
    elif classify_error(exc) == "not_permitted":
        check["row"].update(status="not_permitted", reason=text)
    else:
        check["row"].update(status="error", reason=text)


def _query(conn, sql: str, bounds: ProfileBounds) -> list[dict]:
    """One read-only transaction for one statement, ended at once so no lock outlives it. Params None, so
    psycopg2 never reads a "%" in a name or a placeholder value as a parameter marker."""
    try:
        begin_read(conn, bounds)
        return conn.execute_query_isolated(sql, None)
    finally:
        conn.end_transaction()


def _no_row(check: dict) -> None:
    check["row"].update(status="error", reason="the aggregate query returned no row")


def _run_table(conn, schema: str, table: str, checks: list[dict], bounds: ProfileBounds) -> None:
    """One aggregate SELECT for the table's checks; on error, each check alone (TestGen's CAT fallback)."""
    try:
        result = _query(conn, _select(schema, table, checks), bounds)
    except Exception:
        result = None
    if result is not None:
        for i, check in enumerate(checks):
            if result:
                _conclude(check, result[0].get(f"q{i}"), result[0].get("record_ct"), result[0].get(f"q{i}_x"))
            else:
                _no_row(check)
        return
    for check in checks:
        try:
            result = _query(conn, _select(schema, table, [check]), bounds)
        except Exception as exc:
            _failed(check, exc)
            continue
        if result:
            _conclude(check, result[0].get("q0"), result[0].get("record_ct"), result[0].get("q0_x"))
        else:
            _no_row(check)
