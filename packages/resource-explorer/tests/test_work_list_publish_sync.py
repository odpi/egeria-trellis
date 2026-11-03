"""A work list's re-publish keeps its Egeria WorkingSet in step with RE (the Portal's ask, 2026-10-12).

The Portal's Resource Browser reads these collections and copies members out of them, so it asked for two things:

1. a link back: `additionalProperties.resourceExplorerUrl` on the WorkingSet, pointing at RE's work-list page;
2. removal: a member removed in RE is DETACHED on the next publish. Membership is uni-link, so a re-add converges by
   itself; a removal happens only if RE makes it.

The removals are counted from RE's own record of what it attached (the outbox rows for this collection), by the same
function the writes are planned from. No Egeria anywhere: the access reads are faked at the client-construction
boundary (`tests/zone_fakes.py`), and the CollectionManager is a fake bound to the REAL pyegeria signatures
(`add_to_collection`, `remove_from_collection`, `create_collection`, `update_collection`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from resource_explorer.registry import Project

sys.path.insert(0, str(Path(__file__).parent))

from test_zone_access_investigations_worklists import (  # noqa: E402,F401  (fixtures and helpers are shared)
    SECURED, USER, FakeCollections, _Clients, _install, _real, _zone, registry, web,
)
from tests.zone_fakes import real_lookup  # noqa: E402

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

CM = "pyegeria.omvs.collection_manager.CollectionManager"
REMOVE = _real(CM, "remove_from_collection")
UPDATE = _real(CM, "update_collection")
GET = _real(CM, "get_collection_by_guid")
SECURITY_403 = ("OPEN-METADATA-SECURITY-403-007 User garygeeke is not authorized to issue operation Detach on "
                "WorkingSet coll-1. More words.")
BASE = "https://re.example.org:8810/"


class SyncCollections(FakeCollections):
    """A CollectionManager fake that also holds what is in each collection, so a test reads Egeria's side."""

    def __init__(self):
        super().__init__()
        self.members: dict[str, set] = {}
        self.created: dict[str, dict] = {}
        self.updates: list[tuple[str, dict]] = []
        self.props: dict[str, dict] = {}          # each collection's additionalProperties, as Egeria holds them

    def create_collection(self, *a, **kw):
        guid = super().create_collection(*a, **kw)
        self.created[guid] = kw["body"]
        self.props[guid] = dict(kw["body"]["properties"].get("additionalProperties") or {})
        return guid

    def get_collection_by_guid(self, *a, **kw):
        GET.bind(self, *a, **kw)
        self.calls.append(("get_collection_by_guid", kw.get("graph_query_depth")))
        guid = a[0] if a else kw["guid"]
        return {"elementHeader": {"guid": guid}, "properties": {"additionalProperties": dict(self.props[guid])}}

    def add_to_collection(self, *a, **kw):
        super().add_to_collection(*a, **kw)
        self.members.setdefault(a[0], set()).add(a[1])

    def remove_from_collection(self, *a, **kw):
        REMOVE.bind(self, *a, **kw)
        self.calls.append(("remove_from_collection", a[0], a[1]))
        self.members.setdefault(a[0], set()).discard(a[1])

    def update_collection(self, *a, **kw):
        UPDATE.bind(self, *a, **kw)
        self.calls.append(("update_collection", a[0]))
        body = kw.get("body") or a[1]
        self.updates.append((a[0], body))
        self.props[a[0]] = dict(body["properties"]["additionalProperties"])


def test_the_fake_binds_the_real_detach_and_update_signatures():
    with pytest.raises(TypeError):
        SyncCollections().remove_from_collection("c")                  # element_guid is required
    with pytest.raises(TypeError):
        SyncCollections().update_collection("c")                       # body is required


@pytest.fixture
def world(web, registry, monkeypatch):
    """Three published repos p, q, r; a CollectionManager fake; RE's public URL configured; zones open."""
    registry.add(Project(slug="r", display_name="R", github_url="https://github.com/x/r", description=""))
    registry.set_egeria_asset_guid("r", "g-r")
    fake = _install(monkeypatch)
    cm = SyncCollections()
    monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", lambda who=None: (_Clients(cm), real_lookup()))
    monkeypatch.setenv("RE_PUBLIC_URL", BASE)
    from resource_explorer.work_lists import WorkLists

    WorkLists(registry).create("W", ["p", "q", "r"], entity_type="repo", created_by=USER)
    slug = WorkLists(registry).list_all()[0]["slug"]
    return {"client": web[0], "registry": registry, "cm": cm, "fake": fake, "slug": slug}


