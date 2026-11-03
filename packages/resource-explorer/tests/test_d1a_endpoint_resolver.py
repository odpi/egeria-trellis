"""D1-a: the endpoint resolver, the host-alias table, confirmed vs proposed duplicate links, and the re-anchor plan.

DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md §2, §3, §7-§9. A database's identity in Egeria is its endpoint (host,
port, database), with `EGERIA_HOST_ALIASES` naming this machine; only a CONFIRMED PeerDuplicateLink counts as in
Egeria; for coco_pharma the plan pairs RE's schemas with the content pack's by name. Over a fake gateway shaped like
the live elements read on 2026-10-30 (Endpoint -ConnectToEndpoint-> Connection -ResourceConnection-> asset). Temp
SQLite; no Egeria.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from resource_explorer import catalogue_commit as cc
from resource_explorer import endpoint_identity as ei
from resource_explorer.catalogue_gateway import EndpointRead, GatewayError, Relationship
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.quality import egeria_rules as er
from tests import test_quality_q5_data_quality_rule as q5
from tests.test_value_profile_quality_checks import entity, registry  # noqa: F401 - Q5's fixtures

PACK = "Coco PostgreSQL Server 1"
RE_SERVER = "host.docker.internal:5442"
SCHEMA_PREFIX = "PostgreSQL Relational Database Schema"


class FakeGateway:
    """Endpoints, connections and assets, linked the way the live Egeria links them."""

    def __init__(self):
        self.endpoints: list[EndpointRead] = []
        self.rels: dict[str, list[Relationship]] = {}
        self.calls: list[tuple] = []
        self.fail = ""

    def _link(self, a: str, b: str, type_name: str, b_type: str, b_qn: str, guid: str = "", props=None,
              b_at_end1: bool | None = None):
        self.rels.setdefault(a, []).append(Relationship(type_name=type_name, other_guid=b, other_type=b_type,
                                                        other_qualified_name=b_qn, guid=guid,
                                                        properties=dict(props or {}), other_at_end1=b_at_end1))

    def add_asset(self, guid: str, qn: str, type_name: str, address: str, *, target: str = "") -> str:
        ep, conn = f"ep-{guid}", f"conn-{guid}"
        self.endpoints.append(EndpointRead(ep, f"{qn}::Endpoint", address))
        self._link(ep, conn, "ConnectToEndpoint", "VirtualConnection", f"{qn}::Connection")
        self._link(conn, guid, "ResourceConnection", type_name, qn)
        self._link(conn, ep, "ConnectToEndpoint", "Endpoint", f"{qn}::Endpoint")
        self.rels.setdefault(guid, []).append(Relationship("ResourceConnection", conn, "VirtualConnection"))
        if target:
            self.rels[guid].append(Relationship("CatalogTarget", "jdbc-cataloguer", "IntegrationConnector", guid=target))
        return guid

    def schema(self, guid: str, server: str, name: str, host: str = "host.docker.internal", port: int = 5442,
               db: str = "coco_pharma", target: str = "") -> str:
        return self.add_asset(guid, f"{SCHEMA_PREFIX}::{server}::{db}.{name}", "DeployedDatabaseSchema",
                              f"jdbc:postgresql://{host}:{port}/{db}?currentSchema={name}", target=target)

    def database(self, guid: str, server: str, host: str = "host.docker.internal", db: str = "coco_pharma") -> str:
        return self.add_asset(guid, f"PostgreSQL Relational Database::{server}::{db}", "RelationalDatabase",
                              f"jdbc:postgresql://{host}:5442/{db}")

    def member(self, db_guid: str, schema_guid: str, type_name: str = "DataSetContent", guid: str = "") -> None:
        """As the cataloguer links them (live 2026-10-31): the schema at end 1, the database at end 2."""
        guid = guid or f"dsc-{schema_guid}"
        self._link(db_guid, schema_guid, type_name, "DeployedDatabaseSchema", "", guid=guid, b_at_end1=True)
        self._link(schema_guid, db_guid, type_name, "RelationalDatabase", "", guid=guid, b_at_end1=False)

    def duplicate(self, a: str, b: str, b_qn: str, status) -> None:
        self._link(a, b, "PeerDuplicateLink", "DeployedDatabaseSchema", b_qn, guid=f"dup-{a}-{b}",
                   props={"statusIdentifier": str(status)})

    def annotate(self, db_guid: str, qn: str) -> None:
        self._link(db_guid, f"ann-{len(self.rels)}", "AssociatedAnnotation", "ResourceMeasureAnnotation", qn)

    # the gateway's read surface
    def find_endpoints(self, search: str):
        self.calls.append(("find_endpoints", search))
        if self.fail:
            raise GatewayError(self.fail)
        return [e for e in self.endpoints if search in e.network_address]

    def relationships(self, guid: str):
        self.calls.append(("relationships", guid))
        return list(self.rels.get(guid, []))

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] not in ("find_endpoints", "relationships")]


def _coco_entity(slug="localhost_docker_coco_pharma", host="localhost", egeria_host="host.docker.internal",
                 port=5442, db="coco_pharma"):
    return DatabaseEntity(slug=slug, display_name=db, db_type="postgresql", host=host, port=port,
                          database_name=db, egeria_host=egeria_host)


def _coco(gw: FakeGateway, *, pairs=("aus_inventory", "coco_ods")) -> None:
    """RE's database with 4 schemas (two of them also in the pack) and the pack's 3 schemas (no pack database)."""
    gw.database("re-db", RE_SERVER)
    for name in ("aus_inventory", "coco_ods", "coco_sus", "us_sales"):
        gw.schema(f"re-{name}", RE_SERVER, name, target=f"ct-{name}")
        gw.member("re-db", f"re-{name}")
    for name in tuple(pairs) + ("cocopages",):
        # The pack wrote its endpoints under `localhost`: the alias table makes them the same endpoint.
        gw.schema(f"pack-{name}", PACK, name, host="localhost")
    gw.annotate("re-db", "Annotation::Capture Database Table Measurements::coco_pharma.coco_ods.sites::x")
    gw.annotate("re-db", "Annotation::Capture Database Schema Measurements::coco_pharma.coco_ods::y")
    gw.annotate("re-db", "Annotation::Capture Database Table Measurements::coco_pharma.coco_odsx.t::z")
    gw.annotate("re-db", "Annotation::Capture Database Measurements::coco_pharma::w")


# ── the alias table: declared in one place AND read ──────────────────────────

ALIASES = "localhost,127.0.0.1,host.docker.internal,laz"


def test_the_alias_table_is_declared_with_the_owners_set():
    from resource_explorer.config import EgeriaConfig

    field = EgeriaConfig.model_fields["host_aliases"]
    assert field.alias == "EGERIA_HOST_ALIASES"
    assert field.default == ALIASES
    assert ei.host_aliases() == frozenset(ALIASES.split(","))


def test_the_alias_table_is_declared_in_one_place_only():
    root = pathlib.Path(ei.__file__).parent
    hits = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if "EGERIA_HOST_ALIASES" in p.read_text())
    assert hits == ["config.py", "endpoint_identity.py"]
    # endpoint_identity names it in prose only; the value is read from the config field, never from the environment.
    src = (root / "endpoint_identity.py").read_text()
    assert "get_config().egeria.host_aliases" in src and "os.environ" not in src and "getenv" not in src


def test_the_resolver_reads_the_configured_table(monkeypatch):
    """Configured, then read: drop `laz` from the table and a `laz` endpoint is no longer this database's."""
    gw = FakeGateway()
    gw.schema("laz-s", PACK, "eu_sales", host="laz")
    assert set(ei.resolve(gw, _coco_entity())["schemas"]) == {"eu_sales"}
    monkeypatch.setenv("EGERIA_HOST_ALIASES", "localhost,127.0.0.1,host.docker.internal")
    from resource_explorer import config
    config._config = None
    assert "laz" not in ei.host_aliases()
    assert ei.resolve(FakeGatewayWith(gw), _coco_entity())["schemas"] == {}


