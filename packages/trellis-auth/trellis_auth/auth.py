"""JWT-based authentication, shared across Trellis apps.

Extracted from Egeria Advisor's `advisor/auth.py`, which had a real login
(HS256 JWTs plus Portal SSO); Resource Explorer had none. See
`docs/trellis-auth-extraction.md`.

**Contract change, 2026-09-04 — the app JWT carries an Egeria bearer token,
never a password.** See `docs/runtime-architecture-plan.md` §4
("Authentication: two paths, one token"). Until this date `create_access_token`
signed the raw Egeria password into the HS256 app JWT and
`exchange_portal_token` expected a Portal payload of
`{egeria_user, egeria_password, iat, exp}`. The Egeria Portal never issued
that shape: `demo_auth_handler.py::_make_jwt` issues
`{sub, role, display_name, egeria_token, exp}` where `egeria_token` is a real
Egeria bearer token from pyegeria's `create_egeria_bearer_token()`, applied per
request with `set_bearer_token()` (`egeria_auth.py::apply_token`). So EA's
`POST /api/auth/portal` was wired to a payload nothing produced. Both paths now
end in the same thing: a per-request pyegeria client holding the *user's* Egeria
bearer token.

  * Direct login: `login_with_password()` exchanges the password for an Egeria
    token exactly once; the password is never stored or signed.
  * Portal SSO: `exchange_portal_token()` validates the Portal's real payload
    under the shared secret and hands back the token it already holds.

This module never reads the environment or a config file — every function
takes an `AuthConfig` the caller has already resolved (see `config.py`).

Whether an app *requires* login is no longer app-specific: since 2026-09-04
it is one shared policy in `policy.py` (`AuthPolicy`,
`LoginRequiredMiddleware`, `resolve_policy`), and EA's `_auth_enabled` /
`_anonymous_rag_mode` are derived from it.

Deliberately NOT here (stays app-specific — see the design note, §4):
  * where config lives (`advisor/configdata/advisor.yaml`, `mcp_servers.json`
    are EA's own paths);
  * `resolve_egeria_credentials`'s service-account fallback — deliberately
    excluded per the 2026-08-29 decision that artifact ownership requires an
    authenticated identity with no config fallback.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from starlette.exceptions import HTTPException
from starlette.requests import Request

from trellis_auth.config import AuthConfig

logger = logging.getLogger(__name__)

__all__ = [
    "create_access_token",
    "decode_token",
    "get_current_user",
    "require_egeria_user",
    "is_authenticated",
    "get_egeria_token",
    "get_egeria_credentials",
    "exchange_portal_token",
    "egeria_token_expiry",
    "egeria_token_shape",
    "is_well_formed_egeria_token",
    "login_with_password",
    "validate_egeria_token",
    "validate_egeria_credentials",
    "apply_token",
]


# Egeria's own bearer-token lifetime, measured live on 2026-09-04 against the
# quickstart deployment (`qs-view-server` at https://localhost:9443) by minting
# a token with pyegeria's `create_egeria_bearer_token()` and base64-decoding its
# payload:
#
#     {"iss": "self", "sub": "peterprofile", "iat": 1788540043,
#      "exp": 1788543643, "displayName": "Peter Profile"}   →  exp - iat = 3600
#
# So: an RS256 JWT carrying an `exp` claim, one hour after issue. That is
# shorter than any app JWT TTL we configure (EA defaults to 8 hours), which is
# why `create_access_token` caps the app JWT's `exp` at the Egeria token's own
# — an app session that outlives its Egeria token is a session that looks
# signed-in and can't make a single Egeria call. Refresh is a re-login, as it
# is in the Portal. This constant is a *documentation* value only; the real cap
# always comes from the presented token's own `exp` claim
# (`egeria_token_expiry`), because a differently-configured Egeria may use a
# different lifetime.
EGERIA_TOKEN_TTL_SECONDS_OBSERVED = 3600


# ---------------------------------------------------------------------------
# Token creation / decoding
# ---------------------------------------------------------------------------

def egeria_token_expiry(egeria_token: str) -> Optional[int]:
    """Read the `exp` claim from an Egeria bearer token, as a POSIX timestamp.

    Returns None when the token is opaque, malformed, or carries no `exp`.

    Deliberately decodes *without* verifying the signature: the Egeria token is
    an RS256 JWT signed by the view server with a key we do not hold, and this
    is not an authentication decision — it only bounds how long our own session
    claims to be usable. The token's actual validity is enforced by Egeria on
    every call it is presented to (and by `validate_egeria_token`).
    """
    if not egeria_token:
        return None
    try:
        claims = jwt.decode(egeria_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        logger.debug("auth: Egeria token is not a decodable JWT — %s", exc)
        return None
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def egeria_token_shape(value: Any) -> str:
    """A loggable description of a would-be token: its length and dot count, never its content.

    "442 chars, 6 dots" is enough to tell an error page or a JSON error body from
    a JWT ("595 chars, 2 dots") without putting a credential in a log.
    """
    if not isinstance(value, str):
        return f"not a string ({type(value).__name__})"
    return f"{len(value)} chars, {value.count('.')} dots"


def _b64url_json(segment: str) -> Optional[Dict[str, Any]]:
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        decoded = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def is_well_formed_egeria_token(token: Any) -> bool:
    """True when `token` has the shape of the JWT Egeria's view server issues.

    Egeria's `POST /servers/{server}/api/token` answers with the bare token as
    its body, and pyegeria's `create_egeria_bearer_token` returns
    `response.text` *without checking the HTTP status* (6.x,
    `_base_server_client._async_create_egeria_bearer_token`; on a transport error
    it returns the literal string "FAILED"). So an error body — a 401 for a
    mistyped password, a 5xx from a platform that is still starting, a proxy's
    HTML page — comes back looking like a token, and Egeria then refuses every
    call made with it (2026-10-20: 442 chars, 6 dots, no `exp`). This is the
    gate that keeps such a value out of a session: three non-empty base64url
    segments whose first two decode to JSON objects, the header naming an `alg`.

    Shape only — not a signature check (we do not hold the view server's key)
    and not proof Egeria will honour it; `validate_egeria_token` and RE's
    `check_token_accepted` answer that.
    """
    if not isinstance(token, str) or not token or token != token.strip():
        return False
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    header = _b64url_json(parts[0])
    if header is None or not header.get("alg"):
        return False
    return _b64url_json(parts[1]) is not None


def create_access_token(
    user_id: str,
    egeria_token: str,
    config: AuthConfig,
    role: str = "user",
    display_name: Optional[str] = None,
) -> str:
    """Create the app's signed session JWT, carrying the user's Egeria bearer token.

    Payload shape (matches the Portal's, so one contract serves both the direct
    and the SSO path)::

        {sub, user_id, role, display_name, egeria_token, iat, exp}

    `sub` and `user_id` are the same value, both present: `sub` is what the
    Portal uses and what `decode_token` consumers already read, `user_id` is the
    name the rest of trellis uses for the same thing.

    **No password field, ever.** See the module docstring.

    `exp` is `min(now + config.jwt_ttl_hours, the Egeria token's own exp)`. An
    app session must not outlive the Egeria token it carries.
    """
    if not egeria_token:
        raise ValueError(
            "create_access_token requires an Egeria bearer token. "
            "Obtain one with login_with_password() or from a Portal token; the "
            "app JWT no longer carries a password (2026-09-04)."
        )
    now = datetime.now(timezone.utc)
    app_exp = now + timedelta(hours=config.jwt_ttl_hours)

    egeria_exp = egeria_token_expiry(egeria_token)
    if egeria_exp is not None:
        egeria_exp_dt = datetime.fromtimestamp(egeria_exp, timezone.utc)
        if egeria_exp_dt < app_exp:
            logger.debug(
                "auth: capping app JWT exp at the Egeria token's own expiry (%s)",
                egeria_exp_dt.isoformat(),
            )
            app_exp = egeria_exp_dt

    payload = {
        "sub": user_id,
        "user_id": user_id,
        "role": role,
        "display_name": display_name or user_id,
        "egeria_token": egeria_token,
        "iat": now,
        "exp": app_exp,
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


def decode_token(token: str, config: AuthConfig) -> Dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm])


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def get_current_user(request: Request, config: AuthConfig) -> Optional[Dict[str, Any]]:
    """
    Extract and validate the bearer token from the Authorization header.
    Returns the decoded payload dict, or None if absent / invalid.
    Never raises — callers decide whether anonymous is acceptable.

    This does NOT implement an "auth disabled" bypass — whether an app ever
    calls this at all for anonymous-allowed requests is that app's own
    policy (see module docstring).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer "):]
    try:
        return decode_token(token, config)
    except jwt.PyJWTError as exc:
        logger.debug("auth: invalid token — %s", exc)
        return None


def require_egeria_user(request: Request, config: AuthConfig) -> Dict[str, Any]:
    """
    Like get_current_user but raises HTTP 401 if not authenticated.
    Use as a dependency for endpoints that need live Egeria access.
    """
    user = get_current_user(request, config)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please log in to access live Egeria features.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def is_authenticated(request: Request, config: AuthConfig) -> bool:
    """Convenience helper for conditional auth checks in a query pipeline."""
    return get_current_user(request, config) is not None


# ---------------------------------------------------------------------------
# Per-user Egeria token propagation
# ---------------------------------------------------------------------------

def get_egeria_token(request: Request, config: AuthConfig) -> Optional[str]:
    """
    Extract the caller's Egeria bearer token from the request's app JWT.

    Returns None when there is no authenticated user (no/invalid token), and
    also when a valid app JWT somehow carries no `egeria_token` — callers
    decide whether/how to fall back. Hand the result to `apply_token()`.
    """
    user = get_current_user(request, config)
    if user is None:
        return None
    return user.get("egeria_token") or None


def get_egeria_credentials(*_args: Any, **_kwargs: Any) -> None:
    """Removed 2026-09-04. Use `get_egeria_token(request, config)` instead.

    The app JWT no longer carries an Egeria password, so there is no
    `{user_id, password}` pair to return. Build the per-request pyegeria client
    from the bearer token instead::

        token = get_egeria_token(request, config)
        client = EgeriaTech(view_server=..., platform_url=..., user_id=...)
        apply_token(client, token)

    Kept as a loud shim rather than deleted so a stale caller fails with this
    explanation instead of an AttributeError or, worse, a silently empty
    password. See docs/runtime-architecture-plan.md §4.
    """
    raise RuntimeError(
        "trellis_auth.get_egeria_credentials was removed on 2026-09-04: the app "
        "JWT carries the user's Egeria bearer token, never a password. Use "
        "get_egeria_token(request, config) and apply_token(client, token). "
        "See docs/runtime-architecture-plan.md §4 and docs/trellis-auth-extraction.md."
    )


def apply_token(client: Any, token: Optional[str]) -> None:
    """Apply a per-request Egeria bearer token to a freshly-built pyegeria client.

    Mirrors the Portal's `egeria_auth.apply_token` so both sides build clients
    the same way: when a token was supplied the client reuses it via
    `set_bearer_token()`; otherwise it falls back to obtaining a fresh token
    from the client's own configured `user_id`/`user_pwd`.

    That fallback is *not* the excluded service-account credential resolution —
    it only says "this client was constructed with credentials of its own, use
    them", which is how the legitimately service-account-backed paths
    (bootstrap, outbox drain, background workers) already build their clients.
    An app that requires a per-user identity should check the token is present
    before calling this.
    """
    if token:
        client.set_bearer_token(token)
    else:
        client.create_egeria_bearer_token()


# ---------------------------------------------------------------------------
# Portal token exchange
# ---------------------------------------------------------------------------

def exchange_portal_token(portal_token: str, config: AuthConfig) -> Dict[str, Any]:
    """
    Validate a short-lived JWT issued by the Portal (shared HS256 secret).
    Returns the decoded payload on success; raises HTTP 401 on failure.

    Expected payload — the shape the Portal actually issues
    (`demo_auth_handler.py::_make_jwt`)::

        {sub: str, role: str, display_name: str, egeria_token: str, exp: int}

    A token in the pre-2026-09-04 `{egeria_user, egeria_password}` shape is
    rejected with an explicit message rather than a generic 401: nothing in
    `egeria-workspaces` ever produced it, so seeing one means a caller is still
    on the old contract.
    """
    secret = config.portal_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Portal SSO is not configured on this server.")
    try:
        payload = jwt.decode(portal_token, secret, algorithms=[config.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Portal token has expired. Please reload from the Portal.")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid portal token: {exc}")

    if not payload.get("egeria_token"):
        if "egeria_password" in payload or "egeria_user" in payload:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Portal token uses the retired password contract "
                    "{egeria_user, egeria_password}. Since 2026-09-04 the token must "
                    "carry an Egeria bearer token: {sub, role, display_name, "
                    "egeria_token, exp}. Passwords are never accepted in a token."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="Portal token is missing the required 'egeria_token' claim.",
        )
    if not payload.get("sub"):
        raise HTTPException(status_code=400, detail="Portal token is missing the required 'sub' claim.")
    return payload


# ---------------------------------------------------------------------------
# Egeria credential / token validation
#
# `pyegeria` is imported lazily throughout (it's an optional `egeria` extra); a
# missing install is caught by the same broad except as "Egeria is unreachable"
# and reported as a failure, not an ImportError.
# ---------------------------------------------------------------------------

def login_with_password(user_id: str, password: str, config: AuthConfig) -> Optional[str]:
    """Exchange an Egeria user id + password for an Egeria bearer token, once.

    This is the *only* place a password is handled on the direct login path.
    The caller mints the app JWT from the returned token and drops the
    password; nothing persists it.

    Returns the bearer token on success, None on bad credentials, an
    unreachable Egeria, a reply that is not a well-formed token
    (`is_well_formed_egeria_token`), or a missing `pyegeria` install (same degradation as
    `validate_egeria_credentials`, deliberately: from the login form's point of
    view all three mean "we could not sign you in").
    """
    view_server = config.egeria_view_server
    platform_url = config.egeria_platform_url
    if not view_server or not platform_url:
        logger.warning("auth: Egeria connection config incomplete — cannot exchange password for a token")
        return None
    try:
        from pyegeria.egeria_tech_client import EgeriaTech

        client = EgeriaTech(
            view_server=view_server,
            platform_url=platform_url,
            user_id=user_id,
            user_pwd=password,
        )
        token = client.create_egeria_bearer_token(user_id, password)
        if not token:
            logger.info("auth: Egeria returned no bearer token for %r", user_id)
            return None
        if not is_well_formed_egeria_token(token):
            # pyegeria hands back the response body whatever the status, so a
            # refused or failed token request looks like a token. Never cache
            # one: Egeria would 401 every call while the session claims to be live.
            logger.warning(
                "auth: Egeria's token endpoint answered %r with something that is not a "
                "bearer token (%s) — sign-in refused",
                user_id, egeria_token_shape(token),
            )
            return None
        logger.info("auth: obtained Egeria bearer token for user %r", user_id)
        return token
    except Exception as exc:
        logger.info(
            "auth: password-for-token exchange failed for %r — %s: %s",
            user_id, type(exc).__name__, exc,
        )
        return None


def validate_egeria_token(token: str, config: AuthConfig) -> bool:
    """Check an Egeria bearer token by making one cheap authenticated call.

    Used on the Portal SSO path, where the token was minted by someone else and
    we want to know it works before issuing a session around it. Checks the
    local `exp` claim first (free, and catches the common case) and only then
    makes the call — `get_my_profile()`, the cheapest per-user view-server read,
    which 401s on a bad or expired token.

    Returns False when the token is absent/expired/rejected, when Egeria is
    unreachable, or when `pyegeria` isn't installed. Callers that want SSO to
    keep working while Egeria is briefly down should treat this as an *optional*
    check rather than a gate.
    """
    if not token:
        return False
    exp = egeria_token_expiry(token)
    if exp is not None and exp <= datetime.now(timezone.utc).timestamp():
        logger.info("auth: Egeria token is already expired (exp=%s)", exp)
        return False

    view_server = config.egeria_view_server
    platform_url = config.egeria_platform_url
    if not view_server or not platform_url:
        logger.warning("auth: Egeria connection config incomplete — cannot validate bearer token")
        return False
    try:
        from pyegeria.egeria_tech_client import EgeriaTech

        client = EgeriaTech(
            view_server=view_server,
            platform_url=platform_url,
            user_id=config.egeria_service_account_user or "unknown",
        )
        apply_token(client, token)
        client.get_my_profile()
        return True
    except Exception as exc:
        logger.info("auth: bearer-token validation failed — %s: %s", type(exc).__name__, exc)
        return False


def validate_egeria_credentials(user_id: str, password: str, config: AuthConfig) -> bool:
    """
    Validate credentials against the live Egeria instance by attempting to
    create a bearer token. Returns True on success, False on failure.

    Prefer `login_with_password`, which returns the token it just obtained
    rather than throwing it away — a caller that validates and then mints a
    session would otherwise pay for two Egeria round-trips. This function
    remains for callers that only want a yes/no.

    If `config.egeria_view_server`/`egeria_platform_url` aren't set, falls
    back to comparing against `config.egeria_service_account_*` instead of
    attempting a live call — this is a sanity check for a misconfigured
    deployment, not the credential-resolution fallback that
    `resolve_egeria_credentials` deliberately does NOT get in this package
    (see module docstring).
    """
    if not config.egeria_view_server or not config.egeria_platform_url:
        logger.warning(
            "auth: Egeria connection config incomplete — falling back to service account check"
        )
        return (
            user_id == config.egeria_service_account_user
            and password == config.egeria_service_account_password
        )
    return login_with_password(user_id, password, config) is not None
