"""
JWT-based authentication for Egeria Advisor — a thin adapter over the shared
`trellis-auth` package.

**Extraction note (2026-08-29):** this used to be a self-contained 255-line
module. The mechanism (JWT create/decode, request-header parsing, Portal
token exchange, live-credential validation) now lives in `trellis_auth` —
shared with Resource Explorer so the two apps can't independently drift on
the *contract with the Portal* (a shared-secret JWT agreement), which is the
kind of divergence this repo has hit before (query cache, annotation
properties, ...). See `docs/trellis-auth-extraction.md`.

**Contract change (2026-09-04): the session JWT carries the user's Egeria
bearer token, never their password.** Previously `create_access_token` signed
the raw Egeria password into the HS256 token and `get_egeria_credentials`
handed `{user_id, password}` to every live-Egeria call site, which then called
`create_egeria_bearer_token(user_id, password)` again on each request. The
Portal never issued the password-shaped token this module's SSO route expected
— it issues `{sub, role, display_name, egeria_token, exp}` — so
`POST /api/auth/portal` was wired to a payload nothing produced. Now:

  * the login form exchanges the password for an Egeria bearer token exactly
    once (`trellis_auth.login_with_password`) and forgets it;
  * `get_egeria_credentials(request)` still returns a dict — same name, same
    call shape, so the ~15 `egeria_credentials=` call sites are unchanged — but
    it is now `{user_id, password: "", token}`. `password` is always empty for
    an authenticated user; `token` is what live call sites must use, via
    `trellis_auth.apply_token(client, token)`.

**Login policy change (2026-09-04): login is required, and the policy is
shared.** Until now this module answered "does EA require login at all?" for
itself, from `advisor.yaml`'s `auth.enabled` / `auth.anonymous_rag_mode`. The
project owner's decision of 2026-09-04 moves that answer into `trellis-auth`
so both apps resolve it identically: `trellis_auth.resolve_policy("ADVISOR")`
reads `TRELLIS_REQUIRE_LOGIN` / `TRELLIS_ANONYMOUS_READ` (then the
`ADVISOR_*` forms, which win), and `LoginRequiredMiddleware` — installed in
`advisor/web/app.py` ahead of the user-context middleware — enforces it before
a route handler ever runs. `_auth_enabled()` and `_anonymous_rag_mode()` remain
as names, but they are now *derived from that policy* rather than independent
switches:

    _auth_enabled()       == policy.require_login
    _anonymous_rag_mode() == policy.anonymous_read

The two `advisor.yaml` keys are retired; a checkout that still carries them
gets one deprecation warning naming the env var that replaced them, and the
policy wins.

See `docs/runtime-architecture-plan.md` §4 and `docs/trellis-auth-extraction.md`.

What stays here, deliberately, because it's EA's own config, not
mechanism:
  * config file locations (`advisor/configdata/advisor.yaml`,
    `mcp_servers.json`) and reading the environment for secrets;
  * `get_current_user`/`require_egeria_user`/`is_authenticated`/
    `get_egeria_credentials` are re-implemented here (each just a few lines)
    on top of this module's own policy-aware `get_current_user` rather than
    delegated straight to `trellis_auth`'s versions of the same names, which
    have no notion of a policy-driven anonymous bypass;
  * `resolve_egeria_credentials`'s service-account fallback — deliberately
    excluded from `trellis_auth` (see its module docstring) per the
    2026-08-29 decision that artifact ownership requires an authenticated
    identity with NO config fallback. That decision is unchanged here: the
    fallback still only fires for an *anonymous* request, and it now yields a
    creds dict with an empty `token`, so a call site can tell "this is the
    service account" from "this is a signed-in person" by looking at `token`.

Public API:
  get_policy() -> trellis_auth.AuthPolicy          -- the shared login policy
  create_access_token(user_id, egeria_token, role=..., display_name=...) -> str
  decode_token(token) -> dict
  get_current_user(request) -> Optional[dict]   -- None for anonymous
  require_egeria_user(request) -> dict           -- raises HTTP 401 if missing
  exchange_portal_token(portal_token) -> dict    -- validates the Portal's real shape
  login_with_password(user_id, password) -> Optional[str]   -- Egeria bearer token
  validate_egeria_token(token) -> bool
  validate_egeria_credentials(user_id, password) -> bool
  get_egeria_token(request) -> Optional[str]
  get_egeria_credentials(request) -> Optional[EgeriaCredentials]  -- None if anonymous
  resolve_egeria_credentials(creds) -> EgeriaCredentials          -- falls back to service account
  apply_token(client, token) -> None
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

from fastapi import HTTPException, Request
from loguru import logger

from trellis_auth import AuthConfig
from trellis_auth import apply_token  # re-exported for live call sites
from trellis_auth import create_access_token as _create_access_token
from trellis_auth import decode_token as _decode_token
from trellis_auth import exchange_portal_token as _exchange_portal_token
from trellis_auth import get_current_user as _get_current_user
from trellis_auth import login_with_password as _login_with_password
from trellis_auth import validate_egeria_credentials as _validate_egeria_credentials
from trellis_auth import validate_egeria_token as _validate_egeria_token

__all__ = [
    "EgeriaCredentials",
    "apply_token",
    "get_policy",
    "reset_policy_cache",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "require_egeria_user",
    "is_authenticated",
    "get_egeria_token",
    "get_egeria_credentials",
    "resolve_egeria_credentials",
    "exchange_portal_token",
    "login_with_password",
    "validate_egeria_token",
    "validate_egeria_credentials",
]


class EgeriaCredentials(TypedDict):
    """What a live-Egeria call site gets for the current request.

    `token` is the Egeria bearer token of the signed-in user — the thing to
    authenticate a pyegeria client with (`apply_token`). `password` is retained
    only so that the one legitimately password-backed path (the .env service
    account, used for anonymous/background work) still has somewhere to put its
    value; it is always `""` for a signed-in user, because the session JWT no
    longer carries a password. `token` empty + `user_id` set means "service
    account", never "signed-in person with no token".
    """

    user_id: str
    password: str
    token: str


# ---------------------------------------------------------------------------
# Configuration helpers — unchanged from before the extraction. Config file
# locations and env-var names are EA's own; trellis_auth never reads either.
# ---------------------------------------------------------------------------

_CFG_PATH  = Path(__file__).parent / "configdata" / "advisor.yaml"
_MCP_PATH  = Path(__file__).parent / "configdata" / "mcp_servers.json"

_cfg_cache: Optional[Dict[str, Any]] = None


def _auth_cfg() -> Dict[str, Any]:
    global _cfg_cache
    if _cfg_cache is None:
        try:
            import yaml
            with open(_CFG_PATH) as f:
                _cfg_cache = yaml.safe_load(f).get("auth", {})
        except Exception:
            _cfg_cache = {}
    return _cfg_cache


def _jwt_secret() -> str:
    # ADVISOR_JWT_SECRET wins, then the workspace-wide TRELLIS_JWT_SECRET,
    # then advisor.yaml. The TRELLIS_* form is not decoration: the trellis
    # compose overlay sets TRELLIS_JWT_SECRET on all three services precisely
    # so a session minted by one container stays valid in another, and until
    # 2026-09-06 EA did not read it -- so every EA container fell through to
    # the per-HOSTNAME derivation below, which in Docker is per-container.
    # The stated purpose of that env var was defeated silently, and on this
    # dev box .env carried a TRELLIS_JWT_SECRET that nothing consumed.
    # resource_explorer/auth.py resolves RE_* then TRELLIS_* the same way.
    secret = (
        os.environ.get("ADVISOR_JWT_SECRET", "")
        or os.environ.get("TRELLIS_JWT_SECRET", "")
        or _auth_cfg().get("jwt_secret", "")
    )
    if not secret:
        # Fallback: derive from machine ID for single-host deployments.
        # This means tokens survive process restart but NOT machine migration
        # -- and under Docker the hostname is per-container, so it does not
        # survive a container recreate either.
        #
        # Warn once per process. Until 2026-09-06 this branch was silent,
        # while docs/deploying-trellis-with-quickstart.md's troubleshooting
        # table told operators that "the apps warn once at start when they
        # fall back to a derived secret" -- true of RE, whose jwt_secret()
        # has done this for a while, and not of EA. Silence here is the
        # reason a TRELLIS_JWT_SECRET sitting unread in .env went unnoticed:
        # there was nothing to see. Same warn-once shape as
        # resource_explorer/auth.py's _DERIVED_SECRET_WARNED, because this
        # runs on every request that touches auth.
        import hashlib
        global _DERIVED_SECRET_WARNED
        if not _DERIVED_SECRET_WARNED:
            _DERIVED_SECRET_WARNED = True
            logger.warning(
                "auth: none of ADVISOR_JWT_SECRET, TRELLIS_JWT_SECRET or "
                "advisor.yaml's auth.jwt_secret is set - deriving a per-host "
                "secret. Sessions will not survive a move to another machine, "
                "nor a container recreate."
            )
        machine = os.environ.get("HOSTNAME", "egeria-advisor-local")
        secret = hashlib.sha256(f"egeria-advisor-{machine}".encode()).hexdigest()
    return secret


def _portal_secret() -> str:
    # Same precedence shape as _jwt_secret above, and as RE's portal_secret().
    return (
        os.environ.get("ADVISOR_PORTAL_SECRET", "")
        or os.environ.get("TRELLIS_PORTAL_SECRET", "")
        or _auth_cfg().get("portal", {}).get("shared_secret", "")
    )


def _jwt_ttl_hours() -> int:
    return int(_auth_cfg().get("jwt_ttl_hours", 8))


# ---------------------------------------------------------------------------
# Login policy — resolved by trellis_auth, not decided here (2026-09-04).
# ---------------------------------------------------------------------------

#: EA's env-var prefix for `resolve_policy`. Every knob is read as
#: `TRELLIS_<NAME>` first and `ADVISOR_<NAME>` second, the app-specific one
#: winning — so `TRELLIS_ANONYMOUS_READ=true` turns the dev override on for
#: every trellis app on the box and `ADVISOR_ANONYMOUS_READ=false` takes EA
#: back out of it.
_POLICY_ENV_PREFIX = "ADVISOR"

#: Public in EA beyond `trellis_auth`'s shared defaults. `/` and `/index.html`
#: serve the SPA shell itself, which must load in order to *show* the login
#: form — a 401 there is a blank page with nowhere to sign in. The shell holds
#: no data; every `/api/...` call it makes is still challenged.
#: `/api/auth/me`, `/api/auth/defaults` and `/api/auth/policy` are the three
#: reads the login form itself performs before a token exists.
#: `/admin` is the same shell-must-load reasoning as `/` -- it just never got
#: added when the login-required policy shipped (2026-09-04), so a direct
#: navigation there 401'd before admin.html's own JS ever ran, even for an
#: already-signed-in user: the initial GET carries no Authorization header
#: (a plain browser navigation can't attach one -- only a fetch() call can),
#: and admin.html's fetch() calls didn't attach one either until this same
#: change (see admin.html's Auth.getHeaders() additions). Every /api/admin/*
#: call it makes is still challenged, same as `/`'s own data calls.
_EA_PUBLIC_PATHS = (
    "/",
    "/index.html",
    "/admin",
    "/api/auth/me",
    "/api/auth/defaults",
    "/api/auth/policy",
)

_policy_cache = None
_deprecation_warned = False
_DERIVED_SECRET_WARNED = False


def _warn_retired_yaml_keys() -> None:
    """One warning per process if `advisor.yaml` still carries the retired keys.

    Retired rather than honoured: two places that can answer "is login
    required" is precisely the drift the shared-policy decision removes, and a
    stale `anonymous_rag_mode: true` silently re-opening a deployment would be
    the worst possible way to find that out. The warning names the replacement
    so the fix is one line rather than a doc search.
    """
    global _deprecation_warned
    if _deprecation_warned:
        return
    _deprecation_warned = True
    cfg = _auth_cfg()
    retired = [k for k in ("enabled", "anonymous_rag_mode") if k in cfg]
    if retired:
        logger.warning(
            "auth: advisor.yaml still sets auth.{} — retired on 2026-09-04 and "
            "IGNORED. Login policy is now shared and resolved by trellis-auth: use "
            "TRELLIS_REQUIRE_LOGIN / TRELLIS_ANONYMOUS_READ (or the ADVISOR_* forms). "
            "See docs/runtime-architecture-plan.md §4.".format("/auth.".join(retired))
        )


def get_policy():
    """EA's resolved `trellis_auth.AuthPolicy`. Cached — the env is read once.

    Cached deliberately: the middleware, `get_current_user` and the
    `/api/auth/policy` route must all agree, and re-reading the environment
    per call would let a mid-process `os.environ` change make them disagree
    about whether the request that is in flight needed a token.
    """
    global _policy_cache
    if _policy_cache is None:
        from trellis_auth import resolve_policy
        _warn_retired_yaml_keys()
        _policy_cache = resolve_policy(
            _POLICY_ENV_PREFIX,
            extra_public_paths=_EA_PUBLIC_PATHS,
        )
    return _policy_cache


def reset_policy_cache() -> None:
    """Drop the cached policy — for tests that vary the environment."""
    global _policy_cache, _deprecation_warned
    _policy_cache = None
    _deprecation_warned = False


def _anonymous_rag_mode() -> bool:
    """Derived, not decided: anonymous RAG mode *is* the policy's anonymous read.

    The two were independent switches until 2026-09-04, which meant a
    deployment could sit in the incoherent state of "login required" plus
    "anonymous RAG allowed" and nobody could say from the config which one won
    for a given route.
    """
    return get_policy().anonymous_read


def _auth_enabled() -> bool:
    """Derived: True whenever the shared policy requires login (the default)."""
    return get_policy().require_login


def _base_config() -> AuthConfig:
    """AuthConfig for the token-only operations (no Egeria connection needed)."""
    return AuthConfig(
        jwt_secret=_jwt_secret(),
        jwt_ttl_hours=_jwt_ttl_hours(),
        portal_secret=_portal_secret(),
    )


def _validation_config() -> AuthConfig:
    """AuthConfig for the functions that talk to Egeria (`login_with_password`,
    `validate_egeria_token`, `validate_egeria_credentials`) — also carries the
    Egeria connection + service-account-sanity-check fields. Built lazily (and
    only when one of those is actually invoked) to preserve the original
    module's lazy `from advisor.mcp_config import ...` import, which avoids a
    circular import at module load time."""
    from advisor.mcp_config import get_pyegeria_platform_config

    conn = get_pyegeria_platform_config()
    view_server = conn["view_server"]
    platform_url = conn["platform_url"]

    svc_user = svc_pwd = ""
    try:
        import json
        cfg = json.loads(_MCP_PATH.read_text())
        env = cfg.get("mcpServers", {}).get("pyegeria", {}).get("env", {})
        svc_user = env.get("EGERIA_USER", "")
        svc_pwd = env.get("EGERIA_PASSWORD", "")
    except Exception as exc:
        logger.warning(f"auth: failed to read {_MCP_PATH} for service-account check: {exc}")

    return AuthConfig(
        jwt_secret=_jwt_secret(),
        jwt_ttl_hours=_jwt_ttl_hours(),
        portal_secret=_portal_secret(),
        egeria_view_server=view_server,
        egeria_platform_url=platform_url,
        egeria_service_account_user=svc_user,
        egeria_service_account_password=svc_pwd,
    )


# ---------------------------------------------------------------------------
# Service accounts -- ids no human may sign in as (2026-10-09).
# Resource Explorer's background work runs as its own Egeria account, and RE
# and EA share TRELLIS_JWT_SECRET with no audience check, so an EA session for
# that account would be accepted by RE. Read from configuration only (no RE
# import): RE_DAEMON_USER_ID plus the comma-separated TRELLIS_SERVICE_ACCOUNTS.
# ---------------------------------------------------------------------------

SERVICE_ACCOUNT_DETAIL = "this is a service account; sign in as yourself"


def service_account_ids() -> frozenset:
    ids = set()
    daemon = os.environ.get("RE_DAEMON_USER_ID", "")
    if daemon.strip():
        ids.add(daemon.strip().casefold())
    for part in os.environ.get("TRELLIS_SERVICE_ACCOUNTS", "").split(","):
        if part.strip():
            ids.add(part.strip().casefold())
    return frozenset(ids)


def is_service_account(user_id: Optional[str]) -> bool:
    return bool(user_id) and user_id.strip().casefold() in service_account_ids()


def refuse_service_account(user_id: Optional[str]) -> None:
    """Raise HTTP 403 when `user_id` is a service account. Call before any Egeria call."""
    if is_service_account(user_id):
        logger.warning(f"auth: refused session for service account {user_id!r}")
        raise HTTPException(status_code=403, detail=SERVICE_ACCOUNT_DETAIL)


# ---------------------------------------------------------------------------
# Token creation / decoding — pure delegation, no EA-specific behaviour.
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    egeria_token: str,
    role: str = "user",
    display_name: Optional[str] = None,
) -> str:
    """Create the session JWT carrying the user's Egeria bearer token.

    The JWT's `exp` is capped at the Egeria token's own expiry by
    `trellis_auth` — Egeria's tokens last one hour (measured 2026-09-04, see
    `trellis_auth.auth.EGERIA_TOKEN_TTL_SECONDS_OBSERVED`), well under EA's
    8-hour `jwt_ttl_hours`, so in practice the Egeria expiry is the session
    bound and refresh is a re-login.
    """
    # The one chokepoint every minting path (password, portal, CLI) passes.
    refuse_service_account(user_id)
    return _create_access_token(
        user_id, egeria_token, _base_config(), role=role, display_name=display_name
    )


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.PyJWTError on failure."""
    return _decode_token(token, _base_config())


