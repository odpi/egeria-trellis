"""S5 routes: GET /api/can-edit (yes / no / unknown, as the signed-in user) and GET /api/held/{kind}/{label}
(open a held spec or sheet for a new version; refused unless the check says yes)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from trellis_studio import rights
from trellis_studio.auth import require_user
from trellis_studio.routes_drafts import _kind, raise_http

router = APIRouter()


@router.get("/api/can-edit")
async def get_can_edit(request: Request, ref: str, kind: Optional[str] = None) -> Dict[str, Any]:
    user = require_user(request)
    ref = (ref or "").strip()
    k = rights.kind_of(ref, _kind(kind))
    if not ref or k is None:
        raise HTTPException(status_code=400, detail="Give ref (a qualified name or GUID) and, unless the qualified "
                                                    "name says it, kind (spec, report or sheet).")
    return await rights.can_edit(user, request.app.state.settings, k, rights.label_of(ref, k) if "::" in ref else ref)


@router.get("/api/held/{kind}/{label:path}")
async def get_held(request: Request, kind: str, label: str) -> Dict[str, Any]:
    user = require_user(request)
    try:
        return await rights.open_held(user, request.app.state.settings, _kind(kind), label)
    except rights.NotPermitted as exc:
        status = 403 if exc.answer == "no" else 409
        lead = "You can't edit" if exc.answer == "no" else "Studio can't show that you may edit"
        raise HTTPException(status_code=status, detail=f"{lead} {kind} {label!r}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise_http(exc, "Opening it")
