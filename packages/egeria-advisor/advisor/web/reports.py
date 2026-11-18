"""
Report Spec document/draft API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
slice 6, the last slice). Depends on `advisor.web.shared` for the report
catalog (`_load_report_catalog`, `_catalog_formats`, `_BROWSER_FORMATS`) —
the same helpers `GET /api/reports` always used.

`discover_draft_schema_internal()` and its `_SCHEMA_CACHE` live here rather
than in `shared.py`: this is genuinely reports-domain business logic (report
draft schema discovery against Egeria), not a cross-app-wide helper — its
only other consumer is `advisor/web/templates.py`'s `GET
/api/templates/Column/fields`, which imports it from here (closing the
deferred half of slice 3's `templates.py`, left partial for exactly this
reason).

`tests/unit/test_report_spec_planner.py` imports/monkeypatches
`discover_draft_schema_internal`/`_SCHEMA_CACHE`/`get_report_draft_schema`
from this module now (updated as part of this slice — they were previously
imported from `advisor.web.app`, which no longer defines them; a route
handler's bare-name call resolves against its *own* module's globals, so a
test patching the old module path would silently mock nothing here).

Endpoints:
  GET    /api/reports                                     → report spec catalog (+ restricted, used_by)
  GET    /api/reports/restricted                          → this user's zone-restricted specs (B6)
  POST   /api/reports/restricted/{label}/run              → run one restricted spec as the user (B6)
  POST   /api/reports/specs/{label}/publish               → publish to Egeria: preview|validate|process (B6)
  POST   /api/reports/specs/{label}/withdraw              → withdraw from the consumer folder (B6)
  POST   /api/admin/specs/sync                            → start a sync from Egeria (admin, async; B6/G2)
  GET    /api/admin/specs/sync                            → the sync's status (admin)
  GET    /api/reports/docs                                 → inbox/outbox/trash lists
  GET    /api/reports/docs/{doc_id}                         → doc content
  GET    /api/reports/docs/{doc_id}/export                  → download doc markdown
  POST   /api/reports/specs/import                          → import external doc
  PUT    /api/reports/docs/{doc_id}                          → update doc content
  POST   /api/reports/docs/{doc_id}/execute                  → execute a report spec
  POST   /api/reports/specs/{doc_id}/archive                 → save execution result to outbox
  POST   /api/reports/docs/{doc_id}/retry                    → move to inbox + re-execute
  POST   /api/reports/docs/{doc_id}/recover                  → move to inbox, no re-execute
  GET    /api/reports/docs/{doc_id}/versions                 → version history
  POST   /api/reports/docs/{doc_id}/versions/{version_file}/restore → restore a version
  DELETE /api/reports/docs/{doc_id}                          → soft delete
  POST   /api/reports/docs/{doc_id}/restore-trash             → restore from trash
  DELETE /api/reports/docs/{doc_id}/purge                     → permanently delete
  GET    /api/reports/drafts                                 → active report drafts
  GET    /api/reports/drafts/{draft_id}                       → draft details
  GET    /api/reports/drafts/{draft_id}/schema                 → discovered schema attributes
  DELETE /api/reports/drafts/{draft_id}                       → discard a draft
  POST   /api/reports/drafts/builder                          → blank draft for builder canvas
  POST   /api/reports/specs/edit-by-name                      → find/open a spec's editable draft by name
  POST   /api/reports/specs/{doc_id}/edit                      → open a spec's editable draft by doc_id
  PATCH  /api/reports/drafts/{draft_id}/columns                → update draft columns/metadata
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger

from advisor.web.shared import _load_report_catalog, _catalog_formats, _BROWSER_FORMATS

router = APIRouter()


@router.get("/api/reports")
async def list_reports(request: Request, include_dre: bool = False, refresh: bool = False) -> Dict[str, Any]:
    """Return the report spec catalog grouped by topic.

    Beside the shared catalog: `catalog_status` (what was left out and why, B6 hand-off from A-P),
    `restricted` (the zone-restricted specs this signed-in user can see, read as them and merged per
    user; never in the shared cache) and `used_by` (consumer folders per label, from the last sync's
    consumer index). Each says why when it is empty."""
    from starlette.concurrency import run_in_threadpool
    from advisor.web.shared import _load_report_catalog_with_status
    catalog, catalog_status = _load_report_catalog_with_status(include_dre=include_dre)
    total = sum(len(v) for v in catalog.values())
    formats = _catalog_formats(catalog)
    # Where each spec comes from (builtin | egeria | local | user) and what it shadows, plus the
    # Egeria-held status, which always says why when nothing is held (never a bare empty list).
    from advisor.egeria_spec_sync import egeria_held_status, spec_sources
    from advisor import egeria_spec_restricted as rs
    all_sources = spec_sources()
    listed = {n for names in catalog.values() for n in names}
    sources = {n: all_sources.get(n, {"source": "unknown", "shadows": None, "shadowed_by": None})
               for n in listed}
    shadowed = [{"name": n, **v} for n, v in sorted(all_sources.items()) if v.get("shadowed_by")]
    restricted = await run_in_threadpool(_restricted_for, request, refresh)
    used_by = rs.used_by_index()
    return {
        "catalog": catalog,
        "formats": formats,
        "format_labels": dict(_BROWSER_FORMATS),
        "total": total,
        "include_dre": include_dre,
        "sources": sources,
        "shadowed": shadowed,
        "egeria_held": egeria_held_status(),
        "catalog_status": catalog_status,
        "restricted": rs.with_used_by(restricted, used_by),
        "used_by": used_by,
        "publish": _publish_status(),
    }


def _publish_status() -> Dict[str, Any]:
    """What the publish dialog may offer: restricted publish stays off until the live zone proof (D5)."""
    from advisor.egeria_spec_publish import restricted_publish_status
    return {"restricted": restricted_publish_status()}


def _user_creds(request: Optional[Request]) -> Optional[Dict[str, str]]:
    from advisor.auth import get_egeria_credentials
    return get_egeria_credentials(request) if request is not None else None


def _restricted_for(request: Request, refresh: bool) -> Dict[str, Any]:
    """This user's restricted specs, or a state that says why there are none. A refused token is
    "sign in again" in the catalog, not a failed page."""
    from advisor import egeria_dashboards as ed
    from advisor import egeria_spec_restricted as rs
    try:
        return rs.restricted_specs(_user_creds(request), refresh=refresh)
    except ed.TokenRefused as exc:
        return {"state": "not_signed_in", "specs": [], "message": str(exc), "viewer": None, "cached": False,
                "folders": []}


# ── B6: restricted specs, publish, withdraw, the async sync ────────────────────

def _signed_in(request: Request) -> Dict[str, str]:
    from advisor.auth import refuse_service_account, require_egeria_user
    require_egeria_user(request)
    creds = _user_creds(request) or {}
    refuse_service_account(creds.get("user_id"))
    return creds


@router.get("/api/reports/restricted")
async def list_restricted_specs(request: Request, refresh: bool = False) -> Dict[str, Any]:
    """The zone-restricted report specs this user can see (read as them, cached per credential)."""
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_restricted_for, request, refresh)


@router.post("/api/reports/restricted/{label}/run")
async def run_restricted(request: Request, label: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one restricted spec as this user, fetched from Egeria for the call."""
    from starlette.concurrency import run_in_threadpool
    from advisor import egeria_dashboards as ed
    from advisor import egeria_spec_restricted as rs
    body = body or {}
    try:
        return await run_in_threadpool(rs.run_restricted_spec, _signed_in(request), label,
                                       output_format=body.get("output_format") or "TABLE",
                                       params=body.get("params") or None)
    except ed.TokenRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})


