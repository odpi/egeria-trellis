"""Minimal, fail-closed admin check for RE.

RE has no multi-user authentication system yet (see FeedbackConfig / the
Slice 1 plan). This is deliberately NOT a general auth system — it exists
only to gate the feedback-triage admin endpoints, and is the seam a real
auth system should replace later.

It takes the OPPOSITE default from Egeria Workspaces Portal's
demo_feedback_handler.py::_is_admin(), which returns True when neither
DEMO_MODE nor SERVER_MANAGED_AUTH is set.

**That is a deliberate design there, not a bug, and an earlier version of this
docstring called it one.** Corrected 2026-09-01 by Dan, who owns both: the
Portal has two modes — a public demo requiring an external identity, and a
local mode that relies on Egeria's own users for authentication. Permitting
the request in the unconfigured case is that local mode working as intended,
because the authentication happens in Egeria rather than in the handler.

RE chooses the other default because it has no equivalent: no multi-user
authentication, and nothing behind it that authenticates on its behalf. With
nothing to defer to, absence of admin configuration must deny rather than
permit — the same reasoning reaching the opposite conclusion from different
surroundings, not a correction of anyone.

Recorded at length because the original claim was wrong ABOUT ANOTHER PROJECT
and would have long outlived the conversation that produced it.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from resource_explorer.config import FeedbackConfig


def _signed_in_sub(request: Request) -> str:
    """The signed-in JWT's `sub`, trimmed, or '' when anonymous."""
    from resource_explorer.auth import get_current_user

    user = get_current_user(request)
    return str((user or {}).get("sub") or "").strip()


def is_admin_request(request: Request, cfg: FeedbackConfig) -> bool:
    """True only if the request presents a valid, configured admin credential.

    Recognizes either:
      - header 'X-Admin-Token' matching a non-empty cfg.admin_token (a server-held secret), or
      - the signed-in user's JWT `sub` listed in cfg.admin_users

    Identity comes only from the signed-in session. A client-sent 'X-Admin-User' header is ignored: until
    2026-10-14 it was trusted as sent, so a non-admin who knew an admin's name could claim to be them.

    If neither admin_token nor admin_users is configured, this always
    returns False — fail closed, not fail open.
    """
    token = request.headers.get("X-Admin-Token", "")
    # Constant-time: a plain == lets a remote caller recover the token byte by byte from response timing.
    if cfg.admin_token and token and hmac.compare_digest(token.encode(), cfg.admin_token.encode()):
        return True

    admins = {str(a).strip() for a in (cfg.admin_users or []) if str(a).strip()}
    if admins:
        sub = _signed_in_sub(request)
        if sub and sub in admins:
            return True

    return False


def admin_configured() -> bool:
    """True when this deployment has any admin at all (FEEDBACK_ADMIN_TOKEN or FEEDBACK_ADMIN_USERS set)."""
    from resource_explorer.config import get_config

    cfg = get_config().feedback
    return bool(cfg.admin_token or cfg.admin_users)


def require_admin(request: Request) -> None:
    """Owner ruling (2026-10-14, replacing 2026-10-09's): fail closed when no admin is configured.

    - Anonymous: always 403.
    - Admin configured: only a valid admin credential passes (`is_admin_request`); 403 otherwise.
    - No admin configured: 403 "no admin configured", unless RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true, which brings
      back 2026-10-09's "any signed-in user passes" for local dev only.

    Used by Publish Queue Retry, Run bootstrap now and bulk Egeria Links resolve. Feedback and Curate call
    `is_admin_request` directly and never fail open.
    """
    from resource_explorer.config import get_config

    if not _signed_in_sub(request):
        raise HTTPException(status_code=403, detail="Sign in to do this")
    cfg = get_config().feedback
    if is_admin_request(request, cfg):
        return
    if admin_configured():
        raise HTTPException(status_code=403, detail="Admin credential required")
    if cfg.admin_open_when_unconfigured:
        return
    raise HTTPException(
        status_code=403,
        detail="no admin configured (set FEEDBACK_ADMIN_USERS, or RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true for local dev)",
    )
