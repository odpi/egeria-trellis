"""Sidebar groups in /next were static headers with no toggle and no
persisted state (SPEC-PARITY-INVENTORY-AND-GROUPS.md §3 -- "partly
present": /next groups repos by group_slug and renders a count, but the
header has no collapse). Classic's `index.html` already has three
behaviours here -- persisted collapse (`_COLLAPSED_GROUPS_KEY`),
group-level select respecting collapse (`_toggleGroupSelected`), and a
filter force-expanding every group (":5748-5749") so a match inside a
collapsed group is never silently hidden. This ports all three to /next.

These are source-text assertions, matching this repo's established
pattern for pure front-end JS behaviour (see test_next_component_review.py,
test_next_rail_states.py) -- there is no jsdom/browser harness here, so
live behaviour (persistence across reload, actual click handling) was
verified manually in a browser instead; see
SIDEBAR-GROUP-COLLAPSE-IMPLEMENTED.md."""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


class TestPersistedCollapse:
    def test_own_localstorage_key_distinct_from_classic(self):
        app = _app()
        # Classic's key is 're_collapsed_sidebar_groups' (index.html) -- /next
        # must not reuse it verbatim, since the two surfaces would otherwise
        # fight over one entry with different shapes (spec's own words).
        assert "COLLAPSED_GROUPS_KEY = 're_next_collapsed_sidebar_groups'" in app
        assert "'re_collapsed_sidebar_groups'" not in app

    def test_collapse_state_is_read_from_and_written_to_localstorage(self):
        app = _app()
        assert "function collapsedGroups()" in app
        assert "localStorage.getItem(COLLAPSED_GROUPS_KEY)" in app
        body = app[app.index("function toggleGroupCollapsed("):app.index("function toggleGroupCollapsed(") + 700]
        assert "localStorage.setItem(COLLAPSED_GROUPS_KEY" in body
        assert "renderSidebar();" in body

    def test_group_header_is_a_real_toggle_not_a_static_label(self):
        app = _app()
        # /next's established disclosure idiom elsewhere in this file is
        # <details>/<summary> (openMembers' facet groups, survey history,
        # analyses index) -- the group header follows the same pattern
        # rather than inventing a caret+button widget from scratch.
        assert '<details class="mb-s4" data-group="${esc(g)}" ${collapsed ? \'\' : \'open\'}>' in app
        assert "toggleGroupCollapsed(summary.closest('details').dataset.group)" in app

    def test_toggle_is_not_wired_off_the_native_toggle_event(self):
        # A native 'toggle' listener can fire from the browser's own
        # initial-state handling of the `open` attribute (set fresh on
        # every render), which would silently overwrite a reader's saved
        # preference with whatever the force-expand-on-filter render
        # happened to show. Driving persistence from summary's click
        # instead means it only ever runs on a real user gesture.
        app = _app()
        bind_sidebar = app[app.index("function bindSidebar("):app.index("/* ── Sidebar write paths")]
        assert "addEventListener('toggle'" not in bind_sidebar
        assert "summary.addEventListener('click'" in bind_sidebar


class TestGroupSelectRespectsCollapse:
    def test_group_checkbox_selects_what_the_count_counted(self):
        app = _app()
        assert "function toggleGroupSelected(groupSlug)" in app
        body = app[app.index("function toggleGroupSelected("):app.index("function toggleGroupSelected(") + 700]
        # Members come from visibleProjects(), the same list the header's own
        # count is drawn from -- including members inside a currently
        # collapsed group, since collapsing never removes them from the count.
        assert "visibleProjects()" in body
        assert "allSelected" in body

    def test_group_checkbox_click_does_not_also_toggle_the_disclosure(self):
        app = _app()
        assert "input[data-group-sel]" in app
        assert "e.preventDefault();\n    e.stopPropagation();\n    toggleGroupSelected" in app


class TestForceExpandOnFilter:
    def test_a_filter_forces_every_group_open_regardless_of_saved_state(self):
        app = _app()
        assert "const filterActive = !!(state.filter.trim() || state.scope);" in app
        assert "const collapsed = !filterActive && collapsedSlugs.includes(g);" in app

    def test_force_expand_does_not_corrupt_the_saved_preference(self):
        # The force-expand render must never itself call the function that
        # writes localStorage -- only a genuine click on the header does
        # that (see TestPersistedCollapse.test_toggle_is_not_wired_off_...).
        app = _app()
        render_sidebar = app[app.index("function renderSidebar("):app.index("function selectActionsHtml(")]
        assert "toggleGroupCollapsed(" not in render_sidebar
