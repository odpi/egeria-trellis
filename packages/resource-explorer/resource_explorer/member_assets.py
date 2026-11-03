"""One answer to "what is this member's own Egeria asset?" for every list that holds resources.

Work lists and investigations both ask it (`work_lists` publish, `egeria_investigation_publisher` promote / relink /
link-status), so they cannot disagree. The answer is the kind's OWN pointer: the repo's project asset,
`databases.egeria_asset_guid` (the RelationalDatabase), `file_systems.egeria_asset_guid` (the root FileFolder) --
never the server or the host.

It returns a GUID or raises. It never returns "": an empty stored pointer is its own typed outcome
(`NotInEgeriaYet`), so no caller can read "" as "not in Egeria yet" by accident, and the other outcomes
(`MemberUnresolved`) are never worded as "not in Egeria yet". Each exception carries its words.
"""
from __future__ import annotations

from resource_explorer.resource_types import MEMBER_RESOURCE_TYPES

#: The resource kinds a list's members can be, and so the ones `member_egeria_guid` knows how to read.
MEMBER_ENTITY_TYPES = MEMBER_RESOURCE_TYPES


class MemberUnresolved(Exception):
    """RE could not say whether the member has an Egeria element: an unsupported kind, no such row, or a failed
    registry read. The message is the words."""


class NotInEgeriaYet(Exception):
    """RE holds the resource and its own Egeria pointer is empty. The only outcome worded "not in Egeria yet"."""

    def __init__(self, entity_type: str):
        super().__init__(f"not in Egeria yet · {entity_type}")


class _NoAsset:
    egeria_asset_guid = ""


def _entity(registry, entity_type: str, slug: str):
    if entity_type == "repo":
        return registry.get(slug)
    if entity_type == "database":
        return registry.get_database(slug, allow_unreadable=True)
    if entity_type == "filesystem":
        return registry.get_filesystem(slug)
    if entity_type == "open_data":
        # Registered in RE only: its own pointer is always empty (nothing publishes it in OD1), so it reads
        # "not in Egeria yet", never "cannot link this kind".
        from resource_explorer.open_data.store import OpenDataStore

        return OpenDataStore(registry).get_dataset(slug) and _NoAsset()
    return None


def member_egeria_guid(registry, entity_type: str, slug: str) -> str:
    """The member's own Egeria asset GUID, non-empty. Raises `MemberUnresolved` (kind unsupported: "cannot link this
    kind"; no row: "no such <kind>"; failed read) or `NotInEgeriaYet` (empty pointer)."""
    if entity_type not in MEMBER_ENTITY_TYPES:
        raise MemberUnresolved(f"cannot link this kind · '{entity_type or '(none)'}'")
    try:
        entity = _entity(registry, entity_type, slug)
    except Exception as exc:  # noqa: BLE001 - a failed read is "could not resolve", never "not in Egeria yet"
        raise MemberUnresolved(f"could not read RE's record · {type(exc).__name__}: {exc}"[:300]) from exc
    if entity is None:
        raise MemberUnresolved(f"no such {entity_type} · '{slug}'")
    guid = str(getattr(entity, "egeria_asset_guid", "") or "").strip()
    if not guid:
        raise NotInEgeriaYet(entity_type)
    return guid
