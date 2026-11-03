"""Curate — materializing an accepted verdict into real Egeria elements.

Moved out of `web/routes/curate.py` (`_materialize_if_accepted` at :294,
`_find_candidate_blueprint` at :427, `_materialize_blueprint_if_accepted` at
:449, and the `_slug_to_scope_map` helper both depend on) in step 2b. The plan
(§3) lists this as web-only-today code sitting *above* a core class: the
`ComponentMaterializer` and `BlueprintMaterializer` were already core; only the
decision of when to invoke them, and the assembly of what to pass, lived in a
route module.

Nothing here raises for a materialization failure. The verdict is already saved
and real regardless of whether Egeria was reachable a moment ago, so a failure
comes back as `{"status": "error", "error": ...}` in the field the caller merges
into its response — the same non-fatal-but-visible shape
`EgeriaPublisher.publish()`'s `annotation_types_warning` uses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)


class CurationDenied(PermissionError):
    """The caller may not curate this element. Surfaces as HTTP 403.

    A distinct exception rather than a boolean so the enforcement lives at the
    workflow layer and every entry point inherits it — the plan's isolation
    matrix says authorization is "enforced at the workflow layer so the CLI and
    A2A honour it too", and a boolean returned to a route is a boolean the next
    route forgets to check.
    """


#: JWT `role` claims that may curate anything: an interim override until Egeria appointments.
#:
#: The plan's model is a `GovernanceRole` with `PersonRoleAppointment`, read from Egeria; "the Portal
#: `role` claim is the bridge until appointments are read from Egeria". Brief Z (2026-10-10) keeps it as
#: rule 2, ahead of the zone check: a curator/admin is allowed whatever the zones say.
CURATOR_ROLES = frozenset({"curator", "admin"})

#: Why `AccessDecision.basis` says what it says (tests and the admin surface read it; the screen reads `reason`).
BASIS_ANONYMOUS = "anonymous"
BASIS_PORTAL_ROLE = "portal_role"
BASIS_NO_ZONES = "no_zones"
BASIS_ZONES_OPEN = "zones_not_secured"
BASIS_ZONE_GRANTED = "zone_granted"
BASIS_ZONE_REFUSED = "zone_not_granted"
BASIS_UNREADABLE = "unreadable"

#: What a verdict (a decision recorded in RE) needs: an update of the element (connector :1497-1522).
VERDICT_OPERATIONS = ("UPDATE_PROPERTIES",)


@dataclass(frozen=True)
class AccessDecision:
    """May the current caller change this element? `reason` is empty when allowed: the "who may change
    this" words appear on refused rows only (Brief Z, status words)."""

    allowed: bool
    reason: str = ""
    basis: str = ""
    zones: tuple[str, ...] = ()


class _RegistryUnreadable(RuntimeError):
    """RE could not read its own row for the element: a deny reason, never "not in Egeria"."""


def element_guid_for(registry: ProjectRegistry, entity_type: str, slug: str, scope_locator: str) -> str:
    """The Egeria element a curation item writes to, from RE's own cache rows; "" when it is not in Egeria.

    For a repo: `perspective::cluster` is a blueprint, "" is the repository itself, anything else a component's
    scope. A branch scope with no component of its own has no element: its verdict is a decision only, and
    Publish checks every component it writes on its own. For a database or file system, the scope is the
    resource itself (its asset element).
    """
    scope = (scope_locator or "").strip()
    try:
        if entity_type == "investigation":
            # An investigation is an Egeria Project; "" until it is promoted (a local one has nothing to write to).
            inv = registry.get_investigation(slug) or {}
            return str(inv.get("egeria_project_guid") or "")
        if entity_type == "database":
            entity = registry.get_database(slug, allow_unreadable=True)
            return str(getattr(entity, "egeria_asset_guid", "") or "")
        if entity_type == "filesystem":
            entity = registry.get_filesystem(slug)
            return str(getattr(entity, "egeria_asset_guid", "") or "")
        if "::" in scope:
            perspective, _, cluster = scope.partition("::")
            row = registry.get_materialized_blueprint(entity_type, slug, perspective, cluster) or {}
            return str(row.get("guid") or "")
        if not scope:
            project = registry.get(slug) if entity_type == "repo" else None
            return str(getattr(project, "egeria_asset_guid", "") or "")
        row = registry.get_materialized_component(entity_type, slug, scope) or {}
        return str(row.get("guid") or "")
    except Exception as exc:  # a registry failure is not "not in Egeria": it is unreadable
        raise _RegistryUnreadable(f"RE could not read its own record of this element ({type(exc).__name__}: {exc})") from exc


def configured_zones_for(registry: ProjectRegistry, entity_type: str, slug: str) -> list[str]:
    """The zones RE itself would put this resource's elements in: the private zones when the resource is in a
    private investigation, else the configured draft and publish zones. `[]` = RE configures no zones."""
    from resource_explorer.egeria_identity import configured_publish_zones, draft_zones, private_zones

    owner = ""
    try:
        owner = registry.private_owner_for_entity(entity_type, slug) or ""
    except Exception as exc:
        raise _RegistryUnreadable(f"RE could not read whether this resource is private ({type(exc).__name__}: {exc})") from exc
    if isinstance(owner, str) and owner.strip():
        return private_zones(owner.strip())
    zones: list[str] = []
    for z in [*draft_zones(), *configured_publish_zones()]:
        if z and z not in zones:
            zones.append(z)
    return zones


def _caller_identity():
    from resource_explorer.a2a_auth import caller

    return caller()


