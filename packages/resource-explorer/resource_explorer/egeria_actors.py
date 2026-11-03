"""RE in Egeria's actor graph, through pyegeria's ActorManager (backlog 7h, owner 2026-10-09).

Two jobs, and nothing else:

(a) **RE's own identity, at startup** (`ensure_re_identity_in_egeria`). Run once by the worker's
    leader-elected bootstrap, as the daemon (`Daemon(DaemonReason.IDENTITY_BOOTSTRAP)`):

    * the ITProfile `ITProfile::ResourceExplorer`;
    * a UserIdentity `UserIdentity::<daemon userId>` (the configured EGERIA_USER_ID);
    * the ProfileIdentity link between them.

    Each is ADOPTED by its qualifiedName when it is there (the owner created all three by hand on
    2026-10-09) and created only when Egeria said, plainly, that it is absent
    (`egeria_absence.is_absent`). Two elements under one qualifiedName, a UserIdentity for the
    daemon's userId under another qualifiedName, or the identity already linked to a different
    profile: RE refuses, creates nothing and reports. When the daemon's userId changes, a new
    UserIdentity is linked to the same ITProfile; the old link stays (RE has no delete path) and
    is reported. Never raises; a failure is a status ("could not check (<reason>)"), never a
    blocked start.

(b) **People, read-only** (`ownership_for_person`). When RE stamps Ownership for a person, it
    looks up that person's UserIdentity (by userId) and the ActorProfile (Person, ...) it is
    linked to. With a profile, Ownership follows Egeria's own code
    (`OpenLineageCataloguerIntegrationConnector.addOwnership`, egeria af400039c2): owner = the
    profile's qualifiedName, ownerTypeName = the profile's type name, ownerPropertyName =
    "qualifiedName". Without one (no UserIdentity, no profile, or the lookup failed), the
    userId form RE always wrote: owner = userId, ownerTypeName "UserIdentity",
    ownerPropertyName "userId". A failed lookup is logged, recorded for the connection popover,
    and never blocks the write. RE never creates an identity for a person (open question for the
    Egeria lead). Results are cached per process for `ACTOR_LOOKUP_TTL_SECONDS`, failures for
    `ACTOR_LOOKUP_FAILURE_TTL_SECONDS`; successes are also memoized per request/job scope (one
    press). Concurrent lookups for one person share one Egeria call.

Neither job sets `Ownership.userIds`, the property Egeria's access connector reads
(`isUserAnOwner`); RE has never set it, and changing that is a security decision, not this one.
The lookup never authorizes anything: it only chooses what Ownership names.

Known limits (on the Egeria-lead list):

* **Two RE deployments, one Egeria.** The bootstrap is idempotent per deployment (one leader
  lock in one registry). Two RE deployments with separate registries pointed at one Egeria could
  both see "absent" on a first run and both create; the next start of either then refuses the
  duplicate and reports it. Egeria's create has no upsert by qualifiedName.
* **Zones hide elements.** An ITProfile or UserIdentity in a zone RE's daemon cannot read comes
  back as no element, which reads as "absent": the bootstrap would create a second one, and a
  person's lookup gives the userId form.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from resource_explorer.egeria_absence import ABSENT, PRESENT, is_absent

log = logging.getLogger(__name__)

__all__ = [
    "IT_PROFILE_QN",
    "OwnershipShape",
    "actor_lookup_status",
    "ensure_re_identity_in_egeria",
    "identity_words",
    "ownership_for_person",
    "re_identity_status",
    "user_identity_qn",
]

IT_PROFILE_QN = "ITProfile::ResourceExplorer"
IT_PROFILE_DISPLAY_NAME = "Resource Explorer"

#: How long a person's profile lookup is reused in this process.
ACTOR_LOOKUP_TTL_SECONDS = int(os.environ.get("EXPLORER_ACTOR_LOOKUP_TTL", "300"))
#: How long a FAILED lookup is remembered (round 2): one slow Egeria costs one wait, not one per element.
ACTOR_LOOKUP_FAILURE_TTL_SECONDS = int(os.environ.get("EXPLORER_ACTOR_LOOKUP_FAILURE_TTL", "60"))
#: How long a write waits for the lookup before it falls back to the userId form.
ACTOR_LOOKUP_TIMEOUT_SECONDS = float(os.environ.get("EXPLORER_ACTOR_LOOKUP_TIMEOUT", "15"))

#: The userId form (what RE always stamped, and still stamps when no profile is found).
USER_ID_OWNER_TYPE = "UserIdentity"
USER_ID_OWNER_PROPERTY = "userId"
#: The profile form's ownerPropertyName, as Egeria's own OpenLineage cataloguer writes it.
PROFILE_OWNER_PROPERTY = "qualifiedName"

_PROVENANCE = {"createdByTool": "resource-explorer"}


def user_identity_qn(user_id: str) -> str:
    return f"UserIdentity::{user_id}"


# ---------------------------------------------------------------------------
# Reading Egeria's answers
# ---------------------------------------------------------------------------

def _guid(element: Any) -> str:
    if not isinstance(element, dict):
        return ""
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    return str(header.get("guid") or element.get("elementGUID") or element.get("guid") or "")


def _type_name(element: Any) -> str:
    if not isinstance(element, dict):
        return ""
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    typ = header.get("type") if isinstance(header.get("type"), dict) else {}
    return str(typ.get("typeName") or "")


def _props(element: Any) -> dict:
    if not isinstance(element, dict):
        return {}
    props = element.get("properties")
    return props if isinstance(props, dict) else {}


def _related(entry: Any) -> Any:
    """The element on the far side of a RelatedMetadataElementSummary."""
    if isinstance(entry, dict) and isinstance(entry.get("relatedElement"), dict):
        return entry["relatedElement"]
    return entry


class _Unreadable(Exception):
    """An Egeria answer RE cannot read as either 'there' or 'not there'."""


#: The pageSize every by-name search asks for (`_by_name_body`).
SEARCH_PAGE_SIZE = 100


def _elements(what: str, call: Callable[[], Any], page_size: int = SEARCH_PAGE_SIZE) -> list[dict]:
    """A search's elements. [] only when Egeria said 'none'; anything unreadable raises. A FULL
    page (round 5) may have more behind it, the element searched for among them: "could not
    check", never read as complete."""
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 - classified, never read as "none"
        if is_absent(exc) == ABSENT:
            return []
        raise _Unreadable(f"{what}: {type(exc).__name__}: {str(exc)[:200]}") from exc
    verdict = is_absent(result)
    if verdict == ABSENT:
        return []
    if verdict == PRESENT and isinstance(result, (list, dict)):
        items = result if isinstance(result, list) else [result]
        # Round 2: an element RE cannot parse (no GUID, no properties) might be the very one
        # searched for, so N answers with any unparseable one is "could not check", never "none".
        bad = [e for e in items if not (_guid(e) and isinstance(_props_or_none(e), dict))]
        if bad:
            raise _Unreadable(f"{what}: Egeria returned {len(items)} element(s), {len(bad)} RE cannot parse")
        if page_size and len(items) >= page_size:
            raise _Unreadable(f"{what}: a full page of {len(items)} element(s), possibly truncated")
        return list(items)
    raise _Unreadable(f"{what}: an answer RE cannot read ({type(result).__name__})")


