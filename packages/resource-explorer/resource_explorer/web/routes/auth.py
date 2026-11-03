"""Login, Portal SSO, and the two reads the login form makes before it has a token.

Mirrors `advisor/web/app.py`'s `/api/auth/*` routes deliberately — one shape,
so a reader who knows one app knows the other and neither can drift on the
contract with the Egeria Portal. See `docs/runtime-architecture-plan.md` §4.

All five routes are on the public allowlist (`auth.RE_PUBLIC_PATHS` plus
`trellis_auth`'s shared defaults): a login form that required a token to
reach could never be used.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class PortalTokenRequest(BaseModel):
    portal_token: str


@router.post("/login")
async def auth_login(req: LoginRequest) -> Dict[str, Any]:
    """Exchange Egeria credentials for RE's session JWT.

    The password is used exactly once, here, to obtain an Egeria bearer token.
    It is never stored and never signed into the JWT (contract, 2026-09-04).
    """
    from resource_explorer.auth import create_access_token, login_with_password
    from resource_explorer import egeria_clients
    from resource_explorer.egeria_clients import (
        TOKEN_REFUSED, TOKEN_UNREACHABLE, DaemonSignInRefused, refuse_daemon_sign_in,
    )

    check_token_accepted = egeria_clients.check_token_accepted   # looked up per call: tests swap it

    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="username and password required")
    try:
        refuse_daemon_sign_in(req.username)          # before any Egeria call (Brief L)
    except DaemonSignInRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    egeria_token = await asyncio.get_event_loop().run_in_executor(
        None, login_with_password, req.username, req.password
    )
    if not egeria_token:
        raise HTTPException(
            status_code=401, detail="Invalid credentials or Egeria is unreachable."
        )
    # Egeria mints a token for any user id, then refuses every request made with it (2026-10-16,
    # 'rover'). One authorized read tells the two apart before a session exists.
    verdict = await asyncio.get_event_loop().run_in_executor(
        None, check_token_accepted, req.username, egeria_token
    )
    if verdict == TOKEN_REFUSED:
        log.warning("auth: Egeria issued a token for %r but refused its first read; login refused",
                    req.username)
        raise HTTPException(
            status_code=401,
            detail=(f"Egeria accepted {req.username}'s password but refuses its requests. "
                    "Sign in as a user this Egeria server knows."),
        )
    token = create_access_token(user_id=req.username, egeria_token=egeria_token)
    reply = {"access_token": token, "token_type": "bearer", "egeria_user": req.username}
    if verdict == TOKEN_UNREACHABLE:
        # Not a refusal: the old behaviour (signed in, live calls may fail), said out loud.
        log.warning("auth: could not check with Egeria that %r's token is honoured; "
                    "login accepted as before", req.username)
        reply["check"] = "could not check with Egeria"
    return reply


@router.post("/portal")
async def auth_portal(req: PortalTokenRequest) -> Dict[str, Any]:
    """Exchange a Portal-issued token for RE's own session JWT.

    The Portal has already signed the user into Egeria; its JWT carries the
    resulting bearer token as `{sub, role, display_name, egeria_token, exp}`.
    We validate that under the shared secret (`RE_PORTAL_SECRET`) and re-sign
    it as our own session, so a Portal user never sees a second login.
    """
    from resource_explorer.auth import (
        create_access_token,
        exchange_portal_token,
        validate_egeria_token,
    )

    from resource_explorer.egeria_clients import DaemonSignInRefused, refuse_daemon_sign_in

    payload = exchange_portal_token(req.portal_token)
    egeria_user = payload.get("sub", "")
    egeria_token = payload.get("egeria_token", "")
    try:
        refuse_daemon_sign_in(egeria_user)
    except DaemonSignInRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None

    # Liveness check, deliberately NOT a gate: the token was minted by someone
    # else, so confirming it works is worth one cheap call — but a briefly
    # unreachable Egeria should degrade SSO to "signed in, live calls may
    # fail", which is exactly what a self-minted token would do anyway.
    ok = await asyncio.get_event_loop().run_in_executor(
        None, validate_egeria_token, egeria_token
    )
    if not ok:
        log.warning(
            "auth: Portal token for %r did not validate against Egeria; issuing the "
            "session anyway (live calls may 401)", egeria_user,
        )

    token = create_access_token(
        user_id=egeria_user,
        egeria_token=egeria_token,
        role=payload.get("role", "user"),
        display_name=payload.get("display_name") or egeria_user,
    )
    return {"access_token": token, "token_type": "bearer", "egeria_user": egeria_user}


@router.get("/me")
async def auth_me(request: Request) -> Dict[str, Any]:
    """Who this request is, if anyone. Public — answers `{authenticated: false}`."""
    from resource_explorer.auth import get_current_user

    user = get_current_user(request)
    if user is None:
        return {"authenticated": False}
    user_id = user.get("user_id") or user.get("sub", "")
    return {
        "authenticated": True,
        "user_id": user_id,
        "egeria_user": user_id,
        "role": user.get("role", "user"),
        "display_name": user.get("display_name") or user_id,
    }


@router.post("/logout")
async def auth_logout(request: Request) -> Dict[str, str]:
    """Client-side logout. The session is a signed token the server does not track; the one thing it does hold
    for a session, the person's own database credentials for table previews (in memory only), is dropped here."""
    from resource_explorer import preview_credential_holder
    from resource_explorer.auth import get_current_user

    # The request must carry the bearer (the browser sends it BEFORE it forgets the token); without it there is no
    # identity here and nothing to drop. What is dropped is what THIS sign-in held; another device's is its own.
    claims = get_current_user(request)
    person = (claims or {}).get("user_id") or (claims or {}).get("sub") or ""
    if person:
        header = request.headers.get("Authorization", "")
        token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        preview_credential_holder.clear_session(person, preview_credential_holder.session_key(claims, token))
    return {"status": "ok"}


@router.get("/defaults")
async def auth_defaults() -> Dict[str, Any]:
    """The login-form prefill: `RE_LOGIN_DEFAULT_USER` when set, else empty.

    Never the daemon's userId (Brief L): `EGERIA_USER_ID` is RE's own service account, and
    prefilling it would invite people to sign in as the daemon. Never a password either: this
    endpoint is public by necessity.
    """
    from resource_explorer.config import get_config
    from resource_explorer.egeria_clients import is_daemon_user

    user = (getattr(get_config().egeria, "login_default_user", "") or "").strip()
    return {"username": "" if is_daemon_user(user) else user}


@router.get("/policy")
async def auth_policy() -> Dict[str, Any]:
    """The active login policy, so the SPA knows what to show before it has a token.

    Public deliberately, and it discloses nothing an unauthenticated caller
    could not learn by making one request and reading the 401.
    """
    from resource_explorer.auth import get_policy

    policy = get_policy()
    return {
        "login_required": policy.require_login and not policy.anonymous_read,
        "anonymous_read": policy.anonymous_read,
    }
