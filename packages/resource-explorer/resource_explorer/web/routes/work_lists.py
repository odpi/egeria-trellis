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

from fastapi import APIRouter, HTTPException, Request
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


class MemberRef(BaseModel):
    entity_slug: str
    entity_type: str


class WorkListCreate(BaseModel):
    display_name: str
    entity_slugs: list[str] = Field(default_factory=list)
    #: Members that name their own kind (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §5): a list of more than one
    #: kind. Added after `entity_slugs`, which take `entity_type`.
    members: list[MemberRef] = Field(default_factory=list)
    description: str = ""
    entity_type: str = "repo"
    investigation: str = ""
    rationale: str = ""


class MemberUpdate(BaseModel):
    entity_slug: str
    #: The member's kind. Omit for a list of one kind (it takes the list's); required on a list of more than one.
    entity_type: str = ""
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
    #: The filter the survivors were cut on, with its three buckets (`winnowing.check_filter`). Omit for a
    #: hand-picked shortlist. Given, the survivors must be exactly its kept bucket (422 otherwise).
    filter: dict | None = None


class ReapplyRequest(BaseModel):
    #: The same criteria, evaluated on the parent's current headlines: the new buckets and as-of.
    filter: dict
    #: True when the bench re-applied on its own because an answer behind the filter refreshed; false for the
    #: "re-apply now" press. Recorded on the shortlist either way.
    automatic: bool = False


class PerKindRunRequest(BaseModel):
    #: The analysis to run on each kind's members, by kind. A kind present with none is reported in `not_run`.
    analyses: dict[str, str]
    #: Omit for every member; give a subset (members by slug and kind) for the rows ticked.
    members: list[MemberRef] | None = None


class BatchRunRequest(BaseModel):
    analysis_id: str
    #: Omit to run across the work list's own members. Given explicitly, this
    #: wins — the caller may be running over a filtered subset of the list.
    entity_slugs: list[str] | None = None
    work_list_slug: str = ""
    #: Resource type of `entity_slugs` ('repo' | 'database' | 'filesystem').
    #: Only consulted when `entity_slugs` is given explicitly — when the run
    #: is over `work_list_slug`'s own members instead, the list's kind is
    #: used, and a list of more than one kind is refused here (one analysis
    #: is one kind's): it runs per kind, `POST /{slug}/runs/per-kind`.
    #: Defaults to "repo" for existing callers, same as `WorkListCreate`.
    entity_type: str = "repo"


@router.get("/")
async def list_work_lists(investigation: str = "") -> list[dict]:
    return WorkLists().list_all(investigation=investigation)


