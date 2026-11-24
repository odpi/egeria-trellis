"""TR-54 E1 fixes: what the W4 fixtures showed the DCAT and CKAN adapters getting wrong, fixed with a fixture-based test each.

  (a) ArcGIS: a `license` that is neither a URL nor a known id is STATED TEXT: it goes to licence_text and licence_id is not reported.
  (b) OpenDataSoft's Project Open Data export: `rights` is read as licence_text.
  (c) DKAN's usa.gov licence URLs: judged only if RE's table maps them; otherwise not judged (never "usable").
  (d) DKAN: url from the identifier or an access URL if present, else not reported.
  (e) CKAN: a 404 or a non-JSON answer at /api/3/action at PROBE time means "not a CKAN portal" and registration moves on to DCAT;
      once a portal is known to be CKAN, a 404 stays a raised error with its words.

No schema change: licence_text is a normalised row field and is kept with the dataset through the verbatim record's `_licence_text`
(RE's own underscore-prefixed annotation, like `_licence_note`), so a stored dataset keeps it without a new column.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, reg, store  # noqa: E402,F401  (fixtures are shared)
from test_tr54_e1_portal_reach import FIX, PortalTransport, _rows  # noqa: E402

from resource_explorer.open_data import licence_text, service  # noqa: E402
from resource_explorer.open_data.absence import NOT_REPORTED  # noqa: E402
from resource_explorer.open_data.ckan import CkanAdapter, NotCkan  # noqa: E402
from resource_explorer.open_data.dcat_ap import DcatApAdapter  # noqa: E402
from resource_explorer.open_data.licences import classify_licence  # noqa: E402
from resource_explorer.open_data.transport import TransportError, status_words  # noqa: E402


# ── (a) a free-text licence is stated text, not an id ────────────────────────

def test_arcgis_free_text_licence_goes_to_licence_text_and_the_id_is_not_reported():
    _, rows = _rows("https://data-cotgis.opendata.arcgis.com", "pod_arcgis_hub_head.json")
    for row in rows:
        assert row["licence_id"] == NOT_REPORTED
        assert "as is" in row["licence_text"] and len(row["licence_text"]) > 200
        assert row["licence"] == {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}
        assert licence_text(row) == row["licence_text"]                          # kept with the stored record, read by the accessor


def test_a_licence_url_or_a_known_id_is_still_the_id_and_has_no_text():
    _, rows = _rows("https://opendata.minneapolismn.gov", "pod_arcgis_hub_uri_licence_head.json")
    assert rows[0]["licence_id"] == "cc0-1.0" and rows[0]["licence_text"] == NOT_REPORTED
    assert licence_text(rows[0]) is None


@pytest.mark.parametrize("raw,kind", [
    ("cc-by-4.0", "id"), ("CC0-1.0", "id"), ("https://creativecommons.org/licenses/by/4.0/", "id"),
    ("https://www.usa.gov/publicdomain/label/1.0/", "id"),              # an unmapped URL is still an identifier
    ("OGL-Canada-2.0", "id"),                                            # a bare token with no spaces is an id RE may not know
    ("Licence Ouverte (Etalab)", "text"), ("<p>The City provides the data \"as is\".</p>", "text"),
    ("Creative Commons Attribution", "text"),
])
def test_classify_licence_separates_ids_from_stated_text(raw, kind):
    got = classify_licence(raw)
    assert (got.id != NOT_REPORTED) == (kind == "id") and (got.text != NOT_REPORTED) == (kind == "text")


def test_classify_licence_a_missing_licence_is_neither():
    for raw in (None, "", "   ", NOT_REPORTED):
        got = classify_licence(raw)
        assert got.id == NOT_REPORTED and got.text == NOT_REPORTED


# ── (b) OpenDataSoft: rights is the licence text ─────────────────────────────

def test_opendatasoft_rights_is_read_as_licence_text_not_as_an_id():
    export = "https://data.opendatasoft.com/api/explore/v2.1/catalog/exports/data.json"
    _, rows = _rows(export, "ods_pod_export_limit3.json", feed=export)
    first = rows[0]
    assert first["licence_text"] == "Licence Ouverte (Etalab)" and first["licence_id"] == NOT_REPORTED
    assert licence_text(first) == "Licence Ouverte (Etalab)"
    assert "rights" in first["record"]["_licence_note"]                      # it says where the text came from
    assert first["licence"]["commercial"] == NOT_REPORTED                    # and it is not judged


def test_a_license_wins_over_rights():
    from resource_explorer.open_data.dcat_ap import DcatApAdapter as A
    a = A("https://x.example/data.json", PortalTransport({}))
    row = a.normalise({"title": "t", "identifier": "i", "license": "https://creativecommons.org/publicdomain/zero/1.0/",
                       "rights": "some statement"})
    assert row["licence_id"] == "cc0-1.0" and row["licence_text"] == NOT_REPORTED


# ── (c) DKAN usa.gov licence URLs are judged only if RE's table maps them ────

def test_dkan_usa_gov_licence_urls_are_kept_as_the_id_and_not_judged():
    _, rows = _rows("https://data.healthcare.gov", "pod_dkan2_head.json")
    first = rows[0]
    assert first["licence_id"] == "https://www.usa.gov/publicdomain/label/1.0/" and first["licence_text"] == NOT_REPORTED
    assert first["licence"] == {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}
    assert True not in first["licence"].values()                              # never "usable" by a guess


# ── (d) DKAN: a url from the identifier or an access URL, else not reported ──

def _norm(entry):
    return DcatApAdapter("https://x.example/data.json", PortalTransport({})).normalise(entry)


def test_url_is_the_landing_page_when_there_is_one():
    assert _norm({"identifier": "https://i.example/1", "landingPage": "https://l.example/p"})["url"] == "https://l.example/p"


def test_url_falls_back_to_an_identifier_that_is_a_url():
    assert _norm({"identifier": "https://data.example/api/views/abcd"})["url"] == "https://data.example/api/views/abcd"


def test_url_falls_back_to_a_distributions_access_url_never_a_download_url():
    entry = {"identifier": "e4rr-zk4i", "distribution": [{"downloadURL": "https://x.example/f.csv"},
                                                          {"accessURL": "https://x.example/dataset/e4rr"}]}
    assert _norm(entry)["url"] == "https://x.example/dataset/e4rr"
    assert _norm({"identifier": "e4rr-zk4i", "distribution": [{"downloadURL": "https://x.example/f.csv"}]})["url"] == NOT_REPORTED


def test_dkan2_fixture_without_any_of_them_is_still_not_reported():
    _, rows = _rows("https://data.healthcare.gov", "pod_dkan2_head.json")
    assert all(r["url"] == NOT_REPORTED for r in rows)


# ── (e) the CKAN probe ───────────────────────────────────────────────────────

def test_a_404_at_api_3_action_at_probe_time_is_not_a_ckan_portal():
    t = PortalTransport({})
    with pytest.raises(NotCkan) as info:
        CkanAdapter.probe("https://data.healthcare.gov", t)
    assert "not a CKAN portal (no /api/3/action here)" in str(info.value) and "HTTP 404" in str(info.value)
    assert isinstance(info.value, TransportError)
    assert t.calls == ["https://data.healthcare.gov/api/3/action/package_search"]


def test_a_non_json_answer_at_probe_time_is_not_a_ckan_portal():
    base = "https://x.example"
    t = PortalTransport({f"{base}/api/3/action/package_search": TransportError(f"{base}: the answer was not JSON (content-type text/html)")})
    with pytest.raises(NotCkan, match="not a CKAN portal"):
        CkanAdapter.probe(base, t)


def test_a_ckan_answer_probes_as_ckan():
    t = FakeTransport()
    assert CkanAdapter.probe("https://demo.ckan.org", t)["shape"] == "ckan"


def test_a_server_error_at_probe_time_is_not_read_as_not_ckan():
    base = "https://x.example"
    t = PortalTransport({f"{base}/api/3/action/package_search": TransportError(status_words(f"{base}/api/3/action/package_search", 503))})
    with pytest.raises(TransportError) as info:
        CkanAdapter.probe(base, t)
    assert not isinstance(info.value, NotCkan) and "HTTP 503" in str(info.value)


def test_registering_a_dkan2_portal_as_ckan_moves_on_to_dcat_and_says_so(store):
    base = "https://data.healthcare.gov"
    t = PortalTransport({f"{base}/data.json": "pod_dkan2_head.json"})
    portal = service.register_portal(store, display_name="HealthCare.gov", api_kind="CKAN", base_url=base,
                                     registered_by="tester", transport=t)
    assert portal["adapter"] == "dcat_ap" and portal["api_kind"] == "DCAT-AP"
    assert portal["base_url"] == f"{base}/data.json"
    assert t.calls[0] == f"{base}/api/3/action/package_search"                # CKAN first, then DCAT


def test_registering_a_real_ckan_portal_stays_ckan(store):
    t = FakeTransport()
    portal = service.register_portal(store, display_name="demo", api_kind="CKAN", base_url="https://demo.ckan.org",
                                     registered_by="tester", transport=t)
    assert portal["adapter"] == "ckan" and portal["api_kind"] == "CKAN"


def test_a_portal_that_is_neither_says_both_in_words(store):
    base = "https://nothing.example"
    with pytest.raises(service.OpenDataError) as info:
        service.register_portal(store, display_name="none", api_kind="CKAN", base_url=base, registered_by="t",
                                transport=PortalTransport({}))
    text = str(info.value)
    assert "not a CKAN portal (no /api/3/action here)" in text and "DCAT catalog" in text


def test_once_known_to_be_ckan_a_404_stays_a_raised_error_with_its_words():
    t = PortalTransport({})
    with pytest.raises(TransportError) as info:
        CkanAdapter("https://data.healthcare.gov", t).search("", None, 1)
    assert "HTTP 404" in str(info.value) and not isinstance(info.value, NotCkan)


def test_registering_a_ckan_portal_with_no_transport_makes_no_probe_call(store):
    portal = service.register_portal(store, display_name="demo", api_kind="CKAN", base_url="https://demo.ckan.org",
                                     registered_by="tester")
    assert portal["adapter"] == "ckan"


# ── the fixture nit ──────────────────────────────────────────────────────────

def test_the_dkan_404_fixture_is_trimmed_to_the_status_and_a_minimal_body():
    body = (FIX / "dkan2_ckan_package_search_404.html").read_text(encoding="utf-8")
    assert len(body) < 400 and "<html" in body.lower() and "@" not in body      # no portal e-mail address in it


# ── the one accessor, and what a licence stated in words is called ───────────

def test_the_accessor_returns_the_stated_text_or_none_for_not_reported():
    assert licence_text({"record": {"_licence_text": "Licence Ouverte (Etalab)"}}) == "Licence Ouverte (Etalab)"
    for d in ({"record": {}}, {"record": {"_licence_text": ""}}, {"record": {"_licence_text": NOT_REPORTED}}, {"record": None}, {}):
        assert licence_text(d) is None


def test_no_production_code_reads_the_record_key_but_the_accessor():
    import re
    root = Path(__file__).resolve().parents[1] / "resource_explorer"
    readers = []
    for path in root.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "_licence_text" in line and not path.name == "licences.py" and "LICENCE_TEXT_KEY" not in line and not line.strip().startswith("#"):
                readers.append(f"{path.relative_to(root)}:{n}")
    assert readers == [], f"only licences.py touches the key (the adapter writes it through LICENCE_TEXT_KEY): {readers}"


def test_a_licence_stated_in_words_is_never_called_no_licence():
    from resource_explorer.open_data.licences import licence_statement

    ds = {"licence_id": NOT_REPORTED, "record": {"_licence_text": "Licence Ouverte (Etalab)"}}
    st = licence_statement(ds)
    assert st["state"] == "stated_in_words" and st["words"] == "licence stated in words, not identified"
    assert st["text"] == "Licence Ouverte (Etalab)" and "no licence" not in st["words"].lower()


def test_licence_statement_identified_and_not_stated():
    from resource_explorer.open_data.licences import licence_statement

    assert licence_statement({"licence_id": "cc-by-4.0", "record": {}})["state"] == "identified"
    nothing = licence_statement({"licence_id": NOT_REPORTED, "record": {}})
    assert nothing["state"] == "not_stated" and nothing["words"] == "the portal states no licence"


def test_the_licence_question_row_says_stated_in_words_not_no_licence():
    from resource_explorer.open_data import dataset_questions

    ds = {"licence_id": NOT_REPORTED, "licence": {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED,
                                                    "commercial": NOT_REPORTED},
          "record": {"_licence_text": "Licence Ouverte (Etalab)"}, "portals": ["p"], "retrieved_at": "2026-11-23T00:00:00"}
    words = dataset_questions._licence_partial(ds)
    assert words and "licence stated in words, not identified" in words


@pytest.mark.usefixtures("signed_in_caller")
def test_the_dataset_api_carries_the_licence_statement(reg, store, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app
    from resource_explorer.web import routes

    portal = service.register_portal(store, display_name="demo", api_kind="CKAN", base_url="https://demo.ckan.org", registered_by="t")
    row = {"identifier": "d1", "identifier_kind": "portal_id", "title": "T", "publisher": NOT_REPORTED, "licence_id": NOT_REPORTED,
           "licence": {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED},
           "modified": NOT_REPORTED, "formats": NOT_REPORTED, "size": NOT_REPORTED,
           "record": {"_licence_text": "Licence Ouverte (Etalab)"}}
    store.add_dataset(slug="d1", row=row, portals=[portal["slug"]], retrieved_at="2026-11-23T00:00:00", imported_by="t",
                      work_list_slug="w", datacite=None)
    monkeypatch.setattr(routes.open_data, "_store", lambda: store)
    body = TestClient(app).get("/api/open-data/datasets/d1").json()
    assert body["licence_statement"]["state"] == "stated_in_words"
    assert body["licence_statement"]["words"] == "licence stated in words, not identified"
