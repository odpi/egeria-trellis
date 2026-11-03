"""Statistics currency for a database, derived from stored rows only (S4b).

ONE pure function set; the route (`db_chart_data.statistics_currency`) and the
ranked-tables read both call it, and the UI only renders what it returns.
No query, no clock: ages are measured at `as_of`, the survey the activity rows
came from (the same convention as `db_statistics_freshness`), so a past survey
derives to the same answer today as it did then.

Levels
  table     last analysed time, or the reason there is none
  database  when the counters were last reset (what tells "never analysed"
            from "counters reset since")
  schema    nothing native: a rollup over its tables

Table states
  current          analysed within `stale_days` of `as_of`
  stale            analysed, but longer ago than `stale_days`
  never            no analyse time, and the counters were never reset
  reset            no analyse time (or one older than the reset), and the
                   counters were reset: "never" is unknowable, and not claimed
  never_reset_unknown  no analyse time and the survey did not read the reset
                   time: could be never, could be a reset
  partitioned      a partitioned parent: autovacuum never analyses it (its
                   partitions carry the statistics), so it reads neither
                   "never" nor a count. Needs `relkind`/`table_type` to say so
  not_collected    no activity row for the table in that survey: says nothing
"""
from __future__ import annotations

from datetime import datetime

from resource_explorer.registry import STATS_NEVER_RESET

#: There is no existing per-table "refresh rule" constant to reuse (the
#: `db_statistics_freshness` analysis judges by change counts against
#: autovacuum's trigger, not by age), so the age threshold is stated here.
STALE_DAYS = 30

CURRENT = "current"
STALE = "stale"
NEVER = "never"
RESET = "reset"
NEVER_RESET_UNKNOWN = "never_reset_unknown"
NOT_COLLECTED = "not_collected"
PARTITIONED = "partitioned"
TABLE_STATES = (CURRENT, STALE, NEVER, RESET, NEVER_RESET_UNKNOWN, NOT_COLLECTED)

#: Reset words for the database level.
RESET_AT = "reset_at"
RESET_NEVER = "never_reset"
RESET_UNKNOWN = "unknown"

#: What autovacuum analyses, which is NOT `db_derived._is_base_table`'s question
#: (that allowlist is "has a grain / a key to check": it drops materialised views
#: and keeps foreign tables). Here: plain and temporary tables and materialised
#: views are analysed; views and foreign tables never are. An unrecorded type is
#: kept, as there, because it is not evidence of a view.
_ANALYSED_TABLE_TYPES = frozenset({"BASE_TABLE", "TABLE", "LOCAL_TEMPORARY", "MATERIALIZED_VIEW"})
_PARTITIONED_TYPES = frozenset({"PARTITIONED_TABLE", "PARTITIONED"})


def _ttype(row: dict) -> str:
    if (row.get("relkind") or "") == "p":
        return "PARTITIONED_TABLE"
    return (row.get("table_type") or "").upper().replace(" ", "_")


def _parse(value) -> datetime | None:
    from resource_explorer.surveyors.database.db_derived import _parse_stat_time
    return _parse_stat_time(value)


def is_analysed_kind(row: dict) -> bool:
    """THE one definition of "autovacuum analyses this relation" (see above)."""
    t = _ttype(row)
    return not t or t in _ANALYSED_TABLE_TYPES or t in _PARTITIONED_TYPES


def is_partitioned_parent(row: dict) -> bool:
    return _ttype(row) in _PARTITIONED_TYPES


def _latest(*values) -> tuple[str | None, datetime | None]:
    best, best_dt = None, None
    for v in values:
        dt = _parse(v)
        if dt is not None and (best_dt is None or dt > best_dt):
            best, best_dt = v, dt
    return best, best_dt


def table_currency(activity, as_of, stale_days=STALE_DAYS, table=None):
    """See `_table_currency`; every result also names the survey it is measured at."""
    out = _table_currency(activity, as_of, stale_days, table)
    out["as_of"] = as_of
    return out


