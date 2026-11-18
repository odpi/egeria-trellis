"""Pins from the designer's 2026-09-12 review of the Enrichment pane.

Two things a Python suite can hold the /next shell to without a browser:
that every human-supplied catalog question reaches the Enrichment stage,
and that the timestamp comparisons behind "evidence moved" survive the
three spellings the registry writes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
SHARED_UI = Path(__file__).resolve().parents[2] / "trellis-ui" / "trellis_ui" / "static"  # RE's files shared with EA


class TestEveryHumanQuestionReachesEnrichment:
    """The pane's heading says "the catalog's own questions for a person,
    below". Two of the seven were stage `Analysis` only, so exact phase
    matching kept them off the pane that promised them."""

    def test_all_human_supplied_rows_are_in_the_enrichment_stage(self):
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        human = [q for q in get_questions("repo") if (q.get("answering") or {}).get("kind") == "human"]
        assert human, "no human-supplied rows at all -- the catalog changed shape"
        enrichment = {q["question"] for q in get_questions("repo", phase="Enrichment")}
        missing = [q["question"] for q in human if q["question"] not in enrichment]
        assert missing == [], f"human-supplied questions a person cannot reach on the Enrichment pane: {missing}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestTimestampSpellings:
    """`movedSince` and the rail's `fresh` compared raw ISO strings. The
    registry writes `Z`, `+00:00` and NAIVE stamps (datetime.utcnow()), and
    Date.parse reads a naive one as local time, so the flag fired or failed
    on the suffix. Every comparison now goes through `whenMs`, which reads a
    naive stamp as UTC."""

    def _run(self, expr: str, tmp_path: Path):
        # The shell's modules are .js with no package.json, which node reads
        # as CommonJS; a .mjs copy makes the import unambiguous on any node.
        mod = tmp_path / "format.mjs"
        mod.write_text((SHARED_UI / "format.js").read_text(encoding="utf-8"), encoding="utf-8")
        script = f"import {{ whenMs, ago }} from '{mod.as_uri()}';\nconsole.log(JSON.stringify({expr}));"
        out = subprocess.run(["node", "--input-type=module", "-e", script],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_the_three_spellings_of_one_instant_are_equal(self, tmp_path):
        z, plus, naive = self._run(
            "[whenMs('2026-09-12T01:57:55.332303Z'), whenMs('2026-09-12T01:57:55.332303+00:00'), "
            "whenMs('2026-09-12T01:57:55.332303')]", tmp_path)
        assert z == plus == naive

    def test_a_later_naive_stamp_is_later_than_an_earlier_zoned_one(self, tmp_path):
        # 10s apart; a string compare would put the zoned one "later" because
        # '+' sorts above the digits, and a local-time parse could go either way.
        assert self._run("whenMs('2026-09-12T01:58:05') > whenMs('2026-09-12T01:57:55+00:00')", tmp_path) is True

    def test_no_raw_string_comparison_of_last_run_at_survives(self):
        src = (NEXT / "app.js").read_text(encoding="utf-8")
        import re
        raw = re.findall(r"last_run_at\s*[<>]\s*(?!whenMs)", src) + re.findall(r"(?<!whenMs\()\bnow > at\b", src)
        assert raw == [], f"timestamp compared as a string, not an instant: {raw}"

    def test_all_shell_modules_parse(self, tmp_path):
        # stages/*.js added in the PLAN-FINISH-REPOS.md Part 2 §1 split --
        # discovered rather than named, so a new stage module is covered
        # for free instead of needing this list remembered too.
        shell_modules = ["app.js", "worklist.js", "format.js", "feedback.js"]
        shell_modules += [f"stages/{p.name}" for p in sorted((NEXT / "stages").glob("*.js"))]
        for f in shell_modules:
            mod = tmp_path / Path(f).name.replace(".js", ".mjs")
            mod.write_text((NEXT / f).read_text(encoding="utf-8"), encoding="utf-8")
            r = subprocess.run(["node", "--check", str(mod)], capture_output=True, text=True)
            assert r.returncode == 0, f"{f}: {r.stderr[:400]}"


class TestTheLicenceConfirmIsGated:
    """`proposedFrom` offered whatever the headline said, and "No license
    detected on this repository." is a measured finding with a headline, so
    the row offered that sentence to confirm into the licence field. The
    offer now keys on the `license_risk_tier` finding's label: `none` (no
    licence, or nothing examined) offers nothing."""

    def test_the_offer_reads_the_tier_finding_not_the_headline(self):
        # proposedFrom/renderEnrichment moved to stages/enrichment.js in the
        # PLAN-FINISH-REPOS.md Part 2 §1 split.
        src = (NEXT / "stages" / "enrichment.js").read_text(encoding="utf-8")
        body = src[src.index("function proposedFrom("):src.index("function renderEnrichment(")]
        assert "license_risk_tier" in body and "'none'" in body
        assert "f.headline ||" not in body.split("const raw")[0], "the gate must run before the headline is read"
