"""A published database is related to its server's DatabaseManager (CapabilityAssetUse, useType OWNS), read first.

`database_server_link` mirrors Egeria's PostgreSQL server connector (`PostgresServerCatalogTargetProcessor`
lines 370-426): the capability is the parent at end 1, the database the asset at end 2, `useType` OWNS. The
type is NOT multi-link (Egeria refuses a second link between the same pair), so the read before the create
gives the words and saves a round-trip; Egeria backstops the race.

Every test runs the REAL `database_server_link` against an autospec'd `MetadataExpert` / `AssetMaker`
(`create_autospec(..., spec_set=True)`, so a call with the wrong signature fails), kept by `FakeEgeria`. The fake does NOT
refuse a second create (Egeria would), so a test that sees one proves RE's own read-first, not Egeria's backstop.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# the commit fixtures and helpers, exactly as the commit tests use them
from test_catalogue_commit import (  # noqa: E402,F401
    ME, SOURCE, _curation_access_reads, _zones, choose, derived, entity, fake, press, registry, step, world)

from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer import database_server_link as dsl  # noqa: E402
from resource_explorer.registry import DatabaseEntity  # noqa: E402

pytestmark = pytest.mark.usefixtures("signed_in_caller")

SERVER = "host.docker.internal:5442"


def adds(fake):
    return [c for c in fake.calls if c[0] == "add_capability_asset_use"]


# ── the commit step ───────────────────────────────────────────────────────────


def test_a_fresh_database_is_linked_to_its_servers_database_manager_with_owns(world, fake):
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake, refresh=False)
    dm = fake.dm_of[fake.server_guid]
    assert len(fake.capability_links) == 1
    link = fake.capability_links[0]
    # end 1 is the DatabaseManager capability, end 2 the database (the Java type def), never the server
    assert (link["capability"], link["asset"]) == (dm, fake.db_guid)
    assert fake.server_guid not in (link["capability"], link["asset"])
    assert link["body"] == {"class": "NewRelationshipRequestBody",
                            "properties": {"class": "CapabilityAssetUseProperties", "useType": "OWNS"}}
    detail = step(rec, "publish_elements")["detail"]
    assert step(rec, "publish_elements")["state"] == "done"
    assert f"linked to {SERVER} via DatabaseManager {dm[:8]}" in detail
    # the sentence is also on the database proof (JSON detail, no new column)
    proofs = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc_p_database()]
    assert proofs and proofs[-1]["detail"]["server_link"]["outcome"] == dsl.LINKED


def cc_p_database():
    from resource_explorer import catalogue_commit as cc
    return cc.P_DATABASE


def test_the_adopt_path_links_a_database_that_already_existed_without_one(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    fake.capability_links.clear()                  # an element RE published before this change: no edge
    fake.calls.clear()
    out, rec = press(world, fake, refresh=False)
    assert len(fake.capability_links) == 1
    assert "linked to " in step(rec, "publish_elements")["detail"]


def test_an_already_linked_database_is_read_and_not_linked_again(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    fake.calls.clear()
    out, rec = press(world, fake, refresh=False)
    assert adds(fake) == [] and len(fake.capability_links) == 1
    detail = step(rec, "publish_elements")["detail"]
    assert detail.endswith("already linked") and "linked to " not in detail


def test_a_second_commit_never_makes_a_second_link__and_the_check_bites(world, fake, monkeypatch):
    """Fail on purpose: the same two commits DO make a duplicate when the read-first is removed, so the
    assertion above is a guard and not a coincidence."""
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    press(world, fake, refresh=False)
    assert len(fake.capability_links) == 1                       # with the read: one
    fake.capability_links.clear()
    real_related = dsl.related_entries
    monkeypatch.setattr(dsl, "related_entries",
                        lambda e, rtype, g, end, **k: [] if rtype == dsl.RELATIONSHIP_TYPE else real_related(e, rtype, g, end, **k))
    press(world, fake, refresh=False)
    press(world, fake, refresh=False)
    assert len(fake.capability_links) == 2                       # without it: the bug this guard prevents


def test_a_read_that_cannot_be_made_never_creates_and_says_so(world, fake):
    choose(world, "sales", "catalogue")
    fake.link_read_error = RuntimeError("503 from the view server")
    out, rec = press(world, fake, refresh=False)
    assert adds(fake) == [] and fake.capability_links == []
    s = step(rec, "publish_elements")
    assert s["state"] == "done"                                  # a missing link never fails the publish
    assert "could not check" in s["detail"] and "503 from the view server" in s["detail"]
    assert "not linked" in s["detail"]


def test_a_server_without_a_database_manager_gets_no_link_and_no_new_capability(world, fake):
    choose(world, "sales", "catalogue")
    fake.server_has_dm = False
    out, rec = press(world, fake, refresh=False)
    assert fake.capability_links == [] and adds(fake) == []
    assert dsl.NO_CAPABILITY_WORDS == "server has no DatabaseManager capability · not linked"
    assert dsl.NO_CAPABILITY_WORDS in step(rec, "publish_elements")["detail"]
    # RE created no capability (or anything else) through pyegeria: the maker was never called at all
    assert fake.pyegeria_maker.mock_calls == []


def test_a_database_used_by_another_capability_is_not_linked_to_this_server(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    fake.capability_links[:] = [{"rel": "r-other", "capability": "other-capability-guid", "asset": fake.db_guid}]
    fake.calls.clear()
    out, rec = press(world, fake, refresh=False)
    assert adds(fake) == [] and len(fake.capability_links) == 1
    assert ("not linked: the database is already used by capability other-ca"
            " (not this server's DatabaseManager)") in step(rec, "publish_elements")["detail"]


def test_a_refused_link_is_a_not_linked_sentence_and_the_step_is_still_done(world, fake):
    choose(world, "sales", "catalogue")
    fake.link_create_error = PermissionError("OPEN-METADATA-SECURITY-0011 not authorized")
    out, rec = press(world, fake, refresh=False)
    s = step(rec, "publish_elements")
    assert s["state"] == "done" and "not linked: could not link" in s["detail"]
    assert "OPEN-METADATA-SECURITY-0011" in s["detail"] and fake.capability_links == []


def test_a_gateway_that_fails_outright_is_could_not_check_and_never_fails_the_step(world, fake):
    choose(world, "sales", "catalogue")
    fake.fail["link_database_to_server"] = "boom"
    out, rec = press(world, fake, refresh=False)
    s = step(rec, "publish_elements")
    assert s["state"] == "done" and "could not check" in s["detail"] and "not linked" in s["detail"]


# ── the function itself ───────────────────────────────────────────────────────


def test_the_oldest_database_manager_is_used_and_a_dry_run_writes_nothing(fake):
    fake.dm_of["srv-1"] = "mgr-1"
    out = dsl.link_database_to_server(fake.pyegeria_expert, fake.pyegeria_maker, server_guid="srv-1",
                                      database_guid="db-1", server_label="h:1", write=False)
    assert out.outcome == dsl.WOULD_LINK and out.words == "would link to h:1 via DatabaseManager mgr-1"
    assert fake.capability_links == [] and adds(fake) == []


def test_an_entry_without_a_capability_guid_is_could_not_check_never_a_create(fake):
    fake.dm_of["srv-1"] = "mgr-1"
    fake.capability_links.append({"rel": "r1", "capability": "", "asset": "db-1"})
    out = dsl.link_database_to_server(fake.pyegeria_expert, fake.pyegeria_maker, server_guid="srv-1",
                                      database_guid="db-1")
    assert out.outcome == dsl.COULD_NOT_CHECK and adds(fake) == []


def test_missing_ends_are_not_linked(fake):
    assert dsl.link_database_to_server(fake.pyegeria_expert, fake.pyegeria_maker, server_guid="",
                                       database_guid="db").outcome == dsl.NOT_LINKED
    assert dsl.link_database_to_server(fake.pyegeria_expert, fake.pyegeria_maker, server_guid="s",
                                       database_guid="").outcome == dsl.NOT_LINKED
    assert fake.calls == []


def test_the_real_gateway_method_uses_the_real_clients_in_the_documented_order(fake):
    g = gw.PyegeriaCatalogueGateway(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql",
                                                   host="localhost", egeria_host="host.docker.internal", port=5442,
                                                   database_name="shop"))
    g._clients.update({"MetadataExpert": fake.pyegeria_expert, "AssetMaker": fake.pyegeria_maker})
    fake.dm_of["srv-1"] = "mgr-1"
    out = g.link_database_to_server("srv-1", "db-1", server_name="h:1")
    assert out.outcome == dsl.LINKED
    fake.pyegeria_maker.add_capability_asset_use.assert_called_once()
    args = fake.pyegeria_maker.add_capability_asset_use.call_args[0]
    assert args[:2] == ("mgr-1", "db-1")


# ── the heal ──────────────────────────────────────────────────────────────────


def _db(slug, name, guid=""):
    return DatabaseEntity(slug=slug, display_name=name, db_type="postgresql", host="localhost",
                          egeria_host="host.docker.internal", port=5442, database_name=name, egeria_asset_guid=guid)


@pytest.fixture
def heal_world(tmp_path, fake):
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "heal.db"))
    assert str(tmp_path) in reg.database_url
    def element(qn, type_name):
        g = str(uuid.uuid4())                 # the unique-name lookup rejects an answer whose GUID is not one
        fake.elements[g] = {"qn": qn, "type": type_name, "parent": "", "archived": False, "deleted": False}
        return g

    fake.server_guid = element(f"PostgreSQL Server::{SERVER}", "SoftwareServer")
    fake.dm_of[fake.server_guid] = "mgr-1"
    guids = {}
    for slug in ("unlinked", "linked", "unreadable"):
        g = element(f"PostgreSQL Relational Database::{SERVER}::{slug}", "RelationalDatabase")
        guids[slug] = g
        reg.register_database(_db(slug, slug, g))
    reg.register_database(_db("never_published", "never_published", ""))
    fake.capability_links.append({"rel": "r-already", "capability": "mgr-1", "asset": guids["linked"]})
    fake.lookup_errors[f"PostgreSQL Relational Database::{SERVER}::unreadable"] = TimeoutError("30 s")
    real = gw.PyegeriaCatalogueGateway(_db("x", "x"))
    real._clients.update({"MetadataExpert": fake.pyegeria_expert, "AssetMaker": fake.pyegeria_maker})
    return reg, real, guids


def test_the_heal_links_the_unlinked_and_reports_linked_already_and_could_not_check(heal_world, fake):
    reg, real, guids = heal_world
    from resource_explorer.repair import link_database_servers

    report = link_database_servers(reg, gateway_for=lambda db: real)
    assert (report.linked, report.already_linked, report.could_not_check, report.not_linked) == (1, 1, 1, 0)
    assert report.summary() == "linked 1, already linked 1, could not check 1"
    assert {r["slug"]: r["outcome"] for r in report.rows} == {
        "unlinked": dsl.LINKED, "linked": dsl.ALREADY_LINKED, "unreadable": dsl.COULD_NOT_CHECK}
    assert "never_published" not in {r["slug"] for r in report.rows}        # no egeria_asset_guid: not walked
    assert sorted(l["asset"] for l in fake.capability_links) == sorted([guids["linked"], guids["unlinked"]])
    assert len(adds(fake)) == 1                                              # nothing for linked or unreadable


def test_a_second_heal_makes_no_link(heal_world, fake):
    reg, real, guids = heal_world
    from resource_explorer.repair import link_database_servers

    link_database_servers(reg, gateway_for=lambda db: real)
    n = len(fake.capability_links)
    again = link_database_servers(reg, gateway_for=lambda db: real)
    assert len(fake.capability_links) == n and again.linked == 0 and again.already_linked == 2


def test_a_dry_run_heal_reads_and_writes_nothing(heal_world, fake):
    reg, real, _ = heal_world
    from resource_explorer.repair import link_database_servers

    report = link_database_servers(reg, dry_run=True, gateway_for=lambda db: real)
    assert report.would_link == 1 and report.linked == 0 and adds(fake) == []
    assert report.summary().startswith("would link 1")


def test_the_heal_refuses_a_pointer_that_disagrees_with_egeria(heal_world, fake):
    reg, real, guids = heal_world
    reg.set_database_egeria_guid("unlinked", "some-other-guid")
    from resource_explorer.repair import link_database_servers

    report = link_database_servers(reg, gateway_for=lambda db: real)
    row = next(r for r in report.rows if r["slug"] == "unlinked")
    assert row["outcome"] == dsl.NOT_LINKED and "RE points at some-oth" in row["words"] and report.linked == 0


def test_the_heal_names_the_daemon_as_the_identity(heal_world, monkeypatch):
    reg, real, _ = heal_world
    from resource_explorer import egeria_clients as ec
    from resource_explorer.repair import link_database_servers

    monkeypatch.setattr(ec, "_daemon_credential", lambda: ("resourceexplorernpa", "pw"))
    report = link_database_servers(reg, gateway_for=lambda db: real)
    assert report.acting_as.startswith("resourceexplorernpa (RE daemon, reason egeria_resync")


def test_nothing_at_start_up_runs_the_heal():
    """It runs only on a press or a command: the only callers are the repair CLI and the admin route."""
    root = Path(__file__).parent.parent / "resource_explorer"
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if "link_database_servers" in p.read_text() or "heal_database_server_links" in p.read_text())
    assert callers == ["cli/main.py", "database_server_link.py", "repair.py", "web/routes/repair.py"]


def test_the_admin_route_refuses_a_non_admin_and_runs_for_an_admin(monkeypatch):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from resource_explorer.database_server_link import HealReport
    from resource_explorer.web.app import app

    client = TestClient(app)
    monkeypatch.setattr("resource_explorer.web.admin_auth.is_admin_request", lambda *a, **k: False)
    monkeypatch.setattr("resource_explorer.web.admin_auth.admin_configured", lambda *a, **k: True)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda *a, **k: {"sub": "dan"})
    with patch("resource_explorer.repair.link_database_servers") as heal:
        assert client.post("/api/admin/repair/database-server-links").status_code == 403
        heal.assert_not_called()
        monkeypatch.setattr("resource_explorer.web.admin_auth.is_admin_request", lambda *a, **k: True)
        heal.return_value = HealReport(acting_as="svc", linked=2, already_linked=1, could_not_check=0)
        res = client.post("/api/admin/repair/database-server-links?dry_run=true")
    assert res.status_code == 200 and res.json()["summary"] == "linked 2, already linked 1, could not check 0"
    assert heal.call_args.kwargs == {"dry_run": True}


# ── fix round 1 (2026-10-22) ──────────────────────────────────────────────────

def test_the_read_first_pages_to_the_end_and_finds_the_match_on_page_two(fake):
    """Two pages of CapabilityAssetUse links, the server's DatabaseManager on page 2 -> "already linked"."""
    from unittest.mock import create_autospec

    from pyegeria import AssetMaker, MetadataExpert

    from resource_explorer.egeria_link_once import PAGE_SIZE

    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    maker = create_autospec(AssetMaker, spec_set=True, instance=True)

    def link(cap, rel):
        return {"relationshipGUID": rel, "type": {"typeName": "CapabilityAssetUse"}, "elementAtEnd1": True,
                "versions": {"createTime": "2026-10-02T00:00:00.000+00:00"},
                "element": {"elementGUID": cap, "type": {"typeName": "SoftwareCapability"}}}

    page1 = [link(f"other-{i}", f"r{i}") for i in range(PAGE_SIZE)]
    page2 = [link("mgr-1", "r-ours")]
    seen = []

    def related(guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None,
                starting_at_end=0, start_from=0, page_size=500, *a, **k):
        seen.append((relationship_type, start_from))
        if relationship_type == "SupportedSoftwareCapability":
            return {"elementList": [{"relationshipGUID": "ssc", "type": {"typeName": relationship_type},
                                     "versions": {"createTime": "2026-10-01T00:00:00.000+00:00"},
                                     "element": {"elementGUID": "mgr-1",
                                                 "type": {"typeName": "DatabaseManager",
                                                          "superTypeNames": ["SoftwareCapability"]}}}]} \
                if start_from == 0 else "No elements found"
        return {"elementList": {0: page1, PAGE_SIZE: page2}.get(start_from, [])} \
            if start_from in (0, PAGE_SIZE) else "No elements found"

    expert.get_related_metadata_elements.side_effect = related
    out = dsl.link_database_to_server(expert, maker, server_guid="srv", database_guid="db")
    assert out.outcome == dsl.ALREADY_LINKED and out.relationship_guid == "r-ours"
    assert ("CapabilityAssetUse", PAGE_SIZE) in seen            # page 2 was read
    maker.add_capability_asset_use.assert_not_called()


