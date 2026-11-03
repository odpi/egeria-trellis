"""Convenience helpers for writing activity log entries."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from resource_explorer.registry import ActivityEntry

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_survey(
    registry: "ProjectRegistry",
    entity_type: str,
    entity_slug: str,
    entity_name: str,
    entity_location: str,
    intent: str,
    status: str,
    summary: str,
    detail: str = "",
    items: list[dict] | None = None,
    annotations: list[dict] | None = None,
    runner: dict | None = None,
) -> str:
    """Write a survey activity entry.

    `runner` closes docs/process-model.md's Finding F5. `log_analysis_run` has
    taken a `runner` since 2026-08-30; this function did not, so callers that
    wanted to record ownership had to hand-encode `{"_runner": ...}` into
    `detail` themselves (survey_definitions.py did) and callers that did not
    think to — the scouting scan — left rows with no owner at all, reconciled by
    `run_reconciler`'s six-hour age heuristic instead of by pid liveness. Same
    field, same reader (`run_reconciler.owner_of`), now the same parameter.

    It is merged into `detail` rather than replacing it, so a caller can pass
    both its own JSON detail and an owner. Pass it only for a 'running' row: the
    terminal `update_activity_status()` rewrites `detail` and drops it, which is
    correct — a terminal row is never a reconciliation candidate.

    **Who is the runner is not always the caller.** Since the run queue landed,
    a route that enqueues work is not the process that will execute it, and
    stamping itself would be worse than stamping nothing. Those callers leave
    this unset; `run_queue.execute_run` records the real owner via
    `registry.set_activity_runner` when the work actually starts.
    """
    if runner is not None:
        import json as _json

        try:
            merged = _json.loads(detail or "{}") or {}
        except (TypeError, ValueError):
            merged = {}
        if not isinstance(merged, dict):
            merged = {}
        merged["_runner"] = runner
        detail = _json.dumps(merged)

    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="survey",
        intent=intent,
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_name=entity_name,
        entity_location=entity_location,
        status=status,
        summary=summary,
        detail=detail,
        items=items or [],
        annotations=annotations or [],
    ))
    return entry_id


def log_catalog(
    registry: "ProjectRegistry",
    entity_type: str,
    entity_slug: str,
    entity_name: str,
    entity_location: str,
    status: str,
    summary: str,
    detail: str = "",
    items: list[dict] | None = None,
) -> str:
    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="catalog",
        intent="assessment",
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_name=entity_name,
        entity_location=entity_location,
        status=status,
        summary=summary,
        detail=detail,
        items=items or [],
        annotations=[],
    ))
    return entry_id


def log_preview(
    registry: "ProjectRegistry",
    *,
    slug: str,
    name: str,
    location: str,
    who: str,
    db_user: str,
    schema: str,
    table: str,
    action: str,
    limit: int,
    outcome: str,
    rows: int,
    masked: list[str],
    revealed: list[str],
) -> str:
    """One row per table preview or column reveal (docs/design-notes/REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md §6).

    Who looked (`who`, the signed-in person), as which database user, at which table, when (`ts`), how many rows
    came back, which columns were masked and which revealed. COLUMN NAMES ONLY: no row value is ever passed to this
    function, so none can be stored. `action` is 'preview' or 'reveal'; `outcome` is what the database answered
    ('rows', 'empty', or a refusal word), and a refusal is recorded as an error row because the attempt happened.
    """
    import json

    detail = {"action": action, "who": who, "db_user": db_user, "schema": schema, "table": table, "limit": limit,
              "outcome": outcome, "rows": rows, "masked": list(masked), "revealed": list(revealed)}
    verb = "Revealed columns of" if action == "reveal" else "Previewed"
    summary = (f"{verb} {schema}.{table} · {rows} row(s) · as {db_user} · by {who}"
               + (f" · revealed {', '.join(revealed)}" if revealed else "")
               + ("" if outcome in ("rows", "empty") else f" · {outcome}"))
    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="preview",
        intent="assessment",
        entity_type="database",
        entity_slug=slug,
        entity_name=name,
        entity_location=location,
        status="ok" if outcome in ("rows", "empty") else "error",
        summary=summary,
        detail=json.dumps(detail),
    ))
    return entry_id


def log_preview_credential(
    registry: "ProjectRegistry",
    *,
    slug: str,
    name: str,
    location: str,
    who: str,
    db_user: str,
    outcome: str,
) -> str:
    """One row per attempt to hold a preview credential, whatever came of it (finding 4).

    Who tried, as which database user, and what happened: `held`, `refused`, `unreachable` or `throttled`. The
    password is not a parameter of this function, so it cannot be stored. A failed attempt is an error row.
    """
    import json

    detail = {"action": "credential", "who": who, "db_user": db_user, "outcome": outcome}
    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="preview",
        intent="assessment",
        entity_type="database",
        entity_slug=slug,
        entity_name=name,
        entity_location=location,
        status="ok" if outcome == "held" else "error",
        summary=f"Preview credential {outcome} · as {db_user or '(none)'} · by {who}",
        detail=json.dumps(detail),
    ))
    return entry_id


def _declared_stage(entity_type: str, analysis_id: str) -> str:
    """The stage the catalog declares for this analysis ("analysis", "discovery", ...), so a row for an
    Analysis-stage step does not say 'assessment'. 'assessment' only when the catalog names no stage for it."""
    try:
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        for a in get_analyses(entity_type, include_egeria_live=False):
            if a.get("id") == analysis_id and a.get("intent"):
                return a["intent"]
    except (ImportError, OSError, ValueError, KeyError) as exc:
        # The catalog could not be read: the row is still written, but the lapse is said, not swallowed.
        logging.getLogger(__name__).warning(
            "analysis catalog unreadable for %s/%s (%s); the row says 'assessment'", entity_type, analysis_id, exc)
    return "assessment"


def log_analysis_run(
    registry: "ProjectRegistry",
    entity_type: str,
    entity_slug: str,
    entity_name: str,
    status: str,
    summary: str,
    analysis_id: str,
    published: bool | None = None,
    runner: dict | None = None,
    extra: dict | None = None,
) -> str:
    """One row per local AnalysisKind run (POST /{slug}/analyses/{analysis_id}
    /run) — the Analyses cards' equivalent of log_survey's Survey Definition
    tracking (2026-08-24: direct feedback that Analyses cards had no last-run
    signal at all, unlike Survey Definition cards). `detail` carries
    {"analysis_id": ...} as the join key, same convention as
    survey_definitions.py's survey_definition_ref — see
    registry.get_analysis_last_run().

    `published` is three-state, not a bool default-False: True (auto-publish
    attempted and succeeded), False (attempted and failed — the ☁ Publish
    button on this card should stay visible as a recovery action, findings
    are already stored, only the Egeria write needs retrying), or None
    (never attempted — unassigned resource or no annotations, same button
    stays visible as the only way to publish at all). See
    registry.get_analysis_last_run()'s `last_publish_failed`.

    `runner` (2026-08-30, backgrounded analysis-card runs): the owning
    process's identity (`run_reconciler.process_identity()`), embedded under
    `_runner` in `detail` exactly like `survey_definitions.py`'s
    `log_survey()` call does for its own 'running' row — this is the field
    `run_reconciler.owner_of()` reads to resolve a row left "running" by a
    process that died mid-run. Pass it only for the initial 'running' call;
    the terminal `update_activity_status()` overwrites `detail` and drops it,
    which is fine — a terminal row is never a reconciliation candidate."""
    import json

    detail = {"analysis_id": analysis_id, "published": published}
    if runner is not None:
        detail["_runner"] = runner
    if extra:
        # e.g. `ran_as` for a run with a credential typed for that run only (brief section 8):
        # who it ran as, never with what.
        detail.update(extra)

    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="analysis_run",
        intent=_declared_stage(entity_type, analysis_id),
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_name=entity_name,
        entity_location="",
        status=status,
        summary=summary,
        detail=json.dumps(detail),
    ))
    return entry_id


def log_rfa(
    registry: "ProjectRegistry",
    entity_type: str,
    entity_slug: str,
    entity_name: str,
    status: str,
    summary: str,
    detail: str = "",
    analysis_name: str = "",
    items: list[dict] | None = None,
) -> str:
    """Write an RFA and make sure it actually reaches the RFA drawer.

    `items` carries the same structured pointers an ordinary activity entry can
    (see renderActivityLog's link handling) — an RFA that says "3 publishes are
    stuck" is only actionable if it can also say where to look at them.

    The drawer is fed by GET /api/activity/rfas, which flattens activity entries
    and keeps only *annotations* whose annotation_type contains
    "RequestForAction". It does not look at operation="rfa" at all. This function
    wrote the entry with an empty annotations list, so every RFA it produced was
    invisible there — the entry existed in the activity log, and the surface
    built to act on it never showed it.

    That affected all three callers: Automate's change notifications
    (scheduler.py), Enrichment's context requests (web/routes/context.py), and
    Egeria linkage divergences (egeria_linkage.py). Each is a request for a human
    action that no human was shown.

    The annotation carries the same summary as the entry rather than a different
    one: the drawer reads the annotation's, the activity log reads the entry's,
    and two texts drifting apart for one event would be worse than the
    duplication.
    """
    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="rfa",
        intent="enrichment",
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_name=entity_name,
        entity_location="",
        status=status,
        summary=summary,
        detail=detail,
        items=items or [],
        annotations=[{
            # The exact type string GET /api/activity/rfas substring-matches on,
            # and the same one EgeriaPublisher uses for a published RFA.
            "annotation_type": "RequestForActionAnnotation",
            "analysis_name": analysis_name or "Request for action",
            "count": 1,
            "summary": summary,
            "status": status,
        }],
    ))
    return entry_id


def log_scout(
    registry: "ProjectRegistry",
    entity_type: str,
    entity_slug: str,
    entity_name: str,
    entity_location: str,
    status: str,
    summary: str,
    detail: str = "",
    entry_id: str = "",
    items: list[dict] | None = None,
) -> str:
    """Write a scout entry. Passing the `entry_id` of an earlier 'running' entry
    finalises that row in place (write_activity upserts on id) instead of adding
    a second one, so a long import shows up as running from its first moment."""
    entry_id = entry_id or str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id,
        ts=_now(),
        operation="scout",
        intent="scouting",
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_name=entity_name,
        entity_location=entity_location,
        status=status,
        summary=summary,
        detail=detail,
        items=items or [],
    ))
    return entry_id


def log_first_index(registry: "ProjectRegistry", slug: str, name: str, status: str, summary: str) -> str:
    """One row per first-index run (a person's yes to indexing a repository for chat). The run queue closes it."""
    entry_id = str(uuid.uuid4())
    registry.write_activity(ActivityEntry(
        id=entry_id, ts=_now(), operation="first_index", intent="automate", entity_type="repo",
        entity_slug=slug, entity_name=name, entity_location="", status=status, summary=summary,
    ))
    return entry_id


def log_first_index_batch(registry: "ProjectRegistry", batch_id: str, who: str, total: int, queued: int,
                          path: str) -> str:
    """The ONE activity row of a batch of first-index yeses, keyed by the batch id and rewritten as yeses come in
    (`write_activity` upserts on id). It records the decision: who, which path, how many said yes so far of how many
    were offered. It is a record, not a running job, so it is never left 'running'."""
    import json

    way = {"all": "Index all", "each": "Ask me for each"}.get(path, path)
    registry.write_activity(ActivityEntry(
        id=batch_id, ts=_now(), operation="first_index", intent="automate", entity_type="repo",
        entity_slug="", entity_name="First indexing", entity_location="", status="ok",
        summary=f"First index for chat, {way}: {queued} of {total} repositories queued by {who}.",
        detail=json.dumps({"batch_id": batch_id, "asked_by": who, "path": path, "offered": total,
                           "queued": queued}),
    ))
    return batch_id
