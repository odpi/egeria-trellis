"""ExternalDataScout, the fit row / winnow and Q-0011 overlap (OD2 Part C). Recorded demo.ckan.org fixtures behind
the fake transport; no live call. Two rows' licenses are rewritten to cc-by-sa in memory (the recorded portal has no
share-alike row) and SAYS so where it matters."""
from __future__ import annotations

import pytest
from tests.test_open_data import FakeTransport, NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: F401 - fixtures

from resource_explorer.open_data import fit, service
from resource_explorer.open_data.absence import NOT_REPORTED

NAMES = ["my-org--pcns", "child-gg", "my-sample-dataset-001", "demo-dataset-rust-1234567890", "annakarenina",
         "demo-dataset-rust-123456789", "demo-dataset-rust-1756884178", "demo-dataset-rust-1756883815",
         "demo-dataset-rust-1756883163", "warandpeace"]
SHARE_ALIKE_ROWS = ("demo-dataset-rust-1756884178", "demo-dataset-rust-1756883815")   # SYNTHETIC: rewritten to cc-by-sa


class SaFake(FakeTransport):
    def _records(self):
        out = super()._records()
        for r in out:
            if r["name"] in SHARE_ALIKE_ROWS:
                r["license_id"] = "cc-by-sa"
        return out


@pytest.fixture
def imported(store):
    portal = _register_demo(store)
    got = service.import_datasets(store, _chosen(portal["slug"], *NAMES), work_list_name="shortlist",
                                  transport=SaFake(), egeria_lookup=NO_EGERIA)
    assert got["imported"] == 10
    return got["work_list"]


def test_the_scout_is_priced_as_n_calls_and_makes_exactly_n(store, imported):
    n = len(store.datasets_in_work_list(imported))
    assert service.scout_price(n)["line"] == "10 datasets × 1 call = 10 calls"
    t = SaFake()
    out = service.scout_work_list(store, imported, transport=t)
    assert out["scouted"] == 10 and out["could_not_scout"] == 0 and len(t.calls) == 10
    assert all("no change on their side" in r["words"] for r in out["rows"])


def test_a_failed_read_keeps_what_the_dataset_had_and_is_said(store, imported):
    before = {d["slug"]: d["publisher"] for d in store.datasets_in_work_list(imported)}

    class Down(SaFake):
        def get_json(self, url, params=None, headers=None):
            raise ConnectionError("portal is down")

    out = service.scout_work_list(store, imported, transport=Down())
    assert out["could_not_scout"] == 10 and "portal is down" in out["rows"][0]["words"]
    assert {d["slug"]: d["publisher"] for d in store.datasets_in_work_list(imported)} == before


def test_a_license_filter_drops_the_share_alike_rows_and_says_so(store, imported):
    got = service.fit_work_list(store, imported, {"licence": {"share_alike": False}})
    dropped = [r for r in got["rows"] if r["verdict"] == "dropped"]
    assert len(dropped) == 2 and got["dropped"] == 2
    assert "2 dropped on license" in got["summary"]
    assert all(r["cells"]["license"]["status"] == "does_not_fit" for r in dropped)
    assert len(got["rows"]) == 10                                   # dropped rows are listed, never removed


def test_missing_coverage_is_could_not_check_never_dropped(store, imported):
    got = service.fit_work_list(store, imported, {"period": "2005-2010", "places": ["Global"]})
    by = {r["title"]: r for r in got["rows"]}
    stated = [r for r in got["rows"] if r["coverage"]["period"] != NOT_REPORTED]
    assert len(stated) == 1 and stated[0]["cells"]["period"]["status"] == "fits"          # my-sample-dataset-001, 2000-2020
    unstated = [r for r in got["rows"] if r["coverage"]["period"] == NOT_REPORTED]
    assert len(unstated) == 9
    assert all(r["cells"]["period"]["status"] == "could_not_check" and r["verdict"] == "open" for r in unstated)
    assert got["dropped"] == 0 and got["could_not_check"] == 9 and len(by) == 10
    out_of_range = service.fit_work_list(store, imported, {"period": "2025"})
    assert [r["cells"]["period"]["status"] for r in out_of_range["rows"] if r["coverage"]["period"] != NOT_REPORTED] == ["does_not_fit"]


def test_a_request_with_no_license_need_never_passes_a_row_on_it(store, imported):
    got = service.fit_work_list(store, imported, {})
    assert all(r["cells"]["license"] == {"status": "not_stated", "words": "license need not stated"} for r in got["rows"])
    assert all(r["verdict"] == "open" for r in got["rows"])         # nothing stated, so nothing passes


