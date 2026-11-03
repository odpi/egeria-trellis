"""No `window.prompt()` anywhere in the flow for answering one of the
catalog's Human-Supplied questions (ENRICHMENT-E0-ROW-ANATOMY,
docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md §0.3/§6 item 1).

Before this change, `wireHumanAnswers()` in app.js opened a blocking
`window.prompt(question, prior)` popup: one line, no author, and it froze
the rest of the page. It is replaced by an inline control on the row itself
-- the same input+save shape `stages/enrichment.js` already uses for
judgements/observations -- wired through `data-human-edit` (open),
`data-answer-save` (save) and `data-answer-cancel` (cancel).

Source-text assertion, following this codebase's established pattern for
"this bad thing must not appear in this function's source" (see e.g.
test_enrichment_save_field_entity_type.py) -- the render-harness test
(frontend-build/test-harness/human-question-answer-row-anatomy.test.mjs)
covers what actually renders; this pins that the popup call itself is gone
from the function that used to make it, not just that something else now
also works.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_JS = (
    Path(__file__).resolve().parents[1]
    / "resource_explorer" / "web" / "static" / "next" / "app.js"
)


def _source() -> str:
    return APP_JS.read_text()


def _strip_js_comments(src: str) -> str:
    """Drop `//...` and `/*...*/` comments so a source-text assertion isn't
    tripped up by this test's OWN explanatory comments naming the thing
    being asserted absent (this file's header comment does exactly that:
    it says `window.prompt()` is gone, which would otherwise make the
    literal string appear right next to the code under test)."""
    no_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", no_block)


def _function_source(name: str) -> str:
    """The text of one top-level `function NAME(...) { ... }` block, found by
    brace-matching from its declaration. Good enough for app.js's own
    functions, which are never nested inside an expression that would
    confuse a naive brace count (same assumption other tests in this
    package make about this file's shape)."""
    src = _strip_js_comments(_source())
    m = re.search(rf"function {re.escape(name)}\([^)]*\)\s*\{{", src)
    assert m, f"{name}() not found in app.js"
    depth = 0
    i = m.end() - 1
    start = m.end()
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i]
        i += 1
    raise AssertionError(f"unbalanced braces reading {name}()")


class TestNoPromptInTheAnswerFlow:
    def test_wire_human_answers_never_calls_window_prompt(self):
        body = _function_source("wireHumanAnswers")
        assert "window.prompt" not in body, (
            "wireHumanAnswers() must not call window.prompt() -- answering a "
            "human question is now an inline control (data-human-edit / "
            "data-answer-save / data-answer-cancel), not a blocking popup"
        )

    def test_the_human_row_body_never_calls_window_prompt(self):
        body = _function_source("bodyLines")
        assert "window.prompt" not in body

    def test_the_inline_control_hooks_are_present(self):
        src = _source()
        for hook in ("data-human-edit", "data-answer-save", "data-answer-cancel", "data-answer-input"):
            assert hook in src, f"expected the inline-control hook {hook!r} in app.js"

    def test_savequestionanswer_is_still_the_one_write_path(self):
        """The client no longer does its own read-modify-write of the whole
        context document (see re-api.js) -- it PATCHes one answer, mirroring
        saveEnrichmentField's PATCH .../field."""
        src = _source()
        assert "saveQuestionAnswer(" in src
        re_api = (APP_JS.parent.parent / "re-api.js").read_text()
        assert "PATCH" in re_api or "patch(" in re_api
        assert "/answer'" in re_api or '/answer`' in re_api, (
            "saveQuestionAnswer should PATCH a dedicated .../answer route, "
            "mirroring saveEnrichmentField's PATCH .../field"
        )
