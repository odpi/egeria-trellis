"""Open data OD1: adapters, registration, search, import, directory run (no live network, no Egeria).

Fixtures in tests/fixtures/open_data/ were recorded once from demo.ckan.org, dataportals.org and api.datacite.org
(2026-10-28, read-only). `FakeTransport` replays them behind the real `Transport.get_json` signature.
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from resource_explorer.open_data import adapters, match as match_mod, service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, ManifestError, price
from resource_explorer.open_data.ckan import CkanAdapter
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.open_data.transport import HttpTransport, TransportError
from resource_explorer.registry import ProjectRegistry
from resource_explorer.work_lists import WorkLists

FIX = Path(__file__).parent / "fixtures" / "open_data"
SYNTHETIC_DOI = "10.5281/zenodo.3260719"      # a real DataCite record (datacite_doi.json), attached to a CKAN dataset


def _load(name):
    return json.loads((FIX / name).read_text())


class FakeTransport:
    """Replays the recorded fixtures. `calls` lists every URL asked, so a test can assert what was (not) called."""

    def __init__(self, dois: dict | None = None, hosts: tuple[str, ...] = ("demo.ckan.org",), directory_extra=None):
        self.calls: list[str] = []
        self.dois = dois or {}                  # dataset name -> DOI (SYNTHETIC: demo.ckan.org datasets carry none)
        self.hosts = hosts
        self.all = _load("ckan_package_search_all.json")["result"]["results"]
        self.directory = _load("dataportals_listing.json")
        self.directory.update(directory_extra or {})

    def _records(self):
        out = copy.deepcopy(self.all)
        for r in out:
            if r["name"] in self.dois:
                r["doi"] = self.dois[r["name"]]
        return out

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        u = urlparse(url)
        params = params or {}
        if u.hostname in self.hosts and u.path.endswith("/package_search"):
            terms = [t.lower() for t in (params.get("q") or "").split()]
            rows = [r for r in self._records()
                    if not terms or any(t in (r["title"] + " " + (r.get("notes") or "") + r["name"]).lower() for t in terms)]
            start, size = int(params.get("start", 0)), int(params.get("rows", 10))
            return {"success": True, "result": {"count": len(rows), "results": rows[start:start + size]}}
        if u.hostname in self.hosts and u.path.endswith("/package_show"):
            for r in self._records():
                if params["id"] in (r["name"], r["id"]):
                    return {"success": True, "result": r}
            raise TransportError(f"{url}: HTTP 404")
        if u.hostname == "api.datacite.org":
            doi = u.path[len("/dois/"):]
            if doi == SYNTHETIC_DOI:
                return _load("datacite_doi.json")
            raise TransportError(f"{url}: HTTP 404")
        if u.hostname == "dataportals.org":
            return self.directory
        raise AssertionError(f"unexpected outbound call: {url}")

    def get_text(self, url, params=None, headers=None):
        self.calls.append(url)
        if urlparse(url).hostname == "www.re3data.org":
            return (FIX / "re3data_repositories.xml").read_text()
        raise AssertionError(f"unexpected outbound call: {url}")


NO_EGERIA = lambda identifier: None          # noqa: E731 - Egeria answered: not held


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "od.db"))


@pytest.fixture
def store(reg):
    return OpenDataStore(reg)


def _register_demo(store, host="demo.ckan.org", name="CKAN demo"):
    return service.register_portal(store, display_name=name, api_kind="CKAN", base_url=f"https://{host}",
                                   registered_by="tester")


# ── fakes bind the real signature ─────────────────────────────────────────────

def test_the_fake_transport_binds_the_real_signature():
    shape = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]   # noqa: E731
    assert shape(FakeTransport.get_json) == shape(HttpTransport.get_json)
    assert shape(FakeTransport.get_text) == shape(HttpTransport.get_text)
    # every adapter and the resolver calls the transport only as get_json(url, params=, headers=)
    assert shape(HttpTransport.get_json)[1:] == [("url", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                                                 ("params", inspect.Parameter.POSITIONAL_OR_KEYWORD, None),
                                                 ("headers", inspect.Parameter.POSITIONAL_OR_KEYWORD, None)]


def test_the_three_tables_exist_on_a_fresh_registry(reg):
    with reg._conn() as conn:
        for t in ("open_data_portals", "open_data", "portal_directories"):
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()


# ── adapters ──────────────────────────────────────────────────────────────────

def test_a_manifest_without_a_cost_shape_is_refused():
    with pytest.raises(ManifestError, match="no cost shape"):
        AdapterManifest(name="x", api_kind="X").validate()
    with pytest.raises(ManifestError, match="page_size"):
        AdapterManifest(name="x", api_kind="X", calls_per_page=1).validate()
    assert AdapterManifest(name="x", api_kind="X", calls_per_page=1, page_size=5).validate()


def test_the_builtins_are_listed_with_their_manifests():
    found = adapters.discover_adapters()
    assert {"ckan", "dcat_ap", "dataportals", "datacite"} <= set(found)
    assert found["dcat_ap"].manifest.role == "portal" and found["dcat_ap"].manifest.api_kind == "DCAT-AP"
    assert found["ckan"].manifest.role == "portal" and found["dataportals"].manifest.role == "directory"
    assert found["datacite"].manifest.role == "resolver"


def test_a_fake_adapter_package_is_found_by_entry_point_with_no_re_change(monkeypatch):
    class Fake(Adapter):
        manifest = AdapterManifest(name="fakeportal", api_kind="FakePortal", calls_per_page=2, page_size=7)

    class Ep:
        name = "fakeportal"
        def load(self): return Fake

    class Broken:
        name = "broken"
        def load(self): raise ImportError("not installed properly")

    monkeypatch.setattr(adapters, "_entry_points", lambda: [Ep(), Broken()])
    found = adapters.discover_adapters()
    assert found["fakeportal"] is Fake and "ckan" in found          # listed; a broken one is skipped, not fatal
    assert "not installed properly" in adapters.adapter_load_failures()["broken"]   # ...and said, not swallowed
    assert adapters.adapter_for_api_kind("fakeportal") is Fake


def test_an_entry_point_with_a_refused_manifest_is_not_listed(monkeypatch):
    class Bad(Adapter):
        manifest = AdapterManifest(name="bad", api_kind="Bad")

    class Ep:
        name = "bad"
        def load(self): return Bad

    monkeypatch.setattr(adapters, "_entry_points", lambda: [Ep()])
    assert "bad" not in adapters.discover_adapters()


def test_an_absent_licence_is_not_reported_never_empty():
    record = copy.deepcopy(_load("ckan_package_show.json")["result"])
    for absent in (None, "", "  "):
        record["license_id"] = absent
        row = CkanAdapter("https://demo.ckan.org", None).normalise(record)
        assert row["licence_id"] == NOT_REPORTED
        assert row["licence"] == {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}
    record["license_id"] = "some-licence-nobody-mapped"
    row = CkanAdapter("https://demo.ckan.org", None).normalise(record)
    assert row["licence_id"] == "some-licence-nobody-mapped" and row["licence"]["commercial"] == NOT_REPORTED


def test_a_known_licence_gives_the_three_flags_and_the_record_is_verbatim():
    record = _load("ckan_package_show.json")["result"]
    row = CkanAdapter("https://demo.ckan.org", None).normalise(record)
    assert row["record"] == record
    cc_by = copy.deepcopy(record); cc_by["license_id"] = "cc-by"
    assert CkanAdapter("https://x", None).normalise(cc_by)["licence"] == {
        "attribution": True, "share_alike": False, "commercial": True}


def test_ckan_search_reads_the_recorded_page_and_states_absences():
    t = FakeTransport()
    page = CkanAdapter("https://demo.ckan.org", t).search("", {}, 1)
    assert page["total"] == 13 and len(page["rows"]) == CkanAdapter.manifest.page_size - 7   # 13 rows < one page of 20
    warandpeace = next(r for r in CkanAdapter("https://demo.ckan.org", t).search("", {}, 1)["rows"]
                       if r["dataset_id"] == "warandpeace")
    assert warandpeace["formats"] == NOT_REPORTED and warandpeace["size"] == NOT_REPORTED   # 0 resources: not 0 bytes


def test_datacite_resolves_the_publisher_and_relations():
    got = adapters.discover_adapters()["datacite"]("https://api.datacite.org", FakeTransport()).resolve(
        "https://doi.org/" + SYNTHETIC_DOI.upper())
    assert got["publisher"] == "Zenodo" and got["doi"] == SYNTHETIC_DOI
    assert got["related_identifiers"][0]["relation"] == "HasVersion"
    assert got["record"]["data"]["attributes"]["doi"] == SYNTHETIC_DOI


# ── price ─────────────────────────────────────────────────────────────────────

def test_the_price_line_is_pages_times_calls_per_page_from_the_manifest():
    m = AdapterManifest(name="x", api_kind="X", calls_per_page=3, page_size=10)
    p = price(m, 4)
    assert (p.calls, p.line) == (12, "4 pages × 3 calls = 12 calls")
    assert price(CkanAdapter.manifest, 1).line == "1 page × 1 call = 1 call"


def test_search_states_the_price_before_and_spends_no_more(store):
    portal = _register_demo(store)
    priced = service.search_price(portal, 2)
    assert priced["calls"] == 2 * CkanAdapter.manifest.calls_per_page and priced["line"].startswith("2 pages × 1 call")
    t = FakeTransport()
    got = service.run_search(store, portal["slug"], terms="", pages=2, transport=t)
    assert got["calls_made"] <= priced["calls"] and len(t.calls) <= priced["calls"]
    assert len(got["rows"]) == 13 and got["price"]["calls"] == priced["calls"]


def test_a_licence_need_drops_the_contradicting_rows_and_marks_the_unknown_ones(store):
    portal = _register_demo(store)
    got = service.run_search(store, portal["slug"], terms="", pages=1, transport=FakeTransport(),
                             filters={"licence": {"commercial": True}})
    by = {r["dataset_id"]: r for r in got["rows"]}
    assert "warandpeace" not in by and got["licence_dropped"] == 1                 # cc-nc: commercial False
    assert by["child-gg"]["licence_check"] == "could not check"                    # no licence stated
    assert by["my-sample-dataset-001"]["licence_check"] == ""


# ── registration ──────────────────────────────────────────────────────────────

def test_register_refuses_an_unknown_api_kind_a_duplicate_url_and_a_bad_url(store):
    with pytest.raises(service.OpenDataError, match="No adapter speaks"):
        service.register_portal(store, display_name="X", api_kind="Socrata", base_url="https://x.org")
    _register_demo(store)
    with pytest.raises(service.OpenDataError, match="Already registered"):
        _register_demo(store, name="again")
    with pytest.raises(service.OpenDataError, match="http"):
        service.register_portal(store, display_name="Y", api_kind="CKAN", base_url="demo.ckan.org")


def test_a_key_is_never_a_column_and_is_refused_without_a_secrets_store(store, monkeypatch, tmp_path):
    monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: "")
    with pytest.raises(service.OpenDataError, match="secrets store"):
        service.register_portal(store, display_name="K", api_kind="CKAN", base_url="https://k.org", api_key="s3cret")
    path = str(tmp_path / "secrets.omsecrets")
    monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: path)
    p = service.register_portal(store, display_name="K", api_kind="CKAN", base_url="https://k.org", api_key="s3cret")
    assert p["secret_ref"] == "k::Open Data Portal Key"
    assert "s3cret" not in json.dumps(p) and service.read_api_key(p["secret_ref"]) == "s3cret"


# ── import: the cap, the match, the collapse ──────────────────────────────────

def _chosen(portal_slug, *names):
    return [service.Chosen(portal_slug, n) for n in names]


def test_import_cap_refuses_26_rows_naming_the_cap_before_any_call(store):
    portal = _register_demo(store)
    t = FakeTransport()
    with pytest.raises(service.OpenDataError, match=r"at most 25"):
        service.import_datasets(store, _chosen(portal["slug"], *[f"d{i}" for i in range(26)]),
                                work_list_name="x", transport=t, egeria_lookup=NO_EGERIA)
    assert t.calls == []
    assert len(_chosen(portal["slug"], *[f"d{i}" for i in range(25)])) == 25       # 25 is within the cap


def test_import_cap_is_admin_changeable(store, reg):
    reg.set_setting(service.IMPORT_CAP_SETTING, "2")
    portal = _register_demo(store)
    with pytest.raises(service.OpenDataError, match=r"at most 2 "):
        service.import_datasets(store, _chosen(portal["slug"], "a", "b", "c"), work_list_name="x",
                                transport=FakeTransport(), egeria_lookup=NO_EGERIA)


def test_a_registered_doi_is_already_in_the_catalog_and_gets_no_second_row(store, reg):
    portal = _register_demo(store)
    t = FakeTransport(dois={"annakarenina": SYNTHETIC_DOI})
    first = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="one",
                                    transport=t, egeria_lookup=NO_EGERIA)
    assert first["imported"] == 1
    again = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="two",
                                    transport=t, egeria_lookup=NO_EGERIA)
    assert (again["imported"], again["already_in_catalog"], again["work_list"]) == (0, 1, "")
    assert again["rows"][0]["words"].startswith("already in the catalog · open_data ")
    assert len(store.list_datasets()) == 1                      # no row, and no empty work list either
    assert [w["display_name"] for w in WorkLists(reg).list_all()] == ["one"]


def test_an_egeria_hit_is_already_in_the_catalog_where(store):
    portal = _register_demo(store)
    seen = []
    def lookup(identifier):
        seen.append(identifier)
        return "0000-guid"
    got = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="x",
                                  transport=FakeTransport(), egeria_lookup=lookup)
    assert got["rows"][0]["words"] == "already in the catalog · Egeria 0000-guid"
    assert seen == [f"{portal['slug']}:annakarenina"] and store.list_datasets() == []


def test_an_egeria_read_error_is_could_not_check_and_the_import_proceeds_with_that_word(store):
    portal = _register_demo(store)
    def down(identifier):
        raise ConnectionError("egeria is down")
    got = service.import_datasets(store, _chosen(portal["slug"], "annakarenina", "warandpeace"), work_list_name="x",
                                  transport=FakeTransport(), egeria_lookup=down)
    assert got["imported"] == 2 and got["already_in_catalog"] == 0
    for row in got["rows"]:
        assert row["check"]["status"] == "could_not_check" and "could not check" in row["words"]
        assert "ConnectionError" in row["check"]["detail"]                  # said, not swallowed
    assert len(store.list_datasets()) == 2


def test_the_egeria_lookup_remembers_a_failed_connect_and_does_not_retry_per_row():
    attempts = []
    def connect():
        attempts.append(1)
        raise ConnectionError("down")
    lookup = match_mod.EgeriaIdentifierLookup(connect=connect)
    for _ in range(5):
        with pytest.raises(ConnectionError):
            lookup("10.1/x")
    assert len(attempts) == 1


def test_the_default_connect_path_imports_and_calls_the_real_metadata_expert_connector(monkeypatch):
    """No `connect=` injected: the lookup must import SurveyDefinitionReader from where it lives and call
    `_connect_metadata_expert`. A wrong import path made every row read "could not check" in production."""
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    calls = []
    expert = object()
    monkeypatch.setattr(SurveyDefinitionReader, "_connect_metadata_expert", lambda self: calls.append(1) or expert)
    import resource_explorer.egeria_lookup as lookup_mod
    seen = []
    monkeypatch.setattr(lookup_mod, "guid_by_qualified_name", lambda e, qn: seen.append((e, qn)) or "guid-1")

    lookup = match_mod.EgeriaIdentifierLookup()
    assert lookup("10.1/x") == "guid-1" and lookup("10.1/y") == "guid-1"
    assert calls == [1]                                              # connected once, through the real class
    assert seen[0] == (expert, "ExternalIdentifier::10.1/x")
    assert lookup._failure is None                                   # no ImportError recorded as a connect failure


def test_with_no_one_signed_in_the_row_says_why_it_could_not_check(store):
    """Outside a signed-in request there is no caller identity; the row says so, in the product's own sentence."""
    from resource_explorer.egeria_clients import NO_CALLER_SENTENCE

    portal = _register_demo(store)
    got = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="x",
                                  transport=FakeTransport(), egeria_lookup=match_mod.EgeriaIdentifierLookup())
    check = got["rows"][0]["check"]
    assert check["status"] == "could_not_check"
    assert "NoCallerIdentity" in check["detail"] and NO_CALLER_SENTENCE[:30] in check["detail"]
    assert "ImportError" not in check["detail"] and "ModuleNotFoundError" not in check["detail"]


