"""Pins from the designer's 2026-09-12 review, section 4 -- the journal and
Disposition: one trail format for one fact, a real plural, the reason in
ink, the landing named by the list's name, and the audience as the whole
vocabulary."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


class TestOneTrailFormat:
    def test_both_views_use_the_shared_verdict_line(self):
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        wl = (NEXT / "worklist.js").read_text(encoding="utf-8")
        for src, name in ((app, "app.js"), (wl, "worklist.js")):
            assert "verdictLineHtml" in src and "changedTimesHtml" in src, f"{name} renders its own trail"
            assert "time(s)" not in src, f"{name} still says time(s)"
        # and neither carries a second hand-rolled layout of the same fact
        assert "block text-provenance text-ink-muted\">${\n            r.decided_at" not in wl

    def test_the_work_list_view_keeps_the_sub_tab_rail(self):
        wl = (NEXT / "worklist.js").read_text(encoding="utf-8")
        assert "ctx.subTabs" in wl and "← back to questions" in wl
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        assert re.search(r"subTabs: SUB_TABS(\.filter\(\(t\) => !t\.stageTab\))?\.map", app)

    def test_the_journal_note_names_the_list_and_stays(self):
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        i = app.index("const where = (out.work_lists")
        # renderJournalEntries gained an `entityType` param 2026-09-22
        # (Backlog.md, "Disposition is NOT fixed here") -- match the call
        # regardless of its arguments rather than the literal old text.
        body = app[i:app.index("await renderJournalEntries(slug", i)]
        assert "w.name || w.work_list" in body
        assert "setTimeout" not in body, "the only record the writer gets must not self-destruct"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestTheFormatter:
    def _run(self, expr: str, tmp_path: Path):
        mod = tmp_path / "format.mjs"
        mod.write_text((NEXT / "format.js").read_text(encoding="utf-8"), encoding="utf-8")
        script = (f"import {{ verdictLineHtml, changedTimesHtml }} from '{mod.as_uri()}';\n"
                  f"const esc = (s) => String(s);\nconsole.log(JSON.stringify({expr}));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_changed_times_is_a_real_plural(self, tmp_path):
        assert self._run("[changedTimesHtml(0), changedTimesHtml(1), changedTimesHtml(3)]", tmp_path) == [
            "", "changed once", 'changed <span class="tnum">3</span> times']

    def test_the_reason_sits_in_ink_beside_the_verdict(self, tmp_path):
        html = self._run(
            "verdictLineHtml({disposition:'tracking', decided_at:'2026-08-11T10:00:00Z', decided_by:'dan', reason:'worth watching'}, esc)",
            tmp_path)
        assert html.startswith('<span class="text-ink">tracking</span>')
        assert '(2026-08-11)' in html and '· dan' in html
        assert '<span class="text-ink">worth watching</span>' in html
        assert 'text-ink-muted' not in html


class TestPromotionSelectionInTheShell:
    """REVIEW-PROMOTION (2026-09-12): the half of promotion that lives in JS
    had no tests. What a source read can hold it to; the composition itself
    was exercised in the browser."""

    def _sel(self):
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        return app[app.index("function facetsHtml("):app.index("async function openMembers(")]

    def test_a_typed_name_is_tracked_not_inferred_from_the_string(self):
        body = self._sel()
        assert "let touched = false;" in body and "nameEl.addEventListener('input'" in body
        assert "keep.startsWith(" not in body

    def test_hand_picking_refines_the_facet_rather_than_erasing_it(self):
        body = self._sel()
        assert "plus ${added} added by hand" in body and "less ${removed} removed by hand" in body
        assert "facet = ''; render();" not in body

    def test_the_run_date_is_the_payloads_not_the_browsers(self):
        body = self._sel()
        assert "const runAt = data.run_at || '';" in body
        assert "enrichmentFacts" not in body

    def test_facet_labels_are_whole_and_counts_say_what_they_count(self):
        body = self._sel()
        assert ".split(' ')[0]" not in body
        assert "counts are of the" in body and "${g.count}" in body

    def test_house_rules(self):
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        members = app[app.index("async function openMembers("):app.index("function rowKey(i)")]
        assert "accent-accent" not in members, "gold is not a fill on 200 checkboxes"
        assert "this list is a tree" in self._sel()
        assert "nothing stored by" in members


class TestDispositionRulings:
    """FUNNEL-COST-RULINGS §4 and §5 (designer, 2026-09-13). Verified in a
    browser: 'Reversing using → abandoned — why?' with record / keep, an
    empty reason refused, cancel restoring; the DBs sidebar reading the
    sentence with no facet chips."""

    def test_a_reason_is_asked_only_when_a_terminal_verdict_is_reversed(self):
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        body = app[app.index("function wireDispositionPicker("):app.index("async function loadDispositionPane(")]
        assert "const TERMINAL_DISPOSITIONS = new Set(['using', 'abandoned', 'ignored']);" in app
        assert "if (!TERMINAL_DISPOSITIONS.has(current)) { await commit(value); return; }" in body   # first verdicts never ask
        assert "Reversing <em>${esc(current)}</em> → <em>${esc(value)}</em> — why?" in body
        assert "a reversal needs a reason" in body and "setDisposition(p.github_url, value, reason)" in body

    def test_a_non_repo_now_renders_real_disposition_chips_not_the_old_apology(self):
        """SUPERSEDED (re/multiselect-groups-dbfs, 2026-09-23): FUNNEL-COST-
        RULINGS §5 (docs/design-notes/exchange/REPLY-FUNNEL-COST.md) was correct for
        2026-09-13's mechanism -- a database/filesystem genuinely had no
        disposition, so an empty facet row would have misrepresented a
        mechanism gap as "nothing is dispositioned". That mechanism gap
        closed on 2026-09-22 (PR #223, disposition generalized to
        (entity_type, entity_slug)) and the sidebar facet row was the one
        piece of /next that hadn't caught up -- it kept rendering the "no
        verdict can be recorded" apology over a disposition that had, since
        PR #223, actually been recordable the whole time. The project owner
        confirmed (this PR) that disposition facets are wanted for databases/
        filesystems the same as repos, so the apology is gone and the real
        chip row (counts, marks, the `mark as…` bulk action) now renders for
        every resource type -- see REPLY-FUNNEL-COST.md's own addendum."""
        app = (NEXT / "app.js").read_text(encoding="utf-8")
        assert "No verdict can be recorded for a" not in app
        assert "dispositions exist for repositories only" not in app
        # The facet row itself is no longer gated to state.resourceType ===
        # 'repo' -- it renders unconditionally in renderSidebar().
        start = app.index("function renderSidebar() {")
        end = app.index("\nfunction bindSidebar() {", start)
        body = app[start:end]
        assert 'data-facet="all"' in body
        assert "state.resourceType === 'repo' ? `" not in body