def test_a_wrong_type_element_under_the_database_name_is_could_not_check_never_a_target(heal_world, fake):
    reg, real, guids = heal_world
    fake.elements[guids["unlinked"]]["type"] = "Schema"
    from resource_explorer.repair import link_database_servers

    report = link_database_servers(reg, gateway_for=lambda db: real)
    row = next(r for r in report.rows if r["slug"] == "unlinked")
    assert row["outcome"] == dsl.COULD_NOT_CHECK and adds(fake) == []


def test_the_heal_shares_one_gateway_across_the_walk_and_logs_who_before_writing(heal_world, fake, monkeypatch,
                                                                                caplog):
    reg, real, _ = heal_world
    from resource_explorer import catalogue_gateway as cg
    from resource_explorer import egeria_clients as ec
    from resource_explorer.repair import link_database_servers

    monkeypatch.setattr(ec, "_daemon_credential", lambda: ("resourceexplorernpa", "pw"))
    built = []
    real_cls = cg.PyegeriaCatalogueGateway

    class Counting(real_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._clients.update({"MetadataExpert": fake.pyegeria_expert, "AssetMaker": fake.pyegeria_maker})
            built.append(self)

    monkeypatch.setattr(cg, "PyegeriaCatalogueGateway", Counting)
    with caplog.at_level("INFO"):
        report = link_database_servers(reg)
    assert len(built) == 1 and report.linked == 1
    msgs = [r.getMessage() for r in caplog.records]
    first_write = next(i for i, m in enumerate(msgs) if "egeria write BEFORE" in m)
    acting = next(i for i, m in enumerate(msgs) if "heal starting: acting as resourceexplorernpa (RE daemon" in m)
    assert acting < first_write
