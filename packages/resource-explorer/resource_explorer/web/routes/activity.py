"""Activity log API routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from resource_explorer.auth import get_current_user
from resource_explorer.registry import ProjectRegistry
from resource_explorer.rfas import flatten_rfas

log = logging.getLogger(__name__)

#: Activity rows Curate writes as bookkeeping (who added or removed a tag, the typed spelling): never RFAs.

router = APIRouter()

RFA_STATUSES = {"open", "deferred", "reassigned", "completed"}

# docs/rfa-egeria-todo-followup.md — RE's own friendly verbs stay the wire
# contract this route (and the frontend drawer) speak; internally, from
# this map onward, everything is Egeria's real ToDoProperties.activityStatus
# vocabulary (pyegeria/core/_globals.py's ACTIVITY_STATUS) — one
# translation seam, at this boundary, not scattered across the stack.
_STATUS_TO_ACTIVITY_STATUS = {
    "open": "REQUESTED",
    "deferred": "WAITING",
    "reassigned": "REQUESTED",  # reassignment is an actor change, orthogonal to lifecycle status
    "completed": "COMPLETED",
}


class RfaActionUpdateRequest(BaseModel):
    status: str
    assignee: str = ""
    defer_until: str = ""
    resolution_note: str = ""


class RfaNoteRequest(BaseModel):
    notes: str


class RfaDismissRequest(BaseModel):
    """`reason` is validated by the registry against its own vocabulary
    (not_applicable / wont_do) rather than re-declared here — one list, so a
    third reason added later cannot be accepted by the route and then
    rejected by the store, or vice versa."""
    reason: str
    note: str = ""
    dismissed_by: str = ""


class RfaDismissalClearRequest(BaseModel):
    cleared_by: str = ""


def _registry() -> ProjectRegistry:
    return ProjectRegistry()


def _split_rfa_id(rfa_id: str) -> tuple[str, int, int | None]:
    """`{entry_id}::{annotation_index}` or, for one of several requests grouped under an annotation,
    `{entry_id}::{annotation_index}::{sub}` (`_emit_rfa`). The routes used to `rsplit('::', 1)`, which read
    the sub-index as the annotation index and the annotation index as part of the entry id: every action on
    a grouped request (the common SecurityHygieneCheck case) answered 404 or hit the wrong annotation."""
    parts = rfa_id.split("::")
    try:
        if len(parts) == 2:
            return parts[0], int(parts[1]), None
        if len(parts) == 3:
            return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        pass
    raise HTTPException(status_code=400,
                        detail="Malformed rfa_id — expected '{entry_id}::{annotation_index}[::{sub}]'")


def _acting_user(request: Request, supplied: str = "") -> str:
    """Who did it: the signed-in user, else what the caller named, else nobody. Never a guess."""
    user = get_current_user(request) or {}
    return user.get("user_id") or user.get("sub") or user.get("username") or supplied or ""


@router.get("/")
def list_activity(
    entity_type: str | None = Query(None),
    entity_slug: str | None = Query(None),
    intent: str | None = Query(None),
    operation: str | None = Query(None),
    status: str | None = Query(None),
    since: str | None = Query(None),
    limit: int = Query(200, le=1000),
) -> list[dict]:
    return _registry().list_activity(
        entity_type=entity_type,
        entity_slug=entity_slug,
        intent=intent,
        operation=operation,
        status=status,
        since=since,
        limit=limit,
    )


@router.get("/rfas")
def list_rfas(
    entity_type: str | None = Query(None),
    entity_slug: str | None = Query(None),
    limit: int = Query(500, le=2000),
) -> list[dict]:
    """Return all RequestForAction annotations across all activity log entries, with response actions
    overlaid and dismissals marked -- see `resource_explorer.rfas.flatten_rfas`."""
    return flatten_rfas(_registry(), entity_type, entity_slug, limit)


def _dismissal_subject(registry, entry, ann, member) -> tuple[str, str, str, str]:
    return registry.rfa_dismissal_subject(entry, ann, member)


@router.get("/rfas/dismissals")
def list_rfa_dismissals(include_cleared: bool = Query(False)) -> list[dict]:
    """Every recorded dismissal, newest first — the review surface behind
    Dan's "a future admin setting that allows you to reset or clear some of
    these decisions". `include_cleared=true` adds the ones already reversed,
    which stay in the table as history rather than being deleted."""
    return _registry().list_rfa_dismissals(include_cleared=include_cleared)


@router.post("/rfas/{rfa_id}/dismiss")
def dismiss_rfa(rfa_id: str, body: RfaDismissRequest, request: Request) -> dict:
    """Suppress a finding as not-applicable / won't-do.

    Takes an `rfa_id` because that is what the drawer has in hand, but
    records against the finding's CONTENT (entity + analysis + summary),
    resolved here from the activity entry the id points at. That asymmetry
    is deliberate and is the whole point: the user dismisses the row in
    front of them, and the same finding stays dismissed when the next survey
    run produces it again under a new id.

    The summary is THIS request's own (the sub-indexed member of a grouped
    annotation), the same one `_emit_rfa` keys the listing on; keying on the
    group's first summary would dismiss a sibling and leave the dismissed
    row showing."""
    registry = _registry()
    entry_id, idx, sub = _split_rfa_id(rfa_id)

    entry = registry.get_activity(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No activity entry {entry_id}")
    annotations = entry.get("annotations") or []
    if idx >= len(annotations):
        raise HTTPException(
            status_code=404,
            detail=f"Activity entry {entry_id} has no annotation at index {idx}",
        )
    ann = annotations[idx]
    members = ann.get("items") or [None]
    member = members[sub] if sub is not None and sub < len(members) else None

    try:
        d_type, d_slug, d_analysis, d_summary = _dismissal_subject(registry, entry, ann, member)
        row = registry.dismiss_rfa(
            entity_type=d_type,
            entity_slug=d_slug,
            analysis_name=d_analysis,
            summary_key=d_summary,
            reason=body.reason,
            note=body.note,
            created_by=_acting_user(request, body.dismissed_by),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "id": rfa_id, "dismissal": row}


@router.post("/rfas/dismissals/{dismissal_id}/clear")
def clear_rfa_dismissal(dismissal_id: str, body: RfaDismissalClearRequest, request: Request) -> dict:
    """Reverse a dismissal. The row survives with cleared_at/cleared_by set,
    so "we decided this was not applicable, then changed our mind" stays
    readable — an undelete would lose both halves of that."""
    row = _registry().clear_rfa_dismissal(dismissal_id, cleared_by=_acting_user(request, body.cleared_by))
    if row is None:
        raise HTTPException(status_code=404, detail=f"No dismissal {dismissal_id}")
    return {"status": "success", "dismissal": row}


def _todo_write_check(registry, writes) -> str:
    """Brief Z3: the zone check for an RFA's ToDo write, as the caller. A refusal is a 403 before anything is
    recorded. Round 2 (F2, prepared): when the check could not be READ (Egeria down), the route stays local-first
    as its docstring promises: "" is not returned; the words come back so the caller records the local change,
    skips the sync and keeps the words as the row's sync error (the reconcile pass retries it later)."""
    from resource_explorer.workflows.curate import BASIS_UNREADABLE
    from resource_explorer.workflows.egeria_work_access import writes_access

    d = writes_access(registry, writes)
    if d.allowed:
        return ""
    if d.basis == BASIS_UNREADABLE:
        return f"not permitted · {d.reason}"
    raise HTTPException(status_code=403, detail=d.reason)