def _publish(world):
    r = world["client"].post(f"/api/work-lists/{world['slug']}/publish")
    assert r.status_code == 200, r.text
    return r.json()


def _drain(world):
    """The background worker's pass over everything queued."""
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    return drain_outbox(world["registry"], OutboxClients(collection_manager=world["cm"]), lambda qn: "", limit=50)


def _detach_rows(world):
    return [r for r in world["registry"].list_outbox_elements(entity_slug=world["slug"], limit=50)
            if r["element_kind"] == "collection_membership_detach"]


def _remove(world, member):
    from resource_explorer.work_lists import WorkLists

    WorkLists(world["registry"]).remove_member(world["slug"], member)


def test_a_re_publish_detaches_exactly_the_removed_member_and_carries_the_link_back(world):
    """End to end through the route: add three, publish, drain; remove one, re-publish: exactly one detach is
    queued and applied, Egeria's side holds the other two, and the link back is set on create and on update."""
    cm, slug = world["cm"], world["slug"]
    out = _publish(world)
    _drain(world)
    guid = out["egeria_guid"]
    want = f"https://re.example.org:8810/next?worklist={slug}"
    assert cm.created[guid]["properties"]["additionalProperties"] == {"resourceExplorerUrl": want}
    assert out["link_back"] == {"url": want, "state": "set", "words": ""}
    assert out["removals"]["detached"] == [] and _detach_rows(world) == []
    assert cm.members[guid] == {"g-p", "g-q", "g-r"}

    _remove(world, "r")
    out = _publish(world)
    assert out["removals"]["detached"] == ["r"], out["removals"]
    assert out["removals"]["not_permitted"] == [] and out["removals"]["failed"] == []
    assert [c for c in cm.calls if c[0] == "remove_from_collection"] == [("remove_from_collection", guid, "g-r")]
    rows = _detach_rows(world)
    assert len(rows) == 1 and rows[0]["status"] == "done"
    assert json.loads(rows[0]["payload_json"])["by"] == USER                  # as the person who pressed publish
    assert out["link_back"]["state"] == "unchanged" and cm.updates == []      # read back, already right: not re-sent
    _drain(world)
    assert cm.members[guid] == {"g-p", "g-q"}

    out = _publish(world)                                                      # nothing more to remove
    assert out["removals"]["detached"] == [] and len(_detach_rows(world)) == 1


def test_a_removed_member_whose_add_was_never_sent_is_withdrawn_not_detached(world, monkeypatch):
    from resource_explorer import work_lists

    cm = world["cm"]
    cap = work_lists.INLINE_ATTACH_LIMIT
    monkeypatch.setattr(work_lists, "INLINE_ATTACH_LIMIT", 0)                 # adds drain inline: hold them back
    out = _publish(world)                                                      # queued, not drained
    assert {a["state"] for a in out["attach"]} == {"left to the background drain"}
    monkeypatch.setattr(work_lists, "INLINE_ATTACH_LIMIT", cap)
    _remove(world, "r")
    out = _publish(world)
    assert out["removals"]["withdrawn_before_sent"] == ["r"] and out["removals"]["detached"] == []
    assert _detach_rows(world) == []
    _drain(world)
    assert cm.members[out["egeria_guid"]] == {"g-p", "g-q"}
    assert not any(c[0] == "remove_from_collection" for c in cm.calls)


def test_a_member_never_published_to_egeria_is_not_detached(world):
    registry = world["registry"]
    registry.set_egeria_asset_guid("r", "")
    out = _publish(world)
    assert out["members_without_an_egeria_asset"] == ["r"]
    _drain(world)
    _remove(world, "r")
    out = _publish(world)
    assert out["removals"] == {"detached": [], "not_permitted": [], "failed": [], "waiting": [],
                               "withdrawn_before_sent": [], "outbox_row_ids": []}
    assert not any(c[0] == "remove_from_collection" for c in world["cm"].calls)


