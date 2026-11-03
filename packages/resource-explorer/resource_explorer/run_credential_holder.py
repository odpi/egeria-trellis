"""The one-run credential for an Egeria-coordinated run: held in this process's memory, keyed by process instance GUID.

Design: docs/design-notes/DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.2. The owner (2026-10-16): "a per-run elevated
credential that doesn't replace the stored one". RE's own Re-run already takes one in the body of one call; an
Egeria-coordinated run has no request body at claim time, so the start route leaves it here and the engine host reads
it when it runs a step of that instance.

The whole surface is three functions, and nothing else in RE may read the holder (pinned by a test):

* `put(instance_guid, user, pwd, starter, ttl=None)`: the coordinated start route, only after
  `initiate_process_run` returned the instance GUID, in the same request;
* `take(instance_guid)`: the engine host, for an `re-*` step of that instance. Read-only: the entry stays for the
  instance's later steps. An entry past its TTL is dropped here and never returned;
* `clear(instance_guid)`: the run's terminal state, Cancel run, and the TTL.

Three small facts with no secret in them sit beside it, for the engine host's refusal words and the start's race:

* `process_identity()`: this process's pid and start time, recorded on the run row's marker, so a step refused for
  want of the credential can say whether it ran in another process or after a restart;
* `expired(instance_guid)` / `cleared(instance_guid)`: whether THIS process dropped that instance's entry at its TTL, or
  cleared it when the run read as over or was cancelled (one bounded memory of GUIDs and why, never the credential);
* `start_pending(slug, definition)` / `starting(slug, definition)`: a mark the start holds from before
  `initiate_process_run` until the credential and the marker are written, so a step claimed inside that window is
  refused (retryable), never given the stored login.

There is NO listing, NO iteration and NO route. Process exit clears it (`atexit`), and in any case it was only ever
in memory: nothing here is written to the registry, a run row, the activity log, Egeria, a log line or an exception
message. Nothing here formats the password: `RunCredential.__repr__` hides it, and the holder's own `__repr__` says
only how many entries it holds.

The TTL defaults to 2 hours (owner, Q1) and is configurable with `RE_RUN_CREDENTIAL_TTL_SECONDS`; a value that is not
a finite positive number falls back to the default, so it is never unbounded.
"""
from __future__ import annotations

import atexit
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: Owner, Q1 (2026-10-18): two hours from the start.
DEFAULT_TTL_SECONDS = 7200
TTL_ENV = "RE_RUN_CREDENTIAL_TTL_SECONDS"
#: The scope word a one-run credential is recorded with (`ran_as`, the run row's `run_credential`), the same word RE's
#: own override runs record (`web/routes/survey_definitions.OVERRIDE_SCOPE`).
SCOPE = "this run"
#: How many dropped instance GUIDs this process remembers, and why each went (only to say "timed out" or "cleared");
#: the oldest is forgotten first. Never the credential.
_EXPIRED_MEMORY = 1000
#: Why an entry went, as `_Holder._gone` records it.
GONE_EXPIRED = "expired"
GONE_CLEARED = "cleared"
#: This process, as the run row's marker records it: pid and the time this process first asked who it is (a pid alone
#: is reused across restarts, a container's pid 1 every time). Computed lazily and cached per `os.getpid()` (fix round
#: 2, F5): a worker forked after import is a process of its own, never its parent. No secret.
_PROCESS: dict = {}
_PROCESS_LOCK = threading.Lock()


def process_identity() -> dict:
    """{pid, started}: which process holds the credentials it was given. Recorded on the marker; never secret."""
    pid = os.getpid()
    with _PROCESS_LOCK:
        if _PROCESS.get("pid") != pid:
            _PROCESS.clear()
            _PROCESS.update(pid=pid, started=datetime.now(timezone.utc).isoformat(timespec="microseconds"))
        return dict(_PROCESS)


def configured_ttl() -> float:
    """The TTL in seconds: `RE_RUN_CREDENTIAL_TTL_SECONDS` when it is a finite positive number, else the default."""
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


@dataclass(frozen=True)
class RunCredential:
    """One instance's credential. `password` is excluded from every generated text form."""

    user: str
    password: str = field(repr=False)
    starter: str
    expires_at: float

    def __repr__(self) -> str:          # never the password, in any spelling
        return f"RunCredential(user={self.user!r}, starter={self.starter!r}, password=<hidden>)"

    __str__ = __repr__

    def ran_as(self) -> dict:
        """What a proof row records: who it ran as and the scope. Never the password."""
        return {"user": self.user, "scope": SCOPE}


