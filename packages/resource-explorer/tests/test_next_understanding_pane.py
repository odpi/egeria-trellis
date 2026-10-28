"""Understanding's charts pane (PLAN-FINISH-REPOS.md item 2): pinning the
honesty rules `loadChartsPane()` (next/stages/understanding.js) already
enforces, plus the no-data/error/stale messaging and chart selection, so a
later edit that erodes one of these fails a test instead of a screenshot.

No browser verification happened for this file — see
ITEM-2-UNDERSTANDING-IMPLEMENTED.md for what was and was
not checked live. These tests grep/slice function bodies out of the
concatenated source, the established pattern for /next JS modules without a
browser -- see test_next_curate_pane.py, test_next_rail_states.py, and
test_next_enrichment_fidelity.py's `_app()` helper, reproduced identically
here.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    """app.js plus every stages/*.js module, concatenated -- see the
    identical helper's docstring in test_next_component_review.py. The
    chart-rendering machinery (drawChart, chartAxes, asFigure, allPointDates,
    chartIsStale) stays in app.js by design (see understanding.js's own
    top-of-file comment); loadChartsPane itself lives in stages/
    understanding.js. Concatenating both means a test can grep across the
    seam without caring which file a given function ended up in."""
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _understanding_src():
    return (NEXT / "stages" / "understanding.js").read_text(encoding="utf-8")


class TestOneObservationIsNotATrend:
    """A single-point series is offered (the value is real) but never drawn
    as though it had a shape -- neither in the chip that selects it nor in
    the chart body once drawn."""

    def test_the_index_chip_labels_a_single_point_as_first_measurement(self):
        src = _understanding_src()
        assert "const one = r.points === 1;" in src
        body = src[src.index("const one = r.points === 1;"):src.index("}).join('');", src.index("const one = r.points === 1;"))]
        assert "first measurement" in body
        # the non-singular branch shows the real count, not the same label
        assert "r.points" in body and "text-ink-muted\"> · ${r.points}</span>" in body

    def test_drawn_chart_says_value_not_trend_for_a_single_point(self):
        app = _app()
        body = app[app.index("export async function drawChart("):app.index("function chartLayout(")]
        assert "entry.points === 1" in body
        assert "This is a value, not a trend" in body

    def test_the_probe_counts_points_not_traces(self):
        """A trace holding one observation must not be counted as a usable
        multi-point series -- REPO_CHARTS probing counts points across
        x/labels/r/values, not `fig.data.length`."""
        src = _understanding_src()
        assert "const traces = Array.isArray(fig?.data) ? fig.data : [];" in src
        assert "const points = traces.reduce((n, t) => n + (" in src
        assert "(t.x || t.labels || t.r || t.values || []).length" in src


class TestStaleDataIsFlagged:
    def test_the_same_staleness_threshold_flags_the_chip_and_the_drawn_chart(self):
        app = _app()
        assert "export function chartIsStale(dateStr) {" in app
        threshold = app[app.index("export function chartIsStale(dateStr) {"):app.index("export function chartIsStale(dateStr) {") + 300]
        assert "d >= 7" in threshold

        src = _understanding_src()
        assert "chartIsStale(r.last)" in src, "the chart-index chip must flag a stale latest date"

        body = app[app.index("export async function drawChart("):app.index("function chartLayout(")]
        assert "chartIsStale(entry.last)" in body
        assert "nothing newer has been recorded" in body


class TestNoDataAndErrorMessagingAreDistinctSentences:
    """A 200 with an empty series, a failed probe, and a resource with
    nothing recorded across every chart are three different facts and must
    read as three different sentences -- not folded into one blank state."""

    def test_a_failed_probe_reads_as_unavailable_not_as_empty(self):
        src = _understanding_src()
        assert "error: err.message" in src
        body = src[src.index("if (r.error) {"):src.index("if (!r.points) {")]
        assert "unavailable" in body
        assert "title=\"${esc(r.error)}\"" in body

    def test_a_zero_point_series_says_nothing_recorded_yet_not_an_error(self):
        src = _understanding_src()
        body = src[src.index("if (!r.points) {"):src.index("const one = r.points === 1;")]
        assert "nothing recorded yet" in body
        assert "The series exists and has nothing in it yet" in body

    def test_no_selectable_chart_at_all_states_that_as_a_fact_about_history_not_the_resource(self):
        src = _understanding_src()
        assert "const first = results.find((r) => r.points);" in src
        tail = src[src.index("const first = results.find((r) => r.points);"):]
        assert "Nothing has been recorded for any of this resource's charts yet" in tail
        assert "not about the resource" in tail

    def test_no_resource_selected_is_a_fourth_distinct_message(self):
        src = _understanding_src()
        assert "Select a resource" in src
        assert "Pick a repository from the sidebar to see its charts." in src


class TestChartSelectionActuallyDraws:
    def test_clicking_an_index_chip_calls_drawchart_with_the_matching_entry(self):
        src = _understanding_src()
        assert "const select = (kind) => {" in src
        body = src[src.index("const select = (kind) => {"):src.index("index.querySelectorAll('[data-chart]').forEach((b) =>")]
        assert "drawChart(results.find((r) => r.kind === kind), { bodyId });" in body
        wiring = src[src.index("index.querySelectorAll('[data-chart]').forEach((b) =>"):src.index("const first = results.find")]
        assert "b.addEventListener('click', () => select(b.dataset.chart)));" in wiring

    def test_the_first_chart_with_data_is_auto_selected_on_load(self):
        src = _understanding_src()
        tail = src[src.index("const first = results.find((r) => r.points);"):]
        assert "if (first) {\n    select(first.kind);" in tail


class TestEveryCatalogedChartHasAWorkingProbe:
    """REPO_CHARTS is the sidebar's whole menu; the done-test asks whether a
    real question is reachable and honestly labelled, which requires every
    entry to actually be fetchable via getChart, not just declared."""

    def test_repo_charts_covers_the_stats_backend_and_stays_in_sync_with_the_probe(self):
        api = (NEXT.parent / "re-api.js").read_text(encoding="utf-8")
        block = api[api.index("export const REPO_CHARTS = ["):api.index("];", api.index("export const REPO_CHARTS = ["))]
        kinds = [line.split("'")[1] for line in block.splitlines() if "'" in line]
        assert kinds == [
            "stars", "commits", "weekly_commits", "languages",
            "file_types", "top_committers", "health", "survey_history",
        ]
        # A commit-volume question ("how has commit volume changed?") is
        # answerable via either 'commits' or 'weekly_commits' -- both must
        # be real, fetchable kinds, not placeholders.
        assert "commits" in kinds and "weekly_commits" in kinds

        src = _understanding_src()
        assert "REPO_CHARTS.map(([kind, label]) => getChart(slug, kind)" not in src  # not the literal shape
        assert "wanted.map(async ([kind, label]) => {" in src  # wanted = REPO_CHARTS, or one tab's kinds
        assert "await getChart(slug, kind)" in src
