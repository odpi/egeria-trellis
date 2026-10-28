"""Authentication for the `a2a` process role.

`docs/runtime-architecture-plan.md` §2 says the A2A surface "has **no
authentication at all** today"; §4 ("Authentication: two paths, one token")
says both ways into a trellis app must end in the same thing — *a per-request
pyegeria client holding an Egeria bearer token for the actual user*. This
module is that rule applied to agent-to-agent traffic.

Two accepted credentials, one header
------------------------------------
Every call carries ``Authorization: Bearer <token>``. The token is either

* **a trellis app JWT** — minted by ``trellis_auth.create_access_token`` from
  either login path (direct password exchange, or Portal SSO). It is HS256 and
  we hold the secret, so verifying it is local and free; the caller's Egeria
  bearer token rides inside it as the ``egeria_token`` claim.
* **a raw Egeria bearer token** — what the Portal already holds, and what an
  external orchestrator that talks to Egeria directly already has. We do not
  hold Egeria's signing key, so the only honest check is to *use* it:
  ``trellis_auth.validate_egeria_token`` makes one cheap authenticated call
  (``get_my_profile``) against the view server.

The app JWT is tried first because it is the cheap one and it cannot be
mistaken for the other: a token that verifies under our own HS256 secret was
issued by us.

Why the Egeria path is cached
-----------------------------
Validation is a network round trip to the view server. An A2A conversation is
many calls under one token (send, poll the task, subscribe), so validating per
request would multiply every agent call by an Egeria call. The cache is keyed
on the token itself and expires **with the token**: an Egeria bearer token is
an RS256 JWT carrying its own ``exp`` (one hour, measured — see
``trellis_auth.EGERIA_TOKEN_TTL_SECONDS_OBSERVED``), so a cache entry can be
held exactly as long as the credential it describes is valid and no longer.
An opaque token with no readable ``exp`` falls back to a short TTL rather than
being cached forever.

Only *successful* validations are cached. A rejection is not cached at all: a
token can go from invalid to valid (Egeria was briefly down, the user was just
granted access) and caching "no" would pin a transient failure for an hour,
which is the failure mode that is hard to diagnose from the outside.

What the caller's identity is *for*
-----------------------------------
`current_caller` is a ContextVar, set by the middleware for the duration of the
request and inherited by every task and thread the request spawns — the same
shape the Portal uses (``egeria_auth.py``'s contextvar middleware) so both
sides look alike. The downstream half is `egeria_clients.egeria_client(Caller())`
(Brief I, 2026-10-09): the one factory reads this ContextVar, hands every
sub-client the caller's token, and the Egeria write is attributed to the person
rather than to the service account. With no caller it refuses (401); it never
falls back.

**Scope note.** This module makes the caller's token *available* to agent code.
It does not rewrite RE's existing pyegeria client construction sites, which
still build service-account clients — that is RE's trellis-auth adoption (plan
§4, "Isolation matrix"), a larger change than the A2A role, and deliberately
not attempted here. Until it lands, an A2A call is authenticated but its Egeria
writes are still attributed to the service account.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request

log = logging.getLogger(__name__)

#: TTL used when an Egeria bearer token carries no readable `exp` claim (an
#: opaque token, or a differently-configured platform). Short on purpose: the
#: whole point of the cache is to collapse a burst of calls in one
#: conversation, not to hold a credential judgement for an hour we cannot
#: justify from the token itself.
_OPAQUE_TOKEN_CACHE_SECONDS = 300

#: Safety margin subtracted from a token's own `exp` before caching, so a
#: cached "valid" never outlives the credential by a rounding error.
_CACHE_EXPIRY_SKEW_SECONDS = 30

_WWW_AUTHENTICATE = 'Bearer realm="resource-explorer-a2a"'


# ── settings ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class A2AAuthSettings:
    """Everything the middleware needs, resolved once at startup.

    Deliberately not a pydantic `BaseSettings` in `config.py`: `config.py`'s
    nested settings classes are shared by every RE process, and the A2A role is
    the only thing that needs a JWT secret. Resolving here keeps the blast
    radius of a new env var to this file.
    """

    jwt_secret: str
    portal_secret: str = ""
    jwt_algorithm: str = "HS256"
    allow_anonymous: bool = False
    egeria_view_server: str = ""
    egeria_platform_url: str = ""
    egeria_service_account_user: str = ""

    @property
    def auth_configured(self) -> bool:
        """Whether an app JWT can be verified at all."""
        return bool(self.jwt_secret)

    def to_auth_config(self):
        """Build the `trellis_auth.AuthConfig` this package's functions take."""
        from trellis_auth import AuthConfig

        return AuthConfig(
            jwt_secret=self.jwt_secret or "unset-a2a-secret",
            jwt_algorithm=self.jwt_algorithm,
            portal_secret=self.portal_secret,
            egeria_view_server=self.egeria_view_server,
            egeria_platform_url=self.egeria_platform_url,
            egeria_service_account_user=self.egeria_service_account_user,
        )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def settings_from_env() -> A2AAuthSettings:
    """Resolve the role's auth settings from the environment + RE's config.

    `RE_JWT_SECRET` is the app's own; `TRELLIS_JWT_SECRET` is the workspace-wide
    fallback so a deployment that runs RE and EA behind one Portal can set one
    secret. Neither has a default — a generated-per-boot secret would make
    tokens silently stop working across a restart, and a hardcoded one would be
    worse than no auth because it would look like auth.
    """
    from resource_explorer.config import get_config

    cfg = get_config()
    return A2AAuthSettings(
        jwt_secret=os.getenv("RE_JWT_SECRET") or os.getenv("TRELLIS_JWT_SECRET") or "",
        portal_secret=os.getenv("RE_PORTAL_SECRET") or os.getenv("TRELLIS_PORTAL_SECRET") or "",
        allow_anonymous=_env_flag("A2A_ALLOW_ANONYMOUS", False),
        egeria_view_server=cfg.egeria.view_server,
        egeria_platform_url=cfg.egeria.platform_url,
        egeria_service_account_user=cfg.egeria.user_id,
    )


