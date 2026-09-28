"""REPLY-BLANK-RAIL-AND-LIST-ANSWERS (designer, 2026-09-12, read against
19f3733): the evidence rail had three writers, a guard that read a
preference instead of the DOM, an early return that rendered failure as
blank, and a candidate button that counted answer lines. The rules that
make the class of bug impossible, pinned at the source; the behaviour was
verified in a browser (guard on a closed DOM, last click wins, the frame
names what and for what)."""
from __future__ import annotations

import re
from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    """app.js plus every stages/*.js module plus chat.js, concatenated --
    see the identical helper's docstring in test_next_component_review.py.
    `renderEnrichmentEvidence` moved to stages/enrichment.js in the
    PLAN-FINISH-REPOS.md Part 2 §1 split; the chat-turn-list rendering,
    `listSources`/`listSentences`, and the rail-scope helpers moved to
    next/chat.js in the item 9 extraction (ASSESSMENT-CHAT.md /
    ITEM-9-CHAT-IMPLEMENTED.md) -- `openMembers`/`showEvidence`/
    `railFrame` stayed in app.js (shared with the Questions-checklist's own
    row-promotion path), so a test that spans both must read both files."""
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    src += "\n" + (NEXT / "chat.js").read_text(encoding="utf-8")
    return src


class TestTheRailRules:
    def test_writers_guard_on_the_dom_not_the_preference(self):
        app = _app()
        assert "function railIsShowing()" in app and "classList.contains('rail-closed')" in app
        for fn in ("function showEvidence(", "async function openMembers(", "function renderEnrichmentEvidence("):
            body = app[app.index(fn):app.index(fn) + 1500]
            assert "ensureRailShowing()" in body, f"{fn} does not open the rail it writes into"
            assert "if (!railIsOpen()) setRailOpen(true)" not in body, f"{fn} still guards on localStorage"

    def test_every_terminal_state_of_evidence_is_a_sentence(self):
        app = _app()
        body = app[app.index("function showEvidence("):app.index("Boot\n", app.index("function showEvidence("))]
        assert "if (!out || !env || env === 'loading' || env.__error) return;" not in body
        for phrase in ("Nothing was requested", "Still reading", "failed to read", "No facts on this envelope"):
            assert phrase in body, f"missing sentence: {phrase}"

    def test_three_writers_one_slot_one_ticket(self):
        app = _app()
        assert "let railTicket = 0;" in app
        members = app[app.index("async function openMembers("):app.index("async function openMembers(") + 1400]
        assert "const ticket = railClaim();" in members
        assert members.count("railStale(ticket)") >= 2, "a stale ticket must stand down on both the error and success paths"
        for fn in ("function showEvidence(", "function renderEnrichmentEvidence("):
            assert "railClaim();" in app[app.index(fn):app.index(fn) + 900]

    def test_the_frame_names_what_and_for_what(self):
        app = _app()
        assert "function railFrame(kind, forWhat, bodyHtml" in app
        members = app[app.index("async function openMembers("):app.index("async function openMembers(") + 3000]
        assert "for <span class=\"font-mono\">${esc(slug)}</span>" in members


class TestTheCandidateHeuristicIsGone:
    def test_no_button_counts_answer_lines(self):
        app = _app()
        assert "function listCandidates(" not in app and "function showCandidates(" not in app
        assert "Open as candidates" not in re.sub(r"/\*.*?\*/|^\s*//.*$|^\s*\*.*$", "", app, flags=re.S | re.M)
        assert "l.length < 80" not in app

    def test_a_list_answer_opens_the_member_tree_it_was_answered_from(self):
        app = _app()
        assert "function listSources(body)" in app
        assert "p.role === 'evidence' && MEMBER_LISTED.has(p.key)" in app
        assert "data-list-source" in app and "openMembers({ slug, analysisId: b.dataset.listSource" in app
        assert "if (turn.listSources && turn.listSources.length) return 'list';" in app


