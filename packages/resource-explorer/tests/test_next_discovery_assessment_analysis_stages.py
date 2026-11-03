"""Item 11 (PLAN-FINISH-REPOS.md): Discovery, Assessment and Analysis as
real, built stages in /next.

The mechanism these three stages need already existed for Scouting/
Enrichment/Curate before this item: the generic Questions-checklist engine
in `loadPane()` (app.js), parameterised by `phase`. This item's whole change
is (1) flipping `built: true` for the three, (2) adding one small honest
note about what Analysis's classic Sub-Resources feature does not have a
/next equivalent for, and (3) documentation-only staleness fixes to
Automate's comment about what a card-less Assessment/Analysis stage means.

That note (`renderAnalysisNote`) was later removed once Sub-Resources itself
was built for real -- see `test_next_analysis_subresources.py` and
`docs/design-notes/rulings/RULING-SUBRESOURCES-PLACEMENT.md`. `TestTheGenericEngine
NeededNoStageSpecificBranch` below was updated accordingly: Analysis now has
exactly as little stage-specific code in `loadPane()` as Discovery/
Assessment.

No browser verification of a signed-in session happened for this file — see
ITEM-11-DISCOVERY-ASSESSMENT-ANALYSIS-IMPLEMENTED.md for
what was and was not checked live. These tests grep/slice function bodies
out of the concatenated source, the established pattern for /next JS
modules without a browser -- see test_next_curate_pane.py,
test_next_understanding_pane.py and test_next_enrichment_fidelity.py's
`_app()` helper, reproduced identically here.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
CONFIGDATA = Path(__file__).resolve().parents[1] / "resource_explorer" / "configdata"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _stages_block():
    app = (NEXT / "app.js").read_text(encoding="utf-8")
    return app[app.index("const STAGES = ["):app.index("\n];\n", app.index("const STAGES = ["))]


class TestAllThreeStagesAreBuilt:
    def test_discovery_assessment_analysis_are_marked_built(self):
        block = _stages_block()
        for stage_id in ("discovery", "assessment", "analysis"):
            entry = block[block.index(f"id: '{stage_id}'"):]
            entry = entry[:entry.index("},")]
            assert "built: true" in entry, f"{stage_id} must be built: true"

    def test_investigation_stays_a_frame_not_a_built_stage(self):
        # Regression guard: a careless find/replace across STAGES turning
        # every entry into `built: true` would not fail any other test here,
        # since none of them assert the frame entry's ABSENCE of `built`.
        block = _stages_block()
        entry = block[block.index("id: 'investigation'"):]
        entry = entry[:entry.index("},")]
        assert "built:" not in entry
        # RULING-NAV-GROUPING.md replaced the separate `frame: true` flag
        # with a single `class` field STAGES declares once and the renderer
        # derives everything else from -- see NAV-GROUPING-IMPLEMENTED.md.
        assert "class: 'frame'" in entry


class TestTheGenericEngineNeededNoStageSpecificBranch:
    """Unlike Understanding/Automate (which bypass the Questions engine
    entirely) and Curate (which renders even with zero catalog rows),
    Discovery and Assessment must NOT gain a bypass branch -- the whole
    point of item 11 is that the generic path already reaches them."""

    def test_loadpane_has_no_special_case_branch_for_discovery_or_assessment(self):
        app = _app()
        body = app[app.index("async function loadPane()"):app.index("function wireHumanAnswers(")]
        assert "state.stage === 'discovery'" not in body
        assert "state.stage === 'assessment'" not in body

    def test_analysis_gets_no_stage_level_bypass(self):
        # Analysis used to get one honest note call here (renderAnalysisNote,
        # naming classic's un-ported Sub-Resources sub-tab). That feature is
        # now built for real -- see test_next_analysis_subresources.py --
        # and the note is gone; Analysis reaches the generic engine with
        # exactly as little stage-specific code as Discovery/Assessment.
        app = _app()
        body = app[app.index("async function loadPane()"):app.index("function wireHumanAnswers(")]
        assert "state.stage === 'analysis'" not in body
        assert "renderAnalysisNote" not in app

    def test_discovery_and_assessment_stage_modules_export_nothing(self):
        # They genuinely have no bespoke rendering -- an export here would
        # mean someone started building a bypass and this test would be the
        # signal to write a real one for it instead of leaving it silent.
        for name in ("discovery.js", "assessment.js"):
            src = (NEXT / "stages" / name).read_text(encoding="utf-8")
            assert src.strip().endswith("export {};")


class TestDispositionSubTabAlreadyWritesThroughDiscoveryPy:
    def test_next_setdisposition_posts_to_the_real_discovery_route(self):
        api = (NEXT.parent / "re-api.js").read_text(encoding="utf-8")
        assert "post('/api/discovery/disposition'" in api

    def test_the_route_exists_in_discovery_py(self):
        discovery_py = (NEXT.parent.parent.parent / "web" / "routes" / "discovery.py").read_text(encoding="utf-8")
        assert '@router.post("/disposition"' in discovery_py
        assert "async def set_repo_disposition(" in discovery_py


class TestOrgImportRepoSearchFromListCsvExportLiveAtTheSidebarNotTheStage:
    """Item 11's own scoping call: these discovery.py endpoints are
    corpus-level, not resource-scoped, so this item correctly did not build
    them inside the Discovery stage pane. NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md later built
    them for real at the sidebar's 'Find repos'
    action this item deferred to (`next/discovery-import.js`) -- this class
    now checks that placement held, not that the action still merely links
    out for every resource type (it no longer does, for repos)."""

    def test_find_repos_action_dispatches_by_resource_type(self):
        app = _app()
        assert "'find-repos': () => {" in app
        body = app[app.index("'find-repos': () => {"):]
        body = body[:body.index("\n    },")]
        # Repos, databases and file systems each have a real dialog now (a file
        # system's is the register form, Files step 1); no old-UI-link stub is left.
        assert "openFindReposDialog();" in body
        assert "openRegisterFileSystemDialog();" in body
        assert "oldUiHref()" not in body

    def test_discovery_stage_pane_itself_still_has_no_corpus_level_ui(self):
        # The thing this item's own scoping call was actually protecting:
        # search/import/export UI must not live inside stages/discovery.js,
        # regardless of where else it now lives.
        stage_src = (NEXT / "stages" / "discovery.js").read_text(encoding="utf-8")
        assert "export {};" in stage_src
        assert "searchDiscoveryRepos" not in stage_src

    def test_discovery_py_still_declares_the_four_endpoints(self):
        discovery_py = (NEXT.parent.parent.parent / "web" / "routes" / "discovery.py").read_text(encoding="utf-8")
        assert '@router.get("/inventory.csv")' in discovery_py
        assert '@router.post("/from-list"' in discovery_py
        assert '@router.post("/search"' in discovery_py
        assert "async def _expand_org(" in discovery_py


class TestCatalogHasRealRowsForAllThreeStages:
    """Pins the counts this item's whole argument rests on -- if these ever
    drop to zero, Discovery/Assessment/Analysis would be `built: true` over
    an empty checklist, the exact honesty violation the unbuilt marker
    exists to prevent."""

    def _question_stages(self):
        raw = yaml.safe_load((CONFIGDATA / "question_catalog.yaml").read_text(encoding="utf-8"))
        stages = []
        for e in raw.get("repo_questions") or []:
            stages.extend(part.strip().lower() for part in str(e.get("stage", "")).split("/"))
        return stages

    def test_discovery_has_catalogued_questions(self):
        assert self._question_stages().count("discovery") > 0

    def test_assessment_has_catalogued_questions(self):
        assert self._question_stages().count("assessment") > 0

    def test_analysis_has_catalogued_questions(self):
        assert self._question_stages().count("analysis") > 0

    def _analysis_intents(self):
        raw = yaml.safe_load((CONFIGDATA / "analysis_catalog.yaml").read_text(encoding="utf-8"))
        intents = []
        # analysis_catalog.yaml's top-level shape mirrors question_catalog's
        # repo_questions convention -- walk whatever list of dicts holds an
        # `intent` key rather than assuming one exact top-level key name.
        def walk(node):
            if isinstance(node, dict):
                if "intent" in node:
                    intents.append(str(node["intent"]))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(raw)
        return intents

    def test_discovery_assessment_analysis_all_have_catalog_entries(self):
        intents = self._analysis_intents()
        assert intents.count("discovery") > 0
        assert intents.count("assessment") > 0
        assert intents.count("analysis") > 0