def FakeGatewayWith(gw):
    other = FakeGateway()
    other.endpoints, other.rels = gw.endpoints, gw.rels
    return other


# ── the endpoint ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("address, want", [
    ("jdbc:postgresql://host.docker.internal:5442/coco_pharma", ("host.docker.internal", "5442", "coco_pharma", "")),
    ("jdbc:postgresql://localhost:5442/coco_pharma?currentSchema=aus_inventory",
     ("localhost", "5442", "coco_pharma", "aus_inventory")),
    ("jdbc:postgresql://laz:5442/coco_pharma2", ("laz", "5442", "coco_pharma2", "")),
])
def test_network_addresses_parse(address, want):
    e = ei.parse_network_address(address)
    assert (e.host, e.port, e.database, e.schema) == want


@pytest.mark.parametrize("address", ["", "https://x/y", "jdbc:postgresql://", "jdbc:postgresql://h:5442/"])
def test_anything_else_is_not_an_endpoint(address):
    assert ei.parse_network_address(address) is None


def test_aliases_compare_equal_other_hosts_ports_and_databases_do_not():
    a = ei.Endpoint("host.docker.internal", "5442", "coco_pharma")
    for host in ("localhost", "127.0.0.1", "LAZ", "host.docker.internal"):
        assert ei.same_endpoint(a, ei.Endpoint(host, "5442", "coco_pharma"))
    assert not ei.same_endpoint(a, ei.Endpoint("otherbox", "5442", "coco_pharma"))
    assert not ei.same_endpoint(a, ei.Endpoint("localhost", "5432", "coco_pharma"))
    assert not ei.same_endpoint(a, ei.Endpoint("localhost", "5442", "coco_pharma2"))