def curation_access(registry: ProjectRegistry, entity_type: str, slug: str, scope_locator: str, *,
                    operations=None, reader=None, element_guid: str | None = None,
                    anchor_guid: str = "", creation_zones: list[str] | None = None,
                    zones_after: list[str] | None = None, foreign_after: tuple[str, ...] = ()) -> AccessDecision:
    """THE curation-rights decision (Brief Z). Every verdict route, Publish, the database commit and publish,
    the CLI and A2A call this.

    The project owner, 2026-10-10: "rely on Egeria's security model and emulate it - so if zones are used
    they can control access - if zones aren't being used then it is open".

    1. Not signed in: denied.
    2. A Portal `curator`/`admin` role: allowed (interim, until Egeria appointments).
    3. Zones not in use: allowed for any signed-in user. "Not in use" = the element (when it is in Egeria)
       carries no `ZoneMembership` AND RE configures no draft, publish or private zone for it. With RE
       configuring none and the element not in Egeria yet, nothing is read from Egeria at all.
    4. Zones in use: Egeria's own answer, emulated (`zone_access.zone_grants`, mirroring
       `OpenMetadataAccessSecurityConnector.validateZoneAccess`) for EVERY operation in `operations`: a verdict
       needs UPDATE_PROPERTIES (`VERDICT_OPERATIONS`); a Publish item needs what its write performs
       (`publish_operations`). An element already in Egeria is judged on ITS zones only (Brief Z2 round 2, as
       Egeria does: an existing element with no zones is open, whatever RE configures); an ANCHORED member is
       judged on its anchor's zones with the anchor operation, plus READ on the member
       (`zone_access.ANCHOR_OPERATIONS`, citing validateUserForAnchor*). An element not in Egeria yet is judged
       on the zones RE will create it in (`configured_zones_for`).
    5. Anything unreadable (Egeria unreachable, a zone, control or account read fails, RE's own row): DENIED,
       "could not check access in Egeria (<reason>)". Never open by default when zones might apply.

    `anchor_guid` (round 3): the element a NEW element will be anchored to (a schema's database). Egeria judges
    creating an anchored member on the ANCHOR's zones (validateUserForAnchorMemberCreate :1965-1990, maintainer =
    the user), so with no element yet and an anchor in Egeria, the anchor's zones decide; RE's configured zones
    are the answer only when there is no anchor in Egeria either.

    `creation_zones` (Brief Z3): the zones a NEW element that is neither a resource asset nor anchored is created
    in, when the caller knows them (an investigation's Project: its private zones, or none). Without it a new
    element is judged on `configured_zones_for` the resource.

    `zones_after` (Brief Z3 round 2): judge the EXISTING element `element_guid` on the zones an earlier write of
    the same press moves it to (Egeria checks a reclassify's CLASSIFY/DECLASSIFY after the zone move, on the new
    zones), with its own owners and maintainers. `[]` with `foreign_after` = "RE's own zones are cleared": the
    element keeps those of its current zones that are not in `foreign_after` (RE's), as
    `InvestigationReclassifier._clear_zones` does.

    `element_guid` checks a resolved element instead of the scope's cache row (Publish's run-time re-check of
    an element the write adopted). `decided_by` on a verdict is attribution only; it is not read here.
    `reader` is a `zone_access.EgeriaAccessReader` shared across one press so each read happens once.
    """
    from resource_explorer.zone_access import (
        CREATE_OPERATION,
        OPERATION_WORDS,
        AccessUnreadable,
        EgeriaAccessReader,
        zone_grants,
    )

    ops = tuple(operations or VERDICT_OPERATIONS)
    identity = _caller_identity()
    if identity is None or identity.auth_source == "anonymous" or not identity.user_id:
        return AccessDecision(False, "sign in to curate (POST /api/auth/login or `resource-explorer login`)",
                              BASIS_ANONYMOUS)
    if (identity.role or "").lower() in CURATOR_ROLES:
        return AccessDecision(True, "", BASIS_PORTAL_ROLE)
    user = identity.user_id
    reader = reader or EgeriaAccessReader()
    try:
        guid = element_guid if element_guid is not None else element_guid_for(registry, entity_type, slug, scope_locator)
        if guid and zones_after is not None:
            facts = reader.element(guid)
            zones = list(zones_after) or [z for z in facts.zones if z not in set(foreign_after)]
            if not zones:
                return AccessDecision(True, "", BASIS_NO_ZONES)
            verdict = zone_grants(user, zones, control_for=reader.control, account_for=lambda: reader.account(user),
                                  owners=facts.owners, maintainers=facts.maintainers, operations=ops)
        elif guid:
            verdict, zones = _existing_element_verdict(user, guid, ops, reader)
            if verdict is None:
                return AccessDecision(True, "", BASIS_NO_ZONES)
        elif anchor_guid:
            verdict, zones = _new_member_verdict(user, anchor_guid, ops, reader)
            if verdict is None:
                return AccessDecision(True, "", BASIS_NO_ZONES)
        else:
            zones = (list(creation_zones) if creation_zones is not None
                     else configured_zones_for(registry, entity_type, slug))
            if not zones:
                return AccessDecision(True, "", BASIS_NO_ZONES)
            verdict = zone_grants(user, zones, control_for=reader.control, account_for=lambda: reader.account(user),
                                  operations=ops, creating=CREATE_OPERATION in ops)
    except (AccessUnreadable, _RegistryUnreadable) as exc:
        return AccessDecision(False, f"could not check access in Egeria ({exc})", BASIS_UNREADABLE)
    if verdict.allowed:
        return AccessDecision(True, "", BASIS_ZONE_GRANTED if verdict.granted_by else BASIS_ZONES_OPEN, tuple(zones))
    if verdict.why:
        return AccessDecision(False, verdict.why, BASIS_ZONE_REFUSED, tuple(zones))
    secured = ", ".join(verdict.secured)
    word = OPERATION_WORDS.get(verdict.operation, verdict.operation.lower())
    hidden = set(verdict.person_zones or ())
    if hidden:
        # A zone named after the element's owner would tell the caller whose element it is: the verdict and a
        # reason that names no one; the full detail goes to the server log only.
        log.info("curation access: %s refused %s (secured zones %s; person zones withheld from the reason)",
                 user, word, secured)
    shown = [z for z in verdict.secured if z not in hidden]
    if not shown:
        return AccessDecision(False, f"private to its owner: {user} may not {word} it in Egeria",
                              BASIS_ZONE_REFUSED, tuple(zones))
    listed = ", ".join(shown) + (" and a zone private to its owner" if hidden else "")
    return AccessDecision(
        False, f"zone {listed} does not grant {user} {word} in Egeria" if len(shown) == 1 and not hidden
        else f"zones {listed} do not grant {user} {word} in Egeria",
        BASIS_ZONE_REFUSED, tuple(zones))


def _new_member_verdict(user: str, anchor_guid: str, ops: tuple[str, ...], reader):
    """A NEW member of `anchor_guid` (round 3): every operation of its write judged on the ANCHOR's zones as
    Egeria's validateUserForAnchor* would (`zone_access.ANCHOR_OPERATIONS`; CREATE is
    validateUserForAnchorMemberCreate :1965-1990, maintainer = the user). There is no member to READ yet.
    (None, []) when the anchor carries no zone: open, as Egeria (:1205)."""
    from resource_explorer.zone_access import ANCHOR_OPERATIONS, ZoneVerdict, zone_grants

    anchor = reader.element(anchor_guid)
    if not anchor.zones:
        return None, []
    last = ZoneVerdict(True)
    for op in ops:
        anchor_op, user_maintains, _read = ANCHOR_OPERATIONS.get(op, (op, False, False))
        last = zone_grants(user, anchor.zones, control_for=reader.control, account_for=lambda: reader.account(user),
                           owners=anchor.owners, maintainers=[user] if user_maintains else anchor.maintainers,
                           operations=(anchor_op,))
        if not last.allowed:
            return last, list(anchor.zones)
    return last, list(anchor.zones)


def _existing_element_verdict(user: str, guid: str, ops: tuple[str, ...], reader):
    """Egeria's answer for an element that is already in Egeria: (verdict, zones), or (None, []) when no zone
    applies at all (open). Raises `AccessUnreadable`.

    Its own anchor (no `Anchors`, or anchorGUID = itself): `validateZoneAccess` on its own zones, with its own
    owners and maintainers (validateUserForElement*, :1306-1620, :2540). An anchored member: each operation is
    checked on the ANCHOR's zones as Egeria's validateUserForAnchor* does (`zone_access.ANCHOR_OPERATIONS`), and
    where Egeria also asks it, READ on the member's own zones. No zone on either: open (Egeria :1205)."""
    from resource_explorer.zone_access import ANCHOR_OPERATIONS, READ_OPERATION, ZoneVerdict, zone_grants

    member = reader.element(guid)
    account = lambda: reader.account(user)  # noqa: E731
    if not member.anchor:
        if not member.zones:
            return None, []
        return zone_grants(user, member.zones, control_for=reader.control, account_for=account,
                           owners=member.owners, maintainers=member.maintainers, operations=ops), list(member.zones)
    anchor = reader.element(member.anchor)
    if not anchor.zones and not member.zones:
        return None, []
    last = ZoneVerdict(True)
    needs_read = False
    for op in ops:
        anchor_op, user_maintains, read_member = ANCHOR_OPERATIONS.get(op, (op, False, False))
        needs_read = needs_read or read_member
        last = zone_grants(user, anchor.zones, control_for=reader.control, account_for=account,
                           owners=anchor.owners, maintainers=[user] if user_maintains else anchor.maintainers,
                           operations=(anchor_op,))
        if not last.allowed:
            return last, list(anchor.zones)
    if needs_read:
        read = zone_grants(user, member.zones, control_for=reader.control, account_for=account,
                           owners=member.owners, maintainers=member.maintainers, operations=(READ_OPERATION,))
        if not read.allowed:
            return read, list(member.zones)
    return last, list(anchor.zones or member.zones)


