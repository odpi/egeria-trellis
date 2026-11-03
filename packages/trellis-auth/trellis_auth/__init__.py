"""Shared JWT auth + Portal SSO for Trellis apps.

    from trellis_auth import AuthConfig, create_access_token, get_current_user

    config = AuthConfig(jwt_secret="...", jwt_ttl_hours=8, portal_secret="...")
    egeria_token = login_with_password("dan.egeria", "hunter2", config)
    token = create_access_token("dan", egeria_token, config)
    user = get_current_user(request, config)   # None if absent/invalid

The app JWT carries the user's Egeria *bearer token*, never a password
(contract change 2026-09-04 — see `auth.py`'s module docstring and
`docs/runtime-architecture-plan.md` §4).

**Login is required, and the policy is shared** (decided 2026-09-04,
superseding "whether an app requires login at all is each app's own answer"):

    from trellis_auth import AuthPolicy, LoginRequiredMiddleware, resolve_policy

    policy = resolve_policy("ADVISOR")            # TRELLIS_* then ADVISOR_*
    app.add_middleware(LoginRequiredMiddleware, config=config, policy=policy)

Each app wraps this in a thin adapter that supplies its own resolved
`AuthConfig` and the policy above — see `advisor/auth.py`.

`trellis_auth.session_file` holds the CLI's cached login (the app JWT, mode
0600, under `$XDG_CONFIG_HOME/trellis/<app>/session.json`), shared so both
apps' `login`/`logout` commands behave identically.
"""
from trellis_auth import session_file
from trellis_auth.auth import (
    EGERIA_TOKEN_TTL_SECONDS_OBSERVED,
    apply_token,
    create_access_token,
    decode_token,
    egeria_token_expiry,
    egeria_token_shape,
    exchange_portal_token,
    get_current_user,
    get_egeria_credentials,
    get_egeria_token,
    is_authenticated,
    is_well_formed_egeria_token,
    login_with_password,
    require_egeria_user,
    validate_egeria_credentials,
    validate_egeria_token,
)
from trellis_auth.config import AuthConfig
from trellis_auth.policy import (
    DEFAULT_LOGIN_REQUIRED_MESSAGE,
    DEFAULT_PUBLIC_PATHS,
    OPENAPI_PUBLIC_PATHS,
    AuthPolicy,
    LoginRequiredMiddleware,
    resolve_policy,
)
from trellis_auth.session_file import (
    SessionRecord,
    clear_session,
    expired_message,
    load_session,
    save_session,
    session_path,
)

__all__ = [
    "AuthConfig",
    "AuthPolicy",
    "DEFAULT_LOGIN_REQUIRED_MESSAGE",
    "DEFAULT_PUBLIC_PATHS",
    "LoginRequiredMiddleware",
    "OPENAPI_PUBLIC_PATHS",
    "SessionRecord",
    "clear_session",
    "expired_message",
    "load_session",
    "resolve_policy",
    "save_session",
    "session_file",
    "session_path",
    "EGERIA_TOKEN_TTL_SECONDS_OBSERVED",
    "apply_token",
    "create_access_token",
    "decode_token",
    "egeria_token_expiry",
    "egeria_token_shape",
    "exchange_portal_token",
    "get_current_user",
    "get_egeria_credentials",
    "get_egeria_token",
    "is_authenticated",
    "is_well_formed_egeria_token",
    "login_with_password",
    "require_egeria_user",
    "validate_egeria_credentials",
    "validate_egeria_token",
]