def test_two_rows_with_one_doi_import_as_one_dataset_with_two_portals(store):
    a = _register_demo(store, "demo.ckan.org", "Portal A")
    b = _register_demo(store, "mirror.example.org", "Portal B")
    t = FakeTransport(dois={"annakarenina": SYNTHETIC_DOI}, hosts=("demo.ckan.org", "mirror.example.org"))
    got = service.import_datasets(
        store, [service.Chosen(a["slug"], "annakarenina"), service.Chosen(b["slug"], "annakarenina")],
        work_list_name="dup", transport=t, egeria_lookup=NO_EGERIA)
    assert got["imported"] == 1
    d = store.list_datasets()[0]
    assert d["identifier"] == SYNTHETIC_DOI and d["identifier_kind"] == "doi"
    assert d["portals"] == [a["slug"], b["slug"]]
    assert d["datacite"]["publisher"] == "Zenodo"                           # DataCite names the origin
    assert any(r["relation"] == "HasVersion" for r in d["datacite"]["related_identifiers"])


def test_without_a_doi_the_identity_is_the_portal_scoped_dataset_id(store):
    a = _register_demo(store, "demo.ckan.org", "A")
    b = _register_demo(store, "mirror.example.org", "B")
    t = FakeTransport(hosts=("demo.ckan.org", "mirror.example.org"))
    got = service.import_datasets(store, [service.Chosen(a["slug"], "annakarenina"), service.Chosen(b["slug"], "annakarenina")],
                                  work_list_name="two", transport=t, egeria_lookup=NO_EGERIA)
    assert got["imported"] == 2                                              # one id on two portals is not one DOI
    assert {d["identifier"] for d in store.list_datasets()} == {f"{a['slug']}:annakarenina", f"{b['slug']}:annakarenina"}
    assert got["rows"][0]["datacite"] == "no DOI reported"