def publish_operations(in_egeria: bool, *, attaches: bool = False) -> tuple[str, ...]:
    """Every operation a Publish write performs on an item (Brief Z round 2): CREATE for a new element or
    UPDATE_PROPERTIES for an existing one; CLASSIFY for the Ownership / requestedBy stamps; PUBLISH for the
    zone change of promotion; ATTACH when it links members or compositions (checked on the other ends too)."""
    from resource_explorer.zone_access import (
        ATTACH_OPERATION,
        CLASSIFY_OPERATION,
        CREATE_OPERATION,
        PUBLISH_OPERATION,
        UPDATE_OPERATION,
    )

    ops = [UPDATE_OPERATION if in_egeria else CREATE_OPERATION, CLASSIFY_OPERATION, PUBLISH_OPERATION]
    if attaches:
        ops.append(ATTACH_OPERATION)
    return tuple(ops)


#: What a write to a RESOURCE's own element performs (Brief Z2), by the name its entry points use:
#: `publish` writes it to Egeria (create or update, classify, rezone, attach its report and members);
#: `decision` records a choice in RE about it (the scope decisions, forgetting its links: an update);
#: `start_action` starts an Egeria engine action on it (a native survey: the action's ActionTarget link is an
#: attach on the element); `link` / `unlink` add or remove one link on it (a documentation source's
#: ExternalReference); `delete` removes it (behind the archive-cascade block).
RESOURCE_WRITES = ("publish", "decision", "start_action", "link", "unlink", "delete")


def resource_write_operations(write: str, in_egeria: bool) -> tuple[str, ...]:
    """The Egeria operations one resource-level write performs (`RESOURCE_WRITES`)."""
    from resource_explorer.zone_access import ATTACH_OPERATION, DELETE_OPERATION, DETACH_OPERATION

    if write == "publish":
        return publish_operations(in_egeria, attaches=True)
    if write == "decision":
        return VERDICT_OPERATIONS
    if write in ("start_action", "link"):
        return (ATTACH_OPERATION,)
    if write == "unlink":
        return (DETACH_OPERATION,)
    if write == "delete":
        return (DELETE_OPERATION,)
    raise ValueError(f"unknown resource write {write!r}; expected one of {', '.join(RESOURCE_WRITES)}")


def resource_access(registry: ProjectRegistry, entity_type: str, slug: str, write: str, *,
                    element_guid: str | None = None, reader=None) -> AccessDecision:
    """May the caller make this `write` on a resource's own element (the repository, database or file system
    asset)? Brief Z2: every web route and CLI command that writes a resource calls this, so they all answer with
    `curation_access` (the one decision). The A2A surface has no write (its tools are read-only). `element_guid` "" checks it as not in Egeria (a link RE
    is about to clear and re-create: the stale GUID would only read as unreadable)."""
    try:
        guid = element_guid if element_guid is not None else element_guid_for(registry, entity_type, slug, "")
    except _RegistryUnreadable:
        guid = None                   # curation_access reads it again and denies with the reason
    ops = resource_write_operations(write, in_egeria=guid is None or bool(guid))
    return curation_access(registry, entity_type, slug, "", operations=ops, reader=reader,
                           element_guid=element_guid)


def engine_action_cancel_access(registry: ProjectRegistry, entity_type: str, slug: str, engine_action_guid: str, *,
                                reader=None) -> AccessDecision:
    """May the caller cancel this engine action? Judged on the ENGINE ACTION element, the way Egeria judges it
    (EngineActionHandler.cancelEngineAction, egeria af400039c2 `generic-handlers/.../EngineActionHandler.java`
    :2431-2490): it reads the element first (`getEntityFromRepository` -> validateUserForElementRead,
    OpenMetadataAPIGenericHandler.java:179) and then updates it (`updateBeanInRepository` :2086 ->
    validateUserForElementDetailUpdate :2163, connector :1497-1522). So READ and UPDATE_PROPERTIES on that
    element's zones; an anchored element is judged on its anchor as `curation_access` already does. No other
    operation is performed: the cancel neither creates, classifies nor links. When the element carries no
    ZoneMembership, no zone rule applies and the decision's basis says so (`BASIS_NO_ZONES`)."""
    from resource_explorer.zone_access import READ_OPERATION, UPDATE_OPERATION

    return curation_access(registry, entity_type, slug, "", operations=(READ_OPERATION, UPDATE_OPERATION),
                           reader=reader, element_guid=engine_action_guid)


def require_resource_access(registry: ProjectRegistry, entity_type: str, slug: str, write: str, *,
                            element_guid: str | None = None, reader=None) -> None:
    """`resource_access`, raised as `CurationDenied` (the CLI and A2A form)."""
    d = resource_access(registry, entity_type, slug, write, element_guid=element_guid, reader=reader)
    if not d.allowed:
        raise CurationDenied(d.reason)


class WriteNotPermitted(RuntimeError):
    """A write refused at run time (Brief Z2 round 2), e.g. on an element adopted by qualifiedName. Its message
    is the item's words, "not permitted · <reason>". Deliberately NOT a PermissionError: the web maps those to
    401 "sign in", and this is a refusal of someone who is signed in (or of a run nobody asked for)."""


def zones_in_use(registry: ProjectRegistry, entity_type: str, slug: str, *, reader=None,
                 element_guid: str | None = None) -> list[str]:
    """The zones that govern a write to this resource, with nobody to judge (Brief Z2 round 2): its element's
    zones (an anchored member: its anchor's) when it is in Egeria, else the zones RE would create it in. `[]` =
    no zone in use (open, the owner's rule). Raises `AccessUnreadable` / `_RegistryUnreadable`."""
    from resource_explorer.zone_access import EgeriaAccessReader

    reader = reader or EgeriaAccessReader()
    guid = element_guid if element_guid is not None else element_guid_for(registry, entity_type, slug, "")
    if not guid:
        return configured_zones_for(registry, entity_type, slug)
    facts = reader.element(guid)
    if facts.anchor:
        return list(reader.element(facts.anchor).zones or facts.zones)
    return list(facts.zones)


NO_REQUESTER_WORDS = "no person asked for this run"


