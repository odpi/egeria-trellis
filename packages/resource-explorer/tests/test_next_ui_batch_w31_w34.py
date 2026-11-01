"""ui-batch W31-W34 and the D1-e tutorial line (the Architect's read-backs of W23-W30, 2026-11-01): source-text
contracts for the /next JS. Same pattern as test_next_open_data_words.py: no browser. The harness
(frontend-build/test-harness/w31-w34-ui-batch.test.mjs) runs the real modules for the rendered words."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
NEXT = PKG / "resource_explorer" / "web" / "static" / "next"
COVERAGE = (NEXT / "stages" / "curate-coverage.js").read_text(encoding="utf-8")
SCOPE = (NEXT / "stages" / "curate-scope.js").read_text(encoding="utf-8")
FORMAT = (NEXT / "format.js").read_text(encoding="utf-8")
OD = (NEXT / "admin" / "open_data.js").read_text(encoding="utf-8")
ADMIN = (NEXT / "admin" / "index.js").read_text(encoding="utf-8")
CSS = (NEXT / "tailwind-next.css").read_text(encoding="utf-8")
TUTORIAL = (PKG / "docs" / "tutorial-outline.md").read_text(encoding="utf-8")


def _stamp_slices(iso: str) -> str:
    """format.js `stamp` on a full ISO stamp: `${s.slice(5, 10)} ${s.slice(11, 16)}`."""
    return f"{iso[5:10]} {iso[11:16]}"


def test_w31_coverage_reads_back_in_the_one_time_form_never_the_raw_as_of():
    assert "read back ${esc(result.as_of)}" not in COVERAGE
    assert "read back ${esc(stamp(result.as_of))}" in COVERAGE
    assert "import { stamp } from '/static/next/format.js';" in COVERAGE
    # one helper, shared: curate-scope imports it rather than keeping its own copy
    assert "export function stamp(" in FORMAT
    assert "const stamp =" not in SCOPE and re.search(r"import \{[^}]*\bstamp\b[^}]*\} from '/static/next/format\.js'", SCOPE)
    assert "`${s.slice(5, 10)} ${s.slice(11, 16)}`" in FORMAT
    assert _stamp_slices("2026-10-25T23:47:39.704588") == "10-25 23:47"


def test_w32_modified_is_the_sources_date_with_the_year():
    body = OD[OD.index("export function modifiedWords"):OD.index("export function bytes")]
    assert "toLocaleDateString" not in body and "localTime" not in body       # no "Oct 17, 2:31 PM"
    assert "text.slice(0, 10)" in body                                          # a date-time is cut to its date
    assert "return show(NOT_REPORTED)" in body                                  # absence stays the words, never blank
    assert "field('Modified', modifiedWords(d.modified)" in OD                  # the dataset page says it the same way


def test_w33_the_cost_note_keeps_a_readable_width_and_all_its_words():
    cell = OD[OD.index("data-od-cost>"):OD.index("</td>", OD.index("data-od-cost>"))]
    assert '<div class="min-w-[12rem]">' in cell
    assert "esc(p.cost.cost_note)" in cell                                      # the note's words, not shortened
    assert '<div class="overflow-x-auto"><table class="w-full text-left" data-rc-id="od-portals">' in OD
    assert ".min-w-\\[12rem\\]{" in CSS


def test_w34_only_the_results_pane_scrolls_sideways():
    assert '<div data-od-results class="overflow-x-auto"></div>' in OD
    assert '<table class="w-full min-w-[56rem] table-fixed text-left" data-rc-id="od-candidates">' in OD
    # the dialog is a flex item: min-w-0 lets it shrink below the table's min width, so the overlay does not scroll
    assert '<div class="mt-[3vh] w-full min-w-0 max-w-[920px]' in ADMIN
    assert ".min-w-0{" in CSS


def test_tutorial_d1e_is_merged_and_the_words_are_still_to_be_read_on_screen():
    assert "Pending, once D1-e merges" not in TUTORIAL and "Pending D1-e" not in TUTORIAL
    step = TUTORIAL[TUTORIAL.index("*Explain (design note D1-e"):]
    step = step[:step.index("\n\n")]
    assert "#962" in step and "read the words on the" in step.replace("\n     ", " ")
    g24 = next(line for line in TUTORIAL.splitlines() if line.startswith("| G24 |"))
    assert "#962" in g24 and "read the words on the screen before writing them in" in g24