def test_a_member_added_back_before_its_removal_was_sent_has_the_removal_withdrawn(world):
    out = _publish(world)
    _drain(world)
    registry, guid = world["registry"], out["egeria_guid"]
    queued = registry.enqueue_outbox_element(                                  # a removal of r never sent
        "work_list", world["slug"], "collection_membership_detach", f"CollectionMembership::{guid}::g-r",
        {"collection_guid": guid, "member_guid": "g-r", "entity_type": "work_list", "entity_slug": world["slug"]})
    out = _publish(world)                                                      # r is still a member
    assert registry.get_outbox_element(queued)["status"] == "cancelled"
    assert out["removals"]["detached"] == [] and not any(c[0] == "remove_from_collection" for c in world["cm"].calls)


def _add_row(world, member_guid):
    return [r for r in world["registry"].list_outbox_elements(entity_slug=world["slug"], limit=50)
            if r["element_kind"] == "collection_membership" and r["qualified_name"].endswith(f"::{member_guid}")]


def test_an_attempted_add_that_died_may_have_landed_so_its_member_is_detached(world):
    """A timed-out add may have reached Egeria: attempts > 0 counts as possibly attached (detaching an unlinked
    pair changes nothing in Egeria)."""
    _publish(world)
    registry = world["registry"]
    [row] = _add_row(world, "g-r")
    registry.mark_outbox_failed(row["id"], "ReadTimeout", max_attempts=1)    # dead after one attempt
    _drain(world)
    _remove(world, "r")
    out = _publish(world)
    assert out["removals"]["detached"] == ["r"] and out["removals"]["withdrawn_before_sent"] == []
    assert [c for c in world["cm"].calls if c[0] == "remove_from_collection"] == [
        ("remove_from_collection", out["egeria_guid"], "g-r")]


@pytest.mark.parametrize("words", [
    f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria",
    "refused by Egeria's security: OPEN-METADATA-SECURITY-403-007 User x is not authorized · "
    "needs a permission or zone change, not a retry",
])
def test_a_refused_add_was_never_applied_so_its_member_is_not_detached(world, words):
    _publish(world)
    registry = world["registry"]
    [row] = _add_row(world, "g-r")
    registry.mark_outbox_failed(row["id"], words, max_attempts=1)            # dead, attempts 1, refused
    _drain(world)
    _remove(world, "r")
    for _ in range(2):                                                         # nothing to remove, then or later
        out = _publish(world)
        assert out["removals"]["detached"] == [] and out["removals"]["not_permitted"] == []
    assert _detach_rows(world) == []


def test_an_attempted_add_backing_off_is_cancelled_and_its_member_detached(world):
    _publish(world)
    registry = world["registry"]
    [row] = _add_row(world, "g-r")
    registry.mark_outbox_failed(row["id"], "ReadTimeout", max_attempts=8)    # failed, will retry: attempted once
    _drain(world)
    _remove(world, "r")
    out = _publish(world)
    assert registry.get_outbox_element(row["id"])["status"] == "cancelled"
    assert out["removals"]["detached"] == ["r"] and out["removals"]["withdrawn_before_sent"] == []


def test_a_member_whose_links_were_forgotten_is_not_detached(world):
    """MED-1: still in the list, its asset GUID cleared in RE only (repo_publish.forget_links): not a removal."""
    from resource_explorer.repo_publish import forget_links

    _publish(world)
    _drain(world)
    forget_links(world["registry"], "r", USER)
    out = _publish(world)
    assert out["members_without_an_egeria_asset"] == ["r"]
    assert out["removals"]["detached"] == [] and _detach_rows(world) == []
    assert not any(c[0] == "remove_from_collection" for c in world["cm"].calls)


def test_a_member_whose_asset_now_resolves_to_a_new_guid_has_the_old_one_detached(world):
    _publish(world)
    _drain(world)
    world["registry"].set_egeria_asset_guid("r", "g-r2")
    out = _publish(world)
    assert out["removals"]["detached"] == ["r"]
    assert [c for c in world["cm"].calls if c[0] == "remove_from_collection"] == [
        ("remove_from_collection", out["egeria_guid"], "g-r")]


def test_a_second_press_does_not_queue_a_second_removal(world, monkeypatch):
    _publish(world)
    _drain(world)
    _remove(world, "r")
    monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", lambda *a, **k: {})   # nothing applied inline
    first = _publish(world)
    assert first["removals"]["waiting"] == [{"entity_slug": "r", "words": "queued for the background drain"}]
    second = _publish(world)
    assert second["removals"]["waiting"] == [{"entity_slug": "r", "words": "removal already queued"}]
    [row] = _detach_rows(world)
    assert row["qualified_name"].endswith(f"::g-r::after-add-{_add_row(world, 'g-r')[0]['id']}")


