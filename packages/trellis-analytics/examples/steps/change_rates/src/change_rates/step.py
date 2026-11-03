"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

Per table, from the latest two `database_table_activity` snapshots RE stored (`inputs.ods.table_activity_snapshots`):
the change in PostgreSQL's cumulative insert, update and delete counters between them, divided by the interval in
days. Each measured result names the interval and both as-ofs.

Never a silent zero:
- one snapshot (or none): one ``not_established`` result for the database, "a rate needs two";
- a possible counter reset between the snapshots (the reset time moved, or appeared where the older snapshot had none,
  or a counter went backwards: a reset, failover or restore): ``not_established`` for that table, "statistics were
  reset between snapshots", never the difference; a reset time read before and not after cannot rule one out either;
- a counter missing in either snapshot, a table new since the older one, or two snapshots with no interval between
  them: ``not_established``, naming which.
``nothing_found`` is a measured zero: no writes at all over the interval.
"""
from __future__ import annotations

from datetime import datetime

COUNTERS = (("rows_inserted", "Rows Inserted"), ("rows_updated", "Rows Updated"), ("rows_deleted", "Rows Deleted"))
CAVEAT = ("the difference of PostgreSQL's cumulative counters between two snapshots over their interval; a counter "
          "reset between them (reset, failover or restore) gives no rate, never a negative one")


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


def _key(t: dict) -> str:
    return f"{t['schema_name']}.{t['table_name']}"


def _absent(scope: str, reason: str, words: str) -> dict:
    return {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}


def run(inputs) -> list[dict]:
    snaps = inputs.ods.table_activity_snapshots(inputs.slug, limit=2)
    if len(snaps) < 2:
        if not snaps:
            return [_absent(inputs.slug, "no survey has stored table activity counters for this database yet",
                            "no activity snapshot yet; a rate needs two")]
        return [_absent(inputs.slug, f"only one survey has stored table activity counters "
                                     f"({snaps[0]['surveyed_at']}); a rate needs two",
                        "one activity snapshot only; a rate needs two")]
    new, old = snaps
    start, end = _parse(old["surveyed_at"]), _parse(new["surveyed_at"])
    days = (end - start).total_seconds() / 86400.0 if start and end else None
    if not days or days <= 0:
        why = (f"the two snapshots ({old['surveyed_at']}, {new['surveyed_at']}) have no interval between them"
               if days is not None else f"a snapshot's time could not be read ({old['surveyed_at']}, "
                                        f"{new['surveyed_at']})")
        return [_absent(inputs.slug, why, "no interval between the two activity snapshots")]
    before = {_key(t): t for t in old["tables"]}
    span = f"over {days:,.2f} days, {old['surveyed_at']} to {new['surveyed_at']}"
    out = []
    for t in new["tables"]:
        scope = _key(t)
        prev = before.get(scope)
        if prev is None:
            out.append(_absent(scope, f"not in the earlier activity snapshot ({old['surveyed_at']}); new since then, "
                                      "so there is nothing to difference", "table not in the earlier snapshot"))
            continue
        missing = [label for col, label in COUNTERS if t.get(col) is None or prev.get(col) is None]
        if missing:
            out.append(_absent(scope, f"{', '.join(missing)} not measured in "
                                      f"{'both snapshots' if len(missing) == len(COUNTERS) else 'one snapshot'} "
                                      f"({old['surveyed_at']}, {new['surveyed_at']})",
                               "a write counter is missing in a snapshot"))
            continue
        reset_old, reset_new = prev.get("stats_reset") or None, t.get("stats_reset") or None
        backwards = [label for col, label in COUNTERS if int(t[col]) < int(prev[col])]
        # Never a rate across a POSSIBLE reset: a reset time that moved, one that appears where the older snapshot had
        # none (NULL then a time: a reset the older snapshot could not show), or counters that went backwards.
        if reset_old and not reset_new:
            out.append(_absent(scope, f"the statistics reset time was read in the older snapshot ({reset_old}) and "
                                      f"not in the newer one ({new['surveyed_at']}), so a reset cannot be ruled out: "
                                      "no rate for this interval",
                               "statistics reset time not read in the newer snapshot"))
            continue
        moved = bool(reset_new) and reset_old != reset_new
        if moved or backwards:
            how = (f"the statistics reset time moved ({reset_old or 'not set'} to {reset_new})" if moved
                   else f"{', '.join(backwards)} went backwards, so the counters were reset")
            out.append(_absent(scope, f"{how} between {old['surveyed_at']} and {new['surveyed_at']}: no rate for this "
                                      "interval", "statistics were reset between snapshots"))
            continue
        deltas = {label: int(t[col]) - int(prev[col]) for col, label in COUNTERS}
        total = sum(deltas.values())
        value = {**{f"{label} In Interval": n for label, n in deltas.items()},
                 **{f"{label} Per Day": round(n / days, 3) for label, n in deltas.items()},
                 "Writes In Interval": total, "Writes Per Day": round(total / days, 3),
                 "Interval Days": round(days, 4), "fromAsOf": old["surveyed_at"], "toAsOf": new["surveyed_at"],
                 "basis": CAVEAT}
        if total == 0:
            out.append({"scope": scope, "state": "nothing_found", "value": value,
                        "summary": f"no inserts, updates or deletes {span}"})
        else:
            out.append({"scope": scope, "state": "measured", "value": value,
                        "summary": (f"{total / days:,.1f} writes per day {span} ("
                                    + ", ".join(f"{n:,} {label.split()[-1].lower()}" for label, n in deltas.items())
                                    + ")")})
    if not out:
        return [_absent(inputs.slug, f"the newer activity snapshot ({new['surveyed_at']}) names no tables",
                        "no tables in the activity snapshot")]
    return out