# ── the resolver ─────────────────────────────────────────────────────────────

def test_both_families_are_found_by_endpoint_and_nothing_else():
    gw = FakeGateway()
    _coco(gw)
    gw.schema("far-1", PACK, "aus_inventory", host="otherbox")        # same names, another host: separate
    gw.schema("far-2", PACK, "aus_inventory", port=5432)              # another port: separate
    gw.schema("far-3", PACK, "aus_inventory", db="coco_pharma2")      # `:5442/coco_pharma` matches the find, not the endpoint
    res = ei.resolve(gw, _coco_entity())
    assert res["error"] == "" and res["own_family"] == RE_SERVER
    assert [(f["name"], f["own"], f["schemas"], f["databases"]) for f in res["families"]] == [
        (RE_SERVER, True, 4, 1), (PACK, False, 3, 0)]
    assert sorted(res["schemas"]) == ["aus_inventory", "coco_ods", "coco_sus", "cocopages", "us_sales"]
    assert [e["guid"] for e in res["schemas"]["aus_inventory"]] == ["re-aus_inventory", "pack-aus_inventory"]
    assert {e["guid"] for es in res["schemas"].values() for e in es}.isdisjoint({"far-1", "far-2", "far-3"})
    assert gw.writes() == []


def test_a_failed_read_is_said_never_nothing_there():
    gw = FakeGateway()
    gw.fail = "Egeria answered 503"
    res = ei.resolve(gw, _coco_entity())
    assert res["error"] == "Egeria answered 503" and res["schemas"] == {} and res["families"] == []
    assert ei.reanchor_plan(gw, _coco_entity(), res) is None


# ── confirmed vs proposed duplicate links (D1-3) ─────────────────────────────

def _one_schema_with_duplicate(status) -> dict:
    gw = FakeGateway()
    gw.database("re-db", RE_SERVER)
    gw.schema("re-s", RE_SERVER, "sales")
    gw.duplicate("re-s", "other-s", "PostgreSQL Relational Database Schema::Elsewhere::coco_pharma.sales", status)
    return ei.resolve(gw, _coco_entity())


def test_a_confirmed_duplicate_link_counts_as_in_egeria():
    over = ei.schema_state_overlay(_one_schema_with_duplicate(ei.CONFIRMED_STATUS), "sales")
    assert over["state"] == "in_egeria_duplicate"
    assert over["words"].startswith("in Egeria · also described by ") and "linked as a duplicate" in over["words"]