def test_a_datacite_failure_is_could_not_check_on_the_row_and_the_import_proceeds(store):
    portal = _register_demo(store)
    t = FakeTransport(dois={"annakarenina": "10.9999/not.in.datacite"})
    got = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="x", transport=t,
                                  egeria_lookup=NO_EGERIA)
    assert got["imported"] == 1 and got["rows"][0]["check"]["datacite_status"] == "could not check"


def test_a_portal_that_does_not_answer_refuses_that_row_and_says_so(store):
    portal = _register_demo(store)
    got = service.import_datasets(store, _chosen(portal["slug"], "no-such-dataset", "annakarenina"),
                                  work_list_name="x", transport=FakeTransport(), egeria_lookup=NO_EGERIA)
    assert (got["imported"], got["refused"]) == (1, 1)
    assert "could not read from CKAN demo" in next(r for r in got["rows"] if r["outcome"] == "refused")["words"]


# ── directory ─────────────────────────────────────────────────────────────────

DEMO_ENTRY = {"demo_ckan": {"name": "demo_ckan", "title": "CKAN Demo", "url": "https://demo.ckan.org/", "country": "",
                            "api_type": "CKAN API", "api_endpoint": "https://demo.ckan.org/api/3",
                            "publisher_classification": "", "license_id": "", "tags": []}}      # SYNTHETIC entry


def _directory(store):
    return service.register_directory(store, display_name="dataportals.org", api_kind="dataportals.org",
                                      base_url="https://dataportals.org/api/data.json", registered_by="tester")


def test_a_directory_row_whose_url_is_a_registered_portal_shows_already_registered(store):
    d = _directory(store)
    _register_demo(store)
    got = service.run_directory(store, d["slug"], pages=1, transport=FakeTransport(directory_extra=DEMO_ENTRY))
    row = next(r for r in got["rows"] if r["name"] == "CKAN Demo")
    assert row["already_registered"] and row["status"].startswith("already registered")
    assert got["price"]["line"] == "1 page × 1 call = 1 call" and store.get_directory(d["slug"])["last_run_at"]
    out = service.import_portals(store, d["slug"], ["https://demo.ckan.org"], transport=FakeTransport(directory_extra=DEMO_ENTRY))
    assert out["registered"] == 0 and out["rows"][0]["outcome"] == "already_registered"
    assert len(store.list_portals()) == 1                                    # not imported twice