def person_write_refusal(registry: ProjectRegistry, entity_type: str, slug: str, write: str = "publish", *,
                         element_guid: str | None = None) -> str:
    """The run-time re-check at an automatic write (an analysis or Survey Definition run's auto-publish, a
    scheduled run): "" when it may proceed, else "not permitted · <reason>".

    When a PERSON asked for the run (a signed-in request, or a queued run whose `requested_by` names someone:
    run_queue sets the caller to them) the same `resource_access` decides. A run with NO requester (a schedule
    with no recorded creator, an unattributed queue row) would otherwise write on RE's service account's rights,
    which nobody can be judged by (Brief Z2 round 2): it proceeds only when no zone is in use for the resource
    (the owner's "if zones aren't being used then it is open"); with zones in use, or when that cannot be read,
    it is refused."""
    from resource_explorer.zone_access import AccessUnreadable

    who = _caller_identity()
    try:
        if who is None or not (who.user_id or "").strip():
            zones = zones_in_use(registry, entity_type, slug, element_guid=element_guid)
            if not zones:
                return ""
            return (f"not permitted · {NO_REQUESTER_WORDS}, and zone{'s' if len(zones) != 1 else ''} "
                    f"{', '.join(zones)} {'are' if len(zones) != 1 else 'is'} in use")
        d = resource_access(registry, entity_type, slug, write, element_guid=element_guid)
    except (AccessUnreadable, _RegistryUnreadable) as exc:
        return f"not permitted · could not check access in Egeria ({exc})"[:300]
    except Exception as exc:  # noqa: BLE001 - a failure is a refusal with its reason, never a pass
        return f"not permitted · could not check access ({type(exc).__name__}: {exc})"[:300]
    return "" if d.allowed else f"not permitted · {d.reason}"


def blueprint_attach_scopes(registry: ProjectRegistry, slug: str, key: str) -> list[str]:
    """The other ends a blueprint's write attaches: its member components (scope locators) and its child
    blueprints (`perspective::child`). From RE's own rows (the candidate finding and the slug->scope map)."""
    perspective, _, cluster = key.partition("::")
    detail = find_candidate_blueprint(registry, slug, perspective, cluster) or {}
    scopes_by_slug = slug_to_scope_map(registry, slug) if detail.get("members") else {}
    out = [scopes_by_slug[m] for m in detail.get("members") or [] if scopes_by_slug.get(m)]
    out += [f"{perspective}::{c}" for c in detail.get("children") or []]
    return list(dict.fromkeys(out))


def _reads_zones() -> bool:
    """Whether `curation_access` will reach an Egeria read for the current caller: not for an anonymous
    caller (denied first) nor a Portal curator/admin (allowed first). Only to skip a pointless prefetch; the
    decision itself is `curation_access`'s."""
    identity = _caller_identity()
    return bool(identity is not None and identity.auth_source != "anonymous" and identity.user_id
                and (identity.role or "").lower() not in CURATOR_ROLES)


def _guids_for(registry: ProjectRegistry, slug: str, scopes: list[str]) -> list[str]:
    """The Egeria elements of these scopes, for a prefetch. A scope RE cannot read is left out here:
    `curation_access` reads it again and denies with the reason."""
    out = []
    for scope in scopes:
        try:
            guid = element_guid_for(registry, "repo", slug, scope)
        except _RegistryUnreadable:
            continue
        if guid:
            out.append(guid)
    return out


def publish_item_access(registry: ProjectRegistry, slug: str, kind: str, key: str, *, reader=None,
                        element_guid: str | None = None) -> AccessDecision:
    """May the caller publish this item: every operation its write performs, and for a blueprint ATTACH on each
    member component and child blueprint it links (both ends, as `validateUserForElementAttach` checks).
    Used at the press AND by the run immediately before each write (`architecture_publish.run_publish`).

    The elements the check needs (the item's own, and a blueprint's members and children already in Egeria)
    are read together, once, before the decision (`EgeriaAccessReader.check`): concurrently on the press, on
    one client in the run. Nothing is read for an element not in Egeria yet, nor for a caller the decision
    settles before any read (anonymous, Portal curator/admin)."""
    from resource_explorer.zone_access import ATTACH_OPERATION, EgeriaAccessReader

    reader = reader or EgeriaAccessReader()
    guid = element_guid if element_guid is not None else None
    in_egeria = bool(guid) if guid is not None else bool(_safe_guid(registry, slug, key))
    reads = _reads_zones()
    others = blueprint_attach_scopes(registry, slug, key) if kind == "blueprint" and reads else None
    wanted: list[str] = []
    if reads:
        wanted = ([guid] if guid else _guids_for(registry, slug, [key])) + _guids_for(registry, slug, others or [])
    with reader.check(wanted):
        d = curation_access(registry, "repo", slug, key, reader=reader, element_guid=guid,
                            operations=publish_operations(in_egeria, attaches=kind == "blueprint"))
        if not d.allowed or kind != "blueprint" or d.basis == BASIS_PORTAL_ROLE:
            return d
        for other in (others if others is not None else blueprint_attach_scopes(registry, slug, key)):
            e = curation_access(registry, "repo", slug, other, reader=reader, operations=(ATTACH_OPERATION,))
            if not e.allowed:
                return AccessDecision(False, f"{other.rsplit('/', 1)[-1]}: {e.reason}", e.basis, e.zones)
    return d


def resolved_elements_access(registry: ProjectRegistry, slug: str, key: str, guids: list[str], *,
                             reader=None) -> AccessDecision:
    """The run's check on the elements a blueprint write RESOLVED (Brief Z round 2): the blueprint itself
    (`guids[0]`) for every operation its write performs, and every member, adopted root and child blueprint
    for ATTACH (the other end of each link). All of them are read together, fresh, for this check."""
    from resource_explorer.zone_access import ATTACH_OPERATION, EgeriaAccessReader

    reader = reader or EgeriaAccessReader()
    with reader.check(guids if _reads_zones() else ()):
        for i, guid in enumerate(guids):
            ops = publish_operations(True, attaches=True) if i == 0 else (ATTACH_OPERATION,)
            d = curation_access(registry, "repo", slug, key, reader=reader, element_guid=guid, operations=ops)
            if not d.allowed:
                return d if i == 0 else AccessDecision(False, f"member {guid[:8]}: {d.reason}", d.basis, d.zones)
    return AccessDecision(True, "", BASIS_ZONE_GRANTED)


def _safe_guid(registry: ProjectRegistry, slug: str, key: str) -> str:
    try:
        return element_guid_for(registry, "repo", slug, key)
    except _RegistryUnreadable:
        return ""          # curation_access reads it again and denies with the reason


def may_curate(registry: ProjectRegistry, entity_type: str, slug: str, scope_locator: str, *,
               reader=None, operations=None) -> tuple[bool, str]:
    """`(allowed, why_not)` from `curation_access`."""
    d = curation_access(registry, entity_type, slug, scope_locator, reader=reader, operations=operations)
    return d.allowed, d.reason


def require_curation_rights(registry: ProjectRegistry, entity_type: str, slug: str, scope_locator: str, *,
                            reader=None, operations=None) -> None:
    """`curation_access`, raised. The form every call site should use."""
    d = curation_access(registry, entity_type, slug, scope_locator, reader=reader, operations=operations)
    if not d.allowed:
        raise CurationDenied(d.reason)


def last_decided_by(registry: ProjectRegistry, entity_type: str, slug: str, scope_locator: str) -> str:
    """Who made the latest call on this element (the verdict's `decided_by`), "" when nobody recorded one.

    Attribution only. Until Brief Z (2026-10-10) this was `owner_of` and gated curation; that made an
    element the property of whoever touched it last, and two people's decisions on one Publish plan
    deadlocked each other. Access now follows Egeria's zones (`curation_access`).
    """
    try:
        verdicts = registry.get_component_verdicts(entity_type, slug) or {}
    except Exception:  # pragma: no cover - a registry failure is its own error
        return ""
    row = verdicts.get(scope_locator) or {}
    return str(row.get("decided_by") or "")


