"""S3 routes: history, published versions, diff, and the publish / promote / withdraw plans with their steps.
Errors as in routes_drafts; a plan whose digest no longer matches the previewed one is 409 "preview it again"."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from trellis_studio import lifecycle
from trellis_studio.auth import require_user
from trellis_studio.routes_drafts import _kind, raise_http

router = APIRouter()


@router.get("/api/consumers")
async def get_consumers(request: Request) -> Dict[str, Any]:
    require_user(request)
    return lifecycle.consumers()


@router.get("/api/history")
async def get_history(request: Request, kind: str, label: str) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await lifecycle.history(user, request.app.state.settings, _kind(kind), label)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Reading the history")


@router.get("/api/published-versions")
async def get_published_versions(request: Request, label: str) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await lifecycle.published_versions(user, request.app.state.settings, label)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Reading the published versions")


class DiffRequest(BaseModel):
    draft: Optional[Dict[str, Any]] = None
    label: Optional[str] = None
    a_version: Optional[int] = None
    b_version: Optional[int] = None


@router.post("/api/diff")
async def post_diff(request: Request, body: DiffRequest) -> Dict[str, Any]:
    user = require_user(request)
    if body.draft is None and not (body.label and body.a_version and body.b_version):
        raise HTTPException(status_code=400, detail="Give a draft (against what Egeria holds), or a spec label and "
                                                    "two published versions.")
    try:
        return await lifecycle.diff(user, request.app.state.settings, draft=body.draft, label=body.label,
                                    a_version=body.a_version, b_version=body.b_version)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Comparing")


class PlanRequest(BaseModel):
    op: Literal["publish", "promote", "withdraw"]
    draft: Optional[Dict[str, Any]] = None          # publish
    ref: Optional[Dict[str, str]] = None            # promote: {kind, name} of a saved draft
    kind: Optional[str] = None                      # withdraw
    label: Optional[str] = None                     # withdraw
    folders: List[str] = []


class ApplyRequest(PlanRequest):
    step: Literal["validate", "process"]
    digest: Optional[str] = None
    confirmed: bool = False
    confirm_breaking: bool = False


def _inputs(body: PlanRequest) -> Dict[str, Any]:
    if body.op == "publish":
        if body.draft is None:
            raise HTTPException(status_code=400, detail="Publish needs the draft.")
        return {"draft": body.draft, "folders": body.folders}
    if body.op == "promote":
        if not body.ref or not {"kind", "name"} <= set(body.ref):
            raise HTTPException(status_code=400, detail="Promote needs the saved draft's {kind, name}.")
        _kind(body.ref["kind"])
        return {"ref": body.ref, "folders": body.folders}
    if not body.kind or not body.label:
        raise HTTPException(status_code=400, detail="Withdraw needs the kind and label.")
    return {"kind": _kind(body.kind), "label": body.label, "folders": body.folders}


@router.post("/api/lifecycle/plan")
async def post_plan(request: Request, body: PlanRequest) -> Dict[str, Any]:
    user = require_user(request)
    inputs = _inputs(body)
    try:
        return await lifecycle.plan(user, request.app.state.settings, body.op, **inputs)
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Planning")


@router.post("/api/lifecycle/apply")
async def post_apply(request: Request, body: ApplyRequest) -> Dict[str, Any]:
    user = require_user(request)
    inputs = _inputs(body)
    try:
        return await lifecycle.apply(user, request.app.state.settings, body.op, step=body.step, digest=body.digest,
                                     confirmed=body.confirmed, confirm_breaking=body.confirm_breaking, **inputs)
    except lifecycle.StaleDigest as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, f"The {body.step} step")
