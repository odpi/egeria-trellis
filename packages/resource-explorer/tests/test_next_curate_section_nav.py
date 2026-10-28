"""Curate's sections: stable ids, collapsible, default collapsed, and (Brief S1) each on exactly one TAB.

Originally the page-level section nav + collapsible sections (SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md follow-on).
The jump line (the sticky list of six links) is RETIRED: a repository's Curate is three tabs and each tab holds two
or three sections, which need no contents list. What stays pinned: the six section ids, one `<details>` wrapper per
section, collapsed by default, the scroll anchor of a jump, and where each section lives (TAB_OF_SECTION).

Same static-source-holds-the-design-rules approach as test_next_curate_pane.py: a Python suite cannot run the
browser, but it can hold the shipped source to the rules these additions state as requirements. The behaviour is
driven for real in frontend-build/test-harness/curate-tabs-repo.test.mjs.
"""
from __future__ import annotations

import re
from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"

SECTION_IDS = [
    "curate-sec-what-it-is",
    "curate-sec-what-holds",
    "curate-sec-made-of",
    "curate-sec-blueprints",
    "curate-sec-relates",
    "curate-sec-writes",
]

#: Where each section lives once the tabs are on (stage-tabs.js TAB_OF_SECTION).
TAB_OF = {
    "curate-sec-what-it-is": "curate_structure",
    "curate-sec-made-of": "curate_structure",
    "curate-sec-blueprints": "curate_structure",
    "curate-sec-relates": "curate_structure",
    "curate-sec-what-holds": "curate_contents",
    "curate-sec-writes": "curate_contents",
}


def _curate_src():
    return (NEXT / "stages" / "curate.js").read_text(encoding="utf-8")


def _tabs_src():
    return (NEXT / "stages" / "stage-tabs.js").read_text(encoding="utf-8")


def _draw_body(src):
    return src[src.index("const draw = () => {"):src.index("bindCurateSectionNav(host);")]


class TestSixSectionsExistWithStableIds:
    """One id per section, matching the six sections `renderCurate` renders."""

    def test_all_six_section_ids_are_declared(self):
        src = _curate_src()
        for sid in SECTION_IDS:
            assert sid in src, f"missing section id {sid!r}"

    def test_every_call_site_uses_the_shared_wrapper_for_all_six_sections(self):
        body = _draw_body(_curate_src())
        for sid in SECTION_IDS:
            assert f"curateSectionHtml('{sid}'" in body

    def test_blueprints_is_its_own_section_separate_from_made_of(self):
        # Item 3 nested the blueprint list inside the "made of" column; it is its own top-level, independently
        # collapsible/anchored section. Look at the actual render call site (in `draw`).
        draw_body = _draw_body(_curate_src())
        made_of_call = draw_body[draw_body.index("'curate-sec-made-of'"):draw_body.index("'curate-sec-blueprints'")]
        assert "blueprint-list" not in made_of_call
        assert "component-tree" in made_of_call


class TestTheJumpLineIsRetired:
    """Brief S1 / plan section 3.5: the six-link jump line goes; each tab holds two or three sections."""

    def test_no_nav_markup_or_handlers_remain(self):
        src = _curate_src()
        assert "curateSectionNavHtml" not in src
        assert "data-curate-nav" not in src
        assert "const CURATE_SECTIONS = [" not in src

    def test_the_section_to_tab_table_covers_all_six(self):
        tabs = _tabs_src()
        body = tabs[tabs.index("export const TAB_OF_SECTION = {"):tabs.index("};", tabs.index("export const TAB_OF_SECTION = {"))]
        found = dict(re.findall(r"'(curate-sec-[a-z-]+)': '(curate_[a-z]+)'", body))
        assert found == TAB_OF