@pytest.mark.parametrize("status", [0, 1, 2, 99, ""])
def test_a_proposed_duplicate_link_is_a_possible_duplicate_not_in_egeria(status):
    over = ei.schema_state_overlay(_one_schema_with_duplicate(status), "sales")
    assert over["state"] == "possible_duplicate"
    assert over["words"].startswith("possible duplicate · not confirmed")
    assert not over["words"].startswith("in Egeria")


@pytest.mark.parametrize("status", [4, 5])
def test_a_deprecated_or_obsolete_link_is_no_duplicate(status):
    assert ei.schema_state_overlay(_one_schema_with_duplicate(status), "sales") is None


def test_confirmed_is_egerias_validated_status():
    assert ei.CONFIRMED_STATUS == 3 and ei.is_confirmed(3) and not ei.is_confirmed(1) and not ei.is_confirmed(None)


# ── the re-anchor plan (§8 step 1) ───────────────────────────────────────────

def test_the_plan_pairs_re_schemas_with_the_packs_by_name_and_reads_the_link_type():
    gw = FakeGateway()
    _coco(gw)
    e = _coco_entity()
    res = ei.resolve(gw, e)
    proofs = {"coco_ods.sites::Unique": {"outcome": er.CREATED}, "coco_ods.sites.x::Unique": {"outcome": er.CREATED},
              "us_sales.t::Unique": {"outcome": er.NOT_IN_EGERIA}}
    plan = ei.reanchor_plan(gw, e, res, rule_proofs=proofs)
    rows = {r["schema"]: r for r in plan["rows"]}
    assert list(rows) == ["aus_inventory", "coco_ods", "coco_sus", "us_sales"]
    assert plan["pairs"] == 2
    assert (rows["coco_ods"]["re_guid"], rows["coco_ods"]["pack_guid"], rows["coco_ods"]["pack_family"]) == (
        "re-coco_ods", "pack-coco_ods", PACK)
    assert rows["coco_ods"]["hangs_off"] == {"annotations": 2, "rules": 2, "catalog_target": "ct-coco_ods"}
    assert rows["us_sales"]["hangs_off"]["rules"] == 0          # a rule never published hangs off nothing
    assert rows["coco_sus"]["pack_guid"] == "" and rows["coco_sus"]["words"] == ei.NO_PAIR
    assert ei.KEPT_UNTIL in rows["aus_inventory"]["words"]
    assert plan["database_guid"] == "re-db" and plan["database_link_type"] == "DataSetContent"
    assert "plan only, nothing is written" in plan["words"]
    db = plan["database_row"]          # §8 step 2: the level where the two families meet
    assert (db["re_guid"], db["link_type"], db["members_now"], db["members_after"]) == ("re-db", "DataSetContent", 4, 7)
    assert db["schema_end"] == 1                                  # read off the existing links, never assumed
    assert [x["guid"] for x in db["to_add"]] == ["pack-aus_inventory", "pack-coco_ods", "pack-cocopages"]
    assert db["words"].startswith(f"add DataSetContent from re-db to the pack's 3 schemas under '{PACK}' · "
                                  "after the press: 7 members")
    assert "nothing is written until the press" in db["words"]
    assert gw.writes() == []


def test_the_link_type_is_read_never_guessed():
    gw = FakeGateway()
    _coco(gw)
    gw.member("re-db", "re-coco_sus", type_name="SomethingElse")     # two types seen: no single answer
    plan = ei.reanchor_plan(gw, _coco_entity(), ei.resolve(gw, _coco_entity()))
    assert plan["database_link_type"] == ei.NOT_READ
    assert plan["database_link_types_seen"] == ["DataSetContent", "SomethingElse"]
    db = plan["database_row"]
    assert db["words"].startswith("relationship type not read · several types seen") and "nothing proposed" in db["words"]
    assert db["members_after"] is None


def test_disjoint_families_give_rows_with_no_pair():
    """Today's live coco_pharma: RE's 4 schema names and the pack's 22 do not overlap."""
    gw = FakeGateway()
    _coco(gw, pairs=())
    plan = ei.reanchor_plan(gw, _coco_entity(), ei.resolve(gw, _coco_entity()))
    assert len(plan["rows"]) == 4 and plan["pairs"] == 0
    assert all(r["words"] == "pack element: none · the pack has no schema of this name · RE's addition, kept"
               for r in plan["rows"])
    assert plan["database_row"]["members_after"] == 5 and len(plan["database_row"]["to_add"]) == 1


