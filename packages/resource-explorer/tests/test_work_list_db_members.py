"""A database or file-system work list attaches its published members (wl-0c09fe4404, 2026-10-13).

`WorkLists.publish` resolved every member's GUID with `registry.get(slug)`, which reads repos only: each database
member came back None and was reported "never published to Egeria", so a five-member database list attached nothing
while two of its databases had asset GUIDs. The removal map shared the lookup and the blind spot.

One resolver now (`member_egeria_guid`), by the list's type. These tests run the route end to end with a real
registry on temp sqlite holding real `DatabaseEntity` / `FileSystemEntity` rows; only the Egeria clients are faked,
bound to the real pyegeria signatures (`test_work_list_publish_sync.SyncCollections`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from resource_explorer.registry import DatabaseEntity, FileSystemEntity

sys.path.insert(0, str(Path(__file__).parent))

from test_work_list_publish_sync import BASE, SyncCollections, _detach_rows, _drain  # noqa: E402
from test_zone_access_investigations_worklists import (  # noqa: E402,F401  (fixtures are shared)
    USER, _Clients, _install, registry, web,
)
from tests.zone_fakes import real_lookup  # noqa: E402

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")


def _db(registry, slug, guid=""):
    registry.register_database(DatabaseEntity(slug=slug, display_name=slug, db_type="postgresql", host="localhost",
                                              port=5432, database_name=slug))
    if guid:
        registry.set_database_egeria_guid(slug, guid)


def _fs(registry, slug, guid=""):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=f"/data/{slug}"))
    if guid:
        registry.set_filesystem_egeria_guid(slug, guid)


def _world(web, registry, monkeypatch, entity_type, members):
    fake = _install(monkeypatch)
    cm = SyncCollections()
    monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", lambda who=None: (_Clients(cm), real_lookup()))
    monkeypatch.setenv("RE_PUBLIC_URL", BASE)
    # Every access decision publish asks for, with the entity type it named (Z3: a database member is checked as one).
    from resource_explorer.workflows import egeria_work_access as ewa

    asked, real = [], ewa.access_words

    def spy(registry_, writes, *, entity_type="", slug="", reader=None):
        asked.append((entity_type, slug))
        return real(registry_, writes, entity_type=entity_type, slug=slug, reader=reader)

    monkeypatch.setattr(ewa, "access_words", spy)
    from resource_explorer.work_lists import WorkLists

    WorkLists(registry).create("W", members, entity_type=entity_type, created_by=USER)
    slug = WorkLists(registry).list_all()[0]["slug"]
    return {"client": web[0], "registry": registry, "cm": cm, "fake": fake, "slug": slug, "asked": asked}


def _publish(world):
    r = world["client"].post(f"/api/work-lists/{world['slug']}/publish")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def dbs(web, registry, monkeypatch):
    _db(registry, "usda", "g-usda")
    _db(registry, "adventureworks", "g-aw")
    _db(registry, "treasury")
    return _world(web, registry, monkeypatch, "database", ["usda", "adventureworks", "treasury"])


def test_a_database_work_list_attaches_the_members_with_an_element_and_names_the_one_without(dbs):
    out = _publish(dbs)
    assert out["queued_members"] == 2                                   # main: 0 (every member read "unpublished")
    assert sorted(out["queued_to_attach"]) == ["adventureworks", "usda"]
    assert out["not_published"] == [{"entity_slug": "treasury", "entity_type": "database",
                                     "words": "not in Egeria yet · database"}]
    assert out["members_without_an_egeria_asset"] == ["treasury"]
    assert out["not_resolved"] == []
    _drain(dbs)
    assert dbs["cm"].members[out["egeria_guid"]] == {"g-usda", "g-aw"}
    # Z3: each member's ATTACH was decided as a database, not as a repo.
    assert ("database", "usda") in dbs["asked"] and ("database", "adventureworks") in dbs["asked"]
    assert not any(t == "repo" for t, _ in dbs["asked"])


def test_a_database_member_removed_in_re_is_detached_on_re_publish(dbs):
    out = _publish(dbs)
    _drain(dbs)
    guid = out["egeria_guid"]
    from resource_explorer.work_lists import WorkLists

    WorkLists(dbs["registry"]).remove_member(dbs["slug"], "adventureworks")
    out = _publish(dbs)
    assert out["removals"]["detached"] == ["adventureworks"], out["removals"]
    assert [c for c in dbs["cm"].calls if c[0] == "remove_from_collection"] == [
        ("remove_from_collection", guid, "g-aw")]
    assert len(_detach_rows(dbs)) == 1
    _drain(dbs)
    assert dbs["cm"].members[guid] == {"g-usda"}


def test_a_database_member_still_listed_is_not_removed_when_its_guid_is_cleared(dbs):
    """A member whose element no longer resolves (links forgotten) stays: never removed on a guess."""
    out = _publish(dbs)
    _drain(dbs)
    dbs["registry"].set_database_egeria_guid("usda", "")
    out = _publish(dbs)
    assert out["removals"]["detached"] == [] and not any(c[0] == "remove_from_collection" for c in dbs["cm"].calls)
    assert sorted(m["entity_slug"] for m in out["not_published"]) == ["treasury", "usda"]


def test_a_filesystem_work_list_attaches_by_the_filesystem_guid(web, registry, monkeypatch):
    _fs(registry, "share_a", "g-fa")
    _fs(registry, "share_b")
    world = _world(web, registry, monkeypatch, "filesystem", ["share_a", "share_b"])
    out = _publish(world)
    assert out["queued_members"] == 1
    assert out["queued_to_attach"] == ["share_a"]
    assert out["not_published"] == [{"entity_slug": "share_b", "entity_type": "filesystem",
                                     "words": "not in Egeria yet · filesystem"}]
    _drain(world)
    assert world["cm"].members[out["egeria_guid"]] == {"g-fa"}


def test_a_member_re_does_not_hold_is_not_resolved_not_unpublished(web, registry, monkeypatch):
    _db(registry, "usda", "g-usda")
    world = _world(web, registry, monkeypatch, "database", ["usda", "gone"])
    out = _publish(world)
    assert out["queued_to_attach"] == ["usda"]
    assert out["not_published"] == [] and out["members_without_an_egeria_asset"] == []
    assert [(u["entity_slug"], u["words"]) for u in out["not_resolved"]] == [
        ("gone", "could not resolve · no such database · 'gone'")]


# ── the resolver itself ──────────────────────────────────────────────────────────────────────────────────────

def test_the_resolver_reads_each_type_by_its_own_getter(registry):
    from resource_explorer.member_assets import NotInEgeriaYet, member_egeria_guid

    _db(registry, "usda", "g-usda")
    _db(registry, "treasury")
    _fs(registry, "share_a", "g-fa")
    assert member_egeria_guid(registry, "repo", "p") == "g-p"
    assert member_egeria_guid(registry, "database", "usda") == "g-usda"
    with pytest.raises(NotInEgeriaYet, match="not in Egeria yet · database"):
        member_egeria_guid(registry, "database", "treasury")
    assert member_egeria_guid(registry, "filesystem", "share_a") == "g-fa"


def test_an_unknown_type_or_a_failed_read_is_not_an_answer(registry, monkeypatch):
    from resource_explorer.member_assets import MemberUnresolved, member_egeria_guid

    with pytest.raises(MemberUnresolved, match="cannot link this kind"):
        member_egeria_guid(registry, "spreadsheet", "p")
    with pytest.raises(MemberUnresolved, match="no such repo"):
        member_egeria_guid(registry, "repo", "nope")

    def boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(type(registry), "get_database", boom)
    with pytest.raises(MemberUnresolved, match="could not read RE's record · RuntimeError: registry down"):
        member_egeria_guid(registry, "database", "usda")