@router.post("/")
async def create_work_list(body: WorkListCreate) -> dict:
    from resource_explorer.member_assets import MEMBER_ENTITY_TYPES

    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="display_name is required")
    stray = sorted({m.entity_type for m in body.members} - set(MEMBER_ENTITY_TYPES))
    if stray:
        raise HTTPException(status_code=422, detail=f"not a kind a work list holds: {', '.join(stray)}")
    try:
        return WorkLists().create(
            body.display_name.strip(), [*body.entity_slugs, *(m.model_dump() for m in body.members)],
            description=body.description, entity_type=body.entity_type,
            investigation=body.investigation, created_by=_requested_by(),
            rationale=body.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


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
        # The work list's OWN kind is authoritative here, and `body.entity_type`
        # was never asked for when the caller is running over the list's own
        # members. One analysis is one kind's, so a list of more than one kind
        # is refused with where to go instead, never run as one kind.
        entity_type = wl["entity_type"]
        if entity_type == "mixed":
            raise HTTPException(
                status_code=422,
                detail="this list holds more than one kind; one analysis runs on one kind's members, so run it "
                       f"per kind (POST /api/work-lists/{body.work_list_slug}/runs/per-kind)")
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


@router.post("/{slug}/runs/per-kind")
async def enqueue_per_kind(slug: str, body: PerKindRunRequest) -> dict:
    """Run a list of more than one kind: one batch per kind present, each its own analysis (the definition catalog
    is per kind) and its own set id, so the bench shows one run line per kind. A kind with no analysis chosen is
    reported in `not_run`; a kind refused by zone access comes back as a `refused` line while the others run.
    Every analysis is checked against its kind's catalog before anything is queued (400 naming the kind)."""
    import asyncio

    from resource_explorer.workflows.analysis import resolve_analysis_plan
    from resource_explorer.workflows.curate import CurationDenied
    from resource_explorer.workflows.egeria_work_access import batch_refusal
    from resource_explorer.zone_access import EgeriaAccessReader

    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    held = [(m["entity_type"], m["entity_slug"]) for m in wl["members"]]
    if body.members is not None:
        wanted = {(m.entity_type, m.entity_slug) for m in body.members}
        stray = sorted(f"{k} {s}" for k, s in wanted - set(held))
        if stray:
            raise HTTPException(status_code=422, detail=f"not members of this list: {', '.join(stray)}")
        held = [h for h in held if h in wanted]
    if not held:
        raise HTTPException(status_code=400, detail="nothing to run — the set is empty")
    present = {k for k, _ in held}
    for kind, analysis_id in body.analyses.items():
        if kind not in present or not analysis_id:
            continue
        try:
            resolve_analysis_plan(analysis_id, kind)
        except Exception as exc:
            raise HTTPException(status_code=400,
                                detail=f"unknown analysis {analysis_id!r} for {kind}: {exc}") from exc
    registry = wls.registry
    reader = EgeriaAccessReader()
    try:
        return await asyncio.to_thread(
            wls.enqueue_per_kind, body.analyses, held, work_list_slug=slug, requested_by=_requested_by(),
            refusal_for=lambda kind: (lambda s: batch_refusal(registry, kind, s, reader=reader)))
    except CurationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/column-types")
async def column_types(entity_type: str, analysis_ids: str = "") -> dict:
    """What each analysis behind the bench's columns can be filtered by: its declared measures and its absence
    sentences (`winnowing.column_types`). Declared BEFORE `/{slug}`, for the reason given above."""
    from resource_explorer.member_assets import MEMBER_ENTITY_TYPES
    from resource_explorer.winnowing import column_types as _column_types

    if entity_type not in MEMBER_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"{entity_type!r} is not a kind a work list holds")
    return _column_types(entity_type, [a.strip() for a in analysis_ids.split(",") if a.strip()])


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


@router.get("/{slug}/set-steps")
def list_set_steps(slug: str) -> dict:
    """The steps this list's page offers that take the whole list as their subject, each with its priced line (the sum of
    the members'). Reads RE's own stored rows only: nothing is run and nothing touches the resources."""
    from resource_explorer.surveyors.survey_step_host import set_step_offers

    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    return {"work_list": slug, "members": len(wl["members"]), "offers": set_step_offers(wls.registry, wl)}


@router.post("/{slug}/set-steps/{step}/run")
def run_set_step_route(slug: str, step: str) -> dict:
    """Run one set step over the list's members as they are now: one producing run, a result per member, one roll-up. Egeria
    dispatch of set steps is out of scope; this runs in RE and stores in RE's registry."""
    from resource_explorer.surveyors.survey_step_host import discover_set_steps, run_set_step, set_members

    who = _requested_by()
    if not who:
        raise HTTPException(status_code=401, detail="sign in to run a set step: it runs as you")
    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    members = set_members(wl)
    if not members:
        raise HTTPException(status_code=400, detail="nothing to run — the list has no members")
    manifest = next((m for n, m in {k: v for t, _ in members for k, v in discover_set_steps(t).items()}.items()
                     if n == step), None)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"no set step {step!r} is installed for this list's members")
    try:
        return {**run_set_step(manifest, wls.registry, slug, members), "requested_by": who}
    except Exception as exc:  # noqa: BLE001 - the step's refusal is the answer, in its words
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


class HiddenBody(BaseModel):
    hidden: bool


@router.put("/{slug}/hidden")
async def set_work_list_hidden(slug: str, body: HiddenBody) -> dict:
    """Hide or unhide the list for the signed-in person only (W38). Anyone may, for themselves."""
    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    wls.set_hidden(slug, body.hidden)
    return {"slug": slug, "hidden": body.hidden}


SUGGESTION_PREFIX = "suggested-to-"


def _may_delete(request: Request, wl: dict, caller: str) -> bool:
    """The list's creator, an admin, or (for a `suggested-to-<person>` pointer) the person it was suggested to."""
    from resource_explorer.config import get_config
    from resource_explorer.web.admin_auth import is_admin_request

    if is_admin_request(request, get_config().feedback):
        return True
    if not caller:
        return False
    if caller == (wl.get("created_by") or ""):
        return True
    slug = str(wl.get("slug") or "")
    return slug.startswith(SUGGESTION_PREFIX) and slug[len(SUGGESTION_PREFIX):] == caller


