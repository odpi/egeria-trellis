"""SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md — item 3's finish: multi-select on
the branch tree, and the blueprint list. Same static-source-holds-the-design-
rules approach as test_next_component_review.py/test_next_curate_pane.py: a
Python suite cannot run the browser, but it can hold the shipped source to
the rules the spec states as hard requirements.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


class TestSelectionOnTheTree:
    """§1: no select-mode toggle (the tree's rows are already a work queue);
    each row carries its own checkbox; select-all-shown and select-all-
    matching are two different acts; the footer states counts; the
    confirmation states the TOTAL scope count before acting."""

    def test_no_select_mode_toggle_for_the_tree(self):
        app = _app()
        body = app[app.index("function selectionBarHtml("):app.index("async function renderComponentTree(")]
        assert "selectMode" not in body

    def test_every_branch_row_carries_a_checkbox(self):
        app = _app()
        body = app[app.index("function branchRowHtml("):app.index("function leafRowHtml(")]
        assert 'data-branch-select="${esc(b.path)}"' in body

    def test_select_all_shown_and_select_all_matching_are_distinct(self):
        app = _app()
        body = app[app.index("function selectionBarHtml("):app.index("async function renderComponentTree(")]
        assert "data-select-all-shown" in body
        assert "data-select-all-matching" in body and "select all <span class=\"tnum\">${total}</span> branches" in body

    def test_the_footer_states_shown_and_selected(self):
        app = _app()
        body = app[app.index("function selectionBarHtml("):app.index("async function renderComponentTree(")]
        assert "shown selected" in body

    def test_the_confirmation_states_the_total_scope_count_before_acting(self):
        app = _app()
        body = app[app.index("function recordVerdicts("):]
        assert "scopes.length} branches selected" in body and "scope${count === 1 ? '' : 's'} total" in body

    def test_bulk_reject_needs_no_confirmation_but_bulk_accept_does(self):
        app = _app()
        body = app[app.index("host.querySelector('[data-selection-verdict=\"accepted\"]')"):
                    app.index("host.querySelectorAll('[data-branch-open]')")]
        assert "'accepted'" in body and "'rejected'" in body
        # accept always goes through recordVerdicts with a real count (>1
        # branches means count is a sum of components, so the accept dialog
        # gate `count <= 1` in recordVerdicts is what decides confirmation,
        # not a separate code path here).
        assert "recordVerdicts(slug, paths, 'accepted'" in body
        assert "recordVerdicts(slug, [...selected], 'rejected'" in body

    def test_selection_is_cleared_after_a_bulk_action_completes(self):
        app = _app()
        body = app[app.index("host.querySelector('[data-selection-verdict=\"accepted\"]')"):
                    app.index("host.querySelectorAll('[data-branch-open]')")]
        assert "selected.clear()" in body

    def test_selection_does_not_leak_across_resources(self):
        app = _app()
        body = app[app.index("function curateSelectionSet("):app.index("function selectionBarHtml(")]
        assert "state.curateSelection.slug !== slug" in body


class TestTheBlueprintList:
    """§2/§3: the 12-clusters coverage sentence opens onto a real list; a
    blueprint verdict is reading-scoped and the screen says so; switching
    readings replaces the list."""

    def test_a_row_shows_why_it_is_cohesive_and_a_member_count_that_opens(self):
        app = _app()
        body = app[app.index("function blueprintRowHtml("):app.index("/** The rail: one cluster")]
        assert "bp.signal, bp.carrier" in body
        assert "data-blueprint-members=" in body and "members accepted" in body

    def test_accept_and_reject_are_both_offered_per_row(self):
        app = _app()
        # accept lives in blueprintWriteHtml (brief section 4: offered only with an accepted node)
        body = app[app.index("function blueprintWriteHtml("):app.index("/** The rail: one cluster")]
        assert 'data-blueprint-verdict="accepted"' in body and 'data-blueprint-verdict="rejected"' in body

    def test_the_screen_says_a_blueprint_verdict_is_reading_scoped(self):
        app = _app()
        body = app[app.index("async function renderBlueprintList("):app.index("/** Accepting a cluster is a decision")]
        assert "applies in this reading only" in body
        assert "switching readings shows a different set, not the same set re-judged" in body

    def test_switching_readings_replaces_rather_than_diffs(self):
        app = _app()
        body = app[app.index("async function renderBlueprintList("):app.index("/** Accepting a cluster is a decision")]
        # A fresh innerHTML assignment on every call, no patch/diff step, and
        # a reading switch calls this same function again rather than
        # mutating a subset of the existing rows.
        assert "host.innerHTML = `" in body
        assert "rk.reading = b.dataset.blueprintReading;\n    renderBlueprintList(slug);" in body

    def test_the_foot_names_the_current_and_other_readings(self):
        app = _app()
        body = app[app.index("async function renderBlueprintList("):app.index("/** Accepting a cluster is a decision")]
        assert "clusters shown" in body and "· all in the" in body
        assert "the ${esc(o.p)} reading has" in body

    def test_accepting_names_the_pinned_type_and_does_not_invent_one_for_components(self):
        app = _app()
        body = app[app.index("function recordBlueprintVerdict("):]
        assert "SolutionBlueprint" in body
        assert "the type is pinned" in body
        # The per-component caveat stays untouched elsewhere in this file
        # (the branch-accept dialog) -- this dialog must not repeat or
        # contradict it by pinning a component type here too.
        assert "DeployedSoftwareComponent" not in body


class TestMembershipHonesty:
    """§4, the hard requirement: an accepted blueprint must say its members
    are not yet linked and name how many stand apart -- silence would read
    as zero members, which is false."""

    def test_zero_materialized_members_is_stated_not_silent(self):
        app = _app()
        body = app[app.index("function membershipHonestyLine("):app.index("function blueprintRowHtml(")]
        assert "if (!total)" in body
        assert "nothing to link" in body

    def test_a_nonzero_count_names_the_number_and_opens_it(self):
        app = _app()
        body = app[app.index("function membershipHonestyLine("):app.index("function blueprintRowHtml(")]
        assert "data-blueprint-standapart=" in body
        assert "stand apart" in body

    def test_the_line_only_renders_for_an_accepted_and_materialized_blueprint(self):
        app = _app()
        body = app[app.index("function blueprintRowHtml("):app.index("/** The rail: one cluster")]
        assert "membershipHonestyLine(bp)" in body
        assert "bp.materialized\n" in body or "bp.materialized\n          " in body or "(bp.materialized" in body

    def test_an_accepted_but_unmaterialized_blueprint_says_so_honestly(self):
        app = _app()
        body = app[app.index("function blueprintRowHtml("):app.index("/** The rail: one cluster")]
        assert "not in Egeria yet" in body


class TestDeferredStylingComesFromOneHelper:
    """PR #130's rule, restated by the spec §5: the dashed/deferred styling
    must come from the flag that gates the behaviour, via one shared helper,
    not written inline per-site."""

    def test_the_helper_exists_and_is_exported(self):
        app = _app()
        assert "export function deferredAttrs(isBuilt" in app

    def test_the_stage_nav_and_subtab_rail_use_it_not_an_inline_copy(self):
        app = _app()
        # RULING-NAV-GROUPING.md's rewrite of the stage nav factored each
        # item's markup out of renderIntentNav() into navItemHtml() (so
        # grouping/numbering/separators can be derived from STAGES.class
        # without duplicating the per-item rendering three times) -- the
        # dashed-styling call now lives there rather than inline in
        # renderIntentNav's own body. Scan from navItemHtml() (which
        # immediately precedes renderIntentNav) through the work-list
        # section, so this still covers "the stage nav" as a whole.
        nav_body = app[app.index("function navItemHtml("):app.index("/** The list OF work lists")]
        assert "deferredAttrs(false" in nav_body
        subtab_body = app[app.index("function subTabsHtml("):app.index("export function bindSubTabs(")]
        # Every stage with a sub-tab strip is built, so the strip has no dashed/deferred tab branch any more.
        assert "deferredAttrs(" not in subtab_body
        assert "data-deferred" not in subtab_body
        # Neither site should carry its own independent dashed-style literal
        # any more -- that duplication is exactly what #130 had to fix twice.
        assert "border-bottom:1px dashed currentColor" not in nav_body
        assert "border-bottom:1px dashed currentColor" not in subtab_body

    def test_curate_is_flipped_to_built_now_that_this_item_ships(self):
        app = _app()
        stages_body = app[app.index("const STAGES = ["):app.index("];", app.index("const STAGES = ["))]
        # RULING-NAV-GROUPING.md added `class: 'run'` to Curate's entry
        # alongside `built: true` -- the flip this test guards is `built`,
        # so match on that rather than the now-stale exact-literal line.
        assert "id: 'curate'" in stages_body
        curate_entry = stages_body[stages_body.index("id: 'curate'"):]
        curate_entry = curate_entry[:curate_entry.index("},")]
        assert "built: true" in curate_entry
