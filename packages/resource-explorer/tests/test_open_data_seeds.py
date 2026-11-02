"""Open data seeds: two recorded fixtures (compact @graph; Project Open Data with distributions) and the shipped
"Suggested portals" list (offered, never applied)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from resource_explorer.open_data import seeds
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.dcat_ap import DcatApAdapter
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.registry import ProjectRegistry

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "open_data"
JS = (ROOT / "resource_explorer/web/static/next/admin/open_data.js").read_text(encoding="utf-8")

COMPACT_FEED = "https://data.public.lu/api/1/site/catalog.jsonld"
POD_FEED = "https://healthdata.gov/data.json"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class Replay:
    def __init__(self, name):
        self.name, self.calls = name, []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params))
        return _load(self.name)

    def get_text(self, url, params=None, headers=None):
        raise AssertionError(f"unexpected outbound call: {url}")


def _bare(record):
    return {k: v for k, v in record.items() if not k.startswith("_")}


# ── A1: compact @graph catalog ────────────────────────────────────────────────
def test_the_compact_graph_page_is_a_context_and_a_graph_not_a_flattened_array():
    doc = _load("dcat_catalog_compact_graph_page1.json")
    assert isinstance(doc, dict) and "@context" in doc and isinstance(doc["@graph"], list)


def test_compact_graph_page_reads_titles_publisher_licence_and_states_absences():
    t = Replay("dcat_catalog_compact_graph_page1.json")
    page = DcatApAdapter(COMPACT_FEED, t).search("", {}, 1)
    assert len(page["rows"]) == 5 and page["read"] == 5 and page["calls"] == 1 and len(t.calls) == 1
    assert page["page_size"] == 100 and page["total"] == 3210                      # hydra's own statement
    by = {r["title"]: r for r in page["rows"]}
    row = by["Biosphere reserves: core zones"]
    assert row["publisher"].startswith("SIG-GR @ Ministère du Logement")
    assert row["licence_id"] == "cc-by-4.0"
    assert row["licence"] == {"attribution": True, "share_alike": False, "commercial": True}
    assert row["formats"] == ["GEOJSON", "GPKG", "HTML", "SHP.ZIP"] and row["size"] == 5080649
    assert row["doi"] == NOT_REPORTED and row["identifier_kind"] == "dcat_identifier"
    assert row["modified"].startswith("2026-10-30")
    graph = _load("dcat_catalog_compact_graph_page1.json")["@graph"]
    source = next(n for n in graph if n.get("@id", "").rstrip("/").endswith(row["dataset_id"]) and "Dataset" in str(n.get("@type")))
    assert _bare(row["record"]) == source                                          # verbatim; only underscore keys are RE's


def test_compact_graph_page_has_a_next_page_and_is_not_the_only_page():
    adapter = DcatApAdapter(COMPACT_FEED, Replay("dcat_catalog_compact_graph_page1.json"))
    assert adapter.search("", {}, 1)["total"] == 3210 > 100                        # paging detected: more than one page


# ── A2: Project Open Data data.json with distributions ───────────────────────
def test_pod_fixture_is_a_truncated_head_and_says_so():
    doc = _load("pod_data_json_with_distributions.json")
    assert len(doc["dataset"]) == 20 and doc["@type"] == "dcat:Catalog"
    readme = (FIX / "README").read_text(encoding="utf-8")
    assert "pod_data_json_with_distributions.json" in readme and "first 20" in readme and "31" in readme


def test_pod_distributions_read_format_url_and_state_size_when_absent():
    page = DcatApAdapter(POD_FEED, Replay("pod_data_json_with_distributions.json")).search("", {}, 1)
    assert page["total"] == 20 and page["read"] == 20 and page["calls"] == 1
    first = page["rows"][0]
    assert first["title"].startswith("Compound 526563") and first["formats"] == ["HTML"]
    assert first["size"] == NOT_REPORTED                                           # no byteSize: not 0 bytes
    source = _load("pod_data_json_with_distributions.json")["dataset"][0]
    assert source["distribution"][0]["downloadURL"].startswith("https://pubchem.ncbi.nlm.nih.gov/")
    assert _bare(first["record"]) == source
    nodist = next(r for r in page["rows"] if r["title"] == "Access to Care (2023)")
    assert nodist["formats"] == NOT_REPORTED                                       # no distribution: not "no formats"


def test_pod_licence_uri_modified_and_cadence_are_read_when_present():
    page = DcatApAdapter(POD_FEED, Replay("pod_data_json_with_distributions.json")).search("", {}, 1)
    oxy = next(r for r in page["rows"] if r["title"] == "Oxycodone")
    assert oxy["licence_id"] == "https://open.fda.gov/license"                     # an unknown IRI keeps the IRI
    assert set(oxy["licence"].values()) == {NOT_REPORTED}                          # three flags unknown, never invented
    assert oxy["modified"] == "2025-03-17"
    assert oxy["record"]["accrualPeriodicity"] == "R/P1Y"                          # the stated cadence, verbatim
    plain = page["rows"][0]
    assert plain["licence_id"] == NOT_REPORTED and plain["modified"] == "2025-09-06"
    assert "accrualPeriodicity" not in plain["record"]


# ── B: the seed list ──────────────────────────────────────────────────────────
def test_every_seed_row_has_every_field_and_a_checked_date_or_failed_date():
    raw = seeds._load()
    assert raw
    for r in raw:
        for f in seeds.FIELDS:
            assert f in r, (r.get("display_name"), f)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}|failed \d{4}-\d{2}-\d{2}", str(r["checked"])), r["display_name"]
        assert r["api_kind"] in ("CKAN", "DCAT-AP", ""), r["display_name"]
        assert r["size_note"] and r["why"]


def test_seed_rows_name_the_owners_portals_and_never_catalog_data_gov():
    names = " ".join(r["display_name"] for r in seeds.seed_rows())
    for want in ("data.gov.ie", "Humanitarian Data Exchange", "Open Government Canada", "CKAN demo", "data.europa.eu",
                 "opendata.swiss", "Department of Health and Human Services"):
        assert want in names
    assert all("catalog.data.gov" not in r["feed_url"] for r in seeds.seed_rows())
    ckan = [r for r in seeds.seed_rows() if r["display_name"].startswith("opendata.swiss")]
    assert {r["api_kind"] for r in ckan} == {"CKAN", "DCAT-AP"}                    # one portal through two adapters


def test_data_europa_eu_is_not_yet_with_no_add_and_a_failed_row_is_greyed_with_its_date():
    eu = next(r for r in seeds.seed_rows() if r["display_name"] == "data.europa.eu")
    assert eu["status"] == "not_yet" and eu["can_add"] is False and "needs the SPARQL adapter" in eu["words"]
    failed = [r for r in seeds.seed_rows() if r["status"] == "failed"]
    assert failed and all(not r["can_add"] and r["words"].startswith("did not answer on 2026-") for r in failed)


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "seeds.db"))


@pytest.fixture
def client(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from resource_explorer.web.routes import open_data as routes
    from tests.test_open_data import FakeTransport

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    t = FakeTransport()
    monkeypatch.setattr(routes, "_transport", lambda: t)
    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    c = TestClient(app)
    c.transport = t
    return c


def test_the_list_offers_only_unregistered_rows_and_shrinks_as_portals_are_added(client):
    first = client.get("/api/open-data/portal-seeds").json()
    assert first["count"] == len(first["rows"]) and first["all_registered_words"] == ""
    assert any(r["display_name"] == "CKAN demo" for r in first["rows"])
    r = client.post("/api/open-data/portal-seeds/add", json={"feed_url": "https://demo.ckan.org"})
    assert r.status_code == 200 and r.json()["origin"] == "seed" and r.json()["registered_by"] == "tester"
    second = client.get("/api/open-data/portal-seeds").json()
    assert second["count"] == first["count"] - 1
    assert all(x["display_name"] != "CKAN demo" for x in second["rows"])
    assert client.get("/api/open-data/portals").json()[0]["origin"] == "seed"


def test_a_registered_portal_is_matched_on_host_and_path_not_on_name(client):
    client.post("/api/open-data/portals", json={"display_name": "My own name for it", "api_kind": "CKAN",
                                                "base_url": "https://www.demo.ckan.org/"})
    assert all(r["feed_url"] != "https://demo.ckan.org" for r in client.get("/api/open-data/portal-seeds").json()["rows"])


def test_add_checks_reach_first_and_a_refusal_registers_nothing(client):
    r = client.post("/api/open-data/portal-seeds/add", json={"feed_url": "https://data.humdata.org"})   # not served by the fake
    assert r.status_code == 422 and r.json()["detail"].startswith("Not added: could not reach it")
    assert client.get("/api/open-data/portals").json() == []
    assert any(x["display_name"] == "Humanitarian Data Exchange" for x in client.get("/api/open-data/portal-seeds").json()["rows"])


def test_add_refuses_data_europa_eu_a_failed_row_and_an_address_not_in_the_list(client):
    for row in seeds.seed_rows():
        if not row["can_add"] and row["feed_url"]:
            r = client.post("/api/open-data/portal-seeds/add", json={"feed_url": row["feed_url"]})
            assert r.status_code == 422 and "cannot be added" in r.json()["detail"]
    assert client.post("/api/open-data/portal-seeds/add", json={"feed_url": "https://elsewhere.example"}).status_code == 404
    assert client.post("/api/open-data/portal-seeds/add", json={"feed_url": ""}).status_code == 404
    assert client.get("/api/open-data/portals").json() == []
    assert client.transport.calls == []                                                            # nothing was asked of the network


def test_when_all_are_registered_the_response_says_so():
    assert seeds.unregistered([{"base_url": r["feed_url"]} for r in seeds.seed_rows() if r["feed_url"]]) \
        == [r for r in seeds.seed_rows() if not r["feed_url"]]                                      # only the not-yet row stays
    assert "all suggested portals are registered" in JS


def test_the_screen_draws_a_folded_section_with_the_help_and_no_bulk_add():
    assert "Suggested portals · ${res.rows.length}" in JS
    assert "<details data-od-seeds-fold>" in JS and "<details data-od-seeds-fold open" not in JS   # starts closed
    assert "Registers this portal for everyone, as you. Checks reach first; nothing is imported." in JS
    assert "data-od-seed-add" in JS and "Add all" not in JS and "bulk" not in JS.lower().replace("no bulk", "")
    assert "r.can_add ?" in JS                                                                       # no Add on a not-yet row
