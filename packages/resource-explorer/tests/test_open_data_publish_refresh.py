"""Publish (OD4) and ExternalDataRefresh. The Egeria port is a FAKE that records every call; nothing is written to
Egeria. The dataset records are the recorded demo.ckan.org fixtures; their coverage/licence extras and the "a day
later" change are SYNTHETIC and said so where they matter."""
from __future__ import annotations

import pytest
from tests.test_open_data import FakeTransport, NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: F401 - fixtures

from resource_explorer.open_data import publish, refresh, service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.transport import TransportError

NAME = "annakarenina"


def _resources(n_changes=0):
    res = [{"id": "r1", "name": "a.csv", "format": "CSV", "url": "https://f.example.test/a.csv", "size": 10},
           {"id": "r2", "name": "b.csv", "format": "CSV", "url": "https://f.example.test/b.csv", "size": 20}]
    if n_changes:
        res[1]["size"] = 99                                      # SYNTHETIC: b.csv grew
        res.append({"id": "r3", "name": "c.csv", "format": "CSV", "url": "https://f.example.test/c.csv", "size": 5})
    return res


class Portal(FakeTransport):
    """Serves annakarenina with synthetic distributions, and a synthetic change `later`."""

    def __init__(self, later=False, coverage=True, licence="cc-by"):
        super().__init__()
        self.later, self.coverage, self.licence = later, coverage, licence

    def _records(self):
        out = super()._records()
        for r in out:
            if r["name"] == NAME:
                r["resources"] = _resources(1 if self.later else 0)
                r["license_id"] = self.licence
                r["license_url"] = "https://creativecommons.org/licenses/by/4.0/"
                if self.coverage:
                    r["extras"] = [{"key": "temporal_start", "value": "2019-01-01"}, {"key": "temporal_end", "value": "2023-12-31"},
                                   {"key": "spatial_coverage", "value": "Texas"}]
                if self.later:
                    r["metadata_modified"] = "2026-11-01T00:00:00.000000"
        return out


class FakeEgeria:
    def __init__(self, held=None, licence_types=("cc-by",), fail=None, back_ok=True):
        self.held, self.calls, self.licence_types, self.fail, self.back_ok = dict(held or {}), [], set(licence_types), fail, back_ok
        self.assets = {}

    def find_guid(self, qualified_name, type_name):
        self.calls.append(("find", qualified_name))
        if type_name == "LicenseType":
            return "lt-guid" if qualified_name.split("::", 1)[1] in self.licence_types else ""
        return self.held.get(qualified_name, "")

    def create_asset(self, body):
        self.calls.append(("create", body["properties"]["qualifiedName"]))
        guid = f"guid-{len(self.assets) + 1}"
        self.assets[guid] = body
        return guid

    def add_origin(self, guid, body):
        self.calls.append(("origin", body["properties"]["owner"]))
        if self.fail == "origin":
            raise RuntimeError("Egeria refused the classification")

    def add_scope(self, guid, body):
        self.calls.append(("scope", body["properties"]))

    def link_license(self, license_type_guid, guid, body):
        self.calls.append(("license", body["properties"]["licenseId"]))
        return "rel-guid"

    def read_back(self, guid):
        self.calls.append(("read_back", guid))
        a = self.assets.get(guid)
        if not self.back_ok or not a:
            return {"matched": False, "why": "Egeria does not hold an element with that GUID"}
        return {"matched": True, "type_name": "DataSet", "qualified_name": a["properties"]["qualifiedName"],
                "licence_id": a["properties"]["additionalProperties"].get("licenceId", ""), "has_data_scope": True}


@pytest.fixture
def one(store):
    portal = _register_demo(store)
    got = service.import_datasets(store, _chosen(portal["slug"], NAME), work_list_name="one",
                                  transport=Portal(), egeria_lookup=NO_EGERIA)
    assert got["imported"] == 1
    return got["work_list"], store.datasets_in_work_list(got["work_list"])[0]


