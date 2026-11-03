"""Admin's own /next surface (PLAN-FINISH-REPOS.md item 5): pinning that the
header's ⚙ Admin button opens a real overlay panel — chrome-level, decoupled
from #intent-nav, the same pattern as Activity — with nine real ports
(Annotation Types browse, Question Catalog, Logs, Feedback, Prefect,
Discovery Sources, Groups, Egeria Alignment, Repair) and two named, specific
deferrals (Egeria Links, Publish Queue), each linking out to classic
via the shared `oldUiHref()` helper.

Discovery Sources was ported from a deferral to a full build under
SPEC-ADMIN-THE-FOUR-GAPS.md §3 — see TestDiscoverySourcesPane below and
DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md. Groups was ported
under §2 — see TestGroupsPane below and GROUPS-ADMIN-IMPLEMENTED.md.
Egeria Alignment (Resync) and Repair were ported under §1 — see
TestResyncPane/TestRepairPane below and RECONCILE-ADMIN-IMPLEMENTED.md.

No browser verification of a signed-in session happened for this file — see
ITEM-5-ADMIN-IMPLEMENTED.md for what was and was not
checked live. These tests grep/slice function bodies out of the concatenated
source, the established pattern for /next JS modules without a browser —
see test_next_activity_pane.py and test_next_automate_pane.py's identical
`_app()` helper, reproduced here.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"

#: Egeria Links and Publish Queue were the two named deferrals; both were built under parity P1
#: (PI-130, PI-131). The not-built page in admin/index.js was removed as unreachable.
BUILT_TAB_IDS = [
    "admin-od-portals",
    "admin-od-directories",
    "admin-od-datasets",
    "admin-egeria-links",
    "admin-outbox",
    "annotations",
    "admin-groups",
    "admin-question-catalog",
    "admin-prefect",
    "admin-feedback",
    "admin-logs",
    "admin-resync",
    "admin-repair",
    "admin-discovery-sources",
    "admin-api-fetch",
    "admin-egeria-mounts",
    # architect, 2026-10-24: the value profile's settings (the written-live share among them) readable by a person
    "admin-quality-settings",
]


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _admin_index_src():
    return (NEXT / "admin" / "index.js").read_text(encoding="utf-8")


def _admin_module(name):
    return (NEXT / "admin" / name).read_text(encoding="utf-8")


def _index_html():
    return (NEXT / "index.html").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


class TestAdminIsNotAStagesEntry:
    """Admin is a header-triggered overlay, exactly like Activity -- not a
    STAGES array entry with `built: true`, and not read via currentNavIntent."""

    def test_admin_is_absent_from_the_stages_array(self):
        app = _app()
        stages_block = app[app.index("const STAGES = ["):app.index("];", app.index("const STAGES = ["))]
        assert "id: 'admin'" not in stages_block

    def test_the_module_documents_why_it_is_not_a_stage(self):
        src = _admin_index_src()
        assert "NOT one of app.js's STAGES entries" in src
        assert "decoupled from" in src


class TestTheHeaderButtonOpensAdmin:
    def test_the_placeholder_outbound_link_is_gone(self):
        html = _index_html()
        assert "Admin and feedback are not built in /next" not in html

    def test_the_header_control_is_a_button_not_an_outbound_link(self):
        html = _index_html()
        assert 'id="admin-open-btn"' in html
        btn_start = html.index('id="admin-open-btn"')
        tag_start = html.rindex("<", 0, btn_start)
        assert html[tag_start:tag_start + 10].lower().startswith("<button")

    def test_app_js_wires_the_button_to_openadminpanel(self):
        app = _app()
        assert "import { openAdminPanel } from '/static/next/admin/index.js';" in app
        assert "function wireAdminButton()" in app
        body = app[app.index("function wireAdminButton()"):app.index("function wireAdminButton()") + 400]
        assert "$('admin-open-btn')" in body
        assert "openAdminPanel()" in body

    def test_the_wiring_is_idempotent(self):
        app = _app()
        body = app[app.index("function wireAdminButton()"):app.index("function wireAdminButton()") + 400]
        assert "if (adminButtonWired) return;" in body


class TestGroupsAndTabsMatchClassic:
    """Classic groups eleven panes into Configure/Reconcile/Observe
    (commits 4fb48071/26320f89). The /next port must offer the same
    grouping and the same set of destinations -- not a reshuffled subset."""

    def test_three_groups_named_configure_reconcile_observe(self):
        src = _admin_index_src()
        assert "{ name: 'Configure'" in src
        assert "{ name: 'Reconcile'" in src
        assert "{ name: 'Observe'" in src

    def test_every_classic_tab_id_is_present(self):
        src = _admin_index_src()
        for tab_id in BUILT_TAB_IDS:
            assert f"id: '{tab_id}'" in src, f"missing tab id {tab_id!r}"

    def test_exactly_built_tab_ids_are_wired_to_a_real_renderer(self):
        src = _admin_index_src()
        render_count = src.count("render: render")
        assert render_count == len(BUILT_TAB_IDS)


class TestValueProfileSettingsPane:
    """A read-only Configure pane (architect, 2026-10-24): each value profile and quality check setting with its value,
    source, environment name and meaning, read from GET /api/quality-checks/settings. Behaviour is in the harness
    (admin-quality-settings.test.mjs); this pins the wiring."""

    def test_the_tab_is_in_configure_and_renders_its_own_module(self):
        src = _admin_index_src()
        configure = src[src.index("{ name: 'Configure'"):src.index("{ name: 'Reconcile'")]
        assert ("{ id: 'admin-quality-settings', label: '📏 Value profile settings', render: renderQualitySettings }"
                in configure)
        assert "import { renderQualitySettings } from '/static/next/admin/quality_settings.js';" in src

    def test_the_pane_only_reads(self):
        pane = _admin_module("quality_settings.js")
        assert "getQualitySettings" in pane and "read-only" in pane
        assert "export const getQualitySettings = () => get('/api/quality-checks/settings');" in _reapi_src()
        assert not any(verb in pane for verb in ("post(", "put(", "del("))


class TestEgeriaLinksAndPublishQueueAreBuilt:
    def test_both_tabs_render_their_own_module_and_carry_no_deferral(self):
        src = _admin_index_src()
        for tab_id, renderer in (("admin-egeria-links", "renderEgeriaLinks"), ("admin-outbox", "renderOutbox")):
            i = src.index(f"id: '{tab_id}'")
            block = src[i:src.index("},", i) + 2]
            assert f"render: {renderer}" in block
            assert "defer:" not in block
        assert (NEXT / "admin" / "egeria_links.js").exists() and (NEXT / "admin" / "outbox.js").exists()


class TestNoDeferralMachinery:
    """Every admin tab renders; the not-built page (and its link out to the
    old UI) was removed in re/next-remove-deferral-stubs."""

    def test_admin_index_has_no_not_built_page_or_old_ui_link(self):
        src = _admin_index_src()
        assert "deferredHtml" not in src
        assert "not built in /next" not in src
        assert "oldUiHref" not in src
        assert "defer:" not in src


class TestAnnotationTypesBrowse:
    def test_lists_against_the_real_route(self):
        api = _reapi_src()
        assert "export const listAnnotationTypes = () => get('/api/analyses/annotation-types');" in api

    def test_renders_a_detail_view_with_edit_and_delete(self):
        src = _admin_module("annotation_types.js")
        assert "export async function renderAnnotationTypes(host)" in src
        assert "data-edit" in src
        assert "data-delete" in src

    def test_mutations_are_no_longer_deferred_to_classic(self):
        """SPEC-ADMIN-THE-FOUR-GAPS.md §4 (2026-09-20): register/edit/delete
        against the routes that already existed — this pane used to punt
        every write to classic via `oldUiHref()`; it doesn't anymore."""
        src = _admin_module("annotation_types.js")
        assert "oldUiHref" not in src
        api = _reapi_src()
        assert "registerAnnotationType" in api
        assert "updateAnnotationType" in api
        assert "deleteAnnotationType" in api

    def test_delete_confirmation_names_blast_radius_or_says_unknown(self):
        """§0/§4: a destructive confirmation must say how many annotations
        are affected, or say the count is unknown — never imply zero."""
        src = _admin_module("annotation_types.js")
        assert "getAnnotationTypeUsage" in src
        assert "UNKNOWN" in src or "unknown" in src


