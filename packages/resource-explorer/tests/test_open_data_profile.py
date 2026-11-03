"""ExternalDataProfile (OD3). The datasets are the recorded demo.ckan.org fixtures; their DISTRIBUTIONS are SYNTHETIC
(the recorded portal's resources point nowhere useful), served by the fake transport's `get_bytes`. No live call."""
from __future__ import annotations

import pytest
from tests.test_open_data import FakeTransport, NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: F401 - fixtures

from resource_explorer.open_data import profile, service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.transport import HttpTransport, TransportError

A, B, C = "annakarenina", "warandpeace", "child-gg"
CSV_A = b"name,email,visits\nAda,ada@example.org,3\nBo,bo@example.org,5\nCy,cy@example.org,8\n"
CSV_B = b"name,email,visits\nDee,dee@example.org,1\n"
JSONL_A = b'{"id": 1, "city": "Leeds"}\n{"id": 2, "city": "York"}\n'
DICTIONARY = b"# Data dictionary\n\nvisits: the number of visits in the year.\n\n## email\n\nThe contact address.\n"
BASE = "https://files.example.test/"


def _res(name, fmt, size=None, rid=None):
    r = {"id": rid or name, "name": name, "format": fmt, "url": BASE + name}
    if size is not None:
        r["size"] = size
    return r


RESOURCES = {
    A: [_res("visits.csv", "CSV", 90), _res("cities.jsonl", "JSONL", 60), _res("data_dictionary.md", "MD", 120),
        _res("huge.csv", "CSV", 80 * 1024 * 1024), _res("odd.xyz", "XYZ")],
    B: [_res("visits_2.csv", "CSV", 49)],
    C: [_res("mystery.csv", "CSV")],                       # no stated size, and the fake truncates it
}
BODIES = {"visits.csv": CSV_A, "cities.jsonl": JSONL_A, "data_dictionary.md": DICTIONARY, "visits_2.csv": CSV_B}


class NoCatalog:
    available = False
    unavailable_reason = "Egeria's data classes could not be read (test)"
    data_classes: list = []


class ProfileFake(FakeTransport):
    def __init__(self):
        super().__init__()
        self.fetched: list[str] = []

    def _records(self):
        out = super()._records()
        for r in out:
            if r["name"] in RESOURCES:
                r["resources"] = [dict(x) for x in RESOURCES[r["name"]]]
        return out

    def get_bytes(self, url, max_bytes, headers=None):
        self.calls.append(url)
        self.fetched.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "mystery.csv":
            return b"x" * max_bytes, None, True
        if name in BODIES:
            return BODIES[name], len(BODIES[name]), False
        raise TransportError(f"{url}: HTTP 404")


@pytest.fixture
def listed(store):
    portal = _register_demo(store)
    got = service.import_datasets(store, _chosen(portal["slug"], A, B, C), work_list_name="shortlist",
                                  transport=ProfileFake(), egeria_lookup=NO_EGERIA)
    assert got["imported"] == 3
    return got["work_list"]


def _run(store, wl, t=None):
    t = t or ProfileFake()
    return profile.profile_work_list(store, wl, transport=t, catalog_loader=NoCatalog), t


def _by_title(out):
    return {d["slug"]: d for d in out["datasets"]}


def test_the_price_line_is_an_upper_bound_stated_before_the_press():
    assert profile.profile_price(3)["line"].startswith("3 datasets × up to 6 calls")
    assert "capped at 50 MB" in profile.profile_price(3)["line"]
    assert profile.profile_price(1)["line"].startswith("1 dataset × up to 6 calls")


def test_three_distributions_are_profiled_and_each_carries_its_sample_ratio(store, listed):
    out, _ = _run(store, listed)
    done = [d for ds in out["datasets"] for d in ds["distributions"] if d["state"] == profile.PROFILED]
    assert sorted(d["name"] for d in done) == ["cities.jsonl", "visits.csv", "visits_2.csv"]
    for d in done:
        assert d["sample_ratio"] == 1.0 and "sample ratio 1" in d["words"] and "read whole" in d["words"]
        assert d["rows"] >= 1 and d["retrieved_at"]
        assert all(c["sample_ratio"] == 1.0 for c in d["columns"])
    csv = next(d for d in done if d["name"] == "visits.csv")
    assert csv["rows"] == 3 and [c["column_name"] for c in csv["columns"]] == ["name", "email", "visits"]


def test_a_distribution_over_the_cap_says_so_and_is_never_zero_or_read(store, listed):
    out, t = _run(store, listed)
    a = next(d for d in out["datasets"] if "anna" in d["slug"])
    huge = next(d for d in a["distributions"] if d["name"] == "huge.csv")
    assert huge["state"] == profile.OVER_CAP and huge["words"].startswith("not profiled · over the cap")
    assert "columns" not in huge and "rows" not in huge
    assert BASE + "huge.csv" not in t.fetched                     # a stated size over the cap is never downloaded


def test_a_file_that_turns_out_longer_than_the_cap_is_also_not_profiled(store, listed):
    out, _ = _run(store, listed)
    c = next(d for d in out["datasets"] if "child" in d["slug"])
    only = c["distributions"][0]
    assert only["state"] == profile.OVER_CAP and "longer than the 50 MB cap" in only["words"]
    assert c["outcome"] == "not_profiled"