# ── the caller ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallerIdentity:
    """Who is making this A2A call, and with what Egeria credential."""

    user_id: str
    egeria_token: str | None
    auth_source: str  # "app-jwt" | "egeria-token" | "anonymous"
    role: str = "user"
    display_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "display_name": self.display_name or self.user_id,
            "auth_source": self.auth_source,
            "egeria_token_present": bool(self.egeria_token),
        }


ANONYMOUS = CallerIdentity(
    user_id="anonymous", egeria_token=None, auth_source="anonymous", role="anonymous"
)

# Set by the middleware, read by agent code. A ContextVar rather than a
# thread-local because the A2A executor runs the agent in asyncio tasks: a
# task inherits the context it was created in, and `asyncio.to_thread` copies
# it too, so both the async and the sync-bridged call sites see the caller.
try:  # pragma: no cover - contextvars is stdlib on every supported Python
    from contextvars import ContextVar

    current_caller: ContextVar[CallerIdentity | None] = ContextVar(
        "re_a2a_current_caller", default=None
    )
except ImportError:  # pragma: no cover
    raise


def caller() -> CallerIdentity | None:
    """The identity behind the call currently being served, if any."""
    return current_caller.get()


# ── the Egeria-token validation cache ───────────────────────────────────


class _ValidationCache:
    """token -> expiry timestamp, for tokens Egeria has already accepted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, float] = {}

    @staticmethod
    def _key(token: str) -> str:
        # Hash rather than store the credential itself: this dict outlives the
        # request, and a token in a heap dump is a token someone can replay.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def get(self, token: str) -> bool:
        key = self._key(token)
        now = time.time()
        with self._lock:
            expiry = self._entries.get(key)
            if expiry is None:
                return False
            if expiry <= now:
                del self._entries[key]
                return False
            return True

    def put(self, token: str, expires_at: float) -> None:
        with self._lock:
            self._entries[self._key(token)] = expires_at
            # Opportunistic sweep; this dict is bounded by "distinct tokens
            # seen in the last hour", which for an agent surface is small.
            if len(self._entries) > 512:
                now = time.time()
                self._entries = {k: v for k, v in self._entries.items() if v > now}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:  # pragma: no cover - diagnostics only
        with self._lock:
            return len(self._entries)


_validation_cache = _ValidationCache()


def reset_validation_cache() -> None:
    """Drop every cached validation. For tests and for a config reload."""
    _validation_cache.clear()


def _cache_deadline(token: str) -> float:
    from trellis_auth import egeria_token_expiry

    exp = egeria_token_expiry(token)
    if exp is None:
        return time.time() + _OPAQUE_TOKEN_CACHE_SECONDS
    return max(time.time(), float(exp) - _CACHE_EXPIRY_SKEW_SECONDS)


def _subject_of(token: str) -> str:
    """Best-effort user id from an Egeria bearer token's `sub` claim.

    Unverified on purpose, for the same reason `trellis_auth.egeria_token_expiry`
    is: this is a *label*, not the authentication decision. The decision was
    made by Egeria accepting the token.
    """
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return "egeria-user"
    sub = claims.get("sub")
    return str(sub) if sub else "egeria-user"


def validate_egeria_bearer(
    token: str,
    settings: A2AAuthSettings,
    validator: Callable[..., bool] | None = None,
) -> bool:
    """True if Egeria accepts this token; cached for the token's own lifetime.

    `validator` exists so a test can supply its own without patching an import
    inside a hot path — production always leaves it None.
    """
    if not token:
        return False
    if _validation_cache.get(token):
        return True

    if validator is None:
        from trellis_auth import validate_egeria_token as validator  # type: ignore[no-redef]

    ok = bool(validator(token, settings.to_auth_config()))
    if ok:
        _validation_cache.put(token, _cache_deadline(token))
    return ok


# ── authentication ──────────────────────────────────────────────────────


def bearer_token(headers: Any) -> str | None:
    """Pull the bearer token out of an Authorization header mapping."""
    raw = headers.get("Authorization") or headers.get("authorization") or ""
    if not raw.startswith("Bearer "):
        return None
    token = raw[len("Bearer ") :].strip()
    return token or None


def authenticate(
    request: Request,
    settings: A2AAuthSettings,
    validator: Callable[..., bool] | None = None,
) -> CallerIdentity | None:
    """Resolve the caller, or None if the credential is missing or rejected. Raises
    `egeria_clients.DaemonSignInRefused` for a credential naming RE's own daemon account."""
    token = bearer_token(request.headers)
    if not token:
        return None

    config = settings.to_auth_config()

    # 1. A trellis app JWT — verified locally under our own secret.
    if settings.auth_configured:
        from trellis_auth import get_current_user

        payload = get_current_user(request, config)
        if payload is not None:
            # A session for RE's daemon is refused wherever it was minted (EA shares the secret; a
            # pre-merge session or CLI file may name it) — Brief L round 2. Raises
            # DaemonSignInRefused, which the middleware answers with 403.
            from resource_explorer.egeria_clients import refuse_daemon_sign_in

            refuse_daemon_sign_in(str(payload.get("user_id") or payload.get("sub") or ""))
            return CallerIdentity(
                user_id=str(payload.get("user_id") or payload.get("sub") or "unknown"),
                egeria_token=payload.get("egeria_token") or None,
                auth_source="app-jwt",
                role=str(payload.get("role") or "user"),
                display_name=str(payload.get("display_name") or ""),
            )

    # 2. A raw Egeria bearer token — validated against the view server, once. RE's own daemon
    #    account presented as a person is refused BEFORE any Egeria call (Brief L round 2): the
    #    subject is read first, and DaemonSignInRefused is raised (403 from the middleware).
    from resource_explorer.egeria_clients import refuse_daemon_sign_in

    subject = _subject_of(token)
    refuse_daemon_sign_in(subject)
    if validate_egeria_bearer(token, settings, validator=validator):
        return CallerIdentity(
            user_id=subject,
            egeria_token=token,
            auth_source="egeria-token",
        )

    return None


