"""Slice 17, item 3, the /next Questions tab half: `rowState()` withholds the
✓ glyph when an envelope carries `level_mismatch` (design §18.3), and every
place that counted/gated on `env.answerable` alone for "is this row fully
answered" now goes through `isFullyAnswered()` instead, so the tick, the
"N of M answered" count and the legend cannot disagree about the same row.

No Node runtime in this repo's test suite (confirmed: no *.test.js/*.spec.js
files, no JS execution harness) -- every existing app.js test in this
directory (test_next_rail_states.py, test_next_component_review.py, …) reads
the source as text and asserts structurally. This file follows that same
convention.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app() -> str:
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _function_body(src: str, signature: str, max_len: int = 2000) -> str:
    start = src.index(signature)
    return src[start:start + max_len]


class TestRowStateWithholdsTheTickOnLevelMismatch:
    def test_row_state_checks_level_mismatch_before_answered(self):
        app = _app()
        body = _function_body(app, "function rowState(entry, env) {", 700)
        assert "env.level_mismatch" in body
        assert "return 'partial'" in body

    def test_is_fully_answered_helper_exists_and_excludes_level_mismatch(self):
        app = _app()
        assert "function isFullyAnswered(env)" in app
        body = _function_body(app, "function isFullyAnswered(env) {", 300)
        assert "level_mismatch" in body
        assert "answerable" in body

    def test_answered_count_uses_the_helper_not_bare_answerable(self):
        app = _app()
        body = _function_body(app, "function updateAnsweredCount() {", 900)
        assert "isFullyAnswered(env)" in body
        # The old bare check must be gone from this function specifically --
        # a regression here would silently re-count a level-mismatched row as
        # answered while the row itself shows the withheld-tick glyph.
        assert "return env.answerable;" not in body


class TestPartialIsARealState:
    def test_partial_has_a_glyph_and_a_tone(self):
        app = _app()
        glyph_block = _function_body(app, "const GLYPH = {", 700)
        assert "partial:" in glyph_block
        tone_block = _function_body(app, "const STATE_TONE = {", 900)
        assert "partial:" in tone_block

    def test_partial_is_in_the_legend_with_its_own_label(self):
        app = _app()
        legend_block = _function_body(app, "const LEGEND = [", 500)
        assert "['partial'," in legend_block
        assert "ran, but not at this level" in legend_block

    def test_partial_rows_still_offer_evidence_and_the_numbers_behind_this(self):
        """A level-mismatch row is still a REAL answer (design §18.3: the
        tick is withheld, the row is not hidden or crippled) -- it must keep
        the same secondary actions an 'answered' row gets."""
        app = _app()
        assert "st === 'answered' || st === 'automatic' || st === 'partial'" in app

    def test_body_lines_renders_the_level_note_for_partial_rows(self):
        app = _app()
        body = _function_body(app, "// answered | automatic | partial", 900)
        assert "env.level_note" in body
        assert "st === 'partial'" in body