def test_an_unknown_license_flag_is_could_not_check(store, imported):
    got = service.fit_work_list(store, imported, {"licence": {"commercial": True}})
    child = next(r for r in got["rows"] if "child" in r["title"].lower() or r["identifier"].endswith("child-gg"))
    assert child["cells"]["license"]["status"] == "could_not_check"


def test_format_and_size_cells(store, imported):
    got = service.fit_work_list(store, imported, {"formats": ["csv"], "max_size_bytes": "1000"})
    by = {r["identifier"].split(":")[-1]: r for r in got["rows"]}
    assert by["my-sample-dataset-001"]["cells"]["format"]["status"] == "fits"
    assert by["annakarenina"]["cells"]["format"]["status"] == "does_not_fit"
    assert by["warandpeace"]["cells"]["format"]["status"] == "could_not_check"      # the portal lists no formats
    assert by["warandpeace"]["cells"]["size"]["status"] in ("could_not_check", "fits")


def test_overlap_flags_a_near_duplicate_and_says_what_was_compared(store, imported):
    held = store.list_datasets()
    rust = [d for d in held if "rust" in d["identifier"]]
    got = fit.overlap(rust[0], held)
    assert got["status"] == "may_duplicate" and got["matches"]
    assert "registered resources and Egeria are not compared yet" in got["words"]
    alone = fit.overlap(held[0], [held[0]])
    assert alone["status"] == "none_found" and alone["compared"] == 0