#: What the verdict row says when promotion left zones to Egeria (nothing configured). The two
#: halves are the whole claim: RE put the element in NO zone, and a read-back found none.
ZONES_LEFT_TO_EGERIA_WORDS = "accepted · zones left to Egeria · everyone visible"


def _zone_words(zones: list[str]) -> str:
    return ZONES_LEFT_TO_EGERIA_WORDS if not zones else f"accepted · zone {', '.join(zones)}"


def promote_to_publish_zones(guid: str) -> dict:
    """Move an accepted element out of RE's draft zone. The Egeria-visible effect of "accept".

    **Configured-only (project owner, 2026-10-07).** The zone an accepted element moves into is
    `egeria_identity.configured_publish_zones()` and nothing else: there is no default zone.

    * a zone IS configured: one `add_zone_membership` call replaces the draft zone with exactly
      the configured zone(s) (never briefly in both); the row reads "accepted · zone <name>".
    * NOTHING configured: RE writes no zone at all and CLEARS its own draft `ZoneMembership`
      (the documented clear call), so the element is readable by everyone Egeria lets read it;
      the row reads "accepted · zones left to Egeria · everyone visible". "Cleared" is said only
      after a read of the element's classifications found no `ZoneMembership` on it. Materialization
      stamps `[draft_zone()]` (or the private zones for a private investigation), never the
      private zone for a public resource, so accept has to clear it or the element stays in the
      draft zone the service identity cannot read from.

    Best-effort and reported: the verdict is already saved and real, exactly as materialization
    is, so a failed promotion is a line in the response rather than a rolled-back decision.
    """
    from resource_explorer.egeria_identity import (
        ZoneReadError,
        clear_zone_membership,
        configured_publish_zones,
        current_zones,
        draft_zones,
        private_zone,
        read_zones,
        set_zone_membership,
        zone_lockout_refusal,
    )

    zones = configured_publish_zones()
    if not guid:
        return {"status": "skipped", "reason": "no Egeria element to promote", "zones": zones,
                "words": "accepted · no Egeria element to promote"}

    if not zones:
        # Nothing configured: read FIRST, strictly. `[]` from this reader is "Egeria answered, no
        # ZoneMembership"; an unreadable answer raises and is reported, never read as "nothing to do".
        try:
            before = read_zones(guid)
        except ZoneReadError as exc:
            return {"status": "error", "guid": guid, "zones": [], "error": str(exc),
                    "words": f"accepted · zones not changed · {exc}"}
        if private_zone() in before:
            return _private_skip(guid, before)
        if not before:
            return {"status": "already_unzoned", "guid": guid, "zones": [], "from_zones": [],
                    "words": ZONES_LEFT_TO_EGERIA_WORDS}
        if before != draft_zones():
            # Not RE's own stamp: the element may be one RE adopted (found by qualifiedName) that
            # someone else placed in a zone. Clear ONLY RE's draft zone; never strip another zone.
            return {"status": "left_as_is", "guid": guid, "zones": before, "from_zones": before,
                    "words": f"zones left as they are · {', '.join(before)} · not RE's draft zone"}
        if not clear_zone_membership(guid):
            return {"status": "error", "guid": guid, "zones": [], "from_zones": before,
                    "error": "Egeria did not accept clearing the ZoneMembership",
                    "words": "accepted · zones not changed · Egeria did not accept clearing the draft zone"}
        try:
            after = read_zones(guid)
        except ZoneReadError as exc:
            return {"status": "error", "guid": guid, "zones": [], "from_zones": before,
                    "error": str(exc), "words": f"accepted · could not confirm the zone was cleared · {exc}"}
        if after:
            return {"status": "error", "guid": guid, "zones": after, "from_zones": before,
                    "error": f"the zones read back as {after}, not cleared",
                    "words": f"accepted · still in zone {', '.join(after)} · the clear did not take"}
        return {"status": "promoted", "guid": guid, "zones": [], "from_zones": before,
                "words": ZONES_LEFT_TO_EGERIA_WORDS}

    # **A no-op promotion is an error in Egeria, not a nothing.** Its security connector refuses a
    # zone change whose before and after are equal (observed live 2026-09-04,
    # OMAG-SERVER-SECURITY-403-005 ... from [egeria-runtime] to [egeria-runtime]). Re-accepting an
    # already-accepted element is an ordinary thing to do, so checking first is not an
    # optimisation: without it, "this was already promoted" reads as a permissions failure.
    already = current_zones(guid)
    if already and set(already) == set(zones):
        return {"status": "already_promoted", "guid": guid, "zones": zones, "words": _zone_words(zones)}

    # **Never promote a private element into the publish zones.** Accepting a finding is a
    # curation decision about quality; it is not a decision to make somebody's personal
    # investigation public. Keyed on the zone RE itself applies, which is what Egeria enforces.
    if private_zone() in (already or []):
        return _private_skip(guid, already)

    # **Never remove a zone RE did not stamp.** `add_zone_membership` REPLACES the classification, so
    # writing just the configured zones would drop a foreign zone someone put on an element RE adopted.
    # The write is the configured zones UNIONED with the foreign ones (RE's draft zone goes; nothing
    # else does). Chosen over "leave it and say so" because accept still has to make the element
    # visible in the configured zone, and the union costs the foreign zone nothing.
    # **Never promote into a zone that grants nobody** (Egeria e9fd817f4c: a zone with no control, or with
    # neither READ nor DEFAULT, grants no one; an unreadable control blocks too). Checked before the zone write:
    # an accepted element moved there could not be read or moved back, by RE's daemon or anyone else. After the
    # two no-write answers above, so an already-promoted element still reads as already promoted.
    refusal = zone_lockout_refusal(zones)
    if refusal:
        return {"status": "blocked", "guid": guid, "zones": zones, "from_zones": already, "error": refusal,
                "words": f"accepted · zones not changed · {refusal}"}

    foreign = [z for z in (already or []) if z not in {*draft_zones(), private_zone()}]
    zones = list(dict.fromkeys([*foreign, *zones]))
    ok = set_zone_membership(guid, zones)
    if not ok:
        return {"status": "error", "guid": guid, "zones": zones, "from_zones": already,
                "words": "accepted · zones not changed · Egeria did not accept the zone change",
                "error": "Egeria did not accept the ZoneMembership change"}
    # The success words are said only after the zones are READ BACK (they are stored as proof).
    try:
        after = read_zones(guid)
    except ZoneReadError as exc:
        return {"status": "error", "guid": guid, "zones": zones, "from_zones": already, "error": str(exc),
                "words": f"accepted · could not confirm the zone was set · {exc}"}
    if set(after) != set(zones):
        return {"status": "error", "guid": guid, "zones": after, "from_zones": already,
                "error": f"the zones read back as {after}, not {zones}",
                "words": f"accepted · zone not set · read back as {', '.join(after) or 'no zone'}"}
    return {"status": "promoted", "guid": guid, "zones": zones, "from_zones": already, "words": _zone_words(zones)}


