"""The Curate catalogue-scope section: its state-derived 'saved in RE / catalogued in Egeria'
marker (slice B; a constant until then) and its one-line collapse (open until a scope is declared, collapsed after, the
person's choice remembered per person per database, a real button, works without storage).

Two layers: the pure logic in stages/scope-sources.js is run under node (skipped when node
is absent, like the other node-backed tests); the wiring in curate-scope.js is pinned by
reading its source. The route-level half (the line built from the scope route's own JSON)
lives in test_catalogue_scope.py beside the fixtures that build a declared scope."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
SOURCES = NEXT / "stages" / "scope-sources.js"
SCOPE_JS = NEXT / "stages" / "curate-scope.js"
# node treats a bare .js as CommonJS (there is no package.json "type"), so run a copy as .mjs
_MJS = Path(tempfile.mkdtemp(prefix="re-scope-sources-")) / "scope-sources.mjs"
_MJS.write_text(SOURCES.read_text(encoding="utf-8"), encoding="utf-8")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    out = subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import * as m from '{_MJS.as_uri()}'; console.log(JSON.stringify({expr}));"],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


DECLARED = ("{declared:{declared:true,by:'dan',at:'2026-10-04T08:00:00'},"
            "counts:{schemas_catalogue:7,schemas_offered:29},"
            "survey:{state:'measured',schema_count:29,table_count:266}}")


@needs_node
class TestTheOneLine:
    def test_the_full_line(self):
        assert _run(f"m.scopeCollapsedText({DECLARED})") == (
            "Your scope: 7 of 29 schemas known to RE · declared by dan 10-04 · "
            "Egeria's latest survey covers 29 schemas, 266 tables")

    def test_nothing_declared_gives_no_line(self):
        assert _run("m.scopeCollapsedText({declared:{declared:false},counts:{}})") == ""

    def test_an_unavailable_survey_omits_the_clause_and_never_shows_zero(self):
        for survey in ("{state:'not_measured'}", "{}", "{state:'measured'}", "{state:'not_measured',schema_count:29}"):
            line = _run(f"m.scopeCollapsedText({{declared:{{declared:true,by:'dan',at:'2026-10-04'}},"
                        f"counts:{{schemas_catalogue:7,schemas_offered:29}},survey:{survey}}})")
            assert line == "Your scope: 7 of 29 schemas known to RE · declared by dan 10-04", (survey, line)

    def test_when_the_two_counts_differ_the_line_says_which_is_which(self):
        line = _run("m.scopeCollapsedText({declared:{declared:true,by:'dan',at:'2026-10-04'},"
                    "counts:{schemas_catalogue:2,schemas_offered:8},survey:{state:'measured',schema_count:29,table_count:266}})")
        assert line == ("Your scope: 2 of 8 schemas known to RE \u00b7 declared by dan 10-04 \u00b7 "
                        "Egeria's latest survey covers 29 schemas, 266 tables")

    def test_a_clause_is_omitted_only_when_its_own_figure_is_missing(self):
        base = "Your scope: 2 of 8 schemas known to RE \u00b7 declared by dan 10-04"
        for survey, tail in (("{state:'measured',schema_count:29}", " \u00b7 Egeria's latest survey covers 29 schemas"),
                             ("{state:'measured',table_count:266}", " \u00b7 Egeria's latest survey covers 266 tables"),
                             ("{state:'measured'}", ""), ("{state:'not_measured',schema_count:29}", "")):
            line = _run("m.scopeCollapsedText({declared:{declared:true,by:'dan',at:'2026-10-04'},"
                        f"counts:{{schemas_catalogue:2,schemas_offered:8}},survey:{survey}}})")
            assert line == base + tail, (survey, line)

    def test_a_zero_that_was_measured_is_kept_but_a_missing_count_is_not_a_zero(self):
        z = _run("m.scopeCollapsedText({declared:{declared:true,by:'dan',at:'2026-10-04'},"
                 "counts:{schemas_catalogue:0,schemas_offered:5},survey:{}})")
        assert z.startswith("Your scope: 0 of 5 schemas known to RE")
        gone = _run("m.scopeCollapsedText({declared:{declared:true,by:'dan',at:'2026-10-04'},counts:{},survey:{}})")
        assert gone == "Your scope · declared by dan 10-04" and "schemas" not in gone


@needs_node
class TestOpenOrCollapsed:
    def test_open_until_declared_collapsed_after(self):
        assert _run("[m.scopeStartsOpen({declared:{declared:false}},''), m.scopeStartsOpen({declared:{declared:true}},'')]") == [True, False]

    def test_a_remembered_open_wins_once_declared(self):
        assert _run("m.scopeStartsOpen({declared:{declared:true}},'open')") is True
        assert _run("m.scopeStartsOpen({declared:{declared:true}},'collapsed')") is False

    def test_a_stored_collapse_does_not_take_effect_until_a_scope_is_declared(self):
        assert _run("[m.scopeStartsOpen({declared:{declared:false}},'collapsed'), m.scopeStartsOpen({},'collapsed'),"
                    " m.scopeStartsOpen({declared:{declared:false}},'open')]") == [True, True, True]


@needs_node
class TestRememberedPerPersonPerDatabase:
    STORE = "(()=>{const d={};return {getItem:k=>(k in d?d[k]:null),setItem:(k,v)=>{d[k]=v},d}})()"

    def test_round_trip_and_the_key_has_person_and_database(self):
        r = _run(f"(()=>{{const s={self.STORE};"
                 "const w=m.writeScopePref(s,'alice','db1',true);"
                 "return [w,m.readScopePref(s,'alice','db1'),m.readScopePref(s,'bob','db1'),"
                 "m.readScopePref(s,'alice','db2'),Object.keys(s.d)];})()")
        assert r[0] is True and r[1] == "open" and r[2] == "" and r[3] == ""
        assert len(r[4]) == 1 and "alice" in r[4][0] and "db1" in r[4][0]

    def test_a_collapse_is_stored_and_read_back(self):
        r = _run(f"(()=>{{const s={self.STORE};m.writeScopePref(s,'a','d',false);return m.readScopePref(s,'a','d');}})()")
        assert r == "collapsed"

    def test_signed_out_stores_nothing(self):
        r = _run(f"(()=>{{const s={self.STORE};return [m.writeScopePref(s,'','d',true),Object.keys(s.d).length];}})()")
        assert r == [False, 0]

    def test_a_throwing_or_missing_storage_never_breaks_the_page(self):
        boom = "{getItem(){throw new Error('blocked')},setItem(){throw new Error('blocked')}}"
        assert _run(f"[m.readScopePref({boom},'a','d'), m.writeScopePref({boom},'a','d',true),"
                    "m.readScopePref(null,'a','d'), m.writeScopePref(undefined,'a','d',true)]") == ["", False, "", False]

    def test_garbage_in_storage_is_ignored(self):
        assert _run("m.readScopePref({getItem:()=>'banana'},'a','d')") == ""


class TestTheWiring:
    """Source-reading pins on curate-scope.js (the DOM itself was not driven here)."""

    def _js(self):
        return SCOPE_JS.read_text(encoding="utf-8")

    def test_the_marker_is_state_derived_in_the_same_shape_as_the_other_egeria_fact_lines(self):
        """Curate slice B: the marker is the commit's derived state, never a constant in the JS."""
        js = self._js()
        assert "SCOPE_SAVED_MARKER" not in js and "SCOPE_SAVED_MARKER" not in SOURCES.read_text(encoding="utf-8")
        assert "Saved in Resource Explorer" not in js
        assert "Saved in Resource Explorer" not in SOURCES.read_text(encoding="utf-8").split("*/")[-1]
        i = js.index("data-scope-saved-marker")
        assert "commitHeaderText(view)" in js[i:i + 400]
        # the same classes as the in-file 'reads Egeria element ...' line, and no new accent colour
        assert 'class="mb-s1 text-provenance text-ink-muted"' in js[i:i + 400]
        assert 'data-scope-tree-header class="mb-s1 text-provenance text-ink-muted"' in js

    def test_the_collapsed_line_is_a_real_button_with_aria_expanded(self):
        js = self._js()
        i = js.index("<button type=\"button\" data-scope-collapse")
        btn = js[i:i + 400]
        assert "aria-expanded=" in btn and 'aria-controls="scope-section-body"' in btn
        assert "data-scope-header-line" in btn
        assert 'id="scope-section-body" data-scope-body' in js

    def test_the_choice_is_remembered_through_try_catch_helpers_per_person_and_database(self):
        js = self._js()
        assert "readScopePref(storage(), me, slug)" in js and "writeScopePref(storage(), me, slug, sectionOpen)" in js
        assert "try { return globalThis.localStorage || null; } catch { return null; }" in js
        src = SOURCES.read_text(encoding="utf-8")
        assert "scopeCollapseKey" in src and "${person}.${slug}" in src
        assert src.count("try {") == 2 and src.count("} catch") == 2   # read and write, each guarded

    def test_the_default_is_decided_once_per_visit_so_a_redraw_cannot_flip_it(self):
        js = self._js()
        assert "if (sectionOpen === null) sectionOpen = scopeStartsOpen(view, readScopePref(" in js
        assert "sectionOpen = null; }" in js   # reset when the database changes

    def test_an_undeclared_scope_cannot_be_collapsed_on_screen_by_a_click_either(self):
        js = self._js()
        assert "open = open || !declared;" in js
        assert "const shown = sectionOpen || !declared;" in js and "body.hidden = !shown;" in js
