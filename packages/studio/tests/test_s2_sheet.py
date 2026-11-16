"""S2: the sheet form's server side: Report lookup for placements (by qualified name; the display-name path
carries pyegeria's step-6 words), sheet drafts from laz's real sheet, and whole-sheet preview as the user."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trellis_studio import authoring
from test_s1_drafts import clients  # noqa: F401  (fixture)

SHEET = json.loads((Path(__file__).parent / "fixtures" / "sheet_laz_catalog_health.json").read_text())["sheet"]
REPORTS = json.loads((Path(__file__).parent / "fixtures" / "drafts_real_shape.json").read_text())


def _laz_sheet_draft():
    from pyegeria.authoring.lifecycle import sheet_draft_from_sheet
    from pyegeria.view.dashboards import EgeriaDashboardSheet
    return sheet_draft_from_sheet(EgeriaDashboardSheet.model_validate(SHEET))


def test_laz_sheet_becomes_a_draft_that_round_trips():
    d = _laz_sheet_draft()
    assert d.kind == "sheet" and len(d.placements) == 7
    again = authoring.parse_draft(authoring.as_draft_dict(d))
    assert [p.name for p in again.placements] == [p.name for p in d.placements]


def _fake_load_held(monkeypatch, element=None, refuse=None):
    import pyegeria.authoring as a
    from pyegeria.authoring.lifecycle import report_draft_from_element

    async def fake(kind, label, client):
        assert kind == "report" and client.user_id == "peterprofile"
        if refuse:
            raise a.AuthoringRefused(refuse)
        return report_draft_from_element(element)
    monkeypatch.setattr(a, "async_load_held", fake)


def test_lookup_by_qualified_name(authed, clients, monkeypatch):
    el = REPORTS["laz_report_element"]
    _fake_load_held(monkeypatch, el)
    qn = el["properties"]["qualifiedName"]
    body = authed.get("/api/reports/lookup", params={"ref": qn}).json()
    assert body["found"] and body["by"] == "qualified name" and body["qualified_name"] == qn
    assert body["spec"] == "Analytic Demo - AI Context Readiness"
    assert not any("display name" in n for n in body["notes"])


def test_lookup_by_display_name_says_so_and_hands_back_the_qn(authed, clients, monkeypatch):
    el = REPORTS["laz_report_element"]
    _fake_load_held(monkeypatch, el)
    body = authed.get("/api/reports/lookup", params={"ref": el["properties"]["displayName"]}).json()
    assert body["by"] == "display name" and body["qualified_name"] == el["properties"]["qualifiedName"]
    assert any("kept by qualified name" in n for n in body["notes"])


def test_lookup_unknown_is_pyegerias_words_not_none(authed, clients, monkeypatch):
    words = ("Couldn't confirm whether Egeria holds a Report named 'Catalog Health' (the display-name read ended "
             "unconfirmed, null_not_end, after 25 positions: Egeria didn't confirm there are no more). Give the "
             "Report's qualified name.")
    _fake_load_held(monkeypatch, refuse=words)
    r = authed.get("/api/reports/lookup", params={"ref": "Catalog Health"})
    assert r.status_code == 409 and r.json()["detail"] == words


def test_lookup_needs_a_ref(authed):
    assert authed.get("/api/reports/lookup", params={"ref": " "}).status_code == 400


def test_sheet_preview_runs_placements_only_when_asked(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    seen = []

    async def fake_preview(draft, *, client, max_rows, params=None, run=False):
        seen.append(run)
        return {"kind": "sheet", "label": draft.name, "viewer": client.user_id, "capped": False, "shown": None,
                "result": {"name": draft.name, "heading": draft.heading, "placements": []},
                "notes": [] if run else ["placements resolved, not run (run=True runs them)"]}
    monkeypatch.setattr(a, "async_preview", fake_preview)
    body = authoring.as_draft_dict(_laz_sheet_draft())
    r1 = authed.post("/api/preview", json={"draft": body}).json()
    r2 = authed.post("/api/preview", json={"draft": body, "run_placements": True}).json()
    assert seen == [False, True]
    assert r1["kind"] == "sheet" and "not run" in r1["notes"][0] and r2["notes"] == []


def test_placement_by_qualified_name_is_a_valid_sheet_draft(authed, clients, monkeypatch):
    holder = {}

    class Store:
        def __init__(self, c):
            pass

        async def async_save(self, uid, d):
            holder["d"] = d
            return f"Dashboard::{d.name}"
    monkeypatch.setattr(authoring, "_store", Store)
    qn = REPORTS["laz_report_element"]["properties"]["qualifiedName"]
    draft = {"kind": "sheet", "name": "My health", "heading": "My health", "placements": [
        {"text": "About", "name": "About", "span": "full", "emphasis": "panel"},
        {"report": qn, "name": "AI readiness", "span": "2"}]}
    r = authed.put("/api/drafts", json={"draft": draft})
    assert r.status_code == 200 and r.json()["qualified_name"] == "Dashboard::My health"
    assert holder["d"].placements[1].report == qn


def test_a_bad_placement_is_422(authed, clients):
    bad = {"kind": "sheet", "name": "x", "heading": "x", "placements": [{"name": "neither report nor text"}]}
    assert authed.put("/api/drafts", json={"draft": bad}).status_code == 422