class _Holder:
    """The in-memory map. Private: the module's three functions are the only way in."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, RunCredential] = {}
        self._gone: dict[str, str] = {}                 # instance GUID -> why its entry went (expired / cleared)
        self._pending: dict[tuple[str, str], int] = {}  # (slug, definition) -> starts in flight

    def __repr__(self) -> str:
        with self._lock:
            return f"<run credential holder: {len(self._entries)} held>"

    __str__ = __repr__

    def _forget(self, key: str, why: str) -> None:
        self._gone.pop(key, None)
        self._gone[key] = why
        while len(self._gone) > _EXPIRED_MEMORY:
            del self._gone[next(iter(self._gone))]

    def _expire(self, now: float) -> None:
        for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
            del self._entries[key]
            self._forget(key, GONE_EXPIRED)

    def gone(self, instance_guid: str) -> str:
        key = (instance_guid or "").strip()
        with self._lock:
            self._expire(self._clock())
            return self._gone.get(key, "") if key else ""

    def begin_start(self, slug: str, definition: str) -> None:
        key = ((slug or "").strip(), (definition or "").strip())
        with self._lock:
            self._pending[key] = self._pending.get(key, 0) + 1

    def end_start(self, slug: str, definition: str) -> None:
        key = ((slug or "").strip(), (definition or "").strip())
        with self._lock:
            left = self._pending.get(key, 0) - 1
            if left > 0:
                self._pending[key] = left
            else:
                self._pending.pop(key, None)

    def start_pending(self, slug: str, definition: str) -> bool:
        key = ((slug or "").strip(), (definition or "").strip())
        with self._lock:
            return self._pending.get(key, 0) > 0

    def put(self, instance_guid: str, user: str, pwd: str, starter: str, ttl: float | None = None) -> None:
        key = (instance_guid or "").strip()
        if not key:
            raise ValueError("a one-run credential needs the process instance GUID Egeria returned")
        if not (user or "").strip() or not pwd:
            raise ValueError("a one-run credential needs both a user and a password")
        if not (starter or "").strip():
            raise ValueError("a one-run credential needs the signed-in person who started the run")
        seconds = configured_ttl() if ttl is None else float(ttl)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("a one-run credential's TTL must be a finite positive number of seconds")
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._entries[key] = RunCredential(user=user.strip(), password=pwd, starter=starter.strip(),
                                               expires_at=now + seconds)
            self._gone.pop(key, None)

    def take(self, instance_guid: str) -> RunCredential | None:
        key = (instance_guid or "").strip()
        if not key:
            return None
        with self._lock:
            self._expire(self._clock())
            return self._entries.get(key)

    def clear(self, instance_guid: str) -> None:
        key = (instance_guid or "").strip()
        with self._lock:
            self._expire(self._clock())
            if self._entries.pop(key, None) is not None:
                self._forget(key, GONE_CLEARED)

    def _clear_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._pending.clear()


_HOLDER = _Holder()


def put(instance_guid: str, user: str, pwd: str, starter: str, ttl: float | None = None) -> None:
    """Hold `user`/`pwd` for the process instance `instance_guid`, started by `starter`, for `ttl` seconds (default:
    `configured_ttl()`). Called by the coordinated start route only, after Egeria returned the GUID."""
    _HOLDER.put(instance_guid, user, pwd, starter, ttl)


def take(instance_guid: str) -> RunCredential | None:
    """The credential held for exactly this process instance, or None (never held, cleared, past its TTL, or this
    process restarted). Read-only. The engine host is its only caller."""
    return _HOLDER.take(instance_guid)


def clear(instance_guid: str) -> None:
    """Forget the credential held for this process instance (nothing when none is held)."""
    _HOLDER.clear(instance_guid)


def expired(instance_guid: str) -> bool:
    """Whether this process held a credential for this instance and dropped it at its TTL. Never the credential."""
    return _HOLDER.gone(instance_guid) == GONE_EXPIRED


def cleared(instance_guid: str) -> bool:
    """Whether this process held a credential for this instance and cleared it (the run read as over, or Cancel run).
    Never the credential."""
    return _HOLDER.gone(instance_guid) == GONE_CLEARED


@contextmanager
def starting(slug: str, definition: str):
    """Held by the coordinated start from before `initiate_process_run` until the credential and the run row's marker
    are written (left in a `finally`): while it is held, `start_pending(slug, definition)` is True and the engine host
    refuses an `re-*` step of that definition on that resource rather than run it with the stored login."""
    _HOLDER.begin_start(slug, definition)
    try:
        yield
    finally:
        _HOLDER.end_start(slug, definition)


def start_pending(slug: str, definition: str) -> bool:
    """Whether a start of `definition` on `slug` with a one-run credential is between asking Egeria and recording it."""
    return _HOLDER.start_pending(slug, definition)


def _at_exit() -> None:
    _HOLDER._clear_all()


atexit.register(_at_exit)
