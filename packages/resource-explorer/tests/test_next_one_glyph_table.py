"""REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1 -- "one table in `next/
glyphs.js`, imported by all four surfaces." Before this module, `GLYPH`
(app.js), `CELL` (worklist.js) and `factGlyph` (app.js) each declared their
own glyph-to-meaning mapping and disagreed with each other about what a
glyph meant -- ◐ meant "ran, not at this level" in `GLYPH`, "partial" in
`CELL`, and `no-surveyor` shared ○ with `unrun` in both, told apart only by
colour (which breaks the "colour is never the only channel" design rule
`worklist.js`'s own header already states).

The dispatch's own bar: "one table, three consumers, no glyph has two
meanings." This file pins that bar directly against the running code, the
same way `test_next_renders_text_cross_check.py` pins `readEnvelope` against
`facts.py` -- by running the real JS (via node, not by re-implementing it in
Python) and checking every consumer resolves through `glyphs.js`'s `STATES`.

Three checks:

  1. `app.js`'s `GLYPH` -- every key's glyph equals `STATES[key].glyph`.
  2. `worklist.js`'s `CELL` -- every key's glyph equals `STATES[key].glyph`,
     and its `label` equals `STATES[key].word` (the legend-word half of the
     same consolidation -- reply §1: "Each glyph carries its word in
     `title`/`aria-label`").
  3. `app.js`'s `factGlyph` -- every one of its four cases' glyph equals the
     `STATES` entry it names.

Plus the specific regression the reply calls out by name: `no-surveyor` must
render `◌`, not `○` -- before this pass it shared `○` with `unrun` in both
`GLYPH` and `CELL`.

A fourth consumer was found during G1 and folded in: `stages/curate.js`'s
`curateRecordHtml` declared a second, independent task-state glyph map (its
own `◐` for "running", disagreeing with the canonical `◔`). It was
consolidated to route through `factGlyph` rather than import `glyphs.js`
directly, so it has no glyph-to-meaning mapping of its own to check here --
this file instead asserts the literal old map is gone from the source, so a
future re-introduction of a fifth independent table fails loudly rather than
silently.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT / "app.js"
WORKLIST_JS = NEXT / "worklist.js"
GLYPHS_JS = NEXT / "glyphs.js"
CURATE_JS = NEXT / "stages" / "curate.js"

# The exact GLYPH block in app.js -- see that file's own comment pointing
# back at this test by name.
GLYPH_START = "const GLYPH_KEYS = ["
GLYPH_END = "const GLYPH = Object.fromEntries(GLYPH_KEYS.map((k) => [k, GLYPH_STATES[k].glyph]));"

FACT_GLYPH_START = "export function factGlyph(state) {"
FACT_GLYPH_END = (
    "default: return { glyph: GLYPH_STATES.unclassified.glyph, tone: 'text-ink-muted' };\n"
    "  }\n"
    "}"
)

CELL_START = "const CELL_TONE = {"
CELL_END = (
    "export const CELL = Object.fromEntries(Object.keys(CELL_TONE).map((k) => [\n"
    "  k, { glyph: GLYPH_STATES[k].glyph, tone: CELL_TONE[k], label: GLYPH_STATES[k].word },\n"
    "]));"
)


def _slice(src: str, start_sig: str, end_sig: str) -> str:
    start = src.index(start_sig)
    end = src.index(end_sig, start) + len(end_sig)
    return src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestOneGlyphTable:
    def _run(self, tmp_path: Path) -> dict:
        glyphs_src = GLYPHS_JS.read_text(encoding="utf-8")
        (tmp_path / "glyphs.mjs").write_text(glyphs_src, encoding="utf-8")

        app_src = APP_JS.read_text(encoding="utf-8")
        glyph_block = _slice(app_src, GLYPH_START, GLYPH_END)
        fact_glyph_block = _slice(app_src, FACT_GLYPH_START, FACT_GLYPH_END)

        worklist_src = WORKLIST_JS.read_text(encoding="utf-8")
        cell_block = _slice(worklist_src, CELL_START, CELL_END)

        script = f"""
import {{ STATES }} from './glyphs.mjs';
const GLYPH_STATES = STATES;

{glyph_block}

{fact_glyph_block.replace('export function factGlyph', 'function factGlyph')}

{cell_block}

