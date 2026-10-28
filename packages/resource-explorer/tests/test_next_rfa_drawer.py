"""The RFA drawer in /next (PLAN-FINISH-REPOS.md item 10): a real, scoped
panel that lets someone see and act on their RFAs without leaving /next,
replacing the old honest "The RFA drawer is not built in /next" link out to
classic.

`next/rfa.js` is a NEW top-level module (chrome-level infrastructure, like
`worklist.js` -- not a per-resource stage under `next/stages/`), so unlike
`test_next_curate_pane.py`/`test_next_rail_states.py` this concatenates
`app.js` with `rfa.js` directly rather than with `stages/*.js`. No browser
is available in this suite; these are structural/text pins over the real
source, same approach as the existing `_app()` helpers.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
NEXT = BASE / "resource_explorer" / "web" / "static" / "next"
ROUTES = BASE / "resource_explorer" / "web" / "routes"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    src += "\n" + (NEXT / "rfa.js").read_text(encoding="utf-8")
    return src


def _rfa_js():
    return (NEXT / "rfa.js").read_text(encoding="utf-8")


def _re_api():
    return (BASE / "resource_explorer" / "web" / "static" / "re-api.js").read_text(encoding="utf-8")


def _activity_routes():
    return (ROUTES / "activity.py").read_text(encoding="utf-8")


class TestTheHonestPlaceholderIsGone:
    def test_the_placeholder_link_and_its_apology_are_removed(self):
        app = _app()
        assert "The RFA drawer is not built in /next" not in app
        assert 'href="/" title="The RFA drawer' not in app

    def test_a_real_toggle_replaces_it(self):
        app = _app()
        assert 'id="rfa-drawer-toggle"' in app
        assert "import { toggleRfaDrawer } from '/static/next/rfa.js';" in app

    def test_the_toggle_is_wired_and_scoped_to_the_resource_in_view(self):
        app = _app()
        i = app.index("$('rfa-drawer-toggle')")
        line = app[i:app.index("\n", i)]
        assert "toggleRfaDrawer(" in line
        # Passes the CURRENT resource, if any -- state.selectedSlug is the
        # real field name (there is no state.slug); a per-resource drawer
        # that always widened to "all" would silently drop the "filtered to
        # the current resource" half of the done test.
        assert "state.selectedSlug" in line

    def test_the_header_count_badge_survives(self):
        app = _app()
        assert 'id="rfa-count"' in app
        assert "state.counts.rfas === null ? '–' : state.counts.rfas" in app


class TestTheDrawerIsChromeLevelNotAStage:
    def test_rfa_js_does_not_import_back_from_app_js(self):
        # It is imported BY app.js (like worklist.js) -- importing state/esc/$
        # back from app.js would be circular, unlike the stages/*.js modules
        # which are safe to import app.js from because app.js imports THEM.
        rfa = _rfa_js()
        assert "from '/static/next/app.js'" not in rfa

    def test_it_imports_only_the_api_it_needs(self):
        rfa = _rfa_js()
        # PI-112/113/114 added dismiss, restore and note to the same drawer.
        assert ("import { listRfas, updateRfaAction, dismissRfa, restoreRfaDismissal, saveRfaNote } "
                "from '/static/re-api.js';") in rfa


class TestTheThreeResponseActionsAreWired:
    """Defer / reassign / complete -- the local-only actions the backend
    already supports (`RFA_STATUSES` in web/routes/activity.py). Dismissal,
    restore and notes arrived with PI-112/113/114 (tests/test_rfa_grouped_ids.py and
    the p2-rfa-dismiss-restore-note harness test)."""

    def test_every_backend_status_the_drawer_offers_is_one_the_route_accepts(self):
        routes = _activity_routes()
        m = re.search(r'RFA_STATUSES = \{([^}]*)\}', routes)
        assert m, "could not find RFA_STATUSES in activity.py"
        backend_statuses = {s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()}
        rfa = _rfa_js()
        drawer_statuses = set(re.findall(r"data-rfa-act=\"(\w+)\"", rfa))
        assert drawer_statuses, "no data-rfa-act buttons found"
        assert drawer_statuses <= backend_statuses, (
            f"drawer offers a status the backend route does not accept: {drawer_statuses - backend_statuses}"
        )
        # And the three response actions plus reopen are actually present --
        # not just a subset that happens to validate.
        assert {"deferred", "reassigned", "completed", "open"} <= drawer_statuses

    def test_reopen_is_the_same_endpoint_with_status_open(self):
        rfa = _rfa_js()
        assert 'data-rfa-act="open"' in rfa
        # Only offered once the RFA is no longer already open.
        i = rfa.index('data-rfa-act="open"')
        before = rfa[max(0, i - 200):i]
        assert "rfa.rfa_status !== 'open'" in before

    def test_completed_rows_lose_the_defer_reassign_complete_buttons(self):
        rfa = _rfa_js()
        # A dismissed request offers Restore, not the response actions.
        assert "const canAct = rfa.rfa_status !== 'completed' && !rfa.dismissed;" in rfa

    def test_clicking_an_action_calls_the_real_update_endpoint(self):
        rfa = _rfa_js()
        body = rfa[rfa.index("async function _onListClick("):rfa.index("async function _load(")]
        assert "await updateRfaAction(rfaId, { status, assignee, deferUntil, resolutionNote })" in body
        # A failed PATCH says so on the row, it does not silently drop the click.
        assert "not recorded:" in body

    def test_a_successful_action_updates_the_row_in_place_not_a_full_reload(self):
        rfa = _rfa_js()
        body = rfa[rfa.index("async function _onListClick("):rfa.index("async function _load(")]
        assert "Object.assign(row, {" in body
        assert "_renderList();" in body
        assert "_load();" not in body, "a re-fetch on every action would drop the scope/closed toggle state"


class TestReAssignAndDeferAskBeforeActing:
    def test_reassign_prompts_for_an_assignee(self):
        rfa = _rfa_js()
        assert "inPagePrompt({ title: 'Assign', message: 'Assign to" in rfa
    def test_defer_prompts_for_a_defer_until_value(self):
        rfa = _rfa_js()
        assert "inPagePrompt({ title: 'Defer', message: 'Defer until" in rfa
    def test_cancelling_a_prompt_records_nothing(self):
        rfa = _rfa_js()
        # Every prompt call is followed by a `null` cancel-guard before use.
        assert rfa.count("if (typed === null) return;") >= 3


class TestScopingToTheCurrentResource:
    def test_scope_only_filters_by_entity_slug(self):
        rfa = _rfa_js()
        # The scope filter moved into _scopedRows so the suppressed count is taken over the same scope.
        body = rfa[rfa.index("function _scopedRows("):rfa.index("function _rowHtml(")]
        assert "r.entity_slug === _scopeSlug" in body

    def test_show_closed_excludes_completed_by_default(self):
        rfa = _rfa_js()
        body = rfa[rfa.index("function _visibleRows("):rfa.index("function _rowHtml(")]
        assert "r.rfa_status !== 'completed'" in body

    def test_opening_with_no_slug_does_not_silently_scope_to_nothing(self):
        rfa = _rfa_js()
        body = rfa[rfa.index("export function openRfaDrawer("):rfa.index("export function closeRfaDrawer(")]
        assert "_scopeSlug = slug || '';" in body


class TestEachRowShowsWhatItIsAbout:
    def test_the_row_carries_entity_analysis_summary_and_explanation(self):
        rfa = _rfa_js()
        body = rfa[rfa.index("function _rowHtml("):rfa.index("function _renderList(")]
        for field in ("rfa.entity_name", "rfa.analysis_name", "rfa.summary", "rfa.explanation",
                      "rfa.action_requested", "rfa.action_target_name"):
            assert field in body, f"row is missing {field}"

    def test_dismissed_rfas_stay_visible_with_a_reason_not_hidden(self):
        # docs/rfa-dismissals.md: "suppress with visibility" -- a dismissed
        # RFA is still a row, never a silently-vanished one.
        rfa = _rfa_js()
        body = rfa[rfa.index("function _rowHtml("):rfa.index("function _renderList(")]
        assert "rfa.dismissed" in body and "dismissal" in body


class TestTheApiHelperMatchesTheRoute:
    def test_update_rfa_action_patches_the_real_endpoint(self):
        api = _re_api()
        assert "export const updateRfaAction = " in api
        body = api[api.index("export const updateRfaAction = "):api.index("export const updateRfaAction = ") + 400]
        assert "patch(`/api/activity/rfas/${encodeURIComponent(rfaId)}`" in body

    def test_the_payload_field_names_match_rfaactionupdaterequest(self):
        routes = _activity_routes()
        assert "class RfaActionUpdateRequest(BaseModel):" in routes
        model = routes[routes.index("class RfaActionUpdateRequest(BaseModel):"):
                        routes.index("class RfaNoteRequest(BaseModel):")]
        for field in ("status", "assignee", "defer_until", "resolution_note"):
            assert field in model

        api = _re_api()
        body = api[api.index("export const updateRfaAction = "):api.index("export const updateRfaAction = ") + 400]
        for wire_field in ("status", "assignee", "defer_until", "resolution_note"):
            assert wire_field in body
