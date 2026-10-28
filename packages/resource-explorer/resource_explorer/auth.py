"""Resource Explorer's thin adapter over the shared `trellis-auth` package.

RE's counterpart to `advisor/auth.py`. Deliberately *thin*, and deliberately
the same shape as EA's — the project has been bitten before by two apps
growing two answers to one question (query cache, annotation properties, the
Portal token contract itself), and "how do I know who is calling" is exactly
that kind of question.

What is here
------------
* `get_policy()` — RE's resolved `trellis_auth.AuthPolicy`
  (`resolve_policy("EXPLORER")`, so `TRELLIS_*` then `EXPLORER_*`), plus RE's
  own public paths.
* `auth_config()` — the `AuthConfig` carrying RE's secrets and Egeria
  connection, resolved from `RE_JWT_SECRET` / `RE_PORTAL_SECRET` (the two the
  A2A role already reads — see `a2a_auth.settings_from_env`) and
  `config.egeria`.
* token mint/decode/exchange and `login_with_password`, all pure delegation.

**One identity mechanism per app.** RE already had a request-scoped caller for
the A2A role (`a2a_auth.CallerIdentity` + the `current_caller` ContextVar).
This module does *not* add a second one: `identity_from_claims()` turns an app
JWT's claims into exactly that `CallerIdentity`, and `IdentityMiddleware` in
`web/app.py` sets the same ContextVar the A2A middleware sets. Downstream code
therefore has one question to ask — `a2a_auth.caller()` — whether the call
arrived over HTTP, over A2A, or from the CLI.

**What is deliberately NOT here**: a service-account fallback that turns an
anonymous request into a live Egeria call as `erinoverview`. The worker role's
own loops build service-account clients directly (bootstrap, resync, outbox
drain — see `egeria_identity.py`); nothing on a request path may quietly
borrow that identity, which is the 2026-08-29 decision that artifact ownership
requires an authenticated identity with no config fallback.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings
from starlette.requests import Request

from resource_explorer.config import _ENV_FILE_CONFIG
from trellis_auth import AuthConfig
from trellis_auth import create_access_token as _create_access_token
from trellis_auth import decode_token as _decode_token
from trellis_auth import exchange_portal_token as _exchange_portal_token
from trellis_auth import get_current_user as _get_current_user
from trellis_auth import login_with_password as _login_with_password
from trellis_auth import validate_egeria_token as _validate_egeria_token

log = logging.getLogger(__name__)
_DERIVED_SECRET_WARNED = False

__all__ = [
    "APP_NAME",
    "POLICY_ENV_PREFIX",
    "RE_PUBLIC_PATHS",
    "auth_config",
    "create_access_token",
    "decode_token",
    "exchange_portal_token",
    "get_current_user",
    "get_egeria_token",
    "get_policy",
    "identity_from_claims",
    "jwt_secret",
    "login_with_password",
    "reset_auth_secrets_cache",
    "reset_policy_cache",
    "validate_egeria_token",
]

#: The console-script name, and the directory under `$XDG_CONFIG_HOME/trellis/`
#: the CLI's cached session lives in.
APP_NAME = "resource-explorer"

#: RE's env-var prefix for `resolve_policy`: `TRELLIS_<NAME>` first, then
#: `EXPLORER_<NAME>`, the app-specific one winning. `EXPLORER_` rather than
#: `RE_` because every other RE deployment knob is already `EXPLORER_*`
#: (`EXPLORER_EMBED_WORKER`, `EXPLORER_RUN_QUEUE_ENABLED`); the two secrets
#: keep their `RE_*` names because the A2A role has read them since it landed
#: and renaming a secret breaks a deployment silently.
POLICY_ENV_PREFIX = "EXPLORER"

#: Public in RE beyond `trellis_auth`'s shared defaults.
#:
#: `/` and `/index.html` serve the SPA shell, which must load in order to
#: *show* the login form — a 401 there is a blank page with nowhere to sign in.
#: The shell holds no data; every `/api/...` call it makes is still challenged.
#:
#: `/.well-known/` is already in the shared defaults and covers the A2A agent
#: cards and discovery index, which must be readable before a client holds a
#: token (see `a2a_auth.is_public_path`, which says the same thing for the
#: separately-mounted A2A app).
#:
#: `/healthz` is RE's second liveness name, used by the A2A role.
#: `/classic` is the Classic shell (explicit since Next became the default at `/`)
#: and is public for the same reason.
#: `/next` is the Next UI's shell, and is public for exactly the same
#: reason `/` is: the shell has to load in order to render the login form that
#: obtains the token. It is a bare HTML document that names no data — every
#: `/api/*` call it makes is gated exactly as the current UI's are. Listed as
#: an exact match, not a `/next/` prefix, so it cannot quietly make some
#: future `/next/export` public too.
RE_PUBLIC_PATHS = (
    "/",
    "/index.html",
    "/next",
    "/classic",
    "/healthz",
    "/api/auth/me",
    "/api/auth/defaults",
    "/api/auth/policy",
)


# ---------------------------------------------------------------------------
# Secrets and connection — RE's own resolution, never trellis_auth's
# ---------------------------------------------------------------------------

class _AuthSecretsConfig(BaseSettings):
    """`.env`-aware source for the four auth env vars below.

    This module used to read all four straight from `os.environ`, bypassing
    pydantic-settings (and therefore `.env`) entirely — the only settings in
    the package that did. That is exactly the bug shape `config.py` documents
    twice over (`_ENV_FILES`'s own comment, ~line 14): a value set only in
    `.env` was silently invisible, no error, just a fallback (a derived
    per-host JWT secret, or Portal SSO silently and wrongly "disabled").
    Found live 2026-09-06 on an install where the operator set RE_JWT_SECRET
    in `.env`, restarted, and the log still said neither var was set.

    Reusing `config.py`'s `_ENV_FILE_CONFIG` (same `_ENV_FILES` tuple, same
    `extra="ignore"`) rather than inventing a second mechanism — this is the
    nested-BaseSettings pattern every other alias-based setting in the
    package already uses, fixed there 2026-08-10 for exactly this reason.
    `load_dotenv()` was considered and rejected: it would be a second,
    parallel way of reading `.env` alongside pydantic-settings' own, and
    this package has already been bitten twice (see `_ENV_FILES`'s comment)
    by two answers to "how does `.env` get read" drifting apart.

    Deliberately NOT one field per secret with `AliasChoices("RE_X",
    "TRELLIS_X")`: pydantic-settings resolves an `AliasChoices` field
    independently *per source* (env vars, then dotenv, ...) and then picks
    the highest-priority source that produced any value at all — so a real
    `TRELLIS_JWT_SECRET` env var would outrank an `RE_JWT_SECRET` set only in
    `.env`, silently inverting the RE-wins-over-TRELLIS precedence this
    module has always documented. Two plain fields, combined by the
    `X or Y` below, keep that precedence explicit in the code instead of
    implicit in alias-resolution order — same shape the raw `os.environ`
    version used, just backed by a source that also reads `.env`.

    Cached at module scope (`_auth_secrets()` below) rather than
    instantiated per call: `auth_config()` runs on every authenticated
    request (`get_current_user`, `decode_token`, ...), and re-reading `.env`
    off disk that often would trade a bypassed-`.env` bug for a per-request
    file read. `reset_auth_secrets_cache()` exists so tests can vary the
    environment, same pattern as `reset_policy_cache()`.
    """
    re_jwt_secret: str = Field(default="", alias="RE_JWT_SECRET")
    trellis_jwt_secret: str = Field(default="", alias="TRELLIS_JWT_SECRET")
    re_portal_secret: str = Field(default="", alias="RE_PORTAL_SECRET")
    trellis_portal_secret: str = Field(default="", alias="TRELLIS_PORTAL_SECRET")
    # Kept as a raw string, not `int`: an unparsable RE_JWT_TTL_HOURS must
    # fall back to 8, not raise at settings-construction time. Parsed in
    # `_jwt_ttl_hours()` below, matching the try/except the raw-os.environ
    # version already had.
    jwt_ttl_hours_raw: str = Field(default="", alias="RE_JWT_TTL_HOURS")

    model_config = _ENV_FILE_CONFIG


_auth_secrets_cache: Optional[_AuthSecretsConfig] = None


def _auth_secrets() -> _AuthSecretsConfig:
    global _auth_secrets_cache
    if _auth_secrets_cache is None:
        _auth_secrets_cache = _AuthSecretsConfig()
    return _auth_secrets_cache


def reset_auth_secrets_cache() -> None:
    """Drop the cached auth secrets — for tests that vary the environment."""
    global _auth_secrets_cache
    _auth_secrets_cache = None


def jwt_secret() -> str:
    """The HS256 secret RE signs its own session JWTs with.

    `RE_JWT_SECRET` then `TRELLIS_JWT_SECRET`, exactly as
    `a2a_auth.settings_from_env` resolves it — the A2A role and the web role
    must agree, or a token minted by the login form would be rejected by the
    agent surface in the same process.

    The fallback derives a stable secret from the hostname, matching EA. It
    means tokens survive a process restart but not a machine migration, which
    is the right trade for a single-host dev checkout and is loudly logged so
    a real deployment sets the variable.
    """
    secrets = _auth_secrets()
    secret = secrets.re_jwt_secret or secrets.trellis_jwt_secret
    if secret:
        return secret
    import hashlib

    machine = os.environ.get("HOSTNAME", "resource-explorer-local")
    global _DERIVED_SECRET_WARNED
    if not _DERIVED_SECRET_WARNED:
        # Once per process: this runs on every request that touches auth, and
        # the same line repeated per request buried the useful log lines.
        _DERIVED_SECRET_WARNED = True
        log.warning(
            "auth: neither RE_JWT_SECRET nor TRELLIS_JWT_SECRET is set — deriving a "
            "per-host secret. Sessions will not survive a move to another machine."
        )
    return hashlib.sha256(f"resource-explorer-{machine}".encode()).hexdigest()


def portal_secret() -> str:
    """The shared HS256 secret the Egeria Portal signs its SSO tokens with."""
    secrets = _auth_secrets()
    return secrets.re_portal_secret or secrets.trellis_portal_secret


def _jwt_ttl_hours() -> int:
    raw = _auth_secrets().jwt_ttl_hours_raw.strip()
    try:
        return max(1, int(raw)) if raw else 8
    except ValueError:
        return 8


def auth_config() -> AuthConfig:
    """RE's `AuthConfig` — secrets plus the Egeria connection.

    Unlike EA there is no cheap/expensive split: RE's Egeria connection comes
    from `config.get_config()`, which is already resolved and cached, so
    carrying the connection fields costs nothing and removes a class of bug
    where a caller picks the config without them and gets a silent
    "connection incomplete" from `login_with_password`.
    """
    # Deliberately unguarded. A config RE cannot read is not a degraded
    # login — Egeria is the identity provider, so there is nothing to sign in
    # against and every route would 401 with a message blaming the user's
    # password. Failing at import, loudly, is the honest outcome; swallowing it
    # would produce an app that looks up and rejects everyone.
    from resource_explorer.config import get_config

    egeria = get_config().egeria
    view_server = egeria.view_server
    platform_url = egeria.platform_url
    service_user = egeria.user_id
    return AuthConfig(
        jwt_secret=jwt_secret(),
        jwt_ttl_hours=_jwt_ttl_hours(),
        portal_secret=portal_secret(),
        egeria_view_server=view_server,
        egeria_platform_url=platform_url,
        egeria_service_account_user=service_user,
    )


# ---------------------------------------------------------------------------
# Login policy — resolved by trellis_auth, not decided here
# ---------------------------------------------------------------------------

_policy_cache = None


def get_policy():
    """RE's resolved `trellis_auth.AuthPolicy`. Cached — the env is read once.

    Cached for the same reason EA caches it: the middleware, the
    `/api/auth/policy` route and any handler that asks must all agree, and
    re-reading the environment per call would let a mid-process change make
    them disagree about the request already in flight.
    """
    global _policy_cache
    if _policy_cache is None:
        from trellis_auth import resolve_policy

        _policy_cache = resolve_policy(
            POLICY_ENV_PREFIX, extra_public_paths=RE_PUBLIC_PATHS,
        )
    return _policy_cache


def reset_policy_cache() -> None:
    """Drop the cached policy — for tests that vary the environment."""
    global _policy_cache
    _policy_cache = None


# ---------------------------------------------------------------------------
# Tokens — pure delegation
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    egeria_token: str,
    role: str = "user",
    display_name: Optional[str] = None,
) -> str:
    """RE's session JWT, carrying the user's Egeria bearer token.

    `exp` is capped at the Egeria token's own expiry by `trellis_auth`, so a
    session never outlives the credential it carries.
    """
    return _create_access_token(
        user_id, egeria_token, auth_config(), role=role, display_name=display_name
    )


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and verify one of our own JWTs. Raises `jwt.PyJWTError`."""
    return _decode_token(token, auth_config())


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """The decoded app JWT for this request, or None. Never raises."""
    return _get_current_user(request, auth_config())


def get_egeria_token(request: Request) -> Optional[str]:
    """The signed-in user's Egeria bearer token, or None when anonymous."""
    user = get_current_user(request)
    if user is None:
        return None
    return user.get("egeria_token") or None


def exchange_portal_token(portal_token: str) -> Dict[str, Any]:
    """Validate the Portal's `{sub, role, display_name, egeria_token, exp}`."""
    return _exchange_portal_token(portal_token, auth_config())


def login_with_password(user_id: str, password: str) -> Optional[str]:
    """Exchange an Egeria user id + password for a bearer token, once."""
    return _login_with_password(user_id, password, auth_config())


def validate_egeria_token(token: str) -> bool:
    """One cheap authenticated view-server call to check a bearer token."""
    return _validate_egeria_token(token, auth_config())


# ---------------------------------------------------------------------------
# The one identity object
# ---------------------------------------------------------------------------

def identity_from_claims(claims: Dict[str, Any]):
    """Turn app-JWT claims into the `CallerIdentity` the whole app already uses.

    Deliberately returns `a2a_auth.CallerIdentity` rather than a second,
    web-shaped identity type. RE has one ContextVar (`a2a_auth.current_caller`)
    and one downstream question (`a2a_auth.caller()`); a parallel web identity
    would mean every call site had to know which door the request came through,
    which is precisely the coupling the ContextVar exists to remove.
    """
    from resource_explorer.a2a_auth import CallerIdentity

    return CallerIdentity(
        user_id=str(claims.get("user_id") or claims.get("sub") or "unknown"),
        egeria_token=claims.get("egeria_token") or None,
        auth_source="app-jwt",
        role=str(claims.get("role") or "user"),
        display_name=str(claims.get("display_name") or ""),
    )
