"""The CLI's cached login: one app JWT on disk, mode 0600.

Egeria bearer tokens last one hour and every one of them dies when the platform
restarts (the quickstart leaves `rsa.key-id` empty, so the signing key is random
per restart — see `docs/runtime-architecture-plan.md` §4). A CLI that prompted
for a password on every command would be unusable; a CLI that *stored* the
password would undo the whole point of the 2026-09-04 token contract. So:
`<app> login` exchanges the password for a token exactly once and caches the
**app JWT** here. The password is never written, and neither is the raw Egeria
token on its own — the app JWT already carries it, and carrying one artifact
means there is one expiry to reason about.

Shared rather than app-local so Resource Explorer reuses it verbatim: two
implementations of "where is the cached session and is it still valid" would
drift on the file mode, on the expiry check, or on both.

    from trellis_auth import session_file

    session_file.save_session("egeria-advisor", "peterprofile", app_jwt)
    record = session_file.load_session("egeria-advisor")
    if record is None or record.is_expired:
        print(session_file.expired_message(record, "egeria-advisor login"))

Location: ``$XDG_CONFIG_HOME/trellis/<app-name>/session.json``, falling back to
``~/.config`` when `XDG_CONFIG_HOME` is unset — the XDG default, and the same
place on macOS as on Linux. Deliberately *not* the app's data directory: this is
per-user credential material, not application state, and it must not be picked
up by whatever backs up or syncs a data root.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import jwt

logger = logging.getLogger(__name__)

__all__ = [
    "SessionRecord",
    "SESSION_FILENAME",
    "config_root",
    "session_path",
    "save_session",
    "load_session",
    "clear_session",
    "expired_message",
]

SESSION_FILENAME = "session.json"

#: Mode for the session file: owner read/write only. A cached session is a
#: bearer credential — anyone who can read this file is the user until it
#: expires.
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR          # 0o600
_DIR_MODE = stat.S_IRWXU                          # 0o700


@dataclass(frozen=True)
class SessionRecord:
    """A cached login, as read back off disk.

    `expires_at` is a POSIX timestamp taken from the app JWT's own `exp` claim,
    which `create_access_token` already capped at the Egeria token's expiry —
    so this one number is the real session bound and there is no second clock
    to keep in step. None means the token carried no `exp` (an opaque or
    hand-made token); such a session is never treated as expired here, because
    "I cannot tell" must not silently mean "you are signed out".
    """

    app_name: str
    user_id: str
    token: str
    expires_at: Optional[int] = None
    path: Optional[Path] = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= datetime.now(timezone.utc).timestamp()

    @property
    def expires_at_local(self) -> Optional[datetime]:
        """`expires_at` as a local-time datetime, for messages a person reads."""
        if self.expires_at is None:
            return None
        return datetime.fromtimestamp(self.expires_at, timezone.utc).astimezone()

    @property
    def egeria_expires_at(self) -> Optional[int]:
        """The `exp` of the Egeria bearer token inside the app JWT, or None when it has none.

        `expires_at` is the *app* JWT's bound, and `create_access_token` falls back
        to the app's own TTL (8 hours in RE) when the Egeria token carries no
        readable `exp`. Then `expires_at` is our cap, not Egeria's word, and a
        message saying "signed in until <expires_at>" would vouch for a token
        nobody has dated (2026-10-20: "valid until 05:11" over a token Egeria
        refused). Check this before presenting `expires_at` as the session's end.

        Read unverified, like `_token_expiry`: a display question, not an
        authentication decision.
        """
        from trellis_auth.auth import egeria_token_expiry

        try:
            claims = jwt.decode(self.token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        egeria_token = claims.get("egeria_token")
        return egeria_token_expiry(egeria_token) if isinstance(egeria_token, str) else None

    @property
    def egeria_expiry_known(self) -> bool:
        """True when the session's end comes from the Egeria token's own `exp`."""
        return self.egeria_expires_at is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app": self.app_name,
            "user_id": self.user_id,
            "token": self.token,
            "expires_at": self.expires_at,
        }


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def config_root() -> Path:
    """``$XDG_CONFIG_HOME/trellis``, or ``~/.config/trellis``.

    Read at call time rather than at import so a test (or a `sudo -u` shell)
    can set `XDG_CONFIG_HOME` and have it take effect.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "trellis"