@router.delete("/{slug}")
async def delete_work_list(slug: str, request: Request) -> dict:
    """Remove the list locally. Any Egeria Collection it published stays.

    Only the list's creator, an admin, or (for a suggestion) the person it was suggested to may delete it; anyone
    else may hide it for themselves (`PUT /{slug}/hidden`). One Activity entry says who deleted what."""
    wls = WorkLists()
    wl = wls.get(slug)
    if not wl:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    caller = _requested_by()
    if not _may_delete(request, wl, caller):
        raise HTTPException(
            status_code=403,
            detail=("Only the person who created this work list"
                    f"{' (' + wl['created_by'] + ')' if wl.get('created_by') else ''}, or an admin, can delete it. "
                    "You can hide it for yourself instead."))
    wls.delete(slug)
    name = wl.get("display_name") or slug
    activity_error = ""
    try:
        from resource_explorer.activity_logger import log_catalog

        log_catalog(wls.registry, "work_list", slug, name, "", "ok",
                    f"work list '{name}' deleted by {caller or 'an admin'}",
                    detail=f"Removed locally. {'The Egeria Collection ' + wl['egeria_guid'] + ' stays in Egeria.' if wl.get('egeria_guid') else 'It was never published to Egeria.'}")
    except Exception as exc:  # noqa: BLE001 -- the delete is done; the failure is returned, not hidden
        log.warning("could not write the activity entry for deleting work list %s: %s", slug, exc)
        activity_error = f"{type(exc).__name__}: {exc}"
    return {"removed": slug,
            "egeria_collection_kept": bool(wl.get("egeria_guid")),
            "egeria_guid": wl.get("egeria_guid", ""),
            "deleted_by": caller,
            "activity_logged": not activity_error, "activity_error": activity_error}


@router.post("/{slug}/members")
async def set_member(slug: str, body: MemberUpdate) -> dict:
    from resource_explorer.member_assets import MEMBER_ENTITY_TYPES

    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    if body.entity_type and body.entity_type not in MEMBER_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"{body.entity_type!r} is not a kind a work list holds")
    try:
        wls.set_member(slug, body.entity_slug, entity_type=body.entity_type,
                       rationale=body.rationale, confidence=body.confidence)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return wls.get(slug)


@router.delete("/{slug}/members/{entity_slug}")
async def remove_member(slug: str, entity_slug: str, entity_type: str = "") -> dict:
    """Remove one member. `entity_type` is needed only when two kinds in the list share the slug (422 otherwise)."""
    wls = WorkLists()
    if not wls.get(slug):
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    try:
        wls.remove_member(slug, entity_slug, entity_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
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
    # By slug, each with every kind it is in the list as (a repository and a database may share a slug).
    by_slug: dict[str, list[dict]] = {}
    for m in wl["members"]:
        by_slug.setdefault(m["entity_slug"], []).append(m)
    wanted = list(dict.fromkeys(body.entity_slugs)) if body.entity_slugs is not None \
        else list(by_slug)
    stray = [s for s in wanted if s not in by_slug]
    if stray:
        raise HTTPException(status_code=422,
                            detail=f"not members of this list: {', '.join(stray)}")
    in_scope = {(m["entity_type"], m["entity_slug"])
                for m in reg.list_investigation_members(body.investigation)}
    added: list[str] = []
    already: list[str] = []
    ws_slug = ""
    for s in wanted:
        for member in by_slug[s]:
            kind = member["entity_type"]      # each member as its own kind (§5)
            if (kind, s) in in_scope:
                already.append(s)
                continue
            if not ws_slug:
                ws_slug = reg.get_or_create_working_set(body.investigation)["slug"]
            reason = (member.get("rationale") or "").strip() \
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
            rationale=body.rationale, created_by=_requested_by(), filter=body.filter)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/{slug}/reapply")
async def reapply(slug: str, body: ReapplyRequest) -> dict:
    """Re-cut a filtered shortlist on fresher answers: automatically when an answer behind its filter refreshed
    (`automatic`), or on the "re-apply now" press. Its members become the new kept bucket, plus any member now
    could-not-check, which is held; who joined, dropped and was held come back by name, and the re-apply is
    recorded on the shortlist."""
    try:
        return WorkLists().reapply(slug, body.filter, automatic=body.automatic)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"work list {slug!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


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