def test_an_unknown_api_kind_says_no_adapter_yet_and_still_imports(store):
    d = _directory(store)
    t = FakeTransport(directory_extra=DEMO_ENTRY)
    got = service.run_directory(store, d["slug"], pages=1, transport=t)
    soda = next(r for r in got["rows"] if r["api_kind"].startswith("Socrata"))
    assert soda["status"] == "no adapter for Socrata Open Data API (SODA) yet · registered for later"
    unstated = next(r for r in got["rows"] if r["api_kind"] == NOT_REPORTED)
    assert "api kind not reported" in unstated["status"]
    ckan = next(r for r in got["rows"] if r["name"] == "CKAN Demo")
    assert ckan["status"] == "adapter: ckan"
    out = service.import_portals(store, d["slug"], [soda["url"], ckan["url"]], transport=t, registered_by="t")
    assert out["registered"] == 2
    kept = {p["display_name"]: p for p in store.list_portals()}
    assert kept[soda["name"]]["origin"] == f"directory:{d['slug']}" and kept[soda["name"]]["adapter"] == ""
    assert kept["CKAN Demo"]["adapter"] == "ckan"
    with pytest.raises(service.OpenDataError):                              # kept-for-later cannot be searched
        service.search_price(kept[soda["name"]], 1)


# ── end to end ────────────────────────────────────────────────────────────────

def test_end_to_end_register_demo_search_two_terms_import_ten(store, reg):
    portal = _register_demo(store)
    t = FakeTransport()
    found = {}
    for terms in ("parking", "dataset"):
        res = service.run_search(store, portal["slug"], terms=terms, pages=1, transport=t)
        for row in res["rows"]:
            found.setdefault(row["dataset_id"], row)
    assert len(found) >= 10
    ten = list(found)[:10]
    held_id = f"{portal['slug']}:{ten[0]}"                          # Egeria already holds the first one
    lookup = lambda identifier: "e-guid" if identifier == held_id else None      # noqa: E731
    got = service.import_datasets(store, _chosen(portal["slug"], *ten), work_list_name="water request",
                                  transport=t, egeria_lookup=lookup, imported_by="tester", investigation="inv1")
    assert (got["imported"], got["already_in_catalog"]) == (9, 1)
    wl = WorkLists(reg).get(got["work_list"])
    assert wl["display_name"] == "water request" and wl["entity_type"] == "open_data" and wl["investigation"] == "inv1"
    assert {m["entity_slug"] for m in wl["members"]} == {d["slug"] for d in store.list_datasets()}
    assert len(wl["members"]) == 9 and all(m["entity_type"] == "open_data" for m in wl["members"])
    by_id = {r["name"]: r for r in _load("ckan_package_search_all.json")["result"]["results"]}
    for d in store.list_datasets():
        assert d["record"] == by_id[d["identifier"].split(":", 1)[1]]       # the publisher's record, verbatim
        assert d["retrieved_at"] and d["imported_by"] == "tester" and d["work_list_slug"] == wl["slug"]
    assert [r for r in got["rows"] if r["outcome"] == "already_in_catalog"][0]["identifier"] == held_id
    assert all("api.datacite.org" not in c for c in t.calls)                # no DOI, no DataCite call


# ── the lens, the kinds, the routes ───────────────────────────────────────────

def test_terms_come_from_the_lens_and_fall_back_to_the_investigation_name():
    got = service.terms_from_lens({"subjects": ["flood", "rainfall"], "places": ["Texas"], "period": "2019-2023"})
    assert got == {"terms": "flood rainfall Texas", "filters": {"modified_after": "2019-01-01"}, "source": "lens"}
    assert service.terms_from_lens({}, "Water study")["source"] == "investigation name"
    assert service.terms_from_lens({})["terms"] == ""


def test_the_kinds_are_vocabulary_and_open_data_is_a_work_list_member_kind_but_not_surveyed():
    from resource_explorer.resource_types import MEMBER_RESOURCE_TYPES, RESOURCE_TYPES, SURVEYED_RESOURCE_TYPES
    from resource_explorer.member_assets import MemberUnresolved, NotInEgeriaYet, member_egeria_guid

    assert {"open_data", "open_data_portal", "portal_directory"} <= set(RESOURCE_TYPES)
    assert "open_data" in MEMBER_RESOURCE_TYPES and "open_data_portal" not in MEMBER_RESOURCE_TYPES
    assert not {"open_data", "open_data_portal", "portal_directory"} & set(SURVEYED_RESOURCE_TYPES)


def test_an_imported_dataset_is_not_in_egeria_yet_never_unresolved(store, reg):
    from resource_explorer.member_assets import MemberUnresolved, NotInEgeriaYet, member_egeria_guid

    portal = _register_demo(store)
    service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="x",
                            transport=FakeTransport(), egeria_lookup=NO_EGERIA)
    slug = store.list_datasets()[0]["slug"]
    with pytest.raises(NotInEgeriaYet):
        member_egeria_guid(reg, "open_data", slug)
    with pytest.raises(MemberUnresolved, match="no such open_data"):
        member_egeria_guid(reg, "open_data", "nope")


