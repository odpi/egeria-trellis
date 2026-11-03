"""Rulings after the read-back of 2026-10-05 (zones, the schema action type, engine action properties).

Step 2 stays RE's template creation (deterministic qualifiedName), then attaches SCHEMA-kind through
Egeria's own action type `PostgreSQLGovernance::catalog-postgres-schema` (action target `newAsset`),
falling back to `add_catalog_target`; no zone is written (none exists by default on this build); an
engine action still running on a schema blocks THAT schema's leave-out; non-deterministic GUIDs are
stored on proof rows, never rebuilt. Everything against the fake Egeria and the recorded live shapes."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_catalogue_payloads as live  # noqa: E402
from test_catalogue_commit import (  # noqa: E402,F401
    ME, SOURCE, _zones, choose, derived, entity, fake, press, registry, step, view, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer.registry import DatabaseEntity  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

SALES_QN = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cc, "_sleep", lambda s: None)


@pytest.fixture
def no_zone_config(monkeypatch):
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
    from resource_explorer.config import get_config
    monkeypatch.setattr(get_config().egeria, "default_catalog_zones", [])


def target_proofs(world):
    return [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_TARGET]


# ── (1) step 2: template creation, then Egeria's own attach ──────────────────

def test_the_schema_is_created_from_the_template_first_then_attached_by_the_action_type(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    names = [c[0] for c in fake.calls]
    assert names.index("create_schema_element") < names.index("initiate_catalog_action")
    assert fake.by_qn(SALES_QN) is not None                      # the deterministic qualifiedName
    assert fake.ops("add_catalog_target") == []                  # the fallback was not needed
    _, guid, params = fake.ops("initiate_catalog_action")[0]
    assert guid == fake.by_qn(SALES_QN)["guid"]
    assert params["schemaName"] == "sales" and params["databaseName"] == "shop"
    assert params["serverName"] == "host.docker.internal:5442" and params["versionIdentifier"]
    assert {"hostIdentifier", "portNumber", "schemaDescription", "secretsCollectionName",
            "secretsStorePathName"} <= set(params)
    assert "~{" not in str(params) and "templateGUID" not in params
    assert len(fake.targets) == 1 and fake.targets[0].element_guid == guid


def test_the_proof_row_names_the_mechanism_and_keeps_the_engine_action_guid(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    d = [p for p in target_proofs(world) if "mechanism" in p["detail"]][0]["detail"]
    assert d["mechanism"] == "action_type" and d["action_type"] == live.ACTION_TYPE_QN
    assert d["engine_action"] in fake.actions                    # stored at submission, never reconstructed


@pytest.mark.parametrize("mode", ["refuse", "error"])
def test_when_the_action_type_refuses_or_errors_it_falls_back_to_add_catalog_target(world, fake, mode):
    fake.catalog_action = mode
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "schema_targets")["state"] == "done"
    assert len(fake.ops("add_catalog_target")) == 1 and len(fake.targets) == 1
    d = [p for p in target_proofs(world) if "mechanism" in p["detail"]][0]["detail"]
    assert d["mechanism"] == "add_catalog_target" and d["fallback_reason"]
    if mode == "refuse":
        assert "not acceptable" in d["fallback_reason"] and "Details follow" not in d["fallback_reason"]


def test_the_target_is_never_attached_twice_across_commits_and_mechanisms(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    press(world, fake, refresh=False)
    assert len(fake.targets) == 1 and len(fake.ops("initiate_catalog_action")) == 1
    assert fake.ops("add_catalog_target") == []
    assert [p["detail"]["mechanism"] for p in target_proofs(world) if "mechanism" in p["detail"]] == \
        ["action_type", "already_attached"]


def test_a_still_running_action_is_neither_a_success_nor_a_reason_to_attach_a_second_time(world, fake):
    fake.catalog_action = "pending"
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "schema_targets")["state"] == "failed"
    assert "still" in step(rec, "schema_targets")["detail"] and fake.ops("add_catalog_target") == []
    assert fake.targets == []
    fake.finish_pending_actions()                                 # Egeria attaches it on its own
    press(world, fake, refresh=False)
    assert len(fake.targets) == 1 and fake.ops("add_catalog_target") == []


def test_an_action_that_completed_without_a_target_is_an_error_not_a_second_attach(world, fake):
    choose(world, "sales", "catalogue")
    orig = fake.initiate_catalog_action

    def lose_target(guid, params):
        out = orig(guid, params)
        fake.targets.clear()
        return out
    fake.initiate_catalog_action = lose_target
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "schema_targets")["state"] == "failed" and fake.ops("add_catalog_target") == []


# ── the real gateway's wire shapes for the action type and engine action ────

@pytest.fixture
def real():
    ent = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                         egeria_host="host.docker.internal", port=5442, database_name="shop")
    g = gw.PyegeriaCatalogueGateway(ent)
    c = {n: MagicMock(name=n) for n in ("AssetMaker", "MetadataExpert", "AutomatedCuration", "ServerOps")}
    g._clients.update(c)
    return g, c, ent


def test_the_gateway_initiates_the_action_type_with_new_asset_and_the_template_parameters(real):
    g, c, ent = real
    ac = c["AutomatedCuration"]
    ac.ref_curation_command_base = "http://x/api"
    resp = MagicMock()
    resp.json.return_value = live.LIVE_INITIATE_RESPONSE

    def make(method, url, body):
        make.seen = (method, url, body)
        return resp
    ac.make_request = make
    params = gw.schema_placeholders(ent, "sales")
    guid = g.initiate_catalog_action("schema-guid", params)
    method, url, body = make.seen
    assert guid == live.LIVE_INITIATE_RESPONSE["guid"] and url.endswith("/governance-action-types/initiate")
    assert body["governanceActionTypeQualifiedName"] == live.ACTION_TYPE_QN == gw.CATALOG_SCHEMA_ACTION_TYPE
    assert body["actionTargets"] == [{"class": "NewActionTarget", "actionTargetName": "newAsset",
                                      "actionTargetGUID": "schema-guid"}]
    assert body["requestParameters"] == params and params["schemaName"] == "sales"


def test_an_initiate_answer_with_no_guid_is_an_error():
    with pytest.raises(gw.GatewayError):
        gw.parse_initiate_answer({"class": "GUIDResponse", "relatedHTTPCode": 200})
    with pytest.raises(gw.GatewayError):
        gw.parse_initiate_answer({"guid": "Action not initiated"})


def test_the_engine_action_status_is_activity_status_not_action_status(real):
    g, c, _ = real
    c["MetadataExpert"].get_metadata_element_by_guid.return_value = live.raw_engine_action(
        "ea", "FAILED", message="OMES-SURVEY-ACTION-0018 it threw. More.", completion_ms="1791237499898")
    st = g.engine_action_status("ea")
    assert (st.status, st.completion_time) == ("FAILED", "1791237499898") and st.message.startswith("OMES-SURVEY-ACTION-0018")
    assert gw.parse_engine_action_answer(live.raw_engine_action("ea", "IN_PROGRESS")).status == "IN_PROGRESS"
    # a missing activityStatus is "not stated yet" (never read from the wrong name), an unrecognised element raises
    assert gw.parse_engine_action_answer({"elementGUID": "x", "elementProperties": {"propertiesAsStrings": {"actionStatus": "FAILED"}}}).status == ""
    with pytest.raises(gw.GatewayError):
        gw.parse_engine_action_answer({"unexpected": 1})


def test_the_fake_s_initiation_and_engine_action_answers_have_the_live_shapes(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    fake.engine_action_status(next(iter(fake.actions)))
    assert fake.last_wire[0] == "engine_action" and "activityStatus" in fake.last_wire[1]["elementProperties"]["propertiesAsStrings"]
    assert set(live.LIVE_INITIATE_RESPONSE) == {"class", "requestId", "relatedHTTPCode", "guid"}


# ── (2) zones ────────────────────────────────────────────────────────────────

def test_the_header_says_none_everyone_visible_when_the_read_back_shows_no_zone(world, fake, no_zone_config):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    d = derived(world)
    assert "zones: none · everyone visible" in d["header"]["text"]
    assert d["database"]["zones"] == [] and d["database"]["zones_text"] == "zones: none · everyone visible"
    assert fake.ops("set_zone_membership") == []


def test_no_zone_read_at_all_says_not_read_back_never_none(world, fake, no_zone_config):
    choose(world, "sales", "catalogue")
    fake.fail["read_zones"] = "500 unreadable"
    press(world, fake, refresh=False)
    t = derived(world)["header"]["text"]
    assert "zones: not read back" in t and "everyone visible" not in t


def test_the_gateway_reads_zones_from_the_raw_classifications_and_none_is_an_empty_list(real):
    g, c, _ = real
    me = c["MetadataExpert"].get_metadata_element_by_guid
    me.return_value = live.raw_element("g", "qn", "RelationalDatabase")
    assert g.read_zones("g") == []
    me.return_value = live.raw_element("g", "qn", "RelationalDatabase", zones=["a", "b"])
    assert g.read_zones("g") == ["a", "b"]
    el = live.raw_element("g", "qn", "RelationalDatabase")
    el["classifications"] = [{"classificationName": "ZoneMembership", "classificationProperties": {
        "propertyValueMap": {"zoneMembership": {"arrayValues": {"propertiesAsStrings": {"1": "b", "0": "a"}}}}}}]
    me.return_value = el
    assert g.read_zones("g") == ["a", "b"]
    me.return_value = {"unexpected": 1}
    with pytest.raises(gw.GatewayError):
        g.read_zones("g")


# ── (3) an engine action still running blocks THAT schema's leave-out ───────

def _catalogued(world, fake, *names):
    """Attached, NOT yet cataloged: a cataloged schema (tables read back) always archives, which these tests are not about."""
    for n in names:
        choose(world, n, "catalogue")
    press(world, fake, refresh=False)
    for n in names:
        choose(world, n, "leave_out")


@pytest.mark.parametrize("status", ["REQUESTED", "APPROVED", "IN_PROGRESS"])
def test_a_running_engine_action_blocks_that_schema_only(world, fake, status):
    _catalogued(world, fake, "sales", "archive")
    fake.add_engine_action(SALES_QN, status)
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    rows = {r["schema"]: r for r in p["leave_out"]}
    assert rows["sales"]["blocked"] and rows["sales"]["form"] == "in_use"
    assert rows["sales"]["text"].startswith("sales: in use by a running survey · wait or cancel")
    assert status in rows["sales"]["text"]
    assert not rows["archive"]["blocked"] and rows["archive"]["form"] == gw.SOFT_DELETE
    assert p["blocked_schemas"] == ["sales"] and p["can_commit"]                   # the rest proceeds
    assert "1 schema not committed: sales · in use by a running survey · wait or cancel" in \
        [ln["text"] for ln in p["manifest"]["lines"]]
    assert [x["schema"] for x in cc.start_commit(world["registry"], "db", ME, gateway=fake)["preview"]["leave_out"]]


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED"])
def test_a_finished_engine_action_does_not_block_and_is_not_something_that_hangs_off(world, fake, status):
    _catalogued(world, fake, "sales")
    fake.add_engine_action(SALES_QN, status, completion_time="1791237500067")
    row = cc.build_preview(world["registry"], "db", view(world), fake)["leave_out"][0]
    assert not row["blocked"] and row["form"] == gw.SOFT_DELETE and row["hangs_off"]["total"] == 0


def test_an_unknown_activity_status_blocks_and_shows_the_value(world, fake):
    _catalogued(world, fake, "sales")
    fake.add_engine_action(SALES_QN, "SOMETHING_NEW")
    row = cc.build_preview(world["registry"], "db", view(world), fake)["leave_out"][0]
    assert row["blocked"] and "SOMETHING_NEW" in row["text"] and "wait or cancel" in row["text"]


def test_completion_time_and_message_go_on_the_row_when_present(world, fake):
    _catalogued(world, fake, "sales")
    fake.add_engine_action(SALES_QN, "IN_PROGRESS", completion_time="1791237500067", message="Halfway. More follows.")
    row = cc.build_preview(world["registry"], "db", view(world), fake)["leave_out"][0]
    assert row["in_use"][0]["status"] == "IN_PROGRESS" and row["in_use"][0]["completion_time"] == "1791237500067"
    assert row["in_use"][0]["message"] == "Halfway. More follows." and "Halfway." in row["text"]


def test_the_block_holds_at_press_time_and_deletes_nothing(world, fake):
    _catalogued(world, fake, "sales")
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)           # preview was fine
    fake.add_engine_action(SALES_QN, "IN_PROGRESS")                           # a survey started meanwhile
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert step(rec, "leave_outs")["state"] == "failed" and "in use by a running survey" in step(rec, "leave_outs")["detail"]
    assert fake.deleted_order == [] and fake.by_qn(SALES_QN) is not None


def test_a_failed_relationships_read_still_means_couldn_t_check_not_in_use(world, fake):
    _catalogued(world, fake, "sales")
    fake.fail["relationships"] = "500 no"
    row = cc.build_preview(world["registry"], "db", view(world), fake)["leave_out"][0]
    assert row["blocked"] and row["form"] == "cannot_check"


def test_the_gateway_reads_the_live_action_target_item(real):
    g, c, ent = real
    schema = live.raw_element("sch1", SALES_QN, "DeployedDatabaseSchema")
    ea = live.raw_engine_action("dc40606d", "COMPLETED", message="OMES-SURVEY-ACTION-0019 done", completion_ms="1791237500067")
    item = ("ActionTarget", "c763e40f", ea, live.relationship_properties("serverToSurvey", "COMPLETED", "1791237500067"))
    c["MetadataExpert"].get_all_related_elements.return_value = live.raw_related(schema, [item])
    [r] = g.relationships("sch1")
    assert (r.type_name, r.activity_status, r.completion_time) == ("ActionTarget", "COMPLETED", "1791237500067")
    assert r.completion_message.startswith("OMES-SURVEY-ACTION-0019") and r.other_type == "EngineAction"
    assert "ActionTarget" in cc.STRUCTURAL_RELATIONSHIPS                       # still structural for archive-vs-delete


# ── (4) non-deterministic GUIDs are stored, deterministic names are rebuilt ──

def test_every_name_the_commit_builds_is_deterministic_and_every_guid_is_stored(world, fake):
    ent = entity(world)
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}|@\d{8,}|\d{10,}")
    for qn in (cc.schema_qn(ent, "sales"), cc.names_for(ent)["server"],
               cc.target_name(ent, "sales")):
        assert qn == qn.strip() and not uuid.search(qn), qn
    assert cc.schema_qn(ent, "sales") == SALES_QN
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=True)
    by = {p["proof"]: p for p in world["registry"].list_catalogue_commit_proofs("db")}
    assert by[cc.P_SURVEY]["detail"]["engine_action"] == by[cc.P_SURVEY]["element_guid"]
    assert by[cc.P_TARGET]["target_guid"] and by[cc.P_TARGET]["detail"]["engine_action"]
