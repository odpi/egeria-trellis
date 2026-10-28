"""Investigations — the framing step ahead of Scouting.

`docs/investigation-framing-design.md` §1: an Investigation is one body of work,
"the thing the new tab creates and the context everything else runs inside."

Deliberately NOT a ninth intent. CLAUDE.md rule 17 fixes the eight canonical
intent labels, and this is not another kind of work done *to* a resource — it is
the frame the other eight run inside. It therefore sits at header level with
Activity and Admin, not in `#intent-nav`.

Local-first: an investigation exists with a nullable `egeria_project_guid`, so
all three of §1's starting modes (bind an existing Egeria Project, create a new
one, or stay purely local) produce ONE local row. Promotion is a fill-in, not a
migration.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()  # prefix/tags applied in web/app.py, matching every other route module


class InvestigationCreate(BaseModel):
    display_name: str
    description: str = ""
    #: Suggestive (owner ruling 2026-10-13): a value outside Egeria's purposes list is kept, and the answer's
    #: `purpose_warnings` names it.
    purposes: list[str] = Field(default_factory=list)
    #: Free text as entered, kept apart from `purposes` so it can be mined.
    purpose_text: str = ""
    #: Egeria's own Project classification. Five values; see
    #: `ProjectRegistry.PROJECT_CLASSIFICATIONS`.
    project_classification: str = "StudyProject"
    #: The SECOND axis — `local` (ad-hoc: no Egeria Project, by choice) or
    #: `egeria`. Deliberately not a sixth classification: ad-hoc is the absence
    #: of a Project, not a kind of one, and Egeria has no such type to send.
    egeria_binding: str = "egeria"
    #: Required for `Experiment`, refused for everything else.
    hypothesis: str = ""
    egeria_project_guid: str = ""
    egeria_project_qualified_name: str = ""


class InvestigationUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    purposes: list[str] | None = None
    purpose_text: str | None = None
    #: Sharpening an Experiment's hypothesis stays within one classification.
    #: Changing the classification itself is not here — it can move an
    #: investigation between visibility regimes and needs a flow with a report
    #: rather than a PATCH (design §5).
    hypothesis: str | None = None


class ReclassifyRequest(BaseModel):
    project_classification: str
    #: Required when moving TO Experiment and it has none already.
    hypothesis: str = ""


class EgeriaProjectBinding(BaseModel):
    """The same context shape the publish path already speaks, so folding the
    session-wide control into the investigation teaches nothing downstream a
    new vocabulary."""
    status: str = "unset"
    egeria_project_guid: str = ""
    egeria_project_qualified_name: str = ""
    free_text_name: str = ""


class MemberAdd(BaseModel):
    entity_type: str
    entity_slug: str
    membership_rationale: str = ""
    state: str = "in-scope"


def _registry():
    from resource_explorer.registry import ProjectRegistry
    return ProjectRegistry()


async def _require_writes(reg, slug: str, writes) -> None:
    """Brief Z3: 403 with the reason unless the caller may make every Egeria write a route is about to make
    (`workflows.egeria_work_access.writes_access` -> `curation_access`, the one decision). Off the event loop:
    the check may read Egeria (bounded). Nothing to write to Egeria: nothing to check."""
    import asyncio

    from resource_explorer.workflows.egeria_work_access import writes_access

    writes = list(writes)
    if not writes:
        return
    d = await asyncio.to_thread(writes_access, reg, writes, entity_type="investigation", slug=slug)
    if not d.allowed:
        raise HTTPException(status_code=403, detail=d.reason)


def _nothing_permitted(res) -> str:
    """Brief Z3: the 403 words when a members pass permitted nothing: at least one member refused, none linked,
    and no other member even tried (a member with no asset is a fact, not a permitted write). "" otherwise."""
    refused = [m for m in res.members_unlinkable if str(m.get("reason", "")).startswith("not permitted")]
    tried = [m for m in res.members_unlinkable
             if m not in refused and not str(m.get("reason", "")).startswith("not published")]
    if not refused or res.members_linked or tried:
        return ""
    reasons = sorted({str(m["reason"]).removeprefix("not permitted · ") for m in refused})
    return reasons[0] if len(reasons) == 1 else "; ".join(reasons)


@router.get("/purposes")
async def list_purposes() -> dict:
    """The purposes list, served rather than hardcoded in the SPA.

    Egeria holds it (owner ruling 2026-10-13): read as the signed-in person
    through `investigation_purposes.current_purposes`, which falls back to RE's
    cached copy, and then to the shipped seed, when Egeria cannot be read. The
    answer says which (`source`, `as_of`, `stale`, `words`), so the screen can
    say it is a cache and how old. `purposes` stays the plain list of names;
    `values` carries each one's description. Suggestive, never a constraint.
    """
    import asyncio

    from resource_explorer import investigation_purposes as ip

    reg = _registry()
    pl = await asyncio.to_thread(ip.current_purposes, reg, ip.fetch_as_caller)
    return pl.as_dict()


@router.get("/purposes/free-text")
async def list_purpose_free_text() -> dict:
    """Purposes people wrote rather than picked, with counts, for mining
    (owner ruling 2026-10-13): investigations' free text and values outside the
    list, and Enrichment's Intended use free text. Read-only; nothing here adds
    to the list — a person adds a value to Egeria's list."""
    import asyncio

    reg = _registry()
    return await asyncio.to_thread(reg.purpose_free_text)