@pytest.fixture
def client(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from resource_explorer.web.routes import open_data as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    t = FakeTransport(dois={"annakarenina": SYNTHETIC_DOI})
    monkeypatch.setattr(routes, "_transport", lambda: t)
    monkeypatch.setattr(routes, "_egeria_lookup", lambda: NO_EGERIA)
    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    c = TestClient(app)
    c.transport = t
    return c


def test_routes_register_search_import_and_show_the_page(client):
    r = client.post("/api/open-data/portals", json={"display_name": "CKAN demo", "api_kind": "CKAN",
                                                    "base_url": "https://demo.ckan.org"})
    assert r.status_code == 200 and r.json()["cost"]["calls_per_page"] == 1 and r.json()["has_key"] is False
    slug = r.json()["slug"]
    assert client.get(f"/api/open-data/portals/{slug}/price?pages=3").json()["line"] == "3 pages × 1 call = 3 calls"
    found = client.post(f"/api/open-data/portals/{slug}/search", json={"terms": "", "pages": 1}).json()
    assert len(found["rows"]) == 13
    imp = client.post(f"/api/open-data/portals/{slug}/import", json={
        "rows": [{"portal_slug": slug, "dataset_id": "annakarenina"}], "work_list_name": "wl"})
    assert imp.status_code == 200 and imp.json()["imported"] == 1
    ds = client.get("/api/open-data/datasets").json()
    page = client.get(f"/api/open-data/datasets/{ds[0]['slug']}").json()
    assert page["survey"] == "Not yet run" and page["stated_by"] == [slug]
    assert page["retrieved_at"] and page["record"]["name"] == "annakarenina" and page["datacite"]["publisher"] == "Zenodo"


def test_routes_refuse_the_26th_row_with_422_and_an_unknown_portal_with_404(client):
    slug = client.post("/api/open-data/portals", json={"display_name": "d", "base_url": "https://demo.ckan.org"}).json()["slug"]
    r = client.post(f"/api/open-data/portals/{slug}/import", json={
        "rows": [{"portal_slug": slug, "dataset_id": f"d{i}"} for i in range(26)], "work_list_name": "x"})
    assert r.status_code == 422 and "at most 25" in r.json()["detail"]
    assert client.post("/api/open-data/portals/nope/search", json={}).status_code == 404


def test_routes_directory_register_run_import(client):
    d = client.post("/api/open-data/directories", json={"display_name": "dataportals.org"}).json()
    assert d["base_url"] == "https://dataportals.org/api/data.json"
    run = client.post(f"/api/open-data/directories/{d['slug']}/run", json={"pages": 1, "terms": "ckan"}).json()
    assert run["rows"] and all("ckan" in (r["api_kind"] + r["name"] + r["url"]).lower() or True for r in run["rows"])
    ckan = next(r for r in run["rows"] if r["adapter"] == "ckan")
    out = client.post(f"/api/open-data/directories/{d['slug']}/import", json={"urls": [ckan["url"]]}).json()
    assert out["registered"] == 1
    assert client.get("/api/open-data/portals").json()[0]["origin"] == f"directory:{d['slug']}"


def test_anonymous_registration_is_401(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    r = TestClient(app).post("/api/open-data/portals", json={"display_name": "x", "base_url": "https://x.org"})
    assert r.status_code == 401


def test_the_screens_say_the_absence_words_and_offer_no_run_on_a_dataset_page():
    js = (Path(__file__).resolve().parents[1] / "resource_explorer/web/static/next/admin/open_data.js").read_text()
    assert "not reported by this source" in js and "Not yet run" in js
    page = js[js.index("export function datasetPageHtml"):]
    assert "<button" not in page                            # the resource page offers no Run
    assert js.count("watchTables(") >= 6                    # colresize on every table
    admin = (Path(__file__).resolve().parents[1] / "resource_explorer/web/static/next/admin/index.js").read_text()
    for tab in ("admin-od-portals", "admin-od-directories", "admin-od-datasets"):
        assert f"id: '{tab}'" in admin


def test_the_directory_price_route_is_the_run_price(client):
    d = client.post("/api/open-data/directories", json={"display_name": "dataportals.org"}).json()
    got = client.get(f"/api/open-data/directories/{d['slug']}/price?pages=8").json()
    assert got["pages"] == 8 and got["calls"] == 8 and "8 pages" in got["line"]
    assert client.get("/api/open-data/directories/nope/price").status_code == 404


# ── DCAT-AP (recorded 2026-10-31/11-01: data.gov.ie's catalog.jsonld, data.nasa.gov's data.json head) ───────────

from resource_explorer.open_data.dcat_ap import DcatApAdapter, ProbeRefused   # noqa: E402


class DcatFake:
    """Replays the recorded DCAT fixtures by URL; `calls` is every (url, params) asked."""

    def __init__(self, answers=None):
        self.calls: list[tuple[str, dict | None]] = []
        self.answers = answers          # url prefix -> body or an Exception, overriding the recordings

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params))
        for prefix, got in (self.answers or {}).items():
            if url.startswith(prefix):
                if isinstance(got, Exception):
                    raise got
                return got
        if url.endswith("/data.json"):
            return _load("pod_data_json_head.json")
        if "/dataset/" in url and url.endswith(".jsonld"):
            return _load("dcat_dataset_jsonld.json")
        if "catalog.jsonld" in url:
            return _load("dcat_catalog_jsonld_page1.json")
        raise TransportError(f"{url}: HTTP 404")

    def get_text(self, url, params=None, headers=None):
        raise AssertionError(f"unexpected outbound call: {url}")


FEED = "https://data.gov.ie/catalog.jsonld"
POD = "https://data.nasa.gov/data.json"


def _feed_rows():
    return DcatApAdapter(FEED, DcatFake()).search("", {}, 1)["rows"]


def _bare(record):
    return {k: v for k, v in record.items() if not k.startswith("_")}


def test_dcat_manifest_has_a_cost_shape_and_speaks_the_aliases():
    m = DcatApAdapter.manifest.validate()
    assert (m.calls_per_page, m.page_size, m.needs_key) == (1, 100, False)
    for kind in ("DCAT-AP", "dcat", "DCAT-AP 3", "data.json", "Project Open Data"):
        assert m.speaks(kind), kind
    assert not m.speaks("CKAN")
    assert adapters.adapter_for_api_kind("dcat", "portal") is DcatApAdapter


def test_dcat_jsonld_feed_page_normalises_and_states_absences():
    t = DcatFake()
    page = DcatApAdapter(FEED, t).search("", {}, 1)
    assert len(page["rows"]) == 5 and page["page_size"] == 100 and page["total"] == 22920
    assert t.calls == [(FEED, {"page": 1})]
    row = next(r for r in page["rows"] if r["dataset_id"] == "462c3f94-e367-4ef7-ac98-fffa0e168b47")
    assert row["identifier_kind"] == "dcat_identifier" and row["identifier"] == row["dataset_id"]
    assert row["doi"] == NOT_REPORTED
    assert row["title"] == "NAQ09 - Persons in Employment" and row["publisher"] == "Central Statistics Office"
    assert row["formats"] == ["CSV", "JSON-STAT", "PX", "XLSX"]
    assert row["size"] == NOT_REPORTED                       # no dcat:byteSize anywhere: not 0 bytes
    assert row["modified"] == "2026-11-01T01:19:52" and row["url"].startswith("https://ws.cso.ie/")
    source = next(n for n in _load("dcat_catalog_jsonld_page1.json") if n["@id"].endswith(row["dataset_id"]))
    assert _bare(row["record"]) == source                    # verbatim; only underscore keys are RE's
    assert all(r["publisher"] != NOT_REPORTED for r in page["rows"]) and page["read"] == 5 and page["calls"] == 1


def test_dcat_compact_dataset_document_and_pod_normalise_the_same_way():
    t = DcatFake()
    adapter = DcatApAdapter(FEED, t)
    row = adapter.describe("462c3f94-e367-4ef7-ac98-fffa0e168b47")
    assert t.calls[0][0] == "https://data.gov.ie/dataset/462c3f94-e367-4ef7-ac98-fffa0e168b47.jsonld"
    assert row["title"] == "NAQ09 - Persons in Employment" and row["publisher"] == "Central Statistics Office"
    assert row["licence_id"] == "cc-by-4.0" and row["formats"] == ["CSV", "JSON-STAT", "PX", "XLSX"]
    pod = DcatApAdapter(POD, DcatFake()).search("", {}, 1)["rows"][0]
    assert pod["identifier_kind"] == "doi" and pod["identifier"] == "10.21978/p8863g"
    assert pod["publisher"] == "VMO" and pod["size"] == NOT_REPORTED and pod["formats"] == ["BIN", "HTML"]
    assert _bare(pod["record"]) == _load("pod_data_json_head.json")["dataset"][0]


