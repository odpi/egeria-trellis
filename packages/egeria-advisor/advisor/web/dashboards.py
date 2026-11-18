"""Dashboard Sheets held in Egeria: list, show, run a placement as a tile, drill down (B5, B7a), and author (B7b).

Every route reads as the signed-in user (their Egeria token); see advisor.egeria_dashboards for the
visibility and cache rules. Only the two /api/dashboards/author routes write, previewed then confirmed.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

router = APIRouter()


def _viewer_creds(request: Request) -> Dict[str, str]:
    from advisor.auth import get_egeria_credentials, refuse_service_account, require_egeria_user
    require_egeria_user(request)
    creds = get_egeria_credentials(request) or {}
    refuse_service_account(creds.get("user_id"))
    return creds


async def _call(fn, *args, **kwargs) -> Dict[str, Any]:
    from advisor import egeria_dashboards as ed
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except ed.SignInRequired as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})
    except ed.TokenRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/api/dashboards")
async def list_dashboards(request: Request, refresh: bool = False) -> Dict[str, Any]:
    """The Dashboard Sheets this user can see: state ok / no_sheets / read_failed, with a message."""
    from advisor import egeria_dashboards as ed
    return await _call(ed.list_sheets, _viewer_creds(request), refresh=refresh)


@router.get("/api/dashboards/{name}")
async def show_dashboard(request: Request, name: str, perspective: Optional[str] = None,
                         refresh: bool = False) -> Dict[str, Any]:
    """One sheet resolved for this user: state ok / not_visible / read_failed. No report is run."""
    from advisor import egeria_dashboards as ed
    return await _call(ed.show_sheet, _viewer_creds(request), name, perspective=perspective or None,
                       refresh=refresh)


@router.post("/api/dashboards/{name}/placements/{position}/run")
async def run_dashboard_placement(request: Request, name: str, position: int,
                                  body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one report placement as this user and return its tile (kpi / kpis / chart / trend / markdown /
    empty / absent), served from this user's result cache when fresh, or stale with its age while one
    background run refreshes it. Body: perspective, output_format and params (this view's ⚙ overrides,
    never saved), refresh (run now). Restricted, missing and text placements are refused with their reason."""
    from advisor import egeria_dashboards as ed
    body = body or {}
    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="params must be an object of parameter name to value")
    return await _call(ed.run_placement, _viewer_creds(request), name, position,
                       perspective=body.get("perspective") or None, output_format=body.get("output_format"),
                       params=params or None, refresh=bool(body.get("refresh")))


@router.post("/api/dashboards/{name}/placements/{position}/detail")
async def run_dashboard_detail(request: Request, name: str, position: int,
                               body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a placement's drill-down spec (its detail_spec) as this user, as a table."""
    from advisor import egeria_dashboards as ed
    body = body or {}
    return await _call(ed.run_detail, _viewer_creds(request), name, position,
                       perspective=body.get("perspective") or None, refresh=bool(body.get("refresh")))


# ── authoring (brief B7b): preview, then confirm, then write, as this user ────

@router.post("/api/dashboards/author/plan")
async def plan_dashboard_change(request: Request, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Preview a change, writing nothing: every action it would make, as this user, plus a plan id.
    Body: {"kind": "save" | "create_sheet" | "remove" | "move" | "delete_sheet", "request": {...}}."""
    from advisor import egeria_dashboards_author as author
    body = body or {}
    req = body.get("request")
    if not isinstance(body.get("kind"), str) or not isinstance(req, dict):
        raise HTTPException(status_code=422, detail="kind (text) and request (an object) are required")
    return await _call(author.plan, _viewer_creds(request), body["kind"], req)


@router.post("/api/dashboards/author/apply")
async def apply_dashboard_change(request: Request, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write a previewed change, once. Body: {"plan_id", "confirm"}: true, or the sheet's name to delete it."""
    from advisor import egeria_dashboards_author as author
    body = body or {}
    return await _call(author.apply, _viewer_creds(request), str(body.get("plan_id") or ""),
                       confirm=body.get("confirm"))