console.log(JSON.stringify({{
  glyphKeys: GLYPH_KEYS,
  glyph: GLYPH,
  cellKeys: Object.keys(CELL),
  cell: CELL,
  factGlyphSamples: {{
    measured: factGlyph('measured'),
    error: factGlyph('error'),
    unrun: factGlyph('unrun'),
    fallback: factGlyph('some-unrecognized-state'),
  }},
  states: STATES,
}}));
"""
        script_path = tmp_path / "run.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_glyph_resolves_through_the_one_table(self, tmp_path):
        out = self._run(tmp_path)
        states = out["states"]
        for key in out["glyphKeys"]:
            assert out["glyph"][key] == states[key]["glyph"], (
                f"GLYPH[{key!r}] = {out['glyph'][key]!r} does not match "
                f"the canonical glyphs.js entry {states[key]['glyph']!r}"
            )

    def test_cell_resolves_through_the_one_table(self, tmp_path):
        out = self._run(tmp_path)
        states = out["states"]
        for key in out["cellKeys"]:
            cell = out["cell"][key]
            assert cell["glyph"] == states[key]["glyph"], (
                f"CELL[{key!r}].glyph = {cell['glyph']!r} does not match "
                f"the canonical glyphs.js entry {states[key]['glyph']!r}"
            )
            assert cell["label"] == states[key]["word"], (
                f"CELL[{key!r}].label = {cell['label']!r} does not match "
                f"the canonical glyphs.js word {states[key]['word']!r}"
            )

    def test_fact_glyph_resolves_through_the_one_table(self, tmp_path):
        out = self._run(tmp_path)
        states = out["states"]
        samples = out["factGlyphSamples"]
        assert samples["measured"]["glyph"] == states["measured"]["glyph"]
        assert samples["error"]["glyph"] == states["error"]["glyph"]
        assert samples["unrun"]["glyph"] == states["unrun"]["glyph"]
        assert samples["fallback"]["glyph"] == states["unclassified"]["glyph"]

    def test_no_surveyor_no_longer_shares_a_glyph_with_unrun(self, tmp_path):
        """The specific regression the reply names by example: `no-surveyor`
        used to share `○` with `unrun` in both `GLYPH` and `CELL`, told apart
        only by colour. It must now be its own glyph (`◌`) in both."""
        out = self._run(tmp_path)
        assert out["glyph"]["no-surveyor"] == "◌"
        assert out["glyph"]["no-surveyor"] != out["glyph"]["unrun"]
        assert out["cell"]["no-surveyor"]["glyph"] == "◌"
        assert out["cell"]["no-surveyor"]["glyph"] != out["cell"]["unrun"]["glyph"]

    def test_only_one_glyph_table_declares_no_surveyors_glyph(self):
        """`GLYPH` and `CELL` must be the SAME glyph for every state they
        share -- if they ever diverge again, that is a second table by
        another name."""
        app_src = APP_JS.read_text(encoding="utf-8")
        worklist_src = WORKLIST_JS.read_text(encoding="utf-8")
        # Neither file may declare a literal glyph character as an object
        # value any more (e.g. `answered: '✓'`) -- that is precisely the
        # shape of an independent, second glyph-to-meaning mapping. Every
        # glyph now flows in only through `GLYPH_STATES[k].glyph`.
        literal_glyph_value = __import__("re").compile(
            r"""['"]?[\w-]+['"]?\s*:\s*['"][✓∅◐○◌?⚠◔✕⏵·□]['"]"""
        )
        assert not literal_glyph_value.search(_slice(app_src, GLYPH_START, GLYPH_END)), (
            "app.js's GLYPH block still declares a literal glyph -- it must "
            "read every glyph from GLYPH_STATES instead"
        )
        assert not literal_glyph_value.search(_slice(worklist_src, CELL_START, CELL_END)), (
            "worklist.js's CELL block still declares a literal glyph -- it "
            "must read every glyph from GLYPH_STATES instead"
        )

    def test_curate_js_no_longer_declares_its_own_task_glyph_map(self):
        """A fourth consumer, found during G1: `curateRecordHtml` used to
        declare its own `{ done: '✓', failed: '✗', running: '◐', ... }` --
        with its own `◐` for "running", disagreeing with the canonical `◔`.
        Consolidated to route through `factGlyph` instead; this pins that the
        old, independent map does not come back."""
        curate_src = CURATE_JS.read_text(encoding="utf-8")
        assert "running: '◐'" not in curate_src
        assert "failed: '✗'" not in curate_src
        assert "CURATE_TASK_TO_FACT_STATE" in curate_src
