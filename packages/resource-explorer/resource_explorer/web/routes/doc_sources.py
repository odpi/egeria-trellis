"""Documentation sources API — Enrichment stage, slice 1 ("Declare and
probe") of `docs/design-notes/exchange/BRIEF-DATABASE-DOCUMENTATION-SOURCES.md`.

A person declares a URL pointing at documentation for a database or
filesystem — a wiki page, a data dictionary, a runbook — RE probes it
read-only and reports one of `reachable`/`needs_sign_in`/`not_found`/
`blocked` (`doc_source_probe.py`, mirroring `credential_capability`'s
posture and vocabulary), and when the resource is published each source
becomes an `ExternalReference` on its Egeria asset (`doc_source_egeria.py`,
reusing `egeria_publisher.py`'s `_publish_homepage_reference` pattern).

Only `database`, `filesystem` and (E2) `repo` entity types are wired — the brief allows
scoping filesystem out for time ("prioritize database first... note
explicitly... if scoped out") but it turned out to need no extra code
beyond the entity resolver below, since `FileSystemEntity` carries the same
`egeria_url`/`egeria_server`/
`egeria_asset_guid`/`display_name` shape `DatabaseEntity` does — so both are
built together in this slice rather than filesystem being deferred.
Ingest/re-ingest actions (slice 2, `doc_source_ingestion`) are NOT wired
here; the frontend renders them as visibly disabled ("ingest — coming
soon") rather than omitting the affordance, so slice 2 has a known place to
attach rather than inventing UI from scratch.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer.doc_source_egeria import (
    publish_doc_source,
    read_back_doc_sources,
    resolve_entity_for_doc_source,
)
from resource_explorer.doc_source_probe import probe as run_probe
from resource_explorer.egeria_linkage import describe_publish_status
from resource_explorer.egeria_outbox import (
    drain_outbox_row,
    enqueue_doc_source_publish,
    enqueue_doc_source_unpublish,
)
from resource_explorer.auth import get_current_user
from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)
router = APIRouter()

_ENTITY_TABLES = ("database", "filesystem", "repo")


def _registry() -> ProjectRegistry:
    return ProjectRegistry()


def _attempt_outbox_row_immediately(element_id: int) -> None:
    """Fire a scoped `drain_outbox_row` for one just-enqueued row, off the
    request thread — Egeria publish-state fix round 3 (2026-09-29). Design's
    spec is "publish at once (best effort), falling back to the existing
    15-minute retry loop only if that immediate attempt fails" — not "queue
    it and wait up to 15 minutes for the scheduler's next pass", which is
    what round 2 actually shipped.

    Runs on its own daemon thread (same fire-and-forget shape
    `worker.py`/`rag_system.py` already use elsewhere in this codebase) so
    the HTTP response to the add/remove request is never blocked on an
    Egeria round trip. Builds its OWN `ProjectRegistry()` rather than
    capturing the caller's — connections are not shared across threads
    anywhere else in this codebase either. `drain_outbox` itself never
    raises (it converts every failure into `mark_outbox_failed`/dead-letter
    bookkeeping on the row), so this thread cannot crash the process; a
    failure here just leaves the row exactly where the normal 15-minute
    scheduler drain (`scheduler.py`) would find it and retry it, which is
    the intended fallback, not a bug in this path.
    """
    import contextvars

    # The person's own act (Brief I): the drain runs as the signed-in Caller. A bare thread
    # drops ContextVars, so this one carries the request's context explicitly.
    ctx = contextvars.copy_context()

    def _run() -> None:
        try:
            ctx.run(drain_outbox_row, _registry(), element_id)
        except Exception:
            # Defense in depth only — drain_outbox_row/drain_outbox already
            # catch everything and route failures onto the row itself via
            # mark_outbox_failed, so reaching this is not expected. Logged,
            # not swallowed silently, and never propagated: this thread has
            # no caller left to propagate to by the time it runs.
            log.exception("doc sources: immediate outbox attempt failed for row %s", element_id)

    threading.Thread(target=_run, name=f"doc-source-outbox-{element_id}", daemon=True).start()


def _resolve_entity(registry: ProjectRegistry, entity_type: str, slug: str):
    if entity_type not in _ENTITY_TABLES:
        raise HTTPException(status_code=400,
                             detail=f"documentation sources are not supported for entity_type={entity_type!r}")
    entity = resolve_entity_for_doc_source(registry, entity_type, slug)
    if not entity:
        raise HTTPException(status_code=404, detail=f"{entity_type} '{slug}' not found")
    return entity


def _publish_status(registry: ProjectRegistry, entity_type: str, slug: str, guid: str) -> dict:
    return describe_publish_status(registry, entity_type, slug, guid or "")


def _sync_egeria_read_back(registry: ProjectRegistry, entity_type: str, slug: str, entity) -> None:
    """Best-effort: fold any ExternalReference Egeria has for this asset
    that RE's own `doc_sources` table doesn't know about yet into it — "a
    source declared in Egeria by someone else appears here too" (the
    brief). Never raises; a failed read-back just means the list below is
    local-only for this call, same as when the resource has never been
    published."""
    guid = entity.egeria_asset_guid or ""
    status = _publish_status(registry, entity_type, slug, guid)
    if not status["is_published"]:
        return
    try:
        from resource_explorer.doc_source_egeria import entity_clients
        from resource_explorer.egeria_clients import Caller

        # The signed-in person (Brief I), never the entity's stored credential or the service
        # account: a read-back shows what THIS user can see.
        remote = read_back_doc_sources(guid, clients=entity_clients(entity, Caller()))
    except Exception as exc:
        log.debug("doc sources: read-back skipped for %s/%s: %s", entity_type, slug, exc)
        return
    known_guids = {r["egeria_external_ref_guid"] for r in registry.list_doc_sources(entity_type, slug)
                   if r["egeria_external_ref_guid"]}
    known_urls = {r["url"] for r in registry.list_doc_sources(entity_type, slug)}
    for ref in remote:
        if ref["ref_guid"] in known_guids or ref["url"] in known_urls:
            continue
        # Adoption-race guard (2026-09-29) — found live: a source removed a
        # moment earlier enqueues a `doc_source_unpublish` for its ref guid;
        # if THIS read-back runs before that unpublish's detach+delete
        # actually lands in Egeria, the still-live reference looks exactly
        # like a legitimate "declared elsewhere" one. Skip it — the next
        # read-back (after the unpublish completes) will correctly see it's
        # gone, and a genuinely new source at the same URL self-heals its
        # own fresh publish via `_compute_egeria_state` rather than adopting
        # a reference that is being deleted out from under it.
        if registry.has_pending_unpublish_for_ref(entity_type, slug, ref["ref_guid"]):
            log.info("doc sources: read-back skip — %s (%s) has a pending/running unpublish, "
                      "not adopting it for %s/%s", ref["url"], ref["ref_guid"], entity_type, slug)
            continue
        registry.upsert_doc_source_from_egeria(
            entity_type, slug, url=ref["url"], ref_guid=ref["ref_guid"], label=ref.get("label", ""),
        )


class DocSourceCreate(BaseModel):
    url: str
    label: str = ""
    source_type: str = "other"


class DocSourceOut(BaseModel):
    id: str
    entity_type: str
    entity_slug: str
    url: str
    label: str
    source_type: str
    added_at: str
    added_by: str = ""
    origin: str = "local"
    probe_state: str = ""
    probe_status_code: int | None = None
    probe_ms: int | None = None
    probe_title: str = ""
    probe_byte_count: int | None = None
    probe_error: str = ""
    probed_at: str = ""
    egeria_external_ref_guid: str = ""
    # Egeria publish-state fix (2026-09-29, extended round 4 same day) — one
    # of FIVE states, never empty: "catalogued" / "publishing" /
    # "publish_failed" / "not_catalogued" / "local_only". See
    # `derive_doc_source_egeria_state`'s docstring for what each means and
    # how it's derived (a small, pure function — no branching on how the row
    # got here); find-absence-as-answer is exactly the bug this replaces (an
    # empty publish_note that meant several things indistinguishably, and
    # later, "publishing" rendered for a row with nothing actually pending).
    egeria_state: str = "local_only"
    egeria_state_detail: str = ""


class DocSourcesResponse(BaseModel):
    sources: list[DocSourceOut]
    published: bool
    publish_note: str = ""
    # Per-row rollup for the block header ("N declared · X in Egeria · Y
    # local") — computed here rather than re-derived in JS from `sources`,
    # so the one place that knows the four states' meaning is also the one
    # place that counts them.
    in_egeria_count: int = 0
    local_count: int = 0


def _out(row: dict) -> DocSourceOut:
    return DocSourceOut(**{k: row.get(k) for k in DocSourceOut.model_fields
                            if k not in ("egeria_state", "egeria_state_detail")})


def derive_doc_source_egeria_state(*, ref_guid: str, link_guid: str, is_published: bool,
                                    outbox_row: dict | None) -> tuple[str, str]:
    """Pure derivation: the word a row's Egeria linkage gets, computed ONLY
    from persisted facts passed in — never from which code path produced
    them. Design session (2026-09-29, round 4, after the adoption-race fix)
    asked for exactly this shape: every earlier version of this decision
    trusted `ref_guid` alone as proof of "catalogued", which is also
    precisely what let a read-back-adopted reference (ref guid set, no link
    ever created — see `doc_source_egeria.publish_doc_source`'s adoption-race
    guard) render as fully catalogued when it was never actually linked to
    THIS asset.

    No side effects, no branching on "how we got here" — same `row`/
    `outbox_row` shape whether the row was declared locally, adopted by
    read-back, or is being re-derived on a plain `GET`. See
    `tests/test_doc_sources_routes.py::TestDeriveEgeriaStateTable` for the
    full input-combination table this is pinned against.

    - `catalogued` — BOTH `ref_guid` and `link_guid` are non-empty. This is
      the only state that means "this source has a real
      `ExternalReferenceLink` to THIS asset in Egeria", not merely "an
      `ExternalReference` element with this qualifiedName exists somewhere".
      Detail is the ref guid (the brief's "surfaced somewhere accessible" —
      the frontend puts it in a `title` attribute).
    - `local_only` — the resource itself isn't published yet. Same rule as
      before this fix (`egeria_linkage.describe_publish_status`), unchanged.
    - `publishing` — `outbox_row["status"]` is `pending`/`running`.
    - `publish_failed` — `outbox_row["status"]` is `failed` (still retrying)
      or `dead` (retries exhausted — worded the same way; a `dead` row is
      the rarer case, already reported to a human via the outbox's own
      `record_drain_outcome` → RFA path, and is not given a wording of its
      own). Detail is the row's own `last_error`, never a generic message.
    - `not_catalogued` — everything else: most notably `ref_guid` set,
      `link_guid` EMPTY, and no outbox row in flight — a reference exists
      but was never (yet) linked to this asset, whether from a read-back
      adoption, a crash between create and link, or a row stranded before
      this fix existed. Rendered honestly rather than the old code's
      `publishing` (a real find-absence-as-answer bug: nothing was pending,
      nothing was running, the row said so anyway). The self-heal that
      queues a fresh publish for this exact shape is the ORCHESTRATOR's job
      (`_compute_egeria_state`, below) — this function has no side effects
      and never decides to enqueue anything.
    """
    ref_guid = ref_guid or ""
    link_guid = link_guid or ""
    if ref_guid and link_guid:
        return "catalogued", ref_guid
    if not is_published:
        return "local_only", ""
    status = (outbox_row or {}).get("status")
    if status in ("pending", "running"):
        return "publishing", ""
    if status in ("failed", "dead"):
        return "publish_failed", (outbox_row or {}).get("last_error") or "unknown error"
    return "not_catalogued", ""


def _compute_egeria_state(registry: ProjectRegistry, entity_type: str, slug: str,
                           row: dict, is_published: bool) -> tuple[str, str]:
    """The one place that calls the pure `derive_doc_source_egeria_state`
    AND owns this feature's one side effect: self-healing.

    A row landing on `not_catalogued` while the resource IS published means
    something needs to be (re-)queued — the add/read-back path missed
    enqueueing a publish for it (a source declared before this fix landed, a
    narrow race between "declare" and "this resource's publish landing", or
    a read-back adoption that set a ref guid without a link), OR an existing
    outbox row for it already reached `done` with a claim that no longer
    holds (round 6, 2026-09-29 — see below). Either way `derive_doc_source_
    egeria_state` will keep reporting `not_catalogued` forever unless
    something acts on it here.

    **A `done` outbox row is only a valid reason to skip self-heal WHILE its
    proof still holds.** `derive_doc_source_egeria_state` already proves the
    negative for us: reaching `not_catalogued` at all means `ref_guid` and
    `link_guid` are not BOTH set (see its own docstring) — round 5's own
    "done means verified" rule (`egeria_outbox.py::_create_doc_source_
    publish`) means the only way an outbox row can be `done` AND this row
    still be `not_catalogued` is if that row predates round 5's guarantee
    (exactly the incident round 6 fixes: `doc_sources` row `4711d538`, ref
    guid `b9925119…` deleted from Egeria by an unrelated unpublish, an
    outbox row that reached `done` under the PRE-round-5 write-back rule
    without ever checking the guid still resolved). A `pending`/`running`
    row already reports `publishing` (not `not_catalogued`) and a `failed`/
    `dead` row already reports `publish_failed` — neither reaches this
    branch, so the only outbox statuses possible here are `None` (nothing
    ever queued) or `done` (queued, but its claim is stale).

    - `outbox_row is None` — nothing has ever tracked this element; queue a
      fresh `doc_source_publish` row (unchanged from round 4).
    - `outbox_row["status"] == "done"` — REOPEN that same row (round 6) via
      `registry.reopen_outbox_row` rather than inserting a second row for
      the same element: history stays one row per element, and the very
      next drain (this immediate attempt, or the normal 15-minute loop if
      it fails) runs round 5's verify-before-trust logic against it for
      real, which is what a pre-round-5 `done` row never got.

    LOGGED either way because this firing means something upstream should
    have queued (or correctly completed) it already — the self-heal
    recovering gracefully doesn't make it the expected steady state.
    Idempotent: once (re)queued, the next call finds the pending/running row
    and `derive_doc_source_egeria_state` reports `publishing` instead.
    """
    ref_guid = row.get("egeria_external_ref_guid") or ""
    link_guid = row.get("egeria_link_relationship_guid") or ""
    outbox_row = registry.get_doc_source_outbox_row(entity_type, slug, row["id"]) if is_published else None
    state, detail = derive_doc_source_egeria_state(
        ref_guid=ref_guid, link_guid=link_guid, is_published=is_published, outbox_row=outbox_row,
    )
    if state != "not_catalogued":
        return state, detail
    if outbox_row is not None and outbox_row.get("status") == "done":
        reason = (
            f"self-heal (round 6): {entity_type}/{slug} source {row['id']} is "
            f"not_catalogued (ref_guid={ref_guid!r}, link_guid={link_guid!r}) but its "
            f"tracking outbox row {outbox_row['id']} already reached 'done' with "
            f"egeria_guid={outbox_row.get('egeria_guid')!r} — that claim no longer holds; "
            "reopening for a fresh verified attempt rather than leaving it stuck"
        )
        log.warning("doc sources: %s", reason)
        registry.reopen_outbox_row(outbox_row["id"], reason)
        element_id = outbox_row["id"]
    else:
        log.warning(
            "doc sources: self-heal — %s/%s source %s is published with ref_guid=%r, "
            "link_guid=%r, and no outbox row in flight; the add/read-back path missed "
            "enqueueing a publish for it — queuing one now",
            entity_type, slug, row["id"], ref_guid, link_guid,
        )
        element_id = enqueue_doc_source_publish(registry, entity_type, slug, row["id"], row["url"])
    _attempt_outbox_row_immediately(element_id)
    return "publishing", ""


@router.get("/{entity_type}/{slug}", response_model=DocSourcesResponse)
def list_doc_sources(entity_type: str, slug: str) -> DocSourcesResponse:
    registry = _registry()
    entity = _resolve_entity(registry, entity_type, slug)
    _sync_egeria_read_back(registry, entity_type, slug, entity)
    status = _publish_status(registry, entity_type, slug, entity.egeria_asset_guid or "")
    rows = registry.list_doc_sources(entity_type, slug)
    outs = []
    in_egeria = 0
    for r in rows:
        state, detail = _compute_egeria_state(registry, entity_type, slug, r, status["is_published"])
        out = _out(r)
        out.egeria_state = state
        out.egeria_state_detail = detail
        if state == "catalogued":
            in_egeria += 1
        outs.append(out)
    return DocSourcesResponse(
        sources=outs, published=status["is_published"], publish_note=status["note"],
        in_egeria_count=in_egeria, local_count=len(outs) - in_egeria,
    )


@router.post("/{entity_type}/{slug}", response_model=DocSourceOut)
def add_doc_source(entity_type: str, slug: str, body: DocSourceCreate, request: Request) -> DocSourceOut:
    registry = _registry()
    entity = _resolve_entity(registry, entity_type, slug)
    url = body.url.strip()
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be an http(s) URL")
    if entity.egeria_asset_guid:
        # Brief Z2: on a resource already in Egeria the add queues an ExternalReference link on it.
        from resource_explorer.web.routes.curate import require_resource_write
        require_resource_write(registry, entity_type, slug, "link")

    # Signed-in identity, stamped server-side the same way `save_field` stamps
    # an enrichment author -- read from the request here, on the request's own
    # thread, before any background work. The row's `added_by` was never set
    # at all (the route did not pass it), so every declared source was
    # unsigned; this is not the bare-thread ContextVar drop, the identity was
    # simply never read. Anonymous callers stay unsigned ('') and the display
    # says "added by unknown".
    user = get_current_user(request) or {}
    added_by = user.get("user_id") or user.get("sub") or user.get("username") or ""
    row = registry.add_doc_source(entity_type, slug, url, label=body.label,
                                   source_type=body.source_type, added_by=added_by)
    # Probe immediately on add (the brief's step 2) — synchronous, so the
    # response already carries the state; the gate's "within five seconds"
    # is this call's own latency, not a follow-up poll.
    result = run_probe(url)
    row = registry.record_doc_source_probe(
        entity_type, slug, row["id"], state=result.state, status_code=result.status_code,
        elapsed_ms=result.elapsed_ms, title=result.title, byte_count=result.byte_count,
        error=result.error,
    )
    try:
        from resource_explorer.activity_logger import log_catalog

        log_catalog(
            registry, entity_type=entity_type, entity_slug=slug,
            entity_name=slug, entity_location=url, status="ok",
            summary=f"Declared documentation source: {body.label or url} ({result.state})",
        )
    except Exception:
        pass
    # Egeria publish-state fix: if the resource is ALREADY published, queue
    # this source's Egeria publish right now — _compute_egeria_state's
    # self-heal branch does the enqueue (no outbox row exists yet for a
    # brand-new source), so the response already carries "publishing" rather
    # than a silent local-only gap that would only close on the resource's
    # next full re-publish.
    status = _publish_status(registry, entity_type, slug, entity.egeria_asset_guid or "")
    state, detail = _compute_egeria_state(registry, entity_type, slug, row, status["is_published"])
    out = _out(row)
    out.egeria_state = state
    out.egeria_state_detail = detail
    return out


@router.post("/{entity_type}/{slug}/{source_id}/recheck", response_model=DocSourceOut)
def recheck_doc_source(entity_type: str, slug: str, source_id: str) -> DocSourceOut:
    registry = _registry()
    entity = _resolve_entity(registry, entity_type, slug)
    row = registry.get_doc_source(entity_type, slug, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="documentation source not found")
    result = run_probe(row["url"])
    row = registry.record_doc_source_probe(
        entity_type, slug, source_id, state=result.state, status_code=result.status_code,
        elapsed_ms=result.elapsed_ms, title=result.title, byte_count=result.byte_count,
        error=result.error,
    )
    status = _publish_status(registry, entity_type, slug, entity.egeria_asset_guid or "")
    state, detail = _compute_egeria_state(registry, entity_type, slug, row, status["is_published"])
    out = _out(row)
    out.egeria_state = state
    out.egeria_state_detail = detail
    return out


@router.delete("/{entity_type}/{slug}/{source_id}")
def remove_doc_source(entity_type: str, slug: str, source_id: str) -> dict:
    registry = _registry()
    _resolve_entity(registry, entity_type, slug)
    existing = registry.get_doc_source(entity_type, slug, source_id)
    if existing and existing.get("egeria_external_ref_guid"):
        # Brief Z2: the removal queues the ExternalReference's detach in Egeria; checked BEFORE the local row goes.
        from resource_explorer.web.routes.curate import require_resource_write
        require_resource_write(registry, entity_type, slug, "unlink")
    row = registry.remove_doc_source(entity_type, slug, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="documentation source not found")
    # Removal deletes the local row AND queues the Egeria-side detach+delete
    # of the ExternalReference it created — only that one (the brief's
    # "Removal" rule) — through the SAME outbox/retry mechanism the publish
    # side uses (Egeria publish-state fix, 2026-09-29), rather than the
    # previous one-shot synchronous attempt with no retry if Egeria was
    # unreachable at that exact moment. The local row is already gone by the
    # time this runs, so there is nothing left to roll back if the Egeria
    # side fails or is slow — that's what the outbox's retry is for.
    egeria_unpublish = "not_applicable"
    if row.get("egeria_external_ref_guid"):
        element_id = enqueue_doc_source_unpublish(
            registry, entity_type, slug, row["egeria_external_ref_guid"])
        _attempt_outbox_row_immediately(element_id)
        egeria_unpublish = "queued"
    return {"removed": True, "id": source_id, "egeria_unpublish": egeria_unpublish}


def publish_local_doc_sources(entity_type: str, slug: str, asset_guid: str, *,
                               registry: ProjectRegistry | None = None) -> list[dict]:
    """Publish every locally-declared source with no Egeria reference yet
    as an `ExternalReference` on `asset_guid`. Called from the
    database/filesystem publish routes right after a successful publish, and (Brief F §10) from a repository's
    single Publish (`single_publish.send_with_publish`) —
    see `web/routes/databases.py`'s `publish_database_survey`. Best-effort
    per source; returns the per-source results so the caller can log/report
    which ones failed without the publish itself failing over it.
    """
    from resource_explorer.doc_source_egeria import entity_clients
    from resource_explorer.egeria_clients import current_principal

    registry = registry or _registry()
    entity = _resolve_entity(registry, entity_type, slug)
    # Who: whoever this publish runs as (Brief I) — the signed-in Caller on a route, the daemon on
    # the requester's behalf in a queued run. Built once, BEFORE the loop: no identity is a 401.
    clients = entity_clients(entity, current_principal())
    out = []
    # Brief F §10: the SAME list the page's Publish band shows as "saved · will publish" (one source).
    from resource_explorer.single_publish import unpublished_doc_sources

    to_publish = {row["id"] for row in unpublished_doc_sources(registry, entity_type, slug)}
    for row in registry.list_doc_sources(entity_type, slug):
        if row["id"] not in to_publish:
            out.append({"id": row["id"], "ok": True, "skipped": "already published"})
            continue
        result = publish_doc_source(row, asset_guid, clients=clients, display_name=entity.display_name)
        if result["ok"] and result["ref_guid"]:
            registry.set_doc_source_egeria_ref(entity_type, slug, row["id"],
                                                result["ref_guid"], result.get("link_guid", ""))
        out.append({"id": row["id"], **result})
    return out
