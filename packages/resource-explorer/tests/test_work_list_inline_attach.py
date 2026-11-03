"""A work list's publish applies its membership ADDS inline, as it already applies removals (owner's ruling 2026-10-13).

Before: publish queued one `collection_membership` outbox row per member and only the scheduler loop drained them
(every 900 s), so the person waited up to 15 minutes for members to appear in the WorkingSet. Now the same drain the
scheduler runs is called for each queued row inside the request (`WorkLists._apply_adds`), and each member's words
come from its outbox row afterwards: "attached", "not permitted · <why>", "could not attach · <why>", or "left to
the background drain".

These run the publish ROUTE end to end with a real registry on temp sqlite. The only fake is the pyegeria client
boundary: `egeria_clients._build` (the one place a pyegeria client is constructed) hands back
`create_autospec(<real pyegeria class>, instance=True, spec_set=True)`, so every call is bound against the real
signature and `_default_clients`, `EgeriaPublisher._connect` and `EgeriaPublisher._find_element_guid` are all real.
The zone reads use the shared, signature-bound `FaithfulEgeria` (`_install`), as every Z3 test does. No scheduler
drain is called unless a test says so.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import create_autospec
from tests.qn_lookup_bridge import convert_answer

import pytest

from resource_explorer.registry import DatabaseEntity

sys.path.insert(0, str(Path(__file__).parent))

from test_zone_access_investigations_worklists import (  # noqa: E402,F401  (fixtures are shared)
    USER, _install, _zone, registry, web,
)

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

PLATFORM = "https://localhost:9443"            # an allowed fixture platform (conftest `allow_fixture_platforms`)
MISS = "No elements found"                     # pyegeria's by-name miss


class PyegeriaWorld:
    """What Egeria holds, behind autospec'd pyegeria clients. `build` replaces `egeria_clients._build`."""

    def __init__(self):
        self.collections: dict[str, dict] = {}
        self.members: dict[str, dict[str, dict]] = {}       # collection guid -> member guid -> properties kept
        self.fail_add: dict[str, Exception] = {}            # member guid -> what add_to_collection raises
        self.lookups: list[str] = []                         # every by-name lookup (the real _find_element_guid)
        self.built: list[str] = []

    def build(self, cls, *args):
        inst = create_autospec(cls, instance=True, spec_set=True)
        self.built.append(cls.__name__)
        if cls.__name__ == "CollectionManager":
            inst.create_collection.side_effect = self._create_collection
            inst.add_to_collection.side_effect = self._add_to_collection
        elif cls.__name__ == "MetadataExpert":
            inst.get_related_metadata_elements.side_effect = self._related
            inst.get_metadata_element_by_unique_name.side_effect = (
                lambda name=None, property_name="qualifiedName", body=None, **k: convert_answer(self._guid_for_name(name)))
        elif cls.__name__ == "AutomatedCuration":
            inst.get_guid_for_name.side_effect = self._guid_for_name
        return inst

    def _create_collection(self, display_name=None, description=None, category=None, initial_classifications=None,
                           prop=None, body=None):
        guid = f"0000000{len(self.collections) + 1}-aaaa-bbbb-cccc-dddddddddddd"
        self.collections[guid] = body or {}
        self.members[guid] = {}
        return guid

    def _add_to_collection(self, collection_guid, element_guid, body=None):
        if element_guid in self.fail_add:
            raise self.fail_add[element_guid]
        props = dict((body or {}).get("properties") or {})
        props.pop("class", None)
        self.members[collection_guid][element_guid] = props

    def _related(self, guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None,
                 starting_at_end=0, start_from=0, page_size=500, timeout=30, mermaid_only=False, **kwargs):
        if body.get("startFrom"):
            return MISS
        items = [{"type": {"typeName": "CollectionMembership"}, "elementAtEnd1": True,
                  "element": {"elementGUID": c},
                  "relationshipProperties": {"propertyValueMap": {
                      k: {"primitiveValue": v} for k, v in held[guid].items()}}}
                 for c, held in self.members.items() if guid in held]
        return {"elementList": items} if items else MISS

    def _guid_for_name(self, name, property_name=None, type_name=None):
        self.lookups.append(name)
        return MISS