# ---------------------------------------------------------------------------
# FastAPI helpers — reimplemented (not delegated) because they depend on
# EA's anonymous-mode bypass, which trellis_auth deliberately has no notion
# of (see module docstring).
# ---------------------------------------------------------------------------

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Extract and validate the bearer token from the Authorization header.
    Returns the decoded payload dict, or None if absent / invalid.
    Never raises — callers decide whether anonymous is acceptable.
    """
    if not _auth_enabled():
        return {
            "sub": "anonymous",
            "user_id": "anonymous",
            "role": "user",
            "display_name": "anonymous",
            "egeria_token": "",
            "anonymous": True,
        }
    return _get_current_user(request, _base_config())


def require_egeria_user(request: Request) -> Dict[str, Any]:
    """
    Like get_current_user but raises HTTP 401 if not authenticated.
    Use as a FastAPI dependency for endpoints that need live Egeria access.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please log in to access live Egeria features.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def is_authenticated(request: Request) -> bool:
    """Convenience helper for conditional auth checks in the query pipeline."""
    return get_current_user(request) is not None


# ---------------------------------------------------------------------------
# Per-user Egeria token propagation
#
# The JWT embeds the Egeria bearer token the user's login (or the Portal's)
# obtained for them — never a password. Live-Egeria call sites (reports,
# Dr.Egeria actions, plan execution) must authenticate their pyegeria client
# with THIS token via apply_token(), so that live reads/writes run as the
# actual signed-in user and Egeria's own provenance records that person
# rather than one shared service identity.
# ---------------------------------------------------------------------------