def _props_or_none(element: Any) -> Optional[dict]:
    return element.get("properties") if isinstance(element, dict) else None


#: The type a search BY QUALIFIEDNAME asks for (round 3). Egeria's getRootElementsByName returns
#: only `metadataElementTypeName` and its subtypes, so asking for "ITProfile" hides a Person (or a
#: GlossaryTerm) that already holds `ITProfile::ResourceExplorer`: RE would read "absent", create a
#: twin, and its "is a Person, not an ITProfile" refusal could never fire. qualifiedName is a
#: Referenceable property, unique across every Referenceable, so the qualifiedName searches ask for
#: Referenceable and RE checks the type itself. Searches BY USERID ask for UserIdentity: userId is
#: a UserIdentity property, and the question there is "which UserIdentities carry it".
BY_QN_TYPE = "Referenceable"
BY_USER_ID_TYPE = "UserIdentity"


def _by_name_body(name: str, type_name: str) -> dict:
    """The search body, sent whole. pyegeria 6.1.15's `get_user_identities_by_name` /
    `get_actor_profiles_by_name` map their `metadata_element_type_name` argument onto a
    `metadata_element_type` key that `_async_get_name_request` does not read, so the type filter
    is silently dropped; a full FilterRequestBody carries `metadataElementTypeName` itself. The
    name is ALSO passed positionally, the same value as the body's filter: `get_actor_profiles_by_name`
    requires it (round 4, a live probe: body-only raised TypeError, so the bootstrap could never
    check), and every by-name call passes it for uniformity; the body still wins. RE's
    own qualifiedName/userId/type checks stay regardless. `type_name` is BY_QN_TYPE or
    BY_USER_ID_TYPE (see there)."""
    return {"class": "FilterRequestBody", "filter": name, "metadataElementTypeName": type_name,
            "graphQueryDepth": 3, "startFrom": 0, "pageSize": SEARCH_PAGE_SIZE}