def _db(registry, slug, guid=""):
    registry.register_database(DatabaseEntity(slug=slug, display_name=slug, db_type="postgresql", host="localhost",
                                              port=5432, database_name=slug))
    if guid:
        registry.set_database_egeria_guid(slug, guid)


@pytest.fixture
def world(web, registry, monkeypatch):
    """A database work list of two published databases, zones open, pyegeria faked at its construction."""
    from resource_explorer import config as re_config
    from resource_explorer import egeria_clients

    monkeypatch.setattr(re_config.get_config().egeria, "platform_url", PLATFORM)
    eg = PyegeriaWorld()
    monkeypatch.setattr(egeria_clients, "_build", eg.build)
    fake = _install(monkeypatch)
    _db(registry, "usda", "g-usda")
    _db(registry, "adventureworks", "g-aw")

    # Every drain call, with what it was handed (proves the real lookup went in, and nothing else drained).
    from resource_explorer import egeria_outbox

    drains, real_drain = [], egeria_outbox.drain_outbox

    def spy(registry_, clients=None, find_element_guid=None, **kw):
        drains.append({"find_element_guid": find_element_guid, **kw})
        return real_drain(registry_, clients, find_element_guid, **kw)

    monkeypatch.setattr(egeria_outbox, "drain_outbox", spy)
    from resource_explorer.work_lists import WorkLists

    WorkLists(registry).create("W", ["usda", "adventureworks"], entity_type="database", created_by=USER,
                               rationale="sales data for the churn study")
    slug = WorkLists(registry).list_all()[0]["slug"]
    return {"client": web[0], "registry": registry, "eg": eg, "fake": fake, "slug": slug, "drains": drains}


def _publish(world):
    r = world["client"].post(f"/api/work-lists/{world['slug']}/publish")
    assert r.status_code == 200, r.text
    return r.json()


def _adds(world):
    return {json.loads(r["payload_json"])["member_entity_slug"]: r
            for r in world["registry"].list_outbox_elements(entity_slug=world["slug"], limit=50)
            if r["element_kind"] == "collection_membership"}


def _by_member(out):
    return {a["entity_slug"]: a for a in out["attach"]}


def test_a_publish_attaches_both_members_before_it_returns(world):
    """The ruling's case: no scheduler drain anywhere, and Egeria already holds both members, with the rationale
    kept (read back by `verify_membership_landed`), and the result says "attached" for each from its done row."""
    out = _publish(world)
    guid = out["egeria_guid"]
    assert world["eg"].members[guid] == {
        "g-usda": {"membershipRationale": "sales data for the churn study"},
        "g-aw": {"membershipRationale": "sales data for the churn study"}}
    att = _by_member(out)
    assert {s: a["words"] for s, a in att.items()} == {"usda": "attached", "adventureworks": "attached"}
    assert all(a["state"] == "attached" and a["status"] == "done" for a in att.values())
    rows = _adds(world)
    assert {s: r["status"] for s, r in rows.items()} == {"usda": "done", "adventureworks": "done"}
    assert all(r["last_error"] in ("", None) for r in rows.values())          # read back, every property kept
    assert all(json.loads(r["payload_json"])["by"] == USER for r in rows.values())  # as the person who pressed
    # One scoped drain per queued row, and no unscoped (scheduler) drain.
    assert sorted(d["element_id"] for d in world["drains"]) == sorted(a["outbox_row_id"] for a in out["attach"])


def test_the_real_find_element_guid_is_passed_never_a_lookup_that_answers_blank(world):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    out = _publish(world)
    assert world["drains"], "the adds were drained inline"
    for d in world["drains"]:
        f = d["find_element_guid"]
        assert getattr(f, "__func__", None) is EgeriaPublisher._find_element_guid, f
    # And it ran: the lookup-then-create step asked Egeria by name for each membership, through pyegeria.
    guid = out["egeria_guid"]
    assert sorted(world["eg"].lookups) == sorted(
        [f"CollectionMembership::{guid}::g-usda", f"CollectionMembership::{guid}::g-aw"])