class TestQuestionCatalogBrowse:
    def test_reads_the_real_route(self):
        api = _reapi_src()
        assert "/api/analyses/question-catalog" in api

    def test_filters_by_stage_and_perspective_client_side(self):
        src = _admin_module("question_catalog.js")
        assert "state.stage" in src
        assert "state.perspectives" in src

    def test_export_render_function_exists(self):
        src = _admin_module("question_catalog.js")
        assert "export async function renderQuestionCatalog(host)" in src

    def test_add_and_retire_are_offered_but_no_edit_form_exists(self):
        """SPEC-ADMIN-THE-FOUR-GAPS.md §4 (2026-09-20, project owner decision):
        append-only — add and retire are real UI actions here; there is no
        third path that reworks an existing question's own text."""
        src = _admin_module("question_catalog.js")
        assert "addQuestionCatalogEntry" in src
        assert "retireQuestionCatalogEntry" in src
        assert "data-retire" in src
        assert "updateQuestionCatalogEntry" not in src
        assert "editQuestionCatalogEntry" not in src

    def test_retired_questions_are_shown_distinctly_not_hidden(self):
        src = _admin_module("question_catalog.js")
        assert "retired" in src
        assert "LIFECYCLE" in src


class TestLogsPane:
    def test_reads_the_real_route(self):
        api = _reapi_src()
        assert "export const listLogs = (" in api
        assert "/api/logs/" in api

    def test_three_distinct_empty_states_not_one_generic_message(self):
        src = _admin_module("logs.js")
        assert "buffer is empty" in src
        assert "No records match this filter" in src
        assert "No records to show" in src

    def test_auto_refresh_stops_when_switching_away_from_this_pane(self):
        src = _admin_module("logs.js")
        assert "adminPane !== 'logs'" in src