class TestTheListSentence:
    """REPLY-BLANK-RAIL §2: the rail says the whole count and what the
    model saw, from the compiler's manifest, and the pane holds the list.
    Pinned by extracting the two pure functions and running them in node on
    a manifest of the compiler's shape (context_compile._list_extents)."""

    def _run(self, expr, tmp_path):
        import json, shutil, subprocess
        import pytest
        if shutil.which("node") is None:
            pytest.skip("node not installed")
        # listSources/listSentences/listSentenceHtml moved to next/chat.js in
        # the item 9 extraction (ITEM-9-CHAT-IMPLEMENTED.md) -- was app.js.
        app = (NEXT / "chat.js").read_text(encoding="utf-8")
        start = app.index("const MEMBER_LISTED = new Set(")
        end = app.index("function renderTurnList(")
        src = app[start:end]
        mod = tmp_path / "lists.mjs"
        mod.write_text("const esc = (s) => String(s); const icon = (n) => `<svg data-icon='${n}'/>`;\n" + src
                       + "\nexport { listSources, listSentences, listSentenceHtml, evidenceFooterListsHtml };\n")
        script = (f"import {{ listSources, listSentences, listSentenceHtml, evidenceFooterListsHtml }} "
                  f"from '{mod.as_uri()}';\nconsole.log(JSON.stringify({expr}));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    BODY = {"compiled": {"manifest": {
        "packed": [{"key": "dependency_analysis", "role": "evidence", "rung": "SUMMARY"},
                   {"key": "instructions", "role": "instructions", "rung": "FULL"},
                   {"key": "repository_health", "role": "evidence", "rung": "FULL"}],
        "lists": {"dependency_analysis": {"by_ecosystem.python": {"total": 32, "shown": {"FULL": 32, "SUMMARY": 10}}},
                  "repository_health": {}},
    }}}

    def test_the_sentence_carries_the_total_and_what_the_model_saw(self, tmp_path):
        ls = self._run(f"listSentences({__import__('json').dumps(self.BODY)})", tmp_path)
        assert ls == [{"key": "dependency_analysis", "field": "by_ecosystem", "total": 32, "shown": 10, "parts": 1,
                       "rung": "SUMMARY", "members": True}]
        html = self._run(f"listSentenceHtml(listSentences({__import__('json').dumps(self.BODY)})[0], 'p')", tmp_path)
        assert '<span class="tnum">32</span> dependencies · in <span class="tnum">1</span> ecosystem' in html
        assert '<span class="tnum">10</span> shown to the model</span>' in html and "at summary" not in html
        assert 'open the full list' in html and 'data-list-source="dependency_analysis"' in html
        assert '›' not in html and 'chevron-right' in html

    def test_a_readerless_section_is_not_named_in_the_footer_at_all(self, tmp_path):
        """Corrected 2026-09-25 on review of the first fix
        (REVIEW-SURVEY-PANE-285.md): the footer lists only lists that exist
        to open. A single readerless section used to get its own "No list to
        open" sentence -- that sentence is gone too now, not just collapsed,
        because a footer that lists what exists has no business enumerating
        what doesn't, one key or twelve. The absence moves to sourceLine()'s
        one-line provenance sentence instead (tested below)."""
        import json
        body = {"compiled": {"manifest": {"packed": [{"key": "security_scan", "role": "evidence", "rung": "FULL"}],
                                          "lists": {"security_scan": {"findings": {"total": 3, "shown": {"FULL": 3, "SUMMARY": 3}}}}}}}
        html = self._run(f"evidenceFooterListsHtml(listSentences({json.dumps(body)}), 'p')", tmp_path)
        assert html == ""

    def test_several_readerless_sections_are_also_not_named_in_the_footer(self, tmp_path):
        """Live-reproduced 2026-09-25 (REVIEW-SURVEY-PANE-285.md): a
        database's compiled evidence packs many sections with list-shaped
        fields and NONE of them has a member reader (MEMBER_LISTED is
        repo-shaped analyses only), so the per-section fallback rendered a
        dozen near-identical "No list to open" lines for one answer. The
        first fix collapsed that into one combined line naming every key --
        still a wall, per review; the footer now renders nothing for any of
        them, at any count."""
        import json
        body = {"compiled": {"manifest": {
            "packed": [{"key": "coverage_signals", "role": "evidence", "rung": "FULL"},
                       {"key": "subject_signals", "role": "evidence", "rung": "FULL"},
                       {"key": "grain_determination", "role": "evidence", "rung": "FULL"}],
            "lists": {"coverage_signals": {"gaps": {"total": 2, "shown": {"FULL": 2, "SUMMARY": 2}}},
                      "subject_signals": {"terms": {"total": 4, "shown": {"FULL": 4, "SUMMARY": 4}}},
                      "grain_determination": {"grains": {"total": 3, "shown": {"FULL": 3, "SUMMARY": 3}}}},
        }}}
        html = self._run(f"evidenceFooterListsHtml(listSentences({json.dumps(body)}), 'p')", tmp_path)
        assert html == ""

    def test_a_readerful_section_still_renders_even_alongside_readerless_ones(self, tmp_path):
        import json
        body = {"compiled": {"manifest": {
            "packed": [{"key": "dependency_analysis", "role": "evidence", "rung": "FULL"},
                       {"key": "coverage_signals", "role": "evidence", "rung": "FULL"}],
            "lists": {"dependency_analysis": {"by_ecosystem.python": {"total": 5, "shown": {"FULL": 5, "SUMMARY": 5}}},
                      "coverage_signals": {"gaps": {"total": 2, "shown": {"FULL": 2, "SUMMARY": 2}}}},
        }}}
        html = self._run(f"evidenceFooterListsHtml(listSentences({json.dumps(body)}), 'p')", tmp_path)
        assert "dependency_analysis" in html and "data-list-source" in html
        assert "No list to open" not in html and "coverage_signals" not in html

    def test_a_manifest_without_lists_falls_back_to_the_bare_link(self, tmp_path):
        body = {"compiled": {"manifest": {"packed": [{"key": "cve_scan", "role": "evidence", "rung": "FULL"}]}}}
        import json
        assert self._run(f"listSentences({json.dumps(body)})", tmp_path) == []
        assert self._run(f"listSources({json.dumps(body)})", tmp_path) == ["cve_scan"]