#: Proof rows for a promotion (`catalogue_commit_proofs`, no new table). A row is written AFTER the
#: promotion read the element's zones, and carries the words the verdict row shows, so the screen
#: reads a row and never re-derives the sentence from the branch the code took.
P_PROMOTION = "promotion"
NODE_PROMOTION_COMPONENT = "component_promotion"
NODE_PROMOTION_BLUEPRINT = "blueprint_promotion"


def record_promotion(registry: ProjectRegistry, slug: str, scope: str, node_kind: str,
                     promotion: dict | None, recorded_by: str = "") -> None:
    """Append the proof row for one promotion (`scope` is the component's scope_locator or the
    blueprint's `<perspective>::<cluster>` key). Nothing is recorded for a promotion that was
    never attempted."""
    if not promotion:
        return
    registry.append_catalogue_commit_proof(
        slug, proof=P_PROMOTION, node_kind=node_kind, table_name=scope,
        element_guid=promotion.get("guid", ""), recorded_by=recorded_by,
        detail={"status": promotion.get("status", ""), "words": promotion.get("words", ""),
                "zones": promotion.get("zones", []), "from_zones": promotion.get("from_zones", []),
                "error": promotion.get("error", "")})


def promotion_by_scope(registry: ProjectRegistry, slug: str, node_kind: str) -> dict[str, dict]:
    """{scope: latest promotion row} for a resource: `{words, status, read_at}`. The words are what
    the verdict row shows; absent means no promotion has been recorded for that scope."""
    out: dict[str, dict] = {}
    for p in registry.list_catalogue_commit_proofs(slug):
        if p["proof"] == P_PROMOTION and p["node_kind"] == node_kind:
            d = p.get("detail") or {}
            out[p["table_name"]] = {"words": d.get("words", ""), "status": d.get("status", ""),
                                    "read_at": p["read_at"]}
    return out


def _private_skip(guid: str, in_zones: list[str]) -> dict:
    return {
        "status": "skipped",
        "reason": ("this element belongs to a private investigation; accepting a "
                   "finding does not make it public. Reclassify the investigation "
                   "to share it."),
        "guid": guid,
        "zones": in_zones,
        "words": "accepted · stays private · reclassify the investigation to share it",
    }


def materialize_component_if_accepted(registry: ProjectRegistry, entity_type: str, slug: str,
                              scope_locator: str, verdict: str) -> dict | None:
    """docs/architecture-recovery-report-then-curate.md — acting on the
    decision, not just recording it. Only 'accepted' triggers a real Egeria
    write (see materializer.py's module docstring for the full scope). Only
    wired for entity_type='repo' today, the only kind architecture_recovery
    runs against — a database/filesystem proposal kind would need its own
    materializer, not this one reused past what it was built for.

    Returns None when nothing was attempted (wrong verdict, wrong entity
    type, or the component's own finding row is gone — e.g. withdrawn since
    the review surface last loaded). Never raises: a materialization
    failure is reported in the field the caller merges into the response,
    the same non-fatal-but-visible shape as EgeriaPublisher.publish()'s
    annotation_types_warning, since the verdict itself is already saved and
    real regardless of whether Egeria was reachable just now.
    """
    if verdict != "accepted" or entity_type != "repo":
        return None
    rows = [r for r in registry.query_findings_all_runs(slug, "architecture_recovery", scope_locator)
            if r["check_name"] == "component"]
    if not rows:
        return {"status": "error", "error": "no component finding at this scope to materialize"}
    latest = max(rows, key=lambda r: r["surveyed_at"])
    import json as _json
    detail = _json.loads(latest.get("detail_json") or "{}") if latest.get("detail_json") else {}
    from resource_explorer.surveyors.arch_recovery.materializer import (
        ComponentMaterializer,
        MaterializationError,
    )
    # What the component represents is in its displayName (never its qualifiedName), derived from the
    # evidence class. A repository-derived component that maps to an element Egeria's content pack defines
    # ADOPTS it: no second component, no rename (the pack's name is the pack's).
    from resource_explorer.blueprint_shape import content_pack_name_for_image, display_name, represents_kind
    pack_name = content_pack_name_for_image(detail.get("image") or "")
    refused = ""
    if pack_name:
        from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer
        pack = BlueprintMaterializer(registry=registry).find_content_pack_component(pack_name)
        if isinstance(pack, dict) and pack.get("could_not_check"):
            # Could not tell whether the pack defines it: creating RE's own component would put a second one
            # beside the pack's under another qualifiedName. Nothing is created.
            return {"status": "error", "error": pack["could_not_check"]}
        refused = pack.get("refused") if isinstance(pack, dict) else ""
        if refused:
            pack = None
        if pack:
            registry.record_materialized_component(
                entity_type, slug, scope_locator, pack["qualified_name"], pack["guid"])
            return {"status": "adopted_content_pack", "guid": pack["guid"],
                    "qualified_name": pack["qualified_name"], "matched_by_image": detail["image"],
                    "words": f"{pack_name} · adopted from the content pack · matched by image {detail['image']}"}
    try:
        made = ComponentMaterializer(registry=registry).materialize(
            entity_type, slug, scope_locator,
            name=display_name(detail.get("name", scope_locator), represents_kind(detail)),
            component_type=detail.get("type") or "",
            perspective=detail.get("perspective", ""),
            confidence=latest.get("confidence", 0),
        )
        if refused and isinstance(made, dict):
            made["content_pack_refused"] = refused       # said, not silently skipped
        return made
    except MaterializationError as exc:
        return {"status": "error", "error": str(exc)}


def slug_to_scope_map(registry: ProjectRegistry, slug: str) -> dict[str, str]:
    """component slug -> scope_locator, for every currently-live
    architecture_recovery component finding. THE fix for the plan's own named
    identity-mismatch trap: clustering keys a blueprint's members by component
    slug; verdicts and materialization are keyed by scope_locator. Looking a
    slug up directly in get_materialized_component()/get_component_verdicts()
    without going through this map first silently finds nothing for every
    member.

    Served from the shared recovery snapshot (one bulk read, cached on the
    data's fingerprint; `repo_survey_definition_adapter._blueprint_slug_to_
    scope_map`, the same walk). This used to issue one
    `query_findings_all_runs` per scope: ~1,000 round trips on egeria_git for
    every blueprint a Publish press or run checked (2026-10-10). The snapshot
    returns, per scope, exactly the rows and order the per-scope query does
    (`ProjectRegistry.query_findings_all_runs_by_scope`), so the latest row,
    and hence the map, is the same."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import _blueprint_slug_to_scope_map

    return dict(_blueprint_slug_to_scope_map(registry, slug))


def find_candidate_blueprint(registry: ProjectRegistry, slug: str,
                              perspective: str, cluster_name: str) -> dict | None:
    """The latest candidate_blueprint finding for (perspective, cluster_name),
    or None if it's gone (e.g. re-clustered since the review surface last
    loaded). clustering.py's findings all share scope_locator="" — every
    cluster for a repo is disambiguated by label/detail.name within that one
    scope, per the plan's own Context section."""
    import json as _json

    from resource_explorer.surveyors.arch_recovery.persist import BLUEPRINT_KIND
    rows = [r for r in registry.query_findings(slug, BLUEPRINT_KIND, "")
            if r["check_name"] == "candidate_blueprint"]
    matches = []
    for r in rows:
        detail = _json.loads(r.get("detail_json") or "{}") if r.get("detail_json") else {}
        if detail.get("perspective") == perspective and detail.get("name") == cluster_name:
            matches.append((r, detail))
    if not matches:
        return None
    r, detail = max(matches, key=lambda pair: pair[0]["surveyed_at"])
    return detail


