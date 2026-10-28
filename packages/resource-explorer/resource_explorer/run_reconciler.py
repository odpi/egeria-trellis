"""Resolve activity rows left claiming "running" by a process that is gone.

A Survey Definition run happens in a `daemon=True` thread inside the web
process. Every way that thread can END writes a terminal status — executor
errors and unexpected crashes are both caught in
`_run_survey_definition_background`. Exactly one way it can STOP does not: the
process itself dying. A restart, a Ctrl-C, a kill, and the thread vanishes
mid-run with no chance to write anything.

The row then says "running" forever. Measured 2026-08-26: six such rows, the
oldest three days old, while the user could not tell whether a survey they had
just started was working. "Started, outcome unknown" rendered as "in progress"
is the same answer-shaped-non-answer this codebase keeps removing, sitting in
the one field someone checks when they are already unsure.

**Reconciled by ownership, not by age.** A running row records the pid and
process start time of whoever owns it, and is resolved only when that exact
process is provably gone. Age alone would be wrong here: a real Full Survey on
a large repo takes sixteen minutes, and two Resource Explorer processes can
share one database (a second one started on another port is routine during
development) — so a timeout generous enough to be safe would be too slow to be
useful, and a short one would kill live runs belonging to a peer process.

**`interrupted`, never `error`.** We know the run stopped; we do not know it
failed. Some of these had already written most of their findings. Calling that
an error would be a different false claim in place of the first one.

Fails safe throughout: a row whose owner cannot be determined is LEFT ALONE,
because "I cannot tell whether this is alive" must never resolve to "it is
dead".
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: Terminal status for a run whose process died. Deliberately not "error": the
#: run may have completed most of its work, and we know only that nobody is
#: driving it any more.
INTERRUPTED = "interrupted"

#: Only used for rows with NO recorded owner — those predate ownership
#: recording and can be judged no other way. Generous on purpose: the longest
#: real survey measured here is a 16-minute RepoFullSurvey against the Egeria
#: repo, so this is roughly an order of magnitude clear of it.
_ORPHAN_AGE = timedelta(hours=6)


def process_identity() -> dict:
    """Who is running right now: pid plus a start time to survive pid reuse.

    The start time matters. Pids are recycled, and a fresh process inheriting
    a dead one's pid would make an orphaned row look alive forever.
    """
    return {"pid": os.getpid(), "started_at": _process_start_time(os.getpid())}


def _process_start_time(pid: int) -> str:
    """Process start time as a string, or "" when it cannot be read.

    Returning "" is meaningful: it means we cannot identify this process
    precisely, and callers then decline to reconcile rather than guess.
    """
    try:
        import subprocess

        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover - platform/permission dependent
        return ""


def _is_alive(owner: dict) -> bool | None:
    """True / False / None for "cannot tell".

    None is returned whenever identity is incomplete or unverifiable, and it
    never resolves to False anywhere in this module.
    """
    pid = owner.get("pid")
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by someone else. Not ours to reconcile.
        return True
    except Exception:  # pragma: no cover
        return None

    recorded = owner.get("started_at") or ""
    if not recorded:
        # Pid exists but we cannot confirm it is the SAME process. Pid reuse
        # makes "exists" too weak a claim to act on either way.
        return None
    current = _process_start_time(pid)
    if not current:
        return None
    return current == recorded


def owner_of(detail) -> dict:
    if isinstance(detail, str):
        try:
            detail = json.loads(detail or "{}")
        except (TypeError, ValueError):
            return {}
    return (detail or {}).get("_runner") or {} if isinstance(detail, dict) else {}


def reconcile(registry: "ProjectRegistry | None" = None, *, now=None) -> dict:
    """Resolve running rows whose owner is provably gone.

    Returns counts, including `left_alone` — a row we could not judge is
    reported rather than silently skipped, since a growing number there means
    this is quietly doing nothing.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    with registry._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ts, entity_slug, summary, detail FROM activity_log "
            "WHERE status = 'running' ORDER BY ts"
        ).fetchall()]

    resolved, left_alone, still_running = [], [], []
    for row in rows:
        owner = owner_of(row.get("detail"))
        alive = _is_alive(owner) if owner else None

        if alive is True:
            still_running.append(row["id"])
            continue
        if alive is False:
            resolved.append((row, "its process is no longer running"))
            continue

        # No owner recorded, or identity unverifiable. Age is the only signal
        # left, and it is used ONLY here.
        if not owner:
            age = _age_of(row.get("ts"), now)
            if age is None:
                # An unreadable timestamp is not an old one. Left alone.
                left_alone.append(row["id"])
                continue
            if age > _ORPHAN_AGE:
                resolved.append((
                    row,
                    f"no owner was recorded and it has been {int(age.total_seconds() // 3600)}h",
                ))
                continue
        left_alone.append(row["id"])

    for row, reason in resolved:
        _resolve(registry, row, reason, now)

    return {
        "resolved": len(resolved),
        "still_running": len(still_running),
        "left_alone": len(left_alone),
        "resolved_ids": [r["id"] for r, _ in resolved],
    }


