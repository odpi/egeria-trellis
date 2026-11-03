"""A queued run publishes AS the person who pressed it (architect ruling, option a, 2026-10-21).

The evidence: a synchronous press wrote to Egeria as the person (`createdBy = peterprofile`), but the same press
queued (`run_queue` -> the survey definition executor's auto-publish) wrote the SurveyReport and its annotations as the
service account (`createdBy = resourceexplorernpa`). That breaks the owner rule "run as the initiating user; service
account only when automated with no known user".

What this module holds
----------------------
The presser's own Egeria bearer token, **in this process's memory only**, keyed by the run's id, from the press until
the run ends. It is held the way any per-sign-in secret should be (kept in memory, nowhere
else): nothing here is written to the registry, a run row, the activity log, a log line, an exception message or
Egeria. `HeldSession.__repr__` hides the token and the holder has no listing and no iteration. The one reader is
`publishing_as_presser`, below.

* **Where held:** `hold()` is called by the press route (`web/routes/survey_definitions.py`) with the token the request
  carries, BEFORE the run row is enqueued (the run id is made first), so a worker in the same process can never claim
  a row whose token is not there yet.
* **Lifetime:** released when the run ends (`released_on_exit`, around `execute_run`), at the token's own `exp`, or at the cap
  (`RE_PRESSER_HOLD_TTL_SECONDS`, default 2 hours, never unbounded), whichever is first; and at process exit.
* **Cross-process (claim affinity, owner 2026-10-21):** the press route stamps a non-secret `held_by` (the holding
  process's pid + start time, and the hold's end time) into the run's target JSON, no new column. `claimable_by`,
  called by `claim_next_run`, makes a LIVE other process skip that row, so only the holder claims it. When the holder
  is gone (dead per the reconciler's liveness check) or its hold has run out, anyone may claim it, finds nothing held,
  and the run ends "published: no · your sign-in expired · press Publish on the card", writing nothing as the
  service account. "Cannot tell if the holder is alive" counts as alive until the hold's end time.

What it changes
---------------
Only the run's **publish** (`publishing_as_presser`, wrapped around the executor's `publish_fn` call). The run's
measuring, reads and step execution stay `Daemon(RUN_QUEUE, requested_by)`: a token that ages out mid-run must not
turn a measurement into a failure, and reading as the service account is not the attribution the owner asked about.
A run is "pressed" only when its params say so (`publish_as_presser`, set by the press route). A scheduled run, an
Egeria-dispatched one or a batch with no person is not, and keeps the service account.
"""
from __future__ import annotations

import atexit
import math
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterator

DEFAULT_TTL_SECONDS = 2 * 3600
TTL_ENV = "RE_PRESSER_HOLD_TTL_SECONDS"

#: The run's publish word when the presser's session is not there to publish with. Fixed vocabulary: it starts
#: "published: no", which the card draws as a warning with these words, never as a published run.
SIGN_IN_EXPIRED_WORDS = "published: no · your sign-in expired · press Publish on the card"


class PresserSignInExpired(Exception):
    """The pressed run reached its publish and the presser's Egeria session is gone (expired, never held in this
    process, or released). Nothing was written. `str(exc)` is the run's publish word."""

    def __init__(self, words: str = SIGN_IN_EXPIRED_WORDS) -> None:
        super().__init__(words)


def configured_ttl() -> float:
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
class HeldSession:
    """One held session. `token` is excluded from every generated text form."""

    person: str
    token: str = field(repr=False)
    expires_at: float = 0.0

    def __repr__(self) -> str:                  # never the token, in any spelling
        return f"HeldSession(person={self.person!r}, token=<hidden>)"

    __str__ = __repr__


