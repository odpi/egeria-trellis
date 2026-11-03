"""The persisted start entry of a coordinated run started with a one-run credential (review fix round 2, F2).

The in-memory start-pending mark (`run_credential_holder.starting`) dies with the process. A start that asked Egeria and
then lost its process (a restart between `initiate_process_run` and the run row's marker) would leave a run whose
steps nothing marks as "asked for a one-run credential", and those steps would be given the stored login. Owner, Q2:
after a restart the remaining steps are refused, never a fallback; over-refusal is the only acceptable failure mode.

So the start writes this entry BEFORE asking Egeria, through the existing `app_settings` table (`set_setting`; no DDL):

    key   run_credential_start::<slug>::<definition qualified name>
    value [{nonce, at, holder: {pid, started}, instance}, ...]      (JSON; nothing secret)

* written before `initiate_process_run`: if it cannot be written the run is not started (Egeria is never asked);
* after the holder entry and the run row's marker are written, the start removes its own entry (a removal that fails
  leaves it until the TTL: over-refusal at worst);
* when the marker could not be written, the entry is kept and names the instance Egeria returned, so it stands in for
  the marker;
* an entry older than the TTL (`run_credential_holder.configured_ttl`) is ignored and pruned on the next write.

The engine host reads it (`live_starts`) for an `re-*` step of that definition on that resource that has no holder entry
and no marker (`engine_host._one_run_credential`, `EngineHost._screen`). An entry that names no instance holds back only
an instance Egeria created inside the start's window (`created_in_window`); one created provably outside it is an
ordinary run (owner, 2026-10-18).

Known limit: the value is read, changed and written back. Two PROCESSES starting the same definition on the same
resource in the same instant could each write over the other's entry; a lock serialises this within one process.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

KEY_PREFIX = "run_credential_start::"
#: How long a start of THIS process may stay between writing its entry and recording its marker before a step it could
#: own is refused rather than left for the next sweep: the initiate call's own timeout (pyegeria's 30 s read, 10 s
#: connect) plus the row write, with room to spare.
START_DEFER_SECONDS = 90.0

#: The start window (owner, 2026-10-18: "That seems more useable"). A run the failed or unknown start began was CREATED
#: in Egeria during its initiate call, so an entry that names no instance holds back only an instance whose Egeria
#: creation time lies in [entry.at - skew, entry.at + initiate window + skew] (`engine_host._start_hold`).
#:
#: The initiate window: RE calls `initiate_gov_action_process` once (no retry of its own). pyegeria bounds that call
#: by its client timeout (`settings.Debug.timeout_seconds`, env PYEGERIA_TIMEOUT_SECONDS, default 30 s; RE passes no
#: timeout of its own), and on a 401 makes exactly one token re-mint and one retry, each bounded by the same timeout:
#: at most three timeouts, 90 s by default. `RE_RUN_CREDENTIAL_INITIATE_WINDOW_SECONDS` overrides it.
INITIATE_WINDOW_ENV = "RE_RUN_CREDENTIAL_INITIATE_WINDOW_SECONDS"
INITIATE_ATTEMPTS = 3
PYEGERIA_DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_INITIATE_WINDOW_SECONDS = INITIATE_ATTEMPTS * PYEGERIA_DEFAULT_TIMEOUT_SECONDS
#: The clock skew allowed between RE (which stamps entry.at) and Egeria (which stamps the instance's createTime), on
#: both sides of the window. `RE_RUN_CREDENTIAL_CLOCK_SKEW_SECONDS` overrides it.
CLOCK_SKEW_ENV = "RE_RUN_CREDENTIAL_CLOCK_SKEW_SECONDS"
DEFAULT_CLOCK_SKEW_SECONDS = 60.0

_LOCK = threading.Lock()


def _seconds_env(name: str, default: float, *, allow_zero: bool = False) -> float:
    """A finite non-negative (or positive) number of seconds from env `name`, else `default`."""
    import math
    import os

    raw = (os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        return default
    return value


def initiate_window_seconds() -> float:
    """How long after entry.at a run the start began can have been created: the env override, else three of
    pyegeria's configured client timeouts (see `INITIATE_WINDOW_ENV`)."""
    try:
        from pyegeria.core.config import settings

        timeout = float(settings.Debug.timeout_seconds or PYEGERIA_DEFAULT_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 -- pyegeria's own default when its settings cannot be read
        timeout = PYEGERIA_DEFAULT_TIMEOUT_SECONDS
    return _seconds_env(INITIATE_WINDOW_ENV, INITIATE_ATTEMPTS * timeout)


def clock_skew_seconds() -> float:
    """The skew margin on both sides of the start window (0 allowed)."""
    return _seconds_env(CLOCK_SKEW_ENV, DEFAULT_CLOCK_SKEW_SECONDS, allow_zero=True)


def created_in_window(entry: dict, created) -> bool:
    """Whether an instance created at `created` (naive UTC) may be the run this start entry's initiate call began.
    True (may be) when the entry's time or `created` is unknown: only a creation time PROVEN outside the window says
    no."""
    from datetime import timedelta

    at = parse_when(entry.get("at"))
    if at is None or created is None:
        return True
    skew = timedelta(seconds=clock_skew_seconds())
    return at - skew <= created <= at + timedelta(seconds=initiate_window_seconds()) + skew


def start_key(slug: str, definition: str) -> str:
    return f"{KEY_PREFIX}{(slug or '').strip()}::{(definition or '').strip()}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_when(text) -> datetime | None:
    """A naive-UTC datetime from the registry's ISO spelling (an aware one is converted); None when unreadable."""
    try:
        when = datetime.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def _read(registry, key: str) -> list[dict]:
    raw = registry.get_setting(key)
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError(f"start entry {key!r} is not a list")
    return [e for e in value if isinstance(e, dict)]


def _age_seconds(entry: dict, now: datetime) -> float | None:
    when = parse_when(entry.get("at"))
    return None if when is None else (now - when).total_seconds()


def _live(entries: list[dict], now: datetime) -> list[dict]:
    """Entries younger than the TTL. One whose time cannot be read is kept: unknown is never "stale"."""
    from resource_explorer.run_credential_holder import configured_ttl

    ttl = configured_ttl()
    out = []
    for e in entries:
        age = _age_seconds(e, now)
        if age is None or age < ttl:
            out.append(e)
    return out


def write_start(registry, slug: str, definition: str) -> str:
    """Write this start's entry (instance not yet known) and return its nonce. Raises when it cannot be written: the
    caller then does not start the run."""
    from resource_explorer.run_credential_holder import process_identity

    key = start_key(slug, definition)
    nonce = uuid.uuid4().hex
    with _LOCK:
        now = _now()
        entries = _live(_read(registry, key), now)
        entries.append({"nonce": nonce, "at": now.isoformat(), "holder": process_identity(), "instance": ""})
        registry.set_setting(key, json.dumps(entries))
    return nonce


def _rewrite(registry, slug: str, definition: str, nonce: str, change) -> None:
    key = start_key(slug, definition)
    with _LOCK:
        entries = _live(_read(registry, key), _now())
        registry.set_setting(key, json.dumps([x for x in (change(e) if e.get("nonce") == nonce else e
                                                          for e in entries) if x is not None]))


def name_instance(registry, slug: str, definition: str, nonce: str, instance: str) -> None:
    """Name the instance Egeria returned on this start's entry: the entry then stands in for a marker that could not be
    written. Raises when it cannot be written."""
    _rewrite(registry, slug, definition, nonce, lambda e: {**e, "instance": instance})


def remove_start(registry, slug: str, definition: str, nonce: str) -> None:
    """Remove this start's entry. Raises when it cannot be written (the caller logs it; the TTL ends it)."""
    _rewrite(registry, slug, definition, nonce, lambda e: None)


def live_starts(registry, slug: str, definition: str) -> list[dict]:
    """The entries for this definition on this resource younger than the TTL, each with its `age` in seconds (None when
    its time is unreadable). Raises when they cannot be read: the caller refuses, never guesses."""
    now = _now()
    return [{**e, "age": _age_seconds(e, now)} for e in _live(_read(registry, start_key(slug, definition)), now)]