def get_egeria_token(request: Request) -> Optional[str]:
    """The signed-in user's Egeria bearer token, or None when anonymous."""
    user = get_current_user(request)
    if user is None:
        return None
    return user.get("egeria_token") or None


def get_egeria_credentials(request: Request) -> Optional[EgeriaCredentials]:
    """
    Extract `{user_id, password, token}` from the request's JWT.

    `password` is always `""` — the JWT has carried a bearer token instead of a
    password since 2026-09-04. Returns None only when there is no authenticated
    user (anonymous, no/invalid token) — callers decide whether to fall back
    via resolve_egeria_credentials().
    """
    user = get_current_user(request)
    if user is None:
        return None
    return {
        "user_id": user.get("user_id") or user.get("sub", ""),
        "password": "",
        "token": user.get("egeria_token", ""),
    }


def resolve_egeria_credentials(creds: Optional[Dict[str, str]]) -> EgeriaCredentials:
    """
    Single fallback point for how a live Egeria call authenticates: use the
    per-request credentials if present, otherwise fall back to the .env-backed
    service account (advisor.config.settings.egeria_user/egeria_password).

    A signed-in request yields `token` set and `password` empty — the call site
    should hand `token` to `apply_token()`. The anonymous/background fallback
    yields `password` set and `token` empty, which is the one legitimately
    password-backed path.

    Deliberately does NOT fall back to config/mcp_servers.json — that file's
    "EGERIA_USER"/"EGERIA_PASSWORD" env entries are unresolved template
    placeholders on a typical local checkout, never substituted anywhere.

    This fallback stays app-specific and is NOT in trellis_auth — see the
    module docstring above and docs/trellis-auth-extraction.md §4. The
    2026-08-29 decision (artifact ownership requires an authenticated identity
    with no config fallback) is unaffected: the fallback still fires only for a
    request with no signed-in user at all.
    """
    if creds and creds.get("user_id"):
        return {
            "user_id": creds["user_id"],
            "password": creds.get("password", ""),
            "token": creds.get("token", ""),
        }
    from advisor.config import settings
    return {"user_id": settings.egeria_user, "password": settings.egeria_password, "token": ""}


