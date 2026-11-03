"""Activity's own /next surface (PLAN-FINISH-REPOS.md item 6): pinning that
the header link opens a real in-/next panel reading the same `GET
/api/activity/` endpoint the classic UI's tab reads, rather than the
outbound "opens the current UI" link it replaces, plus the honesty rules
(distinct empty/error/no-match states, entries fetched fresh on every open,
no reference to Activity as a STAGES entry).

No browser verification of a signed-in session happened for this file — see
ITEM-6-ACTIVITY-IMPLEMENTED.md for what was and was not
checked live. These tests grep/slice function bodies out of the concatenated
source, the established pattern for /next JS modules without a browser --
see test_next_curate_pane.py and test_next_understanding_pane.py.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    """app.js plus every stages/*.js module, concatenated -- see the
    identical helper's docstring in test_next_component_review.py."""
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _activity_src():
    return (NEXT / "stages" / "activity.js").read_text(encoding="utf-8")


def _index_html():
    return (NEXT / "index.html").read_text(encoding="utf-8")


class TestActivityIsNotAStagesEntry:
    """Activity is a header-triggered overlay, decoupled from #intent-nav --
    the same pattern as Admin -- not a STAGES array entry with `built: true`.
    """

    def test_activity_is_absent_from_the_stages_array(self):
        app = _app()
        stages_block = app[app.index("const STAGES = ["):app.index("];", app.index("const STAGES = ["))]
        assert "id: 'activity'" not in stages_block

    def test_the_pane_module_documents_why_it_is_not_a_stage(self):
        src = _activity_src()
        assert "NOT one of the" in src and "canonical stage ids" in src
        assert "decoupled" in src


class TestTheHeaderLinkNowOpensInNext:
    """The literal `<a href="/">...not built in /next...</a>` this item
    replaces must actually be gone, not just supplemented."""

    def test_the_old_not_built_wording_is_gone(self):
        html = _index_html()
        assert "The activity log is not built in /next" not in html

    def test_the_header_control_is_a_button_not_an_outbound_link(self):
        html = _index_html()
        assert 'id="activity-open-btn"' in html
        # It must not still be an <a href="/"> -- that is the outbound
        # bounce this item exists to remove.
        btn_start = html.index('id="activity-open-btn"')
        tag_start = html.rindex("<", 0, btn_start)
        assert html[tag_start:tag_start + 10].lower().startswith("<button")

    def test_the_activity_count_span_still_exists_for_rendertopbar(self):
        # renderTopBar() in app.js writes textContent into #activity-count on
        # every render -- removing the header markup must not orphan that.
        html = _index_html()
        assert 'id="activity-count"' in html
        app = _app()
        assert "$('activity-count').textContent =" in app

    def test_app_js_wires_the_button_to_openactivitypanel(self):
        app = _app()
        assert "import { openActivityPanel } from '/static/next/stages/activity.js';" in app
        assert "function wireActivityButton()" in app
        body = app[app.index("function wireActivityButton()"):app.index("function wireActivityButton()") + 400]
        assert "$('activity-open-btn')" in body
        assert "openActivityPanel()" in body

    def test_the_wiring_is_idempotent_like_its_sibling_wire_functions(self):
        # Same guard shape as wireSidebarDrawer/wireTextSize -- attaching a
        # second listener on every renderTopBar() call would double-fire the
        # panel open, the exact bug wireSidebarDrawer's own comment warns
        # about for its own button.
        app = _app()
        body = app[app.index("let activityButtonWired = false;"):app.index("let activityButtonWired = false;") + 400]
        assert "if (activityButtonWired) return;" in body
        assert "activityButtonWired = true;" in body


class TestThePanelReadsTheRealActivityLog:
    def test_it_fetches_from_the_same_endpoint_the_classic_tab_uses(self):
        src = _activity_src()
        assert "import { listActivity } from '/static/re-api.js';" in src
        assert "await listActivity(FETCH_LIMIT)" in src

    def test_it_refetches_on_every_open_rather_than_caching(self):
        # A stale cached page would show a finished run as still "running" --
        # openActivityPanel must reset panel.entries and refetch every call.
        src = _activity_src()
        body = src[src.index("export async function openActivityPanel("):src.index("function renderControls()")]
        assert "panel.entries = null;" in body
        assert "panel.entries = await listActivity(FETCH_LIMIT);" in body

    def test_there_is_no_client_side_merge_or_fake_clear(self):
        # Classic's loadActivityLog merges a client-only in-memory log with
        # the API's; that in-memory log cannot survive a reload and its
        # "Clear" button cannot touch the real record. Neither should exist
        # here.
        src = _activity_src()
        assert "_activityLog" not in src
        assert "clearActivityLog" not in src


class TestDistinctEmptyErrorAndNoMatchStates:
    """No operations ever, a fetch failure, and a filter matching nothing
    are three different facts and must read as three different sentences."""

    def test_a_fetch_failure_reads_as_could_not_be_read(self):
        src = _activity_src()
        body = src[src.index("if (panel.error) {"):src.index("if (panel.entries === null) {")]
        assert "could" in body and "not be read" in body

    def test_zero_entries_ever_says_nothing_has_happened_yet(self):
        src = _activity_src()
        body = src[src.index("if (!panel.entries.length) {"):src.index("if (!rows.length) {")]
        assert "No operations recorded yet" in body
        assert "not because logging is broken" in body

    def test_a_filter_matching_nothing_is_distinct_from_true_emptiness(self):
        src = _activity_src()
        body = src[src.index("if (!rows.length) {"):src.index("const shownOfLoaded")]
        assert "Nothing recorded matches" in body
        assert "No operations recorded yet" not in body

    def test_a_saturated_page_says_so_rather_than_implying_completeness(self):
        # Same "N+"/page-boundary honesty the rest of /next uses (see
        # countOf() in app.js) -- FETCH_LIMIT rows back must not be presented
        # as the whole history.
        src = _activity_src()
        assert "panel.entries.length >= FETCH_LIMIT" in src
        assert "Showing the most recent" in src


class TestEntryRenderingCarriesWhatClassicShows:
    def test_operation_entity_status_and_relative_time_are_all_rendered(self):
        src = _activity_src()
        body = src[src.index("function entryRowHtml("):src.index("function renderList()")]
        assert "OPERATION_LABEL[op.operation]" in body
        assert "op.entity_slug" in body
        assert "STATUS_TONE[" in body and "STATUS_GLYPH[" in body
        assert "ago(op.ts)" in body

    def test_detail_is_collapsed_behind_a_toggle_not_always_shown(self):
        src = _activity_src()
        assert "data-activity-toggle" in src
        assert "class=\"hidden\">${annHtml}${itemsHtml}${detailHtml}</div>" in src
        wiring = src[src.index("const toggle = e.target.closest"):src.index("const toggle = e.target.closest") + 200]
        assert "classList.toggle('hidden')" in wiring

    def test_guids_are_shortened_with_click_to_copy_like_classic(self):
        src = _activity_src()
        assert "function hiGuid(" in src
        assert "data-copy-guid" in src
        assert "navigator.clipboard?.writeText(copy.dataset.copyGuid)" in src

    def test_rfa_operations_are_shown_not_hidden(self):
        # Rule 16 requires RFA operations to be logged; hiding them from this
        # list would recreate the exact "logged but invisible" gap the rule
        # exists to close, even though the RFA drawer itself is a separate,
        # not-yet-built /next surface.
        src = _activity_src()
        assert "rfa: 'RFA'" in src
        assert "rfa: '📝'" in src


class TestFilteringIsClientSideOverTheFetchedPage:
    def test_status_filter_and_text_filter_both_apply(self):
        src = _activity_src()
        body = src[src.index("function filteredEntries()"):src.index("function entryRowHtml(")]
        assert "panel.statusFilter !== 'all'" in body
        assert "entity_slug" in body and "summary" in body

    def test_escape_and_backdrop_click_close_the_panel_like_the_worklist_dialog(self):
        src = _activity_src()
        assert "function onPanelKeydown(e) {" in src
        assert "e.key === 'Escape'" in src
        assert "e.target === el || e.target.closest('[data-act=\"close\"]')" in src