def test_removals_over_the_inline_limit_are_left_to_the_background_drain_and_said(world, monkeypatch):
    _publish(world)
    _drain(world)
    _remove(world, "r")
    monkeypatch.setattr("resource_explorer.work_lists.INLINE_DETACH_LIMIT", 0)
    out = _publish(world)
    assert out["removals"]["waiting"] == [
        {"entity_slug": "r", "words": "left to the background drain (over 0 in one publish)"}]
    assert not any(c[0] == "remove_from_collection" for c in world["cm"].calls)


def test_a_detach_egeria_security_refuses_is_not_permitted_in_egerias_words(world, monkeypatch):
    cm = world["cm"]
    _publish(world)
    _drain(world)
    _remove(world, "r")

    def refuse(*a, **kw):
        REMOVE.bind(cm, *a, **kw)
        raise RuntimeError(SECURITY_403)

    monkeypatch.setattr(cm, "remove_from_collection", refuse)
    out = _publish(world)
    assert out["removals"]["failed"] == []
    [np] = out["removals"]["not_permitted"]
    assert np["entity_slug"] == "r"
    assert np["words"].startswith("not permitted · refused by Egeria's security: OPEN-METADATA-SECURITY-403-007")
    assert _detach_rows(world)[0]["status"] == "dead"


def test_an_add_waits_for_an_earlier_removal_of_the_same_member(registry, monkeypatch):
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    _install(monkeypatch)
    rm = registry.enqueue_outbox_element(
        "work_list", "w", "collection_membership_detach", "CollectionMembership::wg::g-q::after-add-1",
        {"collection_guid": "wg", "member_guid": "g-q"})
    registry.mark_outbox_failed(rm, "transient", max_attempts=8)             # unsettled
    registry.enqueue_outbox_element("work_list", "w", "collection_membership", "CollectionMembership::wg::g-q",
                                    {"collection_guid": "wg", "member_guid": "g-q"})
    cm = SyncCollections()
    drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
    assert not any(c[0] == "add_to_collection" for c in cm.calls)
    add = [r for r in registry.list_outbox_elements(entity_slug="w", limit=5)
           if r["element_kind"] == "collection_membership"][0]
    assert add["status"] == "pending" and int(add["attempts"] or 0) == 0


