"""Work lists and batch runs — the API half of the `/next` Scouting slice.

The one backend change the slice needed: **a batch enqueue** (a set of
resources plus an analysis, N rows in `runs`, a set id back) and **a read for
progress by set id**. Everything else in the slice is client work on top of
these.

Publishing to Egeria is its own endpoint and is never a side effect of
creating or editing a list — see `work_lists.WorkLists.publish`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from resource_explorer.work_lists import WorkLists

log = logging.getLogger(__name__)
router = APIRouter()


def _requested_by() -> str:
    """The signed-in caller, for run attribution and per-user fairness."""
    try:
        from resource_explorer.a2a_auth import caller

        identity = caller()
        return getattr(identity, "user_id", "") or ""
    except Exception:
        return ""


class WorkListCreate(BaseModel):
    display_name: str
    entity_slugs: list[str] = Field(default_factory=list)
    description: str = ""
    entity_type: str = "repo"
    investigation: str = ""
    rationale: str = ""


class MemberUpdate(BaseModel):
    entity_slug: str
    rationale: str = ""
    #: Published as CollectionMembership `confidence` (0-100). Taken as sent and checked by
    #: `WorkLists.set_member` with the outbox's own `membership_confidence`: one rule, a 422 with its words.
    confidence: Any = None


class LinkInvestigation(BaseModel):
    #: '' unlinks. Anything else must name an existing investigation.
    investigation: str = ""


class AddToInvestigation(BaseModel):
    investigation: str
    #: Omit for every member of the list; give a subset for the rows ticked.
    entity_slugs: list[str] | None = None


class PromoteRequest(BaseModel):
    survivors: list[str]
    display_name: str = ""
    rationale: str = ""


class BatchRunRequest(BaseModel):
    analysis_id: str
    #: Omit to run across the work list's own members. Given explicitly, this
    #: wins — the caller may be running over a filtered subset of the list.
    entity_slugs: list[str] | None = None
    work_list_slug: str = ""
    #: Resource type of `entity_slugs` ('repo' | 'database' | 'filesystem').
    #: Only consulted when `entity_slugs` is given explicitly — when the run
    #: is over `work_list_slug`'s own members instead, that work list's own
    #: `entity_type` column is authoritative and this is ignored, since a
    #: work list is homogeneous by construction (`WorkListCreate.entity_type`).
    #: Defaults to "repo" for existing callers, same as `WorkListCreate`.
    entity_type: str = "repo"


@router.get("/")
async def list_work_lists(investigation: str = "") -> list[dict]:
    return WorkLists().list_all(investigation=investigation)


@router.post("/")
async def create_work_list(body: WorkListCreate) -> dict:
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="display_name is required")
    return WorkLists().create(
        body.display_name.strip(), body.entity_slugs,
        description=body.description, entity_type=body.entity_type,
        investigation=body.investigation, created_by=_requested_by(),
        rationale=body.rationale,
    )


# ── Batch runs ──────────────────────────────────────────────────────────
#
# Declared BEFORE the `/{slug}` routes deliberately, matching the
# convention in `analyses.py`: a literal path segment must be registered
# ahead of a single-segment path parameter, or `/runs/...` is liable to
# be captured as a work-list slug the moment either route grows a shape
# that overlaps.


@router.post("/runs/batch")
async def enqueue_batch(body: BatchRunRequest) -> dict:
    """Run one analysis across a set of resources, concurrently.

    Returns a `set_id` to watch. One `runs` row per resource, so one
    resource failing is one row failing — the rest still run, which is the
    whole point of doing this as a set rather than a loop in the browser.
    """
    wls = WorkLists()
    slugs = body.entity_slugs
    entity_type = body.entity_type
    if slugs is None:
        if not body.work_list_slug:
            raise HTTPException(
                status_code=400,
                detail="give entity_slugs, or a work_list_slug to run across")
        wl = wls.get(body.work_list_slug)
        if not wl:
            raise HTTPException(
                status_code=404, detail=f"work list {body.work_list_slug!r} not found")
        slugs = [m["entity_slug"] for m in wl["members"]]
        # The work list's OWN entity_type is authoritative here — a work list
        # is homogeneous by construction, and `body.entity_type` was never
        # asked for when the caller is running over the list's own members
        # rather than an explicit `entity_slugs` subset.
        entity_type = wl["entity_type"]
    if not slugs:
        raise HTTPException(status_code=400, detail="nothing to run — the set is empty")

    # Validate the analysis BEFORE queueing anything, so an unknown id is one
    # 400 rather than N rows that each fail a minute later in another process.
    #
    # `entity_type` — resolved against THIS resource type's catalog and step
    # map, not always the repo's. Before this, a database/filesystem batch
    # validated (and, worse, resolved steps to run) against the repo catalog
    # regardless of what `entity_slugs` actually were.
    from resource_explorer.workflows.analysis import resolve_analysis_plan

    try:
        resolve_analysis_plan(body.analysis_id, entity_type)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"unknown analysis {body.analysis_id!r}: {exc}") from exc

    # Brief Z3: each member's Egeria write is checked as the caller (`egeria_work_access.batch_refusal`, the one
    # decision); a refused member gets no run and comes back in `not_permitted` with its words, the rest run.
    # Nothing permitted at all is a 403. Off the event loop: the check may read Egeria (bounded).
    import asyncio

    from resource_explorer.workflows.curate import CurationDenied
    from resource_explorer.workflows.egeria_work_access import batch_refusal
    from resource_explorer.zone_access import EgeriaAccessReader

    registry = wls.registry
    reader = EgeriaAccessReader()      # one batch: controls and accounts read once
    try:
        return await asyncio.to_thread(
            wls.enqueue_batch, body.analysis_id, slugs,
            work_list_slug=body.work_list_slug, requested_by=_requested_by(),
            entity_type=entity_type, refusal=lambda s: batch_refusal(registry, entity_type, s, reader=reader))
    except CurationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/runs/sets/{set_id}")
async def batch_progress(set_id: str) -> dict:
    """Which runs in this set are queued, running, done or failed.

    Derived from the `runs` rows on every read. A run row that has gone
    missing reports `unknown` rather than being counted as finished — an
    unobserved run is not a successful one.
    """
    progress = WorkLists().batch_progress(set_id)
    if progress is None:
        raise HTTPException(status_code=404, detail=f"run set {set_id!r} not found")
    return progress


@router.get("/{slug}")
async def get_work_list(slug: str) -> dict:
    wl = WorkLists().get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    return wl


@router.get("/{slug}/export.csv")
async def export_work_list(slug: str):
    """A work list's members as the shared CSV contract, named
    `re-work-list-<name>-<date>.csv`."""
    from fastapi.responses import Response

    from resource_explorer.batch_io import export_filename, rows_to_csv_text, work_list_export_rows

    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    name = export_filename("work-list", wl.get("display_name") or slug)
    return Response(content=rows_to_csv_text(work_list_export_rows(wls.registry, wl)),
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{slug}")
async def delete_work_list(slug: str) -> dict:
    """Remove the list locally. Any Egeria Collection it published stays."""
    wl = WorkLists().get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    WorkLists().delete(slug)
    return {"removed": slug,
            "egeria_collection_kept": bool(wl.get("egeria_guid")),
            "egeria_guid": wl.get("egeria_guid", "")}


@router.post("/{slug}/members")
async def set_member(slug: str, body: MemberUpdate) -> dict:
    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    try:
        wls.set_member(slug, body.entity_slug,
                       rationale=body.rationale, confidence=body.confidence)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return wls.get(slug)


@router.delete("/{slug}/members/{entity_slug}")
async def remove_member(slug: str, entity_slug: str) -> dict:
    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    wls.remove_member(slug, entity_slug)
    return wls.get(slug)


@router.put("/{slug}/investigation")
async def link_investigation(slug: str, body: LinkInvestigation) -> dict:
    """Tag a saved list with the investigation it belongs to (or untag it).

    `work_lists.investigation` was only ever set at save time, and only when an
    investigation happened to be current; nothing could set it afterwards.
    """
    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    if body.investigation and not wls.registry.get_investigation(body.investigation):
        raise HTTPException(status_code=404,
                            detail=f"Investigation {body.investigation!r} not found")
    wls.set_investigation(slug, body.investigation)
    return wls.get(slug)


@router.post("/{slug}/add-to-investigation")
async def add_to_investigation(slug: str, body: AddToInvestigation) -> dict:
    """Put this list's members (or the ticked subset) in an investigation's scope.

    Each member's `rationale` becomes its `membership_rationale`; a member with
    none gets "from work list <name>". A member already in scope is SKIPPED,
    not re-added, because re-adding overwrites the reason it was added for
    (and the registry call is `keep_existing` besides, a second guard against a
    race). Nothing is linked here: tagging the list is `PUT .../investigation`.
    """
    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    if not body.investigation:
        raise HTTPException(status_code=422, detail="an investigation is needed")
    reg = wls.registry
    inv = reg.get_investigation(body.investigation)
    if not inv:
        raise HTTPException(status_code=404,
                            detail=f"Investigation {body.investigation!r} not found")
    if inv.get("status") not in (None, "", "open"):
        raise HTTPException(
            status_code=409,
            detail=f"Investigation '{inv.get('display_name') or body.investigation}' "
                   f"is {inv.get('status')}, not open")
    by_slug = {m["entity_slug"]: m for m in wl["members"]}
    wanted = list(dict.fromkeys(body.entity_slugs)) if body.entity_slugs is not None \
        else list(by_slug)
    stray = [s for s in wanted if s not in by_slug]
    if stray:
        raise HTTPException(status_code=422,
                            detail=f"not members of this list: {', '.join(stray)}")
    kind = wl["entity_type"]
    in_scope = {(m["entity_type"], m["entity_slug"])
                for m in reg.list_investigation_members(body.investigation)}
    added: list[str] = []
    already: list[str] = []
    ws_slug = ""
    for s in wanted:
        if (kind, s) in in_scope:
            already.append(s)
            continue
        if not ws_slug:
            ws_slug = reg.get_or_create_working_set(body.investigation)["slug"]
        reason = (by_slug[s].get("rationale") or "").strip() \
            or f"from work list {wl['display_name']}"
        reg.add_working_set_member(ws_slug, kind, s, membership_rationale=reason,
                                   state="in-scope", keep_existing=True)
        added.append(s)
    return {"investigation": body.investigation,
            "investigation_name": inv.get("display_name") or body.investigation,
            "added": added, "already_in_scope": already}


@router.post("/{slug}/promote")
async def promote(slug: str, body: PromoteRequest) -> dict:
    """The survivors become a new, narrower list that records its parent."""
    try:
        return WorkLists().promote(
            slug, body.survivors, display_name=body.display_name,
            rationale=body.rationale, created_by=_requested_by())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")


@router.post("/{slug}/publish")
async def publish(slug: str) -> dict:
    """Publish to Egeria as a `WorkingSet` Collection.

    Explicit, never automatic. The Collection is created synchronously (its
    GUID is needed to attach anything); the memberships are queued to the
    outbox and applied by whichever worker drains it, each carrying its
    `membershipRationale`.

    Brief Z3: the collection and each member are checked as the caller first
    (`WorkLists.publish`); a refused member is in `not_permitted` with its
    words and the rest are queued; 403 when nothing is permitted.
    """
    import asyncio

    from resource_explorer.workflows.curate import CurationDenied

    try:
        return await asyncio.to_thread(WorkLists().publish, slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    except CurationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("publishing work list %s failed", slug)
        raise HTTPException(status_code=502,
                            detail=f"{type(exc).__name__}: {exc}") from exc
