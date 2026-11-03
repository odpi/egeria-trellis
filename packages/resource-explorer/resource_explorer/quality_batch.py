"""Quality slice Q3: the DatabaseQualitySurvey run across a work list of databases, priced first, and on a schedule.

DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md §2.2: nothing new is invented. The definition is Q2's; the batch is one
`survey_definition_run` per database through the same queue, the same route helper and the same access check a press
uses (`survey_definitions.enqueue_definition_run`, `egeria_work_access.batch_refusal`), recorded as a run set in
`work_list_runs` so `WorkLists.batch_progress` watches it like any other set. The batch is a list of runs, never a run
of its own: every result, verdict and proof row stays per database.

Three things are Q3's own:

* **The price line** (§2.3), one row per database, before anything is queued: tables and bytes from RE's latest
  stored inventory (a table with no size reads "size unknown · never analyzed", never 0), the time from the measured
  wall time of this database's earlier quality runs ("not measured yet" when there are none, never a guess), and
  whether the run can start at all (a stored credential).
* **Skip-unchanged** (§2.2 point 3), for the scheduled batch only: a database whose inventory did not change since its
  last quality run is skipped and says so. The inventory is what RE's surveys stored: the tables, the columns with
  their types, and each table's row count. "Did not change" compares the latest inventory with the one the last
  quality run followed; anything that cannot be compared (no quality run yet, no inventory, a row count missing on
  either side) RUNS, because "could not tell" is not "unchanged".
* **The schedule**: a `resource_schedules` row with `entity_type = "work_list"`, the list's slug and the definition
  (target_kind "survey"). It follows membership: each firing runs over the list's members as they are then, the way a
  Group schedule does (REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md §2), and a member with its own
  schedule for the definition keeps it (§3: the more specific decision wins) and is reported as such. No new table.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from resource_explorer.surveyors.survey_definition_reader import qualified_process_name

log = logging.getLogger(__name__)

#: The definition Q3 batches (Q2's, authored in Dr.Egeria and committed).
QUALITY_DEFINITION = "DatabaseQualitySurvey"

#: The `resource_schedules.entity_type` of a schedule on a work list rather than on one resource.
WORK_LIST_ENTITY = "work_list"

#: Outcomes of one member of a batch. OWN_SCHEDULE: the database has its own schedule for the definition, which wins
#: over the list's (REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md §3: "the repository's own schedule
#: wins, then the Group's"), so the list's firing leaves it to its own cadence and says so.
QUEUED, SKIPPED, NOT_PERMITTED, CANNOT_RUN, OWN_SCHEDULE = (
    "queued", "skipped", "not permitted", "cannot run", "own schedule")

#: How many succeeded runs the time estimate reads, newest first.
_MEASURED_RUNS = 5


# ── Times ───────────────────────────────────────────────────────────────────────────────────────────────

def _instant(value) -> datetime | None:
    """An ISO timestamp as an aware UTC instant (RE writes UTC, some of it naive), or None."""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def _day(value) -> str:
    at = _instant(value)
    return at.strftime("%Y-%m-%d %H:%MZ") if at else str(value or "")


def _duration_words(seconds: float) -> str:
    if seconds < 60:
        return f"{max(1, round(seconds))}s"
    m, s = divmod(round(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _bytes_words(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"     # pragma: no cover - the loop always returns


# ── The inventory ───────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Inventory:
    """One stored survey's tables and columns for a database: what skip-unchanged compares."""
    surveyed_at: str
    source: str
    tables: dict = field(default_factory=dict)     # (schema, table) -> row_count (None = not measured)
    columns: dict = field(default_factory=dict)    # (schema, table, column) -> type
    sizes: dict = field(default_factory=dict)      # (schema, table) -> size_bytes (None = not read)


