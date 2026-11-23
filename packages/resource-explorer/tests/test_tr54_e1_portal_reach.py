"""TR-54 E, step 1: do the existing DCAT and CKAN adapters already read ArcGIS Hub, Socrata, OpenDataSoft and DKAN?

Answered with recorded fixtures (tests/fixtures/open_data/README, section "Recorded 2026-11-23"), per portal family:
reads / partial (what is missing) / fails. These tests CHARACTERISE what the adapters do today; they are not wishes.
When a native adapter lands for a family that fell short, its row changes here on purpose.

    ArcGIS Hub (DCAT-US data.json)   READS (a site with a licence URL: licence read, flags judged) or PARTIAL (a site whose
                                     `license` is a free-text HTML disclaimer: it is kept as the licence_id, flags not_reported)
    Socrata (/data.json)             PARTIAL: reads title, publisher, modified, formats; NO licence in the feed at all
    OpenDataSoft                     FAILS at the root (/data.json is 77.9 MB, over RE's 64 MiB read limit; its DCAT export is
                                     RDF/XML); READS the portal's own Project Open Data export when given its URL, PARTIAL:
                                     the licence is in `rights` (free text), which the adapter does not read
    DKAN 2 (CKAN API)                the CKAN adapter FAILS (no /api/3/action: HTTP 404); the DCAT adapter READS its data.json,
                                     PARTIAL: no landingPage so no url, licence on about 30 % of entries, a URL RE's table lacks
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from resource_explorer.open_data import transport as tp
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.ckan import CkanAdapter
from resource_explorer.open_data.dcat_ap import DcatApAdapter, ProbeRefused
from resource_explorer.open_data.transport import TransportError, status_words

FIX = Path(__file__).parent / "fixtures" / "open_data"

#: The size the root /data.json of data.opendatasoft.com answered with on 2026-11-23 (no Content-Length: it streams).
ODS_ROOT_DATA_JSON_BYTES = 77_861_352


class PortalTransport:
    """Replays recorded answers by URL. A URL it has no answer for is HTTP 404, in the real transport's words."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        answer = self.routes.get(url)
        if answer is None:
            raise TransportError(status_words(url, 404))
        if isinstance(answer, Exception):
            raise answer
        return json.loads((FIX / answer).read_text(encoding="utf-8"))

    def get_text(self, url, params=None, headers=None):
        raise AssertionError("not used")

    def get_bytes(self, url, max_bytes, headers=None):
        raise AssertionError("not used")


def _rows(base: str, fixture: str, feed: str | None = None) -> tuple[dict, list[dict]]:
    feed = feed or f"{base}/data.json"
    transport = PortalTransport({feed: fixture})
    probe = DcatApAdapter.probe(base, transport)
    page = DcatApAdapter(probe["feed_url"], transport).search("", None, 1)
    return probe, page["rows"]


def _missing(row: dict, fields=("title", "publisher", "licence_id", "modified", "formats", "size", "url")) -> set[str]:
    return {f for f in fields if row[f] == NOT_REPORTED}


# ── ArcGIS Hub ───────────────────────────────────────────────────────────────

def test_arcgis_hub_with_a_licence_url_reads_in_full_but_for_size():
    probe, rows = _rows("https://opendata.minneapolismn.gov", "pod_arcgis_hub_uri_licence_head.json")
    assert probe["shape"] == "pod" and len(rows) == 5
    first = rows[0]
    assert first["licence_id"] == "cc0-1.0" and first["licence"] == {"attribution": False, "share_alike": False, "commercial": True}
    assert _missing(first) == {"size"}                         # POD states no byteSize: reported as not reported, not 0
    assert first["url"].startswith("https://") and first["formats"] != NOT_REPORTED


def test_arcgis_hub_with_a_free_text_licence_is_partial_the_disclaimer_becomes_the_licence_id():
    """153 of 153 licences on data-cotgis.opendata.arcgis.com are free-text HTML disclaimers (measured on the full feed)."""
    _, rows = _rows("https://data-cotgis.opendata.arcgis.com", "pod_arcgis_hub_head.json")
    row = rows[0]
    assert len(str(row["licence_id"])) > 200 and "as is" in str(row["licence_id"])       # TODAY: text taken as an identifier
    assert row["licence"] == {"attribution": "not_reported", "share_alike": "not_reported", "commercial": "not_reported"}
    assert _missing(row) == {"size"}                           # everything else is read


# ── Socrata ──────────────────────────────────────────────────────────────────

def test_socrata_reads_what_its_feed_states_and_has_no_licence_to_read():
    probe, rows = _rows("https://data.cityofchicago.org", "pod_socrata_head.json")
    assert probe["shape"] == "pod" and len(rows) == 5
    for row in rows:
        assert row["licence_id"] == NOT_REPORTED and row["licence"]["commercial"] == "not_reported"
        assert row["title"] != NOT_REPORTED and row["modified"] != NOT_REPORTED and row["formats"] != NOT_REPORTED
        assert row["size"] == NOT_REPORTED
    assert not any("license" in e for e in json.loads((FIX / "pod_socrata_head.json").read_text())["dataset"])


# ── OpenDataSoft ─────────────────────────────────────────────────────────────