# ── publish ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_dataset_is_published_with_its_licence_origin_and_scope_and_read_back_by_guid(store, one):
    wl, ds = one
    store.update_scouted(ds["slug"], service.portal_adapter(store.get_portal(ds["portal_slug"]), Portal()).describe(NAME), "t")   # the Portal stamps coverage and a license url
    ds = dict(store.get_dataset(ds["slug"]), publisher="Texas Open Data")        # SYNTHETIC: the recorded dataset names none
    egeria = FakeEgeria()
    proof = publish.publish_dataset(store, ds, egeria)
    assert proof["complete"] is True and proof["guid"] == "guid-1" and proof["qualified_name"] == f"OpenData::{ds['identifier']}"
    kinds = [c[0] for c in egeria.calls]
    assert kinds[:1] == ["find"] and "create" in kinds and kinds[-1] == "read_back"
    assert ("origin", ds["publisher"]) in egeria.calls and ("license", "cc-by") in egeria.calls
    scope = next(c[1] for c in egeria.calls if c[0] == "scope")
    assert scope["dataCoverageStartTime"].startswith("2019-01-01") and scope["additionalProperties"]["place"] == "Texas"
    assert {s["step"]: s["outcome"] for s in proof["steps"]} == {"asset": "created", "origin": "written", "scope": "written",
                                                                  "licence": "linked"}
    assert proof["read_back"]["matched"] and proof["read_back"]["licence_id"] == "cc-by"
    assert publish.latest_proof(store.registry, ds["slug"])["guid"] == "guid-1"


def test_a_second_press_reuses_the_asset_and_creates_nothing(store, one):
    _, ds = one
    egeria = FakeEgeria(held={f"OpenData::{ds['identifier']}": "guid-7"})
    egeria.assets["guid-7"] = publish.asset_body(ds)
    proof = publish.publish_dataset(store, ds, egeria)
    assert proof["reused"] is True and proof["steps"][0]["outcome"] == "reused" and "create" not in [c[0] for c in egeria.calls]


def test_coverage_the_portal_did_not_state_writes_no_scope_and_says_so(store, one):
    _, ds = one
    ds = dict(ds, record={k: v for k, v in ds["record"].items() if k != "extras"})     # the portal stated no coverage
    assert publish.scope_body(ds) is None
    proof = publish.publish_dataset(store, ds, FakeEgeria())
    scope = next(s for s in proof["steps"] if s["step"] == "scope")
    assert scope["outcome"] == "not_written" and "not reported by this source" in scope["words"]


def test_a_publisher_the_portal_did_not_name_writes_no_origin_and_says_so(store, one):
    _, ds = one
    assert ds["publisher"] == NOT_REPORTED and publish.origin_body(ds) is None
    origin = next(s for s in publish.publish_dataset(store, ds, FakeEgeria())["steps"] if s["step"] == "origin")
    assert origin["outcome"] == "not_written" and "not reported by this source" in origin["words"]


def test_a_licence_egeria_has_no_type_for_is_kept_as_a_property_and_said(store, one):
    _, ds = one
    proof = publish.publish_dataset(store, ds, FakeEgeria(licence_types=()))
    lic = next(s for s in proof["steps"] if s["step"] == "licence")
    assert lic["outcome"] == "kept_as_property" and "Egeria holds no license type" in lic["words"]


def test_no_licence_reported_links_nothing_and_says_so(store, one):
    _, ds = one
    ds = dict(ds, licence_id=NOT_REPORTED)
    proof = publish.publish_dataset(store, ds, FakeEgeria())
    lic = next(s for s in proof["steps"] if s["step"] == "licence")
    assert lic["outcome"] == "not_written" and "not reported by this source" in lic["words"]


def test_a_failed_classification_is_a_row_that_says_so_and_the_publish_is_not_complete(store, one):
    _, ds = one
    ds = dict(ds, publisher="Texas Open Data")
    proof = publish.publish_dataset(store, ds, FakeEgeria(fail="origin"))
    origin = next(s for s in proof["steps"] if s["step"] == "origin")
    assert origin["outcome"] == "failed" and "refused" in origin["words"] and proof["complete"] is False


def test_a_read_back_that_does_not_match_is_not_complete(store, one):
    _, ds = one
    proof = publish.publish_dataset(store, ds, FakeEgeria(back_ok=False))
    assert proof["complete"] is False and proof["read_back"]["why"]


