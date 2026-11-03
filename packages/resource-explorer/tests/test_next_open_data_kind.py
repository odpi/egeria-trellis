"""Open data as the fourth sidebar kind (OD2 brief Part B3): source-text contracts, no browser (same pattern as
test_next_sidebar_list_db_fs.py)."""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP = (NEXT / "app.js").read_text(encoding="utf-8")
KIND = (NEXT / "open-data-kind.js").read_text(encoding="utf-8")


def test_the_kind_is_a_chip_a_url_value_and_a_lazy_list():
    assert "{ id: 'open_data', label: 'Open data' }" in APP
    assert "const RESOURCE_KINDS = ['repo', 'db', 'filesystem', 'open_data'];" in APP
    assert "type === 'open_data' && !state.openDataLoaded" in APP
    assert "state.resourceType === 'open_data' ? state.openData" in APP


def test_find_runs_the_portal_search():
    assert "open_data: '＋ Find open data'" in APP
    assert "if (state.resourceType === 'open_data') { openFindOpenDataDialog(" in APP
    assert "openPortalPane(body, pick.value" in KIND                       # the same pane Admin used
    assert "listOpenDataPortals" in KIND and "adapter_available" in KIND   # only portals that can be searched


def test_the_list_opens_a_dataset_through_the_sidebars_own_selection_handler():
    assert 'data-slug="${esc(d.slug)}"' in KIND
    branch = APP[APP.index("if (state.resourceType === 'open_data') {\n    // Datasets have no groups"):]
    assert branch.index("investigationBarHtml()") < branch.index("workListsSidebarHtml()") < branch.index("openDataRowsHtml(")
    assert "await renderOpenDataPane(el, state.selectedSlug, state.openData);" in APP


def test_empty_states_are_sentences():
    assert "Nothing imported yet. Use ＋ Find open data to search a data portal." in KIND
    assert "Registering a portal is done in Admin › Open data." in KIND


def test_an_import_refreshes_the_list_and_the_work_lists():
    assert "re:open-data-imported" in APP and "re:open-data-imported" in (NEXT / "admin" / "open_data.js").read_text(encoding="utf-8")


def test_the_shortlist_panel_prices_before_the_press_and_keeps_dropped_rows_listed():
    sl = (NEXT / "open-data-shortlist.js").read_text(encoding="utf-8")
    assert "openDataScoutPrice(workList)" in sl and sl.index("openDataScoutPrice(workList)") < sl.index("scoutOpenData(workList)")
    assert "res.rows.map(" in sl and "dropped" in sl                       # dropped rows are rows with a verdict, not removed
    assert "not stated" in sl and "could not be checked" in sl
    assert "mountShortlist(el.querySelector('[data-od-shortlist]')" in KIND


def test_the_data_request_band_is_on_the_investigation_and_says_when_it_cannot_store():
    band = (NEXT / "data-request.js").read_text(encoding="utf-8")
    inv = (NEXT / "stages" / "investigation.js").read_text(encoding="utf-8")
    assert "mountDataRequestBand(document.getElementById('inv-data-request'), inv.slug)" in inv
    assert "import { mountDataRequestBand } from '/static/next/data-request.js';" in inv
    for dim in ("Subjects", "Period", "Places", "Grain", "License needs", "Formats"):
        assert f"cell('{dim}'" in band
    assert "not stated" in band and "No data request stated for this investigation." in band
    assert "The data request cannot be stored yet" in band and "${got.stored ? '' : 'disabled'}" in band
    assert "openFindOpenDataDialog({ investigation: slug })" in band        # a search from the band carries the request
    sl = (NEXT / "open-data-shortlist.js").read_text(encoding="utf-8")
    assert "appState.investigation" in sl


def test_the_profile_section_prices_first_and_says_what_was_not_profiled():
    pf = (NEXT / "open-data-profile.js").read_text(encoding="utf-8")
    assert pf.index("openDataProfileView(workList)") < pf.index("profileOpenData(workList)")
    assert "v.price.line" in pf and "data-od-dist=\"not-profiled\"" in pf
    assert "never run" in (NEXT / "open-data-profile.js").read_text(encoding="utf-8") or "ds.words" in pf   # the server's words
    assert "stated by" in pf and "mountProfile(" in (NEXT / "open-data-shortlist.js").read_text(encoding="utf-8")


def test_publish_and_look_again_price_first_and_say_each_step():
    pb = (NEXT / "open-data-publish.js").read_text(encoding="utf-8")
    assert pb.index("openDataRefreshPrice(workList)") < pb.index("refreshOpenData(workList)")
    assert "st.price.line" in pb and "data-od-publish-step" in pb and "read back by GUID" in pb
    assert "mountPublish(" in KIND and "mountRefresh(" in (NEXT / "open-data-shortlist.js").read_text(encoding="utf-8")
