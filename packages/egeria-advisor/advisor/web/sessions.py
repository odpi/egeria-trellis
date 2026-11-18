"""
Session-transcript API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
slice 3). Fully self-contained — no dependency on `advisor.web.shared`.

Endpoints:
  GET /api/sessions              → the requester's planning session transcripts (an admin: all)
  GET /api/sessions/{session_id} → one transcript, 404 unless the requester may see it

Namespaced per user (SS-4, A-I1): see advisor/session_logger.py.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions(request: Request) -> Dict[str, Any]:
    """The requester's planning session transcripts, newest first (an admin sees every user's)."""
    from advisor.session_logger import get_session_logger
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    return {"sessions": get_session_logger().list_sessions(user_id=user_id, role=role)}


@router.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> Dict[str, Any]:
    """The full transcript for a planning session: 404 when it doesn't exist or isn't the requester's."""
    from advisor.session_logger import get_session_logger
    from advisor.web.ownership import requester
    user_id, role = requester(request)
    entries = get_session_logger().load_session(session_id, user_id=user_id, role=role, all_users=False)
    if not entries:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return {"session_id": session_id, "entries": entries}
