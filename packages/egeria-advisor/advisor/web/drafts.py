"""
Planning-session draft API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
slice 4). Fully self-contained — no dependency on `advisor.web.shared`.

Endpoints:
  GET    /api/drafts                  → active drafts visible to the requester
  GET    /api/drafts/{draft_id}       → a single draft spec (Plan Canvas)
  PATCH  /api/drafts/{draft_id}/commands → update commands/answers in a draft
  DELETE /api/drafts/{draft_id}       → discard a draft
  POST   /api/drafts/builder          → create a new blank builder-mode draft
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

router = APIRouter()


@router.get("/api/drafts")
async def list_drafts(request: Request) -> Dict[str, Any]:
    """Return active planning session drafts visible to the requester:
    shared + their own namespace, or every namespace for a curator role."""
    from advisor.governance_draft import list_visible_drafts
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    return {"drafts": list_visible_drafts(user_id=user_id, role=role)}


@router.get("/api/drafts/{draft_id}")
async def get_draft(request: Request, draft_id: str) -> Dict[str, Any]:
    """Return a single draft spec by ID (for the Plan Canvas).

    Self-heals doc_id via resolve_live_doc_id() before returning — every
    frontend consumer of this endpoint (Plan Canvas's open(), the Active
    Drafts sidebar) gets a repaired pointer automatically, with no
    frontend-side staleness handling required.

    Ownership-checked: 404 (not 403) for a draft in another user's
    namespace, unless the requester is a curator.
    """
    from advisor.governance_draft import resolve_draft
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    resolved = resolve_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id!r} not found")
    dm, spec = resolved
    resolved_doc_id = dm.resolve_live_doc_id(draft_id, spec=spec)
    if resolved_doc_id != spec.get("doc_id"):
        spec["doc_id"] = resolved_doc_id
    return spec


@router.patch("/api/drafts/{draft_id}/commands")
async def patch_draft_commands(request: Request, draft_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Update commands and answers in a draft (called by Plan Canvas on reorder/add/remove/edit).

    Runs the edited command list through validate_commands() with resort=False —
    warnings (dedup, superseded removal, missing-container insertion, etc.) are
    returned to the caller instead of being silently dropped. resort=False is
    required here specifically: this endpoint fires on every drag-reorder, and
    re-sorting by priority would silently undo a manual reorder.

    Ownership-checked like GET /api/drafts/{draft_id} above.
    """
    from advisor.auth import get_current_user
    from advisor.governance_draft import resolve_draft
    from advisor.plan_validator import validate_commands
    user = get_current_user(request)
    edited_by = (user or {}).get("sub")
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    resolved = resolve_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id!r} not found")
    dm, spec = resolved
    warnings: List[str] = []
    if "commands" in body:
        fixed_commands, spec["answers"], warnings = validate_commands(
            body["commands"], spec.get("answers", {}), resort=False
        )
        spec["commands_identified"] = fixed_commands
    if "answers" in body:
        spec["answers"] = body["answers"]
    dm.save(spec)

    # Sync edits to the generated markdown plan document if it exists.
    # resolve_live_doc_id self-heals a stale doc_id (e.g. after an execution
    # renamed the file) — without it, doc_manager.load() below would silently
    # return None and this whole sync would no-op, saving the draft's JSON
    # but never reaching the actual document, with no error surfaced anywhere.
    doc_id = dm.resolve_live_doc_id(draft_id, spec=spec)
    if doc_id:
        try:
            from advisor.governance_docs import get_doc_manager
            from advisor.agents.plan_elicitor import get_plan_elicitor

            doc_manager = get_doc_manager()
            current_content = doc_manager.load(doc_id)
            if current_content:
                # Update answers from commands_identified pre_filled to ensure they match canvas edits
                for cmd in spec["commands_identified"]:
                    answers_key = cmd.get("_answers_key") or cmd["action"]
                    if "pre_filled" in cmd:
                        spec.setdefault("answers", {})[answers_key] = dict(cmd["pre_filled"])

                elicitor = get_plan_elicitor()
                new_content = elicitor._rebuild_command_sequence(spec, current_content)
                synced_doc_id = dm.sync_document(draft_id, spec, new_content, edited_by=edited_by)
                if synced_doc_id:
                    logger.info(f"Regenerated and updated plan document {synced_doc_id} to match canvas edits")
                else:
                    logger.warning(f"Could not sync plan document for draft {draft_id!r} — doc_id unresolved")
            else:
                logger.warning(
                    f"Plan document {doc_id!r} for draft {draft_id!r} could not be loaded "
                    f"even after doc_id resolution — canvas edits were saved to the draft "
                    f"only, not the document."
                )
        except Exception as exc:
            logger.error(f"Failed to update plan document {doc_id} on patch: {exc}", exc_info=True)

    response: Dict[str, Any] = {"status": "ok"}
    if warnings:
        response["warnings"] = warnings
        response["commands"] = spec["commands_identified"]
    return response


@router.delete("/api/drafts/{draft_id}")
async def delete_draft(request: Request, draft_id: str) -> Dict[str, str]:
    """Discard a planning session draft. Ownership-checked (see GET above)."""
    from advisor.governance_draft import resolve_draft
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    resolved = resolve_draft(draft_id, user_id=user_id, role=role)
    if resolved is None:
        return {"status": "not_found"}
    dm, _spec = resolved
    deleted = dm.delete(draft_id)
    return {"status": "ok" if deleted else "not_found"}


@router.post("/api/drafts/builder")
async def create_builder_draft(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new blank draft in builder mode (Plan Editor entry point).

    Body: {title: str, perspective?: str}
    Returns the draft spec with builder_mode=true and an empty command list.
    Namespaced to the signed-in user; anonymous keeps the shared namespace.
    """
    from advisor.governance_draft import create_builder_draft as _create_builder_draft
    from advisor.auth import get_current_user
    user = get_current_user(request)
    user_id = None if not user or user.get("anonymous") else user.get("user_id") or user.get("sub")
    title = (body.get("title") or "Untitled Plan").strip()
    perspective = body.get("perspective")
    return _create_builder_draft(title, perspective, user_id=user_id)
