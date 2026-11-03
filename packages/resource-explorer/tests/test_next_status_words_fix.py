"""Screen-defect round of 2026-10-29: header sign-in word, unknown ?stage=, page title, anonymous-read dismiss.

Source-reading tests, the pattern for /next JS without a browser (see test_next_investigation_pane.py)."""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
NEXT = STATIC / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


class TestSignedInWord:
    def test_header_word_comes_from_the_confirmed_answer_only(self):
        app = _app()
        assert "|| unconfirmedWord();" in app and "openConnectionPopover(document, btn, { me: { user_id: signedInUser() } })" in app
        body = app[app.index("function unconfirmedWord()"):]
        body = body[:body.index("\n}\n")]
        assert "authenticated === false ? 'not signed in' : 'checking sign-in…'" in body
        assert "Auth." not in app[app.index("function signedInUser()"):app.index("function unconfirmedWord()")] + body


class TestUnknownStage:
    def test_unknown_stage_is_guarded_before_label_is_read(self):
        app = _app()
        load_pane = app[app.index("async function loadPane()"):]
        find = load_pane.index("STAGES.find((s) => s.id === state.stage)")
        guard = load_pane.index("if (!stageDef) {", find)
        label = load_pane.index("stageDef.label", find)
        assert guard < label

    def test_guard_names_the_stage_and_lists_the_real_ones(self):
        app = _app()
        assert "`no stage named ${state.stage}`" in app
        assert "STAGES.map((s) => s.id).join(', ')" in app


class TestNoNextInScreenWords:
    def test_title_has_no_route_name(self):
        html = (NEXT / "index.html").read_text(encoding="utf-8")
        assert "<title>Resource Explorer</title>" in html

    def test_no_user_visible_next_in_strings(self):
        for f in [NEXT / "app.js", *sorted((NEXT / "stages").glob("*.js"))]:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith(("//", "*", "/*")):
                    continue
                assert "in /next" not in s and "/next stopped" not in s and "; /next counts" not in s, f"{f.name}:{n}"


class TestAnonymousDismiss:
    def test_dismiss_button_is_hidden_unless_anonymous_read(self):
        js = (STATIC / "auth.js").read_text(encoding="utf-8")
        assert "anonymousRead = data.anonymous_read === true;" in js
        assert "dismiss.style.display = anonymousRead ? '' : 'none';" in js


class TestLoadingPanesSayWhatTheyWaitFor:
    def test_no_bare_loading_words_remain(self):
        app = _app()
        ctx = (NEXT / "stages" / "context.js").read_text(encoding="utf-8")
        assert ">loading…<" not in app and "still loading</span>" not in app and "pending}</span> still loading" not in app
        assert "Reading what's been supplied…" not in ctx

    def test_each_names_its_wait_and_how_many_are_back(self):
        app = _app()
        ctx = (NEXT / "stages" / "context.js").read_text(encoding="utf-8")
        assert "questions still being read" in app and "none back yet" in app
        assert "still reading <span class=\"tnum\">${pending}</span> · <span class=\"tnum\">${settled.length}</span> back" in app
        assert "waiting on its context and enrichment reads" in ctx
