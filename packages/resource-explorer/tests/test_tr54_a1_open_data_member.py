"""TR-54 item A, step A1: an open-data dataset joins its investigation as a MEMBER, not only as a work-list tag.

Before: `import_datasets` and `import_from_repository` passed `investigation=` to the work list only, so the dataset
never entered the investigation's scope (the Folio). After: the import also adds an `open_data` member to the
investigation's Folio; `POST /investigations/{slug}/members` refuses a kind that is not a member kind with a 422; and
the add-member picker offers "Open data".

No live network, no Egeria. The service tests use a sqlite registry like tests/test_open_data.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _chosen, _register_demo, reg, store  # noqa: E402,F401  (fixtures are shared)

from resource_explorer.open_data import service  # noqa: E402

NEXT = Path(__file__).parent.parent / "resource_explorer" / "web" / "static" / "next"


def _investigation(reg_) -> str:
    return reg_.create_investigation("Water study")["slug"]


def _members(reg_, slug: str) -> list[tuple[str, str]]:
    return [(m["entity_type"], m["entity_slug"]) for m in reg_.list_investigation_members(slug)]


def _search_two(store):
    portal = _register_demo(store)
    t = FakeTransport()
    res = service.run_search(store, portal["slug"], terms="dataset", pages=1, transport=t)
    names = [r["dataset_id"] for r in res["rows"]][:2]
    assert len(names) == 2
    return portal, t, names


def test_import_with_investigation_adds_each_dataset_as_an_open_data_member(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    got = service.import_datasets(store, _chosen(portal["slug"], *names), work_list_name="water", transport=t,
                                  egeria_lookup=lambda _i: None, imported_by="tester", investigation=inv)
    slugs = {d["slug"] for d in store.list_datasets()}
    assert got["imported"] == 2 and len(slugs) == 2
    assert _members(reg, inv) == sorted(("open_data", s) for s in slugs)
    assert got["investigation_members"] == 2                     # said in the result, so a caller can show it


def test_import_without_investigation_adds_no_investigation_member(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    got = service.import_datasets(store, _chosen(portal["slug"], *names), work_list_name="water", transport=t,
                                  egeria_lookup=lambda _i: None, imported_by="tester")
    assert got["imported"] == 2 and got["investigation_members"] == 0
    assert _members(reg, inv) == []


def test_an_investigation_that_does_not_exist_is_said_not_silently_dropped(store, reg):
    portal, t, names = _search_two(store)
    got = service.import_datasets(store, _chosen(portal["slug"], *names), work_list_name="water", transport=t,
                                  egeria_lookup=lambda _i: None, imported_by="tester", investigation="no_such_inv")
    assert got["imported"] == 2 and got["investigation_members"] == 0
    assert got["investigation_note"] == "no investigation 'no_such_inv': datasets kept on the work list only"


def test_importing_the_same_dataset_twice_keeps_one_member_and_its_rationale(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    service.import_datasets(store, _chosen(portal["slug"], names[0]), work_list_name="water", transport=t,
                            egeria_lookup=lambda _i: None, imported_by="tester", investigation=inv)
    ds = store.list_datasets()[0]["slug"]
    reg.add_working_set_member(reg.get_or_create_working_set(inv)["slug"], "open_data", ds,
                               membership_rationale="Dan's note", keep_existing=False)
    held = lambda _i: "e-guid"                                   # noqa: E731  Egeria already holds it: not re-imported
    service.import_datasets(store, _chosen(portal["slug"], names[0]), work_list_name="water", transport=t,
                            egeria_lookup=held, imported_by="tester", investigation=inv)
    rows = reg.list_investigation_members(inv)
    assert [(m["entity_type"], m["entity_slug"]) for m in rows] == [("open_data", ds)]
    assert rows[0]["membership_rationale"] == "Dan's note"


def test_the_member_rationale_names_where_the_dataset_came_from(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    service.import_datasets(store, _chosen(portal["slug"], names[0]), work_list_name="water", transport=t,
                            egeria_lookup=lambda _i: None, imported_by="tester", investigation=inv)
    [m] = reg.list_investigation_members(inv)
    assert m["membership_rationale"].startswith("imported from ") and m["state"] == "in-scope"


# ── the member-add route ────────────────────────────────────────────────────

@pytest.fixture
def client(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app
    from resource_explorer.web.routes import investigations as inv_routes

    monkeypatch.setattr(inv_routes, "_registry", lambda: reg)
    return TestClient(app)


def test_the_members_route_accepts_open_data(client, reg):
    inv = _investigation(reg)
    r = client.post(f"/api/investigations/{inv}/members", json={"entity_type": "open_data", "entity_slug": "ds1"})
    assert r.status_code == 200, r.text
    assert [(m["entity_type"], m["entity_slug"]) for m in r.json()] == [("open_data", "ds1")]


def test_the_members_route_refuses_an_unknown_kind_with_422_naming_the_kinds(client, reg):
    inv = _investigation(reg)
    r = client.post(f"/api/investigations/{inv}/members", json={"entity_type": "spreadsheet", "entity_slug": "x"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "spreadsheet" in detail
    for kind in ("repo", "database", "filesystem", "open_data"):
        assert kind in detail
    assert reg.list_investigation_members(inv) == []             # nothing was written


@pytest.mark.parametrize("kind", ["repo", "database", "filesystem"])
def test_the_existing_kinds_are_still_accepted(client, reg, kind):
    inv = _investigation(reg)
    r = client.post(f"/api/investigations/{inv}/members", json={"entity_type": kind, "entity_slug": "x"})
    assert r.status_code == 200, r.text


# ── the picker ──────────────────────────────────────────────────────────────

def test_the_add_member_picker_lists_open_data():
    src = (NEXT / "stages" / "investigation.js").read_text()
    assert "['open_data', 'Open data']" in src
    for t in ("repo", "database", "filesystem"):
        assert f"['{t}'," in src


def test_the_picker_datalist_offers_the_open_data_list_for_open_data():
    src = (NEXT / "stages" / "investigation.js").read_text()
    body = src[src.index("function slugListRows"):]
    body = body[:body.index("\n}\n")]
    assert "'open_data'" in body and "state.openData" in body


# ── A2 / N1: an already_in_catalog row is added when RE has a slug for it, and says why when it has none ─────────

OD_JS = (NEXT / "admin" / "open_data.js").read_text(encoding="utf-8")


def _import(store, portal, t, names, **kw):
    kw.setdefault("egeria_lookup", lambda _i: None)
    return service.import_datasets(store, _chosen(portal["slug"], *names), work_list_name="water", transport=t,
                                   imported_by="tester", **kw)


def test_n1_a_dataset_RE_already_holds_joins_the_investigation_and_the_row_says_so(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    _import(store, portal, t, names[:1])                                   # held by RE, not yet in any investigation
    held = store.list_datasets()[0]["slug"]
    assert _members(reg, inv) == []
    got = _import(store, portal, t, names[:1], investigation=inv)
    [row] = got["rows"]
    assert row["outcome"] == "already_in_catalog" and row["slug"] == held
    assert row["investigation"] == f"added to {inv}"
    assert _members(reg, inv) == [("open_data", held)] and got["investigation_members"] == 1
    assert got["investigation"] == inv


def test_n1_a_dataset_only_Egeria_holds_says_why_it_was_not_added(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    got = _import(store, portal, t, names[:1], investigation=inv, egeria_lookup=lambda _i: "e-guid")
    [row] = got["rows"]
    assert row["outcome"] == "already_in_catalog" and "slug" not in row
    assert row["investigation"] == f"not added to {inv}: Egeria holds it and RE has no record of it to add"
    assert _members(reg, inv) == [] and got["investigation_members"] == 0
    assert "investigation_note" not in got                                 # the investigation exists: no false note


def test_n1_a_mixed_import_counts_each_row_once(store, reg):
    inv = _investigation(reg)
    portal, t, names = _search_two(store)
    _import(store, portal, t, names[:1])
    got = _import(store, portal, t, names, investigation=inv)             # one held by RE, one new
    assert sorted(r["outcome"] for r in got["rows"]) == ["already_in_catalog", "imported"]
    assert {r["investigation"] for r in got["rows"]} == {f"added to {inv}"}
    assert got["investigation_members"] == 2 == len(_members(reg, inv))


def test_n1_the_import_result_says_how_many_were_added_and_each_rows_reason():
    body = OD_JS[OD_JS.index("export function importResultHtml(r) {"):]
    body = body[:body.index("\n}\n")]
    assert "r.investigation_members" in body and "added to <span" in body    # "N added to <investigation>"
    assert "r.investigation_note" in body                                    # the named investigation does not exist
    assert "o.investigation" in body                                         # each row's own reason, not silence


# ── A2: a fresh install's "Find open data" offers the suggested portals, one press each, never applied ───────────

OD_KIND_JS = (NEXT / "open-data-kind.js").read_text(encoding="utf-8")


def _fn(src: str, head: str) -> str:
    body = src[src.index(head):]
    return body[:body.index("\n}\n")]


def test_a2_the_empty_find_dialog_lists_the_suggested_portals_open_with_an_add_each():
    body = _fn(OD_KIND_JS, "export async function openFindOpenDataDialog")
    empty = body[body.index("if (!portals.length)"):body.index("body.innerHTML = `<label")]
    assert "listPortalSeeds" in empty and "suggestedPortalsHtml(" in empty
    assert "{ open: true }" in empty                                   # a fresh install sees them, not a closed fold
    assert "wirePortalSeedAdd(" in empty                                # one press each, through the shared wiring


def test_a2_adding_a_suggested_portal_reopens_the_find_dialog_so_it_can_be_searched():
    body = _fn(OD_KIND_JS, "export async function openFindOpenDataDialog")
    empty = body[body.index("if (!portals.length)"):body.index("body.innerHTML = `<label")]
    assert "wirePortalSeedAdd(slot, showFind)" in empty                   # after an Add the dialog is the search


def test_a2_nothing_is_added_without_a_press():
    # the only caller of addPortalSeed is the click handler: a render, a load or an empty list never registers a portal
    assert OD_KIND_JS.count("addPortalSeed") == 0
    wire = _fn(OD_JS, "export function wirePortalSeedAdd")
    assert wire.count("addPortalSeed(") == 1 and "addEventListener('click'" in wire
    assert "${SEEDS_ALL_REGISTERED}" in OD_JS                           # an exhausted list says so


def test_a2_the_suggested_table_can_start_open_and_still_starts_closed_in_admin():
    fn = _fn(OD_JS, "export function suggestedPortalsHtml")
    assert "{ open = false } = {}" in fn and "'<details data-od-seeds-fold>'.replace('>', ' open>')" in fn
    assert "suggestedPortalsHtml(res)" in OD_JS                          # Admin's own call is unchanged: folded


def test_a2_the_no_portals_sentence_no_longer_sends_the_person_away_before_offering_the_list():
    assert "Suggested portals are listed below" in OD_KIND_JS
