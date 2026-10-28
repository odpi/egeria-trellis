"""Nav grouping: three classes, declared in the data
(docs/design-notes/rulings/RULING-NAV-GROUPING.md).

The ruling answers a peer critique of the nine-item intent row: Understanding
leaves the run (project owner, 2026-09-18 — it will become a per-user
configured dashboard, not a milder or later stage of the corpus-level run),
and `STAGES` gets a `class` field (`frame` / `run` / `cross-cutting`) that the
renderer must derive grouping, numbering and separator style from — never a
second, independently maintained list of which ids go where. The ruling is
explicit that regrouping in the renderer while `STAGES` still called them
"eight ordered intents" would be the same class of bug as `unbuilt`
(DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md) and #130's dashed-styling bug:
read in three places, set in none.

Same source-text-assertion pattern as test_next_sidebar_group_collapse.py and
test_next_curate_section_nav.py -- there is no jsdom/browser harness here, so
live rendering (actual chevron/middot glyphs, numbering on screen) was
verified manually in a browser; see
NAV-GROUPING-IMPLEMENTED.md.
"""
from __future__ import annotations

import re
from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"

RUN_IDS = ["scouting", "discovery", "assessment", "analysis", "enrichment", "curate"]
CROSS_CUTTING_IDS = ["understanding", "automate"]


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _stages_block(app: str) -> str:
    start = app.index("const STAGES = [")
    end = app.index("];", start)
    return app[start:end]


class TestStaleCommentIsFixed:
    def test_eight_intents_comment_is_gone(self):
        app = _app()
        assert "The eight intents, in their canonical order" not in app

    def test_stages_doc_comment_names_the_three_classes(self):
        app = _app()
        block = app[: app.index("const STAGES = [")]
        # The comment immediately above STAGES should now describe three
        # classes rather than a flat ordered list.
        assert "`frame`" in block
        assert "`run`" in block
        assert "`cross-cutting`" in block


class TestClassIsDeclaredOnEveryStage:
    def test_investigation_is_frame(self):
        block = _stages_block(_app())
        assert re.search(
            r"id:\s*'investigation'.*?class:\s*'frame'", block, re.S
        ), "Investigation must declare class: 'frame'"

    def test_run_stages_declare_run_class_in_order(self):
        block = _stages_block(_app())
        # Each run id appears, in declared order, with class: 'run'.
        last_pos = -1
        for sid in RUN_IDS:
            m = re.search(rf"id:\s*'{sid}'.*?class:\s*'run'", block, re.S)
            assert m, f"{sid} must declare class: 'run'"
            assert m.start() > last_pos, f"{sid} out of order in STAGES"
            last_pos = m.start()

    def test_understanding_and_automate_are_cross_cutting(self):
        block = _stages_block(_app())
        for sid in CROSS_CUTTING_IDS:
            assert re.search(
                rf"id:\s*'{sid}'.*?class:\s*'cross-cutting'", block, re.S
            ), f"{sid} must declare class: 'cross-cutting'"

    def test_understanding_no_longer_declares_a_bare_ordered_position(self):
        # Regression guard for the specific defect this ruling fixes:
        # Understanding must not carry class: 'run'.
        block = _stages_block(_app())
        understanding_entry = re.search(r"id:\s*'understanding'.*?\}", block, re.S).group(0)
        assert "class: 'run'" not in understanding_entry


class TestRunOrderIsDerivedNotDuplicated:
    def test_run_order_map_is_computed_from_stages(self):
        app = _app()
        assert "RUN_ORDER" in app
        # It must be built by filtering STAGES, not a second literal id list.
        assert "STAGES.filter((s) => s.class === 'run')" in app

    def test_no_second_hardcoded_ordering_list_in_the_renderer(self):
        app = _app()
        nav_fn = app[app.index("function renderIntentNav()") : app.index("function renderIntentNav()") + 2500]
        # The renderer must group via STAGES.filter on `.class`, not a
        # literal array of the six/two ids.
        assert "s.class === 'frame'" in nav_fn or "class: 'frame'" not in nav_fn
        assert "'scouting', 'discovery', 'assessment'" not in nav_fn
        assert "['understanding', 'automate']" not in nav_fn


