"""Link a published database to its server's DatabaseManager, read-first: the one place RE makes this link.

**What Egeria does** (mirrored, not invented): the PostgreSQL server connector, when it catalogues a database,
relates the new database to the server's DatabaseManager capability with one `CapabilityAssetUse`
relationship whose `useType` is OWNS (`PostgresServerCatalogTargetProcessor.catalogDatabase`, lines 370-380
build the properties, 413-426 create the database from its template with the capability as the parent and
`OpenMetadataType.CAPABILITY_ASSET_USE_RELATIONSHIP` as the parent relationship). The capability is the
server's: the connector reads it from the server's capabilities (`getDatabaseManager`, lines 211-260: a
related `DatabaseManager` of the server, created and attached by `SupportedSoftwareCapability` only when the
server has none). End 1 is the capability (`consumedByCapabilities`, a SoftwareCapability), end 2 the asset
(`capabilityConsumedAssets`): `OpenMetadataTypesArchive2_7.getCapabilityAssetUseRelationship`, lines 177-217.
Properties: `useType`, `description`, `minimumInstances`, `maximumInstances`; the connector sets only `useType`.

The type is named `CapabilityAssetUse` in Egeria 6 (`OpenMetadataType.CAPABILITY_ASSET_USE_RELATIONSHIP`,
`OpenMetadataType.java:967`); "ServerAssetUse" is its pre-6 name (the 2.7 archive comment "0045 Add
ServerAssetUse"). pyegeria's `AssetMaker.add_capability_asset_use` posts the same relationship.

**Why read first.** `CapabilityAssetUse` is NOT multi-link (`RelationshipDef` defaults to false and
`getCapabilityAssetUseRelationship` never sets it); Egeria's generic handler refuses a second uni-link between
the same pair (`OpenMetadataAPIGenericHandler.java:10573-10600`), so Egeria backstops a race. RE still reads
first: the read gives the words ("already linked" / "used by another capability") and avoids a server
round-trip that would only be refused. It creates only when there is no link, and never when the read could
not be completed ("a missing link is recoverable on the next publish").

**Words** (the commit step's detail, and the heal's per-database line):

  linked to <server> via DatabaseManager <guid8>
  already linked
  not linked: <reason>            (no create call was made)
  could not check: <reason>       (no create call was made)

The RE writes no capability: a server with no DatabaseManager gets "server has no DatabaseManager
capability · not linked". Egeria's connector creates one, but that is the connector's element and RE's
publish adopts the server it did not make; the architect's ruling (2026-10-22) is to link, never to create.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from resource_explorer.egeria_link_once import Unreadable, _entry_element_guid, related_entries

log = logging.getLogger(__name__)

RELATIONSHIP_TYPE = "CapabilityAssetUse"
#: The capability sits at end 1 of CapabilityAssetUse, the asset (the database) at end 2.
CAPABILITY_END = 1
ASSET_END = 2
USE_TYPE = "OWNS"
CAPABILITY_RELATIONSHIP = "SupportedSoftwareCapability"
#: The server sits at end 1 of SupportedSoftwareCapability, the capability at end 2.
SERVER_END = 1
DATABASE_MANAGER = "DatabaseManager"

LINKED = "linked"
ALREADY_LINKED = "already linked"
NOT_LINKED = "not linked"
COULD_NOT_CHECK = "could not check"
#: A dry run's answer where a real run would create.
WOULD_LINK = "would link"

NO_CAPABILITY_WORDS = "server has no DatabaseManager capability · not linked"


@dataclass(frozen=True)
class ServerLink:
    """What one database's link came to. `words` is what a person reads; `outcome` is what a count keys on."""
    outcome: str
    words: str
    capability_guid: str = ""
    relationship_guid: str = ""

    @property
    def made(self) -> bool:
        return self.outcome == LINKED


def _sentence(exc: Exception, n: int = 200) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:n]}"


def _type_names(entry: dict) -> set[str]:
    far = entry.get("element") or entry.get("relatedElement") or {}
    kind = far.get("type") if isinstance(far, dict) else None
    if not isinstance(kind, dict):
        header = far.get("elementHeader") if isinstance(far, dict) else None
        kind = header.get("type") if isinstance(header, dict) else None
    names: set[str] = set()
    if isinstance(kind, dict):
        if kind.get("typeName"):
            names.add(str(kind["typeName"]))
        supers = kind.get("superTypeNames")
        if isinstance(supers, (list, tuple)):
            names.update(str(x) for x in supers if isinstance(x, str))
    return names


def database_managers(expert, server_guid: str) -> list[str]:
    """The GUIDs of the server's DatabaseManager capabilities, oldest relationship first. Raises `Unreadable`
    when the server's capabilities could not be read to the end."""
    entries = related_entries(expert, CAPABILITY_RELATIONSHIP, server_guid, SERVER_END)
    found = []
    for entry in entries:
        if DATABASE_MANAGER not in _type_names(entry):
            continue
        guid = _entry_element_guid(entry)
        if not guid:
            raise Unreadable("a capability entry carries no element GUID")
        found.append((str((entry.get("versions") or {}).get("createTime") or "~"), guid))
    return [g for _, g in sorted(found)]


def _body() -> dict:
    return {"class": "NewRelationshipRequestBody",
            "properties": {"class": "CapabilityAssetUseProperties", "useType": USE_TYPE}}