@router.patch("/rfas/{rfa_id}")
def update_rfa_action(rfa_id: str, body: RfaActionUpdateRequest) -> dict:
    """Record a response action (defer / reassign / complete / reopen) against
    an RFA. The local write (rfa_actions) is authoritative for this response
    regardless of Egeria's reachability — a real Egeria ToDo sync is then
    attempted, same request, non-blocking of this result (docs/rfa-egeria-
    todo-followup.md's "Sync mechanics"); a failed sync never fails this
    call, it just leaves the row for scheduler.py's reconciliation pass to
    retry."""
    if body.status not in RFA_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(RFA_STATUSES)}")
    entry_id, annotation_index, _sub = _split_rfa_id(rfa_id)

    registry = _registry()
    if registry.get_activity(entry_id) is None:
        raise HTTPException(status_code=404, detail="No activity entry backs this RFA id")

    # Brief Z3: the Egeria ToDo this creates (CREATE) or updates (UPDATE_PROPERTIES on its own zones), checked as
    # the caller BEFORE the local write, so a refusal changes nothing in either place (RE and Egeria would
    # otherwise disagree, and the reconciliation pass would pull Egeria's state back over RE's).
    from resource_explorer.workflows.egeria_work_access import todo_writes

    unchecked = _todo_write_check(registry, todo_writes(registry.get_rfa_action(rfa_id)))

    registry.upsert_rfa_action(
        rfa_id=rfa_id,
        entry_id=entry_id,
        annotation_index=annotation_index,
        status=body.status,
        activity_status=_STATUS_TO_ACTIVITY_STATUS[body.status],
        assignee=body.assignee,
        defer_until=body.defer_until,
        resolution_note=body.resolution_note,
    )

    if unchecked:
        # Egeria could not be asked: recorded locally, not synced, the words kept as the row's sync error.
        registry.mark_rfa_sync_error(rfa_id, unchecked)
        return {"status": "success", "id": rfa_id, "rfa_status": body.status, "egeria_sync": unchecked}

    try:
        from resource_explorer.rfa_egeria_sync import sync_rfa_action

        row = registry.get_rfa_action(rfa_id)
        if row is not None:
            sync_rfa_action(registry, row)
    except Exception as exc:
        # Belt-and-suspenders — sync_rfa_action itself never raises, but an
        # import/registry-lookup failure here still must not fail the
        # user's action (same non-blocking guarantee).
        log.warning("RFA Egeria sync skipped for %s: %s", rfa_id, exc)

    return {"status": "success", "id": rfa_id, "rfa_status": body.status}