class _Holder:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, HeldSession] = {}

    def __repr__(self) -> str:
        with self._lock:
            return f"<presser hold: {len(self._entries)} held>"

    def put(self, run_id: str, person: str, token: str, *, not_after: float | None = None,
            ttl: float | None = None) -> None:
        expires = self._clock() + (configured_ttl() if ttl is None else ttl)
        if not_after is not None:
            expires = min(expires, float(not_after))
        with self._lock:
            now = self._clock()
            for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
                del self._entries[key]
            self._entries[run_id] = HeldSession(person=person, token=token, expires_at=expires)

    def take(self, run_id: str) -> HeldSession | None:
        """The held session, or None. An entry past its expiry is dropped here and never returned."""
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[run_id]
                return None
            return entry

    def release(self, run_id: str) -> None:
        with self._lock:
            self._entries.pop(run_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()


_HOLDER = _Holder()
atexit.register(_HOLDER.clear_all)

take = _HOLDER.take
release = _HOLDER.release
clear_all = _HOLDER.clear_all


@contextmanager
def released_on_exit(run_id: str) -> Iterator[None]:
    """Release `run_id`'s hold on EVERY way out of the block: a return, a raise, or a normal end. A `with`, not a
    try/finally at the call site, so `run_queue.execute_run` keeps its structure (its usage log stays outside every
    Try, which `test_llm_usage` pins)."""
    try:
        yield
    finally:
        release(run_id)


def hold(run_id: str, person: str, token: str | None) -> bool:
    """Keep the presser's session for `run_id`. False (nothing held) when there is no run id, person or token.

    The hold ends at the token's own expiry when it states one, so a held session is never offered past it."""
    if not (run_id and person and token):
        return False
    not_after = None
    try:
        from trellis_auth.auth import egeria_token_expiry

        exp = egeria_token_expiry(token)
        not_after = float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 - an unreadable expiry is simply not a tighter bound
        not_after = None
    _HOLDER.put(run_id, person, token, not_after=not_after)
    return True


_HOST: dict | None = None


def host_identity() -> dict:
    """This machine's identity: its hostname, plus the kernel boot id where that is a cheap file read (Linux).
    Read once and cached. A pid and start time mean nothing on another host or in another pid namespace."""
    global _HOST
    if _HOST is None:
        import socket

        boot = ""
        try:
            from pathlib import Path

            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            boot = ""
        _HOST = {"host": socket.gethostname(), "boot_id": boot}
    return _HOST


def held_by_for(run_id: str) -> dict | None:
    """The non-secret claim-affinity stamp for a run this process holds: who holds it, on which host, and until when.
    None when nothing is held for `run_id`, or when this process's own start time cannot be read (a stamp without it
    could not be matched, so the run is not stamped). Carries no credential. Blocking (may fork `ps` once): call it
    off the event loop."""
    from resource_explorer.run_reconciler import process_identity

    entry = _HOLDER.take(run_id)
    identity = process_identity()
    if entry is None or not identity.get("started_at"):
        return None
    return {**identity, **host_identity(), "until": entry.expires_at}


def claimable_by(held_by: object, claimer: dict | None, *, now: float | None = None,
                 alive_cache: dict | None = None) -> bool:
    """May the process `claimer` (a `process_identity()`) claim a row whose target carries this `held_by` stamp?

    True for a row with no stamp (not pressed, or pressed with nothing held), for the holder itself, and for any
    process once the holder is provably dead or its hold has ended. False only for a row held by ANOTHER process that
    is alive or whose liveness cannot be told, inside the hold's time. A holder on a different host (or boot) is
    "cannot tell": a pid check across hosts or pid namespaces is meaningless, so it waits until `until`.
    `alive_cache` memoises the liveness check across one claim call. Pure apart from that check."""
    from resource_explorer.run_reconciler import _is_alive

    if not isinstance(held_by, dict) or not held_by:
        return True
    claimer = claimer or {}
    here = host_identity()
    same_host = (held_by.get("host") == here["host"]
                 and (held_by.get("boot_id") or "") == (here["boot_id"] or ""))
    if (same_host and held_by.get("pid") == claimer.get("pid")
            and held_by.get("started_at") == claimer.get("started_at")):
        return True
    try:
        until = float(held_by.get("until"))
    except (TypeError, ValueError):
        until = 0.0
    if (time.time() if now is None else now) >= until:
        return True
    if not same_host:
        return False
    key = (held_by.get("pid"), held_by.get("started_at"))
    if alive_cache is not None and key in alive_cache:
        return alive_cache[key] is False
    alive = _is_alive(held_by)
    if alive_cache is not None:
        alive_cache[key] = alive
    return alive is False


#: (run id, person) of the pressed run this context is executing; None everywhere else.
_PRESSED: ContextVar["tuple[str, str] | None"] = ContextVar("re_pressed_run", default=None)


@contextmanager
def pressed_run(run_id: str, person: str) -> Iterator[None]:
    """Mark the code in this block as executing the run `run_id` that `person` pressed."""
    reset = _PRESSED.set((run_id, person))
    try:
        yield
    finally:
        _PRESSED.reset(reset)


def is_pressed() -> bool:
    return _PRESSED.get() is not None


@contextmanager
def publishing_as_presser() -> Iterator[None]:
    """Wrap a pressed run's Egeria WRITE: inside, `current_principal()` is the presser (their held token).

    Outside a pressed run it does nothing, so every other run keeps the identity it had. In a pressed run with no
    usable held session it raises `PresserSignInExpired` BEFORE the block runs: nothing is written, and never as the
    service account."""
    pressed = _PRESSED.get()
    if pressed is None:
        yield
        return
    run_id, person = pressed
    held = take(run_id)
    if held is None or held.person != person or not _token_alive(held.token):
        raise PresserSignInExpired()
    from resource_explorer.egeria_clients import acting_as_held_caller

    with acting_as_held_caller(held.person, held.token):
        yield


def _token_alive(token: str) -> bool:
    try:
        from trellis_auth.auth import egeria_token_expiry

        exp = egeria_token_expiry(token)
    except Exception:  # noqa: BLE001 - unreadable: let Egeria answer; it refuses an expired token itself
        return True
    return exp is None or exp > time.time()


# ---------------------------------------------------------------------------
# Proof on screen: who the report says created it
# ---------------------------------------------------------------------------

def created_by_of(report: object) -> str:
    """`versions.createdBy` from an Egeria element read (on `elementHeader`, else the element); '' when it states none.

    Pure. '' is "Egeria stated no creator in what was read", never a guess."""
    if not isinstance(report, dict):
        return ""
    header = report.get("elementHeader") if isinstance(report.get("elementHeader"), dict) else {}
    for versions in ((header or {}).get("versions"), report.get("versions")):
        if isinstance(versions, dict) and versions.get("createdBy"):
            return str(versions["createdBy"])
    return ""


def read_published_by(report_guid: str) -> str:
    """Who Egeria says created the SurveyReport `report_guid`: ONE read by GUID, as whoever the run is acting as.

    The run's "published as <identity>" comes from this read and from nothing the code path believes about itself, so a
    regression to the service account shows on the card. '' when the read could not be made or Egeria stated no
    creator: the caller says nothing, which is "could not tell", not "as nobody"."""
    return created_by_of(_read_element_by_guid(report_guid))


def _read_element_by_guid(guid: str, *, for_lineage: bool = False) -> object:
    """ONE read of the element `guid` by GUID, as whoever the run is acting as; None when it could not be made.
    `for_lineage` also reads an archived element (a leave-out's archived copy carries a Memento)."""
    if not guid:
        return None
    try:
        from pyegeria import AssetMaker

        from resource_explorer.config import egeria_platform_url, egeria_view_server
        from resource_explorer.egeria_clients import current_principal, egeria_client

        maker = egeria_client(current_principal(), purpose="publish read-back",
                              view_server=egeria_view_server(), platform_url=egeria_platform_url()).of(AssetMaker)
        body = {"class": "GetRequestBody", "graphQueryDepth": 0, **({"forLineage": True} if for_lineage else {})}
        return maker.get_asset_by_guid(guid, body=body, output_format="JSON")
    except Exception:  # noqa: BLE001 - unread is "could not tell"; the message may carry a token, so it is not kept
        return None


def written_by_of(element: object) -> str:
    """`versions.updatedBy`, else `versions.createdBy`, from an Egeria element read: the last identity Egeria says
    wrote it. '' when it states neither. Pure."""
    if not isinstance(element, dict):
        return ""
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    for versions in ((header or {}).get("versions"), element.get("versions")):
        if isinstance(versions, dict) and (versions.get("updatedBy") or versions.get("createdBy")):
            return str(versions.get("updatedBy") or versions["createdBy"])
    return ""


#: The proof half of a pressed write path's identity line, when the read-back could not say who wrote.
WRITTEN_AS_UNCHECKED = "written as: could not check"


def version_time_of(element: object) -> datetime | None:
    """`versions.updateTime`, else `versions.createTime`, from an Egeria element read, as an aware UTC datetime (epoch
    ms or ISO); None when it states neither or the value cannot be read. Pure."""
    if not isinstance(element, dict):
        return None
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    for versions in ((header or {}).get("versions"), element.get("versions")):
        if not isinstance(versions, dict):
            continue
        raw = versions.get("updateTime") or versions.get("createTime")
        if raw in (None, ""):
            continue
        text = str(raw).strip()
        try:
            dt = (datetime.fromtimestamp(int(text) / 1000, tz=UTC) if text.isdigit()
                  else datetime.fromisoformat(text.replace("Z", "+00:00")))
        except (ValueError, OverflowError, OSError):
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def written_as_since(element_guids: list[str], since: datetime | None, *, wrote: bool) -> str:
    """'written as <x>' for elements THIS run wrote: <x> from ONE read of each `element_guids` (in order, stopping at
    the first that proves it) by GUID, made as whoever the run is acting as; updatedBy, else createdBy. Said only when
    `wrote` (at least one write step succeeded) and the element's version time is not older than `since` (the run's
    start), so a writer from an earlier commit is never named. `WRITTEN_AS_UNCHECKED` otherwise: nothing written, the
    read failed, Egeria named nobody or no time, or the element was last changed before this run. The proof beside
    the intent line (`applying_as_line`): never derived from the branch the code took."""
    if not wrote or since is None:
        return WRITTEN_AS_UNCHECKED
    for guid in [g for g in element_guids if g]:
        element = _read_element_by_guid(guid, for_lineage=True)
        who, when = written_by_of(element), version_time_of(element)
        if who and when is not None and when >= since:
            return f"written as {who}"
    return WRITTEN_AS_UNCHECKED


def read_published_by_presser(report_guid: str) -> str:
    """`read_published_by`, made INSIDE `publishing_as_presser`: the read runs as the presser (a pressed run), so a
    zone-restricted SurveyReport the presser can see is readable; any other run reads as it acts. '' when the read
    could not be made (including a presser session that has gone): the caller says "creator not read"."""
    if not report_guid:
        return ""
    try:
        with publishing_as_presser():
            return read_published_by(report_guid)
    except PresserSignInExpired:
        return ""


def expired_during_publish(exc: BaseException) -> bool:
    """True when `exc` (or what it was raised from) is the presser's token ageing out, in a pressed run only."""
    if not is_pressed():
        return False
    from resource_explorer.egeria_clients import CallerTokenExpired

    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, CallerTokenExpired):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


