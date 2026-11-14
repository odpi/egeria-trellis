"""Studio's thin adapter over trellis-auth: the same sign-in as EA and RE, and nothing of its own.

The session JWT is trellis-auth's `{sub, user_id, role, display_name, egeria_token, iat, exp}`, HS256, signed with
the box-wide TRELLIS_JWT_SECRET. Owner decision A (2026-11-14): a Studio session JWT is valid at EA, so the
embedded EA panel runs as the user signed in here with no second login. The JWT travels to EA only by
postMessage, to EA's exact origin (see static/ea_panel.js once S4 lands), never in a URL.

There is no anonymous fallback and no service account: `require_user` raises 401, and `egeria_token_or_401`
refuses before any Egeria client is built.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

import trellis_auth
from trellis_auth import AuthConfig

from trellis_studio.config import StudioSettings

#: Paths that work before sign-in, on top of trellis-auth's defaults (/health, /static/, the auth routes).
#: "/" is the page shell (matched exactly), "/api/auth/policy" tells the page to show its sign-in form.
STUDIO_PUBLIC_PATHS = ("/", "/api/auth/policy")


def auth_config(settings: StudioSettings) -> AuthConfig:
    if not settings.jwt_secret:
        raise RuntimeError(
            "Studio needs TRELLIS_JWT_SECRET (the box-wide secret EA and RE also use). It is not derived or "
            "defaulted: a Studio session must verify at EA (owner decision A), and a made-up secret would not."
        )
    return AuthConfig(
        jwt_secret=settings.jwt_secret,
        jwt_ttl_hours=settings.jwt_ttl_hours,
        portal_secret=settings.portal_secret,
        egeria_view_server=settings.egeria_view_server,
        egeria_platform_url=settings.egeria_platform_url,
    )


def current_user(request: Request) -> Optional[Dict[str, Any]]:
    cfg: AuthConfig = request.app.state.auth_config
    return trellis_auth.get_current_user(request, cfg)


def require_user(request: Request) -> Dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to Studio first.",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def user_id_of(user: Dict[str, Any]) -> str:
    return user.get("user_id") or user.get("sub") or ""


def egeria_token_or_401(user: Dict[str, Any]) -> str:
    token = user.get("egeria_token") or ""
    if not token:
        raise HTTPException(status_code=401, detail="Your Studio session carries no Egeria sign-in. Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    return token