def test_a_unique_endpoint_has_no_plan():
    gw = FakeGateway()
    gw.database("t-db", "host.docker.internal:5442", db="throwaway")
    gw.schema("t-s", "host.docker.internal:5442", "sales", db="throwaway")
    e = _coco_entity(slug="throwaway", db="throwaway")
    res = ei.resolve(gw, e)
    assert [f["name"] for f in res["families"]] == ["host.docker.internal:5442"]
    assert ei.reanchor_plan(gw, e, res) is None
    assert ei.schema_state_overlay(res, "sales") is None


# ── Contents: the IN EGERIA column, from the read-back's proof row ───────────

@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(_coco_entity())
    r.register_database(_coco_entity(slug="throwaway", db="throwaway"))
    return r


def _view(names, effective=None):
    return {"schemas": [{"name": n, "effective": (effective or {}).get(n), "tables": []} for n in names]}


def test_contents_says_in_egeria_under_the_pack_beside_res_own_state(reg):
    gw = FakeGateway()
    _coco(gw, pairs=())
    for s in ("aus_inventory", "coco_ods", "coco_sus", "us_sales"):
        reg.append_catalogue_commit_proof("localhost_docker_coco_pharma", proof=cc.P_ELEMENTS, schema_name=s,
                                          element_guid=f"re-{s}", detail={"tables": ["t"], "columns": 1},
                                          read_at="2026-10-30T20:00:00")
    detail = ei.record_resolution(reg, gw, reg.get_database("localhost_docker_coco_pharma"), by="peterprofile")
    assert detail["plan"]["pairs"] == 0
    names = ["aus_inventory", "coco_ods", "coco_sus", "us_sales", "cocopages", "demo"]
    d = cc.derive_commit_state(reg, "localhost_docker_coco_pharma",
                               _view(names, {"cocopages": cc.LEAVE_OUT}))
    s = d["schemas"]
    for name in ("aus_inventory", "coco_ods", "coco_sus", "us_sales"):
        assert s[name]["state"] == "catalogued" and "endpoint" not in s[name]
    assert s["cocopages"]["state"] == "left_out"                               # RE's own word is kept
    assert s["cocopages"]["endpoint"]["words"] == f"in Egeria under '{PACK}'"
    assert s["cocopages"]["endpoint"]["element_guid"] == "pack-cocopages"
    assert s["demo"]["state"] == "none" and "endpoint" not in s["demo"]         # in no family: nothing said
    assert d["endpoint"]["plan"]["pairs"] == 0 and len(d["endpoint"]["plan"]["rows"]) == 4
    assert d["endpoint"]["error"] == ""
    assert d["endpoint"]["neither"] == {"schemas": ["demo"], "words": "demo: not cataloged by RE or the pack"}


def test_a_unique_endpoint_throwaway_shows_no_plan_and_no_change(reg):
    gw = FakeGateway()
    gw.database("t-db", "host.docker.internal:5442", db="throwaway")
    gw.schema("t-s", "host.docker.internal:5442", "sales", db="throwaway")
    view = _view(["sales", "hr"], {"sales": cc.CATALOGUE})
    before = cc.derive_commit_state(reg, "throwaway", view)
    ei.record_resolution(reg, gw, reg.get_database("throwaway"))
    after = cc.derive_commit_state(reg, "throwaway", view)
    assert after["endpoint"]["plan"] is None and after["endpoint"]["neither"] is None
    assert after["schemas"] == before["schemas"] and after["header"] == before["header"]


def test_a_failed_endpoint_read_is_said_on_contents(reg):
    gw = FakeGateway()
    gw.fail = "connection refused"
    ei.record_resolution(reg, gw, reg.get_database("localhost_docker_coco_pharma"))
    d = cc.derive_commit_state(reg, "localhost_docker_coco_pharma", _view(["aus_inventory"]))
    assert d["endpoint"]["error"] == "connection refused" and d["endpoint"]["words"].startswith("not read")
    assert "endpoint" not in d["schemas"]["aus_inventory"]


