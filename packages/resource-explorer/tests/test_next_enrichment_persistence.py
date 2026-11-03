"""PLAN-FINISH-REPOS.md item 1's done test: 'a curator can record and revisit
an enrichment judgement on a repo, and a judgement whose evidence moved says
so.' `tests/test_enrichment_fields.py` and `TestReviseAfterReload` in it pin
the Python persistence/revisit half through the real HTTP routes. This file
pins the two front-end mechanics that make "evidence moved" real rather than
decorative:

1. `movedSince` actually reacts to `state.enrichmentFacts` changing between
   when a judgement was saved and when the pane re-renders -- not just that
   its timestamp comparison is spelling-safe (already covered by
   `test_next_enrichment_fidelity.py`'s `TestTimestampSpellings`).
2. `evidenceSnapshot` actually reads the same `state.enrichmentFacts` that
   `renderEnrichment` populates from a real API call, so what gets saved as
   a judgement's evidence is live data, not a placeholder.

Executed the same way `test_next_curate_pane.py` and `test_next_rail_states.py`
hold `/next`'s JS to structure without a browser: `movedSince` and
`evidenceSnapshot` are not exported (deliberately -- they are private to the
module's Save flow), so their source is extracted from the concatenated
module and run under node with a minimal `state`/`whenMs` stand-in, rather
than importing the whole module (which needs a DOM and this repo's other
`/static/next/*.js` siblings for its own imports).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
ENRICHMENT_JS = NEXT / "stages" / "enrichment.js"


def _extract(src: str, signature: str) -> str:
    """The text of one top-level function, from its `function NAME(` line up
    to (not including) the next top-level `function` declaration."""
    start = src.index(signature)
    nxt = src.index("\nfunction ", start + len(signature))
    nxt2 = src.index("\nexport function ", start + len(signature))
    end = min(x for x in (nxt, nxt2) if x != -1)
    return src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestMovedSinceReactsToLiveFacts:
    """`field.evidence` is the snapshot taken at save time (analysis_id ->
    last_run_at); `movedSince` must compare it against CURRENT
    `state.enrichmentFacts`, not against itself or a copy frozen at save
    time -- otherwise the flag can never fire."""

    def _run(self, body: str, tmp_path: Path) -> str:
        fmt = tmp_path / "format.mjs"
        fmt.write_text(ENRICHMENT_JS.parent.parent.joinpath("format.js").read_text(encoding="utf-8"), encoding="utf-8")
        script = f"""