class TestEachSectionIsDrawnIntoItsOwnTab:
    """`draw()` keeps ONE template and ONE state read but writes each section into its tab's host."""

    def test_contents_sections_go_to_the_contents_host_and_structure_ones_to_the_structure_host(self):
        body = _draw_body(_curate_src())
        contents = body[body.index("hosts.contents.innerHTML"):body.index("hosts.structure.innerHTML")]
        structure = body[body.index("hosts.structure.innerHTML"):]
        for sid, tab in TAB_OF.items():
            host_src = contents if tab == "curate_contents" else structure
            other = structure if tab == "curate_contents" else contents
            assert f"curateSectionHtml('{sid}'" in host_src, f"{sid} belongs on {tab}"
            assert f"curateSectionHtml('{sid}'" not in other, f"{sid} must not also be drawn on the other tab"

    def test_the_three_panels_are_all_in_the_document_and_the_inactive_ones_hidden(self):
        src = _curate_src()
        frame = src[src.index("function tabbedFrameHtml("):src.index("export async function renderCurate(")]
        for panel in ("describe", "contents", "structure"):
            assert f'data-curate-tab-panel="{panel}"' in frame
        assert "hidden" in frame

    def test_a_section_under_a_hidden_tab_does_not_load(self):
        body = _curate_src()
        load = body[body.index("function loadSectionData("):]
        load = load[:load.index("const work = [];")]
        assert "sectionTabShows(id)" in load and "return null" in load


class TestSectionsAreCollapsibleDetailsDefaultCollapsed:
    """Matching /next's existing disclosure idiom (app.js's other <details> usage): <details>/<summary>, summary is
    the section's own heading text, default COLLAPSED (owner, 2026-10-08)."""

    def test_the_section_wrapper_is_a_details_element(self):
        src = _curate_src()
        body = src[src.index("function curateSectionHtml("):src.index("function paneError(")]
        # Owner, 2026-10-08: every section starts collapsed; a person's opening is remembered per viewer.
        assert "<details id=\"${id}\" ${sectionOpen(id) ? 'open' : ''}" in body
        assert "<details id=\"${id}\" open" not in body
        assert "<summary" in body

    def test_every_section_has_a_scroll_margin(self):
        src = _curate_src()
        body = src[src.index("function curateSectionHtml("):src.index("function paneError(")]
        assert 'style="scroll-margin-top:4rem"' in body


class TestAnchorScrollMatchesClassicsConvention:
    """A jump scrolls to the section's START (a section can be thousands of px tall, so centring lands mid-list),
    after opening it and switching to its tab."""

    def test_the_jump_anchors_the_section_start_smoothly(self):
        # Owner, 2026-10-08: block 'center' on a very tall section (hundreds of cluster rows) landed mid-list.
        src = _curate_src()
        body = src[src.index("export function jumpToCurateSection("):src.index("function curateSectionHtml(")]
        assert "scrollIntoView({ behavior: 'smooth', block: 'start' })" in body
        assert "block: 'center'" not in body

    def test_clicking_a_collapsed_section_opens_it_before_scrolling(self):
        src = _curate_src()
        body = src[src.index("export function jumpToCurateSection("):src.index("function curateSectionHtml(")]
        assert "el.open = true" in body
        assert body.index("el.open = true") < body.index("el.scrollIntoView(")

    def test_a_jump_into_another_tab_switches_first(self):
        src = _curate_src()
        body = src[src.index("export function jumpToCurateSection("):src.index("function curateSectionHtml(")]
        assert "tabOfSection(id)" in body
        assert body.index("openCurate({ tab, section: id })") < body.index("el.scrollIntoView(")


class TestComponentTreeInternalsUntouched:
    """A concurrent, separately-assigned piece of work (re/curate-tree-scope-groups) builds scope-hierarchy
    grouping against `renderComponentTree`'s row logic. Wrapping a section must only touch its OUTER container and
    not what that function returns internally."""

    def test_render_component_tree_body_has_no_new_section_wrapper_markup(self):
        src = _curate_src()
        body = src[src.index("async function renderComponentTree("):src.index("/* ── Blueprints")]
        assert "curateSectionHtml" not in body
        assert "data-curate-nav" not in body
        assert "curate-sec-" not in body
