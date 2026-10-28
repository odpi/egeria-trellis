"""Two feedback bugs fixed together (2026-09-23), both pinned at the source
level per this suite's established convention for `/next` JS modules with no
browser test runner wired in (see test_next_chat_pane.py's own docstring and
test_fact_answer_rendering.py's `_fn` helper, reproduced here).

Bug 2 — clicking "Wrong" and then cancelling the optional-comment prompt
(`inPagePrompt`) left `feedback.js`'s `_sendAnswerVerdict()` returning with
NOTHING shown: the "Was this right?" bar's own design principle (stated in
`_said()`'s doc comment) is that every outcome must say what happened. A
bare `return` inside the cancel branch is exactly the failure that comment
describes.

Bug 3 — `chat.js`'s per-turn vote (icon buttons via Lucide `icon()`) and
`feedback.js`'s per-question bar (previously plain "Right/Partly/Wrong" text
links) were two independently-built controls against the identical
agree/partly/disagree vocabulary, posting to the same `/api/feedback/answer`
endpoint. Consolidated onto one shared renderer, `feedbackVotesHtml()` in
app.js, that both files import for their button markup while keeping their
own click-wiring and recording logic.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _read(name: str) -> str:
    return (NEXT / name).read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    """Extract one `function name(...) { ... }` (or `async function`) body
    by brace matching — same technique test_fact_answer_rendering.py and
    test_admin_feedback_view.py use."""
    marker = f"function {name}("
    start = src.index(marker)
    brace = src.index("{", start)
    depth = 1
    i = brace + 1
    while depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[start:i]


class TestBug2CancelPathShowsSomething:
    """`_sendAnswerVerdict`'s disagree/cancel branch must not be a bare
    `return` — every path through the function ends in a visible message."""

    def test_cancel_branch_is_not_a_bare_return(self):
        src = _read("feedback.js")
        fn = _fn(src, "_sendAnswerVerdict")
        idx = fn.index("typed === null")
        # The branch body: from the `{` right after the condition to its
        # matching `}`.
        brace = fn.index("{", idx)
        depth = 1
        i = brace + 1
        while depth:
            if fn[i] == "{":
                depth += 1
            elif fn[i] == "}":
                depth -= 1
            i += 1
        branch_body = fn[brace + 1:i - 1]
        # The bug: a line that is only `return;` with nothing else in the
        # branch body — i.e. the branch does nothing observable before
        # returning.
        stripped_lines = [ln.strip() for ln in branch_body.splitlines() if ln.strip()
                          and not ln.strip().startswith("//")]
        assert stripped_lines, "cancel branch must not be empty"
        assert not (len(stripped_lines) == 1 and stripped_lines[0] == "return;"), (
            "cancel branch is a bare `return` with nothing else — exactly "
            "the failure _said()'s own doc comment describes"
        )
        # It must actually call something that shows the user a message
        # before returning.
        assert "return;" in branch_body
        assert "_said" in branch_body or "_saidAndReset" in branch_body

    def test_cancel_branch_leaves_the_bar_usable_again(self):
        """The fix's chosen UX: reset the bar back to its buttons (not just
        an inert sentence) so the person is not stranded with no way to
        record a verdict."""
        src = _read("feedback.js")
        fn = _fn(src, "_sendAnswerVerdict")
        assert "_saidAndReset" in fn
        reset_fn = _fn(src, "_saidAndReset")
        # Rebuilds the same buttons `_attachTo` originally rendered.
        assert "_barButtonsHtml" in reset_fn

    def test_agree_and_partly_never_touch_the_cancel_branch(self):
        """Only `disagree` opens the prompt at all — `agree`/`partly` must
        reach `_said(bar, 'Recording…')` unconditionally, so they were never
        exposed to this bug in the first place."""
        src = _read("feedback.js")
        fn = _fn(src, "_sendAnswerVerdict")
        # The prompt is gated on verdict === 'disagree'.
        assert "verdict === 'disagree'" in fn
        # And 'Recording…' is reached outside that gated block for the
        # other two verdicts (present exactly once, not inside the
        # disagree-only branch).
        assert fn.count("Recording…") == 1


class TestBug3SharedFeedbackRenderer:
    """chat.js and feedback.js render their vote/verdict buttons through the
    same app.js function rather than each keeping its own icon/text markup."""

    def test_app_js_exports_the_shared_renderer(self):
        app = _read("app.js")
        assert "export function feedbackVotesHtml" in app
        assert "export const FEEDBACK_VOTES" in app

    def test_chat_js_imports_and_uses_the_shared_renderer(self):
        chat = _read("chat.js")
        assert "feedbackVotesHtml" in chat
        assert "from '/static/next/app.js'" in chat
        fn = _fn(chat, "feedbackHtml")
        assert "feedbackVotesHtml(" in fn
        # The old locally-duplicated VOTES array/icon markup is gone from
        # chat.js -- it lives only in app.js now.
        assert "const VOTES = [" not in chat

    def test_feedback_js_imports_and_uses_the_shared_renderer(self):
        fb = _read("feedback.js")
        assert "import { feedbackVotesHtml } from '/static/next/app.js';" in fb
        assert "feedbackVotesHtml(" in fb
        # The old independent "Right/Partly/Wrong" text-link markup and its
        # own data-fb-verdict vocabulary are gone (a historical mention in a
        # comment explaining the switch to `data-vote` is fine).
        assert "ANSWER_VERDICTS" not in fb
        assert 'closest(\'[data-fb-verdict]\')' not in fb
        assert 'dataset.fbVerdict' not in fb

    def test_both_call_sites_pass_a_theme_matching_their_background(self):
        chat = _read("chat.js")
        fb = _read("feedback.js")
        assert "theme: 'chrome'" in chat   # dark rail
        assert "theme: 'paper'" in fb      # light question row

    def test_feedback_js_still_keeps_its_own_row_scanning_machinery(self):
        """The consolidation only replaces button markup -- feedback.js's
        row-attachment machinery (genuinely different from chat's per-turn
        attachment) must be untouched."""
        fb = _read("feedback.js")
        assert "function _scanRows" in fb
        assert "function _watchRows" in fb
        assert "MutationObserver" in fb

    def test_feedback_js_still_prompts_for_a_disagree_comment(self):
        """feedback.js's own extra behavior beyond the shared button markup
        -- the inPagePrompt() for a comment on "Wrong" -- must survive the
        consolidation unchanged."""
        fb = _read("feedback.js")
        fn = _fn(fb, "_sendAnswerVerdict")
        assert "inPagePrompt(" in fn

    def test_chat_js_vote_recording_is_unchanged(self):
        """chat.js's own recording logic (sendFeedback + join-to-gaps-loop)
        must still be reached from its click handler after the extraction --
        this is the "must not regress Chat's existing feedback flow" check."""
        chat = _read("chat.js")
        assert "async function vote(" in chat
        vote_fn = _fn(chat, "vote")
        assert "sendFeedback(" in vote_fn
        assert "submitAnswerFeedback(" in vote_fn
        # The click handler still wires data-vote buttons to vote().
        assert "querySelectorAll('[data-vote]')" in chat
