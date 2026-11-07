"""The Zenodo adapter over the recorded fixtures (2026-11-03; no network, no key)."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from resource_explorer.open_data import adapters, service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.adapters import adapter_for_api_kind, price
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.open_data.transport import HttpTransport, TransportError
from resource_explorer.open_data.zenodo import ZenodoAdapter
from resource_explorer.registry import ProjectRegistry

FIX = Path(__file__).parent / "fixtures" / "open_data"
BASE = "https://zenodo.org/api"
RECORD_ID = "18494263"
RECORD_DOI = "10.5281/zenodo.18494263"
CONCEPT_DOI = "10.5281/zenodo.18494262"


def _load(name):
    return json.loads((FIX / name).read_text())


class ZenodoReplay:
    """Replays the recorded answers behind the real `Transport.get_json` signature; `calls` lists what was asked."""

    def __init__(self):
        self.calls: list[tuple[str, dict, dict]] = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        params = params or {}
        if url == f"{BASE}/records/{RECORD_ID}":
            return _load("zenodo_record_with_files.json")
        if url == f"{BASE}/records":
            q = params.get("q", "")
            if q.startswith("doi:"):
                return _load("zenodo_doi_lookup.json") if RECORD_DOI in q else {"hits": {"total": 0, "hits": []}}
            if q == "clinical trial":
                return _load(f"zenodo_search_clinical_trial_dataset_page{params['page']}.json")
            if not q:
                return {"hits": {"total": 7632651, "hits": []}, "aggregations": {}, "links": {}}
        raise TransportError(f"{url}: HTTP 404")

    def get_text(self, url, params=None, headers=None):
        raise AssertionError(f"unexpected outbound call: {url}")


def test_the_fake_transport_binds_the_real_signature():
    shape = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]   # noqa: E731
    assert shape(ZenodoReplay.get_json) == shape(HttpTransport.get_json)


# ── manifest ──────────────────────────────────────────────────────────────────

def test_manifest_is_discovered_priced_and_needs_no_key():
    cls = adapters.discover_adapters()["zenodo"]
    m = cls.manifest
    assert (m.api_kind, m.role, m.calls_per_page, m.page_size, m.needs_key) == ("Zenodo", "portal", 1, 25, False)
    assert adapter_for_api_kind("InvenioRDM", "portal") is cls and adapter_for_api_kind("zenodo", "portal") is cls
    assert "files list costs one more call" in m.cost_note
    assert price(m, 3).line == "3 pages × 1 call = 3 calls"


# ── search ────────────────────────────────────────────────────────────────────

def test_zenodo_search_reads_two_recorded_pages_and_states_absences():
    t = ZenodoReplay()
    a = ZenodoAdapter(BASE, t)
    p1 = a.search("clinical trial", {"type": "dataset"}, 1)
    p2 = a.search("clinical trial", {"type": "dataset"}, 2)
    assert p1["total"] == p2["total"] == 20482 and len(p1["rows"]) == len(p2["rows"]) == 25 and p1["page_size"] == 25
    url, params, _ = t.calls[0]
    assert url == f"{BASE}/records" and params == {"q": "clinical trial", "size": 25, "page": 1, "type": "dataset"}
    assert t.calls[1][1]["page"] == 2
    first = p1["rows"][0]
    assert first["dataset_id"] == RECORD_ID
    assert first["identifier"] == CONCEPT_DOI and first["identifier_kind"] == "doi"      # the concept DOI wins
    assert first["doi"] == CONCEPT_DOI
    assert first["publisher"] == "Zenodo"                                                # no metadata.publisher in the record
    assert first["licence_id"] == "cc-by-4.0"
    assert first["licence"] == {"attribution": True, "share_alike": False, "commercial": True}
    assert first["modified"] == "2026-02-05T11:43:32.754005+00:00"
    assert first["formats"] == ["XLSX"] and first["size"] == 246814
    assert first["record"]["id"] == 18494263 and first["record"] == p1["rows"][0]["record"]   # verbatim


def test_a_record_without_a_concept_doi_is_keyed_by_its_record_doi():
    rows = ZenodoAdapter(BASE, ZenodoReplay()).search("clinical trial", {}, 1)["rows"]
    no_concept = next(r for r in rows if r["dataset_id"] == "12737228")
    assert "conceptdoi" not in no_concept["record"]
    assert no_concept["identifier"] == no_concept["record"]["doi"].lower() and no_concept["identifier_kind"] == "doi"


def test_a_record_with_no_license_reads_not_reported_not_open():
    rows = ZenodoAdapter(BASE, ZenodoReplay()).search("clinical trial", {}, 1)["rows"]
    row = next(r for r in rows if r["dataset_id"] == "8192485")
    assert "license" not in row["record"]["metadata"]
    assert row["licence_id"] == NOT_REPORTED
    assert row["licence"] == {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}


def test_a_record_with_no_files_has_no_formats_and_no_size_not_zero_bytes():
    rows = ZenodoAdapter(BASE, ZenodoReplay()).search("clinical trial", {}, 1)["rows"]
    row = next(r for r in rows if r["dataset_id"] == "8046590")
    assert row["record"]["files"] == []
    assert row["formats"] == NOT_REPORTED and row["size"] == NOT_REPORTED


def test_licence_flags_follow_the_spdx_ids_zenodo_states():
    rows = ZenodoAdapter(BASE, ZenodoReplay()).search("clinical trial", {}, 1)["rows"]
    by_id = {r["licence_id"]: r["licence"] for r in rows if r["licence_id"] != NOT_REPORTED}
    assert by_id["cc-zero"] == {"attribution": False, "share_alike": False, "commercial": True}
    assert by_id["cc-by-4.0"]["attribution"] is True
    # an id the table does not know (Zenodo's "other-open" IS known; an invented one is not) is not guessed
    row = ZenodoAdapter(BASE, ZenodoReplay()).normalise({"id": 1, "metadata": {"title": "t", "license": {"id": "made-up"}}})
    assert row["licence_id"] == "made-up" and row["licence"]["commercial"] == NOT_REPORTED


def test_a_registered_key_is_sent_only_when_there_is_one():
    t = ZenodoReplay()
    ZenodoAdapter(BASE, t).search("clinical trial", {}, 1)
    ZenodoAdapter(BASE, t, api_key="tok").search("clinical trial", {}, 1)
    assert t.calls[0][2] == {} and t.calls[1][2] == {"Authorization": "Bearer tok"}


# ── describe / distributions / resolve ────────────────────────────────────────

def test_describe_reads_one_record_and_distributions_list_its_files():
    t = ZenodoReplay()
    a = ZenodoAdapter(BASE, t)
    row = a.describe(RECORD_ID)
    assert row["identifier"] == CONCEPT_DOI and row["title"].startswith("Validity and responsiveness")
    assert t.calls[-1][0] == f"{BASE}/records/{RECORD_ID}"
    dist = a.distributions(RECORD_ID)
    assert [d["name"] for d in dist] == ["VIP_ovary.xlsx"]
    assert dist[0]["size"] == 246814 and dist[0]["checksum"].startswith("md5:") and dist[0]["format"] == ["XLSX"]
    assert dist[0]["url"].endswith("/files/VIP_ovary.xlsx/content")


def test_resolve_finds_the_record_for_a_doi_pasted_in_any_spelling():
    t = ZenodoReplay()
    a = ZenodoAdapter(BASE, t)
    row = a.resolve(f"https://doi.org/{RECORD_DOI.upper()}")
    assert row["dataset_id"] == RECORD_ID
    assert t.calls[-1][1]["q"] == f'doi:"{RECORD_DOI}"'
    with pytest.raises(TransportError, match="no record holds the DOI"):
        a.resolve("10.5281/zenodo.1")
    with pytest.raises(ValueError):
        a.resolve("not a doi")


# ── probe ─────────────────────────────────────────────────────────────────────

def test_probe_recognises_an_invenio_base_and_adds_api_when_typed_without_it():
    got = ZenodoAdapter.probe("https://zenodo.org/api", ZenodoReplay())
    assert got == {"feed_url": BASE, "page_size": 25, "total": 7632651}

    class OnlyApi(ZenodoReplay):
        def get_json(self, url, params=None, headers=None):
            if url.startswith("https://zenodo.org/api"):
                return super().get_json(url, params, headers)
            raise TransportError(f"{url}: HTTP 404")

    assert ZenodoAdapter.probe("https://zenodo.org/", OnlyApi())["feed_url"] == BASE


def test_probe_refuses_a_base_that_does_not_answer_hits_in_one_sentence():
    class NotInvenio:
        def get_json(self, url, params=None, headers=None):
            return {"message": "hello"}

    with pytest.raises(TransportError, match="not an InvenioRDM records API"):
        ZenodoAdapter.probe("https://example.org", NotInvenio())


def test_registering_zenodo_stores_the_probed_feed_and_page_size(tmp_path):
    store = OpenDataStore(ProjectRegistry(db_path=str(tmp_path / "z.db")))
    portal = service.register_portal(store, display_name="Zenodo", api_kind="InvenioRDM", base_url="https://zenodo.org",
                                     transport=ZenodoReplay())
    assert portal["base_url"] == BASE and portal["adapter"] == "zenodo" and portal["page_size"] == 25
    assert service.search_price(portal, 2)["line"].startswith("2 pages × 1 call = 2 calls · one call per page")
    got = service.run_search(store, portal["slug"], terms="clinical trial", filters={"type": "dataset"}, pages=2,
                             transport=ZenodoReplay())
    assert got["calls_made"] == 2 and len(got["rows"]) == 50 and got["total"] == 20482
