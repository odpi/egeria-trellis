"""The component review in the Curate column (designer's ports round,
2026-09-14). Verified in a browser on kafka: 641 components as 69 branches,
the ⚠ count on the row, 116 leaves under clients/, the preview dialog."""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    """app.js plus every stages/*.js module, concatenated. PLAN-FINISH-
    REPOS.md Part 2 §1 (APP-JS-SPLIT-IMPLEMENTED.md) moved per-stage
    rendering out of app.js into next/stages/ -- this test greps for
    function bodies by name and does not need to know which file a
    function ended up in, only that the source text it searches covers
    all of them."""
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


class TestReviewHappensAtTheBranch:
    def test_rows_are_branches_and_verdicts_say_where_they_came_from(self):
        app = _app()
        # verdictBadge moved to stages/curate.js; bound to recordVerdicts,
        # the last function in that file, rather than app.js's rowKey.
        body = app[app.index("function verdictBadge("):app.index("function recordVerdicts(")]
        assert "data-branch-open" in body and "accept all ${reach}" in body
        assert "· with <span class=\"font-mono\">${esc(v.inherited_from)}/</span>" in body   # inherited says so
        assert "grouping only — a directory that holds components, not a component itself" in body
        assert "⚠ <span class=\"tnum\">${b.low_confidence}</span> at or below 50%" in body   # confidence routes, never hides

    def test_ports_are_a_column_and_the_foot_says_what_it_looked_in(self):
        app = _app()
        body = app[app.index("function verdictBadge("):app.index("function recordVerdicts(")]
        assert "function portsWords(" in body
        assert "tree.topology" in body
        assert "read from the deployment artifacts" in body and "derived from" not in body

    def test_bulk_accept_goes_through_the_preview_and_nothing_runs_until_confirmed(self):
        app = _app()
        # recordVerdicts is the last function in stages/curate.js -- no
        # next-function marker to bound it, so take the rest of the file.
        body = app[app.index("function recordVerdicts("):]
        assert "openDialog('Accept at the branch'" in body
        assert "Accepting is a decision: nothing is written to Egeria until you press Publish" in body
        # Not "SolutionComponents" -- naming an unverified type in a
        # confirmation dialog before it's pinned is how the SoftwareLibrary
        # mistake happened one round earlier (docs/Backlog.md, "Catalogue in
        # layers"). Plain words until the type is confirmed against a live
        # server, per the designer's own phrasing.
        assert "will be recorded as accepted" in body, "Accept is a decision; Publish creates the components"
        assert "will be created as software components" not in body
        assert "if (verdict !== 'accepted' || count <= 1) { go(); return; }" in body   # rejecting creates nothing; one leaf needs no preview

    def test_no_undo_and_the_word_is_change(self):
        app = _app()
        body = app[app.index("function leafRowHtml("):app.index("async function renderComponentTree(")]
        assert "'change' : 'accept'" in body and "undo" not in body.lower()


class TestRoundTwoSmallItems:
    """Round two, items 2-4 (designer, 2026-09-14). Verified on
    egeria-workspaces: '3 ports ›' opens the rail; '71 ports across 49
    components · 44 wires · 3 not attributable …' at the foot; sort by
    confidence puts templates/ ⚠ 25 first."""

    def test_the_column_has_two_shapes(self):
        app = _app()
        body = app[app.index("function portsWords("):app.index("function openPortsInRail(")]
        assert "own.length <= 2" in body and "data-ports-open" in body and "ports${icon('chevron-right'" in body

    def test_the_ports_list_opens_in_the_rail_with_no_verdict_to_give(self):
        app = _app()
        body = app[app.index("function openPortsInRail("):app.index("function branchRowHtml(")]
        assert "railFrame('Ports'" in body and "no verdict to give" in body

    def test_sort_is_a_sort_never_a_filter(self):
        app = _app()
        body = app[app.index("async function renderComponentTree("):app.index("async function renderComponentDiagram(")]
        assert "data-tree-sort=\"confidence\"" in body and "rows.sort(" in body
        # The SORT never drops a branch. (P3 added a search and a reading filter ahead of it: those are
        # a separate, announced step -- "N of M components match" -- and only act when one is asked for.)
        sort_step = body.split("if (sort === 'confidence')")[1].split("const shown =")[0]
        assert ".filter(" not in sort_step
        assert "const filtering = !!(search.trim() || readingPick);" in body

    def test_the_foot_carries_both_ends_and_the_diagram_reads(self):
        app = _app()
        assert "tree.topology_totals" in app
        body = app[app.index("async function renderComponentDiagram("):app.index("/** The shared preview dialog")]
        assert "The diagram reads; the tree acts" in body and "fact.value.caption" in body
        assert "No diagram to read" in body and "could not be rendered" in body
