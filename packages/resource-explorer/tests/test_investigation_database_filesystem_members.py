"""A database or file-system member of an investigation is linked from its OWN asset (2026-10-24).

Found live: `_asset_guid` returned "" for every non-repo member, so a database with `egeria_asset_guid` set
(coco_pharma) was never attached to the working set and link-status said "not in Egeria yet". Rulings: the member
is the resource's own asset (RelationalDatabase / root FileFolder), "not in Egeria yet" only when that kind's own
pointer is empty, and a member link never touches `entity_egeria_project_context`.

Harness: the autospec'd pyegeria boundary and real registry/routes of test_private_investigation_attach_as_owner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_private_investigation_attach_as_owner import (  # noqa: E402,F401  (fixtures are shared)
    OWNER, ZonedEgeria, _promote, _rows, registry, web, world,
)

DB_ASSET = "g-db-coco"
FS_ASSET = "g-fs-root"


def _register(reg, *, db_guid: str, fs_guid: str):
    from resource_explorer.registry import DatabaseEntity, FileSystemEntity
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql",
                                          host="localhost", port=5432, database_name="coco"))
    reg.register_filesystem(FileSystemEntity(slug="fs1", display_name="FS1", local_mount_point="/tmp/fs1"))
    if db_guid:
        reg.set_database_egeria_guid("coco", db_guid)
    if fs_guid:
        reg.set_filesystem_egeria_guid("fs1", fs_guid)


def _investigation(world, members: list[tuple[str, str]]) -> str:
    r = world["client"].post("/api/investigations/", json={"display_name": "Members", "project_classification":
                                                            "PersonalProject"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    for kind, ms in members:
        r = world["client"].post(f"/api/investigations/{slug}/members", json={"entity_type": kind,
                                                                              "entity_slug": ms})
        assert r.status_code == 200, r.text
    return slug


def _status(world, slug: str) -> dict[tuple[str, str], dict]:
    r = world["client"].get(f"/api/investigations/{slug}/link-status")
    assert r.status_code == 200, r.text
    return {(m["entity_type"], m["entity_slug"]): m for m in r.json()["members"]}


def _context_rows(reg) -> list[tuple]:
    with reg._conn() as conn:
        return [tuple(r) for r in conn.execute("SELECT * FROM entity_egeria_project_context ORDER BY 1,2,3").fetchall()]


def test_database_and_filesystem_members_with_pointers_are_attached_on_relink(world):
    eg, reg = world["eg"], world["registry"]
    _register(reg, db_guid=DB_ASSET, fs_guid=FS_ASSET)
    for g in (DB_ASSET, FS_ASSET):
        eg.elements[g] = {"zones": [], "anchor": "", "kinds": []}
    slug = _investigation(world, [("database", "coco"), ("filesystem", "fs1")])
    out = _promote(world, slug)
    folio = out["collection_guid"]
    before = _context_rows(reg)

    r = world["client"].post(f"/api/investigations/{slug}/relink-members")

    assert r.status_code == 200, r.text
    assert sorted(eg.members[folio]) == sorted([DB_ASSET, FS_ASSET]), eg.members
    # Read-first like repos: a second relink adds nothing new (CollectionMembership upsert, no duplicates).
    r = world["client"].post(f"/api/investigations/{slug}/relink-members")
    assert r.status_code == 200, r.text
    assert sorted(eg.members[folio]) == sorted([DB_ASSET, FS_ASSET]), eg.members
    assert _context_rows(reg) == before


def test_link_status_agrees_with_relink(world):
    eg, reg = world["eg"], world["registry"]
    _register(reg, db_guid=DB_ASSET, fs_guid="")
    eg.elements[DB_ASSET] = {"zones": [], "anchor": "", "kinds": []}
    slug = _investigation(world, [("database", "coco"), ("filesystem", "fs1")])
    _promote(world, slug)

    st = _status(world, slug)
    assert st[("database", "coco")]["state"] == "linked", st
    # An empty file-system pointer is the ONE case that reads "not in Egeria yet".
    assert st[("filesystem", "fs1")]["state"] == "not_linked"
    assert st[("filesystem", "fs1")]["reason"].startswith("not in Egeria yet"), st

    # Fill the pointer: not linked until relink, then linked -- the same function answers both.
    eg.elements[FS_ASSET] = {"zones": [], "anchor": "", "kinds": []}
    reg.set_filesystem_egeria_guid("fs1", FS_ASSET)
    st = _status(world, slug)
    assert st[("filesystem", "fs1")]["state"] == "not_linked" and st[("filesystem", "fs1")]["linkable"], st
    assert not st[("filesystem", "fs1")]["reason"].startswith("not in Egeria yet"), st
    world["client"].post(f"/api/investigations/{slug}/relink-members")
    assert _status(world, slug)[("filesystem", "fs1")]["state"] == "linked"


def test_unknown_kind_has_its_own_reason_and_empty_pointer_is_not_attached(world):
    eg, reg = world["eg"], world["registry"]
    _register(reg, db_guid="", fs_guid="")
    slug = _investigation(world, [("database", "coco"), ("widget", "w1")])
    out = _promote(world, slug)

    st = _status(world, slug)
    assert st[("database", "coco")]["reason"].startswith("not in Egeria yet"), st
    assert st[("widget", "w1")]["state"] == "not_linked"
    assert not st[("widget", "w1")]["reason"].startswith("not in Egeria yet"), st
    assert eg.members[out["collection_guid"]] == []
    reasons = {m["entity_slug"]: m["reason"] for m in out["members_unlinkable"]}
    assert reasons["coco"] == "not in Egeria yet · database", reasons
    assert reasons["w1"].startswith("cannot link this kind"), reasons


@pytest.mark.parametrize("kind,slug,guid,words", [
    ("repo", "r", "gr", None),
    ("database", "d", "gd", None),
    ("filesystem", "f", "gf", None),
    ("database", "d", "", "not in Egeria yet · database"),
    ("filesystem", "f", "", "not in Egeria yet · filesystem"),
    ("database", "nope", "", "no such database"),
    ("widget", "w", "", "cannot link this kind"),
])
def test_work_lists_and_investigations_get_the_same_answer(kind, slug, guid, words):
    """Both call sites use `resource_explorer.member_assets.member_egeria_guid`, over autospec'd registry getters."""
    from unittest.mock import create_autospec
    from resource_explorer.member_assets import MemberUnresolved, NotInEgeriaYet, member_egeria_guid
    from resource_explorer.registry import DatabaseEntity, FileSystemEntity, Project, ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher
    import resource_explorer.work_lists as work_lists

    assert work_lists.member_egeria_guid is member_egeria_guid          # moved, not copied
    reg = create_autospec(ProjectRegistry, instance=True, spec_set=True)
    reg.get.return_value = Project(slug="r", display_name="r", github_url="https://github.com/x/r",
                                   description="", egeria_asset_guid=guid)
    reg.get_database.side_effect = lambda s, allow_unreadable=False: (
        None if s == "nope" else DatabaseEntity(slug=s, display_name=s, db_type="postgresql", host="h", port=1,
                                                database_name=s, egeria_asset_guid=guid))
    reg.get_filesystem.return_value = FileSystemEntity(slug="f", display_name="f", local_mount_point="/f",
                                                       egeria_asset_guid=guid)
    got, why = EgeriaInvestigationPublisher(reg)._member_asset(kind, slug)
    if words is None:
        assert member_egeria_guid(reg, kind, slug) == got == guid and why == ""
        return
    with pytest.raises((MemberUnresolved, NotInEgeriaYet)) as exc:
        member_egeria_guid(reg, kind, slug)
    assert got == "" and why == str(exc.value) and why.startswith(words), (why, str(exc.value))
