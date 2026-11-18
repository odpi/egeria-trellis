"""
Governance-plan document API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
slice 5). Fully self-contained — no dependency on `advisor.web.shared`.

Endpoints:
  POST   /api/plans/import                              → import an external doc as a new plan
  GET    /api/plans                                      → inbox/outbox/trash lists
  GET    /api/plans/{doc_id}                              → plan content
  GET    /api/plans/{doc_id}/export                       → download plan markdown
  GET    /api/plans/{doc_id}/report-export                → download extracted report content
  PUT    /api/plans/{doc_id}                              → save updated plan content
  POST   /api/plans/{doc_id}/execute                       → show an inbox plan; with {pending_id, digest}, run it
  POST   /api/plans/{doc_id}/validate                      → run Dr.Egeria validate
  POST   /api/plans/{doc_id}/retry                         → move failed outbox plan to inbox + re-execute
  POST   /api/plans/{doc_id}/rerun                         → re-execute an outbox plan in place
  POST   /api/plans/{doc_id}/recover                       → move outbox plan back to inbox (no re-execute)
  GET    /api/plans/{doc_id}/versions                      → list plan versions
  POST   /api/plans/{doc_id}/versions/{version_file}/restore → restore a specific version
  POST   /api/plans/{doc_id}/fork                          → create a new plan seeded from doc_id
  POST   /api/plans/{doc_id}/save-as                        → save current/version content as new plan
  POST   /api/plans/{doc_id}/save-as-template                → save as a reusable template
  DELETE /api/plans/{doc_id}                              → move plan to trash
  POST   /api/plans/{doc_id}/restore-trash                  → restore from trash to inbox
  DELETE /api/plans/{doc_id}/purge                          → permanently delete from trash
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from functools import partial
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter()


def _require_visible_plan(request: Request, doc_id: str) -> None:
    """404 unless the requester can see this plan (its own, shared, or an admin's view; SS-4). Checked
    before any route acts on a plan by id: the engine calls below see every namespace."""
    from advisor.governance_docs import get_doc_manager
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    if get_doc_manager().load(doc_id, include_trash=True, requester_user_id=user_id, requester_role=role,
                              enforce_ownership=True) is None:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")


@router.post("/api/plans/import")
async def import_plan(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Import an externally-written Dr.Egeria/LGCI markdown document as a new
    managed plan in inbox. Detects whether the content is already LGCI-structured
    or a bare Dr.Egeria command file and wraps the latter automatically.

    Written into the signed-in user's namespace (docs/runtime-architecture-plan.md
    §4); an anonymous request keeps today's shared namespace.
    """
    from advisor.governance_docs import get_doc_manager
    from advisor.auth import get_current_user
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    title = (body.get("title") or "").strip() or None
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    dm = get_doc_manager()
    try:
        doc_id = dm.import_document(content, title=title, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "doc_id": doc_id, "folder": "inbox"}


@router.get("/api/plans")
async def list_plans(request: Request) -> Dict[str, Any]:
    """Return inbox, outbox, and trash plan document lists, annotated with active draft IDs.

    A signed-in user sees the shared namespace plus their own; a curator
    role (admin/curator) sees every namespace. Anonymous sees shared only.
    """
    from advisor.governance_docs import get_doc_manager
    from advisor.governance_draft import list_visible_drafts
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    dm = get_doc_manager()
    inbox = dm.list_inbox(requester_user_id=user_id, requester_role=role)
    outbox = dm.list_outbox(requester_user_id=user_id, requester_role=role)
    trash = dm.list_trash(requester_user_id=user_id, requester_role=role)

    # Build doc_id → draft_id map for plans that have an active refine/generate draft
    doc_to_draft: Dict[str, str] = {}
    for d in list_visible_drafts(user_id=user_id, role=role):
        if d.get("doc_id") and d.get("phase") in ("generate", "refine", "template_offer"):
            doc_to_draft[d["doc_id"]] = d["draft_id"]

    for entry in inbox:
        entry["draft_id"] = doc_to_draft.get(entry.get("doc_id"))

    return {"inbox": inbox, "outbox": outbox, "trash": trash}


@router.get("/api/plans/{doc_id}")
async def get_plan(request: Request, doc_id: str) -> Dict[str, Any]:
    """Return the content of a plan document by doc_id (inbox, outbox, or trash).

    Ownership-checked: a namespaced plan belonging to another user comes
    back as 404 (never 403) unless the requester is a curator.
    """
    from advisor.governance_docs import get_doc_manager
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    dm = get_doc_manager()
    content = dm.load(doc_id, include_trash=True, requester_user_id=user_id,
                       requester_role=role, enforce_ownership=True)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")
    folder = dm.folder_of(doc_id) or "outbox"
    return {"doc_id": doc_id, "content": content, "folder": folder}


@router.get("/api/plans/{doc_id}/export")
async def export_plan(request: Request, doc_id: str) -> Response:
    """Download the full current content of a plan document (inbox or outbox)."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    content = dm.load(doc_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{doc_id}.md"'},
    )


@router.get("/api/plans/{doc_id}/report-export")
async def export_plan_report(request: Request, doc_id: str) -> Response:
    """
    Download just the report content (Mermaid diagrams, result tables) extracted
    from an executed plan's Dr.Egeria output — shareable independent of the plan
    that produced it.
    """
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager, DocumentManager
    from advisor.agents.outcome_reporter import _extract_report_sections

    dm = get_doc_manager()
    content = dm.load_outbox(doc_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found in outbox")

    # The raw Dr.Egeria output lives inside the collapsible "## Dr.Egeria Execution
    # Output" section appended by GovernancePlanAgent.execute() — pull it out.
    m = re.search(
        r'<summary>.*?</summary>\n\n(.*?)\n\n</details>',
        content, re.DOTALL,
    )
    raw_output = m.group(1) if m else content
    report_md = _extract_report_sections(raw_output)
    if not report_md:
        raise HTTPException(
            status_code=404,
            detail="No extractable report content (Mermaid diagram or result table) found in this plan's output",
        )

    title = DocumentManager._extract_title(content)
    final = (
        f"# {title} — Report\n\n"
        f"*Generated from plan `{doc_id}` on {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n"
        f"{report_md}\n"
    )
    return Response(
        content=final,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{doc_id}_report.md"'},
    )


@router.put("/api/plans/{doc_id}")
async def save_plan(request: Request, doc_id: str, body: Dict[str, Any]) -> Dict[str, str]:
    """Save updated plan content to inbox (with automatic version backup)."""
    _require_visible_plan(request, doc_id)
    from advisor.auth import get_current_user
    from advisor.governance_docs import get_doc_manager
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    user = get_current_user(request)
    edited_by = (user or {}).get("sub")
    dm = get_doc_manager()
    ok = dm.update(doc_id, content, edited_by=edited_by)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found in inbox")
    return {"status": "ok"}


@router.post("/api/plans/{doc_id}/execute")
async def execute_plan(request: Request, doc_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Execute an inbox plan directly (first execution). Direct REST call —
    deliberately not routed through chat text — since "execute the plan X"
    sent as a chat message can be intercepted by context-based routing
    (e.g. an open Plan Canvas session) and mistakenly treated as a plan-
    modification instruction instead of an execute command. See BACKLOG.md.
    For outbox (already-executed) plans, use retry/rerun instead.

    Optional body.draft_id: if this plan originated from a draft, its doc_id
    gets updated to the new outbox id after execution — otherwise a later
    "resume draft" hands back a doc_id that no longer exists anywhere.

    Requires login — this performs live writes against Egeria and must be
    attributable to the signed-in user, not a shared service account.

    The plan confirm gate: without body.pending_id this runs NOTHING and returns the plan as it would
    run (query_type "plan_confirm", with pending_id and plan_digest). Post again with
    {pending_id, digest} to run it, once, as the same sign-in; a changed plan is refused. A re-run (rerun,
    retry) that repeats non-idempotent commands (`ack_required` true, listed in `non_idempotent`) also needs
    "ack_non_idempotent": true.
    """
    return await _gated_run(request, doc_id, body, action="execute", source_folder="inbox")


@router.post("/api/plans/{doc_id}/validate")
async def validate_plan(request: Request, doc_id: str) -> Dict[str, Any]:
    """Run Dr.Egeria validate directive on the plan's command section.

    Requires login — see execute_plan.
    """
    _require_visible_plan(request, doc_id)
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from advisor.agents.governance_plan_agent import get_governance_plan_agent
    agent = get_governance_plan_agent()
    result = await asyncio.get_event_loop().run_in_executor(
        None, partial(agent.validate, doc_id, egeria_credentials=egeria_credentials)
    )
    return result


@router.post("/api/plans/{doc_id}/retry")
async def retry_plan(request: Request, doc_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Move a failed outbox plan back to inbox and re-execute it, once confirmed.

    Requires login, and the confirm gate: see execute_plan.
    """
    return await _gated_run(request, doc_id, body, action="retry", source_folder="outbox")


@router.post("/api/plans/{doc_id}/rerun")
async def rerun_plan(request: Request, doc_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Re-execute an outbox plan directly, in place — no inbox detour.
    Appends a new "## Outcome (Run N)" section to the same outbox document.

    Requires login, and the confirm gate: see execute_plan.
    """
    return await _gated_run(request, doc_id, body, action="execute", source_folder="outbox")


async def _gated_run(request: Request, doc_id: str, body: Optional[Dict[str, Any]], *,
                     action: str, source_folder: str) -> Dict[str, Any]:
    """Show the plan (no pending_id), or run it once with {pending_id, digest} (the plan confirm gate)."""
    _require_visible_plan(request, doc_id)
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from advisor.agents.governance_plan_agent import get_governance_plan_agent
    agent = get_governance_plan_agent()
    body = body or {}
    draft_id = body.get("draft_id") or None
    pending_id, digest = body.get("pending_id"), body.get("digest")
    ack = body.get("ack_non_idempotent") is True   # a re-run's acknowledgement: exactly true, nothing truthy
    if not pending_id:
        run = partial(agent.prepare_execute, doc_id, source_folder=source_folder, draft_id=draft_id,
                      egeria_credentials=egeria_credentials, action=action)
    elif action == "retry":
        run = partial(agent.retry, doc_id, egeria_credentials=egeria_credentials,
                      pending_id=pending_id, digest=digest, ack_non_idempotent=ack)
    else:
        run = partial(agent.execute, doc_id, source_folder=source_folder, draft_id=draft_id,
                      egeria_credentials=egeria_credentials, pending_id=pending_id, digest=digest,
                      ack_non_idempotent=ack)
    return await asyncio.get_event_loop().run_in_executor(None, run)


@router.post("/api/plans/{doc_id}/recover")
async def recover_plan(request: Request, doc_id: str) -> Dict[str, Any]:
    """Move an outbox plan back to inbox for editing (does NOT re-execute)."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    inbox_doc_id = dm.move_to_inbox(doc_id)
    if not inbox_doc_id:
        raise HTTPException(status_code=409, detail=f"Could not recover {doc_id!r} — it may not be in the outbox, or inbox already has a copy.")
    return {"status": "ok", "doc_id": inbox_doc_id, "folder": "inbox"}


@router.get("/api/plans/{doc_id}/versions")
async def list_plan_versions(request: Request, doc_id: str) -> Dict[str, Any]:
    """List available versions for a plan document."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    versions = dm.list_versions(doc_id)
    return {"doc_id": doc_id, "versions": versions}


@router.post("/api/plans/{doc_id}/versions/{version_file:path}/restore")
async def restore_plan_version(request: Request, doc_id: str, version_file: str) -> Dict[str, Any]:
    """Restore a specific version of a plan to inbox."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    ok = dm.restore_version(doc_id, version_file)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Version {version_file!r} not found")
    return {"status": "ok", "doc_id": doc_id, "restored_from": version_file}


@router.post("/api/plans/{doc_id}/fork")
async def fork_plan(request: Request, doc_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new, independent plan seeded from doc_id (or a specific version
    of it). Known objects (Qualified Name + GUID) from the source's Command
    Results table are carried forward as a reference appendix.
    """
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    version_file = body.get("version_file") or None
    dm = get_doc_manager()
    try:
        new_doc_id = dm.fork(doc_id, title, version_file=version_file)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "ok", "doc_id": new_doc_id, "forked_from": doc_id}


@router.post("/api/plans/{doc_id}/save-as")
async def save_plan_as(request: Request, doc_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save doc_id's current content (or a specific prior version) as a new,
    independent plan — the specification only, no history (unlike fork,
    which carries forward a Known Objects appendix and lineage note).
    """
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    version_file = body.get("version_file") or None
    dm = get_doc_manager()
    try:
        new_doc_id = dm.save_as(doc_id, title, version_file=version_file)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "ok", "doc_id": new_doc_id}


@router.post("/api/plans/{doc_id}/save-as-template")
async def save_plan_as_template(request: Request, doc_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save any existing plan document (inbox or outbox) as a named, reusable
    template — a starting point for new plans, not itself executable.
    Outcome/execution history is stripped first.
    """
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager, strip_outcome_sections
    from advisor.plan_templates import get_template_manager
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    dm = get_doc_manager()
    content = dm.load(doc_id, include_trash=True)
    if not content:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")
    content = strip_outcome_sections(content)
    stem = get_template_manager().save(name, content)
    return {"status": "ok", "template": stem}


@router.delete("/api/plans/{doc_id}")
async def delete_plan(request: Request, doc_id: str) -> Dict[str, Any]:
    """Move a plan document from inbox or outbox to trash (saves a version first). Reversible.

    Ownership-checked: 404 (not 403) for a namespaced plan that isn't the
    requester's own, unless the requester is a curator.
    """
    from advisor.governance_docs import get_doc_manager
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    dm = get_doc_manager()
    visible = dm.load(doc_id, requester_user_id=user_id, requester_role=role, enforce_ownership=True)
    if visible is None:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")
    ok = dm.delete(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not found")
    return {"status": "trashed", "doc_id": doc_id}


@router.post("/api/plans/{doc_id}/restore-trash")
async def restore_plan_from_trash(request: Request, doc_id: str) -> Dict[str, Any]:
    """Restore a plan document from trash back to inbox."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    ok = dm.restore_from_trash(doc_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Plan {doc_id!r} not in trash, or already exists in inbox",
        )
    return {"status": "restored", "doc_id": doc_id}


@router.delete("/api/plans/{doc_id}/purge")
async def purge_plan(request: Request, doc_id: str) -> Dict[str, Any]:
    """Permanently delete a plan document from trash. Version history is preserved."""
    _require_visible_plan(request, doc_id)
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    ok = dm.purge(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Plan {doc_id!r} not in trash")
    return {"status": "purged", "doc_id": doc_id}