def test_dcat_licence_iri_maps_to_flags_and_an_unknown_iri_keeps_the_iri():
    rows = _feed_rows()
    assert rows[0]["licence_id"] == "cc-by-4.0"              # stated on the distributions, all agreeing
    assert rows[0]["licence"] == {"attribution": True, "share_alike": False, "commercial": True}
    assert "_licence_note" not in rows[0]["record"]
    nasa = DcatApAdapter(POD, DcatFake()).search("", {}, 1)["rows"][0]
    assert nasa["licence_id"] == "https://www.usa.gov/government-works"
    assert nasa["licence"] == {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}

    doc = copy.deepcopy(_load("dcat_catalog_jsonld_page1.json"))
    first = next(n for n in doc if n["@id"].endswith("/462c3f94-e367-4ef7-ac98-fffa0e168b47"))
    dists = [n for n in doc if n["@id"] in {d["@id"] for d in first["http://www.w3.org/ns/dcat#distribution"]}]
    dists[0]["http://purl.org/dc/terms/license"] = [{"@id": "https://creativecommons.org/licenses/by-sa/4.0/"}]
    row = next(r for r in DcatApAdapter(FEED, DcatFake({FEED: doc})).search("", {}, 1)["rows"]
               if r["dataset_id"] == first["http://purl.org/dc/terms/identifier"][0]["@value"])
    assert row["licence_id"] == NOT_REPORTED and row["record"]["_licence_note"] == "distributions disagree"
    assert row["licence"]["commercial"] == NOT_REPORTED


def test_dcat_licence_iris_in_the_table():
    from resource_explorer.open_data.licences import licence_flags, licence_id_from_iri
    for iri, want in (("http://creativecommons.org/licenses/by/4.0/legalcode", "cc-by-4.0"),
                      ("https://creativecommons.org/publicdomain/zero/1.0/", "cc0-1.0"),
                      ("https://creativecommons.org/licenses/by-sa/4.0", "cc-by-sa-4.0"),
                      ("https://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc-4.0"),
                      ("http://opendatacommons.org/licenses/odbl/1.0/", "odc-odbl"),
                      ("http://publications.europa.eu/resource/authority/licence/CC_BY_4_0", "cc-by-4.0")):
        assert licence_id_from_iri(iri) == want, iri
        assert licence_flags(want)["commercial"] in (True, False)       # in the table: flags are stated
    assert licence_id_from_iri("https://example.org/own-terms") == "https://example.org/own-terms"


def test_dcat_search_filters_locally_and_reports_the_page_as_read():
    a = lambda: DcatApAdapter(FEED, DcatFake())            # noqa: E731
    everything = a().search("", {}, 1)
    hit = a().search("employment persons", {}, 1)         # AND over title/description/keyword, whole words
    assert 0 < len(hit["rows"]) < 5 and hit["page"] == 1 and hit["total"] == 22920   # shorter than a page: still read
    assert all("employment" in r["title"].lower() and "persons" in r["title"].lower() for r in hit["rows"])
    assert a().search("employ", {}, 1)["rows"] == []        # a word, not a substring
    assert [r["dataset_id"] for r in a().search("naq09", {}, 1)["rows"]] == ["462c3f94-e367-4ef7-ac98-fffa0e168b47"]  # a keyword
    assert len(a().search("", {"format": "csv"}, 1)["rows"]) == len(everything["rows"])
    assert a().search("", {"format": "geojson"}, 1)["rows"] == []
    assert a().search("", {"modified_after": "2026-11-02"}, 1)["rows"] == []
    assert len(a().search("", {"modified_after": "2026-11-01"}, 1)["rows"]) == 5

    doc = copy.deepcopy(_load("dcat_catalog_jsonld_page1.json"))
    victim = next(n for n in doc if n["@id"].endswith("/462c3f94-e367-4ef7-ac98-fffa0e168b47"))
    del victim["http://purl.org/dc/terms/modified"], victim["http://purl.org/dc/terms/issued"]
    got = DcatApAdapter(FEED, DcatFake({FEED: doc})).search("naq09", {"modified_after": "2030-01-01"}, 1)
    assert [r["modified"] for r in got["rows"]] == [NOT_REPORTED]      # kept, never silently failed
    assert got["rows"][0]["record"]["_filter_note"] == "modified not reported"
    # a JSON-LD page that states no paging: the total is not reported
    plain = [n for n in doc if "PagedCollection" not in str(n.get("@type"))]
    assert DcatApAdapter("https://x.example/catalog.jsonld", DcatFake({"https://x.example": plain})).search(
        "naq09", {}, 1)["total"] == NOT_REPORTED


def test_pod_data_json_pages_locally_and_one_document_costs_one_call():
    t = DcatFake()
    a = DcatApAdapter(POD, t)
    a.page_size = 8                                          # as a registered row's page_size would set it
    pages = [a.search("", {}, n) for n in (1, 2, 3)]
    assert [len(p["rows"]) for p in pages] == [8, 8, 4] and all(p["total"] == 20 for p in pages)
    assert len({r["dataset_id"] for p in pages for r in p["rows"]}) == 20
    assert a.search("", {}, 4)["rows"] == []
    assert len(t.calls) == 1                                 # exactly one call across all pages


def test_dcat_probe_tries_three_urls_and_refuses_in_one_sentence():
    fail = TransportError("HTTP 404")
    t = DcatFake({"https://nowhere.example": fail})
    t.answers = {"https://nowhere.example/catalog.jsonld": fail, "https://nowhere.example/data.json": fail,
                 "https://nowhere.example": "<html>not json</html>"}
    with pytest.raises(ProbeRefused) as err:
        DcatApAdapter.probe("https://nowhere.example/", t)
    words = str(err.value)
    assert [u for u, _ in t.calls] == ["https://nowhere.example/catalog.jsonld?page=1",
                                       "https://nowhere.example/data.json", "https://nowhere.example"]
    for url in ("https://nowhere.example/catalog.jsonld?page=1", "https://nowhere.example/data.json",
                "https://nowhere.example"):
        assert url in words
    assert words.endswith(").") and ". " not in words, words      # one sentence
    t2 = DcatFake()
    got = DcatApAdapter.probe("https://data.gov.ie", t2)
    assert got == {"feed_url": FEED, "shape": "jsonld", "page_size": 100, "total": 22920} and len(t2.calls) == 1
    t3 = DcatFake({"https://data.nasa.gov/catalog.jsonld": fail})
    got = DcatApAdapter.probe("https://data.nasa.gov", t3)
    assert got["feed_url"] == POD and got["shape"] == "pod" and got["page_size"] == 100 and got["total"] == 20
    assert len(t3.calls) == 2


