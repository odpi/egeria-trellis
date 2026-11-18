"""FUNNEL-COST-RULINGS §3 / REPORT-RECORD-AND-TWO-CALLS §B (designer,
2026-09-13): keep investigating should schedule the deeper surveys -- it
offers, it does not silently queue. Once per verdict, in the pane, never a
modal, never on abandoned/ignored, recommended/using only when never
measured, the decline recorded on the verdict."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
SHARED_UI = Path(__file__).resolve().parents[2] / "trellis-ui" / "trellis_ui" / "static"  # RE's files shared with EA


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestWhoGetsTheOffer:
    def _run(self, expr, tmp_path):
        app = _app()
        src = app[app.index("function depthOfferApplies("):app.index("async function renderDepthOffer(")]
        mod = tmp_path / "offer.mjs"
        mod.write_text(src + "\nexport { depthOfferApplies };\n")
        out = subprocess.run(["node", "--input-type=module", "-e",
                              f"import {{ depthOfferApplies }} from '{mod.as_uri()}'; console.log(JSON.stringify({expr}));"],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_the_rules(self, tmp_path):
        table = self._run("""[
          depthOfferApplies('investigating', true), depthOfferApplies('tracking', false),
          depthOfferApplies('recommended', false), depthOfferApplies('using', false),
          depthOfferApplies('recommended', true), depthOfferApplies('using', true),
          depthOfferApplies('abandoned', false), depthOfferApplies('ignored', false), depthOfferApplies('undecided', false)]""", tmp_path)
        assert table == [True, True, True, True, False, False, False, False, False]


class TestTheOfferIsAnOfferNotAGate:
    def test_three_buttons_and_the_decline_is_recorded(self):
        app = _app()
        body = app[app.index("async function renderDepthOffer("):app.index("function wireDispositionPicker(")]
        for label in (">Run these in background<", ">Choose which<", ">Not now<"):
            assert label in body
        assert "postDepthOfferOutcome(p.github_url, { outcome, analysisIds: ids, runIds })" in body
        assert "sign in to answer the offer" in body and "recorded locally only" not in body
        assert "finish('declined', [])" in body
        assert "This verdict does not schedule anything." in body
        assert "declared ${esc(declaredWord(c)" in body, "an unpriced analysis renders as the declared word"

    def test_once_per_verdict_and_in_the_pane_after_a_verdict(self):
        app = _app()
        body = app[app.index("async function renderDepthOffer("):app.index("function wireDispositionPicker(")]
        assert "if (!latest || latest.depth_offer) { host.innerHTML = ''; return; }" in body
        picker = app[app.index("function wireDispositionPicker("):app.index("async function loadDispositionPane(")]
        assert "renderDepthOffer(p, slot, { afterVerdict: true })" in picker
        assert "window.confirm" not in body and "window.prompt" not in body   # never a modal

    def test_the_trail_carries_the_outcome(self):
        fmt = (SHARED_UI / "format.js").read_text(encoding="utf-8")
        assert "export function depthOfferText(" in fmt and "depth offered, " in fmt
        assert "depthOfferText(r.depth_offer, esc)" in fmt
