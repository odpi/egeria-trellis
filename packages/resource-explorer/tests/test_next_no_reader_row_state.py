"""DATABASE-DIRECT-FIELD-ROWS-IMPLEMENTED.md's vocabulary fix, pinned against
the real JS: a Questions-tab row backed by a `direct`/`chart` mechanism with
no declared reader must render `◌ no reader` (glyphs.js's `no_reader` state),
never `○ not run` (`unrun`) -- `unrun` promises a real analysis exists and
has simply not been triggered yet, which is false for a row with no reader
at all.

Found live 2026-09-29 on `laz_local_adventureworks` (#8810): 11 database
Questions rows showed `○ not run` even though every ANALYSIS on that
resource had run, because none of the 11 is backed by an analysis at all --
`app.js`'s `rowState` fell through its final `return 'unrun'` for any
`direct`/`chart`-kind row that was not yet answerable, with no state in
between "a real analysis has not run" and "no surveyor exists at all"
(`kind === 'gap'` already had its own state, `no-surveyor`).

Same extraction pattern `test_next_one_glyph_table.py` and
`test_next_renders_text_cross_check.py` already use: slice the real function
source out of app.js/glyphs.js and run it in node, rather than
re-implementing the logic in Python (which could silently drift from what
the browser actually runs).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT / "app.js"
GLYPHS_JS = NEXT / "glyphs.js"

ROW_STATE_START = "function rowState(entry, env) {"
ROW_STATE_END = "  return 'unrun';\n}"

NEEDS_LENS_START = "function needsLensDeclaration(env) {"
NEEDS_LENS_END = (
    "  return (env && env.facts ? env.facts : []).some(\n"
    "    (f) => f.analysis_id === 'preliminary_fit' && f.value && f.value.lens_declared === false,\n"
    "  );\n}"
)

GLYPH_START = "const GLYPH_KEYS = ["
GLYPH_END = "const GLYPH = Object.fromEntries(GLYPH_KEYS.map((k) => [k, GLYPH_STATES[k].glyph]));"

STATE_TONE_START = "export const STATE_TONE = {"
STATE_TONE_END = "};"

LEGEND_ORDER_START = "const LEGEND_ORDER = ["
LEGEND_ORDER_END = "];"


def _slice(src: str, start_sig: str, end_sig: str) -> str:
    start = src.index(start_sig)
    end = src.index(end_sig, start) + len(end_sig)
    return src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestNoReaderRowState:
    def _run(self, tmp_path: Path, cases: list) -> list:
        glyphs_src = GLYPHS_JS.read_text(encoding="utf-8")
        (tmp_path / "glyphs.mjs").write_text(glyphs_src, encoding="utf-8")

        app_src = APP_JS.read_text(encoding="utf-8")
        row_state_block = _slice(app_src, ROW_STATE_START, ROW_STATE_END)
        needs_lens_block = _slice(app_src, NEEDS_LENS_START, NEEDS_LENS_END)
        glyph_block = _slice(app_src, GLYPH_START, GLYPH_END)
        state_tone_block = _slice(app_src, STATE_TONE_START, STATE_TONE_END)
        legend_order_block = _slice(app_src, LEGEND_ORDER_START, LEGEND_ORDER_END)

        script = f"""
import {{ STATES }} from './glyphs.mjs';
const GLYPH_STATES = STATES;

{needs_lens_block}
{row_state_block}
{glyph_block}
{state_tone_block.replace('export const STATE_TONE', 'const STATE_TONE')}
{legend_order_block}

const cases = {json.dumps(cases)};
const results = cases.map(({{entry, env}}) => rowState(entry, env));
console.log(JSON.stringify({{
  results,
  glyphKeys: GLYPH_KEYS,
  glyph: GLYPH,
  stateToneKeys: Object.keys(STATE_TONE),
  legendOrder: LEGEND_ORDER,
}}));
"""
        script_path = tmp_path / "run.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_a_direct_row_with_no_reader_is_no_reader_not_unrun(self, tmp_path):
        """The exact defect: a `direct`-kind question the catalog names no
        resolver for used to fall to `unrun` ("○ not run" — a promise a real
        analysis exists). It must now read `no_reader` ("◌ no reader")."""
        out = self._run(tmp_path, [
            {"entry": {"kind": "direct"}, "env": {"answerable": False}},
        ])
        assert out["results"] == ["no_reader"]

    def test_a_chart_row_with_no_reader_is_also_no_reader(self, tmp_path):
        out = self._run(tmp_path, [
            {"entry": {"kind": "chart"}, "env": {"answerable": False}},
        ])
        assert out["results"] == ["no_reader"]

    def test_a_direct_row_that_IS_answerable_is_still_automatic_not_no_reader(self, tmp_path):
        """The fix must not swallow the real case: once a reader exists and
        answers, the row is `automatic`, same as before."""
        out = self._run(tmp_path, [
            {"entry": {"kind": "direct"}, "env": {"answerable": True, "level_mismatch": False}},
        ])
        assert out["results"] == ["automatic"]

    def test_a_gap_row_is_still_no_surveyor_not_no_reader(self, tmp_path):
        """`gap` (no mechanism at all) and an unanswerable `direct`/`chart`
        (a mechanism kind is declared, just not wired up) stay two different
        states, even though both render the same ◌ glyph family."""
        out = self._run(tmp_path, [
            {"entry": {"kind": "gap"}, "env": {"answerable": False}},
        ])
        assert out["results"] == ["no-surveyor"]

    def test_an_analysis_row_that_has_not_run_is_still_plain_unrun(self, tmp_path):
        """The vocabulary fix is scoped to `direct`/`chart` only -- an
        `analysis`-kind question with a real, unrun surveyor keeps its
        honest `unrun` ("not run") state."""
        out = self._run(tmp_path, [
            {"entry": {"kind": "analysis"}, "env": {"answerable": False}},
        ])
        assert out["results"] == ["unrun"]

    def test_no_reader_is_a_declared_glyph_state(self, tmp_path):
        out = self._run(tmp_path, [])
        assert "no_reader" in out["glyphKeys"]
        assert out["glyph"]["no_reader"] == "◌"
        assert "no_reader" in out["stateToneKeys"]

    def test_the_legend_counts_no_reader_separately_from_unrun(self, tmp_path):
        """The KEY line (`LEGEND_ORDER`) must distinguish the two counts --
        the whole point of the fix is that a "no reader yet" row must not be
        folded into the "not run" count on the legend either."""
        out = self._run(tmp_path, [])
        assert "no_reader" in out["legendOrder"]
        assert "unrun" in out["legendOrder"]
        assert out["legendOrder"].index("no_reader") != out["legendOrder"].index("unrun")
