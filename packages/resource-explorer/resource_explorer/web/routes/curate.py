"""Curate API — tags, resource-level feedback, and curator notes.

Curate is about making a resource easier to find and more trustworthy to
reuse (search tags, feedback, curator commentary) — distinct from
Enrichment (context.py), which is about recording facts about the
resource itself (environment, ownership, sensitivity, ...).

Digital-product evaluation, sample-dataset creation, and quality-issue
remediation are explicitly NOT covered here yet — they're real, separate
design questions (data model, workflow) that haven't been scoped, not an
oversight. See docs/curate-followups.md.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer.auth import get_current_user
from resource_explorer.feedback_store import FeedbackStore
from resource_explorer.registry import ProjectRegistry

from resource_explorer.config import get_config
from resource_explorer.web.admin_auth import is_admin_request


def _require_admin(request: Request) -> None:
    """Gate an admin listing, matching routes/feedback.py's `_require_admin`.

    Added 2026-09-01 to close a real drift: this module's `/feedback` listing
    and `routes/feedback.py`'s were two implementations of one feature against
    two endpoints with two auth postures — one gated, one not — and on
    2026-09-01 the ungated one was widened to serve the page-level store as
    well. `admin_auth.is_admin_request` is fail-closed AND an admin token is
    configured here, so the gate was real and being bypassed rather than
    aspirational.

    `docs/admin-surface-options.md` names fixing this drift as the prerequisite
    before any further admin extraction: the one place RE already split a
    surface produced duplicated logic and divergent auth, which is an argument
    against repeating the split at larger scale until it is repaired.

    It also stops being optional once RE reaches the open demo environment
    (`docs/Backlog.md`, "Auth posture ..."), where the difference between a
    gated and an ungated feedback listing is the difference between a form and
    a mailing list.
    """
    if not is_admin_request(request, get_config().feedback):
        raise HTTPException(status_code=403, detail="Admin credential required")


router = APIRouter()


def _require_author(request: Request, action: str) -> str:
    """The signed-in user id, or 401. EVERY write route for tags, resource
    feedback and curator notes calls this first (a test fails if one doesn't).

    The author comes from the session, never from the request body: the write
    models have no `author` field, and pydantic drops unknown keys. Mirrors
    `routes/journal.py` and the report acts: anonymous is refused rather than
    recorded as nobody's. Resolved here, in the request thread, and passed to
    the registry as an explicit argument — never read from a ContextVar inside
    a bare `threading.Thread`, which would drop the caller and write ''.
    """
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(
            status_code=401,
            detail=f"Sign in to {action} — a tag, rating or note needs an author.",
        )
    return author


def _registry() -> ProjectRegistry:
    return ProjectRegistry()


# The materialization workflows moved to resource_explorer/workflows/curate.py
# in step 2b (plan §3: web-only code sitting above the already-core
# ComponentMaterializer/BlueprintMaterializer). The routes below are thin: they
# validate, record the verdict, and merge whatever the workflow reports.
# Accept and Reject are decisions only (Brief A): no route here materializes or promotes; Publish
# (architecture_publish, via POST /api/projects/{slug}/architecture/publish) is the one verb that writes.
from resource_explorer.workflows.curate import (  # noqa: E402
    CurationDenied,
    require_curation_rights as _require_curation_rights,
)


def _authorize_curation(registry: ProjectRegistry, entity_type: str, slug: str,
                        scope_locator: str, reader=None) -> None:
    """403 unless Egeria's zones (emulated) let the caller change this element, or a Portal curator/admin role.

    The decision itself lives in `workflows/curate.curation_access` so the CLI and the A2A surface inherit it
    (plan §4: "enforced at the workflow layer so the CLI and A2A honour it too"); this function is only the
    HTTP shape of the same answer. The detail is the reason alone; the screen puts "not permitted ·" before it.
    """
    try:
        _require_curation_rights(registry, entity_type, slug, scope_locator, reader=reader)
    except CurationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _authorize_publish_item(registry: ProjectRegistry, slug: str, kind: str, key: str, reader=None) -> None:
    """Raises `CurationDenied` unless the caller may PUBLISH this item: every operation its write performs
    (create or update, classify, rezone, and attach on both ends for a blueprint). Brief Z round 2."""
    from resource_explorer.workflows.curate import publish_item_access

    d = publish_item_access(registry, slug, kind, key, reader=reader)
    if not d.allowed:
        raise CurationDenied(d.reason)


async def authorize_resource_write(registry: ProjectRegistry, entity_type: str, slug: str, *,
                                   publish: bool | None = None, write: str | None = None,
                                   element_guid: str | None = None) -> None:
    """403 unless the caller may change this resource's own element (Brief Z round 2, Z2): the database or file
    system asset, or a repository's asset. `write` names what the route does (`workflows.curate.RESOURCE_WRITES`:
    publish, decision, start_action, delete); the older `publish=True/False` means publish/decision. The decision
    is `workflows.curate.resource_access` (the CLI and A2A call the same function). Run off the event loop: the
    check may read Egeria (bounded). The schema elements a database commit writes are checked one by one in the
    commit itself (`catalogue_commit.build_preview`, Brief Z2)."""
    import asyncio

    from resource_explorer.workflows.curate import resource_access

    what = write or ("publish" if publish else "decision")
    d = await asyncio.to_thread(resource_access, registry, entity_type, slug, what, element_guid=element_guid)
    if not d.allowed:
        raise HTTPException(status_code=403, detail=d.reason)


def require_resource_write(registry: ProjectRegistry, entity_type: str, slug: str, write: str, *,
                           element_guid: str | None = None) -> None:
    """`authorize_resource_write` for a synchronous route (already off the event loop): 403 with the reason."""
    from resource_explorer.workflows.curate import resource_access

    d = resource_access(registry, entity_type, slug, write, element_guid=element_guid)
    if not d.allowed:
        raise HTTPException(status_code=403, detail=d.reason)


# ── Tags ─────────────────────────────────────────────────────────────────────

class TagCreate(BaseModel):
    tag: str


@router.get("/tags")
def list_all_tags() -> list[dict]:
    """Distinct tags across all resources, with usage counts — backs
    autocomplete and browse-by-tag."""
    return _registry().list_all_tags()


# NOTE: /tags/{tag}/resources is declared before /tags/{entity_type}/{slug}
# deliberately — both are 2-segment paths under /tags, and Starlette matches
# routes in declaration order, so a route declared after an equally-shaped
# path-param route is unreachable (the same class of bug already documented
# in analyses.py's /perspectives route).
@router.get("/tags/{tag}/resources")
def resources_by_tag(tag: str) -> list[dict]:
    return _registry().list_resources_by_tag(tag)


@router.get("/tags/{entity_type}/{slug}")
def list_tags(entity_type: str, slug: str) -> list[str]:
    return _registry().list_resource_tags(entity_type, slug)


@router.get("/tags-detail/{entity_type}/{slug}")
def list_tags_detail(entity_type: str, slug: str) -> list[dict]:
    """Tags with who added them: [{tag, created_at, author, authored,
    author_label}]. `author` is None for rows from before authors were
    recorded (`authored` false, `author_label` the words to show)."""
    return _registry().list_resource_tags_with_authors(entity_type, slug)


def authorize_curation_change(registry: ProjectRegistry, entity_type: str, slug: str, kind: str,
                              action: str) -> None:
    """403 unless Brief Z's curation access allows this tag or group change on the resource's element (Brief T):
    UPDATE_PROPERTIES, plus the operation Egeria itself checks for the link (`curation_egeria.operations_for`).
    Checked BEFORE RE records the change, so a refusal records nothing."""
    from resource_explorer.curation_egeria import operations_for

    try:
        _require_curation_rights(registry, entity_type, slug, "", operations=operations_for(kind, action))
    except CurationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def curation_state(registry: ProjectRegistry, entity_type: str, slug: str) -> dict:
    """The tags' and the group's state as the plan reads it after a Save (Brief F: a Save sends nothing; the page's
    Publish does). Never fails the RE write that came before it: an error is in the answer."""
    from resource_explorer.curation_egeria import curation_plan, plan_as_dict

    try:
        return plan_as_dict(curation_plan(registry, entity_type, slug))
    except Exception as exc:  # the RE change is recorded; the band re-reads the state from the rows
        return {"error": f"{type(exc).__name__}: {exc}"}


@router.post("/tags/{entity_type}/{slug}")
def add_tag(entity_type: str, slug: str, body: TagCreate, request: Request) -> dict:
    """Add a tag in RE. Brief F (one Publish per page): it waits, "saved · will publish", and the page's Publish
    links it as a public InformalTag (Brief T). Access is checked here, before RE records it."""
    author = _require_author(request, "add a tag")
    typed = body.tag.strip()
    tag = typed.lower()          # RE's key; Egeria's displayName keeps the case it was typed in (round 2)
    if not tag:
        raise HTTPException(status_code=400, detail="tag must not be empty")
    reg = _registry()
    authorize_curation_change(reg, entity_type, slug, "tag", "link")
    reg.add_resource_tag(entity_type, slug, tag, author=author)
    # Brief F round 2: the spelling as typed travels to Egeria's displayName at the page's Publish; RE keeps the
    # tag lower-case and has no column for it, so the activity entry carries it (curation_egeria.typed_spellings).
    import uuid
    from datetime import datetime, timezone
    from resource_explorer.registry import ActivityEntry
    try:
        reg.write_activity(ActivityEntry(
            id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(),
            operation="curate_tag_added", intent="curate",
            entity_type=entity_type, entity_slug=slug,
            summary=f"tag {tag!r} added by {author}",
            annotations=[{"tag": tag, "typed": typed, "added_by": author}],
        ))
    except Exception:  # noqa: BLE001 - the tag is saved; only its typed spelling is lost (the key is used)
        import logging

        logging.getLogger(__name__).warning("Could not record the typed spelling of tag %r on %s %s",
                                            tag, entity_type, slug, exc_info=True)
    return {"status": "success", "tag": tag, "typed": typed, "author": author,
            "egeria": curation_state(reg, entity_type, slug)}


@router.delete("/tags/{entity_type}/{slug}/{tag}")
def remove_tag(entity_type: str, slug: str, tag: str, request: Request) -> dict:
    """Remove a tag in RE. Brief F: the unlink from the asset waits for the page's Publish ("saved · will publish").
    The InformalTag element itself is never deleted: that is a delete under the ISSUE-117 block, and the owner
    deletes by hand."""
    author = _require_author(request, "remove a tag")
    reg = _registry()
    authorize_curation_change(reg, entity_type, slug, "tag", "unlink")
    reg.remove_resource_tag(entity_type, slug, tag)
    # A removal leaves no row to carry an author, so who removed it is
    # recorded in the activity log.
    import uuid
    from datetime import datetime, timezone
    from resource_explorer.registry import ActivityEntry
    reg.write_activity(ActivityEntry(
        id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(),
        operation="curate_tag_removed", intent="enrichment",
        entity_type=entity_type, entity_slug=slug,
        summary=f"tag {tag!r} removed by {author}",
        annotations=[{"tag": tag, "removed_by": author}],
    ))
    return {"status": "success", "removed_by": author, "egeria": curation_state(reg, entity_type, slug)}


# ── Tags and group in Egeria (Brief T, 2026-10-10) ───────────────────────────
# Tags are public InformalTags and the RE group is a Folio (resource_explorer/curation_egeria.py). Brief F: they go
# with the page's ONE Publish; ratings and notes go with it too, as their author (resource_explorer/feedback_egeria.py).

class EgeriaRetry(BaseModel):
    kind: str
    name: str


class EgeriaSync(BaseModel):
    retry: EgeriaRetry | None = None


@router.get("/egeria-state/{entity_type}/{slug}")
def egeria_state(entity_type: str, slug: str) -> dict:
    """Per tag and for the group: its state in Egeria, read from the outbox rows (`curation_egeria.curation_plan`,
    the same function a sync acts on). Read-only: no Egeria call, nothing queued."""
    from resource_explorer.curation_egeria import curation_plan, plan_as_dict

    return plan_as_dict(curation_plan(_registry(), entity_type, slug))


#: Brief F: what a bare send answers now that tags and the group go with the page's Publish.
SEND_WITH_PUBLISH = "tags and the group go to Egeria with the page's Publish; press Publish to send them"


@router.post("/egeria-sync/{entity_type}/{slug}")
def egeria_sync(entity_type: str, slug: str, request: Request, body: EgeriaSync | None = None) -> dict:
    """A person's retry of ONE failed (dead) link or unlink: a new row, never a revived one, drained as them. The
    unlink kinds are destructive and never re-sent on their own, so the retry is this visible, explicit act.
    Brief F: without `retry` it sends nothing (409): the page's ONE Publish sends tags and the group. 403 when
    curation access says no, 409 when there is nothing to retry."""
    from resource_explorer.curation_egeria import RetryRefused, retry_item

    author = _require_author(request, "retry a tag or group change in Egeria")
    reg = _registry()
    if body is not None and body.retry is not None:
        try:
            return retry_item(reg, entity_type, slug, body.retry.kind, body.retry.name, by=author)
        except RetryRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CurationDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=SEND_WITH_PUBLISH)


@router.get("/feedback-state/{entity_type}/{slug}")
def feedback_state(entity_type: str, slug: str, request: Request) -> dict:
    """Brief F: per rating and per journal entry, what it is in Egeria ("saved · will publish", "published 10-11",
    "not sent · <reason>", "<person>'s · waiting for them", "replaced by a newer rating"), as the signed-in viewer
    sees it (their own entries are theirs to send). Read from RE's rows and the outbox rows
    (`feedback_egeria.feedback_plan`, the same function the Publish acts on). No Egeria call."""
    from resource_explorer.feedback_egeria import feedback_plan, plan_as_dict

    user = get_current_user(request)
    viewer = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    return plan_as_dict(feedback_plan(_registry(), entity_type, slug, viewer=viewer))


# ── Feedback ─────────────────────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    rating: int | None = None
    category: str = ""
    message: str



#: Fields the page-level feedback store records that this route must NOT serve.
#:
#: `/api/feedback` (routes/feedback.py) gates on `admin_auth.is_admin_request`,
#: which is fail-closed by design — its docstring says it exists "only to gate
#: the feedback-triage admin endpoints", written to invert an Egeria Workspaces
#: bug where the equivalent check failed OPEN.
#:
#: **This route had no such gate** when, on 2026-09-01, it was widened to serve the
#: page-level store so the Admin pane would stop showing an empty list. That fix
#: was right and its scope was not: it routed data the gated endpoint protects
#: through an ungated one. (It is gated now: `_require_admin` below.) Nothing was exposed in practice — no row carries an
#: email today — but the store has `wants_response` and `consent_to_contact`
#: columns, so emails are expected, and the next one would have been served to
#: anyone who could reach the port.
#:
#: Stripping is the INTERIM fix, agreed with Dan: it closes the exposure now
#: without emptying the pane, which gating would do until the frontend sends
#: `X-Admin-Token`. Gating this route was the real fix and has since landed
#: (`_require_admin`); the stripping is now redundant rather than wrong, and is
#: kept as defence in depth until someone decides the gated route serves the full row.
#:
#: `message`, `rating`, `category`, `page`, `triage_status` and `created_at`
#: stay: they are the feedback itself, which is the point of the pane.
_CONTACT_FIELDS = ("email", "session_id", "user_agent", "viewport", "locale")


def _without_contact_fields(row: dict) -> dict:
    """A page-feedback row with contact/identifying fields removed.

    Removes the keys rather than blanking them. A blank `email` is
    indistinguishable from a row whose author left none — the absence-looks-like
    -a-value shape this codebase keeps removing — and a caller that sees no key
    at all cannot mistake it for a measured empty.
    """
    return {k: v for k, v in row.items() if k not in _CONTACT_FIELDS}

@router.get("/feedback")
def list_all_feedback(
    request: Request,
    limit: int = 200, entity_type: str = "", category: str = "", source: str = ""
) -> dict:
    """Every feedback item in one place, for Admin — from BOTH stores.

    There are two independent feedback systems, and until 2026-09-01 this
    route only read one of them:

      * `resource_feedback` (`ProjectRegistry.add_resource_feedback`, this
        module's `add_feedback` below) — per-resource, written from a
        resource's Curate tab.
      * `feedback` (`resource_explorer/feedback_store.py`) — the page-level
        widget reachable from anywhere in the app.

    A page-level submission and a per-resource one look, from the person who
    wrote it, exactly the same: feedback that was left and never showed up
    here. It was never dropped — the pane just never read the store it landed
    in. Diagnosed 2026-08-31 (Dan: "I added feedback on a page and it never
    makes it to the feedback pane of Admin").

    This combines both into one list rather than merging them: the two tables
    have genuinely different shapes (session/page/consent fields on one side,
    entity_type/slug on the other), and a real merge is a separate, deliberate
    task (see docs/Backlog.md) — not something to fold in as a side effect of
    fixing visibility. Each row carries `source: "page" | "resource"` so the
    two stay distinguishable until that task exists, and `source` is itself a
    filter for the same reason `entity_type`/`category` are.

    Declared before the /{entity_type}/{slug} route below only for readability;
    they cannot collide, since that one needs two path segments.

    Returns per-source counts (not a combined total, which would hide a store
    that is genuinely empty) alongside the rows, and `filtered` because an
    empty list is ambiguous on its own — "nobody has left feedback" and "your
    filter excluded everything" look identical, and only one of those is a
    fact worth stating plainly.
    """
    _require_admin(request)

    reg = _registry()
    resource_rows = reg.list_all_resource_feedback(
        limit=limit, entity_type=entity_type, category=category)
    resource_counts = reg.count_all_resource_feedback()

    page_store = FeedbackStore()
    # entity_type has no meaning for page-level feedback — it was never
    # recorded against a resource at all. A filter on it must exclude every
    # page row, not silently ignore the filter and show them regardless.
    page_rows = [] if entity_type else page_store.list(category=category or None, limit=limit)
    page_stats = page_store.stats()

    combined = [dict(r, source="resource") for r in resource_rows]
    # Full rows now. `_without_contact_fields` was the INTERIM fix while this
    # route was ungated (cb99d72); with the gate in place, stripping would leave
    # two equally-gated views of one store disagreeing about what it contains —
    # which is the drift this change exists to end. The helper is kept and
    # tested, since an ungated caller may exist again.
    combined += [dict(r, source="page") for r in page_rows]
    if source:
        combined = [r for r in combined if r["source"] == source]
    combined.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    return {
        "feedback": combined[:limit],
        "counts": {
            "resource": resource_counts,
            "page": {"total": page_stats["total"]},
        },
        "filtered": bool(entity_type or category or source),
    }


@router.get("/feedback/{entity_type}/{slug}")
def list_feedback(entity_type: str, slug: str) -> list[dict]:
    return _registry().list_resource_feedback(entity_type, slug)


@router.post("/feedback/{entity_type}/{slug}")
def add_feedback(entity_type: str, slug: str, body: FeedbackCreate, request: Request) -> dict:
    author = _require_author(request, "leave feedback")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    if body.rating is not None and not (1 <= body.rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be between 1 and 5")
    return _registry().add_resource_feedback(entity_type, slug, body.rating, body.category, body.message,
                                           author=author)


# ── Curator notes ────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    note: str


@router.get("/notes/{entity_type}/{slug}")
def list_notes(entity_type: str, slug: str) -> list[dict]:
    return _registry().list_curator_notes(entity_type, slug)


@router.post("/notes/{entity_type}/{slug}")
def add_note(entity_type: str, slug: str, body: NoteCreate, request: Request) -> dict:
    author = _require_author(request, "add a note")
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="note must not be empty")
    return _registry().add_curator_note(entity_type, slug, body.note, author=author)


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, request: Request) -> dict:
    """Delete a LEGACY unsigned note. Notes are append-only like the journal:
    a signed note is refused (409), amended by adding a later note instead."""
    _require_author(request, "delete a note")
    reg = _registry()
    note = reg.get_curator_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if note["authored"]:
        raise HTTPException(
            status_code=409,
            detail="A signed note cannot be deleted — notes are append-only. "
                   "Add a later note to amend it.",
        )
    if not reg.delete_curator_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "success"}


# ── Architecture component verdicts ─────────────────────────────────────────
#
# First slice of docs/Backlog.md "take architecture results into Curate":
# accept / reject / retype a single proposed component (§4.1a's proposal
# framing, §3.3b/§3.4's Confidence/ContentStatus axis — not a new
# vocabulary). scope_locator is the same join key architecture_recovery
# findings use (a component's path prefix); the results reader
# (_architecture_recovery_results) merges the latest verdict onto each
# component it returns.

class ComponentVerdictCreate(BaseModel):
    scope_locator: str
    verdict: str  # "accepted" | "rejected" | "retyped"
    retyped_to: str = ""  # required (and only meaningful) when verdict="retyped"
    note: str = ""
    # Classic's rows are one component each: an accept or reject there is about THIS component, not the ones
    # under its path (the Next tree's "this component only" mark, component_tree.ONLY_THIS). False gives the
    # branch meaning: the verdict reaches every component under the path until its own row wins.
    only: bool = True


@router.get("/component-verdicts/{entity_type}/{slug}")
def list_component_verdicts(entity_type: str, slug: str) -> dict[str, dict]:
    """{scope_locator: latest verdict} for every component this resource has
    a curator verdict on — the same shape merged onto each component by
    _architecture_recovery_results.

    Filters out verdict_target='blueprint' rows (added 2026-09-03, Phase B):
    get_component_verdicts' own docstring says it returns both mixed and
    leaves filtering to the caller — this route's name and its one existing
    consumer (_architecture_recovery_results, keyed by scope_locator) both
    promise component verdicts specifically, so a blueprint verdict's key
    (f"{perspective}::{cluster_name}") appearing here would be a surprising,
    if practically harmless, leak. Mirrors list_blueprint_verdicts' own
    client-side filter in the other direction.
    """
    all_verdicts = _registry().get_component_verdicts(entity_type, slug)
    return {k: v for k, v in all_verdicts.items() if v.get("verdict_target") != "blueprint"}


@router.post("/component-verdicts/{entity_type}/{slug}")
def add_component_verdict(entity_type: str, slug: str, body: ComponentVerdictCreate) -> dict:
    if not body.scope_locator.strip():
        raise HTTPException(status_code=400, detail="scope_locator must not be empty")
    if body.verdict not in ProjectRegistry.COMPONENT_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail=f"verdict must be one of {sorted(ProjectRegistry.COMPONENT_VERDICTS)}, got {body.verdict!r}",
        )
    if body.verdict == "retyped" and not body.retyped_to.strip():
        raise HTTPException(status_code=400, detail="retyped_to is required when verdict='retyped'")
    registry = _registry()
    _authorize_curation(registry, entity_type, slug, body.scope_locator)
    from resource_explorer.component_tree import ONLY_THIS
    retyped_to = body.retyped_to
    if body.verdict in ("accepted", "rejected") and not retyped_to.strip() and body.only:
        retyped_to = ONLY_THIS
    # A decision and nothing more, exactly like the Next branch route (projects.branch_verdicts): no Egeria
    # call, no materialize run, no promotion. Publish (POST /api/projects/{slug}/architecture/publish) is the one
    # verb that writes an accepted component to Egeria, and promotes it there. A reject never writes either.
    verdict = registry.record_component_verdict(
        entity_type, slug, body.scope_locator, body.verdict, retyped_to, body.note,
    )
    # Said from the element's cache row, never from this branch: "accepted · not in Egeria yet" until Publish.
    held = registry.get_materialized_component(entity_type, slug, body.scope_locator) or {}
    verdict["in_egeria"] = bool(held.get("guid"))
    verdict["materialized"] = ({"guid": held["guid"], "qualified_name": held.get("qualified_name", "")}
                               if held.get("guid") else None)
    return verdict


@router.get("/component-verdicts/{entity_type}/{slug}/history")
def component_verdict_history(entity_type: str, slug: str, scope_locator: str) -> list[dict]:
    """Full verdict trail for one component, newest first — scope_locator as
    a query param (not a path segment) since it's a path prefix and routinely
    contains slashes."""
    return _registry().list_component_verdict_history(entity_type, slug, scope_locator)


# ── Blueprint verdicts ──────────────────────────────────────────────────────
#
# docs/blueprint-materialization-plan.md — the same report-then-curate shape
# as component verdicts above, one level up: a candidate_blueprint finding
# (a clustering.py proposal, not yet a real Egeria element) is what a
# curator accepts or rejects. Reuses architecture_component_verdicts (see
# the plan's Decision 3) rather than a parallel table — verdict_target
# distinguishes the two, and scope_locator is repurposed to hold
# f"{perspective}::{cluster_name}" since a blueprint has none of its own.
#
# Wires are deliberately NOT enqueued here (project-owner decision,
# 2026-09-03, after Phase A.5's live measurement confirmed
# SolutionLinkingWire duplicates on retry) — a materialized blueprint
# attaches its member components and child blueprints but renders without
# its wire diagram until that's built as its own follow-up.

class BlueprintVerdictCreate(BaseModel):
    perspective: str
    cluster_name: str
    verdict: str  # "accepted" | "rejected"
    note: str = ""
    # A person's flip of the blueprint's shape before the write ("container" | "contents"). NOT SENT (None) keeps
    # the choice already stored for this blueprint, so a client that does not offer the flip (Classic, the CLI)
    # never wipes one made elsewhere; "" or "default" clears it back to the default the plan names
    # (blueprint_shape.py).
    shape: str | None = None
    # A person's identifier for a SECOND blueprint of one kind in a repository (never derived from a cluster
    # name). None keeps the stored one; "" clears it.
    identifier: str | None = None


@router.get("/blueprint-verdicts/{entity_type}/{slug}")
def list_blueprint_verdicts(entity_type: str, slug: str) -> dict[str, dict]:
    """{f"{perspective}::{cluster_name}": latest verdict} — filters
    list_component_verdicts' full result to verdict_target='blueprint'
    client-side, per the plan's Decision 3 (one table, not a second query
    parameter for what is currently one filter)."""
    all_verdicts = _registry().get_component_verdicts(entity_type, slug)
    return {k: v for k, v in all_verdicts.items() if v.get("verdict_target") == "blueprint"}


@router.post("/blueprint-verdicts/{entity_type}/{slug}")
def add_blueprint_verdict(entity_type: str, slug: str, body: BlueprintVerdictCreate) -> dict:
    if not body.perspective.strip() or not body.cluster_name.strip():
        raise HTTPException(status_code=400, detail="perspective and cluster_name must not be empty")
    if body.verdict not in ProjectRegistry.BLUEPRINT_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail=f"verdict must be one of {sorted(ProjectRegistry.BLUEPRINT_VERDICTS)}, got {body.verdict!r}",
        )
    from resource_explorer.blueprint_shape import SHAPES
    shape = None if body.shape is None else ("" if body.shape in ("", "default") else body.shape)
    if shape and shape not in SHAPES:
        raise HTTPException(status_code=400, detail=f"shape must be one of {list(SHAPES)}, got {body.shape!r}")
    from resource_explorer.blueprint_kinds import validate_identifier
    try:
        identifier = None if body.identifier is None else validate_identifier(body.identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    registry = _registry()
    scope_locator = f"{body.perspective}::{body.cluster_name}"
    _authorize_curation(registry, entity_type, slug, scope_locator)
    # A decision and nothing more: no Egeria call, no promotion. Publish writes the blueprint
    # (architecture_publish), with the shape and identifier the person chose. A choice not sent is carried
    # forward from the latest verdict row, so only an explicit value changes it.
    from resource_explorer.architecture_publish import blueprint_choices, encode_blueprint_choices
    stored = blueprint_choices(registry.get_component_verdicts(entity_type, slug).get(scope_locator))
    return registry.record_component_verdict(
        entity_type, slug, scope_locator, body.verdict,
        encode_blueprint_choices(stored["shape"] if shape is None else shape,
                                 stored["identifier"] if identifier is None else identifier),
        body.note, verdict_target="blueprint",
    )
