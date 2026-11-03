"""Which tables a person opted into Table Comparison: the baseline-match checks (B1a, `Match_*`).

DESIGN-QUALITY-B1A-BASELINE-MATCH.md §3, Q4 §7 question 2 (accepted 2026-10-26): TestGen generates its Table
Comparison set only for a table group a person opted in, and RE proposes it only for a table a person opted in
("watch this table for drift"), so six more rows per column never flood Curate unasked.

Stored with no new table: each opt-in or opt-out is its own `app_settings` entry under
`quality_table_comparison::<slug>::`, written once and never updated (`add_setting_once`), the append-only trail
`dependency_table.py` keeps for confirmations. The latest entry per table wins, and the trail keeps who and when.
An opt-out stops new proposals; it does not touch a verdict: an accepted check keeps running until it is retired.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime

log = logging.getLogger(__name__)

WATCH, UNWATCH = "watch", "unwatch"
MAX_NOTE = 500


def _prefix(slug: str) -> str:
    return f"quality_table_comparison::{slug}::"


def history(registry, slug: str) -> dict[tuple[str, str], list[dict]]:
    """{(schema, table): [entry, ...]} oldest first, each {action, by, at, note}. An entry that will not read is
    skipped and counted in the log, never taken as an opt-in."""
    out: dict[tuple[str, str], list[dict]] = {}
    bad = 0
    for _key, value in registry.list_settings_with_prefix(_prefix(registry._normalize_slug(slug))):
        try:
            e = json.loads(value)
        except (TypeError, ValueError):
            e = None
        if not (isinstance(e, dict) and e.get("action") in (WATCH, UNWATCH) and isinstance(e.get("schema"), str)
                and isinstance(e.get("table"), str) and e.get("by") and e.get("at")):
            bad += 1
            continue
        out.setdefault((e["schema"], e["table"]), []).append(
            {"action": e["action"], "by": e["by"], "at": e["at"], "note": e.get("note") or ""})
    if bad:
        log.warning("%d unreadable Table Comparison opt-in(s) for %s ignored", bad, slug)
    return out


def watched_tables(registry, slug: str) -> dict[tuple[str, str], dict]:
    """{(schema, table): the opt-in entry} for every table whose latest entry is an opt-in."""
    return {k: trail[-1] for k, trail in history(registry, slug).items() if trail and trail[-1]["action"] == WATCH}


def set_watch(registry, slug: str, schema: str, table: str, watch: bool, *, by: str, note: str = "") -> dict:
    """Record one opt-in (or opt-out) of one table, as `by`. The table must be in the stored inventory: a name that is
    not there is refused (ValueError), so a typo cannot opt in nothing. Returns the entry written."""
    if not by:
        raise ValueError("an opt-in needs a person to attribute it to")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise ValueError(f"the note is limited to {MAX_NOTE} characters")
    slug = registry._normalize_slug(slug)
    tables = {(t["schema_name"], t["table_name"]) for t in registry.query_detail_rows("database_tables", slug)}
    if (schema, table) not in tables:
        raise ValueError(f"{schema}.{table} is not a table in the stored inventory of {slug}")
    entry = {"schema": schema, "table": table, "action": WATCH if watch else UNWATCH, "by": by,
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
    if note:
        entry["note"] = note
    # Its own key, written once: time_ns orders entries, the random suffix keeps two writers apart.
    registry.add_setting_once(f"{_prefix(slug)}{time.time_ns():020d}-{uuid.uuid4().hex[:8]}", json.dumps(entry))
    return entry
