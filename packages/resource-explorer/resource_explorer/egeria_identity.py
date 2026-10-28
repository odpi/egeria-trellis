"""Who an Egeria write runs as, and what governance metadata it carries.

`docs/runtime-architecture-plan.md` §4 in one module:

* **Per-request client.** Every pyegeria client RE builds *on behalf of a
  person* is authenticated with that person's Egeria bearer token, so Egeria's
  own provenance records the person rather than `erinoverview`. Since Brief I
  (2026-10-09) every client is built by `egeria_clients.egeria_client`, the one
  factory; this module keeps the identity record and the governance stamps.
* **Ownership.** Everything RE publishes gets the `Ownership` classification
  (`0445`) naming the requesting person: their profile (owner = its
  qualifiedName, ownerTypeName = its type) when Egeria links one to their
  UserIdentity, else `owner` = their userId and `ownerTypeName = "UserIdentity"`
  (7h, `egeria_actors.ownership_for_person`). Ownership is attribution; who may curate
  follows Egeria's zones (`workflows/curate.curation_access`, Brief Z).
* **Draft zone: configured-only.** DRAFT is a content status, not a zone (owner ruling
  2026-10-08, retiring the 2026-09-04 one-zone-per-app default). Published assets get NO
  RE-imposed zone; a draft zone (`EXPLORER_DRAFT_ZONE`, conventionally
  `resource-explorer-draft`) exists only if a deployment configures one, and then
  curate-accept moves the element into the configured publish zones. Private-investigation
  work is zoned `[resource-explorer-private, <owner>]`.

The daemon identity is legitimate only where it is named
--------------------------------------------------------
The **worker role's own loops** — bootstrap heal, Egeria resync, the outbox
drain, scheduled and queued surveys — are RE's daemon identity and *should* be
attributed to it: `egeria_clients.Daemon(reason)`, with `reason` from a closed
enum, so a reader can tell a deliberate daemon call from one that merely forgot
a token. Nothing falls back to it silently.

**Deliberate, and Egeria-consistent: a queued run does not carry a token**
(owner, 2026-10-09). Any queued work — a survey, or a person's own Publish or
Curate commit — is committed by RE's daemon, as Egeria's engine hosts commit
theirs, with the person recorded as `requested_by` and stamped as Ownership. An Egeria
bearer token lives one hour (measured — `trellis_auth.
EGERIA_TOKEN_TTL_SECONDS_OBSERVED`) and dies whenever the platform restarts,
while a queued survey may sit in the queue longer than that and then run for
sixteen minutes. Storing the token with the row would mean either a credential
at rest in `runs.target` (rejected outright) or an encrypted column and a key
to manage (out of scope for this pass). So the row carries `requested_by` and
nothing else, and the worker publishes **as the service account with
`Ownership` set to `requested_by`**. Egeria's provenance for such a publish
therefore says "the worker did it"; the `Ownership` classification says whose
it is, and that is the attribution the curate authorization actually reads.
Closing the gap needs an encrypted-at-rest credential store or a delegation
token from Egeria, and is named in the plan as follow-on work.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

log = logging.getLogger(__name__)

__all__ = [
    "DRAFT_ZONE",
    "EgeriaIdentity",
    "caller_credentials",
    "caller_user_id",
    "classification_client",
    "current_identity",
    "current_zones",
    "draft_zone",
    "draft_zones",
    "ensure_draft_zone_exists",
    "ensure_private_zone_exists",
    "private_zone",
    "private_zone_is_enforced",
    "private_zone_status",
    "private_zones",
    "assess_zone_control",
    "check_zone_controls",
    "stamped_zones",
    "zone_controls_line",
    "zone_controls_report",
    "zone_lockout_refusal",
    "ownership_body",
    "person_ownership_body",
    "configured_publish_zones",
    "clear_zone_membership",
    "read_zones",
    "set_ownership",
    "set_zone_membership",
    "stamp_published",
    "use_identity",
    "zone_membership_body",
]


#: The name RE used to stamp by default (2026-09-04). RETIRED as a default on 2026-10-08 (project
#: owner: DRAFT is a content status, not a zone): kept only so a deployment that wants the old
#: behaviour can set `EXPLORER_DRAFT_ZONE=resource-explorer-draft`, and so tests can name it.
#: `draft_zone()` never returns it unless configured.
DRAFT_ZONE = "resource-explorer-draft"

#: RE's private zone — the one that actually DENIES.
#:
#: Personal and Experiment investigations zone their artifacts as
#: `[PRIVATE_ZONE, "<creator userId>"]`, and both halves are load-bearing for
#: opposite reasons (`OpenMetadataAccessSecurityConnector.validateZoneAccess`):
#:
#: * the **userId entry grants** — the connector returns true outright on
#:   `userId.equals(zoneName)`, so the owner gets in with no configuration;
#: * the **PRIVATE_ZONE entry denies** — for anyone else it is a zone the
#:   platform recognises as secured, which makes `securedZoneCount > 0` and
#:   sends them to `return false`.
#:
#: A userId on its own denies NOBODY: an unrecognised zone name is *ignored*
#: (not treated as restrictive), the count stays zero, and the method falls
#: through to `return true`. Verified live 2026-09-07: a non-owner was denied
#: with both entries present, and the owner still read the element.
PRIVATE_ZONE = "resource-explorer-private"

_OWNER_TYPE_NAME = "UserIdentity"

#: `Ownership.ownerPropertyName` — which property of the owning element holds
#: the value in `owner`. For a `UserIdentity` that is `userId`.
_OWNER_PROPERTY_NAME = "userId"


def draft_zone() -> Optional[str]:
    """The draft zone this deployment CONFIGURED (`EXPLORER_DRAFT_ZONE`), or `None`.

    **No default (project owner's ruling, 2026-10-08): DRAFT is a content status, not a zone.**
    RE-authored elements carry no zone unless someone configured one on purpose. The old default,
    `resource-explorer-draft`, named a zone nothing in the security directory defined, so every
    element RE created sat in a zone no rule named. Writers use `draft_zones()` (a list, `[]`
    when unconfigured) and send NO `ZoneMembership` at all when it is empty.
    """
    return (os.environ.get("EXPLORER_DRAFT_ZONE") or "").strip() or None


def draft_zones() -> list[str]:
    """`[draft_zone()]` when one is configured, else `[]` (the default: stamp nothing)."""
    zone = draft_zone()
    return [zone] if zone else []


def referenced_element_zones() -> list[str]:
    """The zones for an element a publish REFERENCES rather than produces. Never the private zone.

    Design §3.6 (`docs/investigation-classification-and-zoning-design.md`): "an investigation zones
    what it produced, never what it references". A repository, database or file-system Asset, and the
    deployment-wide GitHub `SourceControlLibrary`, are shared by every investigation that names them;
    zoning one private to one owner hides it from everyone else, and the next publish by anyone else
    cannot see it, clears its registration and 409s on the re-create. So a referenced element gets
    the deployment's ordinary treatment, whoever publishes and however private their investigation
    is: the configured draft zone, else none (`draft_zones()`). The ONE helper the creates and the
    referenced-element stamp share, so the two cannot drift apart again (2026-10-13 review).
    """
    return draft_zones()


def zone_membership_initial_classification(zones: Iterable[str]) -> dict:
    """The `initialClassifications` entry that CREATES an element already in `zones`.

    The shape Egeria's `NewElementRequestBody.initialClassifications` takes
    (`Map<String, ClassificationProperties>`, `ZoneMembershipProperties` a registered subtype), and
    the one pyegeria's `InitialClassifications` model serialises flat. A `zoneMembership` key inside
    an Asset's `properties` is NOT this: Egeria's `AssetProperties` has no such field and ignores
    unknown keys, so a zone sent there never reaches the element.
    """
    return {"ZoneMembership": {"class": "ZoneMembershipProperties", "zoneMembership": list(zones)}}


def private_zone() -> str:
    """RE's private zone name (`EXPLORER_PRIVATE_ZONE` overrides)."""
    return (os.environ.get("EXPLORER_PRIVATE_ZONE") or "").strip() or PRIVATE_ZONE


def private_zones(owner: str) -> list[str]:
    """The `ZoneMembership` for an element private to `owner`.

    Order is not significant to the connector — it loops the whole list — but
    the secured zone is first so a human reading the classification sees the
    restriction before the exception to it.
    """
    zones = [private_zone()]
    if owner:
        zones.append(owner)
    return zones


def configured_publish_zones() -> list[str]:
    """The publish zones this deployment CONFIGURED, or `[]` when it did not.

    **The configured-only rule (project owner, 2026-10-07): no zone unless someone configured one.**
    This is the ONLY source of a publish zone on any write path (catalogue commit, curate
    promotion, investigation loosening). There is no default: the old fallback zone and its function
    are removed, because the 2026-10-05 rehearsal
    found that writing it onto an element locks the service identity, Egeria's survey engine and
    the cataloguer out of it. A zone is written ONLY when someone set `EXPLORER_PUBLISH_ZONES`
    (or RE's `egeria.default_catalog_zones`) on purpose; with nothing configured the writers leave
    zones to Egeria, and promotion CLEARS RE's own draft zone instead of replacing it.
    """
    raw = os.environ.get("EXPLORER_PUBLISH_ZONES", "")
    zones = [z.strip() for z in raw.split(",") if z.strip()]
    if zones:
        return zones
    from resource_explorer.config import get_config

    return list(get_config().egeria.default_catalog_zones or [])


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EgeriaIdentity:
    """How one pyegeria client should authenticate, and as whom.

    Exactly one of `token` / `password` is meaningful:

    * `token` set — a signed-in person. The factory calls `set_bearer_token`,
      and Egeria's provenance names them.
    * `password` set, `is_service_account` True — the worker role's own
      identity, which is the right attribution for background loops.

    `user_id` is always the name to put in `Ownership.owner`, which is why it
    is set on both: an interim service-account publish still records *whose*
    artifact it is (see the module docstring).
    """

    user_id: str
    token: Optional[str] = None
    password: str = field(default="", repr=False)
    is_service_account: bool = False
    #: Set by `egeria_clients`: "caller" | "system" | "background". Empty on a bare identity,
    #: which `egeria_clients.egeria_client` refuses — who a client acts as is always named.
    kind: str = ""
    #: A Daemon identity's `DaemonReason` value.
    reason: str = ""
    #: A Daemon identity's requester (recorded; `user_id` carries it for Ownership).
    requested_by: str = ""
    #: The Egeria user a client is built for when it differs from `user_id` (Background: the
    #: service account mints the token while `user_id` names the requester for Ownership).
    client_user: str = ""

    @property
    def is_person(self) -> bool:
        return bool(self.token) and not self.is_service_account


def current_identity() -> Optional[EgeriaIdentity]:
    """The signed-in caller for this request/thread, or None.

    Reads `a2a_auth.current_caller` — RE's *one* identity ContextVar, set by
    the web `IdentityMiddleware`, by the A2A middleware and by the CLI's
    `use_identity()`. There is deliberately no second mechanism; see
    `resource_explorer/auth.py`.
    """
    from resource_explorer.a2a_auth import caller

    identity = caller()
    if identity is None or identity.auth_source == "anonymous":
        return None
    return EgeriaIdentity(user_id=identity.user_id, token=identity.egeria_token)


def caller_user_id(default: str = "") -> str:
    """The signed-in user's id, or `default` when there is no caller."""
    identity = current_identity()
    return identity.user_id if identity is not None else default


def caller_credentials() -> EgeriaIdentity:
    """Who an Egeria call here acts as: `egeria_clients.current_principal()`.

    The declared Daemon job, else the signed-in Caller; with neither it raises
    `NoCallerIdentity`. It no longer falls back to the service account (Brief I, 2026-10-09):
    a daemon job names itself with `egeria_clients.acting_as(Daemon(reason))`.
    """
    from resource_explorer.egeria_clients import current_principal

    return current_principal()


@contextmanager
def use_identity(identity: Optional[EgeriaIdentity]) -> Iterator[None]:
    """Run a block as `identity` — the CLI's and the worker's way in.

    Sets the same ContextVar the web and A2A middlewares set, so a CLI command
    and an HTTP request reach identical code with identical results. A None
    identity clears the caller rather than leaving a previous one in place;
    inheriting somebody else's identity is the failure mode this guards.
    """
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    caller_obj = None
    if identity is not None and identity.is_person:
        caller_obj = CallerIdentity(
            user_id=identity.user_id,
            egeria_token=identity.token,
            auth_source="app-jwt",
        )
    reset = current_caller.set(caller_obj)
    try:
        yield
    finally:
        current_caller.reset(reset)


# ---------------------------------------------------------------------------
# Ownership and zones
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OnBehalf:
    """Whose an element is, for ONE write made on a person's behalf (Brief I, rounds 3-4).

    `requester` — who asked: a queued run's `requested_by`, or the signed-in person.
    `owner` — what Ownership is stamped with: the requester (owner's ruling, round 4: no declared-
    owner or investigation branch here — a free-text or team owner gets no UserIdentity). Empty
    when nobody asked, and NEVER RE's own daemon user: the service account is not an owner.
    """

    requester: str
    owner: str

    def provenance(self) -> dict:
        """`additionalProperties.requestedBy`, beside the element's other provenance keys. Empty
        when nobody asked (the daemon's own work)."""
        return {"requestedBy": self.requester} if self.requester else {}


def on_behalf_of(identity: Optional[EgeriaIdentity] = None) -> OnBehalf:
    """THE helper every write made on a person's behalf uses: who asked, and what Ownership names.
    A daemon with no requester (the drain loop's own retry of an unattributed row) gets nothing:
    no owner, no requestedBy — never the service account."""
    identity = identity or caller_credentials()
    if identity.kind == "daemon":
        requester = (identity.requested_by or "").strip()
        daemon_user = identity.client_user or ""
    elif identity.kind == "caller" or identity.is_person:   # a signed-in person (token held)
        requester, daemon_user = (identity.user_id or "").strip(), ""
    else:
        requester, daemon_user = "", ""
    if requester and daemon_user and requester == daemon_user:
        requester = ""                     # the daemon's own user is never recorded as the person
    return OnBehalf(requester=requester, owner=requester)


def _read_additional_properties(element: Any) -> dict:
    """An element's current `additionalProperties` as {key: string}, from a raw generic read
    (`elementProperties.propertyValueMap.additionalProperties.mapValues...`) or a formatted one.
    Raises ValueError when the answer is not an element at all."""
    if not isinstance(element, dict):
        raise ValueError(f"the read returned {type(element).__name__}, not an element")
    if not isinstance(element.get("elementProperties") or element.get("properties"), dict):
        # Not a shape we know: reading it as "no additionalProperties" and writing ours would wipe
        # whatever the element carries. Refuse instead (nothing is written; the caller says why).
        raise ValueError(f"unrecognised element shape (keys: {sorted(element)[:6]})")
    vm = ((element.get("elementProperties") or element.get("properties") or {}).get("propertyValueMap")
          if isinstance(element.get("elementProperties") or element.get("properties"), dict) else None) or {}
    mp = vm.get("additionalProperties") if isinstance(vm, dict) else None
    if isinstance(mp, dict):
        inner = ((mp.get("mapValues") or {}).get("propertyValueMap") or {})
        return {k: str((v or {}).get("primitiveValue", "")) for k, v in inner.items() if isinstance(v, dict)}
    props = element.get("properties") or element.get("elementProperties") or {}
    flat = props.get("additionalProperties") if isinstance(props, dict) else None
    return {k: str(v) for k, v in flat.items()} if isinstance(flat, dict) else {}


def _map_property(values: dict) -> dict:
    return {"class": "MapTypePropertyValue", "typeName": "map<string,string>",
            "mapValues": {"class": "ElementProperties", "propertyValueMap": {
                k: {"class": "PrimitiveTypePropertyValue", "typeName": "string",
                    "primitiveTypeCategory": "OM_PRIMITIVE_TYPE_STRING", "primitiveValue": v}
                for k, v in values.items()}}}


def record_requested_by(element_guid: str, behalf: OnBehalf, *, client: Any) -> str:
    """For an element whose create body cannot carry `additionalProperties` (a template copy, a
    generic metadata-element create): READ its current additionalProperties, merge `requestedBy`
    in, write the merged map. Returns '' when recorded (or nothing to record), else the reason.
    A failed read writes nothing (an update would replace the map with ours alone). Never raises.
    UNVERIFIED LIVE: the map-typed property shape on `update_metadata_element_properties`."""
    if not behalf.requester:
        return ""
    if not element_guid:
        return "no element GUID"
    try:
        current = _read_additional_properties(client.get_metadata_element_by_guid(element_guid))
    except Exception as exc:  # noqa: BLE001 - reported; nothing is written on a failed read
        return f"its additionalProperties could not be read ({type(exc).__name__}: {str(exc)[:160]})"
    try:
        client.update_metadata_element_properties(element_guid, {
            "class": "UpdatePropertiesRequestBody",
            "properties": {"class": "ElementProperties", "propertyValueMap": {
                "additionalProperties": _map_property({**current, **behalf.provenance()})}},
            "replaceProperties": False,
        })
        return ""
    except Exception as exc:  # noqa: BLE001 - reported, the element already exists
        log.warning("egeria: could not record requestedBy=%s on %s — %s: %s",
                    behalf.requester, element_guid, type(exc).__name__, exc)
        return f"{type(exc).__name__}: {str(exc)[:200]}"


def stamp_on_behalf(element_guid: str, behalf: OnBehalf, *, identity: Optional[EgeriaIdentity] = None,
                    props_client: Any = None) -> str:
    """Ownership = the requester, and (with `props_client`) `requestedBy` merged into the
    element's additionalProperties. '' when everything landed (or there was nobody to record),
    else "requester not recorded (<reason>)", which the caller reports as partial."""
    if not behalf.requester:
        return ""
    reasons = []
    if props_client is not None:
        why = record_requested_by(element_guid, behalf, client=props_client)
        if why:
            reasons.append(f"requestedBy: {why}")
    ok, why = set_ownership_reason(element_guid, behalf.owner, identity=identity)
    if not ok:
        reasons.append(f"Ownership: {why}")
    return f"requester not recorded ({'; '.join(reasons)})" if reasons else ""


def ownership_body(owner: str, owner_type_name: str = _OWNER_TYPE_NAME,
                   owner_property_name: str = _OWNER_PROPERTY_NAME) -> dict:
    """The `NewClassificationRequestBody` for `Ownership` (`0445`).

    **`OwnershipProperties`, not `OwnerProperties`.** pyegeria's own
    `add_ownership_to_element` docstring says `OwnerProperties` in its sample
    body, and that is wrong: the method passes `prop=["OwnershipProperties"]`
    to `_async_new_classification_request`, and
    `validate_new_classification_request` rejects anything else with
    `unexpected property class name` before a request is ever made.
    `pyegeria/http clients/Egeria-api-classification-explorer.http` agrees with
    the code. Found by running against the live platform rather than by
    reading — the docstring version fails with a validation error that names
    neither the expected class nor the one supplied. Logged as an issue against
    egeria-python; the value here follows the code, which is what the server
    actually enforces.
    """
    return {
        "class": "NewClassificationRequestBody",
        "properties": {
            "class": "OwnershipProperties",
            "owner": owner,
            "ownerTypeName": owner_type_name,
            "ownerPropertyName": owner_property_name,
        },
    }


def person_ownership_body(user_id: str) -> "tuple[dict, Any]":
    """(the Ownership body, its `egeria_actors.OwnershipShape`) for a PERSON's userId (7h).

    With a profile linked to the person's UserIdentity: owner = the profile's qualifiedName,
    ownerTypeName = its type, ownerPropertyName "qualifiedName" — Egeria's own shape
    (`OpenLineageCataloguerIntegrationConnector.addOwnership`). Otherwise the userId form. A
    failed lookup falls back to the userId form and is reported (`shape.note`); never raises."""
    from resource_explorer.egeria_actors import ownership_for_person

    shape = ownership_for_person(user_id)
    return ownership_body(shape.owner, shape.owner_type_name, shape.owner_property_name), shape


def zone_membership_body(zones: Iterable[str]) -> dict:
    """The `NewClassificationRequestBody` for `ZoneMembership` (`0424`)."""
    return {
        "class": "NewClassificationRequestBody",
        "properties": {
            "class": "ZoneMembershipProperties",
            "zoneMembership": list(zones),
        },
    }


def classification_client(identity: Optional[EgeriaIdentity] = None):
    """A `ClassificationExplorer` authenticated as `identity`.

    Built per call rather than cached on a publisher: the whole point is that
    the client carries *this* caller's credential, and a cached one carries
    whoever built it first.
    """
    from pyegeria import ClassificationExplorer

    from resource_explorer.egeria_clients import egeria_client

    return egeria_client(identity or caller_credentials(), purpose="classification").of(ClassificationExplorer)


def set_ownership(
    element_guid: str,
    owner: str,
    *,
    identity: Optional[EgeriaIdentity] = None,
    client: Any = None,
    owner_type_name: Optional[str] = None,
) -> bool:
    """Classify `element_guid` as owned by `owner`. True when Egeria accepted it.

    Best-effort by design: a publish that produced real elements must not be
    reported as a failure because one classification call did not land. The
    failure is logged and returned as False so a caller that cares (the tests,
    and the publish response's warning field) can say so.
    """
    return set_ownership_reason(element_guid, owner, identity=identity, client=client,
                                owner_type_name=owner_type_name)[0]


def set_ownership_reason(element_guid: str, owner: str, *, identity: Optional[EgeriaIdentity] = None,
                         client: Any = None, owner_type_name: Optional[str] = None) -> tuple[bool, str]:
    """`set_ownership`, with the reason when it did not land. Never raises (round 5): it runs after
    an element was created, so a client that cannot be built (no sign-in, a refused platform) is a
    PARTIAL outcome with its reason — raising here would skip the caller's own record of the
    element it just created.

    `owner` is a PERSON's userId unless `owner_type_name` says otherwise: the person's profile is
    looked up (7h, `person_ownership_body`) and named when Egeria has one."""
    return _set_ownership(element_guid, owner, identity=identity, client=client,
                          owner_type_name=owner_type_name)[:2]


def _set_ownership(element_guid: str, owner: str, *, identity: Optional[EgeriaIdentity] = None,
                   client: Any = None, owner_type_name: Optional[str] = None) -> tuple[bool, str, Any]:
    """(landed, reason, OwnershipShape or None). The one Ownership write."""
    if not element_guid or not owner:
        return False, "no element or no owner", None
    try:
        client = client or classification_client(identity)
    except Exception as exc:  # noqa: BLE001 - reported as partial by the caller
        log.warning("egeria: no Ownership client for %s — %s: %s", element_guid, type(exc).__name__, exc)
        return False, f"the Ownership client could not be built ({type(exc).__name__}: {str(exc)[:160]})", None
    if owner_type_name is None:
        body, shape = person_ownership_body(owner)
    else:
        body, shape = ownership_body(owner, owner_type_name), None
    try:
        client.add_ownership_to_element(element_guid, body)
        log.info("egeria: Ownership(owner=%s, %s) set on %s", body["properties"]["owner"],
                 body["properties"]["ownerTypeName"], element_guid)
        return True, "", shape
    except Exception as exc:
        log.warning(
            "egeria: could not set Ownership(owner=%s) on %s — %s: %s",
            owner, element_guid, type(exc).__name__, exc,
        )
        return False, f"{type(exc).__name__}: {str(exc)[:200]}", shape


def set_zone_membership(
    element_guid: str,
    zones: Iterable[str],
    *,
    identity: Optional[EgeriaIdentity] = None,
    client: Any = None,
) -> bool:
    """Put `element_guid` in exactly `zones`. True when Egeria accepted it.

    `add_zone_membership` *replaces* the classification's property rather than
    appending, which is what promotion needs: an accepted element leaves the
    draft zone in the same call that puts it in the publish zones, with no
    window in which it is in both.
    """
    zones = list(zones)
    if not element_guid or not zones:
        return False
    try:
        client = client or classification_client(identity)
        client.add_zone_membership(element_guid, zone_membership_body(zones))
        log.info("egeria: ZoneMembership%s set on %s", zones, element_guid)
        return True
    except Exception as exc:
        log.warning(
            "egeria: could not set ZoneMembership%s on %s — %s: %s",
            zones, element_guid, type(exc).__name__, exc,
        )
        return False


def clear_zone_membership(
    element_guid: str,
    *,
    identity: Optional[EgeriaIdentity] = None,
    client: Any = None,
) -> bool:
    """Remove the element's `ZoneMembership` classification ("zones left to Egeria"). True when
    Egeria accepted the call. The documented clear call (`clear_zone_membership`, `0424`), used
    when promotion has no CONFIGURED zone to move the element into: RE's draft zone must not stay
    on an accepted element, and RE must not invent a zone to replace it.

    UNVERIFIED LIVE: the body is `{"class": "DeleteClassificationRequestBody"}` per pyegeria's
    model; the caller reads the zones back (`read_zones`) before it says anything is cleared."""
    if not element_guid:
        return False
    try:
        client = client or classification_client(identity)
        client.clear_zone_membership(element_guid, {"class": "DeleteClassificationRequestBody"})
        log.info("egeria: ZoneMembership cleared on %s", element_guid)
        return True
    except Exception as exc:
        log.warning(
            "egeria: could not clear ZoneMembership on %s — %s: %s",
            element_guid, type(exc).__name__, exc,
        )
        return False


def _metadata_client(identity: Optional[EgeriaIdentity] = None):
    """A `MetadataExpert` authenticated as `identity` (the read side of zone checks)."""
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.egeria_clients import egeria_client

    return egeria_client(identity or caller_credentials(), purpose="zone read").of(MetadataExpert)


class ZoneReadError(RuntimeError):
    """The element's zones could not be read: not the same as "it has none"."""


def read_zones(element_guid: str, identity: Optional[EgeriaIdentity] = None) -> list[str]:
    """The element's zones as Egeria holds them NOW. `[]` means Egeria answered and the element has
    no `ZoneMembership`; an unreadable answer RAISES `ZoneReadError`, never `[]`
    (`current_zones` is the lenient reader whose `[]` means "could not tell")."""
    from resource_explorer.catalogue_gateway import zones_of_element

    if not element_guid:
        raise ZoneReadError("no element to read")
    try:
        element = _metadata_client(identity).get_metadata_element_by_guid(element_guid)
        return zones_of_element(element)
    except Exception as exc:                      # re-raised as the one named error, never swallowed
        raise ZoneReadError(f"could not read the zones of {element_guid[:8]}: {exc}") from exc


def current_zones(element_guid: str, identity: Optional[EgeriaIdentity] = None) -> list[str]:
    """The zones `element_guid` is in right now, or `[]` if we could not tell.

    **`[]` means "we could not tell", and callers must treat it that way.**
    Egeria's security connector rejects a zone change whose before and after
    are equal, so `promote_to_publish_zones` checks this first — and it treats
    an empty answer as "go ahead and try" rather than as "the element is in no
    zones". Read the other way round, an unreachable Egeria would look like an
    element that needs promoting and a genuinely promoted element would be
    re-promoted into a 403.
    """
    from resource_explorer.catalogue_gateway import zones_of_element

    if not element_guid:
        return []
    try:
        client = _metadata_client(identity)
        element = client.get_metadata_element_by_guid(element_guid)
        if not isinstance(element, dict):
            return []
        # The one zone reader (6.2's `elementHeader.zoneMembership` first, then the `classifications` list); an
        # answer it cannot read raises, which this lenient reader turns into "could not tell" below.
        return zones_of_element(element)
    except Exception as exc:
        log.debug("egeria: could not read zones of %s — %s", element_guid, exc)
        return []


def stamp_published(
    element_guid: str,
    owner: str,
    *,
    identity: Optional[EgeriaIdentity] = None,
    client: Any = None,
    zones: Optional[Iterable[str]] = None,
) -> dict:
    """`Ownership` + `ZoneMembership` (the zones passed in; by default the CONFIGURED draft
    zone, if any, else none) on one just-published element.

    The pair is applied together because they are one decision — "this is
    yours, and (when a draft zone is configured) it is not visible outside it yet" — and a publish
    that set one without the other would be either an unowned draft or an
    owned element already in the catalogue's normal zones.

    One client is built and reused across both calls: two classifications on
    one element should cost one authentication, not two.
    """
    zones = list(zones) if zones is not None else draft_zones()
    client = client or classification_client(identity)
    landed, _why, shape = _set_ownership(element_guid, owner, client=client)
    return {
        "ownership": landed,
        "zone_membership": set_zone_membership(element_guid, zones, client=client),
        "owner": owner,
        "zones": zones,
        # 7h: which Ownership form went out ('profile' | 'userId'), and why the userId form when a
        # profile lookup failed ('' otherwise). None when no Ownership was attempted.
        "ownership_form": shape.form if shape is not None else None,
        "ownership_note": shape.note if shape is not None else "",
    }


# ---------------------------------------------------------------------------
# The zone itself
# ---------------------------------------------------------------------------

#: Qualified name of RE's draft zone element.
def _zone_qualified_name(zone: str) -> str:
    return f"GovernanceZone::{zone}"


#: How long after WRITING the control before the enforcing connector has it.
#:
#: Measured 2026-09-07 against the quickstart platform: a control written at
#: 02:53:11 was still not enforced 6 minutes later and WAS enforced at 7:03.
#: The deployment's secrets collection declares `refreshTimeInterval: 10`,
#: which `SecretsStoreConnector` multiplies by `60 * 1000` — minutes. The
#: connector reloads on its own timer, so the wait from any given write is
#: anywhere from ~0 to the full interval.
#:
#: Default is deliberately longer than the interval. Being late costs a few
#: minutes of refused private publishing; being early publishes private work
#: into a zone that is not yet enforced, which cannot be undone.
PRIVATE_ZONE_SETTLE_SECONDS = int(os.environ.get("EXPLORER_PRIVATE_ZONE_SETTLE", "720"))

#: Cached result of the last `ensure_private_zone_exists()`. `None` means "not
#: yet asked", which is deliberately NOT the same as "not enforced" — see
#: `private_zone_is_enforced`.
_private_zone_state: Optional[dict] = None


def _platform_name() -> str:
    """The catalogued OMAG Server Platform the Security Officer API addresses.

    Thin wrapper over `_resolve_platform()` for callers (and tests) that only
    need the name and not the guid.
    """
    return _resolve_platform()[0]


def _resolve_platform() -> "tuple[str, Optional[str]]":
    """The catalogued OMAG Server Platform the Security Officer API addresses.

    Returns `(platform_name, platform_guid)`. `platform_guid` is `None` unless
    it was resolved from the catalogue — callers that have it should pass it
    as pyegeria's `platform_guid=` to `get_/set_security_access_control`,
    which bypasses the by-name lookup entirely. That matters because name
    alone can be ambiguous in a way no operator input fixes (see below).

    `EXPLORER_EGERIA_PLATFORM_NAME` wins, with no guid — the operator is
    naming a platform, not picking one out of the catalogue. Otherwise the
    single catalogued `SoftwareServerPlatform`, ignoring the archive's
    `~{placeholder}~` template entries — a real deployment has one, and if
    it somehow has several we try to identify the right one before refusing.
    """
    configured = (os.environ.get("EXPLORER_EGERIA_PLATFORM_NAME") or "").strip()
    if configured:
        return configured, None
    from pyegeria import EgeriaTech

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    tech = egeria_client(Daemon(DaemonReason.ZONE_SETUP), purpose="resolve platform").of(EgeriaTech)
    entries: list[tuple[str, Optional[str], str]] = []  # (name, guid, urlRoot)
    for el in tech.get_elements("SoftwareServerPlatform", output_format="JSON") or []:
        props = el.get("properties") or {}
        name = (props.get("displayName") or "").strip()
        if not name or name.startswith("~"):
            continue
        guid = ((el.get("elementHeader") or {}).get("guid") or "").strip() or None
        url_root = ((props.get("additionalProperties") or {}).get("platformURLRoot") or "").strip()
        entries.append((name, guid, url_root))
    names = [e[0] for e in entries]
    if len(entries) == 1:
        return entries[0][0], entries[0][1]
    if not entries:
        raise RuntimeError(
            "could not identify the platform to configure: no cataloged "
            "SoftwareServerPlatform found. Set EXPLORER_EGERIA_PLATFORM_NAME."
        )

    # Several platforms are catalogued — possibly under the SAME display name.
    # Confirmed live 2026-09-23: one quickstart boot self-registered TWICE,
    # once per hostname it is reachable under (`host.docker.internal` beside
    # `localhost`), producing two entries with identical displayName AND
    # identical `identifier`. Name cannot disambiguate that case even with an
    # operator's help, so try RE's own configured connection URL first — that
    # is a fact about what THIS process talks to, not a guess, and unlike the
    # holder-probe below it works even before any control has ever been
    # created (which is exactly the situation a first-boot ambiguity like
    # this one is in).
    from resource_explorer.config import get_config

    configured_url = (get_config().egeria.platform_url or "").strip().rstrip("/").lower()
    url_matches = [e for e in entries if e[2].strip().rstrip("/").lower() == configured_url]
    if configured_url and len(url_matches) == 1:
        name, guid, _ = url_matches[0]
        log.info("egeria: several platforms cataloged %s; using %r (guid %s), whose "
                 "platformURLRoot matches this process's configured EGERIA_PLATFORM_URL",
                 names, name, guid)
        return name, guid

    # Before refusing, ask which of them already holds OUR control — that is
    # a fact, not a guess, and it is the common case on a deployment that has
    # been running: the control was created on one specific platform and we
    # only need to find it again.
    #
    # This path became real on 2026-09-08: a redeploy catalogued a second
    # platform ("Local OMAG Server Platform" beside "Quickstart OMAG Server
    # Platform"), and the refusal below started firing on a deployment whose
    # private zone was demonstrably still enforcing. Refusing there is the safe
    # direction but it disables a working feature, which is its own kind of
    # wrong answer.
    zone = private_zone()
    probe_failed = ""
    try:
        from pyegeria.omvs.security_officer import SecurityOfficer

        probe = egeria_client(Daemon(DaemonReason.ZONE_SETUP),
                              purpose="resolve platform").of(SecurityOfficer)
        holders: list[tuple[str, Optional[str]]] = []
        for name, guid, _ in entries:
            try:
                got = probe.get_security_access_control(name, zone)
            except Exception:
                continue
            if got and (got.get("associatedSecurityList") or {}):
                holders.append((name, guid))
        if len(holders) == 1:
            log.info("egeria: several platforms cataloged %s; using %r, which holds "
                     "the %r control", names, holders[0][0], zone)
            return holders[0]
        if len(holders) > 1:
            raise RuntimeError(
                f"the {zone!r} control exists on more than one cataloged platform "
                f"({[h[0] for h in holders]}), so which one governs this deployment is "
                "ambiguous. Set EXPLORER_EGERIA_PLATFORM_NAME."
            )
    except RuntimeError:
        raise
    except Exception as exc:
        # Recorded, not only logged. Both paths below refuse, so nothing here
        # turns a failure into a success — but they refuse with different WORDS,
        # and that difference is the point. "None of them holds it" is a
        # positive claim about Egeria, and we are entitled to make it only if
        # the probe actually ran. A probe that threw leaves us unable to tell,
        # and an operator reading "none holds it" goes looking for a missing
        # control instead of a broken Security Officer call. Undetermined is
        # not absent.
        probe_failed = f"{type(exc).__name__}: {exc}"
        log.debug("could not probe platforms for the %r control: %s", zone, exc)

    if probe_failed:
        raise RuntimeError(
            f"could not identify the platform to configure: found {names}, and "
            f"asking which of them holds the {zone!r} control failed "
            f"({probe_failed}) — so whether one does is unknown, not no. Set "
            "EXPLORER_EGERIA_PLATFORM_NAME to say which platform governs this "
            "deployment."
        )

    # The probe ran, and none of them holds it. This would therefore be a CREATE
    # with no evidence for where it belongs, and refusing beats writing security
    # configuration to an arbitrary platform.
    raise RuntimeError(
        f"could not identify the platform to configure: found {names}, and none "
        f"holds the {zone!r} control. Set EXPLORER_EGERIA_PLATFORM_NAME to say "
        "which platform governs this deployment."
    )


def ensure_private_zone_exists(identity: Optional[EgeriaIdentity] = None) -> dict:
    """Create the `SecurityAccessControl` that makes `private_zone()` ENFORCED.

    This is the half that actually denies, and it is a different mechanism from
    `ensure_draft_zone_exists`. That one creates a `GovernanceZone` *metadata
    element*, which is documentation: the security connector never reads it. The
    thing the connector reads is the platform's secrets store, reached through
    the Security Officer OMVS —

        SecurityOfficer.set_security_access_control
          -> OpenMetadataPlatformSecurityVerifier.setSecurityAccessControl
          -> userSecurityConnector.setSecurityAccessControl

    and `getAssociatedSecurityListForZone` reads that same store. A zone element
    without a control is **decorative**: the connector does not recognise the
    zone, so it *ignores* it rather than treating it as restrictive, and
    everything in it is world-readable while looking private.

    **A read-back is not proof of enforcement, and this is the subtle part.**
    The store and the connector that reads it are not the same thing: the
    connector reloads on `refreshTimeInterval`. Measured live — a control
    written at 02:53:11 read back from the store immediately, and a
    privately-zoned element was still readable by a non-owner **six minutes
    later**, then denied at seven. So a freshly created control reports
    `enforced: False` until `PRIVATE_ZONE_SETTLE_SECONDS` has passed. A control
    that was already there when we first looked predates this process and is
    treated as settled.

    The security list names a group that nobody holds. That is the whole point:
    the list must never admit anyone, because access for the owner comes from
    the `userId`-as-zone-name shortcut instead. Two groups are deliberately NOT
    used — `openMetadataMember` (effectively everyone) and `instanceOwnersGroup`
    (whose `isUserAnOwner` returns **true when an element carries no `Ownership`
    classification at all**, so one unstamped element would be readable by
    anybody).

    Requires platform-operator rights (`validateUserAsOperatorForPlatform`, whose
    base implementation only throws — there is no permissive fallback). On the
    Coco-Pharma-seeded quickstart, RE's own account has them. On a stock
    freshstart NO human account does, so this returns `not_authorized` with the
    groups that would grant it, and the caller must treat private publishing as
    unavailable rather than proceeding. Never raises.
    """
    global _private_zone_state
    import time as _time

    zone = private_zone()
    try:
        from pyegeria.omvs.security_officer import SecurityOfficer

        from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

        client = egeria_client(identity or Daemon(DaemonReason.ZONE_SETUP),
                               purpose="private zone").of(SecurityOfficer)
        platform, platform_guid = _resolve_platform()
        guid_kwargs = {"platform_guid": platform_guid} if platform_guid else {}

        existing = client.get_security_access_control(platform, zone, **guid_kwargs)
        if existing and (existing.get("associatedSecurityList") or {}):
            facts = assess_zone_control(zone, existing)
            if facts["verdict"] == "blocked":
                # An operator's control that lists neither READ nor DEFAULT. Today Egeria treats the zone as
                # unsecured for a read (world-readable); once e9fd817f4c ships it grants nobody. Neither is
                # private, so it is reported and refused, never overwritten (it is not RE's to rewrite).
                _private_zone_state = {
                    "status": "locks_out", "zone": zone, "platform": platform,
                    "control_present": True, "enforced": False, "zone_control": facts,
                    "detail": f"the {zone!r} control ({', '.join(facts['operations'])} only): {facts['words']}",
                    "remedy": f"Add a DEFAULT (or READ) list to the {zone!r} security access control.",
                }
                return _private_zone_state
            # Already there when we first looked, so it predates this process
            # and the connector has had at least as long as we have been up.
            _private_zone_state = {
                "status": "exists", "zone": zone, "platform": platform,
                "control_present": True, "enforced": True, "zone_control": facts,
                "basis": "control was already present when this process first looked",
            }
            return _private_zone_state

        client.set_security_access_control(platform, {
            "class": "SecurityAccessControlRequestBody",
            "securityAccessControl": {
                "controlName": zone,
                "controlDisplayName": "Resource Explorer — private",
                "controlTypeName": "GovernanceZone",
                "description": (
                    "Elements belonging to a Personal or Experiment investigation "
                    "in Resource Explorer. Readable only by their creator, whose "
                    "userId is carried as a second zone on each element. The "
                    "security list below names a group nobody holds, on purpose: "
                    "it exists to make this a SECURED zone so that everyone else "
                    "is denied, not to admit anybody."
                ),
                "associatedSecurityList": {"DEFAULT": ["resourceExplorerPrivateNobody"]},
                "otherProperties": {
                    "criteria": "Published by resource-explorer from a private investigation.",
                    "createdBy": "resource-explorer",
                },
            },
        }, **guid_kwargs)

        # Read back. A write that returned without raising is not evidence the
        # control exists — the same lesson as the classification read-back in
        # `egeria_investigation_publisher`.
        back = client.get_security_access_control(platform, zone, **guid_kwargs)
        if not (back and (back.get("associatedSecurityList") or {})):
            log.error(
                "egeria: wrote SecurityAccessControl %r but it did not read back with "
                "a security list — the zone would be IGNORED by the security "
                "connector, so private publishing stays disabled.", zone)
            _private_zone_state = {
                "status": "unconfirmed", "zone": zone, "platform": platform,
                "control_present": False, "enforced": False,
                "zone_control": assess_zone_control(zone, back),
                "detail": "the control did not read back with a security list",
            }
            return _private_zone_state

        settle_at = _time.time() + PRIVATE_ZONE_SETTLE_SECONDS
        log.info("egeria: created SecurityAccessControl %r on %r; enforcement expected "
                 "within %ss once the security connector reloads its secrets store",
                 zone, platform, PRIVATE_ZONE_SETTLE_SECONDS)
        _private_zone_state = {
            "status": "created", "zone": zone, "platform": platform,
            "control_present": True, "enforced": False,
            "zone_control": assess_zone_control(zone, back),
            "settle_after": settle_at,
            "detail": (
                f"just created; the security connector reloads on its own timer, so "
                f"enforcement is not assumed for {PRIVATE_ZONE_SETTLE_SECONDS}s. "
                "Private publishing is refused until then."
            ),
        }
        return private_zone_status()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        unauthorized = "UserNotAuthorized" in detail or "not authorized" in detail.lower()
        log.warning(
            "egeria: could not ensure the private zone %r is enforced — %s. "
            "Private investigations will NOT publish until this is fixed.", zone, detail)
        _private_zone_state = {
            "status": "not_authorized" if unauthorized else "error",
            "zone": zone, "control_present": False, "enforced": False,
            "zone_control": assess_zone_control(zone, None, unreadable=detail[:300]),
            "detail": detail,
            "remedy": (
                "Writing a security access control needs platform-operator rights. "
                "Grant RE's Egeria account one of: serverOperator, infrastructureTeam, "
                "devOpsTeam, dataManagementTeam, securityTeam, serverAdministrator, "
                "runtimeManager, metadataArchitect, platform — or have an operator "
                f"create the {zone!r} GovernanceZone security access control."
            ),
        }
        return _private_zone_state


def private_zone_is_enforced() -> bool:
    """Whether the private zone is KNOWN to deny. Defaults to False.

    Four states collapse to False here, and only one of them is "we checked and
    it does not": never asked, could not ask, confirmed absent, and **present
    but not yet loaded by the enforcing connector**. That last one is the case a
    store read-back cannot see, and it is real — measured at seven minutes.

    The asymmetry is deliberate. Guessing True when the control is missing or
    not yet live means publishing somebody's private work into a zone the
    connector ignores — world-readable, while every RE screen says private.
    Guessing False only means private publishing is refused until somebody
    looks, which is recoverable and visible.
    """
    return bool(private_zone_status().get("enforced"))


def private_zone_status() -> dict:
    """What we currently know, for the admin surface.

    `status: "unknown"` when nothing has asked yet — not a synonym for "not
    enforced", though both refuse a private publish. Resolves `settle_after`
    into the live `enforced` answer so callers never have to do clock
    arithmetic to find out whether they may publish.
    """
    import time as _time

    if _private_zone_state is None:
        return {"status": "unknown", "zone": private_zone(), "control_present": False,
                "enforced": False, "detail": "not checked yet this process",
                "zone_controls": zone_controls_report()}
    state = dict(_private_zone_state)
    # The start-up report over EVERY zone RE stamps (the private zone's entry is `zone_control`).
    state["zone_controls"] = zone_controls_report()
    settle_after = state.pop("settle_after", None)
    if settle_after is not None:
        remaining = max(0, int(settle_after - _time.time()))
        state["enforced"] = state.get("control_present", False) and remaining == 0
        state["settling_seconds_remaining"] = remaining
        if remaining:
            state["status"] = "settling"
    return state


# ---------------------------------------------------------------------------
# The start-up check over every zone RE stamps (design brief, 2026-10-14)
# ---------------------------------------------------------------------------
#
# Egeria e9fd817f4c ("Make governance zones without a security control private"; origin/main 003f6f6510,
# `OpenMetadataAccessSecurityConnector.validateZoneAccess` ~1094-1205) secures EVERY named zone:
#
# * a zone named after the userId grants first, so the owner-id zone RE puts beside the private zone is exempt;
# * any other zone with no SecurityAccessControl, or whose control has neither a list for the operation nor a
#   DEFAULT list (`getAssociatedSecurityListForZone`: the operation's enum NAME, then DEFAULT), grants NOBODY;
# * an element in no zone stays open.
#
# Not in the image running on 2026-10-14 (built 2026-10-06), but in the next rebuild. Before it, such a zone is
# "not secured" and ignored, so the same element was readable by everyone; after it, by no one (RE's own
# daemon, Egeria's survey engine and the cataloguer included) and nothing can unzone it. So RE reads each
# control at start-up, records three facts (control present / absent, the operations it lists, a DEFAULT list
# or not), and refuses to publish into a zone that would lock everyone out.

#: The words for a zone whose control would grant nobody once e9fd817f4c is in the running image.
ZONE_LOCKOUT_WORDS = "would lock out everyone once Egeria e9fd817f4c ships"
#: The words for a READ-only control (no DEFAULT, no write-operation list): readable, but no write is granted.
ZONE_WRITES_DENIED_WORDS = "writes would be denied once Egeria e9fd817f4c ships"
#: A control that could not be read. Never "fine": see `zone_lockout_refusal`.
ZONE_COULD_NOT_CHECK_WORDS = "could not check"

#: `AccessOperation` names (open-connector-framework .../users/AccessOperation.java) other than READ and
#: DEFAULT: the operations a write is judged on.
_WRITE_OPERATIONS = frozenset({
    "CREATE", "UPDATE_PROPERTIES", "UPDATE_STATUS", "DELETE", "ATTACH", "DETACH", "ADD_MEMBER",
    "DELETE_MEMBER", "ADD_FEEDBACK", "DELETE_FEEDBACK", "CLASSIFY", "DECLASSIFY", "PUBLISH",
})

#: The last `check_zone_controls()` answer for the zones OTHER than the private zone, `{zone: entry}`. The
#: private zone's entry lives in `_private_zone_state["zone_control"]`, written by the same read that decides
#: whether it is enforced (`ensure_private_zone_exists`). `None` = not checked in this process yet.
_zone_controls_state: Optional[dict] = None


def stamped_zones() -> list[str]:
    """Every zone RE itself will stamp: the private zone, then any configured draft or publish zone. Never
    the owner-userId zone (exempt in Egeria: `userId.equals(zoneName)` grants first)."""
    return list(dict.fromkeys([private_zone(), *draft_zones(), *configured_publish_zones()]))


def assess_zone_control(zone: str, control: Any, *, unreadable: str = "") -> dict:
    """The three facts about one zone's control, and what they mean once e9fd817f4c ships.

    `control` is pyegeria's answer (`get_security_access_control`: the `securityAccessControl` dict, or None
    when Egeria holds no such control). `unreadable` is why the read failed; then nothing is claimed about the
    control (`operations` / `default_list` are None, not empty) and the verdict is `could_not_check`.
    Keys are read the way Egeria reads them: a list is there when its key holds a non-null value.
    """
    if unreadable:
        return {"zone": zone, "control": "could not check", "operations": None, "default_list": None,
                "verdict": "could_not_check", "words": f"{ZONE_COULD_NOT_CHECK_WORDS} ({unreadable})"}
    if not isinstance(control, dict):
        return {"zone": zone, "control": "absent", "operations": [], "default_list": False, "verdict": "blocked",
                "words": f"no security access control: {ZONE_LOCKOUT_WORDS}"}
    lists = control.get("associatedSecurityList")
    lists = {k: v for k, v in lists.items() if v is not None} if isinstance(lists, dict) else {}
    operations = sorted(k for k in lists if k != "DEFAULT")
    has_default = "DEFAULT" in lists
    entry = {"zone": zone, "control": "present", "operations": operations, "default_list": has_default}
    if not has_default and "READ" not in lists:
        return {**entry, "verdict": "blocked",
                "words": f"its control lists neither READ nor DEFAULT: {ZONE_LOCKOUT_WORDS}"}
    if not has_default and not (_WRITE_OPERATIONS & set(lists)):
        return {**entry, "verdict": "warned",
                "words": f"READ only, no DEFAULT and no write-operation list: {ZONE_WRITES_DENIED_WORDS}"}
    return {**entry, "verdict": "ok", "words": "ok"}


def _read_zone_control(zone: str) -> dict:
    """Read one zone's control through RE's control reader (`zone_access.EgeriaAccessReader.control`: the
    Security Officer client as RE's daemon, the resolved platform guid, bounded, and pyegeria's catch wrapper
    treated as unreadable). Never raises: a failed read is the `could_not_check` entry."""
    from resource_explorer.zone_access import EgeriaAccessReader

    try:
        return assess_zone_control(zone, EgeriaAccessReader().control(zone))
    except Exception as exc:  # noqa: BLE001 - every failure is "could not check", never "no control"
        reason = str(exc) if type(exc).__name__ == "AccessUnreadable" else f"{type(exc).__name__}: {exc}"
        return assess_zone_control(zone, None, unreadable=reason[:300])


def check_zone_controls(zones: Optional[Iterable[str]] = None) -> list[dict]:
    """Read the control of every stamped zone other than the private zone (or just `zones`), remember the
    answers, and return the whole start-up report (`zone_controls_report`). Never raises.

    The private zone is not read here: `ensure_private_zone_exists` reads it (and creates it when missing),
    and records its entry from that same read, so the facts shown and the `enforced` gate cannot disagree."""
    global _zone_controls_state
    targets = [z for z in (zones if zones is not None else stamped_zones()) if z and z != private_zone()]
    state = dict(_zone_controls_state or {})
    for zone in dict.fromkeys(targets):
        state[zone] = _read_zone_control(zone)
    _zone_controls_state = state
    return zone_controls_report()


def zone_controls_report() -> list[dict]:
    """One entry per stamped zone, in stamping order: what the last check found, or `not checked yet`."""
    out = []
    for zone in stamped_zones():
        if zone == private_zone():
            entry = (_private_zone_state or {}).get("zone_control")
        else:
            entry = (_zone_controls_state or {}).get(zone)
        out.append(entry or {"zone": zone, "control": "not checked yet", "operations": None,
                             "default_list": None, "verdict": "unknown", "words": "not checked yet"})
    return out


def zone_controls_line(report: Optional[list[dict]] = None) -> str:
    """The start-up report as one log line: `zone · control … · lists … · DEFAULT yes/no · words` per zone."""
    parts = []
    for e in report if report is not None else zone_controls_report():
        if e["control"] == "present":
            ops = ", ".join(e["operations"]) or "no operation"
            parts.append(f"{e['zone']} · control present · lists {ops} · DEFAULT "
                         f"{'yes' if e['default_list'] else 'no'} · {e['words']}")
        elif e["control"] == "absent":
            parts.append(f"{e['zone']} · control absent · {e['words']}")
        else:
            parts.append(f"{e['zone']} · {e['words']}")
    return "zone controls: " + ("; ".join(parts) or "no zone stamped")


def zone_lockout_refusal(zones: Iterable[str]) -> str:
    """Why a write that stamps `zones` must not happen, or `""` when it may.

    Judges only the zones RE stamps other than the private zone (that one has its own gate,
    `EgeriaPublisher._require_enforced_private_zone`, which carries the same words); the owner-userId zone and
    any zone RE did not configure are not read. A zone not checked yet in this process is checked now, and a
    zone last seen blocked or unreadable is RE-READ before refusing, so an operator's fix takes effect without
    a restart.

    **Unreadable blocks**, as it does for the private zone (`private_zone_is_enforced` is False for `error` and
    `unknown`): the cost asymmetry is the same. Refusing is visible and recoverable; publishing into a zone
    that turns out to grant nobody leaves elements nobody can read or unzone, RE's own daemon included."""
    stamped = set(stamped_zones()) - {private_zone()}
    targets = [z for z in dict.fromkeys(zones) if z in stamped]
    if not targets:
        return ""
    known = _zone_controls_state or {}
    recheck = [z for z in targets if (known.get(z) or {}).get("verdict") not in ("ok", "warned")]
    if recheck:
        check_zone_controls(recheck)
    known = _zone_controls_state or {}
    problems = [known[z] for z in targets if known[z]["verdict"] in ("blocked", "could_not_check")]
    return "; ".join(f"zone '{e['zone']}': {e['words']}" for e in problems)


def ensure_draft_zone_exists(identity: Optional[EgeriaIdentity] = None) -> dict:
    """Create RE's draft `GovernanceZone` if Egeria does not have one. Idempotent.

    Called once from the worker's leader-elected startup, so N processes
    create at most one zone and a fresh Egeria gets one without an operator
    step.

    **pyegeria has no GovernanceZone create or lookup** — searched 2026-09-04
    across `pyegeria/omvs/`: `governance_officer.py` creates
    `GovernanceDefinition`s (a different type family — `GovernanceZone` is
    `0424`, not `0401`), and every other `governance_zone` hit is a
    *filter* parameter on a find. Logged as an issue against egeria-python;
    until it has a first-class call this goes through
    `MetadataExpert.create_metadata_element`, the generic element create, with
    `get_metadata_element_by_unique_name` as the idempotency check.

    Never raises. A deployment whose Egeria is down at worker start must still
    start; the zone is created on the next attempt, and until then a publish
    still names the zone in its `ZoneMembership` — a zone classification does
    not require the zone element to exist, it just cannot be navigated to.
    """
    zone = draft_zone()
    if not zone:
        # Default: no draft zone is configured, so there is nothing to create (2026-10-08 ruling).
        return {"status": "none", "zone": None}
    qualified_name = _zone_qualified_name(zone)
    try:
        from pyegeria.omvs.metadata_expert import MetadataExpert

        from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

        client = egeria_client(identity or Daemon(DaemonReason.ZONE_SETUP),
                               purpose="draft zone").of(MetadataExpert)

        existing = _existing_guid(client, qualified_name)
        if existing:
            return {"status": "exists", "zone": zone, "guid": existing}

        guid = client.create_metadata_element({
            "class": "NewOpenMetadataElementRequestBody",
            "typeName": "GovernanceZone",
            "isOwnAnchor": True,
            "properties": {
                "class": "ElementProperties",
                "propertyValueMap": {
                    "qualifiedName": _string_property(qualified_name),
                    "displayName": _string_property(zone),
                    "description": _string_property(
                        "Elements published by Resource Explorer that are awaiting "
                        "curation. Visible to the publishing user and to curators; "
                        "promoted into the deployment's publish zones on accept."
                    ),
                    "criteria": _string_property(
                        "Published by resource-explorer and not yet accepted."
                    ),
                },
            },
        })
        log.info("egeria: created GovernanceZone %r (%s)", zone, guid)
        return {"status": "created", "zone": zone, "guid": guid}
    except Exception as exc:
        log.warning(
            "egeria: could not ensure GovernanceZone %r exists — %s: %s. Publishes "
            "still carry the zone in their ZoneMembership classification.",
            zone, type(exc).__name__, exc,
        )
        return {"status": "error", "zone": zone, "error": str(exc)}




def _existing_guid(client: Any, qualified_name: str) -> str:
    """The GUID of an element with this qualified name, or `""`.

    **Match the GUID shape, never the error text.** `get_metadata_element_by_
    unique_name` signals "nothing here" by returning a human sentence, and the
    first version of this function tested for the literal `"No element found"`
    from pyegeria's own docstring. The platform actually says **"No elements
    found"** — plural — so the test passed on a miss, the caller reported
    `{"status": "exists", "guid": "No elements found"}`, and the zone was never
    created while the result said it already existed. Caught by running it
    against the live platform; nothing about the return value looked wrong from
    inside the process.

    Testing the shape of what we want, rather than the wording of what we don't,
    cannot go stale when the message changes. Same reasoning, and the same
    regex, as `EgeriaPublisher._find_element_guid`.
    """
    from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

    try:
        found = client.get_metadata_element_by_unique_name(qualified_name)
    except Exception as exc:
        # Only a real miss is "": a look-up that could not be made raises, so `ensure_draft_zone_exists`
        # reports an error and creates nothing (it used to create a second GovernanceZone beside one it
        # could not see).
        if lookup_exception_is_absent(exc):
            return ""
        raise RuntimeError(lookup_could_not_be_made(qualified_name, exc)) from exc
    from resource_explorer.egeria_absence import guid_from_answer

    # Found, a real miss (""), or could-not-check (raised): one shared reader (an odd answer used to be "").
    return guid_from_answer(found, qualified_name)


def _string_property(value: str) -> dict:
    return {
        "class": "PrimitiveTypePropertyValue",
        "typeName": "string",
        "primitiveValue": value,
    }
