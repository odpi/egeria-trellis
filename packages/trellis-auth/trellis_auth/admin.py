"""One admin gate for every Trellis app: a configured user list plus an admin token, failing closed.

**Decision (project owner, 2026-11-10, EA review #3):** "one admin definition for RE and EA". This is RE's
`resource_explorer/web/admin_auth.py` semantics, lifted here unchanged so that moving RE onto it (Phase 1) is
mechanical:

* an anonymous request is refused (403 "Sign in to do this"), whatever else it carries;
* otherwise it passes with an ``X-Admin-Token`` equal, by `hmac.compare_digest`, to the configured token, or when
  the signed-in user's JWT ``sub`` is in the configured admin users;
* with an admin configured and neither credential: 403 "Admin credential required";
* with NOTHING configured: 403 "no admin configured". There is no open-when-unconfigured default here (RE's
  ``RE_ADMIN_OPEN_WHEN_UNCONFIGURED`` dev flag stays RE's own until Phase 1 decides it).

A client-sent ``X-Admin-User`` header is never read: identity comes only from the signed-in session (RE learned
this 2026-10-14, when a non-admin who knew an admin's name could claim to be them).

**Configuration** (`resolve_admin`, the only place this module reads the environment, like `resolve_policy`):
``TRELLIS_ADMIN_USERS`` / ``TRELLIS_ADMIN_TOKEN``, overridden by ``<APP>_ADMIN_USERS`` / ``<APP>_ADMIN_TOKEN`` when
those are set and non-empty (``ADVISOR_ADMIN_USERS`` for EA). Users are comma-separated.

**`AdminPathMiddleware`** applies the gate by path, so an app gates a whole prefix in one place and a route added
under it tomorrow is gated by default (fail closed by prefix).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional, Tuple

from starlette.exceptions import HTTPException
from starlette.requests import Request

logger = logging.getLogger(__name__)

__all__ = [
    "ADMIN_TOKEN_HEADER",
    "AdminConfig",
    "AdminPathMiddleware",
    "SIGN_IN_WORDS",
    "ADMIN_REQUIRED_WORDS",
    "is_admin_request",
    "no_admin_configured_words",
    "require_admin",
    "resolve_admin",
    "signed_in_sub",
]

ADMIN_TOKEN_HEADER = "X-Admin-Token"
SIGN_IN_WORDS = "Sign in to do this"
ADMIN_REQUIRED_WORDS = "Admin credential required"


@dataclass(frozen=True)
class AdminConfig:
    """Who is an admin: user ids (JWT ``sub``) and a server-held token. Both empty = no admin configured."""

    users: FrozenSet[str] = frozenset()
    token: str = ""
    #: The env prefix this was resolved for ("ADVISOR"), for the "no admin configured" sentence only.
    app_prefix: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.users or self.token)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def resolve_admin(app_prefix: str) -> AdminConfig:
    """`TRELLIS_ADMIN_*`, then `<APP_PREFIX>_ADMIN_*`; the app's own value wins when it is set and non-empty."""
    prefix = (app_prefix or "").strip().upper().rstrip("_")

    def pick(name: str) -> str:
        specific = _env(f"{prefix}_{name}") if prefix else ""
        return specific or _env(f"TRELLIS_{name}")

    users = frozenset(u.strip() for u in pick("ADMIN_USERS").split(",") if u.strip())
    return AdminConfig(users=users, token=pick("ADMIN_TOKEN"), app_prefix=prefix)


def signed_in_sub(user: Optional[Dict[str, Any]]) -> str:
    """The signed-in user's JWT ``sub`` (else ``user_id``), trimmed; '' when there is none or it is EA's
    anonymous-mode stand-in (``anonymous: True``), which is never a signed-in person."""
    if not isinstance(user, dict) or user.get("anonymous"):
        return ""
    return str(user.get("sub") or user.get("user_id") or "").strip()


