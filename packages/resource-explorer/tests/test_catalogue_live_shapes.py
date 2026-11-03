"""D2 (rehearsal 2026-10-05): the gateway must parse the shapes Egeria really answers with.

The slice B fake invented its own shapes, so 455 tests passed against a gateway that read every
element as absent. Every test here feeds the REAL gateway the recorded live payloads in
`live_catalogue_payloads.py` (raw elements keyed `elementGUID`/`elementProperties`, the related
elements DICT with `elementList`), and `FakeEgeria` builds its own payloads with the same
builders, so the two cannot drift apart again.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_catalogue_payloads as live  # noqa: E402
from fake_egeria_catalogue import FakeEgeria  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer.registry import DatabaseEntity  # noqa: E402


@pytest.fixture
def real():
    ent = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                         egeria_host="host.docker.internal", port=5442, database_name="shop")
    g = gw.PyegeriaCatalogueGateway(ent)
    clients = {n: MagicMock(name=n) for n in ("AssetMaker", "MetadataExpert", "AutomatedCuration", "ServerOps")}
    g._clients.update(clients)
    return g, clients, ent


def test_read_element_finds_the_live_element_by_qualified_name(real):
    g, c, _ = real
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = live.LIVE_ELEMENT
    el = g.read_element(live.DB_QN)
    assert el is not None
    assert (el.guid, el.qualified_name, el.type_name, el.archived) == (
        live.DB_GUID, live.DB_QN, "RelationalDatabase", False)


def test_read_element_sees_the_live_memento_classification_as_archived(real):
    g, c, _ = real
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = live.raw_element(
        "g1", "qn", "DeployedDatabaseSchema", archived=True)
    assert g.read_element("qn", for_lineage=True).archived is True


def test_elements_under_returns_the_live_elements_of_a_starts_with_search(real):
    g, c, _ = real
    prefix = live.DB_QN + "::"
    c["MetadataExpert"].find_metadata_elements_with_string.return_value = [
        live.raw_element("t1", prefix + "plain::orders", "RelationalTable"),
        live.raw_element("c1", prefix + "plain::orders::order_id", "RelationalColumn"),
        live.raw_element("x", "something else", "Asset"),             # not under the prefix
    ]
    got = g.elements_under(prefix)
    assert [(e.guid, e.type_name) for e in got] == [("t1", "RelationalTable"), ("c1", "RelationalColumn")]
    assert got[0].qualified_name == prefix + "plain::orders"


def test_relationships_reads_the_live_dict_with_its_element_list(real):
    g, c, _ = real
    c["MetadataExpert"].get_all_related_elements.return_value = live.LIVE_RELATED
    rels = g.relationships(live.DB_GUID)
    assert [r.type_name for r in rels] == ["ReportSubject", "ReportSubject", "ActionTarget", "SourcedFrom",
                                           "ResourceConnection"]                  # live had five
    assert rels[0].guid == "r1" and rels[0].other_type == "SurveyReport" and rels[0].other_guid.startswith("0eafcee7")


def test_a_live_term_assignment_makes_the_leave_out_an_archive_not_a_soft_delete(real):
    g, c, ent = real
    qn = gw.schema_qualified_name(gw.server_name_for(ent), "shop", "sales")
    schema = live.raw_element("sch1", qn, "DeployedDatabaseSchema")
    table = live.raw_element("tab1", qn + "::orders", "RelationalTable")
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = schema
    c["MetadataExpert"].find_metadata_elements_with_string.return_value = [table]
    term = live.raw_element("term1", "Term::x", "GlossaryTerm")

    def related(guid, body=None):
        if guid == "tab1":
            return live.raw_related(table, [("SemanticAssignment", "ra", term)])
        return live.raw_related(schema, [("Schema", "rb", live.raw_element("st", "st", "SchemaType"))])
    c["MetadataExpert"].get_all_related_elements.side_effect = related
    read = cc.read_hangs_off(g, ent, "sales")
    assert read["state"] == "read" and read["form"] == gw.ARCHIVE
    assert read["hangs_off"]["by_type"] == {"SemanticAssignment": 1}


def test_an_engine_action_target_is_structural_and_never_makes_a_leave_out_an_archive(real):
    """Architect ruling 2026-10-05: an engine action targeting a schema is Egeria's own machinery,
    never something a person attached. Live relationship type `ActionTarget` (rehearsal)."""
    assert "ActionTarget" in cc.STRUCTURAL_RELATIONSHIPS
    g, c, ent = real
    qn = gw.schema_qualified_name(gw.server_name_for(ent), "shop", "sales")
    schema = live.raw_element("sch1", qn, "DeployedDatabaseSchema")
    action = live.raw_element("ea1", "EngineAction::1", "EngineAction")
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = schema
    c["MetadataExpert"].find_metadata_elements_with_string.return_value = []
    c["MetadataExpert"].get_all_related_elements.return_value = live.raw_related(schema, [("ActionTarget", "ra", action)])
    read = cc.read_hangs_off(g, ent, "sales")
    assert read["form"] == gw.SOFT_DELETE and read["hangs_off"]["total"] == 0


@pytest.mark.parametrize("bad", [{"unexpected": 1}, 42, ["not", "elements"]])
def test_an_unrecognised_element_answer_is_an_error_never_an_absent_element(real, bad):
    g, c, _ = real
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = bad
    with pytest.raises(gw.GatewayError):
        g.read_element("qn")


def test_an_unrecognised_relationships_answer_is_an_error_never_nothing_hangs_off_it(real):
    g, c, _ = real
    c["MetadataExpert"].get_all_related_elements.return_value = [{"relationshipHeader": {}}]   # the OLD invented shape
    with pytest.raises(gw.GatewayError):
        g.relationships("g")


def test_an_unrecognised_elements_answer_is_an_error_never_no_elements(real):
    g, c, _ = real
    c["MetadataExpert"].find_metadata_elements_with_string.return_value = [{"properties": {"qualifiedName": "p::x"}}]      # no guid at all
    with pytest.raises(gw.GatewayError):
        g.elements_under("p::")


def test_no_elements_found_is_still_an_honest_empty(real):
    g, c, _ = real
    c["MetadataExpert"].find_metadata_elements_with_string.return_value = "No elements found"
    c["MetadataExpert"].get_all_related_elements.return_value = "No elements found"
    c["MetadataExpert"].get_metadata_element_by_unique_name.return_value = "No elements found"
    assert g.elements_under("p::") == [] and g.relationships("g") == [] and g.read_element("qn") is None


# ── the fake speaks the live shapes ──────────────────────────────────────────

def test_the_builders_reproduce_the_recorded_live_element_exactly():
    built = live.raw_element(live.DB_GUID, live.DB_QN, "RelationalDatabase", zones=["egeria-runtime"],
                             props={"description": "throwaway rehearsal database", "versionIdentifier": "not recorded"})
    assert built == live.LIVE_ELEMENT
    assert set(live.LIVE_ELEMENT) == live.ELEMENT_KEYS


def test_the_fake_s_raw_payloads_have_exactly_the_live_shapes():
    f = FakeEgeria()
    g = f.add_element("PostgreSQL Relational Database::h:1::d", "RelationalDatabase")
    s = f.add_element("PostgreSQL Relational Database Schema::h:1::d.s", "DeployedDatabaseSchema", parent=g)
    el = f.raw_element(s)
    assert set(el) == live.ELEMENT_KEYS and "elementHeader" not in el and "properties" not in el
    assert el["elementGUID"] == s and el["elementProperties"]["propertiesAsStrings"]["qualifiedName"].endswith("d.s")
    rel = f.raw_related(s)
    assert set(rel) == live.RELATED_KEYS and rel["elementList"]
    assert all(set(item) == live.RELATED_ITEM_KEYS for item in rel["elementList"])
    assert set(live.LIVE_RELATED["elementList"][0]) == live.RELATED_ITEM_KEYS


def test_the_fake_reads_go_through_the_real_parsers_so_a_shape_change_fails_every_commit_test():
    f = FakeEgeria()
    g = f.add_element("PostgreSQL Relational Database::h:1::d", "RelationalDatabase")
    seen = f.read_element("PostgreSQL Relational Database::h:1::d")
    assert seen is not None and seen.guid == g and seen.type_name == "RelationalDatabase"
    assert f.last_wire and f.last_wire[0] == "element"