class TestSourceLineCarriesTheAbsenceFact:
    """The footer names only lists that exist (above); the fact that NONE do
    moves to the answer's own one-line provenance sentence instead, said
    once and generically -- not per key, not combined. REVIEW-SURVEY-
    PANE-285.md's correction to the first fix's combined footer line."""

    def _run(self, expr, tmp_path):
        import json, shutil, subprocess
        import pytest
        if shutil.which("node") is None:
            pytest.skip("node not installed")
        app = (NEXT / "chat.js").read_text(encoding="utf-8")
        start = app.index("function sourceLine(")
        end = app.index("function renderTurnList(")
        src = app[start:end]
        mod = tmp_path / "sourceline.mjs"
        mod.write_text("const esc = (s) => String(s); const icon = (n) => `<svg data-icon='${n}'/>`;\n" + src
                       + "\nexport { sourceLine };\n")
        script = f"import {{ sourceLine }} from '{mod.as_uri()}';\nconsole.log(JSON.stringify({expr}));"
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_no_readable_lists_at_all_adds_the_absence_clause(self, tmp_path):
        import json
        body = {"compiled": {"manifest": {
            "packed": [{"key": "coverage_signals", "role": "evidence", "rung": "FULL"}],
            "lists": {"coverage_signals": {"gaps": {"total": 2, "shown": {"FULL": 2, "SUMMARY": 2}}}},
        }}}
        line = self._run(f"sourceLine({json.dumps(body)})", tmp_path)
        assert "no evidence lists were available for this question" in line

    def test_a_readable_list_present_does_not_add_the_clause(self, tmp_path):
        import json
        body = {"compiled": {"manifest": {
            "packed": [{"key": "dependency_analysis", "role": "evidence", "rung": "FULL"}],
            "lists": {"dependency_analysis": {"by_ecosystem.python": {"total": 5, "shown": {"FULL": 5, "SUMMARY": 5}}}},
        }}}
        line = self._run(f"sourceLine({json.dumps(body)})", tmp_path)
        assert "no evidence lists were available" not in line

    def test_no_lists_in_the_manifest_at_all_does_not_add_the_clause_either(self, tmp_path):
        """Nothing list-shaped was packed at all -- a different, unrelated
        fact from "lists existed but none had a reader" -- so this clause,
        specific to the latter, must not fire here."""
        import json
        body = {"compiled": {"manifest": {"packed": [{"key": "cve_scan", "role": "evidence", "rung": "FULL"}]}}}
        line = self._run(f"sourceLine({json.dumps(body)})", tmp_path)
        assert "no evidence lists were available" not in line


class TestTheRailScopeFollowsTheSelection:
    """The rail header read "scoped to amundsen" under a pane showing
    egeria_python (owner's screenshots, 2026-09-13): renderRail() ran at
    boot and on clear only. The scope line is its own element, updated on
    every path that changes the selection."""

    def test_every_selection_change_updates_the_scope_line(self):
        app = _app()
        assert 'id="rail-scope"' in app and "function renderRailScope()" in app
        i = app.index("state.selectedSlug = b.dataset.slug;")
        assert "renderRailScope();" in app[i:i + 200]
        for anchor in ("if (state.selectedSlug === slug) state.selectedSlug = null;",
                       "state.selectedSlug = state.projects[0]?.slug || null;"):
            j = app.index(anchor)
            assert "renderRailScope();" in app[j:j + 260], anchor