def is_admin_request(request: Request, admin: AdminConfig, user: Optional[Dict[str, Any]]) -> bool:
    """True only for a valid, configured admin credential: the token header (constant-time compare: a plain ==
    lets a remote caller recover the token byte by byte from response timing) or the signed-in ``sub``."""
    token = request.headers.get(ADMIN_TOKEN_HEADER, "")
    if admin.token and token and hmac.compare_digest(token.encode(), admin.token.encode()):
        return True
    sub = signed_in_sub(user)
    return bool(admin.users and sub and sub in admin.users)


def no_admin_configured_words(admin: AdminConfig) -> str:
    app = f"{admin.app_prefix}_ADMIN_USERS or " if admin.app_prefix else ""
    return f"no admin configured (set {app}TRELLIS_ADMIN_USERS)"


def require_admin(request: Request, *, app_prefix: str = "", user: Optional[Dict[str, Any]] = None,
                  admin: Optional[AdminConfig] = None) -> None:
    """Raise 403 unless the request is an admin's. `user` is the app's decoded signed-in user (None when
    anonymous); `admin` defaults to `resolve_admin(app_prefix)`, read on every call so an operator's change needs
    no restart and a test can set the environment."""
    admin = admin if admin is not None else resolve_admin(app_prefix)
    if not signed_in_sub(user):
        raise HTTPException(status_code=403, detail=SIGN_IN_WORDS)
    if is_admin_request(request, admin, user):
        return
    if admin.configured:
        raise HTTPException(status_code=403, detail=ADMIN_REQUIRED_WORDS)
    raise HTTPException(status_code=403, detail=no_admin_configured_words(admin))


def _matches(path: str, prefixes: Tuple[str, ...], exact: Tuple[str, ...]) -> bool:
    if path in exact:
        return True
    return any(path.startswith(p) for p in prefixes)


class AdminPathMiddleware:
    """Pure-ASGI gate: every request whose path is under one of `prefixes` (each ending in ``/``, a prefix match)
    or equal to one of `exact` (or `exact` + ``/...``, its sub-resources) must pass `require_admin`, whatever
    its method.

    Pure ASGI for the same reasons as `LoginRequiredMiddleware` (no response wrapping, so streaming endpoints
    elsewhere in the app are untouched). Install it INSIDE the login middleware, so a request with no session
    gets the login 401 first and this 403 only answers signed-in requests. `current_user(request)` is the app's
    own decoder (None when anonymous); anything it raises is a refusal, never a pass.
    """

    def __init__(self, app, *, app_prefix: str, current_user: Callable[[Request], Optional[Dict[str, Any]]],
                 prefixes: Iterable[str] = (), exact: Iterable[str] = ()) -> None:
        self.app = app
        self.app_prefix = app_prefix
        self.current_user = current_user
        self.prefixes = tuple(prefixes)
        self.exact = tuple(e.rstrip("/") for e in exact)
        self._exact_children = tuple(f"{e}/" for e in self.exact)
        if not resolve_admin(app_prefix).configured:
            logger.warning("trellis-auth: no admin configured for %s: every admin path answers 403 "
                           "(set %s_ADMIN_USERS or TRELLIS_ADMIN_USERS)", app_prefix or "this app",
                           app_prefix or "<APP>")

    def gates(self, path: str) -> bool:
        return _matches(path, self.prefixes + self._exact_children, self.exact)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.gates(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            try:
                user = self.current_user(request)
            except Exception:  # noqa: BLE001 - an identity that cannot be read is no identity: refuse
                logger.warning("trellis-auth: admin gate could not read the signed-in user; refusing", exc_info=True)
                user = None
            require_admin(request, app_prefix=self.app_prefix, user=user)
        except HTTPException as exc:
            logger.info("trellis-auth: %s %s %s (%s)", exc.status_code, scope.get("method"), scope.get("path"),
                        exc.detail)
            body = json.dumps({"detail": exc.detail, "error": "admin_required"}).encode("utf-8")
            await send({"type": "http.response.start", "status": exc.status_code,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode("ascii"))]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