def test_a_format_not_read_and_a_failed_download_are_each_said(store, listed):
    out, _ = _run(store, listed)
    a = next(d for d in out["datasets"] if "anna" in d["slug"])
    odd = next(d for d in a["distributions"] if d["name"] == "odd.xyz")
    assert odd["state"] == profile.NOT_READ_FORMAT and "not read here" in odd["words"]

    class Gone(ProfileFake):
        def get_bytes(self, url, max_bytes, headers=None):
            raise TransportError(f"{url}: HTTP 503")

    gone, _ = _run(store, listed, Gone())
    d = next(x for ds in gone["datasets"] for x in ds["distributions"] if x["name"] == "visits.csv")
    assert d["state"] == profile.DOWNLOAD_FAILED and "HTTP 503" in d["words"]


def test_the_data_dictionary_is_read_as_evidence_stated_by_its_file(store, listed):
    out, _ = _run(store, listed)
    a = next(d for d in out["datasets"] if "anna" in d["slug"])
    doc = a["documents"][0]
    assert doc["state"] == "read" and "a proposal, never a fact" in doc["words"]
    stated = {p["stated_by"] for p in doc["passages"]}
    assert "data_dictionary.md § Data dictionary" in stated or "data_dictionary.md § email" in stated
    assert all(p["status"] == "proposal" for p in doc["passages"])


def test_the_class_match_says_why_when_egeria_could_not_be_read(store, listed):
    out, _ = _run(store, listed)
    a = next(d for d in out["datasets"] if "anna" in d["slug"])
    csv = next(d for d in a["distributions"] if d["name"] == "visits.csv")
    email = next(m for m in csv["matches"] if m["column"] == "email")
    assert email["established"] is False
    assert "could not be read" in (email.get("not_established_reason") or "")


def test_the_fingerprint_finds_the_same_columns_in_a_second_dataset_and_says_what_it_did_not_compare(store, listed):
    out, _ = _run(store, listed)
    b = next(d for d in out["datasets"] if "war" in d["slug"])
    # the first dataset in the run has nothing held to compare with; the second sees the first's visits.csv
    hits = b["fingerprint_overlap"]
    assert hits and hits[0]["same"] is True and hits[0]["with_distribution"] == "visits.csv"
    assert "registered databases and file systems are not compared yet" in b["fingerprint_scope"]
    assert profile.fingerprint_of(["Name", "EMail", "visits"]) == profile.fingerprint_of(["visits", "name", "email"])
    assert profile.fingerprint_of([]) == ""


def test_the_provenance_carries_what_the_registration_holds_and_no_doi_is_said(store, listed):
    out, _ = _run(store, listed)
    a = next(d for d in out["datasets"] if "anna" in d["slug"])
    p = a["provenance"]
    assert p["doi"] == NOT_REPORTED and p["datacite"] == "no DOI reported" and p["portals"]
    assert p["harvested_from"] == NOT_REPORTED


def test_the_result_is_kept_per_dataset_and_read_back_by_the_view(store, listed):
    out, _ = _run(store, listed)
    seen = profile.view(store, listed)
    assert len(seen["datasets"]) == 3 and all(d["profile"] for d in seen["datasets"])
    first = seen["datasets"][0]["profile"]
    assert first["distributions"] and first["ran_at"]
    fresh = profile.view(store, "no_such_list")
    assert fresh["datasets"] == []


def test_a_dataset_never_profiled_says_never_run(store, listed):
    seen = profile.view(store, listed)
    assert all(d["profile"] is None and d["words"] == "Nothing has been measured for this yet · never run"
               for d in seen["datasets"])


def test_a_work_list_without_open_data_has_nothing_to_profile(store):
    with pytest.raises(profile.NothingToProfile):
        profile.profile_work_list(store, "empty", transport=ProfileFake(), catalog_loader=NoCatalog)


def test_a_second_press_while_one_runs_is_refused(store, listed):
    reg_ = store.registry
    assert reg_.take_claim(f"{profile.STEP_KEY}:{listed}", "other", stale_after_seconds=900)
    with pytest.raises(profile.AlreadyRunning):
        _run(store, listed)


def test_a_portal_that_will_not_list_distributions_is_a_row_that_says_so(store, listed):
    class Down(ProfileFake):
        def get_json(self, url, params=None, headers=None):
            raise TransportError("portal is down")

    out, _ = _run(store, listed, Down())
    assert out["profiled"] == 0 and all("could not list the distributions" in d["words"] for d in out["datasets"])


def test_the_fake_get_bytes_binds_the_real_signature():
    import inspect

    shape = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]   # noqa: E731
    assert shape(ProfileFake.get_bytes) == shape(HttpTransport.get_bytes)


def test_the_routes_price_run_and_view(store, listed, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_transport", lambda: ProfileFake())
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    monkeypatch.setattr(fcm, "default_catalog", lambda: NoCatalog())
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    c = TestClient(app)
    got = c.get(f"/api/open-data/work-lists/{listed}/profile").json()
    assert got["price"]["line"].startswith("3 datasets") and all(d["profile"] is None for d in got["datasets"])
    ran = c.post(f"/api/open-data/work-lists/{listed}/profile").json()
    assert ran["profiled"] == 2 and ran["not_profiled"] == 1
    assert c.get("/api/open-data/work-lists/nope/profile").status_code == 404


def test_the_catalog_declares_the_profile_and_it_is_open_data_only():
    import yaml
    from pathlib import Path

    cat = yaml.safe_load((Path(profile.__file__).parent.parent / "configdata" / "analysis_catalog.yaml").read_text())
    entry = next(e for e in cat["open_data_analyses"] if e["id"] == "external_data_profile")
    assert entry["resource_types"] == ["open_data"] and entry["answers_questions"] is False