def test_scout_and_fit_routes(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from resource_explorer.open_data.store import OpenDataStore
    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    monkeypatch.setattr(routes, "_transport", lambda: SaFake())
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    c = TestClient(app)
    portal = _register_demo(OpenDataStore(reg))
    wl = service.import_datasets(OpenDataStore(reg), _chosen(portal["slug"], *NAMES[:3]), work_list_name="r",
                                 transport=SaFake(), egeria_lookup=NO_EGERIA)["work_list"]
    r = c.get(f"/api/open-data/work-lists/{wl}/scout-price")
    assert r.status_code == 200, (wl, r.text)
    assert r.json()["line"] == "3 datasets × 1 call = 3 calls"
    assert c.post(f"/api/open-data/work-lists/{wl}/scout").json()["scouted"] == 3
    assert c.post(f"/api/open-data/work-lists/{wl}/fit", json={"request": {"formats": ["CSV"]}}).json()["passes"] == 0
    assert c.get("/api/open-data/work-lists/nope/scout-price").status_code == 404
    assert c.post("/api/open-data/work-lists/nope/fit", json={}).status_code == 404


# ── re3data (recorded 2026-10-30 01:19Z, GET https://www.re3data.org/api/v1/repositories, first 40 of 3540) ──────

def test_re3data_lists_repositories_with_only_what_the_listing_states(store):
    from tests.test_open_data import FakeTransport as Fake

    d = service.register_directory(store, display_name="re3data", api_kind="re3data",
                                   base_url="https://www.re3data.org/api/v1/repositories", registered_by="t")
    t = Fake()
    out = service.run_directory(store, d["slug"], pages=1, terms="", transport=t)
    assert out["total"] == 40 and len(out["rows"]) == 40 and t.calls == ["https://www.re3data.org/api/v1/repositories"]
    assert out["price"]["line"] == "1 page × 1 call = 1 call"
    first = out["rows"][0]
    assert first["name"] == "Odum Institute Archive Dataverse"
    assert first["country"] == first["domain"] == first["api_kind"] == first["url"] == NOT_REPORTED   # not in the listing
    assert "no adapter" in first["status"] or "not reported" in first["status"] or first["status"]


def test_re3data_refuses_a_listing_with_a_dtd_and_a_non_xml_answer():
    from resource_explorer.open_data.re3data import Re3dataDirectory
    from resource_explorer.open_data.transport import TransportError

    class T:
        def __init__(self, text): self.text = text
        def get_text(self, url, params=None, headers=None): return self.text

    for bad in ('<!DOCTYPE x [<!ENTITY a "b">]><list/>', "not xml"):
        with pytest.raises(TransportError):
            Re3dataDirectory(transport=T(bad)).list_portals(1)


def test_the_re3data_fixture_is_the_recorded_shape():
    from tests.test_open_data import FIX

    text = (FIX / "re3data_repositories.xml").read_text()
    assert text.count("<repository>") == 40 and "<id>r3d100000001</id>" in text


def test_the_scout_is_declared_in_the_analysis_catalog_once():
    from resource_explorer.surveyors import analysis_catalog_reader as reader

    entries = reader.get_analyses("open_data", include_egeria_live=False)
    assert [e["id"] for e in entries] == ["external_data_scout", "external_data_profile", "external_data_refresh"]   # + OD3, OD4
    e = entries[0]
    assert e["resource_types"] == ["open_data"] and e["availability"] == "inline" and e["action"] == "survey"
    assert not [x for x in reader.get_analyses("repo", include_egeria_live=False) if x["id"] == "external_data_scout"]


# ── B4: the data request on an investigation (table approved by the project owner, 2026-10-30) ─────────────────

def _routes_client(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from resource_explorer.open_data.store import OpenDataStore
    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    monkeypatch.setattr(routes, "_transport", lambda: SaFake())
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    return TestClient(app)


def test_the_data_request_round_trips_and_an_old_investigation_reads_not_stated(reg, store, monkeypatch):
    c = _routes_client(reg, monkeypatch)
    none = c.get("/api/open-data/investigations/inv-old/data-request").json()
    assert none == {"investigation": "inv-old", "stated": False, "stored": True, "request": None}
    saved = c.put("/api/open-data/investigations/inv-1/data-request", json={
        "subjects": ["flood", " rainfall "], "period": "2019-2023", "places": ["Texas"], "grain": "daily",
        "licence": {"commercial": True, "share_alike": False}, "formats": ["csv"]}).json()
    assert saved["request"]["subjects"] == ["flood", "rainfall"] and saved["request"]["formats"] == ["CSV"]
    assert saved["request"]["licence"] == {"commercial": True, "share_alike": False} and saved["request"]["updated_by"] == "tester"
    assert c.get("/api/open-data/investigations/inv-1/data-request").json()["stated"] is True
    assert c.get("/api/open-data/investigations/inv-2/data-request").json()["stated"] is False     # per investigation


def test_a_missing_table_is_no_data_request_stated_and_a_save_says_why(reg, store, monkeypatch):
    with reg._conn() as conn:
        conn.execute("DROP TABLE investigation_data_requests")
    c = _routes_client(reg, monkeypatch)
    got = c.get("/api/open-data/investigations/inv-1/data-request").json()
    assert got["stated"] is False and got["stored"] is False and got["request"] is None
    r = c.put("/api/open-data/investigations/inv-1/data-request", json={"period": "2020"})
    assert r.status_code == 409 and "cannot be stored yet" in r.json()["detail"]
    assert c.post("/api/open-data/lens-terms?investigation=inv-1", json={}).status_code == 200      # still answers


def test_the_stored_request_feeds_lens_terms_and_the_winnow(reg, store, monkeypatch):
    c = _routes_client(reg, monkeypatch)
    c.put("/api/open-data/investigations/inv-1/data-request", json={
        "subjects": ["flood"], "places": ["Texas"], "period": "2019-2023", "licence": {"share_alike": False}})
    terms = c.post("/api/open-data/lens-terms?investigation=inv-1", json={}).json()
    assert terms["terms"] == "flood Texas" and terms["filters"] == {"modified_after": "2019-01-01"} and terms["source"] == "data request"
    portal = _register_demo(store)
    wl = service.import_datasets(store, _chosen(portal["slug"], *NAMES), work_list_name="r", transport=SaFake(),
                                 egeria_lookup=NO_EGERIA)["work_list"]
    got = c.post(f"/api/open-data/work-lists/{wl}/fit", json={"request": {}, "investigation": "inv-1"}).json()
    assert got["request_source"] == "the data request on inv-1" and got["dropped"] == 2          # the two share-alike rows; "Global" covers Texas
    plain = c.post(f"/api/open-data/work-lists/{wl}/fit", json={"request": {}, "investigation": "inv-none"}).json()
    assert plain["request_source"] == "no data request stated" and plain["dropped"] == 0


def test_a_global_coverage_statement_covers_any_place_and_a_stated_other_place_still_drops(store, imported):
    got = service.fit_work_list(store, imported, {"places": ["Texas"]})
    stated = next(r for r in got["rows"] if r["coverage"]["places"] != NOT_REPORTED)       # states "Global"
    assert stated["cells"]["place"] == {"status": "fits", "words": "covers Texas (global)"}
    assert got["dropped"] == 0
    ds = {"slug": "x", "title": "t", "identifier": "i", "licence": {}, "formats": [], "size": NOT_REPORTED, "licence_id": NOT_REPORTED,
          "record": {"extras": [{"key": "spatial_coverage", "value": "France"}]}}
    assert fit.fit_row(ds, fit.DataRequest(places=["Texas"]))["cells"]["place"]["status"] == "does_not_fit"
    ds["record"]["extras"][0]["value"] = "Worldwide"
    assert fit.fit_row(ds, fit.DataRequest(places=["Texas"]))["cells"]["place"]["words"] == "covers Texas (global)"
