"""Source-level regression tests for two copy fixes to `/next`'s Survey &
analyses pane, `loadSurveyPane()` (`resource_explorer/web/static/next/app.js`)
— Phase 1b coordinator brief, slice 12's own two small fixes.

No JS test runner is wired into this suite (see
`test_next_egeria_native_processes_ui.py`'s identical note), so these pin
the fix at the source level: extract the function body from the real
shipped source and assert on it directly.

(a) The red "Scope: all tiers — stage filter unavailable · retry" badge
    must not render when there are zero candidates at all — the full-scan
    fallback is inevitable with nothing to scope, and retry cannot change
    that. The stage label should render neutrally instead.
(b) "No local or RE-authored survey definitions for this resource" / "The
    adapter registered none for this technology type." reads as though the
    resource itself is the problem. Replaced with "No Survey Definitions
    have been authored for <technology type> yet" / "The analyses below
    still run individually; a Survey Definition only bundles them into an
    Egeria-launchable process." — the "Also known to Egeria" section
    (rendered separately, unconditionally) is unchanged.
"""
from __future__ import annotations

from pathlib import Path

NEXT_DIR = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT_DIR / "app.js"


def _balanced(js: str, start: int) -> str:
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
    start = js.index(signature)
    paren = js.index("(", start)
    params_end = paren + len(_balanced(js, paren))
    brace = js.index("{", params_end)
    return js[start:brace] + _balanced(js, brace)


def _load_survey_pane_source() -> str:
    return _fn_decl(APP_JS.read_text(), "async function loadSurveyPane(")


class TestFullScanBadgeHiddenWithZeroCandidates:
    def test_the_warn_badge_condition_also_checks_all_length(self):
        src = _load_survey_pane_source()
        # Both the class-selection ternary and the label ternary must gate
        # on `all.length`, not `data.scoping` alone.
        assert "data.scoping === 'full-scan' && all.length" in src

    def test_a_bare_full_scan_check_with_no_length_guard_is_gone(self):
        """Regression pin: the OLD unconditional check must not still be
        present anywhere in this function (e.g. a second, un-fixed copy)."""
        src = _load_survey_pane_source()
        assert "data.scoping === 'full-scan' ?" not in src
        assert "data.scoping === 'full-scan'\n" not in src


class TestEmptyStateCopyDoesNotBlameTheResource:
    def test_the_new_title_names_the_technology_type_not_the_resource(self):
        src = _load_survey_pane_source()
        assert "No Survey Definitions have been authored for" in src
        assert "No local or RE-authored survey definitions for this resource" not in src

    def test_the_new_body_explains_survey_definitions_bundle_analyses(self):
        src = _load_survey_pane_source()
        assert "The analyses below still run individually" in src
        assert "a Survey Definition only bundles them into an" in src
        assert "The adapter registered none for this technology type." not in src

    def test_the_also_known_to_egeria_section_is_still_rendered_unconditionally(self):
        """That section is a separate call (`nativeProcessesSectionHtml`),
        rendered regardless of whether `all.length` is zero -- must not be
        folded into or removed by the empty-state message fix."""
        src = _load_survey_pane_source()
        assert "nativeProcessesSectionHtml(data.egeria_native_processes)" in src