def materialize_blueprint_if_accepted(registry: ProjectRegistry, entity_type: str, slug: str,
                                       perspective: str, cluster_name: str, verdict: str,
                                       shape: str = "", identifier: str = "",
                                       check_resolved=None) -> dict | None:
    """Same non-fatal-but-visible shape as materialize_component_if_accepted above —
    the verdict itself is already saved and real regardless of whether this
    succeeds. Only 'accepted' triggers a write; only entity_type='repo' is
    wired (architecture_recovery/clustering's only kind today).

    Partial progress is real progress (Decision 2/step 4 of the plan): an
    unmaterialized member or child is reported, not treated as a reason to
    enqueue nothing.

    **The blueprint is written in ONE of two shapes, never a mix** (project owner, 2026-10-08;
    `blueprint_shape.py`, DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md 6a):

    * container (the default, when the cluster's root is a real component): the root is the ONLY blueprint
      member and each other member is its sub-component by `SolutionComposition`; Egeria draws the
      encapsulation.
    * contents (the root is merely a grouping): the children are the members, no root element.

    `shape` ("container" | "contents" | "") is a person's flip before the write; "" takes the default.
    The plan, with the words that name the shape and why, comes back as `result["shape"]` and is stored
    as a `shape` proof row. Egeria draws the diagram: RE writes only components, compositions and
    memberships.
    """
    if verdict != "accepted" or entity_type != "repo":
        return None
    cluster = find_candidate_blueprint(registry, slug, perspective, cluster_name)
    if cluster is None:
        return {"status": "error",
               "error": "no candidate_blueprint finding for this (perspective, cluster_name) "
                        "to materialize"}

    from dataclasses import replace

    from resource_explorer.blueprint_shape import (
        CONTAINER,
        SHAPES,
        component_nodes,
        distinct_name,
        find_root,
        plan_shape,
    )
    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import (
        BlueprintIdentifierNeeded,
        BlueprintMaterializationError,
        BlueprintMaterializer,
    )
    from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer
    materializer = BlueprintMaterializer(registry=registry)
    from resource_explorer.blueprint_kinds import (
        blueprint_display_name,
        repo_label,
    )
    from resource_explorer.surveyors.repo_survey_definition_adapter import _candidate_blueprints_results

    requested = shape if shape in SHAPES else ""
    nodes_by_slug = component_nodes(registry, slug)
    member_slugs = cluster.get("members") or []
    # A member the recovery has no component finding for still counts (by its slug); it just carries no
    # evidence class, so it can never be mistaken for a real-component root.
    from resource_explorer.blueprint_shape import Node
    known = [nodes_by_slug.get(s) or Node(slug=s, name=s) for s in member_slugs]

    project = registry.get(slug)
    roots = [b for b in _candidate_blueprints_results(registry, slug)
             if b["perspective"] == perspective and not b.get("parent")]
    sole_root = len(roots) == 1 and roots[0]["cluster_name"] == cluster_name
    # A blueprint's name is the KIND and the repository and never equals a member's name (6a rule 1).
    # ...and says what its members represent when they are all of one kind (container definitions, code
    # modules): displayName only, the qualifiedName never carries it.
    from resource_explorer.blueprint_shape import blueprint_suffix_name
    from resource_explorer.blueprint_shape import display_name as suffixed_name
    display_name = distinct_name(
        blueprint_suffix_name(
            blueprint_display_name(repo_label(slug, getattr(project, "display_name", "") or ""), perspective,
                                   cluster.get("name", cluster_name), sole_root=sole_root,
                                   identifier=identifier.strip()),
            [n.kind for n in known]),
        [n.name for n in known] + [suffixed_name(n.name, n.kind) for n in known])
    try:
        result = materializer.materialize_blueprint_element(
            entity_type, slug, perspective, cluster_name,
            # The KIND is in the name (owner, 2026-10-07): "Egeria Deployment Blueprint".
            display_name=display_name,
            oversized=bool(cluster.get("oversized")),
            # Identity is kind + repository (architect's ruling, 2026-10-08): the root cluster's name is not in
            # it. A person's `identifier` is needed only for a second blueprint of the kind.
            identifier=identifier,
            live_clusters={b["cluster_name"] for b in _candidate_blueprints_results(registry, slug)
                           if b["perspective"] == perspective},
            # A blueprint a person deleted in Egeria must not be trusted from RE's cache.
            verify_cached=True,
        )
    except BlueprintIdentifierNeeded as exc:
        return {"status": "error", "error": str(exc), "needs_identifier": True}
    except BlueprintMaterializationError as exc:
        return {"status": "error", "error": str(exc)}

    blueprint_guid = result["guid"]
    slug_to_scope = {s: n.scope for s, n in nodes_by_slug.items()}
    member_guids, unmaterialized_members = materializer.resolve_member_guids(
        registry, entity_type, slug, member_slugs, slug_to_scope,
    )
    # Components that exist in Egeria but this registry never recorded are ADOPTED by qualifiedName.
    findable = [s for s in unmaterialized_members if s in slug_to_scope]
    if findable:
        adopted = materializer.adopt_unmaterialized_members(
            registry, entity_type, slug, findable, slug_to_scope)
        member_guids = {**member_guids, **adopted}
        unmaterialized_members = [s for s in unmaterialized_members if s not in adopted]
        skipped = getattr(materializer, "adoption_skipped", None)
        adoption_skipped = dict(skipped) if isinstance(skipped, dict) else {}
    else:
        adoption_skipped = {}
    child_guids, unmaterialized_children = materializer.resolve_child_blueprint_guids(
        registry, entity_type, slug, perspective, cluster.get("children") or [], verify=True,
    )
    # A child whose cached GUID Egeria no longer holds (the wipe) is reported, never linked, never recreated here.
    children_gone = dict(getattr(materializer, "child_blueprints_gone", None) or {}) \
        if isinstance(getattr(materializer, "child_blueprints_gone", None), dict) else {}
    children_unreadable = dict(getattr(materializer, "child_blueprints_unreadable", None) or {}) \
        if isinstance(getattr(materializer, "child_blueprints_unreadable", None), dict) else {}

    # The root may be an element Egeria's content pack already defines: adopt it rather than leave the
    # container unwritten, and let it count as a real component (the one test, 6a).
    qn_override: dict[str, str] = {}
    adopted_words: list[str] = []
    content_pack_refused: list[str] = []
    nodes = [replace(n, guid=member_guids.get(n.slug, "")) for n in known]
    root = find_root(cluster.get("name", cluster_name), nodes, cluster.get("composed_into", ""))
    if root is not None and not root.guid and not root.structural:
        pack = materializer.find_content_pack_component(root.name)
        if isinstance(pack, dict) and (pack.get("refused") or pack.get("could_not_check")):
            # Ambiguous, or the search could not be made: nothing adopted, and the words are shown.
            content_pack_refused.append(pack.get("refused") or pack["could_not_check"])
            pack = None
        if pack:
            qn_override[root.slug] = pack["qualified_name"]
            matched = f"image {root.image}" if root.image else "name"
            adopted_words.append(f"{pack.get('name') or root.name} · adopted from the content pack · matched by {matched}")
            nodes = [replace(n, guid=pack["guid"], content_pack=True) if n.slug == root.slug else n
                     for n in nodes]
            unmaterialized_members = [s for s in unmaterialized_members if s != root.slug]
    plan = plan_shape(cluster.get("name", cluster_name), nodes, requested=requested,
                      composed_into=cluster.get("composed_into", ""))

    def qn_of(node) -> str:
        return qn_override.get(node.slug) or ComponentMaterializer.qualified_name_for(
            entity_type, slug, node.scope)

    scope_key = f"{perspective}::{cluster_name}"
    if check_resolved is not None:
        # Brief Z round 2: every element this write resolved (the blueprint, cached or adopted members, an
        # adopted content-pack root, child blueprints) is checked before the first link or membership is
        # written. A refusal writes nothing further.
        resolved = [blueprint_guid, *[n.guid for n in nodes if n.guid], *child_guids.values()]
        why = check_resolved(list(dict.fromkeys(g for g in resolved if g)))
        if why:
            return {"status": "not_permitted", "guid": blueprint_guid, "error": why}
    member_guid_list: list[str] = []
    composition_results: list[dict] = []
    composition_error = ""
    if plan.shape == CONTAINER:
        # Only the container is a member. With no element for it there is no membership at all: falling
        # back to the children would silently be the other shape.
        if plan.root.guid:
            member_guid_list = [plan.root.guid]
            children = [(c.guid, qn_of(c)) for _, c in plan.compositions if c.guid]
            try:
                composition_results = materializer.link_sub_components(
                    plan.root.guid, qn_of(plan.root), children)
                if not isinstance(composition_results, list):
                    raise TypeError("link_sub_components returned no rows")
            except Exception as exc:
                # Anything unexpected (a Pyegeria exception, a KeyError from a changed answer shape) must
                # not strand a blueprint with no memberships and no proofs: say so, and carry on.
                composition_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.warning("composition step failed for %s: %s", scope_key, composition_error)
                composition_results = [
                    {"key": f"SolutionComposition::{qn_of(plan.root)}::{cqn}", "container_guid": plan.root.guid,
                     "child_guid": cg, "status": "error", "read_back": False, "error": composition_error}
                    for cg, cqn in children]
        elif plan.root.slug not in unmaterialized_members:
            unmaterialized_members = [*unmaterialized_members, plan.root.slug]
    else:
        member_guid_list = [n.guid for n in plan.members if n.guid]

    # Direct members left by an EARLIER run (the old blueprint): reported, never removed.
    extra_members: list[str] = []
    extra_read = False
    existing = materializer.blueprint_member_guids(blueprint_guid)
    if isinstance(existing, (set, frozenset)):
        extra_read = True
        if plan.shape == CONTAINER:
            extra_members = sorted(existing & {c.guid for _, c in plan.compositions if c.guid})
        elif plan.root is not None and plan.root.guid in existing:
            extra_members = [plan.root.guid]

    from resource_explorer.egeria_outbox import enqueue_blueprint_members
    all_member_guids = member_guid_list + list(child_guids.values())
    row_ids = enqueue_blueprint_members(registry, entity_type, slug, blueprint_guid, all_member_guids)

    written = plan.shape != CONTAINER or bool(plan.root and plan.root.guid)
    _record_shape_proofs(registry, slug, scope_key, blueprint_guid, plan, composition_results,
                         written=written, extra_members=extra_members)

    result["shape"] = plan.to_dict()
    result["compositions"] = composition_results
    result["extra_members"] = extra_members
    result["extra_members_read"] = extra_read
    if extra_members:
        n = len(extra_members)
        result["extra_members_words"] = (
            f"{n} {'child is' if n == 1 else 'children are'} also direct member{'' if n == 1 else 's'} "
            f"from an earlier run: a steward can detach {'it' if n == 1 else 'them'} in Egeria Explorer")
    if adoption_skipped:
        result["adoption_skipped"] = adoption_skipped
    if content_pack_refused:
        result["content_pack_refused"] = content_pack_refused
    if adopted_words:
        result["adopted_from_content_pack"] = adopted_words
    if composition_error:
        result["composition_error"] = composition_error
    result["enqueued_membership_rows"] = len(row_ids)
    # What this write actually handed over: the members and linked child blueprints it enqueued (an adopted
    # content-pack root included) and the children of every composition Egeria shows. A child that was gone or
    # unreadable is in none of these.
    composed = {c["child_guid"] for c in composition_results if c.get("status") in ("linked", "already_present")}
    result["attached_guids"] = sorted(set(all_member_guids) | composed)
    # Kept apart for the Publish plan: a direct member and a composed sub-component are different handoffs, so
    # a flip from container to contents must still attach the sub-components as members.
    result["attached_members"] = sorted(set(all_member_guids))
    result["attached_composed"] = sorted(composed)
    if unmaterialized_members:
        result["unmaterialized_members"] = unmaterialized_members
    if unmaterialized_children:
        result["unmaterialized_children"] = unmaterialized_children
    if children_gone:
        result["children_gone"] = children_gone            # {name: the GUID Egeria no longer holds}
    if children_unreadable:
        result["children_unreadable"] = children_unreadable
    unfinished = [c for c in composition_results if c["status"] in ("unconfirmed", "error", "unread")]
    unproven = result.get("status") == "adopted_unproven"
    if unmaterialized_members or unmaterialized_children or composition_error or unfinished:
        result["status"] = "partial"
    if unproven:                       # the re-key proof row could not be written: the pane CAN show it
        result["adopted_unproven"] = True
    return result