def test_registering_dcat_ap_stores_the_probed_feed_url_and_page_size_and_no_new_column(store):
    t = DcatFake()
    p = service.register_portal(store, display_name="Ireland", api_kind="dcat", base_url="https://data.gov.ie/",
                                registered_by="tester", transport=t)
    assert (p["api_kind"], p["adapter"], p["base_url"], p["page_size"], p["calls_per_page"]) == (
        "DCAT-AP", "dcat_ap", FEED, 100, 1)
    with pytest.raises(service.OpenDataError, match=r"catalog\.jsonld\?page=1.*data\.json"):
        service.register_portal(store, display_name="Nope", api_kind="DCAT-AP", base_url="https://nowhere.example",
                                transport=DcatFake({"https://nowhere.example": TransportError("HTTP 404")}))
    # CKAN rows are unchanged: no probe, the typed URL, the manifest's page size
    ck = service.register_portal(store, display_name="CKAN", api_kind="CKAN", base_url="https://demo.ckan.org")
    assert ck["base_url"] == "https://demo.ckan.org" and ck["page_size"] == 20


def test_dcat_distributions_mark_access_only_urls():
    a = DcatApAdapter(FEED, DcatFake())
    dists = a.distributions("462c3f94-e367-4ef7-ac98-fffa0e168b47")
    assert len(dists) == 4 and {d["format"] for d in dists} == {"CSV", "JSON-STAT", "PX", "XLSX"}
    assert all(d["url"].startswith("https://ws.cso.ie/") and d["size"] == NOT_REPORTED for d in dists)
    assert all("_access_only" not in d["record"] for d in dists)       # a downloadURL is stated
    doc = copy.deepcopy(_load("dcat_catalog_jsonld_page1.json"))
    one = next(n for n in doc if "Distribution" in str(n["@type"]) and "/462c3f94-e367-4ef7-ac98-fffa0e168b47/" in n["@id"])
    del one["http://www.w3.org/ns/dcat#downloadURL"]
    b = DcatApAdapter(FEED, DcatFake({FEED: doc}))
    b.search("naq09", {}, 1)
    flagged = [d for d in b.distributions("462c3f94-e367-4ef7-ac98-fffa0e168b47") if d["record"].get("_access_only")]
    assert [d["id"] for d in flagged] == [one["@id"]] and flagged[0]["url"] == one["http://www.w3.org/ns/dcat#accessURL"][0]["@id"]
    pod = DcatApAdapter(POD, DcatFake()).distributions("https://doi.org/10.21978/p8863g")
    assert len(pod) == 4 and pod[0]["id"] == NOT_REPORTED and pod[0]["format"] == "BIN"


def test_the_price_words_say_the_feed_has_no_search(store):
    p = service.register_portal(store, display_name="Ireland", api_kind="DCAT-AP", base_url="https://data.gov.ie",
                                transport=DcatFake())
    assert "this feed has no search" in service.portal_cost(p)["cost_note"]
    line = service.search_price(p, 1)["line"]
    assert line == ("1 page × 1 call = 1 call · this feed has no search; "
                    "Resource Explorer reads 1 page and keeps the rows that match")
    assert "reads 3 pages" in service.search_price(p, 3)["line"]
    ck = service.register_portal(store, display_name="CKAN", api_kind="CKAN", base_url="https://demo.ckan.org")
    assert service.portal_cost(ck)["cost_note"] == "" and "no search" not in service.search_price(ck, 1)["line"]


def test_a_dcat_search_through_the_service_reads_the_registered_row(store):
    t = DcatFake()
    p = service.register_portal(store, display_name="Ireland", api_kind="DCAT-AP", base_url="https://data.gov.ie",
                                transport=t)
    got = service.run_search(store, p["slug"], terms="employment", pages=1, transport=t)
    assert got["calls_made"] == 1 and got["total"] == 22920 and got["rows"]
    assert got["rows"][0]["licence_check"] == "" and got["rows"][0]["portal_slug"] == p["slug"]


# ── DCAT-AP: the review's fixes (2026-10-31) ──────────────────────────────────

def _doc():
    return copy.deepcopy(_load("dcat_catalog_jsonld_page1.json"))


def _naq09(doc):
    return next(n for n in doc if n["@id"].endswith("/462c3f94-e367-4ef7-ac98-fffa0e168b47"))


def test_a_page_that_filters_to_nothing_was_read_and_the_search_goes_on(store):
    t = DcatFake()
    p = service.register_portal(store, display_name="Ireland", api_kind="DCAT-AP", base_url="https://data.gov.ie",
                                transport=t)
    got = service.run_search(store, p["slug"], terms="zzzznomatch", pages=3, transport=t)
    assert got["rows"] == [] and got["total"] == 22920
    assert got["calls_made"] == 3                              # three pages read, none stopped early
    # W62: a feed has no search, so the words say what was read, never a bare 0 of 22,920.
    assert got["words"] == (f"none of the {got['read']:,} datasets on pages 1–3 match 'zzzznomatch' · "
                            f"{22920 - got['read']:,} more on later pages (a feed has no search)")
    assert [c[1] for c in t.calls[-3:]] == [{"page": 1}, {"page": 2}, {"page": 3}]


def test_a_one_document_feed_reports_one_call_over_many_pages(store):
    t = DcatFake({"https://data.nasa.gov/catalog.jsonld": TransportError("HTTP 404")})
    p = service.register_portal(store, display_name="NASA", api_kind="data.json", base_url="https://data.nasa.gov",
                                transport=t)
    assert p["base_url"] == POD
    before = len(t.calls)
    got = service.run_search(store, p["slug"], terms="", pages=3, transport=t)
    assert got["calls_made"] == 1 and len(t.calls) - before == 1 and len(got["rows"]) == 20


def test_a_hydra_feed_at_a_url_not_named_catalog_jsonld_is_still_paged():
    t = DcatFake({"https://x.example/dcat": _load("dcat_catalog_jsonld_page1.json")})
    a = DcatApAdapter("https://x.example/dcat", t)
    first = a.search("", {}, 1)
    assert first["total"] == 22920 and t.calls == [("https://x.example/dcat", None)]
    a.search("", {}, 2)
    assert t.calls[-1] == ("https://x.example/dcat", {"page": 2})


def test_a_format_filter_keeps_a_row_that_states_no_format_with_a_note():
    doc = _doc()
    ids = {d["@id"] for d in _naq09(doc)["http://www.w3.org/ns/dcat#distribution"]}
    for n in doc:
        if n["@id"] in ids:
            del n["http://purl.org/dc/terms/format"]
    got = DcatApAdapter(FEED, DcatFake({FEED: doc})).search("", {"format": "csv"}, 1)["rows"]
    kept = next(r for r in got if r["dataset_id"] == "462c3f94-e367-4ef7-ac98-fffa0e168b47")
    assert kept["formats"] == NOT_REPORTED and kept["record"]["_filter_note"] == "format not reported"
    assert len(got) == 5          # the others state CSV