@router.patch("/rfas/{rfa_id}/notes")
def update_rfa_note(rfa_id: str, body: RfaNoteRequest) -> dict:
    """Record free-text notes against an RFA, independent of any
    Defer/Reassign/Complete action. Real bug fixed 2026-08-16: the drawer's
    "Record answer" button previously never called this (or any) backend
    endpoint — it only wrote to a purely client-side, in-memory log, so
    nothing survived a page reload."""
    entry_id, annotation_index, _sub = _split_rfa_id(rfa_id)

    registry = _registry()
    if registry.get_activity(entry_id) is None:
        raise HTTPException(status_code=404, detail="No activity entry backs this RFA id")

    # Brief Z3 round 2 (F3): the NoteLog and Note this creates on the RFA's ToDo (CREATE, judged on the ToDo as
    # their anchor), checked as the caller before the local write, the same shape as the status route.
    from resource_explorer.workflows.egeria_work_access import note_writes

    unchecked = _todo_write_check(registry, note_writes(registry.get_rfa_action(rfa_id)))

    registry.upsert_rfa_note(rfa_id=rfa_id, entry_id=entry_id, annotation_index=annotation_index, notes=body.notes)

    if unchecked:
        registry.mark_rfa_note_sync_error(rfa_id, unchecked)
        return {"status": "success", "id": rfa_id, "notes": body.notes, "egeria_sync": unchecked}

    try:
        from resource_explorer.rfa_egeria_sync import sync_rfa_note

        row = registry.get_rfa_action(rfa_id)
        if row is not None:
            sync_rfa_note(registry, row)
    except Exception as exc:
        # Non-blocking, same guarantee as the status route — a failed (or
        # not-yet-possible, no ToDo yet) note sync never fails this call.
        log.warning("RFA note Egeria sync skipped for %s: %s", rfa_id, exc)

    return {"status": "success", "id": rfa_id, "notes": body.notes}


@router.get("/{entry_id}")
def get_activity_entry(entry_id: str) -> dict:
    entry = _registry().get_activity(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Activity entry not found")
    return entry


@router.get("/{entry_id}/declared-vs-received")
def get_declared_vs_received(entry_id: str) -> dict:
    """One completed survey run held against its own declaration.

    Per declared annotation type: received under this run's SurveyReport, or
    not — and if not, WHY, from the run's own step statuses. "Ran and produced
    nothing of this type" is an answer; "the step did not finish" is a defect;
    the two were indistinguishable before this route and the second was
    invisible. See run_reconciliation.py for what is deliberately not claimed.

    404 for an unknown entry; 409 for an entry that is not a survey run with a
    step list, because there is nothing to reconcile against and an empty
    reconciliation would read as "everything matched".
    """
    from resource_explorer.run_reconciliation import reconcile_run

    entry = _registry().get_activity(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Activity entry not found")
    rec = reconcile_run(entry, _registry())
    if rec is None:
        raise HTTPException(
            status_code=409,
            detail="This activity entry is not a survey run with a step list, so there is no declaration to hold it against.",
        )
    return rec.to_dict()
