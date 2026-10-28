"""The Investigation frame pane in /next (stages/investigation.js): a real
port of classic's (index.html) Investigations tab -- list/create,
per-investigation detail (members across repo/database/filesystem,
per-member disposition, next-steps, purposes, classification), the Egeria
bind/promote/sync/relink flow, reclassify, and close/suspend/reopen.

Before this, stages/investigation.js was a 17-line honest placeholder and
`loadPane()` rendered every `class: 'frame'` STAGES entry as "not in /next".

No browser verification with a signed-in session is asserted by these
tests -- they grep/slice the concatenated source, the established pattern
for /next JS modules without a browser (see test_next_discovery_import_search.py's
identical `_app()` helper, reproduced here plus this module's own source).
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _inv_src():
    return (NEXT / "stages" / "investigation.js").read_text(encoding="utf-8")


def _app_src():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


class TestStageIsFrameNotAStage:
    """Investigation is not a 9th canonical intent
    (docs/investigation-framing-design.md §1) -- it stays `class: 'frame'`
    in STAGES and bypasses the generic Questions-checklist engine, the same
    shape as Understanding/Automate."""

    def test_stages_array_still_marks_investigation_as_frame(self):
        app = _app_src()
        assert "{ id: 'investigation', label: 'Investigation', class: 'frame' }" in app

    def test_load_pane_dispatches_to_the_real_renderer(self):
        app = _app_src()
        assert "await renderInvestigation();" in app
        assert "from '/static/next/stages/investigation.js';" in app

    def test_frame_class_no_longer_falls_into_the_generic_not_in_next_message(self):
        # The old branch printed "not in /next" for every frame-class stage.
        # Investigation must be handled before that branch is reached.
        app = _app_src()
        load_pane = app[app.index("async function loadPane()"):]
        inv_branch = load_pane.index("state.stage === 'investigation'")
        old_branch = load_pane.index("!stageDef?.built")
        assert inv_branch < old_branch


class TestModuleExports:
    def test_exports_render_and_deep_link_entry_points(self):
        src = _inv_src()
        assert "export async function renderInvestigation()" in src
        assert "export function openInvestigationDetail(slug)" in src

    def test_imports_shared_chrome_from_app_js_not_the_reverse(self):
        # Same convention as curate.js/automate.js: a stage module imports
        # state/esc/$/... back from app.js rather than being handed a
        # container/resource as parameters.
        src = _inv_src()
        assert "from '/static/next/app.js';" in src
        idx = src.index("from '/static/next/app.js';")
        block = src[max(0, idx - 300):idx]
        for name in ("state", "esc", "$", "setInvestigation", "refreshInvestigationsAndSidebar"):
            assert name in block


class TestApiWrappersCoverTheFullRouteSurface:
    """web/routes/investigations.py has ~18 routes; only list/members
    wrappers pre-existed in re-api.js. Every remaining route needs one."""

    def test_full_route_surface_has_wrappers(self):
        api = _reapi_src()
        expected_paths = {
            "/api/investigations/purposes",
            "/api/investigations/classifications",
            "/api/investigations/${encodeURIComponent(slug)}`",
            "/api/investigations/${encodeURIComponent(slug)}/close",
            "/api/investigations/${encodeURIComponent(slug)}/suspend",
            "/api/investigations/${encodeURIComponent(slug)}/reopen",
            "/api/investigations/${encodeURIComponent(slug)}/egeria-project",
            "/api/investigations/${encodeURIComponent(slug)}/promote",
            "/api/investigations/${encodeURIComponent(slug)}/reclassify",
            "/api/investigations/${encodeURIComponent(slug)}/relink-members",
            "/api/investigations/${encodeURIComponent(slug)}/sync-egeria",
            "/api/investigations/${encodeURIComponent(slug)}/dispositions",
            "/api/investigations/${encodeURIComponent(slug)}/next-steps",
        }
        for path in expected_paths:
            assert path in api, f"missing wrapper for {path}"

    def test_create_posts_the_full_investigation_create_body(self):
        api = _reapi_src()
        fn = api[api.index("export const createInvestigation"):]
        fn = fn[:fn.index("\n\n")]
        for field in (
            "display_name", "description", "purposes", "project_classification",
            "egeria_binding", "hypothesis", "egeria_project_guid", "egeria_project_qualified_name",
        ):
            assert field in fn

    def test_no_duplicate_list_investigations_export(self):
        # The file used to carry two separate "Investigations" sections,
        # each with its own listInvestigations -- a syntax error waiting to
        # happen if both were ever built from a plain script tag instead of
        # a module, and a maintenance trap either way. Must be exactly one.
        api = _reapi_src()
        assert api.count("export const listInvestigations = ") == 1

    def test_set_disposition_uses_query_params_matching_the_route_signature(self):
        # POST /{slug}/dispositions/{entity_type}/{entity_slug}?disposition=&rationale=
        api = _reapi_src()
        fn = api[api.index("export const setInvestigationDisposition"):]
        fn = fn[:fn.index("\n\n")]
        assert "?disposition=" in fn
        assert "&rationale=" in fn


class TestListView:
    def test_create_button_and_dialog_wired(self):
        src = _inv_src()
        assert 'data-act="inv-new"' in src
        assert "openCreateDialog" in src

    def test_include_closed_toggle_reflects_module_state(self):
        src = _inv_src()
        assert "let _includeClosed = false;" in src
        assert "includeClosed: _includeClosed" in src

    def test_current_investigation_is_marked_in_the_list(self):
        src = _inv_src()
        assert "inv.slug === state.investigation" in src

    def test_private_investigations_show_a_visibility_note(self):
        # docs/investigation-classification-and-zoning-design.md:
        # PRIVATE_CLASSIFICATIONS investigations 404 for non-owners and are
        # visible only to their creator -- the ones that DO come back must
        # say so, using the server's own visibility_note rather than a
        # hardcoded guess at the reason.
        src = _inv_src()
        assert "inv.visibility === 'private'" in src
        assert "visibility_note" in src


class TestCreateFormMatchesTheClassificationVocabulary:
    def test_hypothesis_field_is_conditional_and_cleared_when_hidden(self):
        # HYPOTHESIS_REQUIRED_FOR = ("Experiment",) -- registry.py. The
        # field must hide/show off the served requires_hypothesis flag, not
        # a hardcoded "Experiment" string comparison in the UI, and must be
        # cleared (not just hidden) so a stale value can't ride along.
        src = _inv_src()
        assert "requires_hypothesis" in src
        fn = src[src.index("const syncHyp = () => {\n    const need = requiresHyp"):]
        fn = fn[:fn.index("};")]
        assert "hypWrap.classList.toggle" in fn
        assert ".value = '';" in fn

    def test_two_separate_selects_for_classification_and_binding(self):
        # docs/investigation-classification-and-zoning-design.md: "a single
        # list can't express an ad-hoc Personal investigation" -- must be
        # two <select> controls, not one merged dropdown.
        src = _inv_src()
        assert 'id="inv-new-class"' in src
        assert 'id="inv-new-binding"' in src


class TestMembersAndDispositions:
    def test_member_form_offers_all_three_entity_types(self):
        src = _inv_src()
        for t in ("repo", "database", "filesystem"):
            assert f"['{t}'," in src

    def test_add_member_slug_datalist_reflects_the_selected_entity_type(self):
        # Regression: the slug field's autocomplete used to always list
        # state.projects (repos) regardless of the type dropdown, so picking
        # "Database"/"Filesystem" left no suggestions at all -- reachable in
        # principle (the click handler reads the type correctly), but with
        # nothing telling a user what a valid slug looked like. The type
        # select must react to its own change and refresh the datalist from
        # the matching resource list, loading it on demand if not cached yet.
        src = _inv_src()
        assert "ensureResourceListLoaded" in src
        change_block = src[src.index("#inv-add-type"):]
        change_block = change_block[change_block.index("addEventListener('change'"):]
        change_block = change_block[:change_block.index("});")]
        assert "ensureResourceListLoaded" in change_block
        assert "inv-add-slug-list" in change_block
        assert "state.databases" in src and "state.filesystems" in src

    def test_disposition_select_offers_the_full_working_set_vocabulary(self):
        src = _inv_src()
        for d in ("tracking", "investigating", "recommended", "using", "abandoned", "ignored"):
            assert d in src

    def test_undecided_is_the_absence_of_a_disposition_not_a_server_value(self):
        # registry.py: undecided/unjudged is computed client-side as
        # membership minus every disposition set, never a value of its own.
        src = _inv_src()
        assert "◌ undecided" in src
        assert '<option value="" $' in src

    def test_add_member_refreshes_the_sidebar_working_set_when_current(self):
        # loadWorkingSet()'s cache only tracks the CURRENT investigation --
        # a write to a different one must not trigger a pointless refresh,
        # but a write to the current one must, or "In scope" in the sidebar
        # goes stale until reload.
        src = _inv_src()
        fn = src[src.index("[data-act=\"inv-add-member\"]"):]
        fn = fn[:fn.index("});")]
        assert "inv.slug === state.investigation" in fn
        assert "refreshInvestigationsAndSidebar" in fn


class TestEgeriaFlow:
    def test_bind_promote_sync_relink_unbind_all_present(self):
        src = _inv_src()
        for act in ("inv-bind", "inv-promote", "inv-sync", "inv-relink", "inv-unbind"):
            assert f'data-act="{act}"' in src

    def test_promote_result_reports_partial_success_distinctly(self):
        # PromotionResult carries members_linked/members_unlinkable/errors
        # separately -- a promote that half-worked must not read as either
        # a clean success or a clean failure.
        src = _inv_src()
        fn = src[src.index("inv-promote"):]
        fn = fn[:fn.index("el.querySelector('[data-act=\"inv-sync\"]')")]
        assert "members_linked" in fn
        assert "members_unlinkable" in fn
        assert "result.ok" in fn

    def test_unlinkable_members_render_slug_and_reason_not_the_raw_object(self):
        # members_unlinkable is list[dict] (entity_type/entity_slug/reason --
        # egeria_investigation_publisher.py), not list[str]. Mapping it
        # straight through esc() (as members_linked/errors correctly do,
        # since THOSE are list[str]) stringifies each dict to the literal
        # text "[object Object]" -- reproduced live 2026-09-22 on a real
        # promote with unlinkable members. The fix must read a field off
        # each entry, not hand the whole object to esc().
        src = _inv_src()
        fn = src[src.index("inv-promote"):]
        fn = fn[:fn.index("el.querySelector('[data-act=\"inv-sync\"]')")]
        unlinkable_line = fn[fn.index("members_unlinkable"):]
        unlinkable_line = unlinkable_line[:unlinkable_line.index("\n", unlinkable_line.index("\n") + 1) + 40]
        assert "members_unlinkable.map(esc)" not in fn, (
            "regression: mapping members_unlinkable straight through esc() "
            "renders '[object Object]' for each entry"
        )
        if "members_unlinkable.map(memberLine)" in unlinkable_line:
            # Brief Z3: one helper renders a member line (with the not-permitted cue); it reads the fields.
            helper = src[src.index("const memberLine"):]
            unlinkable_line = helper[:helper.index("};")]
        assert "entity_slug" in unlinkable_line
        assert "reason" in unlinkable_line

    def test_promote_reports_classification_confirmation_tristate(self):
        # classification_confirmed is tri-state: equals request / '' (dropped)
        # / null (couldn't verify) -- collapsing this to a boolean would
        # silently hide the "couldn't verify" case, per classic's own
        # 3-state rendering (index.html investigations panel).
        src = _inv_src()
        fn = src[src.index("inv-promote"):]
        fn = fn[:fn.index("el.querySelector('[data-act=\"inv-sync\"]')")]
        assert "classification_confirmed === result.classification_requested" in fn
        assert "classification_confirmed === null" in fn


class TestReclassify:
    def test_reclassify_names_the_tightening_risk(self):
        # docs/investigation-classification-and-zoning-design.md: tightening
        # (public -> private) can be permanently unavailable once artifacts
        # reach a public zone; loosening always works. The UI must say this,
        # not just fire the request silently.
        src = _inv_src()
        fn = src[src.index("'[data-act=\"inv-reclassify\"]'"):]
        fn = fn[:fn.index("el.querySelector('[data-act=\"inv-close\"]')")]
        assert "Loosening" in fn or "loosening" in fn
        assert "Tightening" in fn or "tightening" in fn

    def test_reclassify_reports_still_public_and_local_applied(self):
        src = _inv_src()
        fn = src[src.index("'[data-act=\"inv-reclassify\"]'"):]
        fn = fn[:fn.index("el.querySelector('[data-act=\"inv-close\"]')")]
        assert "still_public" in fn
        assert "local_applied" in fn


class TestLifecycle:
    def test_close_asks_for_confirmation_and_says_nothing_is_deleted(self):
        src = _inv_src()
        fn = src[src.index("'[data-act=\"inv-close\"]'"):]
        fn = fn[:fn.index("'[data-act=\"inv-suspend\"]'")]
        assert "inPageConfirm" in fn
        assert "Nothing is deleted" in fn

    def test_suspend_and_reopen_do_not_require_confirmation(self):
        # Mirrors classic: only close's lifecycle-aware confirm dialog
        # exists; suspend/reopen are lighter, reversible actions.
        src = _inv_src()
        suspend_fn = src[src.index("'[data-act=\"inv-suspend\"]'"):]
        suspend_fn = suspend_fn[:suspend_fn.index("'[data-act=\"inv-reopen\"]'")]
        assert "inPageConfirm" not in suspend_fn


class TestOpenInvestigationLinkFromResourceHeader:
    """The /next equivalent of classic's index.html "Open Investigation →"
    link (~line 5779). Classic's own version has no deep link to a specific
    investigation either (a resource can be in several); this one switches
    to the frame and opens whichever investigation is current."""

    def test_link_only_shown_when_resource_is_in_the_current_working_set(self):
        app = _app_src()
        fn = app[app.index("export function resourceHeaderHtml"):]
        fn = fn[:fn.index('<div id="resource-action"')]
        assert "state.investigation && state.workingSet.has(slug)" in fn
        assert 'data-act="open-investigation"' in fn

    def test_click_opens_the_investigation_detail_not_just_the_list(self):
        app = _app_src()
        fn = app[app.index("[data-act=\"open-investigation\"]"):]
        fn = fn[:fn.index("});") + 3]
        assert "openInvestigationDetail(state.investigation)" in fn
        assert "state.stage = 'investigation'" in fn


class TestVocabularyIsServedNotHardcoded:
    """docs/Architecture.md / registry.py: purposes and classifications are
    server-served valid-value-sets, not a second hardcoded RE vocabulary."""

    def test_purposes_and_classifications_come_from_the_api(self):
        """Purposes load through the shared purposes.js (one fetch of
        /api/investigations/purposes for every picker, Egeria's list behind it)."""
        src = _inv_src()
        purposes = (NEXT / "purposes.js").read_text(encoding="utf-8")
        assert "loadPurposes" in src and "from '/static/next/purposes.js'" in src
        assert "getInvestigationPurposes" in purposes
        assert "getInvestigationClassifications" in src
        assert "_classifications = null;" in src