def _resolve(registry, row: dict, reason: str, now: datetime) -> None:
    registry.update_activity_status(
        row["id"], INTERRUPTED,
        summary=(f"Interrupted — {reason}. Findings written before it stopped "
                 "are kept; whether it completed is not known."),
        detail=json.dumps({
            **(_as_dict(row.get("detail"))),
            "reconciled_at": now.isoformat(),
            "reconcile_reason": reason,
        }),
    )
    log.info("reconciled orphaned run %s (%s): %s",
             row["id"], row.get("entity_slug"), reason)


def reconcile_one(registry: "ProjectRegistry", row: dict, *, now=None) -> bool | None:
    """Judge ONE running activity row the way `reconcile` does, by its recorded owner only.

    True: its owner is provably gone and the row is now `interrupted`. False: its owner is alive (left running).
    None: no owner recorded or not verifiable (left running; never treated as dead). The age fallback for rows
    with no owner is NOT applied here: a caller deciding whether to proceed must not guess."""
    if row.get("status") != "running":
        return None
    owner = owner_of(row.get("detail"))
    alive = _is_alive(owner) if owner else None
    if alive is False:
        _resolve(registry, row, "its process is no longer running",
                 now or datetime.now(timezone.utc).replace(tzinfo=None))
        return True
    return False if alive else None


def reconcile_runs(
    registry: "ProjectRegistry | None" = None,
    *,
    now=None,
    ignore_heartbeat: bool = False,
) -> dict:
    """Fail `runs` rows whose claiming process is provably gone.

    The run-queue counterpart of `reconcile()` above, and deliberately the same
    judgement rather than a second one: a missed heartbeat makes a row a
    *candidate*, and only `_is_alive()` returning a definite False makes it a
    failure. A worker parked in a slow Egeria call is late, not dead, and the
    plan's own rule is that the pid-liveness check stays the source of truth
    for "dead" — the heartbeat is a cheap filter in front of it, not a verdict.

    `ignore_heartbeat=True` skips that filter and tests every claimed/running
    row. Used at worker startup: the filter exists to keep the periodic sweep
    cheap, and at startup the previous process's abandoned claims may well have
    heartbeated seconds before they died.

    Fails safe the same way: a row whose owner cannot be determined is left
    alone and counted, because "I cannot tell" must never resolve to "it is
    dead".
    """
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if ignore_heartbeat:
        rows = registry.list_runs(state="claimed", limit=1000)
        rows += registry.list_runs(state="running", limit=1000)
    else:
        from resource_explorer.run_queue import (
            HEARTBEAT_INTERVAL_SECONDS,
            STALE_AFTER_INTERVALS,
        )

        cutoff = now - timedelta(
            seconds=HEARTBEAT_INTERVAL_SECONDS * STALE_AFTER_INTERVALS
        )
        rows = registry.stale_active_runs(cutoff.isoformat())

    resolved, left_alone, still_running = [], [], []
    for row in rows:
        owner = _as_dict(row.get("runner"))
        alive = _is_alive(owner) if owner else None
        if alive is True:
            still_running.append(row["id"])
            continue
        if alive is False:
            resolved.append(row)
            continue
        # No owner recorded, or identity unverifiable. Unlike the activity-log
        # reconciler there is no age fallback here: every row in this table was
        # claimed by a process that recorded its identity at claim time, so a
        # missing owner means something wrote the row by hand, and guessing
        # about it is exactly what that reconciler's `_ORPHAN_AGE` exists to
        # avoid doing lightly.
        left_alone.append(row["id"])

    for row in resolved:
        last_beat = row.get("heartbeat_at") or row.get("claimed_at") or "unknown"
        error = (
            f"claimed by {row.get('claimed_by') or 'an unidentified worker'}, "
            f"whose process is no longer running (last heartbeat {last_beat}). "
            "Whatever it wrote before it stopped is kept; whether it completed "
            "is not known."
        )
        registry.finish_run(row["id"], "failed", error=error)
        log.info("reconciled stale run %s (%s): %s", row["id"], row.get("kind"), error)

    return {
        "resolved": len(resolved),
        "still_running": len(still_running),
        "left_alone": len(left_alone),
        "resolved_ids": [r["id"] for r in resolved],
    }


def _age_of(ts, now: datetime):
    """How long ago `ts` was, or None when it cannot be read.

    Activity timestamps are stored timezone-AWARE
    ("2026-08-24T13:55:13.489501+00:00"). Subtracting a naive `now` from one
    raises TypeError, which an earlier version of this caught as "unreadable"
    and sent every row to left_alone — the reconciler then ran cleanly and did
    nothing at all, on rows three days stale. Failing safe is right; failing
    safe silently on EVERY row is just a no-op wearing a safety belt.
    """
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    reference = now.replace(tzinfo=None) if now.tzinfo is not None else now
    return reference - parsed


def _as_dict(detail) -> dict:
    if isinstance(detail, str):
        try:
            return json.loads(detail or "{}") or {}
        except (TypeError, ValueError):
            return {}
    return detail if isinstance(detail, dict) else {}