#: Egeria's own definitions, verbatim from `OpenMetadataType.java` (model
#: 0130). Kept as prose here rather than in the SPA for the same reason
#: `/purposes` exists: a second copy in the frontend is the mirror that has
#: drifted twice in this codebase already. The `label` is the short form for a
#: dropdown; the `description` is Egeria's, so the two cannot disagree.
_CLASSIFICATION_HELP = {
    "StudyProject": ("Study",
                     "A focused analysis of a topic, person, object, or situation."),
    "Task": ("Task",
             "A self-contained, short activity, typically for one or two people."),
    "Campaign": ("Campaign",
                 "A long-term strategic initiative that is implemented through "
                 "multiple related projects."),
    "PersonalProject": ("Personal",
                        "An informal project created by an individual to help them "
                        "organize their own work."),
    "Experiment": ("Experiment",
                   "A project testing a hypothesis, which is recorded on the "
                   "project itself."),
}


@router.get("/classifications")
async def list_classifications() -> dict:
    """The Project-classification vocabulary, and the separate binding axis.

    Two axes, deliberately, though the UI shows them as one control:

    * `classifications` — Egeria's own `ProjectKind` subtypes, sent as
      `initialClassifications` when the investigation is promoted. A value
      Egeria does not know is rejected at that boundary, which is why this list
      mirrors `OpenMetadataType.java` rather than being RE's own vocabulary.
    * `bindings` — whether there is an Egeria Project at all. "Ad-hoc" lives
      here, NOT as a sixth classification, because it is the *absence* of a
      Project rather than a kind of one. Egeria has no `adHoc` type to send.

    Served rather than hardcoded in the SPA for the same reason `/purposes` is:
    the frontend copy is what drifts.
    """
    from resource_explorer.registry import ProjectRegistry

    return {
        "classifications": [
            {
                "name": name,
                "label": _CLASSIFICATION_HELP.get(name, (name, ""))[0],
                "description": _CLASSIFICATION_HELP.get(name, (name, ""))[1],
                "requires_hypothesis": name in ProjectRegistry.HYPOTHESIS_REQUIRED_FOR,
            }
            for name in ProjectRegistry.PROJECT_CLASSIFICATIONS
        ],
        "bindings": [
            {"name": ProjectRegistry.BINDING_EGERIA,
             "label": "Create in Egeria",
             "description": "Gets an Egeria Project, so it can be shared, zoned "
                            "and found by others."},
            {"name": ProjectRegistry.BINDING_LOCAL,
             "label": "Ad-hoc",
             "description": "Stays local. Nothing is written to Egeria — for "
                            "looking at something without committing to it. "
                            "Promotable later."},
        ],
        "default_classification": "StudyProject",
        "default_binding": ProjectRegistry.BINDING_EGERIA,
    }


@router.get("/")
async def list_investigations(include_closed: bool = False) -> list[dict]:
    return _registry().list_investigations(include_closed=include_closed)