def test_an_existing_collection_gains_the_link_merged_with_keys_others_added(world, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("resource_explorer.config.WebUIConfig", lambda: SimpleNamespace(public_url=""))
    cm = world["cm"]
    out = _publish(world)                                                      # published before the URL existed
    guid = out["egeria_guid"]
    cm.props[guid] = {"portalNote": "kept"}                                    # someone else's key
    monkeypatch.setattr("resource_explorer.config.WebUIConfig", lambda: SimpleNamespace(public_url=BASE))
    out = _publish(world)
    want = f"https://re.example.org:8810/next?worklist={world['slug']}"
    assert out["link_back"] == {"url": want, "state": "set", "words": ""}
    assert cm.updates[-1][1]["properties"]["additionalProperties"] == {"portalNote": "kept",
                                                                       "resourceExplorerUrl": want}
    assert cm.updates[-1][1]["mergeUpdate"] is True
    assert ("get_collection_by_guid", 0) in cm.calls                          # the collection alone, no graph


def test_a_malformed_public_url_is_treated_as_none(monkeypatch, caplog):
    from resource_explorer.work_lists import resource_explorer_url

    for bad in ("re.example.org:8810", "ftp://re.example.org", "http://", "javascript:alert(1)"):
        monkeypatch.setenv("RE_PUBLIC_URL", bad)
        assert resource_explorer_url("wl-1") == "", bad


def test_a_detach_the_caller_may_not_make_is_refused_with_its_words_and_nothing_is_queued(world):
    cm = world["cm"]
    _publish(world)
    _drain(world)
    _zone(world["fake"], "g-r", ["dbas"])                                      # r now in a zone USER has no grant in
    _remove(world, "r")
    out = _publish(world)
    assert out["removals"]["detached"] == []
    assert out["removals"]["not_permitted"] == [
        {"entity_slug": "r", "words": f"not permitted · zone {SECURED} does not grant {USER} detach in Egeria"}]
    assert _detach_rows(world) == []
    assert not any(c[0] == "remove_from_collection" for c in cm.calls)


def test_a_queued_detach_is_rechecked_at_the_drain_and_a_refusal_is_dead_not_retried(registry, monkeypatch):
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    fake = _install(monkeypatch)
    _zone(fake, "g-q", ["dbas"])
    registry.enqueue_outbox_element(
        "work_list", "w", "collection_membership_detach", "CollectionMembership::wg::g-q",
        {"collection_guid": "wg", "member_guid": "g-q", "member_entity_slug": "q", "entity_type": "work_list",
         "entity_slug": "w", "by": USER})
    cm = SyncCollections()
    summary = drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
    assert cm.calls == [] and summary.get("not_permitted") == 1
    row = registry.list_outbox_elements(entity_slug="w", limit=5)[0]
    assert row["status"] == "dead"
    assert row["last_error"] == f"not permitted · zone {SECURED} does not grant {USER} detach in Egeria"


def test_a_failed_detach_is_dead_at_once_and_said(world, monkeypatch):
    cm = world["cm"]
    _publish(world)
    _drain(world)
    _remove(world, "r")

    def refuse(*a, **kw):
        REMOVE.bind(cm, *a, **kw)
        raise RuntimeError("Egeria said no")

    monkeypatch.setattr(cm, "remove_from_collection", refuse)
    out = _publish(world)
    assert out["removals"]["detached"] == []
    [failed] = out["removals"]["failed"]
    assert failed["entity_slug"] == "r" and failed["words"].startswith("not retried: destructive write")
    assert _detach_rows(world)[0]["status"] == "dead"


def test_no_public_url_leaves_the_key_out_and_sends_no_update(world, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.delenv("RE_PUBLIC_URL", raising=False)
    monkeypatch.setattr("resource_explorer.config.WebUIConfig", lambda: SimpleNamespace(public_url=""))  # no .env
    cm = world["cm"]
    out = _publish(world)
    assert "additionalProperties" not in cm.created[out["egeria_guid"]]["properties"]
    assert out["link_back"] == {"url": "", "state": "not configured", "words": ""}
    _publish(world)
    assert cm.updates == []


def test_the_page_url_matches_the_shape_the_next_page_writes(monkeypatch):
    from resource_explorer.work_lists import resource_explorer_url

    monkeypatch.setenv("RE_PUBLIC_URL", BASE)
    assert resource_explorer_url("wl-1") == "https://re.example.org:8810/next?worklist=wl-1"
    assert resource_explorer_url("wl-1", "database") == "https://re.example.org:8810/next?type=database&worklist=wl-1"


def test_the_detach_waits_for_an_earlier_add_of_the_same_member(registry, monkeypatch):
    """An add still being sent cannot land after the removal: the removal is deferred, nothing sent, no attempt."""
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    _install(monkeypatch)

    add = registry.enqueue_outbox_element("work_list", "w", "collection_membership", "CollectionMembership::wg::g-q",
                                          {"collection_guid": "wg", "member_guid": "g-q"})
    registry.mark_outbox_failed(add, "transient", max_attempts=8)               # failed, backing off: unsettled
    registry.enqueue_outbox_element(
        "work_list", "w", "collection_membership_detach", "CollectionMembership::wg::g-q",
        {"collection_guid": "wg", "member_guid": "g-q", "entity_type": "work_list", "entity_slug": "w"})
    cm = SyncCollections()
    drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
    assert not any(c[0] == "remove_from_collection" for c in cm.calls)
    detach = [r for r in registry.list_outbox_elements(entity_slug="w", limit=5)
              if r["element_kind"] == "collection_membership_detach"][0]
    assert detach["status"] == "pending" and int(detach["attempts"] or 0) == 0


def test_the_detach_kind_is_destructive_and_not_issue_117s():
    from resource_explorer import egeria_outbox as ob

    assert ob.is_destructive_outbox_kind("collection_membership_detach")
    assert "collection_membership_detach" in ob.DESTRUCTIVE_OUTBOX_KINDS
    assert "collection_membership_detach" in ob._SELF_RESOLVING_KINDS     # never adopts a found element instead
