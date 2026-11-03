"""A person's OWN database credential for table previews: held in this process's memory for their session, nowhere else.

Design: REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md §3 ("kept for this session only · never stored"). Owner rulings: the
person previewing uses their own credential, not the surveyor's, and RE keeps it for the session only.

What "the session" is here. RE's session is a signed JWT and the server keeps no session state (`web/routes/auth.py`,
`auth_logout`), so this holder IS the server-side session state for this one thing. An entry is:

* tied to the signed-in user AND to that one sign-in: the key is (user id, session key, database slug). The session
  key is the token's `jti` when it has one, else a sha256 of the token (`session_key`), so another person never reads
  it, and a new login or another device is asked again (design: "this session only");
* dropped at the earliest of the session token's own expiry (`not_after`, a wall-clock time, when the route knows it),
  the TTL (`RE_PREVIEW_CREDENTIAL_TTL_SECONDS`, default 8 hours, never unbounded), the person's sign-out
  (`clear_session`, which the logout route calls with the bearer the request carries; `clear_user` drops every session), "use another credential" (`forget`), and process exit (`atexit`).

Nothing here is written to the registry, the secrets store (`.omsecrets`), a run row, the activity log, Egeria, a log
line or an exception message. `PreviewCredential.__repr__` hides the password, and the holder's own `__repr__` says
only how many entries it holds. There is NO listing and NO iteration. The one reader is `table_preview` (pinned by a
test), which takes the credential, connects, and lets it go out of scope.

The memory is per process: a second web worker would not see it, and the person is asked again. That is the safe
direction to fail in.
"""
from __future__ import annotations

import atexit
import hashlib
import math
import os
import threading
import time
from dataclasses import dataclass, field

DEFAULT_TTL_SECONDS = 8 * 3600
TTL_ENV = "RE_PREVIEW_CREDENTIAL_TTL_SECONDS"


def configured_ttl() -> float:
    """The TTL in seconds: the env value when it is a finite positive number, else the default."""
    raw = (os.environ.get(TTL_ENV) or "").strip()
    if not raw:
        return float(DEFAULT_TTL_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_TTL_SECONDS)
    if not math.isfinite(value) or value <= 0:
        return float(DEFAULT_TTL_SECONDS)
    return value


def session_key(claims: dict | None, token: str = "") -> str:
    """What identifies ONE sign-in: the JWT `jti` if it has one, else a sha256 of the token. '' when neither exists.

    The digest is a one-way name for the session; the token itself is never kept here."""
    jti = (claims or {}).get("jti")
    if jti:
        return f"jti:{jti}"
    if token:
        return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    return ""


@dataclass(frozen=True)
class PreviewCredential:
    """One held credential. `password` is excluded from every generated text form."""

    user: str
    password: str = field(repr=False)
    expires_at: float = 0.0

    def __repr__(self) -> str:                  # never the password, in any spelling
        return f"PreviewCredential(user={self.user!r}, password=<hidden>)"

    __str__ = __repr__


class _Holder:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str], PreviewCredential] = {}

    def __repr__(self) -> str:
        with self._lock:
            return f"<preview credential holder: {len(self._entries)} held>"

    def put(self, person: str, session: str, slug: str, user: str, password: str, *,
            not_after: float | None = None, ttl: float | None = None) -> None:
        expires = self._clock() + (configured_ttl() if ttl is None else ttl)
        if not_after is not None:
            expires = min(expires, float(not_after))
        with self._lock:
            now = self._clock()
            for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
                del self._entries[key]               # an expired sign-in's entry goes, whoever's it was
            self._entries[(person, session, slug)] = PreviewCredential(
                user=user, password=password, expires_at=expires)

    def take(self, person: str, session: str, slug: str) -> PreviewCredential | None:
        """The held credential, or None. An entry past its expiry is dropped here and never returned."""
        with self._lock:
            entry = self._entries.get((person, session, slug))
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[(person, session, slug)]
                return None
            return entry

    def held_user(self, person: str, session: str, slug: str) -> str | None:
        """Only the database user's NAME, for the form and the header; never the password."""
        entry = self.take(person, session, slug)
        return entry.user if entry else None

    def forget(self, person: str, session: str, slug: str) -> None:
        with self._lock:
            self._entries.pop((person, session, slug), None)

    def clear_session(self, person: str, session: str) -> None:
        """Sign-out: drop what THIS sign-in held."""
        with self._lock:
            for key in [k for k in self._entries if k[0] == person and k[1] == session]:
                del self._entries[key]

    def clear_user(self, person: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == person]:
                del self._entries[key]

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()


_HOLDER = _Holder()
atexit.register(_HOLDER.clear_all)

#: The module's whole surface. Bound methods of one private holder; there is no way to list what is held.
put = _HOLDER.put
take = _HOLDER.take
held_user = _HOLDER.held_user
forget = _HOLDER.forget
clear_session = _HOLDER.clear_session
clear_user = _HOLDER.clear_user
clear_all = _HOLDER.clear_all