class TestFeedbackPane:
    def test_reads_the_real_gated_route(self):
        src = _admin_module("feedback.js")
        assert "/api/curate/feedback" in src

    def test_reuses_classics_admin_token_key_and_header(self):
        src = _admin_module("feedback.js")
        assert "re_admin_token" in src
        assert "X-Admin-Token" in src

    def test_a_403_renders_the_token_gate_not_an_empty_list(self):
        src = _admin_module("feedback.js")
        assert "res.status === 403" in src
        assert "tokenGateHtml" in src

    def test_absent_rating_renders_an_em_dash_not_zero_stars(self):
        src = _admin_module("feedback.js")
        i = src.index("function ratingHtml")
        body = src[i:i + 300]
        assert "r === null || r === undefined" in body
        assert "—" in body


class TestPrefectPane:
    def test_reads_status_and_flow_runs_and_can_cancel(self):
        api = _reapi_src()
        assert "export const getPrefectStatus = ()" in api
        assert "export const listPrefectFlowRuns = (" in api
        assert "export const cancelPrefectFlowRun = (" in api

    def test_cancel_is_confirmed_before_the_write(self):
        src = _admin_module("prefect.js")
        assert "inPageConfirm(" in src


class TestGroupsPane:
    """SPEC-ADMIN-THE-FOUR-GAPS.md §2: create/delete/assign/suggestions, all
    ports of classic's createAdminGroup/deleteAdminGroup/
    openAssignGroupModal+submitAssignGroup/applyGroupSuggestion against the
    existing /api/projects/groups* routes. See groups.js's own header for
    what's a straight port versus a deliberate departure (assignment lives
    in this pane, not behind a per-resource button, since /next has none
    yet)."""

    def test_the_tab_is_wired_to_a_real_renderer_not_deferred(self):
        src = _admin_index_src()
        assert "{ id: 'admin-groups', label: '🗂 Groups', render: renderGroups }" in src

    def test_reapi_exposes_create_delete_and_suggestions(self):
        api = _reapi_src()
        assert "export const groupSuggestions = ()" in api
        assert "/api/projects/groups/suggestions" in api
        assert "export const createGroup = (" in api
        assert "export const deleteGroup = (" in api

    def test_delete_has_no_confirmation_flag_on_the_route_itself(self):
        # Mirrors removeProject's own comment: the DELETE route takes no
        # confirm parameter, so the caller's inPageConfirm is the only
        # confirmation that will ever exist.
        api = _reapi_src()
        i = api.index("export const deleteGroup = (")
        body = api[max(0, i - 400):i + 100]
        assert "confirmation" in body.lower()

    def test_delete_confirms_and_names_where_members_go_not_that_they_vanish(self):
        # SPEC-ADMIN-THE-FOUR-GAPS.md §0/§2: a group delete reads as
        # destructive and is not -- the confirmation must say members return
        # to Ungrouped, not merely warn generically. Classic's own pane has
        # no confirm here at all (checked directly against index.html) --
        # this is the fix, not a copy.
        src = _admin_module("groups.js")
        i = src.index("async function onDeleteGroup(")
        body = src[i:src.index("\n}\n", i)]
        assert "inPageConfirm(" in body
        assert "return to Ungrouped" in body
        assert "nothing is deleted" in body

    def test_suggestions_are_rendered_and_applied_via_the_real_routes(self):
        src = _admin_module("groups.js")
        assert "groupSuggestions()" in src
        assert "async function onApplySuggestion(" in src
        assert "createGroup(" in src
        assert "assignGroup(" in src

    def test_assignment_uses_the_real_route_and_all_three_resource_types(self):
        api = _reapi_src()
        assert "export const assignGroup = (" in api
        assert "/api/projects/${encodeURIComponent(slug)}/group" in api
        src = _admin_module("groups.js")
        assert "kind === 'database' ? 'database' : kind === 'filesystem' ? 'filesystem' : 'repo'" in src

    def test_a_mutation_refreshes_the_sidebars_own_copy_of_groups(self):
        # state.groups/state.projects are otherwise only populated once, in
        # app.js's start() -- without this, the sidebar would show stale
        # groupings until a full page reload.
        app = _app()
        assert "export async function refreshGroupsAndSidebar()" in app
        src = _admin_module("groups.js")
        assert "refreshGroupsAndSidebar" in src


