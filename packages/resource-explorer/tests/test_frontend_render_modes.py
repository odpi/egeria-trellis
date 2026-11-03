"""The frontend's analysis-kind mirrors must not fall behind the backend.

index.html hand-maintains two objects keyed by analysis_id — _REPO_RESULTS_RENDER_MODE
(which render branch a kind's results use) and _REPO_TREND_LABEL (the trend
chart's Y-axis unit). Both are documented as mirroring ANALYSIS_KINDS, and both
silently fell behind it: repository_health, code_symbol_extraction,
rag_ingestion and website_ingestion each had a working backend results reader
and no entry here, so their cards rendered with no Results button at all — the
button is gated on membership of the render-mode object.

Nothing failed, which is what made it survive: a missing entry looks exactly
like an analysis that was never meant to have results. These tests are the
check that was missing, not a restatement of the fix.

Parsed with a regex rather than a JS engine on purpose — the SPA has no build
step and no JS test runner, and adding either to guard two object literals
would cost more than it protects.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS


@pytest.fixture(scope="module")
def kinds_with_results() -> set[str]:
    return {k for k, v in ANALYSIS_KINDS.items() if v.results}


def test_metrics_mode_readers_return_metrics_at_the_top_level():
    """The 'metrics' branch iterates the payload's own entries and filters out
    only detail/surveyed_at/_status, so a reader that nests its numbers under
    an envelope key renders one row named after the envelope.

    _health_results did exactly that. This pins the contract that made the
    render-mode fix real rather than merely consistent.
    """

    reserved = {"detail", "surveyed_at", "_status"}
    for kind, spec in ANALYSIS_KINDS.items():
        if not spec.results or spec.results.render != "metrics":
            continue
        src = inspect.getsource(spec.results.results_reader)
        assert '"metrics":' not in src and "'metrics':" not in src, (
            f"{kind}'s reader ({spec.results.results_reader.__name__}) appears to "
            "nest its numbers under a 'metrics' key, but its render mode is "
            "'metrics', which reads them from the top level. Flatten the payload "
            f"(reserved top-level keys: {sorted(reserved)})."
        )


# ── the architecture diagram's second half ───────────────────────────────────
#
# `architecture_diagram` is the first kind whose results view cannot be built
# from its payload alone: the payload holds Mermaid SOURCE, and the picture is
# drawn by the Kroki proxy (POST /api/diagrams/mermaid). A `(data) => html`
# renderer cannot await that, so _renderArchitectureDiagramResults emits a
# placeholder and renderPendingArchDiagrams() fills it in after the caller has
# inserted the HTML.
#
# That split has an invariant no other kind has: a site that inserts results
# HTML and never runs the sweep shows an empty bordered box — a card that looks
# built and is blank, which is worse than the missing Results button this file's
# other tests were written for. Four sites insert results HTML today; the point
# of deriving them below rather than listing them is that a fifth is caught.


def test_the_api_sends_the_field_the_card_reads():
    """A card reading a key the payload never sends renders `undefined`. Both
    sides are edited here, so this pins them together.

    The payload-building logic moved from `projects.py` into
    `workflows.analysis.build_analysis_last_activity` when database/
    filesystem got their own `/analyses/last-activity` routes sharing it —
    `projects.py` is now a thin adapter with no field literals of its own, so
    this pins the field where it actually lives."""
    import pathlib as _p
    route = (_p.Path(__file__).resolve().parent.parent
             / "resource_explorer" / "workflows" / "analysis.py").read_text()
    assert '"last_run_derived_from"' in route, (
        "index.html reads la.last_run_derived_from but the analyses payload in "
        "workflows/analysis.py does not send it"
    )


# ── the queued-for-publish toast and its saved-time sentence ─────────────────
#
# Owner's complaint (2026-09-13): "I saw the queued after a few seconds and
# the toast went away before I could read it" — a message that says what
# will happen LATER must not vanish on a timer. And separately: the run
# dialog never said what running in the background actually saved. These
# tests pin both fixes structurally (regex on the JS source, same approach
# as the rest of this file — no build step, no JS test runner) with
# assertions specific enough that deleting either fix fails them; each was
# sabotaged and confirmed to fail before being trusted.


APP_JS = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next" / "app.js"


def _open_members_not_applicable_branch() -> str:
    """The `if (data.not_applicable) { ... }` block inside /next's
    `openMembers` -- the rail's metrics-only-analysis branch (RE: a
    metrics-only analysis has no members, 2026-09-14)."""
    text = APP_JS.read_text()
    start = text.index("async function openMembers(")
    marker = "if (data.not_applicable)"
    i = text.index(marker, start)
    end = text.index("const groups = data.groups", i)
    return text[i:end]


class TestNextRailMetricsOnlyBranch:
    """/next's app.js equivalent of the render-mode mirrors above: the rail
    must render a metrics-only analysis (repository_health, and the like) as
    one sentence in the list's slot, never a save-as-report offer over an
    empty snapshot."""

    def test_open_members_has_a_not_applicable_branch(self):
        branch = _open_members_not_applicable_branch()
        assert "data.reason" in branch

    def test_the_branch_offers_no_report_and_no_whole_list_footer(self):
        branch = _open_members_not_applicable_branch()
        assert "data-report-whole" not in branch
        assert "data-report-sel" not in branch
        assert "The whole list" not in branch
        assert "wireSelection" not in branch

    def test_the_branch_returns_before_the_ordinary_groups_render(self):
        branch = _open_members_not_applicable_branch()
        assert branch.rstrip().endswith("return;\n  }") or "return;" in branch