def test_opendatasoft_root_data_json_is_over_the_read_limit_so_the_probe_fails_in_words():
    assert ODS_ROOT_DATA_JSON_BYTES > tp.MAX_JSON_BYTES == 64 * 1024 * 1024
    base = "https://data.opendatasoft.com"
    too_big = TransportError(f"{base}/data.json: the answer is over the {tp.MAX_JSON_BYTES:,} byte read limit; not read")
    html = TransportError(f"{base}: the answer was not JSON (content-type text/html)")
    with pytest.raises(ProbeRefused) as info:
        DcatApAdapter.probe(base, PortalTransport({f"{base}/data.json": too_big, base: html}))
    assert "Nothing at" in str(info.value)


def test_opendatasoft_dcat_export_is_rdf_xml_and_the_probe_says_there_is_no_adapter_for_it():
    export = "https://data.opendatasoft.com/api/explore/v2.1/catalog/exports/dcat"
    rdf = TransportError(f"{export}: the answer was not JSON (content-type application/rdf+xml)")
    with pytest.raises(ProbeRefused) as info:
        DcatApAdapter.probe(export, PortalTransport({export: rdf}))
    assert "application/rdf+xml, which has no adapter yet" in str(info.value)
    head = (FIX / "ods_dcat_export_limit2.rdf").read_text(encoding="utf-8")
    assert head.startswith("<?xml") and "dcat:Dataset" in head                     # it IS DCAT: the adapter reads only JSON
    refused = json.loads((FIX / "ods_jsonld_export_refused.json").read_text())
    assert refused["error_code"] == "InvalidExportFormat" and "jsonld" not in refused["message"]


def test_opendatasoft_own_project_open_data_export_reads_but_the_licence_in_rights_is_not_read():
    export = "https://data.opendatasoft.com/api/explore/v2.1/catalog/exports/data.json"
    probe, rows = _rows(export, "ods_pod_export_limit3.json", feed=export)
    assert probe["shape"] == "pod" and len(rows) == 3
    for row in rows:
        assert row["title"] != NOT_REPORTED and row["modified"] != NOT_REPORTED and row["formats"] != NOT_REPORTED
        assert row["licence_id"] == NOT_REPORTED                                   # TODAY: `rights` is ignored
    stated = [e.get("rights") for e in json.loads((FIX / "ods_pod_export_limit3.json").read_text())["dataset"]]
    assert "Licence Ouverte (Etalab)" in stated                                    # ... though the portal states one


def test_opendatasoft_root_head_cut_is_a_pod_catalog_of_three_entries():
    doc = json.loads((FIX / "pod_opendatasoft_head.json").read_text(encoding="utf-8"))
    assert doc["@id"] == "https://data.opendatasoft.com/data.json" and len(doc["dataset"]) == 3


# ── DKAN 2 ───────────────────────────────────────────────────────────────────

def test_the_ckan_adapter_fails_on_dkan2_with_no_ckan_api_and_never_reads_that_as_nothing_found():
    base = "https://data.healthcare.gov"
    transport = PortalTransport({})
    with pytest.raises(TransportError) as info:
        CkanAdapter(base, transport).search("", None, 1)
    assert "HTTP 404" in str(info.value) and transport.calls == [f"{base}/api/3/action/package_search"]
    body = (FIX / "dkan2_ckan_package_search_404.html").read_text(encoding="utf-8", errors="replace")
    assert "<html" in body.lower()                                                  # the recorded 404 is a web page, not CKAN JSON


def test_the_dcat_adapter_reads_dkan2_data_json_partially_no_landing_page_and_a_licence_url_outside_the_table():
    probe, rows = _rows("https://data.healthcare.gov", "pod_dkan2_head.json")
    assert probe["shape"] == "pod" and len(rows) == 5
    for row in rows:
        assert row["title"] != NOT_REPORTED and row["modified"] != NOT_REPORTED and row["formats"] == ["CSV"]
        assert row["url"] == NOT_REPORTED and row["size"] == NOT_REPORTED           # no landingPage, no byteSize
    first = rows[0]
    assert first["licence_id"] == "https://www.usa.gov/publicdomain/label/1.0/"
    assert first["licence"]["commercial"] == "not_reported"                         # a URL RE's licence table does not know
    assert rows[3]["licence_id"] == NOT_REPORTED                                    # and some entries state none
    cadence = [e.get("accrualPeriodicity") for e in json.loads((FIX / "pod_dkan2_head.json").read_text())["dataset"]]
    assert any(cadence)                                                             # DKAN states a cadence (TR-54 B3 can use it)


# ── the fixtures are what the README says ────────────────────────────────────

POD_HEADS = {"pod_arcgis_hub_head.json": 5, "pod_arcgis_hub_uri_licence_head.json": 5, "pod_socrata_head.json": 5,
             "pod_dkan2_head.json": 5, "pod_opendatasoft_head.json": 3, "ods_pod_export_limit3.json": 3}


@pytest.mark.parametrize("name,count", sorted(POD_HEADS.items()))
def test_each_pod_head_is_a_catalog_envelope_with_the_stated_number_of_verbatim_entries(name, count):
    doc = json.loads((FIX / name).read_text(encoding="utf-8"))
    assert doc["@type"] == "dcat:Catalog" or "conformsTo" in doc
    assert len(doc["dataset"]) == count


def test_the_readme_names_every_new_fixture():
    readme = (FIX / "README").read_text(encoding="utf-8")
    section = readme[readme.index("Recorded 2026-11-23"):]
    for name in [*POD_HEADS, "ods_dcat_export_limit2.rdf", "ods_jsonld_export_refused.json",
                 "ods_catalog_datasets_limit2.json", "dkan2_ckan_package_search_404.html"]:
        assert name in section, f"{name} is not described in the README"
