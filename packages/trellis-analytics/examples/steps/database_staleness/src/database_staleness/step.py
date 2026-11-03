"""Level 1: the code. A plain callable, `run(inputs) -> results`.

It owns the measurements and their reasons, and nothing else. Two measures, kept distinct (design ruling, engine host
step 4: never one function with two meanings):

- **database staleness** (the step's own measure): the change in each table's row count between the latest two
  row-count snapshots RE stored, over their interval. Only snapshots feed it. A survey report it was handed is
  provenance only (the host records it as `derived_from_report`), never a substitute figure.
- **writes since statistics reset** (`WRITES`), only when the host hands a survey report (`source_report`, in an
  Egeria process, the native PostgreSQL survey that just ran): per table, the cumulative rows inserted, updated and
  deleted PostgreSQL counts since its statistics were last reset, as of the report. Totals, not a rate and not a
  change since the last survey.

`inputs` is what the wrapper hands it (the ODS reader and the source report, as the manifest declares); each result
names its scope, its measure when it is not staleness, its state and, when the state is not a measurement, why.
"""
from __future__ import annotations


def _key(t: dict) -> str:
    return f"{t['schema_name']}.{t['table_name']}"


def _is_view(t: dict) -> bool:
    return str(t.get("table_type") or "").upper().replace("_", " ") == "VIEW"


#: The second measure, as the manifest declares it.
WRITES = "writes_since_statistics_reset"

#: The native PostgreSQL survey's annotations (Egeria's names, live 2026-10-15). A table measurement carries
#: PostgreSQL's cumulative pg_stat counters and no row count; the database measurement may carry the reset time.
TABLE_MEASUREMENTS = "Capture Database Table Measurements"
DATABASE_MEASUREMENTS = "Capture Database Measurements"
_COUNTERS = (("rowsInserted", "Number Of Rows Inserted"), ("rowsUpdated", "Number Of Rows Updated"),
             ("rowsDeleted", "Number Of Rows Deleted"))
CAVEAT = ("cumulative since PostgreSQL's statistics were last reset: a total, not a rate and not a change since the "
          "last survey")


def _report_scope(props: dict) -> str:
    parts = str(props.get("Table Qualified Name") or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else str(props.get("Table Name") or "")


def writes_since_reset(slug: str, report: dict) -> list[dict]:
    """The second measure, from the survey report alone: one result per table, each naming the report, its as-of
    time and the reset caveat."""
    guid, as_of = str(report.get("guid") or ""), str(report.get("at") or "")
    anns = report.get("annotations") or []
    reset = next((str((a.get("resource_properties") or {}).get("Last statistics reset") or "")
                  for a in anns if a.get("annotation_type") == DATABASE_MEASUREMENTS), "")
    tables = [a for a in anns if a.get("annotation_type") == TABLE_MEASUREMENTS]
    if not tables:
        return [{"scope": slug, "measure": WRITES, "state": "not_established",
                 "reason": f"survey report {guid} carries no table measurements",
                 "summary": "not measured · the survey report has no table measurements"}]
    out = []
    for a in tables:
        props = a.get("resource_properties") or {}
        scope = _report_scope(props)
        if str(props.get("Table Type") or "").upper().replace("_", " ") == "VIEW":
            out.append({"scope": scope, "measure": WRITES, "state": "not_applicable",
                        "reason": "a view holds no rows of its own",
                        "summary": "not applicable · a view holds no rows"})
            continue
        counts = {}
        for key, name in _COUNTERS:
            try:
                counts[key] = int(str(props.get(name)).strip())
            except (TypeError, ValueError):
                counts[key] = None
        missing = [name for key, name in _COUNTERS if counts[key] is None]
        if missing:
            out.append({"scope": scope, "measure": WRITES, "state": "not_established",
                        "reason": f"{', '.join(missing)} not measured in survey report {guid}",
                        "summary": "not measured · a write counter is missing in the survey report"})
            continue
        total = sum(counts.values())
        value = {**counts, "writesSinceStatisticsReset": total, "asOf": as_of, "statisticsReset": reset or None,
                 "sourceReport": guid, "basis": CAVEAT}
        when = f"as of {as_of}" if as_of else f"in survey report {guid[:8]}"
        since = f"since statistics reset {reset}" if reset else "since PostgreSQL's statistics were last reset"
        out.append({"scope": scope, "measure": WRITES, "state": "measured" if total else "nothing_found",
                    "value": value, "summary": f"{total:,} rows inserted, updated or deleted {since} ({when}); "
                                               "a total, not a rate"})
    return out


def run(inputs) -> list[dict]:
    out = staleness(inputs)
    report = getattr(inputs, "source_report", None)
    if report:
        out += writes_since_reset(inputs.slug, report)
    else:
        # Standalone (design ruling, 2026-10-15): staleness runs from the snapshots alone; the second measure is
        # absent, and the absence is recorded, never left silent.
        out.append({"scope": inputs.slug, "measure": WRITES, "state": "not_established",
                    "reason": "no survey report in this run",
                    "summary": "writes since statistics reset: not measured · no survey report in this run"})
    return out


def staleness(inputs) -> list[dict]:
    """The step's own measure, from the stored row-count snapshots only."""
    snaps = inputs.ods.row_count_snapshots(inputs.slug, limit=2)
    if not snaps:
        return [{"scope": inputs.slug, "state": "not_established",
                 "reason": "no survey has measured row counts for this database yet",
                 "summary": "not measured · no survey has counted rows yet"}]
    latest = snaps[0]
    previous = {_key(t): t for t in snaps[1]["tables"]} if len(snaps) > 1 else None
    out = []
    for t in latest["tables"]:
        scope = _key(t)
        if _is_view(t):
            out.append({"scope": scope, "state": "not_applicable", "reason": "a view holds no rows of its own",
                        "summary": "not applicable · a view holds no rows"})
            continue
        if previous is None:
            out.append({"scope": scope, "state": "not_established",
                        "reason": f"not measured · one snapshot only: only one survey has measured row counts "
                                  f"({latest['surveyed_at']}); a change needs two",
                        "summary": "not measured · one snapshot only"})
            continue
        before = previous.get(scope)
        if before is None:
            out.append({"scope": scope, "state": "not_established",
                        "reason": f"not in the previous survey ({snaps[1]['surveyed_at']})",
                        "summary": "not measured · table not in the previous survey"})
            continue
        missing = [at for at, row in ((snaps[1]["surveyed_at"], before), (latest["surveyed_at"], t))
                   if row.get("row_count") is None]
        if missing:
            out.append({"scope": scope, "state": "not_established",
                        "reason": f"row count not measured in the survey of {' and '.join(missing)}",
                        "summary": "not measured · row count missing in a survey"})
            continue
        b, a = int(before["row_count"]), int(t["row_count"])
        value = {"rowsBefore": b, "rowsAfter": a, "rowsChanged": a - b,
                 "previousSurvey": snaps[1]["surveyed_at"], "latestSurvey": latest["surveyed_at"]}
        if a == b:
            out.append({"scope": scope, "state": "nothing_found", "value": value,
                        "summary": f"no rows changed since {snaps[1]['surveyed_at']} ({a:,} rows)"})
        else:
            out.append({"scope": scope, "state": "measured", "value": value,
                        "summary": f"{a - b:+,} rows since {snaps[1]['surveyed_at']} ({b:,} → {a:,})"})
    return out
