"""Sub-Resources in /next (re/next-analysis-subresources).

docs/design-notes/rulings/RULING-SUBRESOURCES-PLACEMENT.md (merged, PR #214) found
classic's Sub-Resources sub-tab (index.html's loadAnalysisSubResourcesView,
~380 lines) decomposes into two features with two different, already-
existing homes -- nothing joins /next's four-tab sub-strip:

  1. the candidate list / selection / catalogue UI is RUN CONFIGURATION for
     `sub_resource_survey` and belongs on **Survey & analyses**, attached to
     that analysis's own row (next to the Run button it was otherwise a dead
     end without) -- `mountSubResourcePanel` in `stages/analysis.js`, wired
     from `app.js`'s `analysisIndexRowHtml`/`renderAnalysesIndexSection`.
  2. its results are an ordinary analysis's output and belong on **By
     analysis**, exactly like any other analysis -- and in fact already
     render there via `loadByAnalysisPane()`'s generic per-board findings
     loop, since `sub_resource_survey` is registered in the `data_profile`
     Survey Result dashboard (repo_survey_definition_adapter.py) alongside
     data_file_profiling. No /next code was needed for this half.

Two defects the ruling named, both checked here:
  (a) classic's silent tab-fallback (index.html:2088,
      `if (tab === 'sub-resources' && !selectedProject) tab = 'catalog'`)
      must not be ported -- there is no fifth tab to fall back FROM, and
      Survey & analyses' own `paneNeedsRepo()` gate already blocks the whole
      pane (row and all) honestly when nothing is selected, rather than
      silently substituting a different view.
  (b) the temporary honest note (`renderAnalysisNote`, formerly mounted into
      `enrichment-form` from the Questions tab) is gone now that the real
      feature is built -- see test_next_discovery_assessment_analysis_stages.py's
      updated `TestTheGenericEngineNeededNoStageSpecificBranch` for the
      regression guard on its removal.

No browser verification with a signed-in session is asserted by the
static-source tests below -- see this branch's own report for what was
verified live instead. These tests grep/slice the concatenated source, the
established pattern for /next JS modules without a browser (test_next_
investigation_pane.py, test_next_discovery_import_search.py, test_next_db_
fs_gate_removal.py).
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
ROUTES = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "routes"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _analysis_src():
    return (NEXT / "stages" / "analysis.js").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


def _projects_py():
    return (ROUTES / "projects.py").read_text(encoding="utf-8")


class TestNoFifthTabIsAdded:
    """The ruling's central point: sub-resources specifically does not join
    SUB_TABS — see `test_no_sub_resources_tab_id_anywhere_in_sub_tabs`
    below, the actual guard. Slice 22 (2026-09-27) added a genuinely new,
    unrelated tab (`schema_inventory`, database-only — see `resourceTypes`
    filtering in `subTabsHtml()`); ENRICHMENT-E1-CONTEXT-TAB (2026-09-29)
    added a second, `context`, gated to the Enrichment stage via the new
    `stages` filter in `subTabsHtml()` (the same shape `resourceTypes`
    gates Schema Inventory to databases — the designer's reply §1: "Schema
    Inventory is a type-specific extra tab, and Context follows the same
    pattern"). So the exact count this test pins is "the current canonical
    set," not "four forever" or "five forever"; the ruling this class is
    named for was never about a hard cap on tab count — only that
    `sub-resources` specifically must never be one of them, which
    `test_no_sub_resources_tab_id_anywhere_in_sub_tabs` below still checks."""

    def test_sub_tabs_are_the_current_canonical_set(self):
        app = _app()
        start = app.index("const SUB_TABS = [")
        end = app.index("];", start)
        block = app[start:end]
        ids = ["context", "questions", "survey", "by_analysis", "disposition", "schema_inventory", "value_profile",
               "table_preview"]
        for i in ids:
            assert f"id: '{i}'" in block
        assert block.count("id: '") == len(ids)

    def test_no_sub_resources_tab_id_anywhere_in_sub_tabs(self):
        app = _app()
        start = app.index("const SUB_TABS = [")
        end = app.index("];", start)
        assert "sub-resources" not in app[start:end]
        assert "sub_resources" not in app[start:end]


class TestSchemaInventoryTabGatesOnTheRealResourceTypeValue:
    """Found live, `laz_local_adventureworks`, 2026-09-27: Dan's Slice 22
    usability gate failed before task 1 -- the Schema Inventory tab never
    appeared for ANY database. `resourceTypes: ['database']` used the
    display word, but `state.resourceType` is always the short form 'db'
    (see the module comment above `$` -- "'repo' | 'db' | 'filesystem'" --
    and every other comparison site in this file: `apiEntityType`,
    `nonRepoLabel`, the find-title branch, the empty-selection prompt all
    compare to 'db'). `subTabsHtml()`'s own `.includes(state.resourceType)`
    filter silently never matched, so the tab was dropped for every
    database. No existing test asserted the tab's gating at all -- these
    are new, not fixes to a test that used the wrong fixture value."""

    def test_sub_tabs_resourcetypes_uses_the_short_form(self):
        app = _app()
        start = app.index("const SUB_TABS = [")
        end = app.index("];", start)
        block = app[start:end]
        row = block[block.index("id: 'schema_inventory'"):]
        row = row[:row.index("},") + 1]
        assert "resourceTypes: ['db']" in row
        assert "'database'" not in row

    def test_pane_guard_compares_against_the_short_form(self):
        app = _app()
        start = app.index("async function loadSchemaInventoryPane()")
        end = app.index("\n}\n", start)
        body = app[start:end]
        assert "state.resourceType !== 'db'" in body
        assert "state.resourceType !== 'database'" not in body


class TestRunConfigurationAttachedToTheAnalysisRow:
    """The candidate-selection/catalogue UI is attached to sub_resource_
    survey's own row on Survey & analyses, not a separate view."""

    def test_analysis_index_row_html_special_cases_only_sub_resource_survey(self):
        app = _app()
        start = app.index("function analysisIndexRowHtml(row) {")
        end = app.index("\n}", start)
        body = app[start:end]
        assert "SUBRES_ANALYSIS_ID" in body
        assert "data-subres-toggle" in body
        assert "id=\"subres-panel\"" in body

    def test_subres_analysis_id_constant_names_the_real_catalog_id(self):
        app = _app()
        assert "const SUBRES_ANALYSIS_ID = 'sub_resource_survey';" in app

    def test_the_run_button_is_untouched_and_still_generic(self):
        # The existing generic Run/re-run button must still work for
        # sub_resource_survey exactly like any other analysis -- the toggle
        # is an ADDITION beside it, not a replacement.
        app = _app()
        start = app.index("function analysisIndexRowHtml(row) {")
        end = app.index("\n}", start)
        body = app[start:end]
        assert 'data-analysis-run="${esc(row.analysis_id)}"' in body

    def test_render_analyses_index_section_wires_the_toggle_to_the_real_mount(self):
        app = _app()
        start = app.index("async function renderAnalysesIndexSection(")
        end = app.index("\n}", app.index("data-analysis-run", start))
        body = app[start:end]
        assert "data-subres-toggle" in body
        assert "mountSubResourcePanel(slug, subresPanel)" in body

    def test_app_js_imports_the_mount_function_not_the_old_note(self):
        app = _app()
        assert "import { mountSubResourcePanel } from '/static/next/stages/analysis.js';" in app
        assert "renderAnalysisNote" not in app

    def test_the_run_button_passes_entity_type_not_just_slug_and_id(self):
        """Live-reproduced 2026-09-25: `runAnalysis(slug, aid)` -- missing
        the third `entityType` arg -- defaults to 'repo' (re-api.js), so a
        database's "run"/"re-run" button POSTed to `/api/projects/{slug}/
        analyses/{aid}/run` (repo's route) instead of `/api/databases/{slug}
        /analyses/{aid}/run`, 404ing. `Queueing…` reverted to `run →` the
        instant the request failed, with only a hover tooltip explaining
        why -- reported as "the button works but doesn't do anything".
        `rerun()` (the Questions-checklist run button, same file) already
        passes `apiEntityType(state.resourceType)` correctly; this pins the
        same call shape for the Analyses-index button too."""
        app = _app()
        start = app.index("async function renderAnalysesIndexSection(")
        end = app.index("\n}", app.index("data-analysis-run", start))
        body = app[start:end]
        assert "runAnalysis(slug, aid, apiEntityType(state.resourceType), { force })" in body


class TestSubResourcePanelPortsClassicsRealBehaviour:
    """Not a redesign -- the same D2/D3/D4/D5/D6 funnel stages classic's
    loadAnalysisSubResourcesView implemented: findings review with select/
    filter/sort, cataloguing (local + optional Egeria publish), and scoped
    analysis dispatch against already-cataloged sub-resources."""

    def test_mount_function_is_exported(self):
        src = _analysis_src()
        assert "export async function mountSubResourcePanel(slug, panel)" in src

    def test_reads_findings_cataloged_rows_and_the_analysis_catalog(self):
        src = _analysis_src()
        assert "getAnalysisResults(slug, 'sub_resource_survey')" in src
        assert "listSubResources(slug)" in src
        assert "listAnalyses('repo')" in src

    def test_selection_filter_and_sort_affordances_are_present(self):
        """Brief 2a: the checkbox and "select all worthy" are gone; each row has the two-part selector under
        "Publish to Egeria?" and the toolbar acts on the filtered rows."""
        src = _analysis_src()
        assert "data-subres-filter" in src
        assert "data-subres-sort" in src
        assert "data-subres-pick" not in src and "data-subres-select-all" not in src
        assert "SCOPE_HEAD" in src and "toolbarHtml(s.scope, rows)" in src

    def test_publishing_posts_nothing_the_server_reads_the_record(self):
        src = _analysis_src()
        assert "await catalogSubResources(slug)" in src
        assert "data-subres-publish" not in src, "the sandbox flag is retired"
        assert "publishToEgeria" not in src

    def test_published_items_are_shown_in_the_egeria_lane_and_stay_choosable(self):
        src = _analysis_src()
        assert "egeriaCellHtml(r)" in src
        assert "Already cataloged" not in src

    def test_scoped_analysis_dispatch_uses_the_shape_gate(self):
        src = _analysis_src()
        assert "isShapeCompatible(a.target_shape, row.kind)" in src
        assert "runScopedAnalysis(slug, analysisId, locator)" in src
        assert "getScopedAnalysisResults(slug, analysisId, locator)" in src

    def test_a_reload_after_writes_invalidates_the_cached_panel_state(self):
        # Cataloging changes what "already catalogued" means, so the panel
        # must re-fetch rather than keep showing pre-write selection state.
        src = _analysis_src()
        assert "await s.scope.load()" in src


class TestNoSilentTabFallbackWasPorted:
    """Defect (a): classic's `if (tab === 'sub-resources' && !selectedProject)
    tab = 'catalog'` must not exist anywhere in /next -- there is no fifth
    tab to silently fall back from in the first place."""

    def test_no_silent_substitution_pattern_anywhere_in_next(self):
        app = _app()
        analysis_src = _analysis_src()
        assert "tab = 'catalog'" not in app
        assert "tab = 'catalog'" not in analysis_src

    def test_survey_pane_gate_is_the_single_honest_block_not_a_silent_switch(self):
        # paneNeedsRepo() is called once, up front, and either blocks the
        # whole pane (row and toggle both) or lets everything through --
        # never a partial, unexplained substitution of one view for another.
        app = _app()
        start = app.index("async function loadSurveyPane() {")
        end = app.index("\n}", start + 2000)
        body = app[start:end]
        assert "paneNeedsRepo()" in body


class TestTheOldPlaceholderNoteIsFullyRetired:
    """Defect (b): the temporary note pointed at the wrong tab (Questions)
    and is now simply gone, because the feature it deferred is built."""

    def test_render_analysis_note_no_longer_exists_anywhere(self):
        assert "renderAnalysisNote" not in _app()
        assert "renderAnalysisNote" not in _analysis_src()

    def test_load_pane_has_no_analysis_specific_branch_left(self):
        app = _app()
        body = app[app.index("async function loadPane()"):app.index("function wireHumanAnswers(")]
        assert "state.stage === 'analysis'" not in body


class TestReApiHasTheSubResourceWrappers:
    def test_wrappers_exist_and_hit_the_real_routes(self):
        src = _reapi_src()
        assert "export const listSubResources = (slug) =>" in src
        assert "/sub-resources" in src
        assert "export const catalogSubResources = " in src
        assert "/sub-resources/catalog" in src
        assert "export const runScopedAnalysis = " in src
        assert "export const getScopedAnalysisResults = " in src
        assert "export function isShapeCompatible(" in src

    def test_shape_compatibility_mirrors_the_python_reference(self):
        src = _reapi_src()
        start = src.index("export function isShapeCompatible(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "'corpus'" in body
        assert "'single_container'" in body
        assert "'single_leaf'" in body

    def test_backend_routes_this_module_calls_actually_exist(self):
        py = _projects_py()
        assert '@router.get("/{slug}/sub-resources"' in py
        assert '@router.post("/{slug}/sub-resources/catalog"' in py
        assert '@router.post("/{slug}/sub-resources/analyses/{analysis_id}/run"' in py
        assert '@router.get("/{slug}/sub-resources/analyses/{analysis_id}/results")' in py
        assert '@router.get("/{slug}/analyses/{analysis_id}/results")' in py


class TestResultsAlreadyReachByAnalysisGenerically:
    """The second half of the ruling needed no /next code: sub_resource_
    survey's findings-shaped results already flow through loadByAnalysisPane's
    generic per-board findings loop, because the analysis is registered in
    the data_profile Survey Result dashboard alongside data_file_profiling."""

    def test_by_analysis_pane_handles_any_findings_array_generically(self):
        # BY-ANALYSIS-PROGRESSIVE-AND-GRAPH (2026-09-28): the per-board
        # findings loop moved out of loadByAnalysisPane's own body and into
        # boardFindingsHtml(board), one card body renderer called for every
        # board generically -- same behavior (still no analysis-id special
        # case), different function.
        app = _app()
        start = app.index("function boardFindingsHtml(board)")
        end = app.index("\n}", start)
        body = app[start:end]
        assert "Array.isArray(res.findings)" in body
        # No analysis-id special case -- the loop is generic across every
        # analysis a dashboard names, sub_resource_survey included.
        assert "sub_resource_survey" not in body

    def test_sub_resource_survey_is_registered_in_a_dashboard(self):
        adapter = (ROUTES.parent.parent / "surveyors" / "repo_survey_definition_adapter.py").read_text(
            encoding="utf-8")
        start = adapter.index('"data_profile": SurveyResultDashboard(')
        end = adapter.index("),", start)
        assert "sub_resource_survey" in adapter[start:end]

    def test_sub_resource_survey_carries_the_analysis_intent(self):
        catalog = (ROUTES.parent.parent / "configdata" / "analysis_catalog.yaml").read_text(encoding="utf-8")
        start = catalog.index("- id: sub_resource_survey")
        end = catalog.index("\n\n", start)
        assert "intent: analysis" in catalog[start:end]
