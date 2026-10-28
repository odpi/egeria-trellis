"""The CLI's side of the cached login.

`resource-explorer login` exchanges an Egeria password for a bearer token once,
wraps it in RE's own session JWT and caches that under
`$XDG_CONFIG_HOME/trellis/resource-explorer/session.json` (mode 0600). Every
later command reads it back, so the CLI runs as the signed-in person rather
than as the `.env` service account — which is what makes Egeria's own
provenance record who did it (`docs/runtime-architecture-plan.md` §4).

The file format, the location and the expiry check all live in
`trellis_auth.session_file`, shared verbatim with Egeria Advisor. What is
RE-specific and therefore here: the app name, minting the app JWT from RE's
own `AuthConfig`, and the two ways a command asks for the session —

  * `resolve_identity(explicit_user)` — best effort. Returns whoever we can
    name, for attribution and namespacing. A read-only command (`list`,
    `ask` against the local corpus, `runs list`) needs no Egeria and must not
    be blocked by a lapsed session.
  * `require_identity()` — hard requirement. Prints **exactly one line** and
    exits 2 when there is no live session, for commands that will make a live
    Egeria call and would otherwise fail deep inside pyegeria with a stack
    trace that names none of this.

Exit code 2 rather than 1, deliberately and identically to EA: 1 is "the
command ran and failed", 2 is "you are not signed in", and a script wrapping
the CLI can tell them apart without parsing the message.
"""
from __future__ import annotations

import sys
from typing import Optional, Tuple

from trellis_auth import session_file
from trellis_auth.session_file import SessionRecord

from resource_explorer.auth import APP_NAME
from resource_explorer.egeria_identity import EgeriaIdentity

LOGIN_COMMAND = "resource-explorer login"

#: Exit status for "no live session" — see the module docstring.
EXIT_NOT_SIGNED_IN = 2

__all__ = [
    "APP_NAME",
    "LOGIN_COMMAND",
    "EXIT_NOT_SIGNED_IN",
    "clear_session",
    "load_session",
    "activate",
    "require_and_activate",
    "require_identity",
    "resolve_identity",
    "save_login",
    "session_status_line",
]


def load_session() -> Optional[SessionRecord]:
    """The cached session, expired or not (check `.is_expired`)."""
    return session_file.load_session(APP_NAME)


def clear_session() -> bool:
    """Forget the cached session. True if there was one."""
    return session_file.clear_session(APP_NAME)


def save_login(user_id: str, egeria_token: str) -> SessionRecord:
    """Mint RE's session JWT around `egeria_token` and cache it.

    The Egeria token is wrapped rather than stored bare so the cached artifact
    is exactly what the web app's `Authorization: Bearer` header carries — one
    format, one expiry, and a CLI session that can be handed straight to the
    local server (which is how this change's own live verification works).
    """
    from resource_explorer.auth import create_access_token

    app_jwt = create_access_token(user_id=user_id, egeria_token=egeria_token)
    return session_file.save_session(APP_NAME, user_id, app_jwt)


def _identity_from(record: SessionRecord) -> EgeriaIdentity:
    """The `EgeriaIdentity` a live cached session stands for.

    The Egeria bearer token is read back out of the app JWT rather than stored
    separately; `decode_token` verifies our own signature on the way, so a
    tampered session file fails here rather than at the first Egeria call.
    """
    from resource_explorer.auth import decode_token

    from resource_explorer.egeria_clients import refuse_daemon_sign_in

    claims = decode_token(record.token)
    user_id = claims.get("user_id") or claims.get("sub") or record.user_id
    # A cached session for RE's daemon account (a pre-merge login) is refused: raises
    # DaemonSignInRefused (Brief L round 2).
    refuse_daemon_sign_in(user_id)
    return EgeriaIdentity(user_id=user_id, token=claims.get("egeria_token") or None)


def resolve_identity(
    explicit_user: Optional[str] = None,
) -> Tuple[Optional[str], Optional[EgeriaIdentity]]:
    """`(user_id, identity)` for this invocation — never fails.

    `--user` still wins for the *name*, because it always could and scripts
    depend on it; it does not conjure credentials, so a `--user` that disagrees
    with the cached login gets that name and the cached user's Egeria token.
    That combination is legitimate (curating on someone's behalf) and Egeria
    still records who really made the call.

    `(None, None)` when nothing is signed in. A lapsed session is reported by
    the caller that actually needs one, not here.
    """
    from resource_explorer.egeria_clients import DaemonSignInRefused

    record = load_session()
    if record is None or record.is_expired:
        return explicit_user, None
    try:
        identity = _identity_from(record)
    except DaemonSignInRefused:
        return explicit_user, None          # treated as not signed in; require_identity says why
    return explicit_user or identity.user_id, identity


def require_identity(console=None) -> EgeriaIdentity:
    """The identity for a command that must talk to Egeria, or exit 2.

    Prints **exactly one line** — no traceback, no banner, no second "try
    again" sentence. The line always names the command that fixes it, and says
    *when* a session lapsed, because "expired at 09:14" tells a person whether
    they were idle for an hour or whether the platform restarted under them.
    """
    from resource_explorer.egeria_clients import DaemonSignInRefused

    record = load_session()
    if record is not None and not record.is_expired:
        try:
            return _identity_from(record)
        except DaemonSignInRefused as exc:
            message = f"{exc} (run `{LOGIN_COMMAND}`)"
            if console is not None:
                console.print(f"[red]{message}[/red]")
            else:  # pragma: no cover - every CLI caller passes a console
                print(message, file=sys.stderr)
            sys.exit(EXIT_NOT_SIGNED_IN)
    message = session_file.expired_message(record, LOGIN_COMMAND)
    if console is not None:
        console.print(f"[red]{message}[/red]")
    else:  # pragma: no cover - every CLI caller passes a console
        print(message, file=sys.stderr)
    sys.exit(EXIT_NOT_SIGNED_IN)


def activate(identity: Optional[EgeriaIdentity]) -> None:
    """Publish `identity` as the caller for the rest of this CLI process.

    The same ContextVar the web and A2A middlewares set, so a CLI command and
    an HTTP request reach identical code and produce identical Egeria
    attribution. Set-and-forget rather than the `use_identity()` context
    manager because a CLI invocation *is* the scope: there is no next request
    to leak into, and a command that returns down several code paths would
    otherwise need the block wrapped around all of them.
    """
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    if identity is None or not identity.is_person:
        current_caller.set(None)
        return
    current_caller.set(
        CallerIdentity(
            user_id=identity.user_id,
            egeria_token=identity.token,
            auth_source="app-jwt",
        )
    )


def require_and_activate(console=None) -> EgeriaIdentity:
    """`require_identity()` followed by `activate()` — the usual pair.

    Every gated command wants both: exit 2 without a session, and run the rest
    of the command as that person. Two calls at every site is two chances to
    do the first and forget the second, which fails *silently* — the command
    works and the Egeria write is attributed to the service account.
    """
    identity = require_identity(console)
    activate(identity)
    return identity


def session_status_line() -> str:
    """One line describing the cached session, for `login`/`logout` output."""
    record = load_session()
    if record is None:
        return f"not signed in — run `{LOGIN_COMMAND}`"
    if record.is_expired:
        return session_file.expired_message(record, LOGIN_COMMAND)
    if record.expires_at_local is None:
        return f"signed in as {record.user_id}"
    return f"signed in as {record.user_id} until {record.expires_at_local:%H:%M}"