def link_database_to_server(expert, asset_maker, *, server_guid: str, database_guid: str, server_label: str = "",
                            write: bool = True) -> ServerLink:
    """Relate `database_guid` to `server_guid`'s DatabaseManager with `CapabilityAssetUse` (useType OWNS), unless
    the database already has such a link. Never raises: a failure comes back as the outcome. `write` False is a
    dry run: every read happens, the create does not, and a would-be create answers `WOULD_LINK`."""
    if not server_guid:
        return ServerLink(NOT_LINKED, "not linked: no server element")
    if not database_guid:
        return ServerLink(NOT_LINKED, "not linked: no database element")
    try:
        managers = database_managers(expert, server_guid)
    except Unreadable as exc:
        return ServerLink(COULD_NOT_CHECK, f"could not check the server's capabilities ({exc}) · not linked")
    if not managers:
        return ServerLink(NOT_LINKED, NO_CAPABILITY_WORDS)
    try:
        existing = related_entries(expert, RELATIONSHIP_TYPE, database_guid, ASSET_END)
    except Unreadable as exc:
        return ServerLink(COULD_NOT_CHECK,
                          f"could not check the database's existing {RELATIONSHIP_TYPE} links ({exc}) · "
                          "not linked: a duplicate is worse than a missing link")
    owners = []
    for entry in existing:
        guid = _entry_element_guid(entry)
        if not guid:
            # It might be the link we are looking for: never create past an entry we cannot attribute.
            return ServerLink(COULD_NOT_CHECK,
                              f"an existing {RELATIONSHIP_TYPE} link carries no capability GUID · not linked")
        owners.append((guid, str(entry.get("relationshipGUID") or "")))
    ours = [(g, r) for g, r in owners if g in managers]
    if ours:
        if len(ours) > 1:
            log.warning("database %s has %d %s links to its server's DatabaseManager (duplicates); not adding another",
                        database_guid, len(ours), RELATIONSHIP_TYPE)
        return ServerLink(ALREADY_LINKED, "already linked", capability_guid=ours[0][0], relationship_guid=ours[0][1])
    if owners:
        other = owners[0][0]
        return ServerLink(NOT_LINKED, f"not linked: the database is already used by capability {other[:8]} "
                                      "(not this server's DatabaseManager)")
    capability = managers[0]
    where = f"{server_label or server_guid[:8]} via DatabaseManager {capability[:8]}"
    if not write:
        return ServerLink(WOULD_LINK, f"would link to {where}", capability_guid=capability)
    log.info("egeria write BEFORE: add_capability_asset_use capability=%s asset=%s type=%s useType=%s",
             capability, database_guid, RELATIONSHIP_TYPE, USE_TYPE)
    try:
        asset_maker.add_capability_asset_use(capability, database_guid, _body())
    except Exception as exc:  # noqa: BLE001 - returned for the caller to record
        log.warning("egeria write AFTER: add_capability_asset_use capability=%s asset=%s outcome=failed: %s",
                    capability, database_guid, _sentence(exc))
        return ServerLink(NOT_LINKED, f"not linked: could not link ({_sentence(exc)})"[:400], capability_guid=capability)
    log.info("egeria write AFTER: add_capability_asset_use capability=%s asset=%s outcome=ok", capability, database_guid)
    return ServerLink(LINKED, f"linked to {where}", capability_guid=capability)


# ── the heal ──────────────────────────────────────────────────────────────────

@dataclass
class HealReport:
    """The heal's tally. `acting_as` says who the writes were made as (the daemon, recorded as such)."""
    acting_as: str = ""
    dry_run: bool = False
    linked: int = 0
    already_linked: int = 0
    could_not_check: int = 0
    not_linked: int = 0
    would_link: int = 0
    rows: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        if self.dry_run:
            head = f"would link {self.would_link}"
        else:
            head = f"linked {self.linked}"
        tail = f"already linked {self.already_linked}, could not check {self.could_not_check}"
        if self.not_linked:
            tail += f", not linked {self.not_linked}"
        return f"{head}, {tail}"

    def as_dict(self) -> dict:
        return {"acting_as": self.acting_as, "dry_run": self.dry_run, "summary": self.summary(),
                "linked": self.linked, "already_linked": self.already_linked,
                "could_not_check": self.could_not_check, "not_linked": self.not_linked,
                "would_link": self.would_link, "databases": self.rows}


def heal_database_server_links(registry, gateway_for, *, acting_as: str = "", dry_run: bool = False) -> HealReport:
    """Walk RE's registered databases that carry an `egeria_asset_guid`, read each one's CapabilityAssetUse links,
    and link the unlinked ones. `gateway_for(db_entity)` gives the gateway to use (the daemon's, built by the
    caller); it must have `link_published_database(db_entity, write=)`. Runs only when called: nothing at
    start-up calls it. A database whose check fails is counted `could not check` and nothing is created for it."""
    report = HealReport(acting_as=acting_as, dry_run=dry_run)
    # Recorded BEFORE the first write, so a walk that dies half-way still says who was acting.
    log.info("database/server link heal starting: acting as %s, %s", acting_as or "?",
             "dry run, no writes" if dry_run else "writes")
    for db in registry.list_databases():
        if not (getattr(db, "egeria_asset_guid", "") or "").strip():
            continue
        try:
            result = gateway_for(db).link_published_database(db, write=not dry_run)
        except Exception as exc:  # noqa: BLE001 - one database never stops the walk
            result = ServerLink(COULD_NOT_CHECK, f"could not check ({_sentence(exc)}) · not linked")
        if result.outcome == LINKED:
            report.linked += 1
        elif result.outcome == WOULD_LINK:
            report.would_link += 1
        elif result.outcome == ALREADY_LINKED:
            report.already_linked += 1
        elif result.outcome == COULD_NOT_CHECK:
            report.could_not_check += 1
        else:
            report.not_linked += 1
        report.rows.append({"slug": db.slug, "database_guid": db.egeria_asset_guid, "outcome": result.outcome,
                            "words": result.words})
    log.info("database/server link heal (acting as %s, %s): %s", acting_as or "?",
             "dry run" if dry_run else "writes", report.summary())
    return report