def _element(what: str, call: Callable[[], Any]) -> dict:
    """One element by GUID; anything but an element raises."""
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001
        raise _Unreadable(f"{what}: {type(exc).__name__}: {str(exc)[:200]}") from exc
    if isinstance(result, dict) and _guid(result):
        return result
    raise _Unreadable(f"{what}: an answer RE cannot read ({type(result).__name__})")


def _profile_of_identity(identity: dict) -> Optional[dict]:
    """The ActorProfile a UserIdentity is linked to (`userProfile`, ProfileIdentity), or None."""
    linked = identity.get("userProfile")
    return _related(linked) if isinstance(linked, dict) and _guid(_related(linked)) else None


# ---------------------------------------------------------------------------
# (a) RE's own identity
# ---------------------------------------------------------------------------

PRESENT_WORD = "present"
CREATED_WORD = "created"
COULD_NOT_CHECK_WORD = "could not check"

_state_lock = threading.Lock()
_identity_state: Optional[dict] = None


def _set_state(state: dict) -> dict:
    global _identity_state
    state = {**state, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with _state_lock:
        _identity_state = state
    return dict(state)


def re_identity_status() -> dict:
    """The last bootstrap outcome in THIS process, for whoami and the popover.
    `{"status": "not checked"}` when the bootstrap has not run here (another process may hold it)."""
    with _state_lock:
        return dict(_identity_state) if _identity_state else {"status": "not checked"}


def identity_words(state: Optional[dict]) -> str:
    """'present' / 'created' / 'could not check (<reason>)' — the one line the log and popover show."""
    if not state or state.get("status") == "not checked":
        return "not checked in this process"
    status = state.get("status")
    extra = [n for n in (state.get("notes") or []) if n]
    if status == COULD_NOT_CHECK_WORD:
        return f"could not check ({state.get('reason') or 'no reason recorded'})"
    words = status or "unknown"
    if status == CREATED_WORD and state.get("created"):
        words = f"created ({', '.join(state['created'])})"
    return words + (f" · {'; '.join(extra)}" if extra else "")


def _actor_manager(reason) -> Any:
    """A `shared=False` ActorManager: the caller owns it and closes it (`_close`)."""
    from pyegeria import ActorManager

    from resource_explorer.egeria_clients import Daemon, egeria_client

    return egeria_client(Daemon(reason), purpose="actor graph", shared=False).of(ActorManager)


def _close(client: Any) -> None:
    """Close a client RE built (round 3: each lookup built one and never closed it). Tidy-up only."""
    close = getattr(client, "close_session", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - tidy-up, never the answer
            pass


def _one_by_qn(found: list[dict], qn: str, what: str) -> Optional[dict]:
    exact = [e for e in found if _props(e).get("qualifiedName") == qn]
    if len(exact) > 1:
        raise _Refused(f"{len(exact)} {what} elements named {qn!r} "
                       f"({', '.join(_guid(e) for e in exact)}); RE will not pick one")
    return exact[0] if exact else None


class _Refused(Exception):
    """A state RE will not resolve by itself (duplicates, a conflicting link)."""


def ensure_re_identity_in_egeria(client: Any = None) -> dict:
    """Ensure the ITProfile, RE's UserIdentity and their ProfileIdentity link. Never raises.

    Returns (and records for `re_identity_status`) a dict with `status` 'present' | 'created' |
    'could not check', `reason` (when could not check), `created` (which of 'ITProfile',
    'UserIdentity', 'ProfileIdentity' this call wrote), the two GUIDs, and `notes` (things RE
    reports but leaves alone, e.g. an earlier daemon userId still linked).
    """
    from resource_explorer.egeria_clients import (
        NO_SERVICE_ACCOUNT_SENTENCE, DaemonReason, NoServiceAccount, _daemon_credential)

    try:
        user_id = (_daemon_credential()[0] or "").strip()
    except NoServiceAccount as exc:
        # The seam refuses (no persona default). The start-up check reports; it never raises.
        reason = ("no daemon userId is configured" if str(exc) == NO_SERVICE_ACCOUNT_SENTENCE else str(exc))
        return _set_state({"status": COULD_NOT_CHECK_WORD, "reason": reason})
    if not user_id:
        return _set_state({"status": COULD_NOT_CHECK_WORD, "reason": "no daemon userId is configured"})
    created: list[str] = []
    notes: list[str] = []
    profile_guid = identity_guid = ""
    built = None
    try:
        am = client
        if am is None:
            am = built = _actor_manager(DaemonReason.IDENTITY_BOOTSTRAP)

        # 1. The ITProfile, adopted by qualifiedName. Searched across every Referenceable
        #    (BY_QN_TYPE), so an element of another type holding the name is seen and refused.
        profiles = _elements("ITProfile search", lambda: am.get_actor_profiles_by_name(
            IT_PROFILE_QN, body=_by_name_body(IT_PROFILE_QN, BY_QN_TYPE), output_format="JSON"))
        profile = _one_by_qn(profiles, IT_PROFILE_QN, "profile")
        if profile is not None and _type_name(profile) != "ITProfile":
            raise _Refused(f"{IT_PROFILE_QN!r} is a {_type_name(profile) or 'element of unknown type'}, "
                           "not an ITProfile")
        if profile is None:
            profile_guid = am.create_actor_profile({
                "class": "NewElementRequestBody", "isOwnAnchor": True,
                "properties": {
                    "class": "ITProfileProperties", "typeName": "ITProfile",
                    "qualifiedName": IT_PROFILE_QN, "displayName": IT_PROFILE_DISPLAY_NAME,
                    "description": ("IT profile for Resource Explorer (trellis), which runs background "
                                    "survey, analysis and publish work under its own userId."),
                    "additionalProperties": dict(_PROVENANCE)}})
            if not isinstance(profile_guid, str) or not profile_guid:
                raise _Unreadable(f"the ITProfile create returned no GUID ({type(profile_guid).__name__})")
            created.append("ITProfile")
        else:
            profile_guid = _guid(profile)

        # 2. The daemon's UserIdentity, adopted by qualifiedName. A UserIdentity carrying the same
        #    userId under ANOTHER qualifiedName is refused: a second one would make Egeria's
        #    by-userId lookups ambiguous.
        qn = user_identity_qn(user_id)
        by_qn = _elements("UserIdentity search", lambda: am.get_user_identities_by_name(
            qn, body=_by_name_body(qn, BY_QN_TYPE), output_format="JSON"))
        by_uid = _elements("UserIdentity search", lambda: am.get_user_identities_by_name(
            user_id, body=_by_name_body(user_id, BY_USER_ID_TYPE), output_format="JSON"))
        merged = {(_guid(e) or str(i)): e for i, e in enumerate([*by_qn, *by_uid])}
        found = list(merged.values())
        identity = _one_by_qn(found, qn, "UserIdentity")
        if identity is not None and _type_name(identity) != "UserIdentity":
            raise _Refused(f"{qn!r} is a {_type_name(identity) or 'element of unknown type'}, "
                           "not a UserIdentity")
        if identity is None:
            others = [e for e in found if _props(e).get("userId") == user_id]
            if others:
                raise _Refused(
                    f"a UserIdentity for userId {user_id!r} already exists as "
                    f"{', '.join(repr(_props(e).get('qualifiedName')) for e in others)}; RE adopts only "
                    f"{qn!r} and will not create a second identity for one userId")
            identity_guid = am.create_user_identity({
                "class": "NewElementRequestBody", "isOwnAnchor": True,
                "properties": {"class": "UserIdentityProperties", "typeName": "UserIdentity",
                               "qualifiedName": qn, "userId": user_id,
                               "additionalProperties": dict(_PROVENANCE)}})
            if not isinstance(identity_guid, str) or not identity_guid:
                raise _Unreadable(f"the UserIdentity create returned no GUID ({type(identity_guid).__name__})")
            created.append("UserIdentity")
        else:
            identity_guid = _guid(identity)

        # 3. The ProfileIdentity link, read from the identity itself.
        current = _element("UserIdentity read", lambda: am.get_user_identity_by_guid(
            identity_guid, output_format="JSON"))
        linked = _profile_of_identity(current)
        if linked is not None and _guid(linked) != profile_guid:
            raise _Refused(
                f"{qn!r} is already linked to another profile "
                f"({_props(linked).get('qualifiedName') or _guid(linked)}); RE will not link it twice")
        if linked is None:
            am.link_identity_to_profile(identity_guid, profile_guid, {
                "class": "NewRelationshipRequestBody",
                "properties": {"class": "ProfileIdentityProperties",
                               "description": "Resource Explorer's own account (DIGITAL) in the platform user directory."}})
            created.append("ProfileIdentity")

        # 4. What else the ITProfile carries: an earlier daemon userId stays linked (RE has no
        #    delete path), reported so a person can retire it.
        try:
            prof = _element("ITProfile read", lambda: am.get_actor_profile_by_guid(profile_guid, output_format="JSON"))
            old = sorted({str(_props(_related(e)).get("userId") or _props(_related(e)).get("qualifiedName") or _guid(_related(e)))
                          for e in (prof.get("userIdentities") or []) if _guid(_related(e)) != identity_guid})
            if old:
                notes.append(f"also linked to {IT_PROFILE_QN}: {', '.join(old)} (an earlier daemon userId? "
                             "RE has no delete path; retire it by hand if it is no longer used)")
        except _Unreadable as exc:
            notes.append(f"other linked identities not read ({exc})")
    except _Refused as exc:
        return _set_state({"status": COULD_NOT_CHECK_WORD, "reason": f"refused: {exc}", "created": created,
                           "it_profile_guid": profile_guid, "user_identity_guid": identity_guid,
                           "user_id": user_id})
    except Exception as exc:  # noqa: BLE001 - reported; startup never waits on this
        reason = str(exc) if isinstance(exc, _Unreadable) else f"{type(exc).__name__}: {str(exc)[:200]}"
        return _set_state({"status": COULD_NOT_CHECK_WORD, "reason": reason, "created": created,
                           "it_profile_guid": profile_guid, "user_identity_guid": identity_guid,
                           "user_id": user_id})
    finally:
        if built is not None:
            _close(built)
    return _set_state({"status": CREATED_WORD if created else PRESENT_WORD, "created": created,
                       "it_profile_guid": profile_guid, "user_identity_guid": identity_guid,
                       "user_id": user_id, "notes": notes})


# ---------------------------------------------------------------------------
# (b) People: look up, never create
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OwnershipShape:
    """What one Ownership classification names. `form` is 'profile' or 'userId'; `note` says why
    the userId form was used when a lookup was attempted and failed ('' otherwise)."""

    owner: str
    owner_type_name: str
    owner_property_name: str
    form: str
    note: str = ""


def _user_id_form(user_id: str, note: str = "") -> OwnershipShape:
    return OwnershipShape(user_id, USER_ID_OWNER_TYPE, USER_ID_OWNER_PROPERTY, "userId", note)


_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, OwnershipShape]] = {}
#: Round 3: the outstanding failure PER USER (a success for one person never hides another's).
#: Insertion order is recency: a re-failure is popped and re-added.
_lookup_failures: dict[str, dict] = {}