def _inventory_keys(registry, slug: str) -> list[tuple[str, str]]:
    """(surveyed_at, source) of every stored table inventory, newest first, leaving out a survey marked invalid. Read
    from `database_tables` itself (an inventory is its table rows) and, for the invalid marks, from `database_surveys`
    without its blobs, which can be tens of MB for one database."""
    slug = registry._normalize_slug(slug)
    with registry._conn() as conn:
        rows = conn.execute("SELECT DISTINCT surveyed_at, source FROM database_tables WHERE database_slug = ?",
                            (slug,)).fetchall()
    invalid = {(s.get("surveyed_at"), s.get("source") or "local")
               for s in registry.get_database_surveys(slug, include_invalid=True, with_survey_data=False)
               if s.get("invalid_at")}
    keys = [(r["surveyed_at"], r["source"] or "local") for r in rows]
    keys = [k for k in keys if k[0] and k not in invalid]
    keys.sort(key=lambda k: _instant(k[0]) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return keys


def _read_inventory(registry, slug: str, surveyed_at: str, source: str) -> Inventory | None:
    tables = registry.query_detail_rows("database_tables", slug, surveyed_at, source)
    if not tables:
        return None
    columns = registry.query_detail_rows("database_columns", slug, surveyed_at, source)
    key = lambda r: (r.get("schema_name") or "", r.get("table_name") or "")
    return Inventory(
        surveyed_at=surveyed_at, source=source,
        tables={key(t): t.get("row_count") for t in tables},
        sizes={key(t): t.get("size_bytes") for t in tables},
        columns={(*key(c), c.get("column_name") or ""): (c.get("base_type") or c.get("data_type") or "").strip()
                 for c in columns},
    )


def inventories(registry, slug: str) -> list[Inventory]:
    """Every stored inventory that has table rows, newest first."""
    out = []
    for at, source in _inventory_keys(registry, slug):
        inv = _read_inventory(registry, slug, at, source)
        if inv is not None:
            out.append(inv)
    return out


def last_quality_run_at(registry, slug: str) -> str:
    """When the last quality run profiled this database: the newest `database_value_profiles` batch. "" = never."""
    rows = registry.query_detail_rows("database_value_profiles", slug)
    return str(rows[0].get("surveyed_at") or "") if rows else ""


def _differences(before: Inventory, after: Inventory) -> tuple[list[str], list[str]]:
    """(what changed, what could not be compared) between two inventories, in words."""
    changed: list[str] = []
    unknown: list[str] = []
    added = sorted(after.tables.keys() - before.tables.keys())
    dropped = sorted(before.tables.keys() - after.tables.keys())
    if added:
        changed.append(f"{len(added)} table(s) added")
    if dropped:
        changed.append(f"{len(dropped)} table(s) dropped")
    cols_added = after.columns.keys() - before.columns.keys()
    cols_dropped = before.columns.keys() - after.columns.keys()
    retyped = [k for k in before.columns.keys() & after.columns.keys()
               if before.columns[k] and after.columns[k] and before.columns[k] != after.columns[k]]
    if cols_added:
        changed.append(f"{len(cols_added)} column(s) added")
    if cols_dropped:
        changed.append(f"{len(cols_dropped)} column(s) dropped")
    if retyped:
        changed.append(f"{len(retyped)} column(s) retyped")
    counted = 0
    for t in sorted(before.tables.keys() & after.tables.keys()):
        old, new = before.tables[t], after.tables[t]
        if old is None or new is None:
            unknown.append(f"{t[0]}.{t[1]}")
        elif old != new:
            counted += 1
    if counted:
        changed.append(f"row counts changed on {counted} table(s)")
    return changed, unknown


@dataclass(frozen=True)
class ChangeVerdict:
    """Whether the inventory changed since the last quality run. `unchanged` is True only when that was compared and
    nothing differed; every other case runs."""
    unchanged: bool
    words: str
    last_quality_run_at: str = ""
    inventory_at: str = ""


def inventory_change(registry, slug: str) -> ChangeVerdict:
    """Skip-unchanged's question, answered from what RE stored. Opens no connection to the database."""
    quality_at = last_quality_run_at(registry, slug)
    if not quality_at:
        return ChangeVerdict(False, "never had a quality run")
    invs = inventories(registry, slug)
    if not invs:
        return ChangeVerdict(False, "no inventory stored · cannot tell whether it changed", quality_at)
    latest = invs[0]
    q = _instant(quality_at)
    followed = next((inv for inv in invs if q and (_instant(inv.surveyed_at) or q) <= q), None)
    if followed is None:
        return ChangeVerdict(False, f"no inventory from before its last quality run on {_day(quality_at)} "
                                    "· cannot tell whether it changed", quality_at, latest.surveyed_at)
    if followed.surveyed_at == latest.surveyed_at and followed.source == latest.source:
        return ChangeVerdict(True, f"unchanged since its last quality run on {_day(quality_at)} "
                                   f"· no new inventory since {_day(latest.surveyed_at)}",
                             quality_at, latest.surveyed_at)
    changed, unknown = _differences(followed, latest)
    if changed:
        return ChangeVerdict(False, f"changed since its last quality run on {_day(quality_at)} · "
                                    + "; ".join(changed), quality_at, latest.surveyed_at)
    if unknown:
        return ChangeVerdict(False, f"cannot tell whether it changed · row count not measured on "
                                    f"{len(unknown)} table(s)", quality_at, latest.surveyed_at)
    return ChangeVerdict(True, f"unchanged since its last quality run on {_day(quality_at)} "
                               f"· the inventory of {_day(latest.surveyed_at)} matches",
                         quality_at, latest.surveyed_at)


# ── The price line ──────────────────────────────────────────────────────────────────────────────────────

def measured_seconds(registry, slug: str, definition: str = QUALITY_DEFINITION) -> list[float]:
    """Wall times of this database's succeeded runs of `definition`, newest first, from the queue's own rows."""
    out: list[float] = []
    for row in registry.list_runs(kind="survey_definition_run", state="succeeded", limit=500):
        try:
            target = json.loads(row.get("target") or "{}")
        except ValueError:
            continue
        # A run is recorded under the reference it was queued with: the bare name (before 2026-10-30) or the qualified one.
        if target.get("slug") != slug or (target.get("params") or {}).get("survey_definition_ref") not in (
                definition, qualified_process_name(definition)):
            continue
        t0, t1 = _instant(row.get("started_at")), _instant(row.get("finished_at"))
        if t0 and t1 and t1 >= t0:
            out.append((t1 - t0).total_seconds())
        if len(out) >= _MEASURED_RUNS:
            break
    return out


def _credential_words(db) -> str:
    """"" when the run can authenticate with what is stored, else why it cannot start."""
    if getattr(db, "credential_status", "") == "unreadable":
        return f"credential unreadable · re-enter the credential for {db.slug}"
    if not getattr(db, "db_user", "") or not getattr(db, "db_password", ""):
        return "no stored credential · register one before a batch can run it"
    return ""


def price_row(registry, slug: str, *, definition: str = QUALITY_DEFINITION) -> dict:
    """One database's line, before the batch starts. Every figure says whether it was measured."""
    db = registry.get_database(slug, allow_unreadable=True)
    if db is None:
        return {"entity_slug": slug, "display_name": slug, "can_run": False,
                "blocked": f"no such database · '{slug}'", "tables": None, "size": None, "time": None,
                "change": None, "words": f"cannot run · no such database · '{slug}'"}
    invs = inventories(registry, slug)
    latest = invs[0] if invs else None
    if latest is None:
        tables = {"count": None, "words": "tables not recorded · no survey stored yet"}
        size = {"bytes": None, "unknown_tables": None, "words": "size unknown · no survey stored yet"}
    else:
        known = [b for b in latest.sizes.values() if b is not None]
        unknown_n = len(latest.sizes) - len(known)
        tables = {"count": len(latest.tables), "as_of": latest.surveyed_at,
                  "words": f"{len(latest.tables)} tables · as of {_day(latest.surveyed_at)}"}
        parts = [_bytes_words(sum(known))] if known else []
        if unknown_n:
            parts.append(f"{unknown_n} table(s) size unknown · never analyzed")
        size = {"bytes": sum(known) if known else None, "unknown_tables": unknown_n,
                "words": " · ".join(parts) if parts else "size unknown · never analyzed"}
    secs = measured_seconds(registry, slug, definition)
    if secs:
        ordered = sorted(secs)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        time = {"seconds": median, "basis": "measured", "runs": len(secs),
                "words": f"about {_duration_words(median)} · measured over {len(secs)} run(s)"}
    else:
        time = {"seconds": None, "basis": "not measured", "runs": 0,
                "words": "time not measured yet · the first run sets it"}
    from resource_explorer.surveyors.database.survey_definition_adapter import _quality_bounds

    budget_min = _quality_bounds().table_budget_seconds / 60      # the bound the run itself applies
    blocked = _credential_words(db)
    change = inventory_change(registry, slug)
    words = " · ".join(p for p in (tables["words"], size["words"], time["words"]) if p)
    return {
        "entity_slug": slug, "display_name": getattr(db, "display_name", "") or slug,
        "can_run": not blocked, "blocked": blocked,
        "tables": tables, "size": size, "time": time,
        "budget_words": f"each table's profile stops at {budget_min:g} min",
        "change": {"unchanged": change.unchanged, "words": change.words,
                   "last_quality_run_at": change.last_quality_run_at, "inventory_at": change.inventory_at},
        "words": f"cannot run · {blocked}" if blocked else words,
    }


def price_batch(registry, slugs, *, definition: str = QUALITY_DEFINITION) -> dict:
    """The price line for a batch: one row per database, and the total time when every row is measured."""
    rows = [price_row(registry, s, definition=definition) for s in dict.fromkeys(slugs)]
    runnable = [r for r in rows if r["can_run"]]
    timed = [r["time"]["seconds"] for r in runnable if r["time"]["seconds"] is not None]
    if runnable and len(timed) == len(runnable):
        total = {"seconds": sum(timed), "words": f"about {_duration_words(sum(timed))} in all, if run one after "
                                                 "another · measured"}
    elif timed:
        total = {"seconds": None, "words": f"{len(runnable) - len(timed)} of {len(runnable)} not measured yet · "
                                           "no total until each has run once"}
    else:
        total = {"seconds": None, "words": "not measured yet · the first batch sets the price"}
    return {"definition": definition, "rows": rows, "runnable": len(runnable), "total": total}


# ── Members ─────────────────────────────────────────────────────────────────────────────────────────────

def work_list_members(registry, work_list_slug: str) -> list[str]:
    """The members of a database work list, as they are now. Raises KeyError (no such list) or ValueError (a list of
    another kind: the definition is a database definition)."""
    from resource_explorer.work_lists import WorkLists

    wl = WorkLists(registry).get(work_list_slug)
    if not wl:
        raise KeyError(work_list_slug)
    if wl.get("entity_type") != "database":
        raise ValueError(f"{QUALITY_DEFINITION} runs on databases · '{work_list_slug}' is a list of "
                         f"{wl.get('entity_type') or 'repo'}")
    return [m["entity_slug"] for m in wl["members"]]


# ── The batch ───────────────────────────────────────────────────────────────────────────────────────────

def _record_set(registry, set_id: str, work_list_slug: str, queued: list[dict], definition: str) -> None:
    """The run set, in the same table and shape `WorkLists.enqueue_batch` writes, so `batch_progress` reads it."""
    now = datetime.now(UTC).isoformat()
    with registry._conn() as conn:
        for row in queued:
            conn.execute(
                "INSERT INTO work_list_runs (set_id, run_id, work_list_slug,"
                " entity_slug, analysis_id, enqueued_at) VALUES (?, ?, ?, ?, ?, ?)",
                (set_id, row["run_id"], work_list_slug, row["entity_slug"], definition, now))


def enqueue_quality_batch(registry, slugs, *, work_list_slug: str = "", requested_by: str = "",
                          pressed=None, started_by_person: str = "", skip_unchanged: bool = False,
                          refusal=None, definition: str = QUALITY_DEFINITION,
                          own_schedules: dict | None = None) -> dict:
    """One `survey_definition_run` per database that can run, each through the press's own helper.

    Every member comes back with its outcome and its words: queued (with its run id and price), skipped (unchanged,
    scheduled batches only), not permitted (the caller may not publish it: `refusal(slug)` words) or cannot run (no
    such database, no usable credential). A member that cannot run is never queued to fail a minute later.
    `pressed` is the presser's identity for a press (their session is held per run); None for a scheduled batch,
    which runs as `requested_by` without a held session. `own_schedules` ({slug: cadence}, scheduled batches only):
    members whose own schedule for the definition wins over the list's."""
    from resource_explorer.engine_host import STARTED_BY_RE
    from resource_explorer.web.routes.survey_definitions import enqueue_definition_run
    from resource_explorer.workflows.survey_definition import SurveyDefinitionRunParams

    members = list(dict.fromkeys(slugs))
    if not members:
        raise ValueError("a batch needs at least one database")
    set_id = f"set-{uuid.uuid4().hex[:12]}"
    outcomes: list[dict] = []
    queued: list[dict] = []
    for slug in members:
        price = price_row(registry, slug, definition=definition)
        base = {"entity_slug": slug, "display_name": price["display_name"], "price": price}
        if not price["can_run"]:
            outcomes.append({**base, "outcome": CANNOT_RUN, "words": price["words"]})
            continue
        own = own_schedules.get(slug) if own_schedules else None
        if own:
            outcomes.append({**base, "outcome": OWN_SCHEDULE,
                             "words": f"own schedule · runs {own} on its own, not with this list"})
            continue
        if skip_unchanged and price["change"]["unchanged"]:
            outcomes.append({**base, "outcome": SKIPPED, "words": f"skipped · {price['change']['words']}"})
            continue
        why = refusal(slug) if refusal is not None else ""
        if why:
            outcomes.append({**base, "outcome": NOT_PERMITTED, "words": why})
            continue
        # Resolved by the definition's qualified name, through the shared resolver (the bare name does not resolve in
        # Egeria; a display name is never used).
        params = SurveyDefinitionRunParams(survey_definition_ref=qualified_process_name(definition), started_by=STARTED_BY_RE,
                                           started_by_person=started_by_person)
        started = enqueue_definition_run(registry, "database", slug, params, pressed=pressed,
                                         requested_by_user=requested_by, as_presser=pressed is not None)
        row = {"run_id": started["run_id"], "entity_slug": slug, "activity_id": started["activity_id"]}
        queued.append(row)
        outcomes.append({**base, **row, "outcome": QUEUED, "words": f"queued · {price['words']}"})
    if queued:
        _record_set(registry, set_id, work_list_slug, queued, definition)
    log.info("quality batch %s: %s", set_id, batch_summary(outcomes))
    return {"set_id": set_id if queued else "", "definition": definition, "work_list_slug": work_list_slug,
            "members": outcomes, "summary": batch_summary(outcomes)}


def batch_summary(outcomes: list[dict]) -> str:
    """One sentence for the activity row and the schedule: counts first, then each member that did not run, by name,
    with its words (a skip says why; nothing is silently dropped)."""
    others = (SKIPPED, OWN_SCHEDULE, NOT_PERMITTED, CANNOT_RUN)
    n = {k: sum(1 for o in outcomes if o["outcome"] == k) for k in (QUEUED, *others)}
    head = [f"{n[QUEUED]} queued"] + [f"{n[k]} {k}" for k in others if n[k]]
    tail = [f"{o['entity_slug']} {o['words']}" for o in outcomes if o["outcome"] != QUEUED]
    return " · ".join(head) + (": " + "; ".join(tail) if tail else "")


# ── The schedule ────────────────────────────────────────────────────────────────────────────────────────

def run_scheduled_batch(registry, work_list_slug: str, definition: str, *, created_by: str = ""
                        ) -> tuple[str, str, list[str], dict]:
    """A `work_list` schedule firing: the list's members as they are now, skip-unchanged on, each member checked as
    the schedule's creator (the scheduler sets them as the caller). Returns (name, location, errors, result) in the
    scheduler's shape; `result` carries every member's outcome for the activity row."""
    from resource_explorer.workflows.egeria_work_access import batch_refusal
    from resource_explorer.zone_access import EgeriaAccessReader

    if definition != QUALITY_DEFINITION:
        return (work_list_slug, "", [f"a work-list schedule runs {QUALITY_DEFINITION} only · got '{definition}'"], {})
    try:
        slugs = work_list_members(registry, work_list_slug)
    except KeyError:
        return (work_list_slug, "", [f"Work list '{work_list_slug}' not found — schedule may be stale"], {})
    except ValueError as exc:
        return (work_list_slug, "", [str(exc)], {})
    if not slugs:
        return (work_list_slug, "", [f"Work list '{work_list_slug}' has no members · nothing to run"], {})
    reader = EgeriaAccessReader()
    result = enqueue_quality_batch(
        registry, slugs, work_list_slug=work_list_slug, requested_by=created_by, started_by_person=created_by,
        pressed=None, skip_unchanged=True, definition=definition,
        own_schedules=own_schedules(registry, slugs, definition),
        refusal=lambda s: batch_refusal(registry, "database", s, reader=reader))
    errors = [f"{o['entity_slug']}: {o['words']}" for o in result["members"]
              if o["outcome"] in (NOT_PERMITTED, CANNOT_RUN)]
    return (work_list_slug, "", errors, result)


def own_schedules(registry, slugs, definition: str) -> dict[str, str]:
    """{slug: cadence} for the members that have their own enabled schedule for `definition`."""
    out = {}
    for slug in slugs:
        for row in registry.get_schedules("database", slug):
            if row.get("analysis_id") == definition and row.get("enabled") and row.get("schedule") != "manual":
                out[slug] = row.get("schedule") or ""
    return out
