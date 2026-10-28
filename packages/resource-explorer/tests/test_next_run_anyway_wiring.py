"""PI-063: every user-started single-analysis run in /next recognises the server's 'skipped' answer.

Source-text pin (the closures are not reachable from the render harness): each call site must test
isFreshnessSkip(...) before it reads activity_id off the answer, and the forced re-run must pass force.
"""
from __future__ import annotations

from pathlib import Path

APP = (Path(__file__).resolve().parents[1]
       / "resource_explorer/web/static/next/app.js").read_text()


def _between(start: str, end: str) -> str:
    i = APP.index(start)
    return APP[i:APP.index(end, i)]


def test_enrichment_map_run_offers_run_anyway():
    body = _between("data-run-enrichment-analysis", "By analysis, on Enrichment")
    assert "isFreshnessSkip(started)" in body and "{ force: true }" in body


def test_survey_index_run_offers_run_anyway():
    body = _between("const startRun = async", "host.querySelectorAll('[data-analysis-run]')")
    assert "isFreshnessSkip(started)" in body
    assert "{ force }" in body and "startRun(b, aid, null, true)" in body


def test_question_rerun_offers_run_anyway_foreground_and_background():
    body = _between("async function rerun(", "One measure, rendered for reading")
    assert body.count("isFreshnessSkip(") == 2
    assert body.count("force: true") == 2
    skip = body.index("isFreshnessSkip(started)")
    assert skip < body.index("started.activity_id"), "the skip must be tested before activity_id is read"