def session_path(app_name: str) -> Path:
    """Where `app_name`'s cached session lives."""
    return config_root() / app_name / SESSION_FILENAME


# ---------------------------------------------------------------------------
# Read / write / clear
# ---------------------------------------------------------------------------

def _token_expiry(token: str) -> Optional[int]:
    """The `exp` claim of an app JWT, without verifying the signature.

    Unverified is correct here and only here: this is the token *we* just
    minted or previously cached, and reading its expiry is not an
    authentication decision — the server verifies the signature on every
    request the token is presented to. Verifying locally would mean the CLI
    needed the app's JWT secret, which is precisely what it must not have.
    """
    if not token:
        return None
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        logger.debug("session_file: token is not a decodable JWT — %s", exc)
        return None
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def save_session(
    app_name: str,
    user_id: str,
    token: str,
    expires_at: Optional[int] = None,
) -> SessionRecord:
    """Cache `token` for `app_name`, mode 0600. Returns the stored record.

    `expires_at` defaults to the token's own `exp` claim. The write is atomic
    (temp file in the same directory, `chmod`, then `os.replace`) and the mode
    is set **before** the rename, so the file is never briefly world-readable
    at its final name — a `open(); write(); chmod()` sequence leaves exactly
    that window open.
    """
    if not token:
        raise ValueError("save_session requires a token")
    target = session_path(app_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(_DIR_MODE)
    except OSError as exc:  # pragma: no cover - unusual filesystems only
        logger.debug("session_file: could not tighten directory mode on %s — %s", target.parent, exc)

    record = SessionRecord(
        app_name=app_name,
        user_id=user_id,
        token=token,
        expires_at=expires_at if expires_at is not None else _token_expiry(token),
        path=target,
    )

    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".session-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record.to_dict(), fh, indent=2)
            fh.write("\n")
        tmp.chmod(_FILE_MODE)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.debug("session_file: wrote %s", target)
    return record


def load_session(app_name: str) -> Optional[SessionRecord]:
    """The cached session for `app_name`, or None when there is none / it is unreadable.

    An *expired* session is still returned — the caller needs the expiry time
    to print a useful message, and cannot get it from None. Check
    `record.is_expired`.
    """
    target = session_path(app_name)
    try:
        data = json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("session_file: ignoring unreadable session at %s — %s", target, exc)
        return None
    token = data.get("token") or ""
    if not token:
        logger.warning("session_file: session at %s carries no token — ignoring", target)
        return None
    expires_at = data.get("expires_at")
    return SessionRecord(
        app_name=data.get("app") or app_name,
        user_id=data.get("user_id") or "",
        token=token,
        expires_at=int(expires_at) if isinstance(expires_at, (int, float)) else _token_expiry(token),
        path=target,
    )


def clear_session(app_name: str) -> bool:
    """Delete the cached session. True if one was there, False if not."""
    target = session_path(app_name)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("session_file: could not remove %s — %s", target, exc)
        return False


def expired_message(record: Optional[SessionRecord], login_command: str) -> str:
    """The one line a CLI prints when it needs Egeria and has no live session.

    One line, not a paragraph, and it always names the command that fixes it.
    A record with a known expiry says *when* it lapsed, because "expired at
    09:14" tells a person whether they were idle for an hour or whether the
    platform restarted under them — the two have different fixes.
    """
    if record is not None and record.expires_at_local is not None:
        return f"session expired at {record.expires_at_local:%H:%M}, run `{login_command}`"
    if record is not None:
        return f"session is no longer valid, run `{login_command}`"
    return f"not signed in, run `{login_command}`"