def _table_currency(activity: dict | None, as_of: str, stale_days: int = STALE_DAYS,
                    table: dict | None = None) -> dict:
    """One table's currency from its `database_table_activity` row (or None).
    `table` (a `database_tables` row) only matters for a partitioned parent."""
    if table is not None and is_partitioned_parent(table):
        return {"state": PARTITIONED, "last_analyzed": None, "age_days": None, "stats_reset": None}
    now = _parse(as_of)
    if not activity or now is None:
        return {"state": NOT_COLLECTED, "last_analyzed": None, "age_days": None,
                "stats_reset": None}
    raw_reset = activity.get("stats_reset")
    reset_dt = _parse(raw_reset)
    last, last_dt = _latest(activity.get("last_analyze"), activity.get("last_autoanalyze"))
    out = {"last_analyzed": last, "age_days": None,
           "stats_reset": raw_reset if raw_reset else None}
    if last_dt is not None and (reset_dt is None or last_dt >= reset_dt):
        age = max(0, (now - last_dt).days)
        out["age_days"] = age
        out["state"] = STALE if age > stale_days else CURRENT
        return out
    if last_dt is not None:             # analysed, but before the counters were reset
        out["age_days"] = max(0, (now - last_dt).days)
        out["state"] = RESET
        return out
    if reset_dt is not None:
        out["state"] = RESET
    elif raw_reset == STATS_NEVER_RESET:
        out["state"] = NEVER
    else:
        out["state"] = NEVER_RESET_UNKNOWN
    return out


def _database_reset(activity_rows: list[dict]) -> dict:
    stamps = [(_parse(a.get("stats_reset")), a.get("stats_reset"))
              for a in activity_rows if a.get("stats_reset")]
    dated = [(dt, raw) for dt, raw in stamps if dt is not None]
    if dated:
        return {"state": RESET_AT, "at": max(dated)[1]}
    if any(raw == STATS_NEVER_RESET for _, raw in stamps):
        return {"state": RESET_NEVER, "at": None}
    return {"state": RESET_UNKNOWN, "at": None}


def _rollup(rows: list[dict]) -> dict:
    counts = {s: 0 for s in TABLE_STATES}
    ages = []
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        if r["state"] in (CURRENT, STALE) and r["age_days"] is not None:
            ages.append(r["age_days"])
    return {
        "total": len(rows), "counts": counts,
        "never": counts[NEVER] + counts[NEVER_RESET_UNKNOWN],
        "stale": counts[STALE], "reset_tables": counts[RESET],
        "not_collected": counts[NOT_COLLECTED],
        # "behind" is what the line says: stale or never analysed. Tables whose
        # counters were reset are NOT in it (unknowable), and are counted apart.
        "behind": counts[STALE] + counts[NEVER] + counts[NEVER_RESET_UNKNOWN],
        "oldest_days": max(ages) if ages else None,
    }


def derive_statistics_currency(
    tables: list[dict], activity_rows: list[dict], as_of: str,
    schemas: list[str] | None = None, stale_days: int = STALE_DAYS,
) -> dict:
    """The whole answer for one survey: database line, schema rollups, per table.

    `tables`: `database_tables` rows (views are left out: nothing to analyse).
    `activity_rows`: `database_table_activity` rows of the same survey.
    `schemas`: schema names, so a schema with no tables is still a row (empty).
    """
    act = {(a.get("schema_name") or "", a.get("table_name") or ""): a for a in activity_rows}
    per_table: dict[str, dict] = {}
    partitioned: list[dict] = []
    by_schema: dict[str, list[dict]] = {s: [] for s in (schemas or [])}
    for t in tables:
        if not is_analysed_kind(t):
            continue
        s, n = t.get("schema_name") or "", t.get("table_name") or ""
        cur = table_currency(act.get((s, n)), as_of, stale_days, table=t)
        cur["schema"], cur["table"] = s, n
        per_table[f"{s}.{n}"] = cur
        if cur["state"] == PARTITIONED:        # shown, but in no count
            partitioned.append(cur)
        else:
            by_schema.setdefault(s, []).append(cur)
    schema_rows = []
    for name in sorted(by_schema):
        r = _rollup(by_schema[name])
        r["schema"] = name
        r["empty"] = r["total"] == 0
        r["partitioned"] = sum(1 for p in partitioned if p["schema"] == name)
        schema_rows.append(r)
    db = _rollup([c for c in per_table.values() if c["state"] != PARTITIONED])
    db["counters_reset"] = _database_reset(list(act.values()) if per_table else [])
    db["partitioned"] = len(partitioned)
    return {"as_of": as_of, "stale_days": stale_days,
            "database": db, "schemas": schema_rows, "tables": per_table}
