"""Automate's pane in /next (PLAN-FINISH-REPOS.md item 4): pinning the real
part of the port (subscription and schedule listing/toggling, against the
same routes classic uses). Subscription CREATION used to be this module's
one deliberate, honestly-named gap; as of re/next-automate-create-
subscription it is real, but lives in app.js's Questions-checklist engine
(a question row's "🔔 notify me" action), not in this file -- see
`TestSubscriptionCreationNowLivesInTheQuestionsEngine` below, and
test_next_notify_subscription.py for the creation flow itself.

No browser verification happened for this file with a signed-in session --
see ITEM-4-AUTOMATE-IMPLEMENTED.md for what was and was
not checked live. These tests grep/slice function bodies out of the
concatenated source, the established pattern for /next JS modules without a
browser -- see test_next_curate_pane.py and test_next_understanding_pane.py's
identical `_app()` helper, reproduced here.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _automate_src():
    return (NEXT / "stages" / "automate.js").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


class TestAutomateIsWiredIn:
    """The three wiring points PLAN-FINISH-REPOS.md's stub asked for: the
    STAGES flag, the import line, and a dispatch that bypasses the generic
    Questions engine (same shape as Understanding's)."""

    def test_automate_is_marked_built_in_the_stages_array(self):
        app = _app()
        stages = app[app.index("const STAGES = ["):app.index("];", app.index("const STAGES = ["))]
        automate_line = [
            l for l in stages.splitlines() if "id: 'automate'" in l
        ][0]
        assert "built: true" in automate_line

    def test_app_js_imports_the_automate_module(self):
        app = _app()
        assert "from '/static/next/stages/automate.js';" in app
        assert "renderAutomate" in app

    def test_load_pane_dispatches_automate_before_the_generic_pane_and_returns(self):
        app = _app()
        i = app.index("if (state.stage === 'automate') {")
        block = app[i:app.index("}", app.index("await renderAutomate();", i))]
        assert "await renderAutomate();" in block
        assert "return;" in app[i:i + 400]
        # It must come before the generic "not in /next" built-check, the way
        # Understanding's branch does, or a stage flagged built:true here
        # would still fall through to the shared Questions engine.
        # RULING-NAV-GROUPING.md: the frame check now reads `stageDef.class`
        # (declared once on STAGES) rather than a separate `stageDef.frame`
        # flag -- see NAV-GROUPING-IMPLEMENTED.md.
        j = app.index("if (stageDef.class === 'frame' || !stageDef.built) {")
        assert i < j


class TestSubscriptionsAreReal:
    def test_lists_subscriptions_against_the_real_route(self):
        api = _reapi_src()
        assert "export const listSubscriptions = (" in api
        assert "/api/automate/subscriptions" in api

    def test_toggling_calls_activate_or_deactivate(self):
        api = _reapi_src()
        assert "export const setSubscriptionActive = (id, active) =>" in api
        body = api[api.index("export const setSubscriptionActive"):api.index(
            ";", api.index("export const setSubscriptionActive"))]
        assert "activate" in body and "deactivate" in body

    def test_a_subscription_with_no_schedule_says_it_can_never_fire(self):
        src = _automate_src()
        assert "has_schedule === false" in src
        assert "never fires (no schedule)" in src

    def test_the_filter_checkbox_only_appears_with_a_selected_resource(self):
        src = _automate_src()
        body = src[src.index("async function renderSubscriptions("):src.index(
            "async function renderSchedules(")]
        assert "const filterToggle = slug" in body
        assert "data-automate-filter" in body

    def test_toggle_button_disables_itself_and_reports_a_save_failure_on_the_button(self):
        src = _automate_src()
        body = src[src.index("[data-toggle-sub]"):]
        assert "b.disabled = true;" in body
        assert "Not saved:" in body


class TestSchedulesAreReal:
    def test_lists_all_schedules_against_the_real_global_route(self):
        api = _reapi_src()
        assert "export const listAllSchedules = () => get('/api/schedules/');" in api

    def test_delete_hits_the_real_delete_route_and_confirms_first(self):
        api = _reapi_src()
        assert "export const deleteSchedule = (entityType, entitySlug, analysisId) =>" in api
        assert "method: 'DELETE'" in api[api.index("export const deleteSchedule"):]

        src = _automate_src()
        body = src[src.index("[data-delete-sched]"):]
        assert "inPageConfirm(" in body
        assert "deleteSchedule(entityType, entitySlug, analysisId)" in body


class TestSubscriptionCreationNowLivesInTheQuestionsEngine:
    """re/next-automate-create-subscription: creation is real now, but not
    IN this module -- it hangs off the Questions-checklist engine's question
    rows (app.js's `provenanceLine`/`openNotifyDialog`), since that is where
    Assessment/Analysis actually live in /next (there is no card grid here to
    attach a button to, and building a standalone form in this file would
    have been exactly the half-built-feature outcome PLAN-FINISH-REPOS.md
    warned against). This class pins that automate.js's own comment block
    was corrected to say so, that the pane's own copy points at the real
    action rather than the old dead end, and that automate.js itself still
    contains no create form -- the previous version of this test class
    pinned the opposite (creation deferred, `createSubscription` absent
    everywhere); the absence-from-this-file half is still true, so it is
    re-pinned here rather than dropped."""

    def test_the_top_comment_no_longer_claims_creation_is_unbuilt(self):
        src = _automate_src()
        assert "CREATING a subscription is now built too" in src
        assert "CREATING a subscription is NOT built here" not in src

    def test_the_top_comment_names_the_real_attachment_point(self):
        src = _automate_src()
        assert "openNotifyDialog" in src
        assert "provenanceLine" in src

    def test_the_pane_points_at_the_question_row_action_not_a_dead_end(self):
        src = _automate_src()
        body = src[src.index("async function renderSubscriptions("):src.index(
            "async function renderSchedules(")]
        assert "🔔 notify me" in body
        assert "question row" in body

    def test_it_no_longer_links_out_to_classic(self):
        # Classic was retired (re/retire-classic, 2026-10-21). The link-out was a fallback for database/filesystem
        # resources, which the Questions engine's openNotifyDialog has covered since re/next-db-fs-gate-removal
        # (2026-09-22), so nothing replaces it: the pane must not point at a UI that no longer exists.
        app = _app()
        assert "oldUiHref" not in app
        src = _automate_src()
        assert "oldUiHref" not in src
        assert "/classic" not in src

    def test_no_create_subscription_form_exists_in_this_module(self):
        # The real creation logic lives in app.js (openNotifyDialog) and
        # re-api.js (createSubscription), not here -- automate.js only reads
        # subscriptions, it never writes one into existence. The comment
        # block legitimately names classic's `_createSubscriptionFromCard`
        # (which contains "createSubscription" as a substring), so this
        # checks for an actual call/import instead of a bare substring.
        src = _automate_src()
        assert "await createSubscription(" not in src
        assert "createSubscription }" not in src
        assert "CreateSubscriptionRequest" not in src


class TestAutomateHasItsOwnSubnavNotTheSharedSubTabs:
    def test_automate_does_not_render_the_standard_sub_tabs_row(self):
        src = _automate_src()
        assert "subTabsHtml" not in src
        assert "bindSubTabs" not in src

    def test_it_toggles_between_exactly_two_tabs(self):
        src = _automate_src()
        body = src[src.index("function subnavHtml()"):src.index("function bindSubnav(")]
        assert "'subscriptions'" in body and "'schedules'" in body
