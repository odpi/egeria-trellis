"""Open data words and bench (Architect's walk W1-W14, 2026-10-29): source-text contracts for the /next JS, plus the
directory price route. Same pattern as test_next_sidebar_list_db_fs.py: no browser."""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
OD = (NEXT / "admin" / "open_data.js").read_text(encoding="utf-8")
WORKLIST = (NEXT / "worklist.js").read_text(encoding="utf-8")
ADMIN = (NEXT / "admin" / "index.js").read_text(encoding="utf-8")


def test_w10_the_bench_never_asks_the_question_catalog_about_open_data():
    assert "KINDS_WITHOUT_QUESTIONS = new Set(['open_data'])" in WORKLIST
    loop = WORKLIST[WORKLIST.index("for (const [k, ms] of groups) {"):]
    assert loop.index("KINDS_WITHOUT_QUESTIONS.has(k)") < loop.index("await getQuestions(")
    assert "No questions are asked of" in WORKLIST             # said as a sentence, not an error
    assert "open_data: ['dataset', 'datasets']" in WORKLIST


def test_w11_the_enrichment_sentence_is_only_said_at_enrichment():
    assert "ctx.stage === 'enrichment' ? ' — by design for Enrichment" in WORKLIST


def test_w1_w2_one_never_run_line_and_no_design_codes():
    assert "Not yet run" in OD
    assert "scout arrives with OD2" not in OD
    assert "(O1)" not in OD and "(O13)" not in OD


def test_w3_a_blank_importer_is_not_worded_as_a_person():
    assert "|| 'not recorded')" not in OD
    assert "was not recorded" in OD


def test_w4_one_cue_for_an_unsettled_license():
    assert "r.licence_check ? `<span" in OD
    assert "<span class=\"text-state-warn\">· ${esc(r.licence_check)}" not in OD


def test_w5_the_directory_price_line_comes_from_the_server():
    assert "8 pages × 1 call" not in OD and "the listing is read once)`" not in OD
    assert "openDataDirectoryPrice" in OD


def test_w6_w8_w12_w13_words():
    assert "cost not declared by the adapter" in OD
    for old in ("Licence needs", ">Licence<", "Licence flags", "dropped on licence"):
        assert old not in OD
    assert "gave no value" in OD
    assert "export function modifiedWords" in OD and "field('Modified', modifiedWords(d.modified)" in OD


def test_w7_the_empty_state_says_why():
    assert "export function noCandidatesWords" in OD
    assert "the portal has no datasets for these terms" in OD and "dropped" in OD


def test_w14_the_open_data_screens_are_visible_entries():
    assert "name: 'Open data', visible: true" in ADMIN
    assert "data-admin-tab" in ADMIN


def test_dcat_ap_registration_help_and_refusal_use_us_spelling_and_say_there_is_no_search():
    import re

    from resource_explorer.open_data.dcat_ap import DcatApAdapter, ProbeRefused
    from resource_explorer.open_data.transport import TransportError

    assert "<option>DCAT-AP</option>" in OD and "DCAT_AP_HELP" in OD
    help_text = re.search(r"DCAT_AP_HELP = '([^']*)'", OD).group(1)
    assert "1 to 3 addresses" in help_text and "has no search" in help_text
    refusal = ""
    try:
        DcatApAdapter.probe("https://nowhere.example", type("T", (), {
            "get_json": lambda self, url, params=None, headers=None: (_ for _ in ()).throw(TransportError("HTTP 404"))})())
    except ProbeRefused as exc:
        refusal = str(exc)
    cost = DcatApAdapter.manifest.cost_note
    for words in (help_text, refusal, cost):
        assert words
        assert not re.search(r"licen[cs]e|colou?r|catalogue|behaviour|organis", words, re.I), words   # US spelling
    assert refusal.endswith(").") and ". " not in refusal      # one sentence