@router.post("/")
async def create_investigation(req: InvestigationCreate) -> dict:
    if not req.display_name.strip():
        raise HTTPException(status_code=400, detail="display_name is required")
    try:
        return _registry().create_investigation(
            req.display_name.strip(), description=req.description,
            purposes=req.purposes,
            purpose_text=req.purpose_text,
            project_classification=req.project_classification,
            egeria_binding=req.egeria_binding,
            hypothesis=req.hypothesis,
            egeria_project_guid=req.egeria_project_guid,
            egeria_project_qualified_name=req.egeria_project_qualified_name,
        )
    except ValueError as exc:
        # A classification or binding violation is a 400, not a silent drop: an
        # unrecognised classification would be rejected at the Egeria boundary
        # much later, and an Experiment with no hypothesis would publish the
        # classification with its point missing. Purposes are NOT here: the list
        # is suggestive (owner ruling 2026-10-13), so an unknown purpose is kept
        # and named in `purpose_warnings` instead.
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{slug}")
async def get_investigation(slug: str) -> dict:
    inv = _registry().get_investigation(slug)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return inv


@router.get("/{slug}/members")
async def get_members(slug: str) -> list[dict]:
    """What is in scope for this investigation.

    Flattened deliberately: storage is the two-hop Egeria shape (Project
    --ResourceList--> WorkingSet --CollectionMembership--> resource), but every
    caller only wants "what is in scope", so they should not have to walk it.
    """
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return reg.list_investigation_members(slug)


