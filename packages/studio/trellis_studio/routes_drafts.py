"""S1 routes: the user's drafts, new from a template or prototype, save, validate and preview.

Every route runs as the signed-in user (trellis_studio.authoring). Errors come back in words:
  401  the session is gone, or Egeria refused the user's token
  409  pyegeria.authoring refused (an unknown lookup refusing a write is one)
  422  the draft isn't a valid draft
  502  Egeria couldn't be read or the call failed; never turned into "none" or "not found"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from trellis_studio import authoring
from trellis_studio.auth import require_user

logger = logging.getLogger("trellis_studio")
router = APIRouter()


def raise_http(exc: BaseException, doing: str) -> NoReturn:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (ValidationError,)):
        raise HTTPException(status_code=422, detail=f"That isn't a valid draft: {exc}") from exc
    if authoring.is_refusal(exc):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if authoring.is_auth_failure(exc):
        raise HTTPException(status_code=401, detail="Egeria no longer accepts your sign-in. Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"}) from exc
    if isinstance(exc, authoring.StudioReadFailed):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.warning("studio: %s failed: %s: %s", doing, type(exc).__name__, str(exc)[:300])
    raise HTTPException(status_code=502, detail=f"{doing} failed ({type(exc).__name__}). Nothing is assumed "
                                                f"from a failed call: try again, or ask an administrator.") from exc


def _kind(kind: Optional[str]) -> Optional[str]:
    if kind is not None and kind not in authoring.KINDS:
        raise HTTPException(status_code=400, detail=f"A draft kind is one of {', '.join(authoring.KINDS)}.")
    return kind


class NewDraftRequest(BaseModel):
    new_label: str
    template: Optional[str] = None
    prototype: Optional[Dict[str, str]] = None   # {kind, label}


class DraftBody(BaseModel):
    draft: Dict[str, Any]


class ValidateRequest(DraftBody):
    dr_egeria: bool = False


class PreviewRequest(DraftBody):
    max_rows: int = Field(default=25, ge=1, le=200)
    params: Optional[Dict[str, Any]] = None
    run_placements: bool = False


@router.get("/api/templates")
async def get_templates(request: Request) -> Dict[str, Any]:
    require_user(request)
    return {"templates": authoring.templates()}


@router.get("/api/drafts")
async def get_drafts(request: Request, kind: Optional[str] = None) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await authoring.list_drafts(user, request.app.state.settings, _kind(kind))
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Reading your drafts")


@router.get("/api/drafts/{kind}/{name:path}")
async def get_draft(request: Request, kind: str, name: str) -> Dict[str, Any]:
    user = require_user(request)
    try:
        d = await authoring.load_draft(user, request.app.state.settings, _kind(kind), name)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Reading the draft")
    if d is None:
        # pyegeria read it and found no element by that name in the user's private zone.
        raise HTTPException(status_code=404, detail=f"No {kind} draft named {name!r} is in your private draft zone.")
    return {"draft": d}


@router.post("/api/drafts/new")
async def post_new(request: Request, body: NewDraftRequest) -> Dict[str, Any]:
    user = require_user(request)
    if (body.template is None) == (body.prototype is None):
        raise HTTPException(status_code=400, detail="Give a template or a prototype, not both.")
    if body.prototype is not None and not {"kind", "label"} <= set(body.prototype):
        raise HTTPException(status_code=400, detail="A prototype is {kind, label}.")
    try:
        d = await authoring.new_from(user, request.app.state.settings, template=body.template,
                                     prototype=body.prototype, new_label=body.new_label)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Making the draft")
    return {"draft": d, "saved": False}


@router.put("/api/drafts")
async def put_draft(request: Request, body: DraftBody) -> Dict[str, Any]:
    user = require_user(request)
    try:
        qn = await authoring.save_draft(user, request.app.state.settings, body.draft)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Saving the draft")
    return {"saved": True, "qualified_name": qn,
            "words": "Saved in Egeria, in your private draft zone: only you can see it until it's promoted."}


@router.delete("/api/drafts/{kind}/{name:path}")
async def delete_draft(request: Request, kind: str, name: str) -> Dict[str, Any]:
    user = require_user(request)
    try:
        gone = await authoring.delete_draft(user, request.app.state.settings, _kind(kind), name)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Deleting the draft")
    if not gone:
        raise HTTPException(status_code=404, detail=f"No {kind} draft named {name!r} is in your private draft zone.")
    return {"deleted": True}


@router.post("/api/validate")
async def post_validate(request: Request, body: ValidateRequest) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await authoring.validate(user, request.app.state.settings, body.draft, dr_egeria=body.dr_egeria)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Validating the draft")


@router.post("/api/preview")
async def post_preview(request: Request, body: PreviewRequest) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await authoring.preview(user, request.app.state.settings, body.draft, max_rows=body.max_rows,
                                       params=body.params, run_placements=body.run_placements)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Previewing the draft")


@router.get("/api/reports/lookup")
async def get_report_lookup(request: Request, ref: str) -> Dict[str, Any]:
    """Find a Report for a sheet placement, as the user. 409 carries pyegeria's words when it's absent or when
    Egeria couldn't confirm either way."""
    user = require_user(request)
    if not ref.strip():
        raise HTTPException(status_code=400, detail="Give a Report's qualified name or display name.")
    try:
        out = await authoring.lookup_report(user, request.app.state.settings, ref.strip())
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Looking up the Report")
    if out["by"] == "display name":
        out["notes"].append("Found by display name. The placement is kept by qualified name, which is unique; a "
                            "display name can name more than one Report, and on Egeria's display-name read "
                            "\"none\" is only said when the read reached a confirmed end.")
    return out