def test_everything_the_publisher_stated_is_marked_stated_by_the_publisher(store, one):
    _, ds = one
    props = publish.asset_body(ds)["properties"]["additionalProperties"]
    assert props["statedBy"].startswith("stated by the publisher") and props["externalIdentifier"] == ds["identifier"]


def test_a_private_investigation_refuses_before_any_write(store, one, monkeypatch):
    from resource_explorer import private_publish

    _, ds = one
    monkeypatch.setattr(store.registry, "private_owner_for_entity", lambda t, s: "someone", raising=False)
    egeria = FakeEgeria()
    with pytest.raises(publish.PublishRefused):
        publish.publish_dataset(store, ds, egeria)
    assert egeria.calls == []


# ── refresh ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_price_is_two_calls_per_dataset():
    assert refresh.refresh_price(3)["line"] == "3 datasets × 2 calls (the dataset, its distributions) = 6 calls"


def test_the_first_look_does_not_claim_no_change_about_distributions_it_never_held(store, one):
    wl, _ = one
    out = refresh.refresh_work_list(store, wl, transport=Portal())
    r = out["rows"][0]
    assert r["outcome"] == "first_look" and "nothing earlier to compare" in r["words"] and NO_CHANGE_WORDS not in r["words"]


NO_CHANGE_WORDS = "no change on their side"


def test_a_second_look_with_nothing_new_says_no_change_on_their_side(store, one):
    wl, _ = one
    refresh.refresh_work_list(store, wl, transport=Portal())
    again = refresh.refresh_work_list(store, wl, transport=Portal())
    assert again["rows"][0]["words"] == NO_CHANGE_WORDS and again["unchanged"] == 1


def test_a_look_a_day_later_shows_the_diff(store, one):
    wl, _ = one
    refresh.refresh_work_list(store, wl, transport=Portal())
    later = refresh.refresh_work_list(store, wl, transport=Portal(later=True))
    r = later["rows"][0]
    assert r["outcome"] == "changed" and later["changed"] == 1
    whats = [c["words"] for c in r["changes"]]
    assert any(w.startswith("modified:") for w in whats)
    assert "distribution added: c.csv" in whats and any("b.csv: size 20 → 99" in w for w in whats)


def test_a_portal_that_does_not_answer_keeps_the_earlier_look_and_values(store, one):
    wl, ds = one
    refresh.refresh_work_list(store, wl, transport=Portal())
    before = store.get_dataset(ds["slug"])["modified"]

    class Down(Portal):
        def get_json(self, url, params=None, headers=None):
            raise TransportError("portal is down")

    out = refresh.refresh_work_list(store, wl, transport=Down(later=True))
    assert out["could_not_refresh"] == 1 and "portal is down" in out["rows"][0]["words"]
    assert store.get_dataset(ds["slug"])["modified"] == before
    assert refresh.refresh_work_list(store, wl, transport=Portal())["rows"][0]["words"] == NO_CHANGE_WORDS


def test_a_work_list_with_no_open_data_cannot_be_refreshed(store):
    with pytest.raises(service.NotFound):
        refresh.refresh_work_list(store, "none", transport=Portal())


def test_the_routes_publish_refresh_and_read_the_proof(store, one, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from resource_explorer.web.routes import open_data as routes

    wl, ds = one
    egeria = FakeEgeria()
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_transport", lambda: Portal())
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    monkeypatch.setattr(routes, "_egeria_port", lambda: egeria)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    c = TestClient(app)
    assert c.get(f"/api/open-data/work-lists/{wl}/refresh-price").json()["calls"] == 2
    assert c.post(f"/api/open-data/work-lists/{wl}/refresh").json()["first_look"] == 1
    assert c.get(f"/api/open-data/datasets/{ds['slug']}/publish").json()["words"] == "Not published to Egeria yet"
    assert c.post(f"/api/open-data/datasets/{ds['slug']}/publish").json()["guid"] == "guid-1"
    assert c.get(f"/api/open-data/datasets/{ds['slug']}/publish").json()["proof"]["guid"] == "guid-1"
    assert c.post("/api/open-data/datasets/nope/publish").status_code == 404