@router.get("/{slug}/scope.csv")
async def export_scope(slug: str):
    """The investigation's in-scope members as the shared CSV contract, named
    `re-scope-<investigation>-<date>.csv`. Importing it into a second
    investigation adds the resources; `status_in_scope` and `status_fit` are
    words for a reader and are never read back."""
    from fastapi.responses import Response

    from resource_explorer.batch_io import export_filename, rows_to_csv_text, scope_export_rows

    reg = _registry()
    inv = reg.get_investigation(slug)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    name = export_filename("scope", inv.get("slug") or slug)
    return Response(content=rows_to_csv_text(scope_export_rows(reg, slug)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/{slug}/members")
async def add_member(slug: str, req: MemberAdd) -> list[dict]:
    """Add one resource to this investigation's working set.

    The working set is created lazily here rather than at investigation
    creation, so a brand-new investigation genuinely has none — which is what
    makes the empty sidebar an honest prompt to go and scout rather than an
    empty shell that looks broken.
    """
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    ws = reg.get_or_create_working_set(slug)
    reg.add_working_set_member(
        ws["slug"], req.entity_type, req.entity_slug,
        membership_rationale=req.membership_rationale, state=req.state,
    )
    return reg.list_investigation_members(slug)


@router.delete("/{slug}/members/{entity_type}/{entity_slug}")
async def remove_member(slug: str, entity_type: str, entity_slug: str) -> list[dict]:
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    ws_slug = reg.investigation_working_set_slug(slug)
    if ws_slug:
        reg.remove_working_set_member(ws_slug, entity_type, entity_slug)
    return reg.list_investigation_members(slug)


@router.post("/{slug}/close")
async def close_investigation(slug: str) -> dict:
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return reg.close_investigation(slug)


@router.post("/{slug}/suspend")
async def suspend_investigation(slug: str) -> dict:
    """Pause without unbinding — see INVESTIGATION_STATUSES for why this is a
    different word from `closed` rather than the same action under a nicer name."""
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return reg.set_investigation_status(slug, "suspended")


@router.post("/{slug}/reopen")
async def reopen_investigation(slug: str) -> dict:
    """The route that did not exist: `close` had no way back except a direct
    SQL update, which is what turned one accidental click into a data-recovery
    incident. Works from either `closed` or `suspended` — both land on `open`."""
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return reg.set_investigation_status(slug, "open")


@router.put("/{slug}/egeria-project")
async def bind_egeria_project(slug: str, req: EgeriaProjectBinding) -> dict:
    """Bind (or clear) this investigation's Egeria Project.

    §1 makes binding to an Egeria Project one of the three ways to START an
    investigation — so it belongs here rather than as a parallel session-wide
    setting. Two controls answering "which project does this work belong to"
    is two answers to one question.
    """
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    # Brief Z3: the working set this may create under the Project (CREATE on the Project's zones, ATTACH), checked
    # before the binding is recorded so a refusal changes nothing.
    from resource_explorer.workflows.egeria_work_access import bind_writes

    await _require_writes(reg, slug, bind_writes(reg, slug, req.status, req.egeria_project_guid))
    result = reg.set_investigation_egeria_project(slug, req.model_dump())

    # Binding to an existing Project is one of the two ways to end up with one,
    # and both need the same thing next: somewhere in Egeria for the membership
    # to live. Without this, binding produced a Project with no working-set
    # Collection and the membership had nowhere to go — a gap promotion did not
    # have, purely because it happened to create both.
    #
    # Best-effort and off the event loop: a working set that cannot be created
    # right now must not fail the binding itself, which is a local decision and
    # already valid. ensure_working_set is idempotent, so the next attempt
    # completes it rather than duplicating.
    if req.status == "linked" and req.egeria_project_guid:
        import asyncio

        from resource_explorer.surveyors.egeria_investigation_publisher import (
            EgeriaInvestigationPublisher,
        )

        ws = await asyncio.to_thread(
            EgeriaInvestigationPublisher(reg).ensure_working_set, slug
        )
        result["working_set"] = {
            "collection_guid": ws.collection_guid,
            "resource_list_linked": ws.resource_list_linked,
            "errors": ws.errors,
        }
    return result


@router.post("/{slug}/promote")
async def promote_to_egeria(slug: str) -> dict:
    """Create the Egeria side of this investigation — Project, working-set
    Collection, ResourceList and CollectionMembership.

    The local record was shaped for this from the start, so it is a replay
    rather than a migration. Partial success is a real outcome and is reported
    as such: a member whose repo has never been published has no asset GUID to
    link, and inventing one would be worse than saying so.
    """
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = _registry()
    inv = reg.get_investigation(slug)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")

    # Brief Z3: the new Project's writes (CREATE, CLASSIFY, ATTACH; PUBLISH when private) as the caller, before
    # anything is written; each member's end is checked one by one inside promote (a refused member is
    # reported with its words, the rest are linked). Not asked when promote would refuse anyway (already bound,
    # or ad-hoc): it says why itself.
    from resource_explorer.surveyors.egeria_investigation_publisher import ownerless_private_words
    from resource_explorer.workflows.egeria_work_access import investigation_member_refusal, promote_writes

    # An ownerless private one is refused by promote itself, with its words and its proof row (ruling
    # 2026-10-13); the access check would judge an unzoned create that will never be sent.
    if (not inv.get("egeria_project_guid") and (inv.get("egeria_binding") or "egeria") != "local"
            and not ownerless_private_words(inv)):
        await _require_writes(reg, slug, promote_writes(inv))

    # In a thread, not inline. pyegeria's synchronous methods drive their own
    # event loop internally, so calling them from inside FastAPI's running loop
    # raises "this event loop is already running" — which a CLI test never sees,
    # because there is no loop running there. Same asyncio.to_thread treatment
    # every other long-running route here already uses (run_survey,
    # refresh_project).
    import asyncio

    result = await asyncio.to_thread(EgeriaInvestigationPublisher(reg).promote, slug,
                                     refusal=investigation_member_refusal(reg, slug))
    if result.project_guid:
        # Record the binding even on partial success — the Project genuinely
        # exists now, and leaving the local row unbound would orphan it.
        reg.set_investigation_egeria_project(slug, {
            "status": "linked",
            "egeria_project_guid": result.project_guid,
            "egeria_project_qualified_name": result.project_qualified_name,
        })
    return result.as_dict()


@router.patch("/{slug}")
async def update_investigation(slug: str, req: InvestigationUpdate) -> dict:
    """Rename or re-describe. The slug is an identifier and never changes —
    members, the Egeria binding and inherited context rows all reference it."""
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    try:
        return reg.update_investigation(
            slug, display_name=req.display_name, description=req.description,
            purposes=req.purposes, purpose_text=req.purpose_text, hypothesis=req.hypothesis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{slug}/reclassify")
async def reclassify_investigation(slug: str, req: ReclassifyRequest) -> dict:
    """Change the classification, moving the Egeria elements with it.

    The owner's point 7. Not a PATCH, because visibility follows the
    classification: this can move an investigation between visibility regimes,
    which is several Egeria writes that fail independently, so it answers with a
    per-element report rather than the updated row.

    Read `still_public` before `ok`. On a tightening it names elements that
    should be private and are not — the one outcome a person must not miss, and
    the one that RE's own screens cannot show them, because Phase 3's filter is
    local and will happily render the investigation as private regardless.
    """
    from resource_explorer.surveyors.investigation_reclassifier import (
        InvestigationReclassifier,
    )

    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")

    # In a thread for the same reason promote is: pyegeria's synchronous methods
    # drive their own event loop and raise inside a running one. `asyncio.to_thread`
    # copies the context, so `current_user_id()` still resolves — measured
    # 2026-09-08, and a raw thread would NOT (see test_private_zoning).
    import asyncio

    # Brief Z3: every element the reclassify moves is checked as the caller before it moves (the Project:
    # PUBLISH, CLASSIFY, DECLASSIFY; each SurveyReport: PUBLISH), inside the reclassifier so the check and
    # the write read the same list. A refused Project is a 403 and nothing changes.
    from resource_explorer.workflows.egeria_work_access import access_words, investigation_reader

    # One press: controls and accounts read once, elements fresh per check; a private investigation's elements
    # are read as its signed-in owner (the service account cannot see them).
    from resource_explorer.zone_access import AccessUnreadable

    try:
        reader = investigation_reader(reg, slug)
    except AccessUnreadable as exc:
        raise HTTPException(status_code=403, detail=f"could not check access in Egeria ({exc})")
    result = await asyncio.to_thread(
        InvestigationReclassifier(reg).reclassify, slug,
        req.project_classification, req.hypothesis,
        access=lambda writes: access_words(reg, writes, entity_type="investigation", slug=slug, reader=reader),
    )
    if result.not_permitted:
        raise HTTPException(status_code=403, detail=result.not_permitted.removeprefix("not permitted · "))
    return result.as_dict()


@router.post("/{slug}/relink-members")
async def relink_members(slug: str) -> dict:
    """Re-attach this investigation's in-scope resources to its Egeria working set.

    promote() links members once and then refuses to run again, so members that
    had no asset GUID at promotion time — all of them, if the investigation was
    promoted before its repos were published — had no way to be linked
    afterwards. Idempotent: CollectionMembership is uni-link, so re-attaching an
    already-attached member is an upsert.
    """
    import asyncio

    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    # Brief Z3: the Folio's end (ATTACH, judged on its Project) once, then each member's end inside relink; a
    # refused member is reported with its words and the rest are linked; 403 when nothing is permitted.
    from resource_explorer.workflows.egeria_work_access import investigation_member_refusal, relink_writes

    await _require_writes(reg, slug, relink_writes(reg, slug))
    res = await asyncio.to_thread(
        EgeriaInvestigationPublisher(reg).relink_members, slug,
        refusal=investigation_member_refusal(reg, slug),
    )
    refused = _nothing_permitted(res)
    if refused:
        raise HTTPException(status_code=403, detail=refused)
    return res.as_dict()


@router.get("/{slug}/link-status")
async def link_status(slug: str) -> dict:
    """Which members are linked to the investigation's Egeria working set.

    Read-only, no Egeria call: derived from the same asset GUIDs and outbox rows
    `relink-members` decides and records from. Per member `state` is `linked`,
    `not_linked` (with a reason) or `could_not_tell`; a caller must not turn
    `could_not_tell` into a zero.
    """
    import asyncio

    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return await asyncio.to_thread(EgeriaInvestigationPublisher(reg).link_status, slug)


@router.post("/{slug}/sync-egeria")
async def sync_egeria_project(slug: str) -> dict:
    """Push this investigation's name/description to its Egeria Project.

    Separate from PATCH deliberately: renaming is a local edit and must not fail
    because Egeria is unreachable. But leaving the two permanently disagreeing
    about the same body of work is the drift this frame exists to prevent, so
    the sync is offered explicitly rather than skipped.
    """
    import asyncio

    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = _registry()
    inv = reg.get_investigation(slug)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    # Brief Z3: UPDATE_PROPERTIES on the bound Project, as the caller.
    from resource_explorer.workflows.egeria_work_access import sync_writes

    await _require_writes(reg, slug, sync_writes(inv))
    res = await asyncio.to_thread(
        EgeriaInvestigationPublisher(reg).sync_project_properties, slug
    )
    return res.as_dict()


@router.get("/{slug}/dispositions")
async def get_dispositions(slug: str) -> dict:
    """{disposition: [members]} for this investigation.

    Derived from WorkingSet membership rather than stored separately — a
    resource's disposition within an investigation IS which WorkingSet it sits
    in, so there is only one place it can disagree with itself: none.
    """
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    return reg.investigation_dispositions(slug)


@router.post("/{slug}/dispositions/{entity_type}/{entity_slug}")
async def set_disposition(slug: str, entity_type: str, entity_slug: str,
                          disposition: str = "", rationale: str = "") -> dict:
    """Set (or clear) a resource's disposition within this investigation.

    Clearing leaves it in the Folio — in scope, unjudged — rather than removing
    it, because "we have not decided yet" is a real state and losing the
    resource would be the wrong way to express it.
    """
    reg = _registry()
    if not reg.get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")
    if disposition and disposition != "undecided" and disposition not in reg.WORKING_SET_DISPOSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown disposition '{disposition}'; valid: {list(reg.WORKING_SET_DISPOSITIONS)}",
        )
    return reg.set_investigation_disposition(slug, entity_type, entity_slug,
                                             disposition, rationale=rationale)


@router.get("/{slug}/next-steps")
async def next_steps(slug: str) -> dict:
    """What this investigation still lacks, as offers rather than a form.

    The creation form asks only what §1 says an investigation IS — a name, why
    it exists, and how it relates to Egeria. Everything else is surfaced here,
    where the absence already is: an investigation with no resources says so and
    points at Scouting; one with no Egeria binding offers to make one.

    Deliberately phrased as offers. A scheduled run has nobody to ask and must
    not block; an interactive session can act on the same list. Neither is
    prompted, and an unanswered offer is a standing offer, not an open question.
    """
    reg = _registry()
    inv = reg.get_investigation(slug)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")

    steps: list[dict] = []
    if not inv.get("purposes") and not inv.get("purpose_text"):
        steps.append({
            "id": "declare_purpose",
            "title": "No purpose declared",
            "detail": "Purpose is why this work exists — it ranks what gets proposed. "
                      "Without it nothing can be ordered by relevance.",
            "action": "edit",
        })
    if not inv.get("member_count"):
        steps.append({
            "id": "add_resources",
            "title": "Nothing in scope yet",
            "detail": "Find resources in Scouting and add them to the working set. "
                      "An investigation with no resources cannot survey anything.",
            "action": "scouting",
        })
    else:
        # The remainder, not merely "has anyone judged anything". Keying on the
        # latter let ONE judged resource out of nineteen report the whole
        # investigation as judged — and the summary line above this list then
        # said "Nothing outstanding", which was false while 14 sat unjudged.
        judged = {
            (m["entity_type"], m["entity_slug"])
            for members in reg.investigation_dispositions(slug).values()
            for m in members
        }
        unjudged = [
            m for m in reg.list_investigation_members(slug)
            if (m["entity_type"], m["entity_slug"]) not in judged
        ]
        if unjudged:
            none_yet = not judged
            steps.append({
                "id": "set_dispositions",
                "title": "Nothing judged yet" if none_yet
                         else f"{len(unjudged)} resource(s) not judged yet",
                "detail": (f"{inv['member_count']} resource(s) in scope, none marked. "
                           if none_yet else
                           f"{len(judged)} of {inv['member_count']} resource(s) in scope carry a "
                           f"disposition; {len(unjudged)} do not. ")
                          + "Dispositions are how this investigation says which ones matter.",
                "action": "disposition",
            })
    if inv.get("egeria_project_status") == "unbound":
        # After an Egeria reset: the project it named is gone. A status word with a proof or none at all,
        # so this is its own state, not "local only" (a choice) and not "linked" (a project that is there).
        from resource_explorer.catalogue_commit import PROJECT_UNBOUND_WORDS
        steps.append({
            "id": "bind_egeria",
            "title": PROJECT_UNBOUND_WORDS,
            "detail": "Egeria was reset and the project this investigation named is gone. Promote to "
                      "recreate it under the same name, or bind to an existing Egeria Project.",
            "action": "egeria",
        })
    elif inv.get("egeria_project_status") != "linked":
        steps.append({
            "id": "bind_egeria",
            "title": "Local only — not in Egeria",
            "detail": "Bind to an existing Egeria Project or create one. Resources in "
                      "scope then inherit that binding instead of each needing its own.",
            "action": "egeria",
        })
    return {"investigation": slug, "steps": steps, "complete": not steps}