class TestNavRendersSeparatorsFromClass:
    def test_chevron_separator_joins_the_run(self):
        app = _app()
        assert "NAV_CHEVRON" in app
        assert "runItems" in app
        assert ".join(NAV_CHEVRON)" in app

    def test_middot_separator_joins_cross_cutting_and_precedes_worklists(self):
        app = _app()
        assert "NAV_MIDDOT" in app
        assert ".join(NAV_MIDDOT)" in app
        # A middot must precede the work-list control, same treatment as the
        # boundary between the run and the cross-cutting group.
        idx = app.index("id=\"worklist-nav\"")
        preceding = app[max(0, idx - 800) : idx]
        assert "NAV_MIDDOT" in preceding

    def test_numbering_is_only_applied_to_run_items(self):
        app = _app()
        nav_fn = app[app.index("function renderIntentNav()") : app.index("function renderIntentNav()") + 2500]
        assert "RUN_ORDER.get(s.id)" in nav_fn
        # Cross-cutting and frame mapping calls must not pass a number.
        cross_call = re.search(r"crossItems\s*\.map\(\(s\)\s*=>\s*navItemHtml\(s\)\)", nav_fn)
        frame_call = re.search(r"frameItems\s*\.map\(\(s\)\s*=>\s*navItemHtml\(s\)\)", nav_fn)
        assert cross_call, "cross-cutting items must render with no number"
        assert frame_call, "frame items must render with no number"


class TestFrameCheckIsDerivedFromClass:
    def test_loadpane_frame_gate_reads_class_not_a_separate_flag(self):
        app = _app()
        assert "stageDef?.frame" not in app
        assert "stageDef.frame" not in app
        assert "stageDef?.class === 'frame'" in app


class TestInvestigationHeaderScopeVisibility:
    """RULING-NAV-GROUPING.md §3: the ad-hoc/bound-to-a-Project distinction
    "deserves permanent visibility" in the header. This checks the
    visibility half that shipped -- not the larger "becomes an interactive
    scope control" idea, which NAV-GROUPING-IMPLEMENTED.md defers."""

    def test_header_has_a_scope_badge_element(self):
        html = (NEXT / "index.html").read_text(encoding="utf-8")
        assert 'id="investigation-scope"' in html

    def test_scope_badge_is_hidden_on_narrow_screens_like_the_name(self):
        html = (NEXT / "index.html").read_text(encoding="utf-8")
        assert "#investigation-name, #investigation-scope { display: none; }" in html
        # the Classic link stays visible: Classic-only features must stay reachable on a phone
        assert "#switch-ui { display: none; }" not in html

    def test_render_top_bar_sets_scope_text_from_egeria_binding(self):
        app = _app()
        fn = app[app.index("function renderTopBar()") : app.index("function renderTopBar()") + 4000]
        assert "investigation-scope" in fn
        # registry.py's ProjectRegistry.BINDING_LOCAL/BINDING_EGERIA: "egeria"
        # means "has [a Project], or is meant to" -- so this must key off
        # `egeria_binding` alone, not additionally require a populated
        # `egeria_project_guid` (a promotion not yet run looks identical to a
        # purely local investigation from the GUID alone).
        assert "egeria_binding" in fn
        assert "ad hoc" in fn
        assert "bound to Egeria Project" in fn


class TestDeferredStateDotIsNotBuiltHere:
    """RULING-NAV-GROUPING.md §4 proposes a per-tab measured/partial/never-run
    dot with two constraints, but no such dot exists anywhere in /next today
    -- it is a proposal being ruled on, not an existing feature this task
    regroups. Building it now would be feature-completeness work, not nav
    chrome, so it stays out; NAV-GROUPING-IMPLEMENTED.md records the
    deferral and the two constraints for whoever builds it."""

    def test_no_per_tab_state_dot_exists_in_the_nav_renderer(self):
        app = _app()
        nav_fn = app[app.index("function renderIntentNav()") : app.index("function renderIntentNav()") + 3000]
        assert "state-dot" not in nav_fn
        assert "measured / partial / never run" not in nav_fn