class TestResyncPane:
    """SPEC-ADMIN-THE-FOUR-GAPS.md §1 — Resync is global drift reconciliation,
    a different job from Repair (per-repo correction). The whole design is:
    do not flatten a Finding's `repair_step`/`needs_decision` into "a row with
    a button" — three distinct shapes, not one generic list item."""

    def test_reads_the_real_scan_and_apply_routes(self):
        api = _reapi_src()
        assert "export const getResyncScan = ()" in api
        assert "/api/egeria/resync/scan" in api
        assert "export const applyResyncSteps = (" in api
        assert "/api/egeria/resync/apply" in api

    def test_export_render_function_exists(self):
        src = _admin_module("resync.js")
        assert "export async function renderResync(host)" in src

    def test_unreachable_is_never_rendered_as_no_drift(self):
        src = _admin_module("resync.js")
        assert "d.reachable" in src
        assert "Deliberately not reported as" in src

    def test_scheduled_steps_get_a_state_row_not_a_fix_button(self):
        src = _admin_module("resync.js")
        assert "clear_stale_assets" in src
        assert "clear_orphan_publish_claims" in src
        assert "flag_vanished_publishes" in src
        assert "function scheduledRowHtml" in src
        body = src[src.index("function scheduledRowHtml"):src.index("function scheduledRowHtml") + 2200]
        assert "Run now" in body
        # A scheduled row must not carry the same tick-a-box affordance as a
        # repairable one -- it is a status report with a "run now" action,
        # not a selectable fix.
        assert "data-resync-step" not in body

    def test_no_button_when_repair_step_is_empty(self):
        src = _admin_module("resync.js")
        assert "function decisionRowHtml" in src
        body = src[src.index("function decisionRowHtml"):src.index("function decisionRowHtml") + 900]
        assert "checkbox" not in body
        assert "<button" not in body

    def test_needs_decision_is_framed_as_a_question_not_an_action(self):
        src = _admin_module("resync.js")
        body = src[src.index("function decisionRowHtml"):src.index("function decisionRowHtml") + 900]
        assert "your call" in body

    def test_clear_stale_investigations_names_the_binding_it_unbinds(self):
        """The most dangerous control in the product per egeria_resync.py's
        own comment above SAFE_SCHEDULED_STEPS -- its confirmation must say
        what it unbinds and how many, not just "clears N records"."""
        src = _admin_module("resync.js")
        i = src.index("clear_stale_investigations:")
        block = src[i:i + 700]
        assert "UNBIND" in block.upper()
        assert "Project" in block

    def test_apply_selected_confirms_before_writing(self):
        src = _admin_module("resync.js")
        assert "inPageConfirm(" in src
        assert "async function applySelected" in src

    def test_scheduled_flag_comes_from_finding_data_not_a_hardcoded_list(self):
        """RESYNC-STATUS-ROUTE-IMPLEMENTED.md item 2 -- this file used to
        keep its own `SCHEDULED_STEPS` Set mirroring egeria_resync.py's
        SAFE_SCHEDULED_STEPS by hand. That duplication must be gone: the
        pane now reads `finding.scheduled`, a field the backend computes."""
        src = _admin_module("resync.js")
        # The old hand-maintained copy declared its own `SCHEDULED_STEPS`
        # constant -- checked as a standalone identifier (not a substring)
        # so this doesn't false-positive on `SAFE_SCHEDULED_STEPS`, which is
        # legitimately still named in comments referencing egeria_resync.py.
        assert "const SCHEDULED_STEPS" not in src
        assert "new Set([" not in src
        assert "f.scheduled" in src
        assert "f.repair_step && f.scheduled" in src
        assert "f.repair_step && !f.scheduled" in src

    def test_reads_the_real_status_route(self):
        api = _reapi_src()
        assert "export const getResyncStatus = ()" in api
        assert "/api/egeria/resync/scheduler-status" in api
        src = _admin_module("resync.js")
        assert "getResyncStatus" in src

    def test_consecutive_failures_flag_shows_when_nonzero_silent_when_zero(self):
        """Design reviewer's exact requirement: a consecutive-failure count
        earns a flag when non-zero, silence otherwise."""
        src = _admin_module("resync.js")
        assert "function scheduledRowHtml" in src
        body = src[src.index("function scheduledRowHtml"):src.index("function scheduledRowHtml") + 2200]
        # Gated on failures > 0 -- nothing rendered unconditionally.
        assert "failures > 0" in body
        assert "consecutive failure" in body
        # last_run_at is surfaced too, for recency at a glance.
        assert "last_run_at" in body

    def test_expensive_steps_default_unticked(self):
        src = _admin_module("resync.js")
        assert "f.expensive ? '' : 'checked'" in src


