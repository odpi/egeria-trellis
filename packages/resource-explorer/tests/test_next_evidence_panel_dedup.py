"""Source-level regression tests for /next's Evidence rail
(`showEvidence`, `resource_explorer/web/static/next/app.js`) — two owner-
reported defects from the same live gate (2026-09-26):

(a) A "mixed" question answered by more than one analysis_id (e.g. "How
big is this database" — schema_inventory + row_count_snapshot) rendered
EVERY key in each fact's own `.value` dict independently, with no
awareness of what an earlier fact already showed. Both readers happen to
call their own fields "tables"/"table_count", so the evidence panel
listed the same numbers twice.

(b) The ⚠ next to "ran Xm ago" on a Survey Definition card did nothing on
click — see test_registry.py's `TestSurveyDefinitionLastActivityCarriesRunErrors`
for the backend half (`last_run_errors` now threaded through) and this
file for the frontend half (the button and its click handler).

No JS test runner is wired into this suite (see
`test_next_egeria_native_processes_ui.py`'s own note), so both are pinned
at the source level.
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


class TestEvidencePanelDedupesRepeatedKeysAcrossFacts:
    def _show_evidence_source(self) -> str:
        return _fn_decl(APP_JS.read_text(), "function showEvidence(")

    def test_a_shared_keys_set_is_tracked_across_facts(self):
        src = self._show_evidence_source()
        assert "shownKeys" in src
        assert "new Set()" in src

    def test_each_facts_entries_are_filtered_against_it(self):
        src = self._show_evidence_source()
        assert "shownKeys.has(k)" in src
        assert "shownKeys.add(k)" in src


class TestRunErrorIndicatorIsClickable:
    def _last_run_html_source(self) -> str:
        return _fn_decl(APP_JS.read_text(), "function lastRunHtml(")

    def test_the_warning_glyph_becomes_a_button_when_errors_are_present(self):
        src = self._last_run_html_source()
        assert "data-run-errors" in src
        assert "last_run_errors" in src

    def test_a_handler_for_the_new_button_opens_a_dialog(self):
        # The handler is an inline arrow passed to forEach, not a named
        # function declaration `_fn_decl` can extract by signature — locate
        # it by its registration call instead and pull the balanced block
        # that follows.
        src = APP_JS.read_text()
        marker = "el.querySelectorAll('[data-run-errors]')"
        start = src.index(marker)
        paren = src.index("(", start + len(marker))
        block = _balanced(src, paren)
        assert "openDialog" in block
        assert "last_run_errors" in block