class _Flight:
    """One lookup in progress for one user; concurrent callers wait on it (round 3)."""

    __slots__ = ("done", "error", "shape", "started")

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.done = threading.Event()
        self.shape: Optional[OwnershipShape] = None
        self.error: Optional[BaseException] = None


_inflight: dict[str, _Flight] = {}


def _record_failure(user_id: str, reason: str) -> None:
    with _cache_lock:
        _lookup_failures.pop(user_id, None)
        _lookup_failures[user_id] = {"user_id": user_id, "reason": reason,
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _clear_failure(user_id: str) -> None:
    with _cache_lock:
        _lookup_failures.pop(user_id, None)


def actor_lookup_status(user_id: Optional[str] = None) -> Optional[dict]:
    """The outstanding failed profile lookup for `user_id` (stripped, as `ownership_for_person`
    strips it) in this process, or None (its last lookup here succeeded, or none failed). With no
    `user_id` (no signed-in caller) always None: round 5, never another user's failure."""
    user_id = (user_id or "").strip()
    if not user_id:
        return None
    with _cache_lock:
        rec = _lookup_failures.get(user_id)
        return dict(rec) if rec else None


def clear_actor_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _lookup_failures.clear()
        _inflight.clear()     # a running lookup finishes on its own and is no longer joined


def _lookup(user_id: str, client: Any = None) -> OwnershipShape:
    """One person's profile lookup. A client this call builds is closed before it returns."""
    from resource_explorer.egeria_clients import DaemonReason

    if client is not None:
        return _lookup_with(user_id, client)
    am = _actor_manager(DaemonReason.ACTOR_LOOKUP)
    try:
        return _lookup_with(user_id, am)
    finally:
        _close(am)


def _lookup_with(user_id: str, am: Any) -> OwnershipShape:
    found = _elements("UserIdentity search", lambda: am.get_user_identities_by_name(
        user_id, body=_by_name_body(user_id, BY_USER_ID_TYPE), output_format="JSON"))
    mine = [e for e in found if _props(e).get("userId") == user_id]
    if not mine:
        return _user_id_form(user_id)
    if len(mine) > 1:
        raise _Unreadable(f"{len(mine)} UserIdentities carry userId {user_id!r}; RE will not pick one")
    profile = _profile_of_identity(mine[0])
    if profile is None or not _type_name(profile):
        # The search's graph may stop short of the profile: read the identity itself.
        full = _element("UserIdentity read", lambda: am.get_user_identity_by_guid(_guid(mine[0]), output_format="JSON"))
        profile = _profile_of_identity(full)
    if profile is None:
        return _user_id_form(user_id)
    qn, type_name = _props(profile).get("qualifiedName"), _type_name(profile)
    if not qn or not type_name:
        raise _Unreadable(f"the profile linked to {user_id!r} came back without a qualifiedName or type")
    return OwnershipShape(str(qn), type_name, PROFILE_OWNER_PROPERTY, "profile")


def _press_memo() -> Optional[dict]:
    """This request's / job's client scope (one press, one publish run), or None outside one.
    Lookups are memoized there so one person costs one lookup per press, whatever the caches say."""
    from resource_explorer.egeria_clients import current_scope

    return current_scope()


def _bounded_lookup(user_id: str) -> "OwnershipShape":
    """`_lookup` on its own daemon thread, waited for at most `ACTOR_LOOKUP_TIMEOUT_SECONDS`.

    Not the shared pool: called FROM a shared-pool thread, `concurrency.run_sync` runs inline with
    no bound at all (its re-entrancy rule), and a dedicated pool is what `concurrency` exists to
    prevent. One short-lived thread per actual lookup is cheap because lookups are cached; a stuck
    one is abandoned (daemon) and its late answer discarded. Round 3: one thread per USER at a
    time. A caller that finds a lookup for the same user in flight waits on it (each waiter still
    bounded by the timeout) instead of starting another, and the thread closes the client
    `_lookup` built. Round 5: a flight older than the timeout is replaced (a leader stuck in
    pyegeria's asyncio hang must not pin the user; its `finally` removes only its own entry), and
    a thread that cannot start resolves its flight and re-raises."""
    with _cache_lock:
        flight = _inflight.get(user_id)
        if flight is not None and time.monotonic() - flight.started >= ACTOR_LOOKUP_TIMEOUT_SECONDS:
            flight = None                                  # stuck: abandon it, start afresh
        leader = flight is None
        if leader:
            flight = _inflight[user_id] = _Flight()

    if leader:
        def run() -> None:
            try:
                flight.shape = _lookup(user_id)
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting callers
                flight.error = exc
            finally:
                with _cache_lock:
                    if _inflight.get(user_id) is flight:
                        del _inflight[user_id]
                flight.done.set()

        try:
            threading.Thread(target=run, name=f"re-actor-lookup-{user_id}", daemon=True).start()
        except BaseException as exc:
            with _cache_lock:
                if _inflight.get(user_id) is flight:
                    del _inflight[user_id]
            flight.error = exc
            flight.done.set()
            raise

    if not flight.done.wait(ACTOR_LOOKUP_TIMEOUT_SECONDS):
        raise _Unreadable(f"no answer within {ACTOR_LOOKUP_TIMEOUT_SECONDS:g}s")
    if flight.error is not None:
        raise flight.error
    return flight.shape


def ownership_for_person(user_id: str, *, client: Any = None) -> OwnershipShape:
    """The Ownership shape for a person's userId. Never raises; never creates anything.

    Found and not-found answers are cached for `ACTOR_LOOKUP_TTL_SECONDS`; a FAILURE (Egeria
    unreadable, ambiguous, slow) is cached too, for `ACTOR_LOOKUP_FAILURE_TTL_SECONDS`, as the userId
    form with `note` set, so a press over many elements asks once, not once per element. Inside a
    request or job scope a SUCCESS is also memoized for that scope (round 3: a failure is not, so
    a long job asks again once the failure TTL has passed). Every lookup is bounded by
    `ACTOR_LOOKUP_TIMEOUT_SECONDS`. A failure logs one WARNING and is recorded for
    `actor_lookup_status(user_id)`; the next successful lookup for THE SAME user clears it."""
    user_id = (user_id or "").strip()
    if not user_id:
        return _user_id_form(user_id)
    memo = _press_memo()
    memo_key = ("re_actor_lookup", user_id)
    if memo is not None and memo_key in memo:
        return memo[memo_key]
    now = time.time()
    with _cache_lock:
        hit = _cache.get(user_id)
        if hit and hit[0] > now:
            if memo is not None and not hit[1].note:      # successes only (a failure has a note)
                memo[memo_key] = hit[1]
            return hit[1]
    try:
        if client is not None:
            shape = _lookup(user_id, client)
        else:
            shape = _bounded_lookup(user_id)
        ttl = ACTOR_LOOKUP_TTL_SECONDS
        _clear_failure(user_id)             # round 3: this user's record only
    except Exception as exc:  # noqa: BLE001 - a lookup never blocks the write
        reason = str(exc) if isinstance(exc, _Unreadable) else f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("egeria: profile lookup for %r failed — Ownership uses the userId form "
                    "(not asked again for %ss): %s", user_id, ACTOR_LOOKUP_FAILURE_TTL_SECONDS, reason)
        _record_failure(user_id, reason)
        shape = _user_id_form(user_id, note=f"profile lookup failed: {reason}")
        ttl = ACTOR_LOOKUP_FAILURE_TTL_SECONDS
    with _cache_lock:
        _cache[user_id] = (time.time() + ttl, shape)
    if memo is not None and not shape.note:
        memo[memo_key] = shape
    return shape
