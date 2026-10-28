"""Publish Queue API — the detailed record behind an activity-log summary.

The activity log is an audit trail: one line per operation, saying *that* a
publish was incomplete. This is the log proper — which elements, their
qualifiedNames, how many attempts each took, and what Egeria actually said.
Admin → Publish Queue reads it, and the activity entry links here.

Kept separate from `activity.py` deliberately. Folding per-element detail into
activity entries would make every incomplete publish write a wall of text into
a surface designed to be skimmed one line at a time — and the detail belongs to
the rows, which outlive the entry and change as retries happen.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer.registry import ProjectRegistry
from resource_explorer.web import admin_auth

router = APIRouter()

#: Statuses a caller may filter by. Rejecting anything else keeps a typo from
#: silently returning the unfiltered table, which would read as "nothing is
#: stuck" exactly when something is.
_VALID_STATUSES = ("pending", "running", "failed", "dead", "done", "superseded")
#: "running" means a drainer holds a claim on the row. It is a real,
#: queryable state rather than an internal flag: a row stuck in it past
#: CLAIM_LEASE_SECONDS is the signature of a drainer that died mid-batch.


def is_destructive_kind(element_kind: str) -> bool:
    """True for an outbox kind whose write archives, deletes or detaches something in Egeria.

    A retry of one is itself a destructive write (row 69822, 2026-10-06), so the Publish Queue never offers it
    and this route refuses it; a person re-presses the original control instead. The rule lives in
    `egeria_outbox.is_destructive_outbox_kind`, shared with the drain's lease reclaim.
    """
    from resource_explorer.egeria_outbox import is_destructive_outbox_kind

    return is_destructive_outbox_kind(element_kind)


class OutboxListResponse(BaseModel):
    counts: dict[str, int]
    rows: list[dict]
    max_attempts: int


@router.get("/", response_model=OutboxListResponse)
def list_outbox(
    status: str | None = None,
    run_id: str | None = None,
    entity_slug: str | None = None,
    limit: int = 200,
) -> OutboxListResponse:
    """Pending, retrying, dead and completed element writes.

    `counts` is over the WHOLE table, never the filtered page — a panel showing
    "2 dead" while filtered to `pending` should still say 2 dead, or the filter
    silently becomes a claim about the system.
    """
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status {status!r}. Valid: {', '.join(_VALID_STATUSES)}",
        )
    registry = ProjectRegistry()
    rows = registry.list_outbox_elements(
        status=status, run_id=run_id, entity_slug=entity_slug, limit=limit,
    )
    # `destructive` tells the panel to draw no Retry on this row, so the control is absent rather than refused.
    # `not_retryable` (the words) likewise: a dead row a retry would fail the same way, e.g. a membership
    # confidence RE refused to send.
    from resource_explorer.egeria_outbox import retry_refusal

    for r in rows:
        r["destructive"] = is_destructive_kind(r.get("element_kind", ""))
        r["not_retryable"] = retry_refusal(r)
    return OutboxListResponse(
        counts=registry.outbox_counts(),
        rows=rows,
        max_attempts=registry.OUTBOX_MAX_ATTEMPTS,
    )


@router.post("/{row_id}/retry")
def retry_outbox_element(row_id: int, request: Request) -> dict:
    """Return one dead row to the queue.

    Only dead rows. A 409 rather than a silent no-op when the row is in any
    other state: "retry" on a row that is merely backing off would discard its
    backoff, and on a completed row would re-apply a write that already
    succeeded — both worth telling the caller about rather than absorbing.
    """
    admin_auth.require_admin(request)     # a retry re-queues an Egeria write: admin only
    registry = ProjectRegistry()
    existing = registry.get_outbox_element(row_id)
    if existing is not None and is_destructive_kind(existing.get("element_kind", "")):
        # Checked BEFORE the state change, never after: the row must stay exactly as it is.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Element {row_id} is a destructive write ({existing['element_kind']}): it archives, deletes or "
                "detaches in Egeria, so it is never retried from here. Press the original control again."
            ),
        )
    if existing is not None:
        from resource_explorer.egeria_outbox import retry_refusal

        why = retry_refusal(existing)
        if why:
            raise HTTPException(status_code=409, detail=f"Element {row_id} is not retried from here: {why}.")
    if registry.retry_outbox_element(row_id):
        return {"retried": True, "id": row_id}
    rows = registry.list_outbox_elements(limit=1000)
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No outbox element {row_id}")
    raise HTTPException(
        status_code=409,
        detail=(
            f"Element {row_id} is {row['status']}, not dead — only dead-lettered "
            f"elements can be re-queued. A 'failed' element is already scheduled "
            f"to retry at {row['next_attempt_at'] or 'its next backoff'}."
        ),
    )


@router.post("/purge")
def purge_completed(older_than_days: int = 14) -> dict:
    """Drop completed rows past the retention window.

    Only 'done' rows — superseded ones are kept as the record, dead ones still need a human and pending ones are live
    work. See `purge_outbox_completed`.
    """
    removed = ProjectRegistry().purge_outbox_completed(older_than_days=older_than_days)
    return {"removed": removed, "older_than_days": older_than_days}