# ── the ASGI middleware ─────────────────────────────────────────────────


def is_public_path(path: str) -> bool:
    """Discovery is unauthenticated; everything else is not.

    An agent card says *what an agent is and how to authenticate to it*. A
    Portal or an external orchestrator has to be able to read that before it
    holds a credential — an A2A surface whose cards are behind the credential
    they describe cannot be discovered at all. The cards carry no project data,
    only the agent's own description, so this is metadata, not content.
    """
    if path in {"/.well-known/agents.json", "/health", "/healthz"}:
        return True
    return path.endswith("/.well-known/agent-card.json") or path.endswith(
        "/.well-known/agent.json"
    )


class A2AAuthMiddleware:
    """Bearer auth in front of every mounted agent app.

    Pure ASGI rather than `BaseHTTPMiddleware` deliberately: A2A's
    `message:stream` and `tasks/{id}:subscribe` are server-sent-event responses,
    and `BaseHTTPMiddleware` buffers through an anyio stream that has repeatedly
    broken long-lived SSE. This wrapper touches the scope and the response
    start, never the body.
    """

    def __init__(self, app: Any, settings: A2AAuthSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        from resource_explorer.egeria_clients import DaemonSignInRefused

        try:
            offered = await self._authenticate(request)
        except DaemonSignInRefused as exc:
            # RE's daemon presented as a caller: 403 in every mode — never anonymous instead.
            await self._forbidden(send, str(exc))
            return
        if self.settings.allow_anonymous:
            # Still resolve an identity when one was offered — anonymous mode
            # lowers the gate, it does not throw away a credential the caller
            # took the trouble to send.
            identity = offered or ANONYMOUS
        else:
            identity = offered
            if identity is None:
                await self._unauthorized(scope, send, path)
                return

        reset = current_caller.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            current_caller.reset(reset)

    async def _authenticate(self, request: Request) -> CallerIdentity | None:
        import asyncio

        # The Egeria branch makes a blocking HTTP call to the view server on a
        # cache miss. Off the event loop, or one unauthenticated caller stalls
        # every in-flight agent run.
        return await asyncio.to_thread(authenticate, request, self.settings)

    async def _forbidden(self, send, detail: str) -> None:
        import json

        body = json.dumps({"error": "forbidden", "detail": detail}).encode("utf-8")
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("latin-1"))]})
        await send({"type": "http.response.body", "body": body})

    async def _unauthorized(self, scope, send, path: str) -> None:
        import json

        log.info(
            "a2a: 401 on %s from %s — no accepted bearer token",
            path,
            (scope.get("client") or ("?",))[0],
        )
        body = json.dumps(
            {
                "error": "unauthorized",
                "detail": (
                    "This endpoint requires an Authorization: Bearer token — either a "
                    "trellis app JWT or a raw Egeria bearer token. Agent cards and "
                    "/.well-known/agents.json are readable without one."
                ),
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", _WWW_AUTHENTICATE.encode("latin-1")),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