def test_reinclude_checks_read_both_families():
    gw = FakeGateway()
    gw.schema_elements = lambda prefix: []
    states = {"aus_inventory": {"state": "uncommitted", "endpoint": {"state": "in_egeria_other", "family": PACK,
                                                                     "element_guid": "pack-aus_inventory"}}}
    out = cc.reinclude_checks(gw, _coco_entity(), ["aus_inventory", "eu_sales"], None, held=states)
    assert out["aus_inventory"]["state"] == "clear"           # D1-a changes no create; it says what is there
    assert out["aus_inventory"]["held_by"]["family"] == PACK and PACK in out["aus_inventory"]["held_by"]["words"]
    assert "held_by" not in out["eu_sales"]
    assert cc.held_elsewhere_rows(states, ["aus_inventory"])[0]["element_guid"] == "pack-aus_inventory"


# ── publish-rules: a check on a pack schema finds the pack's element ─────────
# Over Q5's own fixtures and fake (database `coco_ods` at localhost:5442, check `sales.orders.order_id::Unique`).

class _RulesWithEndpoint(q5.FakeGateway):
    def __init__(self, elements, resolution=None, boom=""):
        super().__init__(elements)
        self.resolution, self.boom, self.resolved = resolution, boom, 0

    def endpoint_resolution(self, db_entity):
        self.resolved += 1
        if self.boom:
            raise RuntimeError(self.boom)
        return self.resolution


def _pack_resolution(entity, schema="sales"):
    gw = FakeGateway()
    gw.schema("pack-sales", PACK, schema, host="127.0.0.1", db=entity.database_name)
    return ei.resolve(gw, entity)


def test_publish_rules_finds_the_packs_element_and_stops_saying_not_in_egeria(registry, entity):
    q5._accept(registry, q5.UNIQUE, "Unique")
    pack_col = f"{SCHEMA_PREFIX}::{PACK}::coco_ods.sales::orders::order_id"
    gw = _RulesWithEndpoint({pack_col: q5.COLUMN_GUID}, _pack_resolution(entity))
    out = q5._publish(registry, entity, gw)
    assert out["counts"] == {"created": 1}
    assert (q5.COLUMN_GUID, "rule-1") in gw.links
    looked = [c[1] for c in gw.calls if c[0] == "element_guid"]
    assert looked == [q5._element_qn(entity, "sales.orders.order_id"), pack_col]   # RE's own name first
    assert gw.resolved == 1


def test_publish_rules_reads_no_endpoint_when_res_own_name_holds_it(registry, entity):
    q5._accept(registry, q5.UNIQUE, "Unique")
    gw = _RulesWithEndpoint({q5._element_qn(entity, "sales.orders.order_id"): q5.COLUMN_GUID}, None, boom="unused")
    assert q5._publish(registry, entity, gw)["counts"] == {"created": 1} and gw.resolved == 0


def test_publish_rules_on_a_unique_endpoint_is_unchanged(registry, entity):
    q5._accept(registry, q5.UNIQUE, "Unique")
    out = q5._publish(registry, entity, _RulesWithEndpoint({}, {"schemas": {}, "families": [], "error": ""}))
    assert out["counts"] == {"not_in_egeria": 1}


@pytest.mark.parametrize("resolution, boom", [(None, "no client"), ({"error": "Egeria answered 503"}, "")])
def test_a_resolver_that_cannot_be_read_is_unreadable_never_not_in_egeria(registry, entity, resolution, boom):
    q5._accept(registry, q5.UNIQUE, "Unique")
    out = q5._publish(registry, entity, _RulesWithEndpoint({}, resolution, boom))
    assert out["counts"] == {"unreadable": 1}
    assert "endpoint" in out["rows"][0]["detail_json"]["error"]


def test_the_ui_renders_the_endpoint_line_and_the_plan():
    src = (pathlib.Path(cc.__file__).parent / "web/static/next/stages/curate-scope.js").read_text()
    assert "export function endpointStateHtml" in src and "export function reanchorPlanHtml" in src
    assert re.search(r"data-endpoint-plan-host>\$\{reanchorPlanHtml", src)