# ---------------------------------------------------------------------------
# Portal token exchange — pure delegation.
# ---------------------------------------------------------------------------

def exchange_portal_token(portal_token: str) -> Dict[str, Any]:
    """
    Validate a short-lived JWT issued by the Portal (shared HS256 secret).
    Returns decoded payload on success; raises HTTP 401/400 on failure.

    Expected portal token payload — the shape the Portal actually issues
    (`demo_auth_handler.py::_make_jwt`):
      { sub: str, role: str, display_name: str, egeria_token: str, exp: int }
    """
    return _exchange_portal_token(portal_token, _base_config())


# ---------------------------------------------------------------------------
# Egeria login / token validation — delegates, but resolves EA's own connection
# config + service-account-sanity-check values first (trellis_auth never
# reads mcp_servers.json itself).
# ---------------------------------------------------------------------------

def login_with_password(user_id: str, password: str) -> Optional[str]:
    """Exchange an Egeria user id + password for a bearer token, once.

    Returns the token, or None when the credentials are bad / Egeria is
    unreachable. The caller mints the session JWT from the token and drops the
    password — this is the only point at which EA handles one.
    """
    refuse_service_account(user_id)
    return _login_with_password(user_id, password, _validation_config())


def validate_egeria_token(token: str) -> bool:
    """Check an Egeria bearer token with one cheap authenticated view-server call."""
    return _validate_egeria_token(token, _validation_config())


def validate_egeria_credentials(user_id: str, password: str) -> bool:
    """
    Validate credentials against the live Egeria instance by attempting to
    create a bearer token.  Returns True on success, False on failure.
    Falls back to checking against configured service account if Egeria is down.

    Prefer `login_with_password`, which keeps the token it just obtained.
    """
    refuse_service_account(user_id)
    return _validate_egeria_credentials(user_id, password, _validation_config())