#: Said in the run detail when the sign-in expired part-way through the publish.
PARTIAL_WRITE_NOTE = ("The sign-in expired while publishing: some writes may already have been made as the person who "
                      "pressed; press Publish on the card to finish.")


# ---------------------------------------------------------------------------
# The identity line: who a pressed write path wrote as (2026-10-23)
# ---------------------------------------------------------------------------

#: Where the identity came from, in "applying as <user> from <source>" (the dr_egeria/bootstrap identity line's shape).
SOURCE_PRESSER = "the sign-in of the person who pressed"
SOURCE_SERVICE_ACCOUNT = "the service account"


def applying_as_line(identity: object) -> str:
    """'applying as <user> from <source>' for `identity`, what `current_principal()` returned where the writes are
    about to be made. An INTENT line, not proof: who Egeria says wrote is `written_as_since`, from a read-back. '' for
    no identity. Holds no credential."""
    if identity is None:
        return ""
    kind = getattr(identity, "kind", "")
    user = getattr(identity, "client_user", "") or getattr(identity, "user_id", "")
    source = SOURCE_PRESSER if kind == "caller" else SOURCE_SERVICE_ACCOUNT
    requester = getattr(identity, "requested_by", "") or ""
    if kind != "caller" and requester:
        source = f"{source}, on behalf of {requester}"
    return f"applying as {user} from {source}"
