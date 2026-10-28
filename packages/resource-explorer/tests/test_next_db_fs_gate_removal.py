"""DB/FS gate removal in /next (re/next-db-fs-gate-removal, 2026-09-22).

The project owner reversed the earlier "DB and FS stay out of /next" ruling
(docs/design-notes/exchange/COORDINATOR-BRIEF-MULTI-RESOURCE.md, 2026-09-20) — see
docs/Backlog.md's reversal entry. This slice:

  - relaxes `paneNeedsRepo()` so Survey no longer blocks db/filesystem (its
    backend, `/api/survey-definitions/{entity_type}/...`, is genuinely
    resource-type-generic);
  - replaces the old blanket "Repos only, in /next" message on By analysis,
    Disposition and the Questions checklist with `paneNeedsRepoBackend()`,
    which is honest about WHY those three stay repo-only today — a verified
    backend gap (repo-keyed routes/tables), not an unbuilt /next UI;
  - fixes two silent-wrong-data hardcodes: `automate.js`'s subscription
    filter and `worklist.js`'s analysis-menu lookup both ignored the actual
    selected/work-list resource type and always asked for 'repo'.

No browser verification with a signed-in session is asserted by these tests
-- see test_next_discovery_import_search.py's identical rationale. These
tests grep/slice the concatenated source, the established pattern for
/next JS modules without a browser.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _worklist_src():
    return (NEXT / "worklist.js").read_text(encoding="utf-8")


def _automate_src():
    return (NEXT / "stages" / "automate.js").read_text(encoding="utf-8")


class TestPaneNeedsRepoNoLongerBlanketBlocksNonRepo:
    """`paneNeedsRepo()` used to return the "Repos only, in /next" message
    for ANY non-repo resourceType, unconditionally -- including Survey,
    whose backend (getSurveyCandidates/runSurveyDefinition, both
    /api/survey-definitions/{entity_type}/...) never needed repo at all."""

    def test_pane_needs_repo_no_longer_gates_on_resource_type(self):
        # It may still use state.resourceType for the "pick a ..." wording,
        # but must no longer branch on `!== 'repo'` to block the pane.
        src = _app()
        start = src.index("function paneNeedsRepo() {")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "resourceType !== 'repo'" not in body
        assert "Repos only" not in body

    def test_survey_pane_calls_the_relaxed_helper_not_the_backend_gate(self):
        src = _app()
        start = src.index("async function loadSurveyPane() {")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "paneNeedsRepo()" in body
        assert "paneNeedsRepoBackend" not in body

    def test_survey_candidates_call_threads_the_real_resource_type(self):
        src = _app()
        assert "getSurveyCandidates(slug, { entityType: apiEntityType(state.resourceType)" in src
        assert "getSurveyCandidates(slug, { phase: state.stage })" not in src

    def test_run_survey_definition_call_threads_the_real_resource_type(self):
        src = _app()
        assert "runSurveyDefinition(slug, ref, { entityType: apiEntityType(state.resourceType) })" in src


class TestApiEntityTypeTranslatesTheUiShorthand:
    """Verified live against the running dev server 2026-09-22: the backend's
    canonical vocabulary is 'repo' | 'database' | 'filesystem'
    (schedules.py's _RESOURCE_LOOKUP, survey_definition_executor.py's
    _ADAPTERS) -- 'db', /next's own sidebar-chip shorthand, is not a valid
    entity_type anywhere server-side. GET /api/survey-definitions/db/.../candidates
    400s, and GET /api/analyses/db silently returns [] instead of erroring --
    exactly the silent-wrong-data shape this task exists to fix elsewhere.
    Every call site that sends state.resourceType to the server must
    translate it first."""

    def test_helper_exists_and_maps_db_to_database(self):
        src = _app()
        assert "export function apiEntityType(resourceType) {" in src
        assert "resourceType === 'db' ? 'database' : resourceType" in src

    def test_survey_pane_call_sites_translate_before_sending(self):
        src = _app()
        assert "entityType: apiEntityType(state.resourceType)" in src
        # Neither call site sends the raw, untranslated UI shorthand.
        assert "entityType: state.resourceType" not in src

    def test_automate_subscription_filter_translates_before_sending(self):
        src = _automate_src()
        assert "apiEntityType" in src
        assert "entityType: apiEntityType(state.resourceType)" in src
        assert "entityType: state.resourceType" not in src


class TestPaneNeedsRepoBackendIsGoneEntirely:
    """`paneNeedsRepoBackend()` existed for exactly one straggler:
    Disposition, whose backend (`registry.py`'s `repo_dispositions`/
    `repo_disposition_history`, keyed on `github_url` alone) genuinely had
    no database/filesystem equivalent, unlike By analysis/Questions (fixed
    in `re/next-generalize-byanalysis-disposition-questions` -- see
    TestByAnalysisAndQuestionsNoLongerGatedToRepo below), which were only
    ever a route-level gap over an already-generic backend.

    Disposition's gap was closed in `re/generalize-disposition`
    (docs/Backlog.md, "Disposition is NOT fixed here", 2026-09-22):
    `repo_dispositions`' PK widened to (entity_type, entity_slug), and the
    journal/records routes grew entity-generic siblings. With Disposition
    off the gate, nothing calls `paneNeedsRepoBackend()` any more, so the
    dead function was removed outright rather than left as an unused trap
    for a future gate to accidentally resurrect."""

    def test_backend_gate_helper_no_longer_exists(self):
        src = _app()
        assert "function paneNeedsRepoBackend(" not in src
        assert "paneNeedsRepoBackend(" not in src

    def test_old_undifferentiated_message_is_gone(self):
        src = _app()
        assert "Repos only, in /next" not in src

    def test_disposition_pane_uses_the_plain_gate_now(self):
        src = _app()
        start = src.index("async function loadDispositionPane() {")
        end = src.index("\n}", src.index("await renderRecords(slug", start))
        body = src[start:end]
        assert "const blocked = paneNeedsRepo();" in body
        assert "paneNeedsRepoBackend" not in body

    def test_disposition_pane_threads_apientitytype(self):
        src = _app()
        start = src.index("async function loadDispositionPane() {")
        end = src.index("\n}", src.index("await renderRecords(slug", start))
        body = src[start:end]
        assert "apiEntityType(state.resourceType)" in body

    def test_by_analysis_pane_no_longer_uses_the_backend_gate(self):
        src = _app()
        start = src.index("async function loadByAnalysisPane() {")
        end = src.index("\n}", start + 2000)
        body = src[start:end]
        assert "paneNeedsRepoBackend(" not in body
        assert "const blocked = paneNeedsRepo();" in body

    def test_questions_engine_no_longer_gates_on_backend_either(self):
        # The paneNeedsRepoBackend() call this dispatcher used to make is
        # gone -- database/filesystem's questions route now exists
        # (workflows.scouting.build_question_checklist). Only paneNeedsRepo()
        # (select a resource) remains.
        src = _app()
        assert "paneNeedsRepoBackend(stageDef?.label" not in src


class TestByAnalysisAndQuestionsNoLongerGatedToRepo:
    """docs/Backlog.md, "By analysis / scouting-questions were repo-only"
    (2026-09-22): both panes now reach a real database/filesystem backend
    instead of stopping at a UI gate."""

    def test_get_survey_dashboards_threads_entity_type(self):
        src = (Path(__file__).resolve().parents[1] / "resource_explorer"
               / "web" / "static" / "re-api.js").read_text(encoding="utf-8")
        start = src.index("function _surveyResultsPath(")
        end = src.index("};", src.index("export const getSurveyDashboards"))
        body = src[start:end]
        # no default: the kind is required (see test-harness/no-default-entity-type.test.mjs)
        assert "entityType = 'repo'" not in body
        assert "requireKind('getSurveyDashboards', entityType)" in body
        assert "/api/databases/" in body
        assert "/api/filesystems/" in body

    def test_get_questions_threads_entity_type(self):
        src = (Path(__file__).resolve().parents[1] / "resource_explorer"
               / "web" / "static" / "re-api.js").read_text(encoding="utf-8")
        assert "function _questionsPath(entityType" in src
        assert "/api/databases/" in src
        assert "/api/filesystems/" in src

    def test_app_js_passes_apientitytype_at_both_boundaries(self):
        src = _app()
        by_analysis_start = src.index("async function loadByAnalysisPane() {")
        by_analysis_end = src.index("\n}", by_analysis_start + 2000)
        assert "apiEntityType(state.resourceType)" in src[by_analysis_start:by_analysis_end]

        checklist_start = src.index("checklist = await getQuestions(slug, {")
        checklist_call = src[checklist_start:src.index("});", checklist_start)]
        assert "apiEntityType(state.resourceType)" in checklist_call

        context_call = src[src.index("const ctx = await getContext("):][:80]
        assert "apiEntityType(state.resourceType)" in context_call


class TestAutomateNoLongerHardcodesRepo:
    def test_subscription_filter_threads_state_resource_type(self):
        src = _automate_src()
        assert "state.resourceType" in src
        assert "entityType: 'repo'" not in src


class TestWorklistNoLongerHardcodesRepo:
    def test_analysis_menu_reads_the_work_lists_own_entity_type(self):
        src = _worklist_src()
        assert "listAnalyses(kindOf(wl)" in src
        assert "entity_type || 'repo'" not in src
        assert "listAnalyses('repo'" not in src
