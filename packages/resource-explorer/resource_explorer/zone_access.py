"""May this person UPDATE an element, as Egeria's zone security would answer? (Brief Z, 2026-10-10)

The owner's rule, verbatim: "rely on Egeria's security model and emulate it - so if zones are used they can
control access - if zones aren't being used then it is open". `workflows/curate.curation_access` owns the
decision (sign-in, the Portal role override, "zones not in use"); this module is only the Egeria half: given
the zones that govern an element, does Egeria's access connector grant this user an update on it?

Mirrored from Egeria's `OpenMetadataAccessSecurityConnector` (egeria-v6/egeria,
open-metadata-implementation/adapters/open-connectors/metadata-security-connectors/
open-metadata-access-security-connector/src/main/java/org/odpi/openmetadata/metadatasecurity/accessconnector/
OpenMetadataAccessSecurityConnector.java at egeria af400039c2, read 2026-10-10):

* `validateUserForElementDetailUpdate` (:1497-1522) checks `validateZoneAccess` with
  `AccessOperation.UPDATE_PROPERTIES`: what a verdict needs. Publish needs more (see the constants below).
* `validateZoneAccess` (:1090-1206) loops the element's `ZoneMembership`:
    - a zone whose name equals the userId grants outright (:1122-1125);
    - a zone with NO associated security list "is not a secured zone and is ignored" (:1130-1134);
    - a secured zone grants when its list holds `allUsers` (:1141-1144), the account type's group
      (:1149-1155), one of the account's security groups (:1157-1166) or roles (:1168-1177), or one of the
      instance groups (:1179-1189);
    - after the loop, any secured zone seen and none granted => denied (:1195-1198); otherwise allowed (:1205).
* `getAssociatedSecurityListForZone` (:911-958) reads the control named after the zone and takes the list for
  the operation's enum NAME, falling back to `DEFAULT`; no control, or neither key => `null` (not secured).
* `getInstanceBasedGroups` (:1218-1248): the userId itself; `instanceOwner` when `isUserAnOwner`
  (:1260-1286: TRUE when the element has no `Ownership`, or its `userIds` holds the user); and
  `existingMaintainer` / `newMaintainer` from the element's maintainers.
* Group names are the connector's defaults (`controls/OpenMetadataSecurityConfigurationProperty.java`
  :46-110): allUsers, instanceOwner, existingMaintainer, newMaintainer, employeeUsers, contractUsers,
  externalUsers, digitalUsers. A deployment that renamed them in the connector's configuration would need
  the same names here (named in the module constants below; RE cannot read the connector's config).

**One deliberate departure: unreadable DENIES.** Egeria's own `getAssociatedSecurityListForZone` logs a
secrets-store failure and returns null (:943-957), i.e. treats the zone as unsecured. RE cannot see that log
and must not open by default when zones might apply (Brief Z rule 5), so any read that fails here raises
`AccessUnreadable` and the caller denies with "could not check access in Egeria (<reason>)".

**The account is read first for any zoned element** (round 2), as Egeria does (:1103): an unknown or
disabled account is refused before any zone is looked at. Only with no zone at all (the owner's "not in use"
rule) is nothing read.

**Every operation the write performs is checked** (round 2): Publish creates (CREATE), updates
(UPDATE_PROPERTIES), attaches (ATTACH, both ends), classifies (CLASSIFY) and rezones (PUBLISH). The daemon does
the write, so RE's check is the only one that sees the person.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, NamedTuple, Optional

log = logging.getLogger(__name__)

#: `AccessOperation` enum NAMES (frameworks/open-connector-framework/.../users/AccessOperation.java): the keys
#: of a control's `associatedSecurityList` (`getAssociatedSecurityListForZone` reads `operation.name()`, then
#: DEFAULT). Which validator checks which (OpenMetadataAccessSecurityConnector.java):
#:   CREATE             validateUserForElementCreate :1306-1330 (zones the NEW element is created in; the
#:                      creator is passed as its only maintainer)
#:   UPDATE_PROPERTIES  validateUserForElementDetailUpdate :1497-1522
#:   ATTACH             validateUserForElementAttach :1582-1620, on BOTH ends
#:   CLASSIFY           validateUserForElementClassify :1840 (any classification but ZoneMembership)
#:   PUBLISH            validateUserForElementClassify :1816 / Declassify :1911 (a ZoneMembership change)
#:   DETACH             validateUserForElementDetach :1639-1694, on BOTH ends (Brief Z2: a leave-out's target removal)
#:   DELETE             validateUserForElementDelete :2540-2566 (archive, soft delete or purge: Brief Z2's leave-out
#:                      and bulk delete, both still behind the archive-cascade block)
#:   READ               the member side of every anchored-member check (validateUserForAnchorAttach :2129-2135)
#:   DELETE_MEMBER      validateUserForAnchorMemberDelete :2481-2500 (on the ANCHOR's zones)
#:   DECLASSIFY         validateUserForElementDeclassify :1900-1950 (any classification but ZoneMembership; Brief Z3:
#:                      an investigation reclassify removes the old Project kind)
READ_OPERATION = "READ"
DELETE_MEMBER_OPERATION = "DELETE_MEMBER"
CREATE_OPERATION = "CREATE"
UPDATE_OPERATION = "UPDATE_PROPERTIES"
ATTACH_OPERATION = "ATTACH"
DETACH_OPERATION = "DETACH"
DELETE_OPERATION = "DELETE"
CLASSIFY_OPERATION = "CLASSIFY"
PUBLISH_OPERATION = "PUBLISH"
DECLASSIFY_OPERATION = "DECLASSIFY"
DEFAULT_OPERATION = "DEFAULT"
ADD_FEEDBACK_OPERATION = "ADD_FEEDBACK"

#: The words a refusal uses for each operation.
OPERATION_WORDS = {
    CREATE_OPERATION: "create",
    UPDATE_OPERATION: "update",
    ATTACH_OPERATION: "attach",
    DETACH_OPERATION: "detach",
    DELETE_OPERATION: "delete",
    DELETE_MEMBER_OPERATION: "delete (anchored member)",
    READ_OPERATION: "read",
    CLASSIFY_OPERATION: "classify",
    PUBLISH_OPERATION: "publish (zone change)",
    DECLASSIFY_OPERATION: "declassify",
    # Brief T: a tag link / unlink is feedback (Brief F: so is a Rating) (validateUserForElementAddFeedback :1696-1730 and
    # validateUserForElementDeleteFeedback :1748-1770); a membership removal is a DETACH.
    "ADD_FEEDBACK": "add feedback (a tag or a rating)",
    "DELETE_FEEDBACK": "untag",
    "DETACH": "detach",
}

#: The access connector's default dynamic group names (OpenMetadataSecurityConfigurationProperty.java).
ALL_USERS_GROUP = "allUsers"
INSTANCE_OWNER_GROUP = "instanceOwner"
EXISTING_MAINTAINER_GROUP = "existingMaintainer"
NEW_MAINTAINER_GROUP = "newMaintainer"
ACCOUNT_TYPE_GROUPS = {
    "EMPLOYEE": "employeeUsers",
    "CONTRACTOR": "contractUsers",
    "EXTERNAL": "externalUsers",
    "DIGITAL": "digitalUsers",
}

#: Seconds an access read may take before RE stops waiting and denies ("could not check").
ACCESS_READ_TIMEOUT_SECONDS = 20


class AccessUnreadable(RuntimeError):
    """A zone, control, account or element could not be read: never the same as "no restriction"."""


def security_list_for(control: Optional[dict], operation: str = UPDATE_OPERATION) -> Optional[list[str]]:
    """The security list a control gives `operation`, or None when the zone is not secured for it.

    Mirrors `getAssociatedSecurityListForZone` (:911-958): the operation's own key first, then DEFAULT.
    """
    if not isinstance(control, dict):
        return None
    lists = control.get("associatedSecurityList")
    if not isinstance(lists, dict):
        return None
    for key in (operation, DEFAULT_OPERATION):
        value = lists.get(key)
        if value is not None:
            return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    return None


def instance_groups(user_id: str, owners: Optional[list[str]], maintainers: Optional[list[str]]) -> list[str]:
    """`getInstanceBasedGroups` (:1218-1248). `owners` None means the element has no `Ownership` userIds,
    which `isUserAnOwner` (:1260-1286) reads as "the user is an owner"."""
    groups = [user_id]
    if owners is None or user_id in owners:
        groups.append(INSTANCE_OWNER_GROUP)
    if maintainers is not None:
        groups.append(EXISTING_MAINTAINER_GROUP if user_id in maintainers else NEW_MAINTAINER_GROUP)
    return groups


@dataclass
class ZoneVerdict:
    """What `zone_grants` found. `secured` names the zones that restrict the refused operation, `operation` is
    that operation, and `why` says what refused when it was not a zone (an unknown or disabled account)."""

    allowed: bool
    secured: list[str] = field(default_factory=list)
    granted_by: str = ""
    operation: str = ""
    why: str = ""
    #: The `secured` zones named after a PERSON who owns or maintains the element (a private element is zoned
    #: `[private zone, <owner's userId>]`). A refusal sentence must not name them: it would tell the caller whose
    #: element it is. Detail for the server log only.
    person_zones: list[str] = field(default_factory=list)


def _one_operation(user_id: str, zones: list[str], operation: str, account: dict,
                   control_for: Callable[[str], Optional[dict]],
                   owners: Optional[list[str]], maintainers: Optional[list[str]]) -> ZoneVerdict:
    """The zone loop of `validateZoneAccess` (:1117-1198) for one operation; the account is already read."""
    # :1122 — a zone named after the user grants outright. Checked across every zone before any read: any
    # grant returns true in Egeria's loop, so the answer is the same and no control is read for nothing.
    if user_id in zones:
        return ZoneVerdict(True, granted_by=f"zone {user_id}", operation=operation)
    secured: list[tuple[str, list[str]]] = []
    for zone in zones:
        sl = security_list_for(control_for(zone), operation)
        if sl is None:
            continue                      # :1130 not a secured zone: ignored, not "everyone"
        secured.append((zone, sl))
    if not secured:
        return ZoneVerdict(True, operation=operation)               # :1205 no secured zone: access
    names = [z for z, _ in secured]
    type_group = ACCOUNT_TYPE_GROUPS.get(str(account.get("userAccountType") or "").upper())
    groups = [str(g) for g in (account.get("securityGroups") or [])]
    roles = [str(r) for r in (account.get("securityRoles") or [])]
    own = instance_groups(user_id, owners, maintainers)
    for zone, sl in secured:
        if ALL_USERS_GROUP in sl:                                   # :1141
            return ZoneVerdict(True, names, f"zone {zone} · {ALL_USERS_GROUP}", operation)
        if type_group and type_group in sl:                         # :1149-1155
            return ZoneVerdict(True, names, f"zone {zone} · {type_group}", operation)
        hit = next((g for g in [*groups, *roles, *own] if g in sl), None)   # :1157-1189
        if hit:
            return ZoneVerdict(True, names, f"zone {zone} · {hit}", operation)
    return ZoneVerdict(False, names, "", operation)                 # :1195 secured, nothing granted


def zone_grants(user_id: str, zones: Iterable[str], *,
                control_for: Callable[[str], Optional[dict]],
                account_for: Callable[[], Optional[dict]],
                owners: Optional[list[str]] = None,
                maintainers: Optional[list[str]] = None,
                operations: Iterable[str] = (UPDATE_OPERATION,),
                creating: bool = False) -> ZoneVerdict:
    """`validateZoneAccess` (:1090-1206) for `user_id` on an element in `zones`, for EVERY operation in
    `operations` (all must be granted: a write that performs several is checked for each).

    `control_for(zone)` returns the zone's security access control (None: none set up) and RAISES
    `AccessUnreadable` when it cannot tell; `account_for()` likewise returns the caller's user account.
    `creating`: the element does not exist yet, and `validateUserForElementCreate` passes the creator as its
    only maintainer (:1324), so the maintainer group is `existingMaintainer`.

    With no zone at all nothing is read (the owner's "not in use" rule). With any zone the ACCOUNT IS READ
    FIRST, as `validateZoneAccess` does (:1103, `getActiveUserAccount` :244-258): an unknown or disabled
    account is refused whatever the zones would grant. A session minted before the account was disabled, or
    by another app sharing the JWT secret, must not outlive the account.
    """
    zones = [z for z in zones if z]
    ops = list(dict.fromkeys(operations)) or [UPDATE_OPERATION]
    if not zones:
        return ZoneVerdict(True, operation=ops[0])
    account = account_for()
    if not isinstance(account, dict):
        return ZoneVerdict(False, [], "", ops[0], f"Egeria has no account for {user_id}")
    status = str(account.get("userAccountStatus") or "").upper()
    if status != "AVAILABLE":
        return ZoneVerdict(False, [], "", ops[0],
                           f"the Egeria account of {user_id} is {status.lower() or 'not marked available'}")
    if creating:
        maintainers = [user_id]
    last = ZoneVerdict(True, operation=ops[0])
    for op in ops:
        last = _one_operation(user_id, zones, op, account, control_for, owners, maintainers)
        if not last.allowed:
            people = {str(p) for p in [*(owners or []), *(maintainers or [])] if p and p != user_id}
            last.person_zones = [z for z in last.secured if z in people]
            return last
    return last


#: Brief Z2 round 2: what Egeria checks on the ANCHOR when the element written is an anchored member (its
#: `Anchors.anchorGUID` names another element), by the operation on the member, and whether the member is then
#: also checked for READ. OpenMetadataAccessSecurityConnector.java at egeria af400039c2:
#:   CREATE            validateUserForAnchorMemberCreate :1965-1990  CREATE on the anchor, maintainer = the user
#:   UPDATE_PROPERTIES validateUserForAnchorMemberUpdate :2010-2035  UPDATE_PROPERTIES on the anchor, maintainer = the user
#:   ATTACH            validateUserForAnchorAttach :2100-2145         ATTACH on the anchor (maintainer = the user), READ on the member
#:   DETACH            validateUserForAnchorDetach :2162-2207         DETACH on the anchor (maintainer = the user), READ on the member
#:   CLASSIFY          validateUserForAnchorClassify :2346-2400       CLASSIFY on the anchor, the anchor's own maintainers
#:   PUBLISH           validateUserForAnchorClassify :2346-2370       PUBLISH on the anchor (a zone change), the anchor's maintainers
#:   DELETE            validateUserForAnchorMemberDelete :2481-2520   DELETE_MEMBER on the anchor (maintainer = the user), READ on the member
#:   DECLASSIFY        validateUserForAnchorDeclassify :2416-2466     DECLASSIFY on the anchor, the anchor's maintainers (Brief Z3)
#:   ADD_FEEDBACK      validateUserForAnchorAddFeedback :2224-2250    ADD_FEEDBACK on the anchor (maintainer = the user), READ on
#:                     the feedback element once it exists (Brief F: a Rating, a tag link; a NEW one has nothing to read)
#: (operation on the member) -> (operation on the anchor, the user is the maintainer, READ on the member too)
ANCHOR_OPERATIONS = {
    CREATE_OPERATION: (CREATE_OPERATION, True, False),
    UPDATE_OPERATION: (UPDATE_OPERATION, True, False),
    ATTACH_OPERATION: (ATTACH_OPERATION, True, True),
    DETACH_OPERATION: (DETACH_OPERATION, True, True),
    CLASSIFY_OPERATION: (CLASSIFY_OPERATION, False, False),
    PUBLISH_OPERATION: (PUBLISH_OPERATION, False, False),
    DELETE_OPERATION: (DELETE_MEMBER_OPERATION, True, True),
    DECLASSIFY_OPERATION: (DECLASSIFY_OPERATION, False, False),
    ADD_FEEDBACK_OPERATION: (ADD_FEEDBACK_OPERATION, True, False),
}


class ElementFacts(NamedTuple):
    """What a zone decision needs from one element: (zones, owners, maintainers, anchor). `anchor` is the
    `Anchors.anchorGUID` when it names ANOTHER element ('' when the element is its own anchor or has none)."""

    zones: list
    owners: Optional[list]
    maintainers: Optional[list]
    anchor: str = ""


# ── reading the element, the controls and the account from Egeria ─────────────────────────────────────

def _string_array(classification: dict, name: str) -> Optional[list[str]]:
    """A string-array property of a raw classification as Egeria's `getStringArrayProperty` reads it
    (OMRSRepositoryPropertiesUtilities:277-323): None when the property is absent or null, or an array with no
    values; else its strings. Present but in a shape RE cannot read RAISES `AccessUnreadable` (review round 1:
    `userIds` of `{}` or `"{0=bob}"` read as None, which `instance_groups` turns into instanceOwner)."""
    cp = classification.get("classificationProperties") or classification.get("properties") or {}
    if not isinstance(cp, dict):
        raise AccessUnreadable(f"a classification with unreadable properties: its {name} are unknown")
    pvm = cp.get("propertyValueMap")
    if isinstance(pvm, dict) and pvm.get(name) is not None:
        value = pvm[name]
        if isinstance(value, dict):
            arr_values = value.get("arrayValues")
            arr = arr_values.get("propertiesAsStrings") if isinstance(arr_values, dict) else None
            if isinstance(arr, dict):
                out = [str(arr[k]) for k in sorted(arr, key=lambda x: int(x) if str(x).isdigit() else 0)]
                return out or None
            if value.get("arrayCount") == 0 and arr_values is None:
                return None
        raise AccessUnreadable(f"a {name} RE cannot read: unknown")
    value = cp.get(name)
    if isinstance(value, list):
        return [str(v) for v in value] or None
    if value is not None:
        raise AccessUnreadable(f"a {name} RE cannot read: unknown")
    strings = cp.get("propertiesAsStrings")
    if isinstance(strings, dict) and strings.get(name) is not None:
        raise AccessUnreadable(f"a {name} RE cannot read (only as a string): unknown")
    return None


def _classifications(element: dict, name: str) -> list[dict]:
    """The element's `name` classifications: Egeria 6.2's dedicated header field first (`elementHeader.anchor`,
    `.ownership`), else the `classifications` list (MetadataExpert's shape). A header field RE cannot read raises
    `AccessUnreadable`, never "absent"."""
    from resource_explorer.catalogue_gateway import UnreadableClassification, classifications_named

    try:
        return classifications_named(element, name)
    except UnreadableClassification as exc:
        raise AccessUnreadable(str(exc)) from exc


_ANCHORS_BEAN_KEYS = {"class", "typeName", "anchorGUID", "anchorTypeName", "anchorDomainName", "anchorScopeGUIDs",
                      "zoneMembership", "effectiveFrom", "effectiveTo", "extendedProperties"}


def _anchor_of(element: dict) -> str:
    """The `Anchors` classification's anchorGUID ('' with no Anchors). Raises `AccessUnreadable` for an Anchors
    whose anchor RE cannot read: an unknown anchor is never "its own anchor"."""
    for c in _classifications(element, "Anchors"):
        cp = c.get("classificationProperties") or c.get("properties") or {}
        if not isinstance(cp, dict):
            raise AccessUnreadable("an Anchors classification with unreadable properties")
        value = cp.get("anchorGUID")
        if isinstance(value, str):
            return value
        pv = (cp.get("propertyValueMap") or {}).get("anchorGUID")
        if isinstance(pv, dict) and isinstance(pv.get("primitiveValue"), str):
            return pv["primitiveValue"]
        strings = cp.get("propertiesAsStrings")
        if isinstance(strings, dict) and isinstance(strings.get("anchorGUID"), str):
            return strings["anchorGUID"]
        if value is not None or pv is not None or (isinstance(strings, dict) and "anchorGUID" in strings):
            raise AccessUnreadable("an Anchors classification whose anchorGUID RE cannot read")
        # No anchorGUID at all, in a shape RE recognises: Egeria counts the element as its own anchor
        # (OpenMetadataAPIAnchorHandler.getAnchorsFromAnchorsClassification: "If the anchorGUID is not filled out
        # then the entity is its own anchor"; null properties = unanchored), so its own zones decide.
        # Seen live on egeria_git's Asset: Anchors {anchorTypeName, anchorDomainName}, no anchorGUID.
        # 6.2's flattened bean (`elementHeader.anchor`): `class` AnchorsProperties and only that bean's keys
        # (AnchorsProperties.java:25-29 + ClassificationBeanProperties/ClassificationProperties), so a mis-cased
        # `anchorGuid` is never read as "no anchorGUID".
        known = (not cp or isinstance(cp.get("propertyValueMap"), dict) or isinstance(strings, dict)
                 or (cp.get("class") == "AnchorsProperties" and set(cp) <= _ANCHORS_BEAN_KEYS)
                 or set(cp) <= {"anchorGUID", "anchorTypeName", "anchorDomainName", "anchorScopeGUID", "class"})
        if not known:
            raise AccessUnreadable("an Anchors classification in a shape RE cannot read")
        return ""
    return ""


def element_facts(element: Any, requested_guid: str = "") -> ElementFacts:
    """(zones, owners, maintainers, anchor) of a raw metadata element. Raises `AccessUnreadable` when the
    answer is not an element (so "unreadable" is never read as "no zones").

    owners: the `Ownership` classification's `userIds` (what `isUserAnOwner` reads), None when absent.
    maintainers: `versions.maintainedBy` when Egeria returned it, else None (no maintainer groups).
    """
    from resource_explorer.catalogue_gateway import GatewayError, zones_of_element

    try:
        zones = zones_of_element(element)
    except GatewayError as exc:
        raise AccessUnreadable(str(exc)) from exc
    owners: Optional[list[str]] = None
    for c in _classifications(element, "Ownership"):
        owners = _string_array(c, "userIds")
        break
    maintainers = None
    for holder in (element, element.get("elementHeader") or {}):
        versions = holder.get("versions") if isinstance(holder, dict) else None
        if isinstance(versions, dict) and isinstance(versions.get("maintainedBy"), list):
            maintainers = [str(m) for m in versions["maintainedBy"]]
            break
    anchor = _anchor_of(element)
    if anchor:
        # Round 3: "its own anchor" is decided against the GUID that was ASKED for, never one the answer carries
        # (an answer naming another element must not make a member its own anchor). With nothing to compare
        # against, the anchor cannot be judged: unreadable, which denies.
        if not requested_guid:
            raise AccessUnreadable("an Anchors classification on an element read without its GUID")
        if anchor == requested_guid:
            anchor = ""                     # its own anchor: its own zones decide
    return ElementFacts(zones, owners, maintainers, anchor)


def _metadata_expert():
    """The read client for an element, as RE's daemon (an access check reads what the caller may not)."""
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    return egeria_client(Daemon(DaemonReason.ACCESS_CHECK), purpose="curation access: element",
                         shared=False).of(MetadataExpert)


def _metadata_expert_as(identity):
    """The read client for an element, as `identity` (a signed-in Caller), never the daemon.

    For a PRIVATE investigation's elements (zoned `[private zone, owner]`): Egeria refuses RE's service account
    those by design (OPEN-METADATA-SECURITY-403-020), so an access check that read them as the daemon could only
    ever answer "could not check" (found live 2026-10-13, the owner's privacy proof). The identity is handed in
    explicitly by the thread that holds it, never read from a ContextVar here: these reads run on pool threads."""
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.egeria_clients import egeria_client

    return egeria_client(identity, purpose="curation access: element (as its owner)",
                         shared=False).of(MetadataExpert)


def _security_officer():
    """The Security Officer client that reads controls and accounts (the same store the connector reads)."""
    from pyegeria.omvs.security_officer import SecurityOfficer

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    return egeria_client(Daemon(DaemonReason.ACCESS_CHECK), purpose="curation access: controls",
                         shared=False).of(SecurityOfficer)


def _platform() -> tuple[str, Optional[str]]:
    from resource_explorer.egeria_identity import _resolve_platform

    return _resolve_platform()


def _close(client: Any) -> None:
    close = getattr(client, "close_session", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - tidy-up, never the answer
            pass


def _bounded(what: str, fn: Callable[[], Any]) -> Any:
    """Run one Egeria read on the shared pool (a client built in the thread that drives it), bounded."""
    from concurrent.futures import TimeoutError as _Timeout

    from resource_explorer.concurrency import run_sync

    try:
        return run_sync(fn, timeout=ACCESS_READ_TIMEOUT_SECONDS)
    except AccessUnreadable:
        raise
    except _Timeout as exc:
        raise AccessUnreadable(f"{what}: no answer within {ACCESS_READ_TIMEOUT_SECONDS}s") from exc
    except Exception as exc:  # noqa: BLE001 - every failure is the one named error, never "no restriction"
        raise AccessUnreadable(f"{what}: {type(exc).__name__}: {exc}"[:300]) from exc


def _catch_wrapped(client: Any, *names: str) -> bool:
    """Whether pyegeria's `dynamic_catch` installed loguru's `logger.catch` on any of these methods of THIS
    client's class. `dynamic_catch` decides at import time (`enable_logger_catch`); the wrapper, when there,
    carries `__wrapped__` (functools.wraps). So this asks the function itself, not today's setting."""
    cls = type(client)
    for name in names:
        fn = getattr(cls, name, None)
        if fn is not None and getattr(fn, "__wrapped__", None) is not None:
            return True
    return False


#: How many element reads one batch runs at once (each on its own client, built and closed in its thread).
ACCESS_READ_CONCURRENCY = 4


def _read_elements_on_one_client(guids: list[str], read_as=None) -> dict[str, Any]:
    """Read `guids` one after another on ONE client built, driven and closed in this thread (pyegeria ISSUE-96
    binds a client to the thread that first drives it). {guid: (zones, owners, maintainers) | AccessUnreadable}:
    a failed read is that element's answer, never the batch's, and never "no zones". `read_as`: the identity
    to read as (`_metadata_expert_as`); None reads as RE's daemon (`_metadata_expert`)."""
    out: dict[str, Any] = {}
    client = _metadata_expert() if read_as is None else _metadata_expert_as(read_as)
    try:
        for guid in guids:
            try:
                out[guid] = element_facts(client.get_metadata_element_by_guid(guid), guid)
            except AccessUnreadable as exc:
                out[guid] = AccessUnreadable(f"the element {guid[:8]}: {exc}"[:300])
            except Exception as exc:  # noqa: BLE001 - every failure is the one named error
                out[guid] = AccessUnreadable(f"the element {guid[:8]}: {type(exc).__name__}: {exc}"[:300])
    finally:
        _close(client)
    return out


def read_elements(guids: Iterable[str], read_as=None) -> dict[str, Any]:
    """Read several elements' (zones, owners, maintainers) at once: up to `ACCESS_READ_CONCURRENCY` clients on
    the shared pool, each reading its share in turn, so a blueprint's members cost one client per lane rather
    than one per element (pyegeria's constructor handshake + a token mint + the read was three Egeria calls per
    element). The reads run as RE's daemon (`Daemon(ACCESS_CHECK)`, named in `_metadata_expert`), so no caller
    ContextVar has to reach the pool threads. Bounded: a lane with no answer within
    `ACCESS_READ_TIMEOUT_SECONDS` per element it holds makes each of its elements unreadable. From a pool
    thread (the run) the lanes run inline, one after another (concurrency's re-entrancy rule). `read_as` (a
    private investigation's owner, signed in) is captured here and handed to each lane, never re-read there."""
    from concurrent.futures import TimeoutError as _Timeout

    from resource_explorer.concurrency import in_pool_thread, run_sync_all

    todo = [g for g in dict.fromkeys(guids) if g]
    if not todo:
        return {}
    lanes = 1 if in_pool_thread() else min(ACCESS_READ_CONCURRENCY, len(todo))
    shares = [todo[i::lanes] for i in range(lanes)]
    longest = max(len(s) for s in shares)
    results = run_sync_all([lambda s=s: _read_elements_on_one_client(s, read_as) for s in shares],
                           timeout=ACCESS_READ_TIMEOUT_SECONDS * longest)
    out: dict[str, Any] = {}
    for share, (ok, value) in zip(shares, results):
        if ok and isinstance(value, dict):
            out.update(value)
            continue
        if isinstance(value, _Timeout):
            why = f"no answer within {ACCESS_READ_TIMEOUT_SECONDS}s"
        else:
            why = f"{type(value).__name__}: {value}"
        for guid in share:
            out[guid] = AccessUnreadable(f"the element {guid[:8]}: {why}"[:300])
    for guid in todo:                      # a lane that answered without an element it was given
        out.setdefault(guid, AccessUnreadable(f"the element {guid[:8]}: no answer"))
    return out


class EgeriaAccessReader:
    """The three Egeria reads a zone decision needs, for one batch of decisions.

    Controls and accounts are cached for the reader's life (one request, or one run): they do not change
    because RE writes. Elements are the part that can: a run promotes elements as it goes, so an element's
    zones can change between two checks of one run. Hence:

    * `cache_elements=True` (a request that decides and then writes nothing to Egeria before it returns: the
      Publish press, the branch-verdict route) keeps every element read for the request. Never across
      requests or callers: the reader is built per request and dropped with it.
    * `cache_elements=False` (the default; the run's re-check right before each write) reads every element
      fresh for each check. `check(guids)` reads the elements one check needs together, once, and forgets
      them when the check ends, so the next check reads again.
    """

    def __init__(self, *, cache_elements: bool = False, read_as=None) -> None:
        #: Who the ELEMENT reads are made as: None = RE's daemon (`Daemon(ACCESS_CHECK)`); a Caller identity for a
        #: private investigation's checks made by its signed-in owner (`egeria_work_access.investigation_reader`).
        #: Controls and accounts are always read as the daemon (they are not the private elements).
        self.read_as = read_as
        self._controls: dict[str, Optional[dict]] = {}
        self._accounts: dict[str, Optional[dict]] = {}
        self._platform: Optional[tuple[str, Optional[str]]] = None
        self._cache_elements = cache_elements
        self._elements: dict[str, Any] = {}
        self._checks = 0

    def prefetch(self, guids: Iterable[str]) -> None:
        """Read the elements not held yet, together (`read_elements`). Kept until the check ends, or for the
        reader's life with `cache_elements`."""
        todo = [g for g in dict.fromkeys(guids) if g and g not in self._elements]
        if todo:
            self._elements.update(read_elements(todo, self.read_as))
            # Brief Z2 round 2: an anchored member is judged on its anchor, so the anchors are read too, together.
            anchors = [f.anchor for g in todo if isinstance(f := self._elements.get(g), ElementFacts) and f.anchor
                       and f.anchor not in self._elements]
            if anchors:
                self._elements.update(read_elements(anchors, self.read_as))

    @contextmanager
    def check(self, guids: Iterable[str] = ()):
        """One access check: the elements it will need are read together up front, and (unless the reader
        caches elements) forgotten when it ends."""
        self._checks += 1
        try:
            self.prefetch(guids)
            yield self
        finally:
            self._checks -= 1
            if not self._cache_elements and not self._checks:
                self._elements.clear()

    def element(self, guid: str) -> ElementFacts:
        got = self._elements.get(guid)
        if got is None:
            got = read_elements([guid], self.read_as).get(guid) or AccessUnreadable(f"the element {guid[:8]}: no answer")
            if self._cache_elements or self._checks:
                self._elements[guid] = got
        if isinstance(got, BaseException):
            raise AccessUnreadable(str(got))        # a fresh raise each time; the held answer is unchanged
        return got

    def _resolved_platform(self) -> tuple[str, Optional[str]]:
        if self._platform is None:
            self._platform = _bounded("the platform", _platform)
        return self._platform

    def control(self, zone: str) -> Optional[dict]:
        if zone not in self._controls:
            name, guid = self._resolved_platform()
            kwargs = {"platform_guid": guid} if guid else {}

            def read():
                client = _security_officer()
                try:
                    got = client.get_security_access_control(name, zone, **kwargs)
                    # pyegeria's catch wrapper returns None for a FAILED read: "no control" (= open) and a
                    # failure look the same. With the wrapper installed, None is unreadable, never open.
                    if got is None and _catch_wrapped(client, "get_security_access_control",
                                                      "_async_get_security_access_control"):
                        raise AccessUnreadable(
                            f"the access control for zone {zone}: pyegeria's error catch is installed, so "
                            "'no control' cannot be told from a failed read")
                    return got
                finally:
                    _close(client)
            got = _bounded(f"the access control for zone {zone}", read)
            self._controls[zone] = got if isinstance(got, dict) else None
        return self._controls[zone]

    def account(self, user_id: str) -> Optional[dict]:
        if user_id not in self._accounts:
            name, guid = self._resolved_platform()
            kwargs = {"platform_guid": guid} if guid else {}

            def read():
                client = _security_officer()
                try:
                    return client.get_user_account(name, user_id, **kwargs)
                finally:
                    _close(client)
            got = _bounded(f"the account of {user_id}", read)
            self._accounts[user_id] = got if isinstance(got, dict) else None
        return self._accounts[user_id]