async def _publish_call(fn, request: Request, label: str, body: Dict[str, Any], **kw) -> Dict[str, Any]:
    from starlette.concurrency import run_in_threadpool
    from advisor import egeria_dashboards as ed
    from advisor.egeria_spec_publish import PublishRefused
    creds = _signed_in(request)
    try:
        return await run_in_threadpool(lambda: fn(creds, label, step=body.get("step") or "preview",
                                                  confirmed=bool(body.get("confirmed")),
                                                  digest=body.get("digest"), **kw))
    except PublishRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ed.TokenRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})


@router.post("/api/reports/specs/{label}/publish")
async def publish_spec(request: Request, label: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Publish a local spec to Egeria as this user: step preview | validate | process (process needs
    `confirmed` and the previewed `digest`). Body: {step, visibility: public|restricted, zones,
    also_portal, confirmed, digest}."""
    from advisor.egeria_spec_publish import publish
    body = body or {}
    return await _publish_call(publish, request, label, body, visibility=body.get("visibility") or "public",
                               zones=body.get("zones") or [], also_portal=bool(body.get("also_portal")))


@router.post("/api/reports/specs/{label}/withdraw")
async def withdraw_spec(request: Request, label: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Withdraw a spec from EA's consumer folder (and the Portal's, when named), as this user.
    Body: {step, folders, confirmed, digest}."""
    from advisor.egeria_spec_publish import withdraw
    body = body or {}
    return await _publish_call(withdraw, request, label, body, folders=body.get("folders") or None)


@router.post("/api/admin/specs/sync")
async def start_spec_sync() -> Dict[str, Any]:
    """Start a report-spec sync from Egeria in the background (admin; gated by the /api/admin/ path).
    One at a time: a second start answers 409 "A report spec sync is already running." """
    from advisor.egeria_spec_sync import SyncBusy, start_sync
    try:
        return start_sync()
    except SyncBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/api/admin/specs/sync")
async def spec_sync_status() -> Dict[str, Any]:
    """The sync's state (running | ok | failed | not_synced) and what is held, with its words."""
    from advisor.egeria_spec_sync import sync_status
    return sync_status()


# ── Report Spec Document endpoints ──────────────────────────────────────────────

@router.get("/api/reports/docs")
async def list_report_docs(request: Request) -> Dict[str, Any]:
    """Return inbox, outbox, and trash report spec document lists, annotated
    with active draft IDs. Namespace-scoped like GET /api/plans."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.report_draft import list_visible_report_drafts
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    dm = get_report_spec_doc_manager()
    lists: Dict[str, List[Dict[str, Any]]] = {}
    messages: Dict[str, Optional[str]] = {}
    for folder, fn in (("inbox", dm.list_inbox), ("outbox", dm.list_outbox), ("trash", dm.list_trash)):
        try:
            lists[folder] = fn(requester_user_id=user_id, requester_role=role)
            messages[folder] = None
        except Exception as exc:  # noqa: BLE001 -- stated per folder, never an empty list on its own
            logger.warning(f"report docs: the {folder} could not be read: {exc}")
            lists[folder] = []
            messages[folder] = f"Your report spec {folder} could not be read: {type(exc).__name__}: {exc}"
    inbox = lists["inbox"]

    # Build doc_id -> draft_id map for active report drafts
    doc_to_draft: Dict[str, str] = {}
    try:
        for d in list_visible_report_drafts(user_id=user_id, role=role):
            if d.get("doc_id") and d.get("phase") in ("generate", "refine"):
                doc_to_draft[d["doc_id"]] = d["draft_id"]
    except Exception as exc:  # noqa: BLE001 -- stated
        logger.warning(f"report docs: drafts could not be listed: {exc}")
        messages["drafts"] = f"Open drafts could not be listed, so no document shows its draft: {exc}"

    for entry in inbox:
        entry["draft_id"] = doc_to_draft.get(entry.get("doc_id"))
        _mark_runnable(entry)

    scope = ("shared documents only (sign in to see your own)" if user_id is None
             else "shared documents and your own" + (" and every user's (curator)" if role == "curator" else ""))
    for folder, what in (("inbox", "report spec documents"), ("outbox", "executed reports"),
                         ("trash", "deleted documents")):
        if messages.get(folder) is None and not lists[folder]:
            messages[folder] = f"No {what} here yet; listing {scope}."
    return {"inbox": inbox, "outbox": lists["outbox"], "trash": lists["trash"], "messages": messages,
            "scope": scope}


def _mark_runnable(entry: Dict[str, Any]) -> None:
    """Say when an inbox document is listed but not registered (so it cannot run), and why: the doc
    store logs a failed parse or a taken name and lists the document anyway."""
    from pyegeria.view.base_report_formats import get_report_registry
    doc_id = entry.get("doc_id")
    if not doc_id or get_report_registry().get(doc_id) is not None:
        entry["registered"] = True
        return
    entry["registered"] = False
    why = "it is not registered"
    try:
        from pathlib import Path
        from advisor.report_spec_parser import parse_report_spec_markdown, register_report_spec
        register_report_spec(doc_id, parse_report_spec_markdown(Path(entry["path"]).read_text(encoding="utf-8")))
        entry["registered"] = True
        return
    except Exception as exc:  # noqa: BLE001 -- the words go on the entry
        why = f"{type(exc).__name__}: {exc}"
    entry["not_runnable_reason"] = f"Listed, but it cannot run: {why}"


@router.get("/api/reports/docs/{doc_id}")
async def get_report_doc(request: Request, doc_id: str) -> Dict[str, Any]:
    """Return the content of a report spec document by doc_id (inbox, outbox, or trash).

    Ownership-checked like GET /api/plans/{doc_id}.
    """
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    dm = get_report_spec_doc_manager()
    content = dm.load(doc_id, include_trash=True, requester_user_id=user_id,
                       requester_role=role, enforce_ownership=True)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Report spec {doc_id!r} not found")
    folder = dm.folder_of(doc_id) or "outbox"
    return {"doc_id": doc_id, "content": content, "folder": folder}


@router.get("/api/reports/docs/{doc_id}/export")
async def export_report_doc(doc_id: str) -> Response:
    """Download the full current content of a report spec document (inbox or outbox)."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    dm = get_report_spec_doc_manager()
    content = dm.load(doc_id, include_trash=True)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Report spec {doc_id!r} not found")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{doc_id}.md"'},
    )


