"""
Feedback API routes for Egeria Advisor.

Since Phase 2 (A-F1, 2026-11-11) feedback lives in the shared trellis-feedback store
(`advisor.feedback_store`), not in JSONL files under the data root.

Endpoints:
  POST /api/feedback            → record a 👍/😐/👎 vote as an `answer` item (honest status; see the route)
  GET  /api/feedback/analysis   → statistics + gap analysis, read from the store (admin only)
  /api/feedback/v2/...          → the library's own router (`trellis_feedback.router.make_router`):
                                  GET /items, GET /items/{id}, POST /triage, DELETE /items/{id} and
                                  GET /causes are admin-only; GET /meta and POST /items are open to
                                  whoever the login middleware lets through.

Retired, with the JSONL writes:
- `GET /api/feedback/extended`;
- `PATCH /api/feedback/extended/{idx}`, which edited a record by its line position and could land on
  the wrong one once a line was skipped.

Triage is now an append-only event on an item, by the item's id.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from starlette.concurrency import run_in_threadpool

from advisor.web.shared import FeedbackRequest

router = APIRouter()


def _voter_id(request: Request) -> Optional[str]:
    """The signed-in user's id, or None when anonymous: the same extraction as the app's
    user-context middleware (a claim from EA's own validated session JWT, not a client field)."""
    from advisor.auth import get_current_user
    user = get_current_user(request)
    if not user or user.get("anonymous"):
        return None
    return user.get("user_id") or user.get("sub") or None


def _record_vote(req: FeedbackRequest, user_id: Optional[str]) -> Dict[str, Any]:
    """The synchronous work of one vote; run in a worker thread, never on the event loop.
    Raises FeedbackWriteError when the library item was not written."""
    from advisor.feedback_collector import get_feedback_collector
    from advisor.feedback_store import RATING_BY_VOTE, vote_key
    if req.vote not in RATING_BY_VOTE:
        raise HTTPException(status_code=400, detail=f"vote must be one of {sorted(RATING_BY_VOTE)}, got {req.vote!r}")
    fc = get_feedback_collector()
    outcome = fc.record_vote(
        query=req.query,
        query_type=req.query_type,
        collections_searched=[],
        response_length=len(req.response_text or ""),
        rating=RATING_BY_VOTE[req.vote],
        session_id=req.session_id or None,
        perspective=req.perspective or None,
        routing_agent=req.routing_agent or None,
        # The intent selector is not something the user wrote: its own field, never user_comment.
        intent_override=req.intent_override or None,
        user_id=user_id,
        response_text=req.response_text or "",
        idempotency_key=vote_key(req.session_id, req.exchange_id, req.vote, req.vote_seq),
        exchange_id=req.exchange_id or None,
    )
    return {
        "status": "ok",
        "item": {"id": outcome.get("item_id")},
        "created": bool(outcome.get("created", True)),
        # MLflow is queued in the background and never reported here: its outcome is logged once.
        "secondary": {"query_metrics": outcome.get("query_metrics")},
    }


@router.post("/api/feedback")
async def record_feedback(req: FeedbackRequest, request: Request) -> Dict[str, Any]:
    """Record a 👍/😐/👎 vote as a trellis-feedback `answer` item.

    - The vote's idempotency key is session + exchange + vote. A retried send with the same key is the
      same vote: one item, `created` false.
    - The work runs in a worker thread, and MLflow is queued in the background and never awaited
      (Phase 0 S4).

    Honest status:
    - {"status": "ok", "item": {"id"}, "created", "secondary": {"query_metrics": ok|no_match|skipped|
      error|not_run}} only when the library item was written, or already held.
    - Otherwise HTTP 500 "could not record your feedback: <reason>".
    """
    from advisor.feedback_collector import FeedbackWriteError
    user_id = _voter_id(request)
    try:
        return await run_in_threadpool(_record_vote, req, user_id)
    except HTTPException:
        raise
    except FeedbackWriteError as exc:
        logger.error(f"Feedback not recorded: {exc}")
        raise HTTPException(status_code=500, detail=f"could not record your feedback: {exc}")
    except Exception as exc:  # the collector itself could not start
        logger.error(f"Feedback not recorded: {exc}")
        raise HTTPException(status_code=500,
                            detail=f"could not record your feedback: {type(exc).__name__}: {exc}")


@router.get("/api/feedback/analysis")
def feedback_analysis() -> Dict[str, Any]:
    """Feedback statistics plus gap analysis, read from the trellis-feedback store (admin only: the
    path is gated in advisor.web.app).

    {state, message, source, stats, gaps, improvements, count}:
    - `ok`: the votes were read.
    - `none`: the store was read and holds no votes yet.
    - `not_read`: the store could not be read, with `message` saying why. `stats` is then null, never
      zeros that would read as "no votes".
    """
    from advisor.feedback_collector import FeedbackReadError, get_feedback_collector
    fc = get_feedback_collector()
    source = "legacy file" if getattr(fc, "legacy_file", None) else "trellis-feedback"
    try:
        result = fc.analysis()
    except FeedbackReadError as exc:
        logger.warning(f"Feedback analysis not read: {exc}")
        return {"state": "not_read", "message": f"Feedback not read: {exc}", "source": source,
                "stats": None, "gaps": None, "improvements": None, "count": None}
    count = result.get("count", 0)
    return {
        "state": "ok" if count else "none",
        "message": None if count else "No votes have been recorded yet.",
        "source": source,
        **result,
    }


def _mount_library_router() -> None:
    """The library's own routes, at /api/feedback/v2. The store is opened on first use (LazyStore), so
    EA starts without Postgres; until it can be reached those routes answer 503 with the reason."""
    from trellis_feedback.router import make_router
    from advisor.auth import is_admin
    from advisor.feedback_store import LazyStore
    router.include_router(
        make_router(LazyStore(), is_admin=is_admin, current_user=lambda request: _voter_id(request) or ""),
        prefix="/api/feedback/v2",
    )


_mount_library_router()
