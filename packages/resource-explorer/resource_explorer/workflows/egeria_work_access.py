"""Zone access for the Egeria writes of investigations, work lists and RFA ToDos (Brief Z3, 2026-10-11).

Brief Z2 put every resource write route on `workflows.curate.resource_access` -> `curation_access`. These writes
are not to a resource's own asset: they create or change an investigation's `Project`, its working-set `Folio`,
a work list's `WorkingSet` collection, the `CollectionMembership` of each member, and an RFA's `ToDo`. So each
write is named here as the Egeria ELEMENTS it touches and the operation Egeria checks on each, and every one is
decided by the SAME `curation_access` (the rules are Z2's and unchanged: anonymous denied, a Portal
curator/admin overrides, no zone in use is open, zones in use emulate Egeria, unreadable denies with the reason;
a no-requester write proceeds only when no zone is in use).

The operations, from Egeria's `OpenMetadataAccessSecurityConnector` (egeria af400039c2, cited in
`zone_access.py`):

* a new element: CREATE on the zones it is created in (`validateUserForElementCreate` :1306-1330; the creator
  is its maintainer). An element created ANCHORED to another (the Folio under its Project) is judged on the
  anchor (`validateUserForAnchorMemberCreate` :1965-1990), which `curation_access(anchor_guid=)` emulates.
* a property update (an investigation's name pushed to its Project; a ToDo's status): UPDATE_PROPERTIES
  (`validateUserForElementDetailUpdate` :1497-1522).
* a link (ResourceList Project->Folio, CollectionMembership collection->member): ATTACH on BOTH ends
  (`validateUserForElementAttach` :1582-1620); an anchored end is judged on its anchor plus READ on itself
  (`validateUserForAnchorAttach` :2100-2145).
* a classification (the Project kind, the Investigation marker): CLASSIFY (`validateUserForElementClassify`
  :1840-1856); removing one: DECLASSIFY (`validateUserForElementDeclassify` :1930-1948); a zone change: PUBLISH
  (:1816-1822, :1911-1917).

**The zones a new element is created in are the ones RE writes.** RE stamps no zone on a Project, Folio,
WorkingSet or ToDo except a private investigation's own private zones (`egeria_investigation_publisher.promote`
step 1a). So a new one is judged on exactly those, and with none it is "zones not in use" (open). Egeria itself
may then put an element written with no zone in the server's `defaultZones`, which RE cannot read; that is the
documented gap (Brief Z's "RE is stricter than Egeria on CREATE" runs the other way here).

Nothing here imports FastAPI (`workflows/__init__`): a route turns a refusal into a 403, the run-time re-checks
turn it into a row's words "not permitted · <reason>".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

NOT_PERMITTED = "not permitted"


@dataclass(frozen=True)
class ElementWrite:
    """One Egeria element a write touches, and every operation Egeria checks on it for that write.

    `guid` "" is an element this write CREATES: judged on the anchor it is created under (`anchor_guid`), else on
    `creation_zones` (the zones RE writes on it; () = none). `label` names a secondary end in a refusal
    ("the working set: zone x does not grant ..."); the primary element of a write has none."""

    guid: str
    operations: tuple[str, ...]
    label: str = ""
    anchor_guid: str = ""
    creation_zones: tuple[str, ...] = ()
    #: Round 2: an EXISTING element judged on the zones an earlier write of the same press moves it to (None: its
    #: own). `()` with `foreign_after`: RE's own zones (`foreign_after`) are cleared and the rest stay.
    zones_after: Optional[tuple[str, ...]] = None
    foreign_after: tuple[str, ...] = ()


#: The words a PRIVATE investigation's write carries while nobody who may make it is here: RE's service account
#: cannot see the investigation's elements (Egeria refuses it them by design), so it neither checks nor writes
#: them, and the row or the press waits for the owner (2026-10-13, the owner's privacy proof).
def owner_waiting_words(owner: str) -> str:
    return f"waiting for the owner, {owner}, to retry: the service account cannot see a private investigation"


def investigation_unscoped(registry, slug: str) -> Optional[dict]:
    """The investigation row whoever is asking (the shared identity's view): deciding whether to PROTECT must see
    a private investigation the current caller (or the daemon) cannot, as `ProjectRegistry.private_owner_for_entity`
    does. None only when there is no such row.

    Fails CLOSED (review of 1841e4a8): a row RE cannot read raises `zone_access.AccessUnreadable`. Reading it as
    "no investigation" would take a private one for shared and check it as the service account."""
    from resource_explorer.registry import SHARED_USER_ID
    from resource_explorer.zone_access import AccessUnreadable

    try:
        try:
            return registry.get_investigation(slug, user_id=SHARED_USER_ID)
        except TypeError:                 # a registry-shaped test double without the keyword
            return registry.get_investigation(slug)
    except Exception as exc:  # unreadable is never "shared"
        raise AccessUnreadable(f"the investigation {slug}: {type(exc).__name__}: {exc}"[:300]) from exc


def private_owner(inv: Optional[dict]) -> str:
    """The owner a private investigation's Egeria elements are zoned to, or "" (shared, ownerless or unknown)."""
    from resource_explorer.surveyors.egeria_investigation_publisher import is_private_investigation

    if not inv or not is_private_investigation(inv):
        return ""
    return (inv.get("created_by") or "").strip()


def owner_identity(inv: Optional[dict], identity=None):
    """Who an access check reads a PRIVATE investigation's elements as: the signed-in owner, as a Caller
    identity (`egeria_clients.Caller()`, or `identity` when the caller hands one in, as the outbox drain does),
    or None: a shared investigation, or nobody who IS its owner is signed in (the daemon then reads as before).
    Captured by the thread that holds the caller and passed on explicitly, never re-read on a pool thread."""
    owner = private_owner(inv)
    if not owner:
        return None
    if identity is None:
        from resource_explorer.egeria_clients import Caller

        try:
            identity = Caller()
        except PermissionError:           # no caller, or an expired sign-in
            return None
    if getattr(identity, "kind", "") != "caller" or (getattr(identity, "user_id", "") or "") != owner:
        return None
    return identity


def investigation_reader(registry, slug: str, *, identity=None, **kw):
    """An `EgeriaAccessReader` for one investigation's checks: element reads as its signed-in owner when it is
    private (`owner_identity`), else as RE's daemon, as every other check reads."""
    from resource_explorer.zone_access import EgeriaAccessReader

    return EgeriaAccessReader(read_as=owner_identity(investigation_unscoped(registry, slug), identity), **kw)


def _curate():
    from resource_explorer.workflows import curate

    return curate


def writes_access(registry, writes: Iterable[ElementWrite], *, entity_type: str = "", slug: str = "",
                  reader=None):
    """May the caller make EVERY write in `writes`: one `curation_access` per element, first refusal wins.
    Allowed when there is nothing to write to Egeria. The existing elements are read together once
    (`EgeriaAccessReader.check`), and nothing is read for a caller the decision settles first."""
    from resource_explorer.zone_access import EgeriaAccessReader

    wc = _curate()
    writes = list(writes)
    last = wc.AccessDecision(True, "", wc.BASIS_NO_ZONES)
    if not writes:
        return last
    if reader is None:
        # An investigation's own elements (its Project, its Folio) are read as its owner when it is private.
        if entity_type == "investigation" and slug:
            from resource_explorer.zone_access import AccessUnreadable

            try:
                reader = investigation_reader(registry, slug)
            except AccessUnreadable as exc:          # could not tell whether it is private: denied, its words
                return wc.AccessDecision(False, f"could not check access in Egeria ({exc})", wc.BASIS_UNREADABLE)
        else:
            reader = EgeriaAccessReader()
    wanted = [g for w in writes for g in (w.guid, w.anchor_guid) if g] if wc._reads_zones() else []
    with reader.check(wanted):
        for w in writes:
            d = wc.curation_access(registry, entity_type, slug, "", operations=w.operations, reader=reader,
                                   element_guid=w.guid, anchor_guid=w.anchor_guid,
                                   creation_zones=None if (w.guid or w.anchor_guid) else list(w.creation_zones),
                                   **({"zones_after": list(w.zones_after), "foreign_after": tuple(w.foreign_after)}
                                      if w.guid and w.zones_after is not None else {}))
            if not d.allowed:
                if w.label and d.basis != wc.BASIS_ANONYMOUS:
                    return wc.AccessDecision(False, f"{w.label}: {d.reason}", d.basis, d.zones)
                return d
            last = d
    return last


def require_writes(registry, writes: Iterable[ElementWrite], *, entity_type: str = "", slug: str = "",
                   reader=None) -> None:
    """`writes_access`, raised as `workflows.curate.CurationDenied` (its reason)."""
    wc = _curate()
    d = writes_access(registry, writes, entity_type=entity_type, slug=slug, reader=reader)
    if not d.allowed:
        raise wc.CurationDenied(d.reason)


def _zones_in_use(writes: list[ElementWrite], reader) -> list[str]:
    """Every zone that governs these writes, with nobody to judge: an existing element's own zones and its
    anchor's, a new anchored element's anchor's, a new element's creation zones. Raises `AccessUnreadable`."""
    out: list[str] = []
    for w in writes:
        if w.guid:
            facts = reader.element(w.guid)
            zones = [*facts.zones, *(reader.element(facts.anchor).zones if facts.anchor else [])]
        elif w.anchor_guid:
            zones = list(reader.element(w.anchor_guid).zones)
        else:
            zones = list(w.creation_zones)
        out += [z for z in zones if z and z not in out]
    return out


def writes_refusal(registry, writes: Iterable[ElementWrite], *, entity_type: str = "", slug: str = "",
                   reader=None) -> str:
    """The run-time re-check before a queued or retried write: "" when it may proceed, else the row's words
    "not permitted · <reason>". As the person the write was queued for when the caller names one (the same
    `writes_access`); with NO requester it proceeds only when no zone is in use (Brief Z2 round 2's rule, the
    owner's "if zones aren't being used then it is open"). A failure is a refusal with its reason, never a pass."""
    from resource_explorer.zone_access import AccessUnreadable, EgeriaAccessReader

    wc = _curate()
    writes = list(writes)
    if not writes:
        return ""
    who = wc._caller_identity()
    try:
        if who is None or not (who.user_id or "").strip():
            zones = _zones_in_use(writes, reader or EgeriaAccessReader())
            if not zones:
                return ""
            many = len(zones) != 1
            return (f"{NOT_PERMITTED} · {wc.NO_REQUESTER_WORDS}, and zone{'s' if many else ''} "
                    f"{', '.join(zones)} {'are' if many else 'is'} in use")
        d = writes_access(registry, writes, entity_type=entity_type, slug=slug, reader=reader)
    except (AccessUnreadable, wc._RegistryUnreadable) as exc:
        return f"{NOT_PERMITTED} · could not check access in Egeria ({exc})"[:300]
    except Exception as exc:  # noqa: BLE001 - a failure is a refusal with its reason, never a pass
        return f"{NOT_PERMITTED} · could not check access ({type(exc).__name__}: {exc})"[:300]
    return "" if d.allowed else f"{NOT_PERMITTED} · {d.reason}"


def words(d) -> str:
    """A refused decision as its row words."""
    return "" if d.allowed else f"{NOT_PERMITTED} · {d.reason}"


def access_words(registry, writes: Iterable[ElementWrite], *, entity_type: str = "", slug: str = "",
                 reader=None) -> str:
    """`writes_access` for one item of a request (the caller is the person; anonymous is denied), as the item's
    words: "" when permitted, else "not permitted · <reason>". A failure is a refusal with its reason."""
    try:
        return words(writes_access(registry, writes, entity_type=entity_type, slug=slug, reader=reader))
    except Exception as exc:  # noqa: BLE001 - a failure is a refusal with its reason, never a pass
        return f"{NOT_PERMITTED} · could not check access ({type(exc).__name__}: {exc})"[:300]


# ── investigations ──────────────────────────────────────────────────────────────────────────────────────

def _ops():
    from resource_explorer import zone_access as z

    return z


def project_creation_zones(inv: dict) -> tuple[str, ...]:
    """The zones `promote` creates the investigation's Project in: the private zones for a private one
    (step 1a), else none (RE stamps no zone on a Project)."""
    from resource_explorer.egeria_identity import private_zones
    from resource_explorer.surveyors.egeria_investigation_publisher import is_private_investigation

    if is_private_investigation(inv):
        return tuple(private_zones((inv.get("created_by") or "").strip()))
    # An ownerless private one (`ownerless_private_words`) is never created at all: promote refuses it before
    # any write, so its "no zones" here is never a create that happens (ruling 2026-10-13).
    return ()


def promote_writes(inv: dict) -> list[ElementWrite]:
    """`EgeriaInvestigationPublisher.promote`'s writes, before the members: the NEW Project (CREATE, CLASSIFY for
    its kind and the Investigation marker, ATTACH for the ResourceList to its Folio, PUBLISH when it is zoned
    private). The Folio is created anchored to that Project, so Egeria judges it on the Project's zones with the
    same operations (`validateUserForAnchorMemberCreate`, `validateUserForAnchorAttach`): covered here. Each
    member's own end is `member_write`."""
    z = _ops()
    zones = project_creation_zones(inv)
    ops = (z.CREATE_OPERATION, z.CLASSIFY_OPERATION, z.ATTACH_OPERATION) + ((z.PUBLISH_OPERATION,) if zones else ())
    return [ElementWrite("", ops, creation_zones=zones)]


def member_write(member_guid: str) -> ElementWrite:
    """A member's end of its CollectionMembership: ATTACH on the member's asset (its own zones, or its anchor's)."""
    return ElementWrite(member_guid, (_ops().ATTACH_OPERATION,))


def collection_write(collection_guid: str, label: str = "") -> ElementWrite:
    """A collection's end of a membership or ResourceList: ATTACH (a Folio is anchored to its Project: judged
    there, plus READ on the Folio)."""
    return ElementWrite(collection_guid, (_ops().ATTACH_OPERATION,), label=label)


def member_detach_write(member_guid: str) -> ElementWrite:
    """A member's end of a CollectionMembership being removed: DETACH (`validateUserForElementDetach`
    :1639-1694, on both ends)."""
    return ElementWrite(member_guid, (_ops().DETACH_OPERATION,))


def collection_detach_write(collection_guid: str, label: str = "") -> ElementWrite:
    """A collection's end of a CollectionMembership being removed: DETACH."""
    return ElementWrite(collection_guid, (_ops().DETACH_OPERATION,), label=label)


def _working_set_guid(registry, slug: str) -> str:
    """The investigation's recorded Folio GUID, read-only (a check must not create the working-set row)."""
    ws_slug = registry.investigation_working_set_slug(slug)
    ws = registry.get_working_set(ws_slug) if ws_slug else None
    return str((ws or {}).get("egeria_collection_guid") or "")


def bind_writes(registry, slug: str, status: str, project_guid: str) -> list[ElementWrite]:
    """Binding to an existing Project writes to Egeria only through `ensure_working_set`, and only when no Folio
    is recorded yet: a NEW Folio anchored to the Project (CREATE, judged on the Project) and its ResourceList
    (ATTACH on the Project). Clearing or a binding with no GUID writes nothing to Egeria."""
    z = _ops()
    if status != "linked" or not project_guid or _working_set_guid(registry, slug):
        return []
    return [ElementWrite(project_guid, (z.ATTACH_OPERATION,)),
            ElementWrite("", (z.CREATE_OPERATION,), label="the working set", anchor_guid=project_guid)]


def sync_writes(inv: dict) -> list[ElementWrite]:
    """`sync_project_properties`: UPDATE_PROPERTIES on the bound Project; nothing when unbound."""
    guid = (inv.get("egeria_project_guid") or "").strip()
    return [ElementWrite(guid, (_ops().UPDATE_OPERATION,))] if guid else []


def relink_writes(registry, slug: str) -> list[ElementWrite]:
    """`relink_members`, the collection end: ATTACH on the recorded Folio (judged on its Project). Each member's
    end is `member_write`. Nothing when there is no Folio to attach to (relink reports that itself)."""
    guid = _working_set_guid(registry, slug)
    return [collection_write(guid)] if guid else []


def reclassify_project_write(project_guid: str, from_kind: str) -> ElementWrite:
    """The Project's writes on a reclassify that moves visibility: PUBLISH (the zone change), CLASSIFY (the new
    kind) and DECLASSIFY (the old kind, removed by name when it is one RE knows)."""
    from resource_explorer.registry import ProjectRegistry

    z = _ops()
    ops = (z.PUBLISH_OPERATION, z.CLASSIFY_OPERATION)
    if from_kind and from_kind in ProjectRegistry.PROJECT_CLASSIFICATIONS:
        ops += (z.DECLASSIFY_OPERATION,)
    return ElementWrite(project_guid, ops)


def reclassify_kind_after_move(project_guid: str, from_kind: str, target: Iterable[str],
                               own_zones: Iterable[str] = ()) -> ElementWrite:
    """Round 2 (F6): the Project's kind change judged on the zones the move puts it in, as Egeria judges it (the
    reclassifier moves the zones first, then declassifies the old kind and classifies the new one, each checked
    on the element's zones at that moment). `own_zones`: RE's zones a loosening with no configured zone clears."""
    from resource_explorer.registry import ProjectRegistry

    z = _ops()
    ops = (z.CLASSIFY_OPERATION,)
    if from_kind and from_kind in ProjectRegistry.PROJECT_CLASSIFICATIONS:
        ops += (z.DECLASSIFY_OPERATION,)
    return ElementWrite(project_guid, ops, label="the investigation Project in its new zones",
                        zones_after=tuple(target), foreign_after=tuple(own_zones))


def report_write(report_guid: str) -> ElementWrite:
    """A member's SurveyReport moved with a reclassify: PUBLISH (its zone change)."""
    return ElementWrite(report_guid, (_ops().PUBLISH_OPERATION,))


# ── work lists ──────────────────────────────────────────────────────────────────────────────────────────

def work_list_collection_write(collection_guid: str) -> ElementWrite:
    """A work list's `WorkingSet`: an existing one is ATTACHed to; a new one is CREATEd (RE stamps no zone on it)
    and then ATTACHed to."""
    z = _ops()
    if collection_guid:
        return ElementWrite(collection_guid, (z.ATTACH_OPERATION,))
    return ElementWrite("", (z.CREATE_OPERATION, z.ATTACH_OPERATION), creation_zones=())


def work_list_link_back_write(collection_guid: str) -> ElementWrite:
    """A re-publish setting the link back to RE on an existing WorkingSet: UPDATE_PROPERTIES."""
    return ElementWrite(collection_guid, (_ops().UPDATE_OPERATION,))


def batch_refusal(registry, entity_type: str, slug: str, *, reader=None) -> str:
    """One member of a work-list batch run: "" when the caller may make the Egeria write its run makes, else
    "not permitted · <reason>". An analysis run writes to Egeria only through its auto-publish, which happens
    when the resource is assigned an Egeria Project (`workflows/analysis.py`); that is Brief Z2's resource write
    `publish` (`resource_access`). A member whose run writes nothing to Egeria is not checked. The run re-checks
    as the requester right before its publish (`person_write_refusal`, unchanged)."""
    wc = _curate()
    try:
        if not registry.has_assigned_egeria_project(entity_type, slug):
            return ""
        d = wc.resource_access(registry, entity_type, slug, "publish", reader=reader)
    except Exception as exc:  # noqa: BLE001
        return f"{NOT_PERMITTED} · could not check access ({type(exc).__name__}: {exc})"[:300]
    return words(d)


# ── RFA ToDos ───────────────────────────────────────────────────────────────────────────────────────────

def todo_writes(rfa_row: Optional[dict]) -> list[ElementWrite]:
    """`rfa_egeria_sync.sync_rfa_action`'s write: UPDATE_PROPERTIES on the RFA's ToDo when it has one
    (`update_asset`), else CREATE of a new one (`create_my_todo`: own anchor, no zone written by RE). The
    originator/assignee links it makes go to the caller's own profile and are not checked here."""
    z = _ops()
    guid = str((rfa_row or {}).get("egeria_todo_guid") or "")
    if guid:
        return [ElementWrite(guid, (z.UPDATE_OPERATION,))]
    return [ElementWrite("", (z.CREATE_OPERATION,), creation_zones=())]


def note_writes(rfa_row: Optional[dict]) -> list[ElementWrite]:
    """Round 2 (F3): `rfa_egeria_sync.sync_rfa_note`'s writes, a NoteLog (the first time) and a Note, both created
    ANCHORED to the RFA's ToDo. They are not feedback types: Egeria judges them as anchored members, CREATE on the
    anchor with the user as maintainer (`validateUserForAnchorMemberCreate` :1965-1990). Nothing is written while
    the RFA has no ToDo yet (the note waits for one), so nothing is checked."""
    guid = str((rfa_row or {}).get("egeria_todo_guid") or "")
    if not guid:
        return []
    return [ElementWrite("", (_ops().CREATE_OPERATION,), anchor_guid=guid)]


def per_member(items: Iterable, refusal: Callable[[object], str]) -> tuple[list, list[tuple[object, str]]]:
    """Split `items` into (permitted, [(item, words)]) by `refusal(item)` ("" = permitted)."""
    ok, refused = [], []
    for item in items:
        why = refusal(item)
        if why:
            refused.append((item, why))
        else:
            ok.append(item)
    return ok, refused


def investigation_member_refusal(registry, slug: str, *, unattended: bool = False) -> Callable[[dict], str]:
    """The per-member check `promote` and `relink_members` ask (`refusal(member)`): ATTACH on the member's asset
    (`member["member_guid"]`). In a request (the default) it is the caller's `writes_access`, and the
    collection's end was checked by the route. `unattended` (resync, which may run with nobody signed in and has
    no route check in front of it) also checks the recorded Folio's end on every member, by `writes_refusal`'s
    rule (as the person who pressed Apply, or, with nobody, open only while no zone is in use)."""
    from resource_explorer.zone_access import AccessUnreadable

    folio = _working_set_guid(registry, slug) if unattended else ""
    # One pass: controls and accounts read once, elements fresh per member; a private investigation's reads are
    # made as its signed-in owner (the Folio is anchored to the private Project).
    try:
        reader = investigation_reader(registry, slug)
    except AccessUnreadable as exc:                  # fails closed: every member refused with the reason
        why = f"{NOT_PERMITTED} · could not check access in Egeria ({exc})"[:300]
        return lambda member: why

    def refusal(member: dict) -> str:
        ws = [member_write(str(member.get("member_guid") or ""))]
        if folio:
            ws.append(collection_write(folio, "the working set"))
        kw = {"entity_type": str(member.get("entity_type") or ""), "slug": str(member.get("entity_slug") or ""),
              "reader": reader}
        return writes_refusal(registry, ws, **kw) if unattended else access_words(registry, ws, **kw)
    return refusal
