"""ui-batch W35, the D1-e tutorial quote, hover help slice 1 and collapsed result sections slice 1 (2026-11-01):
source-text contracts for the /next JS and the tutorial. Same pattern as test_next_ui_batch_w31_w34.py: no browser.
The harness (frontend-build/test-harness/fs-sections-hover-help.test.mjs) draws the real card for the rendered words.
W36 is a step's words and is tested beside the step (trellis-analytics tests/test_files_derived_steps.py)."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
NEXT = PKG / "resource_explorer" / "web" / "static" / "next"
OD = (NEXT / "admin" / "open_data.js").read_text(encoding="utf-8")
FS = (NEXT / "fs-survey.js").read_text(encoding="utf-8")
APP = (NEXT / "app.js").read_text(encoding="utf-8")
SECTIONS = (NEXT / "sections.js").read_text(encoding="utf-8")
TUTORIAL = (PKG / "docs" / "tutorial-outline.md").read_text(encoding="utf-8")

SEEN = ("not yet published · 26: 3 undecided · 22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's · "
        "1 left out by a confirmed proposal")


def test_w35_the_portal_url_wraps_inside_its_cell():
    cell = re.search(r'<td class="([^"]*)" data-od-portal-url>\$\{esc\(p\.base_url\)\}</td>', OD)
    assert cell and "break-all" in cell.group(1).split() and "font-mono" in cell.group(1).split()
    assert 'data-od-cost><div class="min-w-[12rem]">' in OD                      # W33 kept
    assert '<div class="overflow-x-auto"><table class="w-full text-left" data-rc-id="od-portals">' in OD


def test_tutorial_quotes_the_d1e_sentence_as_seen_and_keeps_the_readers_own_counts():
    step = TUTORIAL[TUTORIAL.index("*Explain (design note D1-e"):]
    step = " ".join(step[:step.index("\n\n")].split())
    assert f'"{SEEN}"' in step and "2026-11-01" in step and "in words like" not in step
    assert "read the words on the screen before writing them in" in step
    g24 = next(line for line in TUTORIAL.splitlines() if line.startswith("| G24 |"))
    assert f'"{SEEN}"' in g24 and "read the words on the screen before writing them in" in g24


def test_hover_help_every_folder_survey_press_carries_its_words():
    # btn(act, label, on, help) renders the title; every call passes a fourth argument from the words table
    assert 'const btn = (act, label, on, help) => `<button type="button" data-fs-act="${act}" ${on ? \'\' : \'disabled\'} title="${esc(help)}"' in FS
    calls = re.findall(r"btn\('([a-z-]+)', .*?, (W\.\w+|canSurvey \? W\.\w+ : W\.\w+)\)", FS)
    assert {act for act, _ in calls} == {"check", "survey", "publish", "measure", "publish-measures", "profile",
                                          "read-egeria", "publish-profiles", "derive", "read-narratives", "match-classes"}
    helps = set(re.findall(r"W\.(\w+Help)\b", FS))
    assert len(helps) >= 12
    for name in helps:
        m = re.search(rf"^  {name}: (['\"])(.+?)\1,$", FS, re.M)
        assert m, f"{name} is a one-line entry in FOLDER_SURVEY_WORDS"
        words = m.group(2)
        assert 20 < len(words) <= 170, f"{name} shows whole ({len(words)})"
        assert not re.search(r"\d{4}-\d{2}-\d{2}T", words)


def test_hover_help_analysis_run_presses_say_what_they_queue():
    assert "export function analysisRunTitle(" in APP
    assert "Queues this one analysis on ${name}. Watch it in Activity; the card redraws when it ends." in APP
    assert "kind === 'filesystem' ? \" Runs over the last walk's kept rows; publishes nothing.\" : ''" in APP
    # the Analyses index row: the title when runnable, the runnable_reason when off (kept)
    assert ("<button data-analysis-run=\"${esc(row.analysis_id)}\" ${row.runnable ? `title=\"${esc(analysisRunTitle())}\"` "
            ": 'disabled title=\"' + esc(row.runnable_reason) + '\"'}") in APP
    # the Enrichment row: the credential reason when off (kept), the title otherwise
    assert ('${selectedCredentialUnreadable() ? `disabled title="${CREDENTIAL_UNREADABLE_TEXT}"` '
            ': `title="${esc(analysisRunTitle())}"`}') in APP


def test_collapsed_sections_are_details_remembered_per_resource():
    assert "export const SECTION_ROWS_OPEN = 8;" in SECTIONS
    assert "`re-next.section.${person}.${slug}.${section}`" in SECTIONS
    for fn in ("readSectionPref", "writeSectionPref", "sectionStartsOpen"):
        assert f"export function {fn}(" in SECTIONS
    assert "try {" in SECTIONS and "catch { return" in SECTIONS                 # never throws
    assert "<details data-fs-section=" in FS and "<summary class=\"cursor-pointer\" data-fs-section-summary>" in FS
    for key in ("files", "measures", "profiles", "classes", "derived"):
        assert f"fold('{key}')" in FS
    assert "derive: 'derived'" in FS                                              # Derive opens what it filled
    assert "writeSectionPref(storage(), me, slug, d.dataset.fsSection, d.open)" in FS
    assert "if (d.open === was) return;" in FS                                    # the page's own open writes nothing