import {{ whenMs }} from '{fmt.as_uri()}';
globalThis.whenMs = whenMs;
{body}
"""
        script_path = tmp_path / "run.mjs"
        script_path.write_text(script, encoding="utf-8")
        out = subprocess.run(["node", str(script_path)],
                              capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def _moved_since_fn(self) -> str:
        src = ENRICHMENT_JS.read_text(encoding="utf-8")
        return _extract(src, "function movedSince(field)")

    def test_nothing_moved_when_facts_are_unchanged_since_the_snapshot(self, tmp_path):
        fn = self._moved_since_fn()
        out = self._run(f"""
{fn}
globalThis.state = {{ enrichmentFacts: {{ cve_scan: {{ last_run_at: '2026-09-01T00:00:00Z' }} }} }};
console.log(JSON.stringify(movedSince({{ evidence: {{ cve_scan: '2026-09-01T00:00:00Z' }} }})));
""", tmp_path)
        assert json.loads(out) == []

    def test_an_analysis_that_reran_after_the_judgement_is_flagged(self, tmp_path):
        fn = self._moved_since_fn()
        out = self._run(f"""
{fn}
globalThis.state = {{ enrichmentFacts: {{ cve_scan: {{ last_run_at: '2026-09-05T00:00:00Z' }} }} }};
console.log(JSON.stringify(movedSince({{ evidence: {{ cve_scan: '2026-09-01T00:00:00Z' }} }})));
""", tmp_path)
        assert json.loads(out) == ["cve_scan"]

    def test_only_the_evidence_ids_the_judgement_actually_carried_are_checked(self, tmp_path):
        # A fact that reran but was never part of this judgement's snapshot
        # must not show up -- movedSince is per-judgement, not global.
        fn = self._moved_since_fn()
        out = self._run(f"""
{fn}
globalThis.state = {{ enrichmentFacts: {{
  cve_scan: {{ last_run_at: '2026-09-01T00:00:00Z' }},
  secret_scan: {{ last_run_at: '2026-09-09T00:00:00Z' }},
}} }};
console.log(JSON.stringify(movedSince({{ evidence: {{ cve_scan: '2026-09-01T00:00:00Z' }} }})));
""", tmp_path)
        assert json.loads(out) == []

    def test_a_naive_and_a_zoned_spelling_of_the_same_instant_do_not_false_positive(self, tmp_path):
        fn = self._moved_since_fn()
        out = self._run(f"""
{fn}
globalThis.state = {{ enrichmentFacts: {{ cve_scan: {{ last_run_at: '2026-09-01T00:00:00' }} }} }};
console.log(JSON.stringify(movedSince({{ evidence: {{ cve_scan: '2026-09-01T00:00:00+00:00' }} }})));
""", tmp_path)
        assert json.loads(out) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestEvidenceSnapshotReadsLiveFacts:
    def test_it_snapshots_every_fact_that_has_run(self, tmp_path):
        src = ENRICHMENT_JS.read_text(encoding="utf-8")
        fn = _extract(src, "function evidenceSnapshot()")
        fmt = tmp_path / "format.mjs"
        fmt.write_text((ENRICHMENT_JS.parent.parent / "format.js").read_text(encoding="utf-8"), encoding="utf-8")
        script = f"""
{fn}
globalThis.state = {{ enrichmentFacts: {{
  cve_scan: {{ last_run_at: '2026-09-01T00:00:00Z' }},
  secret_scan: {{ last_run_at: '' }},
  license_classification: {{}},
}} }};
console.log(JSON.stringify(evidenceSnapshot()));
"""
        script_path = tmp_path / "run.mjs"
        script_path.write_text(script, encoding="utf-8")
        out = subprocess.run(["node", str(script_path)],
                              capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        # secret_scan has no last_run_at and license_classification has no
        # field at all -- neither should be snapshotted as evidence, since
        # a snapshot entry claims "this had run by the time of judgement".
        assert json.loads(out.stdout) == {"cve_scan": "2026-09-01T00:00:00Z"}


class TestSaveWiresTheLiveSnapshotIn:
    """Structural pin: the Save handler must call `evidenceSnapshot()` at
    save time for a judgement (not send `{}` or a stale value), and
    `renderEnrichment` must populate `state.enrichmentFacts` from a real API
    call BEFORE building the rows that call `movedSince` off it -- otherwise
    the flag is checked against facts from whatever the previous render
    happened to leave behind."""

    def test_judgement_saves_send_the_live_snapshot(self):
        src = ENRICHMENT_JS.read_text(encoding="utf-8")
        body = src[src.index("host.querySelectorAll('[data-save]')"):src.index("host.querySelectorAll('[data-owner-interim]')")]
        assert "evidence: kind === 'judgement' ? evidenceSnapshot() : {}" in body

    def test_facts_are_fetched_before_the_rows_that_read_them_are_built(self):
        src = ENRICHMENT_JS.read_text(encoding="utf-8")
        render = src[src.index("export async function renderEnrichment("):src.index("export function renderEnrichmentEvidence(")]
        # The fetch is `fetchEnrichmentEvidence` (extracted so Context shares
        # it); it must still be awaited before the rows are built, and it
        # must be what assigns state.enrichmentFacts.
        assert "state.enrichmentFacts = facts" in src[src.index("export async function fetchEnrichmentEvidence("):]
        fetch_at = render.index("await fetchEnrichmentEvidence(slug)")
        rows_at = render.index("JUDGEMENTS.map((d) => fieldRowHtml(d, 'judgement'))")
        assert fetch_at < rows_at, "facts must be loaded before movedSince (via fieldRowHtml) reads them"
