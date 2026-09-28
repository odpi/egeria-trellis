"""Source-level regression test for /next's Survey & analyses pane silently
dropping `egeria_native_processes` (`docs/Backlog.md`'s "`/next`'s Survey &
analyses tab silently drops Egeria's own native, technology-specific survey
processes" entry, from `#244`).

Live reproduction (coco_pharma, a PostgreSQL database resource): the backend
(`GET` the survey-candidates endpoint, `resource_explorer/web/routes/
survey_definitions.py`) correctly returns `candidates: []` (no RE-authored
Survey Definition exists for database resources -- a separate, already-logged
gap) alongside a non-empty `egeria_native_processes` list carrying real,
runnable-elsewhere Egeria processes for that technology type. Classic
(`web/static/index.html`'s `renderSurveyPanel`, `nativeProcessesHtml`) reads
and renders this field as an informational block; `/next`'s `loadSurveyPane`
(`web/static/next/app.js`) never read it at all, so the pane showed "No
survey definitions for this resource" even though Egeria's own native
processes for the technology were real and known.

No JS test runner is wired into this suite (see test_next_prerequisite_
proposal_ui.py's own note), so this pins the fix at the source level: read
the real shipped source, extract the function/branch under test, and assert
on what it actually does.
"""
from __future__ import annotations

from pathlib import Path

NEXT_DIR = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT_DIR / "app.js"


def _balanced(js: str, start: int) -> str:
    """From `start` (pointing at an opening brace/paren), the matching close."""
    depth = 0
    i = start
    started = False
    while True:
        ch = js[i]
        if ch in "([{":
            depth += 1
            started = True
        elif ch in ")]}":
            depth -= 1
        if started and depth == 0:
            return js[start:i + 1]
        i += 1


def _fn_decl(js: str, signature: str) -> str:
    """A `function NAME(...) { ... }` declaration (or `async function`),
    given its exact `function ...(` signature text, through the matching
    closing brace."""
    start = js.index(signature)
    paren = js.index("(", start)
    params_end = paren + len(_balanced(js, paren))
    brace = js.index("{", params_end)
    return js[start:brace] + _balanced(js, brace)


def _app_js_source() -> str:
    return APP_JS.read_text()


class TestNativeProcessesSectionRenderer:
    """The dedicated renderer exists, reads the right field, and is
    informational only -- no run affordance, matching classic's own scope."""

    def _fn(self) -> str:
        return _fn_decl(_app_js_source(), "function nativeProcessesSectionHtml(")

    def test_it_reads_display_name_kind_and_description(self):
        fn = self._fn()
        assert "p.display_name" in fn
        assert "p.kind" in fn
        assert "p.description" in fn

    def test_it_is_informational_only_no_run_button(self):
        """Even for a `survey_existing`-kind process, wiring it to run from
        this pane is a separate follow-up (Backlog.md #244) -- not this fix."""
        fn = self._fn()
        assert "data-run-survey" not in fn
        assert "data-plan-survey" not in fn
        assert "addEventListener" not in fn

    def test_empty_or_missing_list_renders_nothing(self):
        fn = self._fn()
        assert "if (!nativeProcesses.length) return ''" in fn


class TestLoadSurveyPaneRendersNativeProcessesRegardlessOfCandidates:
    """The gap: `/next` never read `egeria_native_processes` at all. The fix
    must call the renderer with the field straight off the response, and the
    call must not be gated inside the `!all.length` empty-state branch --
    it's shown whenever Egeria has processes to report, same as classic."""

    def _fn(self) -> str:
        return _fn_decl(_app_js_source(), "async function loadSurveyPane(")

    def test_it_reads_egeria_native_processes_off_the_response(self):
        fn = self._fn()
        assert "data.egeria_native_processes" in fn
        assert "nativeProcessesSectionHtml(" in fn

    def test_the_call_is_not_nested_inside_the_empty_candidates_branch(self):
        fn = self._fn()
        native_pos = fn.index("nativeProcessesSectionHtml(data.egeria_native_processes)")
        empty_branch_pos = fn.index("!all.length")
        assert native_pos < empty_branch_pos, (
            "nativeProcessesSectionHtml must be computed before/independent of "
            "the !all.length empty-state check, not nested inside it -- the "
            "native processes are a separate fact shown whether or not "
            "RE-authored candidates exist")

    def test_the_rendered_html_is_placed_in_the_panel_output(self):
        fn = self._fn()
        # The variable holding the rendered section must actually be spliced
        # into the template the pane assigns to el.innerHTML.
        assert "${nativeProcessesHtml}" in fn