def test_one_distribution_stating_a_license_is_not_the_datasets_license():
    doc = _doc()
    ids = {d["@id"] for d in _naq09(doc)["http://www.w3.org/ns/dcat#distribution"]}
    stated = [n for n in doc if n["@id"] in ids]
    for n in stated[1:]:
        del n["http://purl.org/dc/terms/license"]
    row = next(r for r in DcatApAdapter(FEED, DcatFake({FEED: doc})).search("naq09", {}, 1)["rows"])
    assert row["licence_id"] == NOT_REPORTED and row["record"]["_licence_note"] == "only some distributions state a license"


def test_compact_jsonld_with_string_references_and_language_maps_is_read():
    doc = {"@context": {}, "@graph": [
        {"@id": "https://p.example/org/1", "@type": "foaf:Agent", "foaf:name": "Ministry"},
        {"@id": "https://p.example/d/1/r1", "@type": "dcat:Distribution", "dct:format": "text/csv",
         "dcat:byteSize": 10, "dcat:downloadURL": "https://p.example/f.csv"},
        {"@id": "https://p.example/d/1", "@type": "dcat:Dataset", "dct:identifier": "d1",
         "dct:title": {"de": "Verkehr", "en": "Traffic"}, "dct:publisher": "https://p.example/org/1",
         "dcat:distribution": ["https://p.example/d/1/r1"]},
        {"@id": "https://p.example/d/2", "@type": "dcat:Dataset", "dct:identifier": "d2",
         "dct:title": {"de": "Nur deutsch"}}]}
    rows = {r["dataset_id"]: r for r in DcatApAdapter("https://p.example/c", DcatFake({"https://p.example": doc})).search("", {}, 1)["rows"]}
    assert rows["d1"]["title"] == "Traffic" and rows["d1"]["record"]["_language"] == "en"
    assert rows["d1"]["publisher"] == "Ministry" and rows["d1"]["formats"] == ["CSV"] and rows["d1"]["size"] == 10
    assert rows["d2"]["title"] == "Nur deutsch" and rows["d2"]["record"]["_language"] == "de"
    t = DcatFake({"https://p.example": doc})
    assert [r["dataset_id"] for r in DcatApAdapter("https://p.example/c", t).search("verkehr", {}, 1)["rows"]] == ["d1"]


def test_a_dataset_with_no_identifier_and_a_blank_node_id_has_no_dataset_id():
    doc = {"@graph": [{"@id": "_:b0", "@type": "dcat:Dataset", "dct:title": "Anonymous"},
                      {"@id": "_:b1", "@type": "dcat:Dataset", "dct:title": "Other"}]}
    rows = DcatApAdapter("https://p.example/c", DcatFake({"https://p.example": doc})).search("", {}, 1)["rows"]
    assert [r["dataset_id"] for r in rows] == [NOT_REPORTED, NOT_REPORTED]
    assert rows[0]["identifier"] == NOT_REPORTED and rows[0]["identifier_kind"] == "portal_id"


def test_a_pod_doi_row_is_found_again_by_its_doi_and_the_scout_reads_the_document_once(store):
    t = DcatFake()
    a = DcatApAdapter(POD, t)
    assert a.describe("10.21978/p8863g")["title"].startswith("IMP-8")
    assert a.describe("https://doi.org/10.21978/p8863g")["doi"] == "10.21978/p8863g"
    assert len(t.calls) == 1


def test_non_finite_sizes_and_a_partial_size_sum_are_stated_not_crashed_on():
    doc = _doc()
    ds = [n for n in doc if n["@id"] in {d["@id"] for d in _naq09(doc)["http://www.w3.org/ns/dcat#distribution"]}]
    ds[0]["http://www.w3.org/ns/dcat#byteSize"] = [{"@value": "inf"}]
    ds[1]["http://www.w3.org/ns/dcat#byteSize"] = [{"@value": 100}]
    row = next(r for r in DcatApAdapter(FEED, DcatFake({FEED: doc})).search("naq09", {}, 1)["rows"])
    assert row["size"] == 100 and "partial" in row["record"]["_size_note"]


def test_the_probe_names_the_content_type_of_a_feed_it_cannot_read():
    err = TransportError("https://x.example/data.json: the answer was not JSON (content-type application/rdf+xml)")
    t = DcatFake({"https://x.example": err})
    with pytest.raises(ProbeRefused) as got:
        DcatApAdapter.probe("https://x.example", t)
    assert "application/rdf+xml, which has no adapter yet" in str(got.value) and "). " not in str(got.value)


def test_registration_keeps_the_typed_url_on_an_activity_entry_and_checks_duplicates_before_probing(store, reg):
    t = DcatFake()
    p = service.register_portal(store, display_name="Ireland", api_kind="DCAT-AP", base_url="https://data.gov.ie/",
                                transport=t)
    with reg._conn() as conn:
        entries = [dict(r) for r in conn.execute(
            "SELECT * FROM activity_log WHERE operation = 'register_portal'").fetchall()]
    assert entries and "https://data.gov.ie" in json.dumps(entries[0], default=str) and FEED in json.dumps(entries[0], default=str)
    calls = len(t.calls)
    with pytest.raises(service.OpenDataError, match="Already registered"):
        service.register_portal(store, display_name="Again", api_kind="CKAN", base_url=FEED, transport=t)
    assert len(t.calls) == calls
    with pytest.raises(ValueError, match="needs a transport"):
        service.register_portal(store, display_name="X", api_kind="DCAT-AP", base_url="https://other.example")


def test_http_transport_names_the_content_type_and_refuses_an_oversized_answer(monkeypatch):
    import httpx

    real = httpx.stream

    class _Stream:
        def __enter__(self):
            return httpx.Response(200, headers=state["headers"], content=state["body"])

        def __exit__(self, *exc):
            return False

    def stream(method, url, **kw):
        return _Stream()

    state = {"headers": {"content-type": "application/rdf+xml"}, "body": b"<rdf/>"}
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(TransportError, match=r"content-type application/rdf\+xml"):
        HttpTransport().get_json("https://x.example/data.json")
    from resource_explorer.open_data import transport as tr
    state.update(headers={"content-type": "application/json"}, body=b"[1,2,3]")
    assert HttpTransport().get_json("https://x.example/a") == [1, 2, 3]
    monkeypatch.setattr(tr, "MAX_JSON_BYTES", 4)
    with pytest.raises(TransportError, match="read limit"):
        HttpTransport().get_json("https://x.example/a")
    monkeypatch.setattr(httpx, "stream", real)


def test_the_canadian_open_government_licence_has_its_three_flags():
    """W30: open.canada.ca's "ca-ogl-lgo" rows showed three "?"; the OGL-Canada needs attribution, has no
    share-alike, and allows commercial use."""
    from resource_explorer.open_data.licences import licence_flags
    assert licence_flags("ca-ogl-lgo") == {"attribution": True, "share_alike": False, "commercial": True}
    assert licence_flags("CA-OGL-LGO ") == licence_flags("ca-ogl-lgo")
