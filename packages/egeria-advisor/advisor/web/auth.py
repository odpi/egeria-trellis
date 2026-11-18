"""
Auth API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
first slice). Follows `admin.py`'s existing pattern: a bare `APIRouter()`
with full paths on each route (no `prefix=` at `include_router` time), fully
self-contained — no dependency on `advisor.web.shared` or any other route
module's state.

Endpoints:
  POST /api/auth/login     → exchange Egeria credentials for a session JWT
  POST /api/auth/portal    → exchange a Portal-issued token for a session JWT
  GET  /api/auth/me        → info about the currently authenticated user
  POST /api/auth/logout    → client-side logout (no server session state)
  GET  /api/auth/defaults  → default Egeria username, for login-form prefill
  GET  /api/auth/policy    → active login policy, for the SPA's login UI
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class PortalTokenRequest(BaseModel):
    portal_token: str


@router.post("/api/auth/login")
async def auth_login(req: LoginRequest) -> Dict[str, Any]:
    """Exchange Egeria credentials for a session JWT.

    The password is used exactly once, here, to obtain an Egeria bearer token;
    it is never stored and never signed into the JWT (contract change
    2026-09-04 — see advisor/auth.py and docs/runtime-architecture-plan.md §4).
    """
    from advisor.auth import login_with_password, create_access_token, refuse_service_account
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="username and password required")
    refuse_service_account(req.username)  # before any Egeria call
    egeria_token = await asyncio.get_event_loop().run_in_executor(
        None, login_with_password, req.username, req.password
    )
    if not egeria_token:
        raise HTTPException(status_code=401, detail="Invalid credentials or Egeria is unreachable.")
    token = create_access_token(user_id=req.username, egeria_token=egeria_token)
    return {"access_token": token, "token_type": "bearer", "egeria_user": req.username}


@router.post("/api/auth/portal")
async def auth_portal(req: PortalTokenRequest) -> Dict[str, Any]:
    """Exchange a Portal-issued short-lived token for a local session JWT.

    The Portal has already logged the user into Egeria and its JWT carries the
    resulting bearer token: {sub, role, display_name, egeria_token, exp}. We
    validate that under the shared secret and re-sign it as our own session —
    no Egeria round-trip on this path beyond the optional cheap validation
    below, and no password anywhere.
    """
    from advisor.auth import (PORTAL_TOKEN_ACCEPTED, PORTAL_TOKEN_UNREACHABLE, check_portal_egeria_token,
                              create_access_token, exchange_portal_token, refuse_service_account)
    payload = exchange_portal_token(req.portal_token)
    egeria_user = payload.get("sub", "")
    refuse_service_account(egeria_user)  # before the Egeria check
    egeria_token = payload.get("egeria_token", "")

    # The token was minted by someone else, so Egeria checks it before a session is wrapped round it
    # (A-I1, 2026-11-11). A token Egeria REJECTS (401, expired, forged, missing) is refused. Egeria
    # briefly unreachable still degrades SSO to "signed in, live features will fail on use", logged.
    verdict, reason = await asyncio.get_event_loop().run_in_executor(
        None, check_portal_egeria_token, egeria_token
    )
    if verdict == PORTAL_TOKEN_UNREACHABLE:
        logger.warning(f"auth: Portal token for {egeria_user!r} not checked, Egeria not reached ({reason}); "
                       "issuing the session (live calls will fail until Egeria answers)")
    elif verdict != PORTAL_TOKEN_ACCEPTED:
        logger.warning(f"auth: refused Portal sign-in for {egeria_user!r}: {reason}")
        raise HTTPException(status_code=401, detail=f"Egeria did not accept the Portal's sign-in for "
                                                    f"{egeria_user!r}: {reason}. Sign in again in the Portal.",
                            headers={"WWW-Authenticate": "Bearer"})

    token = create_access_token(
        user_id=egeria_user,
        egeria_token=egeria_token,
        role=payload.get("role", "user"),
        display_name=payload.get("display_name") or egeria_user,
    )
    return {"access_token": token, "token_type": "bearer", "egeria_user": egeria_user}


@router.get("/api/auth/me")
async def auth_me(request: Request) -> Dict[str, Any]:
    """Return info about the currently authenticated user.

    `is_admin` (Phase 0 S1, 2026-11-10) is the one shared admin gate's answer for this request
    (`advisor.auth.is_admin` -> `trellis_auth.require_admin`), so the UI can hide the Admin link. Cosmetic: the
    server's gate on the admin paths is `trellis_auth.AdminPathMiddleware`."""
    from advisor.auth import get_current_user, is_admin
    user = get_current_user(request)
    if user is None:
        return {"authenticated": False, "is_admin": False}
    user_id = user.get("user_id") or user.get("sub", "")
    return {
        "authenticated": True,
        "is_admin": is_admin(request),
        "user_id": user_id,
        # `egeria_user` kept as a response key for the existing UI; since
        # 2026-09-04 the JWT identifies the user by `sub`/`user_id` and carries
        # their Egeria bearer token rather than a separate egeria_user/password
        # pair, so the two are the same identity.
        "egeria_user": user_id,
        "role": user.get("role", "user"),
        "display_name": user.get("display_name") or user_id,
    }


@router.post("/api/auth/logout")
async def auth_logout() -> Dict[str, str]:
    """Client-side logout — server has no session state to clear."""
    return {"status": "ok"}


@router.get("/api/auth/defaults")
async def auth_defaults() -> Dict[str, Any]:
    """The optional login-form prefill (ADVISOR_LOGIN_DEFAULT_USER), or "".

    Public, so it never names a service account (A-I1): it used to return EGERIA_USER, EA's own
    service account, to anyone. That id is now on the refusal list, and a configured default that is a
    service account is dropped with a warning. Never a password."""
    from advisor.auth import is_service_account
    from advisor.config import settings
    name = (settings.advisor_login_default_user or "").strip()
    if name and is_service_account(name):
        logger.warning(f"auth: ADVISOR_LOGIN_DEFAULT_USER {name!r} is a service account; not offered")
        name = ""
    return {"username": name}


@router.get("/api/auth/policy")
async def auth_policy() -> Dict[str, Any]:
    """The active login policy, so the SPA can decide how to present the form.

    Public, deliberately: the browser needs this *before* it holds a token, and
    it discloses nothing an unauthenticated caller cannot already learn by
    making one request and reading the 401. With `login_required` true the SPA
    shows a non-dismissible login overlay instead of starting up into a page
    whose every panel is a failed fetch.
    """
    from advisor.auth import get_policy
    policy = get_policy()
    return {
        "login_required": policy.require_login and not policy.anonymous_read,
        "anonymous_read": policy.anonymous_read,
    }
