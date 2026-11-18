"""A FastAPI router over one app's FeedbackStore -- §4 item 5 of docs/trellis-feedback-extraction.md.

The library does not decide who is an admin or who is signed in: the app passes both in. Submitting
is open to any caller the app's own login policy lets through; reading, triage and deletion need
`is_admin` (Q3: only admins read other users' questions and answers, whatever the login policy).

Requires the `api` extra (fastapi). The store itself does not.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from trellis_feedback.models import (
    CAUSE_CHAIN,
    CAUSES,
    ITEM_SOURCE,
    KINDS,
    PRODUCT_CATEGORIES,
    STATUSES,
    UNDIAGNOSED,
    FeedbackError,
    FeedbackItem,
)
from trellis_feedback.store import FeedbackStore, NotFound

#: Takes ids in one external source, returns those whose rows still exist.
ExistsLookup = Callable[[list[str]], Iterable[str]]


class SubmitBody(BaseModel):
    kind: str
    idempotency_key: str | None = Field(default=None, max_length=128)
    session_id: str = ""
    vote: int | None = None
    query_text: str = ""
    response_text: str = ""
    intent_label: str = ""
    perspective: str = ""
    routing_agent: str = ""
    compile_id: str = ""
    entity_type: str = ""
    slug: str = ""
    category: str = ""
    page: str = ""
    element_guid: str = ""
    rating: int | None = None
    email: str = ""
    wants_response: bool = False
    consent_to_contact: bool = False
    build_version: str = ""
    user_agent: str = ""
    viewport: str = ""
    locale: str = ""
    message: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class TriageBody(BaseModel):
    ref_id: str
    status: str
    source: str = ITEM_SOURCE
    note: str = ""
    #: 0.3: a diagnosis of an answer item (closed-loop design §2); "" keeps the current one.
    cause: str = ""
    secondary_cause: str = ""


def make_router(
    store: FeedbackStore,
    *,
    is_admin: Callable[[Request], bool],
    current_user: Callable[[Request], str],
    external_lookup: dict[str, ExistsLookup] | None = None,
) -> APIRouter:
    """Routes for `store`, to be mounted by the app under a prefix of its choosing.

    `current_user(request)` returns the signed-in user's id, or "" when anonymous. It is the only
    source of identity: a client-sent user field is never read. Whether an anonymous caller may
    submit at all is the app's login middleware's decision, not this router's; one that gets
    through is stored with user "".

    `external_lookup` maps each of the store's declared external sources to an `ExistsLookup`.
    Without one, a source's rows cannot be triaged through these routes, and their "item gone"
    state cannot be computed.
    """
    lookups = dict(external_lookup or {})
    unknown = set(lookups) - set(store.external_sources)
    if unknown:
        raise FeedbackError(f"external_lookup names sources the store does not declare: {sorted(unknown)}")

    router = APIRouter()

    def _admin(request: Request) -> str:
        if not is_admin(request):
            raise HTTPException(status_code=403, detail="Admin only")
        return current_user(request) or ""

    def _lookup(source: str) -> ExistsLookup:
        fn = lookups.get(source)
        if fn is None:
            raise HTTPException(status_code=400, detail=f"source {source!r} has no lookup in this app")
        return fn

    @router.get("/meta")
    def meta() -> dict[str, Any]:
        """The vocabularies, so a UI need not hard-code them."""
        return {
            "app": store.app, "statuses": list(STATUSES), "kinds": list(KINDS),
            "categories": list(PRODUCT_CATEGORIES),
            "sources": [ITEM_SOURCE, *sorted(store.external_sources)],
            "causes": list(CAUSES), "cause_chain": list(CAUSE_CHAIN), "undiagnosed": UNDIAGNOSED,
        }

    @router.post("/items")
    def submit(body: SubmitBody, request: Request) -> dict[str, Any]:
        data = body.model_dump()
        key = data.pop("idempotency_key")
        item = FeedbackItem(app=store.app, user_id=current_user(request) or "", **data)
        try:
            stored, created = store.submit(item, idempotency_key=key)
        except FeedbackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not created:
            # A replay returns only the id. The store already matches keys per user, but every
            # anonymous caller is the user "", so echoing the stored item would hand one anonymous
            # caller another's question, answer and contact details (0.1 read-back P2).
            return {"item": {"id": stored.id}, "created": False}
        return {"item": stored.to_dict(), "created": True}

    @router.get("/items")
    def list_items(
        request: Request,
        kind: str | None = None, status: str | None = None, category: str | None = None,
        cause: str | None = None, since: str | None = None, until: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        _admin(request)
        filters = {"kind": kind or None, "status": status or None, "category": category or None,
                   "cause": cause or None, "since": since or None, "until": until or None}
        try:
            rows = store.list(**filters, limit=limit, offset=offset)
        except FeedbackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": rows, "total": store.count(), "matched": store.count_matching(**filters),
                "filtered": any(filters.values())}

    @router.get("/causes")
    def causes(request: Request, vote: int | None = None, since: str | None = None,
               until: str | None = None) -> dict[str, Any]:
        """Answer items by current cause, every cause listed (zeros included) plus undiagnosed."""
        _admin(request)
        return {"counts": store.cause_counts(vote=vote, since=since or None, until=until or None),
                "causes": list(CAUSES), "undiagnosed": UNDIAGNOSED}

    @router.get("/items/{item_id}")
    def get_item(item_id: str, request: Request) -> dict[str, Any]:
        _admin(request)
        item = store.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"no feedback item {item_id!r}")
        return {"item": item.to_dict(), "triage": store.state(item_id).to_dict(),
                "history": [e.to_dict() for e in store.history(item_id)]}

    @router.delete("/items/{item_id}")
    def delete_item(item_id: str, request: Request) -> dict[str, Any]:
        _admin(request)
        if not store.delete_item(item_id):
            raise HTTPException(status_code=404, detail=f"no feedback item {item_id!r}")
        return {"deleted": item_id}

    @router.post("/triage")
    def triage(body: TriageBody, request: Request) -> dict[str, Any]:
        actor = _admin(request)
        if body.source != ITEM_SOURCE:
            if body.source not in store.external_sources:
                raise HTTPException(status_code=400, detail=f"unknown source {body.source!r}")
            # A row that is gone keeps its history (Q7) but takes no new status: there is nothing
            # left to decide about.
            if str(body.ref_id) not in {str(x) for x in _lookup(body.source)([str(body.ref_id)])}:
                raise HTTPException(status_code=404, detail=f"{body.source} {body.ref_id!r} does not exist")
        try:
            event = store.triage(body.ref_id, body.status, source=body.source, note=body.note, actor=actor,
                                 cause=body.cause, secondary_cause=body.secondary_cause)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FeedbackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"event": event.to_dict(), "triage": store.state(body.ref_id, source=body.source).to_dict()}

    @router.get("/external/{source}")
    def external(source: str, request: Request, ids: str = "") -> dict[str, Any]:
        """Triage state for rows the app lists itself (`ids`, comma-separated), plus every
        referenced row of this source that no longer exists ("item gone", Q7)."""
        _admin(request)
        if source not in store.external_sources:
            raise HTTPException(status_code=400, detail=f"unknown source {source!r}")
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        gone = store.gone(source, _lookup(source))
        return {
            "source": source,
            "states": {k: v.to_dict() for k, v in store.states(wanted, source=source).items()},
            "gone": [{"ref_id": g, "triage": store.state(g, source=source).to_dict(),
                      "history": [e.to_dict() for e in store.history(g, source=source)]} for g in gone],
        }

    @router.delete("/external/{source}/{ref_id}")
    def delete_external(source: str, ref_id: str, request: Request) -> dict[str, Any]:
        """Clear the history of a gone row (Q7). A row that still exists keeps its history: deleting
        a live row's triage would silently reset it to `new`."""
        _admin(request)
        if source not in store.external_sources:
            raise HTTPException(status_code=400, detail=f"unknown source {source!r}")
        if str(ref_id) in {str(x) for x in _lookup(source)([str(ref_id)])}:
            raise HTTPException(status_code=409, detail=f"{source} {ref_id!r} still exists; its history stays")
        n = store.delete_events(ref_id, source=source)
        if not n:
            raise HTTPException(status_code=404, detail=f"no triage history for {source} {ref_id!r}")
        return {"deleted_events": n, "source": source, "ref_id": ref_id}

    return router