def test_a_member_refused_at_the_drain_recheck_is_not_permitted_and_not_attached(world, monkeypatch):
    """The zone tightens between the press-time check and the drain: the drain's Z3 re-check (as the person who
    pressed publish) refuses it, nothing is sent for it, and its words say why; the other member is attached."""
    from resource_explorer import egeria_outbox

    real = egeria_outbox._queued_write_refusal

    def tightened(registry_, row, *a, **k):
        _zone(world["fake"], "g-aw", ["dbas"])               # SECURED, granting dbas only, not garygeeke
        return real(registry_, row, *a, **k)

    monkeypatch.setattr(egeria_outbox, "_queued_write_refusal", tightened)
    out = _publish(world)
    att = _by_member(out)
    assert att["usda"]["state"] == "attached"
    assert att["adventureworks"]["state"] == "not permitted"
    assert att["adventureworks"]["words"].startswith("not permitted · ")
    assert "g-aw" not in world["eg"].members[out["egeria_guid"]]
    assert _adds(world)["adventureworks"]["status"] == "dead"            # permanent until a zone changes


def test_a_failing_add_stays_for_the_background_retry_and_the_publish_still_succeeds(world):
    world["eg"].fail_add["g-aw"] = RuntimeError("Egeria answered 500: the repository is busy")
    out = _publish(world)                                                  # 200: asserted in _publish
    att = _by_member(out)
    assert att["usda"]["state"] == "attached"
    assert att["adventureworks"]["state"] == "could not attach"
    assert att["adventureworks"]["words"] == \
        "could not attach · RuntimeError: Egeria answered 500: the repository is busy"
    assert att["adventureworks"]["will_retry"] is True
    row = _adds(world)["adventureworks"]
    assert row["status"] == "failed" and row["attempts"] == 1            # backing off; the scheduler sends it again


def test_adds_over_the_inline_cap_are_left_to_the_background_drain_and_said(world, monkeypatch):
    monkeypatch.setattr("resource_explorer.work_lists.INLINE_ATTACH_LIMIT", 1)
    out = _publish(world)
    first, second = out["attach"]
    assert first["state"] == "attached"
    assert second == {"entity_slug": second["entity_slug"], "outbox_row_id": second["outbox_row_id"],
                      "status": "pending", "state": "left to the background drain",
                      "words": "left to the background drain · over 1 in one publish"}
    assert len(world["drains"]) == 1
    assert len(world["eg"].members[out["egeria_guid"]]) == 1


def test_a_drain_that_raises_fails_no_publish_and_leaves_the_rows_pending(world, monkeypatch):
    from resource_explorer import egeria_outbox

    def boom(*a, **kw):
        raise RuntimeError("drain blew up")

    monkeypatch.setattr(egeria_outbox, "drain_outbox", boom)
    out = _publish(world)
    assert {a["state"] for a in out["attach"]} == {"left to the background drain"}
    assert {r["status"] for r in _adds(world).values()} == {"pending"}
    assert out["attach_error"].startswith("the inline send failed for 2 member(s) · left to the background drain · ")
    assert "usda: RuntimeError: drain blew up" in out["attach_error"]


def test_a_publish_whose_inline_drains_all_ran_says_no_attach_error(world):
    assert _publish(world)["attach_error"] == ""


def test_a_publish_with_no_look_up_fails_loudly_before_anything_is_written(world, monkeypatch):
    """A drain handed no look-up cannot tell "absent" from "could not check": publish says so and writes nothing."""
    from resource_explorer import egeria_outbox

    real = egeria_outbox._default_clients
    monkeypatch.setattr(egeria_outbox, "_default_clients", lambda who=None: (real(who)[0], None))
    r = world["client"].post(f"/api/work-lists/{world['slug']}/publish")
    assert r.status_code == 502, r.text
    assert "no Egeria look-up to check what already exists" in r.json()["detail"]
    assert world["eg"].collections == {} and _adds(world) == {} and world["drains"] == []
