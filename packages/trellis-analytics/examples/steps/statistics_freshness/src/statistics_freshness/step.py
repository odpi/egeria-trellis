"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

Per table, from the newest `database_table_activity` snapshot RE stored (`inputs.ods.table_activity_snapshots`):

- the last ANALYZE and VACUUM (manual and automatic), stated as Postgres gave them; Postgres's NULL is the finding
  "never", never an empty value;
- days since each, relative to the snapshot's as-of, never to the moment this runs;
- a status: ``never analyzed`` (both analyse times NULL), ``stale`` (changes since the last analyse past autovacuum's
  own trigger, ``50 + 0.1 * live rows``, PostgreSQL's defaults, since no survey stores this database's settings),
  ``current``, or ``analysed; changes since not read``.

A table whose statistics were not read (no live-row count; a native survey's row) is ``not_established``, never
"never analysed". One summary result for the database counts the statuses. Every result carries its as-of and the
caveat; every state carries a sentence.
"""
from __future__ import annotations

from datetime import datetime

#: The database summary, the second declared measure.
SUMMARY = "statistics_freshness_summary"

#: PostgreSQL's defaults for autovacuum's analyse trigger (no survey stores this database's own settings).
ANALYZE_THRESHOLD = 50
ANALYZE_SCALE_FACTOR = 0.1
CAVEAT = ("ages are relative to the snapshot's as-of, not to now; 'stale' uses PostgreSQL's default autovacuum "
          "settings (threshold 50, scale factor 0.1), so a server or per-table override is not reflected")

NEVER = "never"
NEVER_ANALYZED, STALE, CURRENT, CHANGES_NOT_READ = ("never analyzed", "stale", "current",
                                                     "analysed; changes since not read")
STATUSES = (NEVER_ANALYZED, STALE, CURRENT, CHANGES_NOT_READ)

#: Published keys: Egeria's resource-measure convention is the metric's display name (RE's table_statistics_metrics).
LAST_ANALYZE, LAST_AUTO_ANALYZE = "Last Analyze", "Last Auto Analyze"
LAST_VACUUM, LAST_AUTO_VACUUM = "Last Vacuum", "Last Auto Vacuum"
LIVE_ROWS, CHANGED = "Number Of Live Rows", "Number Of Rows Changed Since Analyze"
RESET = "Last statistics reset"


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        from datetime import timezone
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _latest(*values) -> str:
    stated = [v for v in values if v]
    return max(stated, key=lambda v: _parse(v) or datetime.min) if stated else ""


def _days(as_of: datetime | None, when: str) -> float | None:
    then = _parse(when)
    return round((as_of - then).total_seconds() / 86400.0, 1) if as_of and then else None


def _ago(days: float | None, when: str) -> str:
    return f"{days:,.1f} days ago" if days is not None else f"at {when}"


def _table(t: dict, source: str, as_of: str) -> dict:
    scope = f"{t['schema_name']}.{t['table_name']}"
    live = t.get("live_tuples")
    if source == "egeria" or live is None:
        return {"scope": scope, "state": "not_established",
                "reason": f"no table statistics were read for this table in the snapshot of {as_of}",
                "summary": "not measured · statistics not read for this table"}
    at = _parse(as_of)
    analysed = _latest(t.get("last_analyze"), t.get("last_autoanalyze"))
    vacuumed = _latest(t.get("last_vacuum"), t.get("last_autovacuum"))
    changes = t.get("pending_changes")
    threshold = ANALYZE_THRESHOLD + ANALYZE_SCALE_FACTOR * (int(live) if analysed else 0)
    days_analyse, days_vacuum = _days(at, analysed), _days(at, vacuumed)
    if not analysed:
        status = NEVER_ANALYZED
    elif changes is None:
        status = CHANGES_NOT_READ
    else:
        status = STALE if int(changes) > threshold else CURRENT
    value = {LAST_ANALYZE: t.get("last_analyze") or NEVER, LAST_AUTO_ANALYZE: t.get("last_autoanalyze") or NEVER,
             LAST_VACUUM: t.get("last_vacuum") or NEVER, LAST_AUTO_VACUUM: t.get("last_autovacuum") or NEVER,
             LIVE_ROWS: int(live), CHANGED: None if changes is None else int(changes),
             "Days Since Analyze": days_analyse, "Days Since Vacuum": days_vacuum,
             "Analyze Threshold": round(threshold, 1), "Statistics Status": status,
             RESET: t.get("stats_reset") or None, "asOf": as_of, "basis": CAVEAT}
    vac = "never vacuumed" if not vacuumed else f"vacuumed {_ago(days_vacuum, vacuumed)}"
    if status == NEVER_ANALYZED:
        words = f"never analysed; {vac}"
    elif status == STALE:
        words = (f"stale · {int(changes):,} rows changed since the last analyse {_ago(days_analyse, analysed)}, "
                 f"past autovacuum's {threshold:,.0f}; {vac}")
    elif status == CURRENT:
        words = f"current · analysed {_ago(days_analyse, analysed)}, {int(changes):,} rows changed since; {vac}"
    else:
        words = f"analysed {_ago(days_analyse, analysed)}; rows changed since not read; {vac}"
    return {"scope": scope, "state": "measured", "value": value, "summary": f"{words} (as of {as_of})"}


def _summary(slug: str, results: list[dict], as_of: str) -> dict:
    read = [r for r in results if r["state"] == "measured"]
    if not read:
        return {"scope": slug, "measure": SUMMARY, "state": "not_established",
                "reason": f"no table statistics were read for any table in the snapshot of {as_of}",
                "summary": "not measured · no table statistics read"}
    counts = {s: sum(1 for r in read if r["value"]["Statistics Status"] == s) for s in STATUSES}
    unread = len(results) - len(read)
    parts = [f"{counts[NEVER_ANALYZED]} never analysed", f"{counts[STALE]} stale", f"{counts[CURRENT]} current"]
    if counts[CHANGES_NOT_READ]:
        parts.append(f"{counts[CHANGES_NOT_READ]} with changes since not read")
    if unread:
        parts.append(f"{unread} not read")
    ages = [r["value"]["Days Since Analyze"] for r in read if r["value"]["Days Since Analyze"] is not None]
    value = {"Tables": len(results), "Never Analyzed": counts[NEVER_ANALYZED], "Stale": counts[STALE],
             "Current": counts[CURRENT], "Changes Since Analyze Not Read": counts[CHANGES_NOT_READ],
             "Not Read": unread, "Oldest Analyze Days": max(ages) if ages else None, "asOf": as_of, "basis": CAVEAT}
    return {"scope": slug, "measure": SUMMARY, "state": "measured", "value": value,
            "summary": f"of {len(results)} tables: {', '.join(parts)} (as of {as_of})"}


def run(inputs) -> list[dict]:
    snaps = inputs.ods.table_activity_snapshots(inputs.slug, limit=1)
    if not snaps:
        why = "no survey has stored table statistics (pg_stat_user_tables) for this database yet"
        return [{"scope": inputs.slug, "state": "not_established", "reason": why,
                 "summary": "not measured · no table statistics stored"},
                {"scope": inputs.slug, "measure": SUMMARY, "state": "not_established", "reason": why,
                 "summary": "not measured · no table statistics stored"}]
    snap = snaps[0]
    results = [_table(t, snap["source"], snap["surveyed_at"]) for t in snap["tables"]]
    return results + [_summary(inputs.slug, results, snap["surveyed_at"])]