class TestRepairPane:
    """SPEC-ADMIN-THE-FOUR-GAPS.md §1 -- Repair is per-repository correction,
    NOT drift reconciliation; it needs no design beyond §0's blast-radius
    rule, including naming what an action does NOT do."""

    def test_reads_the_real_repair_routes(self):
        api = _reapi_src()
        for fn in (
            "repairRename", "repairGithubUrl", "repairEnableCollection",
            "getRepairDrift", "getRepairMemberships",
            "repairRepointMembership", "repairDropMembership",
        ):
            assert f"export const {fn} = " in api
        assert "/api/admin/repair/repos/" in api

    def test_export_render_function_exists(self):
        src = _admin_module("repair.js")
        assert "export async function renderRepair(host)" in src

    def test_destructive_actions_are_confirmed(self):
        src = _admin_module("repair.js")
        assert src.count("inPageConfirm(") >= 3  # rename, github-url change, drop membership

    def test_names_what_it_does_not_do(self):
        """SPEC-ADMIN-THE-FOUR-GAPS.md §0's sharpest example -- classic's
        "this does not delete your files on disk" -- ported here for the
        repair actions whose names sound more destructive than they are."""
        src = _admin_module("repair.js")
        assert "does not touch GitHub" in src
        assert "does not remove the repo from RE" in src or "does not remove it from RE" in src

    def test_is_distinct_from_resync_not_folded_together(self):
        src = _admin_module("repair.js")
        assert "NOT Resync" in src
        resync_src = _admin_module("resync.js")
        assert "NOT Repair" in resync_src