@router.post("/api/reports/specs/import")
async def import_report_spec(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Import an externally-written Report Spec markdown document as a new
    managed spec in inbox. Mirrors POST /api/plans/import (namespaced to
    the signed-in user; anonymous keeps the shared namespace).
    """
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.auth import get_current_user
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    title = (body.get("title") or "").strip() or None
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    dm = get_report_spec_doc_manager()
    try:
        doc_id = dm.import_document(content, title=title, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "doc_id": doc_id, "folder": "inbox"}


@router.put("/api/reports/docs/{doc_id}")
async def update_report_doc(doc_id: str, body: Dict[str, str]) -> Dict[str, Any]:
    """Update report spec document content (called by canvas or manual save)."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    dm = get_report_spec_doc_manager()
    content = body.get("content", "")
    ok = dm.update(doc_id, content)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Report spec {doc_id!r} not found in inbox")
    return {"status": "ok"}


@router.post("/api/reports/docs/{doc_id}/execute")
async def execute_report_doc(
    request: Request,
    doc_id: str,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a report spec document and append run outcome.

    Requires login — this performs a live Egeria read and must be
    attributable to the signed-in user, not a shared service account.
    """
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from advisor.agents.report_spec_agent import get_report_spec_agent
    agent = get_report_spec_agent()
    body_data = body or {}
    fmt = body_data.get("output_format", "REPORT")
    params = body_data.get("params")
    dry_run = body_data.get("dry_run", False)
    return agent.execute(
        doc_id,
        dry_run=dry_run,
        output_format=fmt,
        custom_params=params,
        egeria_credentials=egeria_credentials,
    )


@router.post("/api/reports/specs/{doc_id}/archive")
async def archive_report_result(doc_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Save an execution result snapshot to the outbox without re-running the report."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    doc_manager = get_report_spec_doc_manager()
    body_data = body or {}
    content = body_data.get("content", "")
    from datetime import datetime
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    outcome_md = f"## Outcome\n\n**Status:** Completed\n**Saved At:** {ts_str}\n\n{content}\n"
    outbox_id = doc_manager.move_to_outbox(doc_id, outcome_md)
    if not outbox_id:
        return {"ok": False, "error": f"Could not save {doc_id} to outbox"}
    return {"ok": True, "outbox_id": outbox_id}


@router.post("/api/reports/docs/{doc_id}/retry")
async def retry_report_doc(request: Request, doc_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Move an executed report back to inbox and re-execute it.

    Requires login — see execute_report_doc.
    """
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from advisor.agents.report_spec_agent import get_report_spec_agent
    agent = get_report_spec_agent()
    body_data = body or {}
    fmt = body_data.get("output_format", "REPORT")
    return agent.retry(doc_id, output_format=fmt, egeria_credentials=egeria_credentials)


@router.post("/api/reports/docs/{doc_id}/recover")
async def recover_report_doc(doc_id: str) -> Dict[str, Any]:
    """Move an executed report back to inbox (stripping outcomes) without re-running."""
    from advisor.agents.report_spec_agent import get_report_spec_agent
    agent = get_report_spec_agent()
    return agent.recover(doc_id)


@router.get("/api/reports/docs/{doc_id}/versions")
async def get_report_doc_versions(doc_id: str) -> List[Dict[str, str]]:
    """Return version history for a report spec document."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    return get_report_spec_doc_manager().list_versions(doc_id)


@router.post("/api/reports/docs/{doc_id}/versions/{version_file:path}/restore")
async def restore_report_doc_version(doc_id: str, version_file: str) -> Dict[str, str]:
    """Restore a version of a report spec document."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    ok = get_report_spec_doc_manager().restore_version(doc_id, version_file)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to restore version")
    return {"status": "ok"}


@router.delete("/api/reports/docs/{doc_id}")
async def delete_report_doc(request: Request, doc_id: str) -> Dict[str, str]:
    """Soft delete a report spec document. Ownership-checked (see GET above)."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    dm = get_report_spec_doc_manager()
    visible = dm.load(doc_id, requester_user_id=user_id, requester_role=role, enforce_ownership=True)
    if visible is None:
        return {"status": "not_found"}
    deleted = dm.delete(doc_id)
    return {"status": "ok" if deleted else "not_found"}


@router.post("/api/reports/docs/{doc_id}/restore-trash")
async def restore_report_doc_trash(doc_id: str) -> Dict[str, str]:
    """Restore a report spec document from trash."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    restored = get_report_spec_doc_manager().restore_from_trash(doc_id)
    return {"status": "ok" if restored else "not_found"}


@router.delete("/api/reports/docs/{doc_id}/purge")
async def purge_report_doc(doc_id: str) -> Dict[str, str]:
    """Permanently delete a report spec document from trash."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    purged = get_report_spec_doc_manager().purge(doc_id)
    return {"status": "ok" if purged else "not_found"}


# ── Report Spec Draft endpoints ─────────────────────────────────────────────────

@router.get("/api/reports/drafts")
async def list_report_drafts(request: Request) -> List[Dict[str, Any]]:
    """List active report drafts visible to the requester (see GET /api/drafts)."""
    from advisor.report_draft import list_visible_report_drafts
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    return list_visible_report_drafts(user_id=user_id, role=role)


@router.get("/api/reports/drafts/{draft_id}")
async def get_report_draft(request: Request, draft_id: str) -> Dict[str, Any]:
    """Get report spec draft details by draft_id. Ownership-checked (see GET /api/drafts/{id})."""
    from advisor.report_draft import resolve_report_draft
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    resolved = resolve_report_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Report draft {draft_id!r} not found")
    return resolved[1]


_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def _schema_answer(state: str, message: Optional[str], attributes: Optional[List[Dict[str, str]]] = None
                   ) -> Dict[str, Any]:
    return {"state": state, "attributes": attributes or [], "message": message}


async def discover_draft_schema_with_status(
    draft_id: str, egeria_credentials: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Discover a draft's schema, and say why when there is none (A-P P2 hand-off to B6).

    {"state", "attributes", "message"}; state is ok | empty | draft_not_found | spec_invalid |
    connection_unreadable | connection_incomplete | discovery_failed. Only "ok" has attributes;
    every other state has a sentence. A caller without credentials of their own is refused with
    HTTP 401 "Sign in to run reports." (S3b), never a guess.

    Ownership-checked via the ambient request context (advisor.request_context):
    resolves draft_id across the shared root and every namespace exactly like
    the direct REST routes do, honouring the requester's own visibility
    (own namespace + shared, or every namespace for a curator role) — a
    draft namespaced to a different user is treated as not found here too.
    """
    from advisor.report_draft import resolve_report_draft
    from advisor.report_spec_parser import register_report_spec, parse_report_spec_markdown
    from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
    from advisor.report_pipeline import get_report_pipeline
    from advisor.request_context import current_user
    from pyegeria.egeria_tech_client import EgeriaTech
    import copy

    _requester = current_user() or {}
    resolved = resolve_report_draft(draft_id, user_id=_requester.get("user_id"), role=_requester.get("role"))
    draft = resolved[1] if resolved else None
    if not draft:
        logger.warning(f"Draft {draft_id} not found for schema discovery")
        return _schema_answer("draft_not_found", f"Report draft '{draft_id}' was not found, or is not yours to see.")

    # Check cache first
    current_config = copy.deepcopy({
        "action_function": draft.get("action_function"),
        "target_type": draft.get("target_type"),
        "answers": draft.get("answers")
    })
    cached = _SCHEMA_CACHE.get(draft_id)
    if cached:
        time_elapsed = time.time() - cached["timestamp"]
        if time_elapsed < 3600 and cached["draft_config"] == current_config:
            logger.info(f"Returning cached schema for draft {draft_id}")
            return _schema_answer("ok", None, cached["schema_data"])

    try:
        elicitor = get_report_spec_elicitor()
        md_content = elicitor._generate_report_spec_md(draft)
        spec = parse_report_spec_markdown(md_content)
    except Exception as exc:  # noqa: BLE001 -- stated
        logger.warning(f"Failed to parse draft spec in schema discovery: {exc}")
        return _schema_answer("spec_invalid", f"The draft could not be turned into a report spec: {exc}")

    pipeline = get_report_pipeline()
    try:
        conn = pipeline._read_pyegeria_connection(egeria_credentials=egeria_credentials)
    except Exception as exc:  # noqa: BLE001 -- stated
        logger.error(f"Failed to read Egeria connection info: {exc}")
        return _schema_answer("connection_unreadable", f"EA's Egeria connection settings could not be read: {exc}")

    from advisor.egeria_signin import SIGN_IN_TO_RUN_REPORTS, lacks_credential
    if conn and lacks_credential(conn.get("token"), conn.get("user_pwd")):
        # Never reach apply_token with neither: it would mint on pyegeria's
        # configured password under this user id.
        logger.info(f"Schema discovery refused for {conn.get('user_id')!r}: {SIGN_IN_TO_RUN_REPORTS}")
        raise HTTPException(status_code=401, detail=SIGN_IN_TO_RUN_REPORTS)

    from advisor.report_pipeline import _conn_is_complete
    if not _conn_is_complete(conn):
        logger.warning("Egeria connection is not fully configured; skipping schema discovery")
        return _schema_answer("connection_incomplete",
                              "Egeria connection is not configured (config/mcp_servers.json -> pyegeria.env): "
                              "the view server and platform URL are needed to discover a schema.")

    temp_spec_id = f"temp_schema_{draft_id}"
    try:
        register_report_spec(temp_spec_id, spec)
        client = EgeriaTech(
            view_server=conn["view_server"],
            platform_url=conn["platform_url"],
            user_id=conn["user_id"],
            user_pwd=conn["user_pwd"]
        )
        from advisor.auth import apply_token
        apply_token(client, conn.get("token"))

        # Speculative Discovery: query Egeria using client at depth 5
        schema_data = client.get_report_spec_schema(
            report_spec_name=temp_spec_id,
            search_string="*",
            graph_query_depth=5,
            exclude_system_properties=True
        )
    except Exception as e:  # noqa: BLE001 -- stated
        logger.warning(f"Live schema discovery failed on Egeria server: {e}")
        return _schema_answer("discovery_failed", f"Egeria could not be asked for the schema: {e}")
    rows = schema_data if isinstance(schema_data, list) else []
    if len(rows) == 1 and rows[0].get("attribute_path") == "Error":
        return _schema_answer("discovery_failed", f"Egeria could not be asked for the schema: "
                                                  f"{rows[0].get('data_type') or 'no reason given'}")
    if not rows or (len(rows) == 1 and rows[0].get("attribute_path") == "Info"):
        return _schema_answer("empty", "Egeria returned no sample elements to inspect, so no attributes can be "
                                       "discovered until an element of this type exists (and is visible to you).")
    # Store in cache
    _SCHEMA_CACHE[draft_id] = {
        "timestamp": time.time(),
        "draft_config": current_config,
        "schema_data": rows
    }
    return _schema_answer("ok", None, rows)


async def discover_draft_schema_internal(
    draft_id: str, egeria_credentials: Optional[Dict[str, str]] = None
) -> List[Dict[str, str]]:
    """The discovered attributes only (an empty list when there are none). Callers that show the
    answer to someone use `discover_draft_schema_with_status`, which says why."""
    return (await discover_draft_schema_with_status(draft_id, egeria_credentials))["attributes"]


@router.get("/api/reports/drafts/{draft_id}/schema")
async def get_report_draft_schema(request: Request, draft_id: str) -> List[Dict[str, str]]:
    """Return the dynamically discovered schema attributes for a report draft.

    Requires login — this performs a live Egeria read and must be
    attributable to the signed-in user, not a shared service account.
    """
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    answer = await discover_draft_schema_with_status(draft_id, egeria_credentials=egeria_credentials)
    if answer["state"] == "ok":
        return answer["attributes"]
    # The canvas's schema explorer already renders an "Error" row's data_type as "Discovery failed: <why>".
    return [{"attribute_path": "Error", "data_type": answer["message"], "state": answer["state"]}]


@router.delete("/api/reports/drafts/{draft_id}")
async def delete_report_draft(request: Request, draft_id: str) -> Dict[str, str]:
    """Discard an active report spec draft. Ownership-checked (see GET /api/drafts/{id})."""
    from advisor.report_draft import resolve_report_draft
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    resolved = resolve_report_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        return {"status": "not_found"}
    dm, _spec = resolved
    deleted = dm.delete(draft_id)
    return {"status": "ok" if deleted else "not_found"}


@router.post("/api/reports/drafts/builder")
async def create_report_builder_draft(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a blank report spec draft for builder canvas entry point.
    Namespaced to the signed-in user; anonymous keeps the shared namespace."""
    from advisor.report_draft import get_report_draft_manager
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    title = (body.get("title") or "Untitled Report").strip()
    dm = get_report_draft_manager(user_id)
    spec = dm.create(
        title=title,
        original_query=f"[builder] {title}",
        action_function="GlossaryManager.find_glossaries",
        target_type="Glossary",
        columns=[
            {"name": "Display Name", "key": "displayName", "format": False, "detail_spec": None, "formats": "ALL"},
            {"name": "GUID", "key": "guid", "format": True, "detail_spec": None, "formats": "ALL"}
        ],
        answers={
            "Heading": title,
            "Description": "Report built with Report Builder"
        }
    )
    spec["phase"] = "confirm_action"
    spec["phase_label"] = "Building report"
    spec["builder_mode"] = True
    dm.save(spec)
    return spec


@router.post("/api/reports/specs/edit-by-name")
async def edit_spec_by_name(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a report_name (registered spec name), find the matching inbox doc and return
    an editable draft.  Returns {found, draft_id, doc_id} — found=False means the spec
    is built-in (not in the catalog inbox) and the client should offer "Save as spec" instead.
    """
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.report_draft import get_report_draft_manager
    from advisor.report_spec_parser import parse_report_spec_markdown

    report_name = (body.get("report_name") or "").strip()
    if not report_name:
        return {"found": False}

    dm_doc = get_report_spec_doc_manager()
    dm_draft = get_report_draft_manager()

    if report_name.startswith("draft_report_"):
        draft = dm_draft.load(report_name)
        if draft:
            return {"found": True, "draft_id": report_name, "doc_id": draft.get("doc_id")}

    # Search inbox for a spec whose doc_id or title matches report_name
    inbox = dm_doc.list_inbox()
    match = None
    name_lower = report_name.lower().replace("-", " ").replace("_", " ")
    for entry in inbox:
        doc_id = entry.get("doc_id", "")
        title  = (entry.get("title") or "").lower().replace("-", " ").replace("_", " ")
        if doc_id.lower() == name_lower or title == name_lower:
            match = entry
            break

    if not match:
        # Fuzzy: check if report_name appears as a substring of doc_id or title
        for entry in inbox:
            doc_id = entry.get("doc_id", "")
            title  = (entry.get("title") or "").lower()
            if name_lower in doc_id.lower() or name_lower in title:
                match = entry
                break

    if not match:
        return {"found": False}

    doc_id = match["doc_id"]

    # Check if a draft already exists for this doc_id
    for draft in dm_draft.list_drafts():
        if draft.get("doc_id") == doc_id:
            return {"found": True, "draft_id": draft["draft_id"], "doc_id": doc_id}

    # Create a draft from the inbox spec content
    raw = dm_doc.load(doc_id)
    if not raw:
        return {"found": False}

    try:
        parsed = parse_report_spec_markdown(raw)
    except Exception:
        return {"found": False}

    title = parsed.get("heading") or doc_id
    spec = dm_draft.create(
        title=title,
        original_query=f"[edit] {doc_id}",
        action_function=parsed.get("action", {}).get("function", ""),
        target_type=parsed.get("target_type", ""),
        columns=parsed.get("columns_raw", []),
    )
    spec["doc_id"] = doc_id
    spec["phase"] = "refine"
    spec["phase_label"] = "Editing spec"
    dm_draft.save(spec)
    return {"found": True, "draft_id": spec["draft_id"], "doc_id": doc_id}


@router.post("/api/reports/specs/{doc_id}/edit")
async def edit_spec_by_id(doc_id: str) -> Dict[str, Any]:
    """Open an editable draft directly from a catalog doc_id (no name lookup needed)."""
    from advisor.report_spec_docs import get_report_spec_doc_manager
    from advisor.report_draft import get_report_draft_manager
    from advisor.report_spec_parser import parse_report_spec_markdown

    dm_doc   = get_report_spec_doc_manager()
    dm_draft = get_report_draft_manager()

    if doc_id.startswith("draft_report_"):
        draft = dm_draft.load(doc_id)
        if draft:
            return {"found": True, "draft_id": doc_id, "doc_id": draft.get("doc_id")}

    # Return existing draft if one already tracks this doc_id
    for draft in dm_draft.list_drafts():
        if draft.get("doc_id") == doc_id:
            return {"found": True, "draft_id": draft["draft_id"], "doc_id": doc_id}

    raw = dm_doc.load(doc_id)
    if not raw:
        return {"found": False, "error": f"Spec {doc_id!r} not found in catalog"}

    try:
        parsed = parse_report_spec_markdown(raw)
    except Exception as exc:
        return {"found": False, "error": str(exc)}

    columns = []
    for fmt in (parsed.formats or []):
        for col in (fmt.attributes or []):
            columns.append({
                "name": col.name,
                "key": col.key or "",
                "format": col.format if col.format is not None else False,
                "detail_spec": col.detail_spec,
                "formats": "ALL",
            })

    perspectives = []
    questions = []
    if parsed.question_spec:
        for qs in parsed.question_spec:
            perspectives.extend(getattr(qs, "perspectives", []) or [])
            questions.extend(getattr(qs, "questions", []) or [])

    spec = dm_draft.create(
        title=parsed.heading or doc_id,
        original_query=f"[edit] {doc_id}",
        action_function=(parsed.action.function if parsed.action else ""),
        target_type=parsed.target_type or "",
        columns=columns,
        perspectives=perspectives,
        questions=questions,
    )
    spec["doc_id"]  = doc_id
    spec["answers"] = {"Heading": parsed.heading, "Description": parsed.description}
    if parsed.action and parsed.action.spec_params:
        sp = parsed.action.spec_params
        spec["content_filters"]   = {k: v for k, v in sp.items()
                                      if k in ("search_string", "metadata_element_type",
                                               "metadata_element_subtypes", "starts_with",
                                               "ends_with", "ignore_case",
                                               "limit_results_by_status", "governance_zone_filter",
                                               "anchor_type_name", "anchor_domain")}
        spec["shape_defaults"]    = {k: v for k, v in sp.items()
                                      if k in ("sequencing_property", "sequencing_order",
                                               "graph_query_depth", "max_mermaid_node_count",
                                               "skip_relationships", "include_only_relationships")}
        spec["performance_hints"] = {k: v for k, v in sp.items()
                                      if k in ("page_size", "start_from",
                                               "relationship_page_size",
                                               "as_of_time", "effective_time")}
    spec["phase"]       = "refine"
    spec["phase_label"] = "Editing spec"
    dm_draft.save(spec)
    return {"found": True, "draft_id": spec["draft_id"], "doc_id": doc_id}


@router.patch("/api/reports/drafts/{draft_id}/columns")
async def patch_report_draft_columns(request: Request, draft_id: str, body: Dict[str, Any]) -> Dict[str, str]:
    """Update columns and metadata in a report draft (called by Report Canvas edits).

    Ownership-checked: resolves draft_id across the shared root and every
    namespace via resolve_report_draft() — a draft namespaced to another
    user comes back as 404 (never 403) unless the requester is a curator,
    matching every other draft/document route.
    """
    from advisor.report_draft import resolve_report_draft
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    role = (user or {}).get("role")
    resolved = resolve_report_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id!r} not found")
    dm, spec = resolved

    if "columns" in body:
        spec["columns"] = body["columns"]
    if "answers" in body:
        spec["answers"].update(body["answers"])
    if "action_function" in body:
        spec["action_function"] = body["action_function"]
    if "saved_query" in body:
        # Route B (B6/N6): a SavedQuery as the spec's source; null clears it
        if body["saved_query"] is None:
            spec.pop("saved_query", None)
        else:
            from advisor.spec_saved_query import check_saved_query
            try:
                spec["saved_query"] = check_saved_query(body["saved_query"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            spec["action_function"] = None
    if "target_type" in body:
        spec["target_type"] = body["target_type"]
    if "heading" in body:
        spec.setdefault("answers", {})["Heading"] = body["heading"]
    if "perspectives" in body:
        spec["perspectives"] = body["perspectives"]
    if "questions" in body:
        spec["questions"] = body["questions"]
    if "content_filters" in body:
        spec["content_filters"] = body["content_filters"]
    if "shape_defaults" in body:
        spec["shape_defaults"] = body["shape_defaults"]
    if "performance_hints" in body:
        spec["performance_hints"] = body["performance_hints"]
    dm.save(spec)

    # Sync changes back to the generated markdown RSD file if it exists
    doc_id = spec.get("doc_id")
    if doc_id:
        try:
            from advisor.report_spec_docs import get_report_spec_doc_manager
            from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
            doc_manager = get_report_spec_doc_manager()
            elicitor = get_report_spec_elicitor()
            new_content = elicitor._generate_report_spec_md(spec)
            doc_manager.update(doc_id, new_content)
            logger.info(f"Updated report spec document {doc_id} to match canvas edits")
        except Exception as exc:
            logger.error(f"Failed to update report document {doc_id} on patch: {exc}", exc_info=True)

    return {"status": "ok"}