P_SHAPE = "shape"
P_COMPOSITION = "composition"
NODE_BLUEPRINT_SHAPE = "blueprint_shape"


def _record_shape_proofs(registry: ProjectRegistry, slug: str, scope_key: str, blueprint_guid: str,
                         plan, compositions: list[dict], *, written: bool = True,
                         extra_members: list[str] | None = None) -> None:
    """Proof rows (`catalogue_commit_proofs`, no new table): one for the shape that was chosen and why,
    and one per composition. Each composition row says whether a read by GUID really backed it
    (`read_back`), never a blanket True. The shape row names the shape as written: a container whose root
    has no element says so instead of claiming the shape. A failed proof write never undoes the Egeria
    write; it is logged."""
    shape_said = plan.shape if written else f"{plan.shape} (not written: root has no GUID)"
    try:
        registry.append_catalogue_commit_proof(
            slug, proof=P_SHAPE, node_kind=NODE_BLUEPRINT_SHAPE, table_name=scope_key,
            element_guid=blueprint_guid,
            detail={"shape": shape_said, "written": written, "default_shape": plan.default_shape,
                    "why": plan.why, "words": plan.words, "flipped": plan.flipped,
                    "flip_refused": plan.flip_refused, "root": plan.root.name if plan.root else "",
                    "extra_members": list(extra_members or [])})
        for c in compositions:
            registry.append_catalogue_commit_proof(
                slug, proof=P_COMPOSITION, node_kind=NODE_BLUEPRINT_SHAPE, table_name=scope_key,
                element_guid=c["container_guid"], target_guid=c["child_guid"], qualified_name=c["key"],
                detail={"status": c["status"], "read_back": bool(c.get("read_back")),
                        "error": c.get("error", ""), "note": c.get("note", "")})
    except Exception as exc:                      # the Egeria write stands; the proof is what failed
        log.warning("could not record the shape proofs for %s: %s", scope_key, exc)