class TestDiscoverySourcesPane:
    """SPEC-ADMIN-THE-FOUR-GAPS.md §3: run's own route is read-only (returns
    candidates, imports nothing), so this pane must preview before either of
    its two effectful actions — importing from a run, and applying a
    refresh — never fire-and-hope. Delete's confirmation must be worded from
    the registry's actual (no-FK) behaviour, not a guess."""

    def test_reads_and_writes_against_the_real_routes(self):
        api = _reapi_src()
        assert "export const listDiscoverySources = ()" in api
        assert "export const createDiscoverySource = (" in api
        assert "export const deleteDiscoverySource = (" in api
        assert "export const runDiscoverySource = (" in api
        assert "export const previewSourceRefresh = (" in api
        assert "export const applySourceRefresh = (" in api
        assert "export const searchDiscoveryRepos = (" in api
        assert "export const importDiscoveredRepos = (" in api

    def test_export_render_function_exists(self):
        src = _admin_module("discovery_sources.js")
        assert "export async function renderDiscoverySources(host)" in src

    def test_run_shows_candidates_before_any_import_is_possible(self):
        """The route itself never imports (checked against
        run_discovery_source in web/routes/discovery.py) -- the module's
        own header must say so, and importing must be a distinct,
        confirmed follow-up action, not something doRun triggers itself."""
        src = _admin_module("discovery_sources.js")
        assert "READ-ONLY" in src
        assert "no import" in src.lower()
        i = src.index("async function doRun(")
        run_body = src[i:src.index("\n}", i)]
        assert "importDiscoveredRepos" not in run_body

    def test_import_confirmation_names_the_count_and_destination(self):
        src = _admin_module("discovery_sources.js")
        i = src.index("async function doImportSelected(")
        body = src[i:src.index("\n}", i)]
        assert "inPageConfirm(" in body
        assert "picked.length" in body
        assert "dest" in body  # names the destination group (or "no group")

    def test_refresh_previews_before_it_applies(self):
        src = _admin_module("discovery_sources.js")
        i = src.index("async function doRefresh(")
        body = src[i:src.index("\n}", i)]
        assert "previewSourceRefresh(" in body
        assert "inPageConfirm(" in body
        confirm_pos = body.index("inPageConfirm(")
        apply_pos = body.index("applySourceRefresh(")
        assert confirm_pos < apply_pos

    def test_delete_confirmation_says_imported_repos_are_unaffected(self):
        """registry.py's discovery_sources table has no foreign key into
        projects -- deleting a source cannot touch anything already
        imported from it, and the confirmation must say so rather than
        reading as a generic destructive warning."""
        src = _admin_module("discovery_sources.js")
        i = src.index("async function doDelete(")
        body = src[i:src.index("\n}", i)]
        assert "inPageConfirm(" in body
        assert "does not affect any repositories already imported" in body

    def test_three_add_paths_are_present(self):
        src = _admin_module("discovery_sources.js")
        assert "'search'" in src and "searchFormHtml" in src
        assert "listFormHtml" in src
        assert "quickAddHtml" in src

    def test_save_github_source_is_not_treated_as_an_add_source_path(self):
        """_saveGithubSource never created a discovery source, and the override it set is gone (PI-084): the module
        must not name the removed route at all, only say what the old control was."""
        src = _admin_module("discovery_sources.js")
        assert "github-base-url" not in src
        assert "not create a discovery source" in src.lower()
