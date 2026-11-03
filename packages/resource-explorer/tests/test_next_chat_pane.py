"""Chat's own /next module (PLAN-FINISH-REPOS.md item 9): pinning the
extraction out of app.js into next/chat.js, the rail-keeps-provenance-only /
pane-holds-the-answer placement move (SPEC-THE-STAGE-PAGE.md's rule, applied
per ASSESSMENT-CHAT.md §2), and the three follow-ups from that assessment's
§3 — opening the compile, using the stream, and joining the vote to the gaps
loop item 8 built.

No browser verification of a signed-in session happened for this file — see
ITEM-9-CHAT-IMPLEMENTED.md for what was and was not
checked live. These tests grep/slice function bodies out of the source, the
established pattern for /next JS modules without a browser — see
test_next_understanding_pane.py and test_next_admin_pane.py's identical
`_app()`-style helpers, reproduced here for chat.js and app.js together.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
STATIC = NEXT.parent


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _chat():
    return (NEXT / "chat.js").read_text(encoding="utf-8")


def _api():
    return (STATIC / "re-api.js").read_text(encoding="utf-8")


class TestRailTurnLabel:
    """The open turn's caption is a status, not an action: "open in pane" read as a link that did nothing
    (owner, 2026-10-26), because the turn it labels is already the one the pane shows."""

    def test_the_open_turn_says_it_is_shown_in_the_pane(self):
        src = _chat()
        assert "' · shown in pane'" in src
        assert "' · open in pane'" not in src


class TestExtraction:
    """next/chat.js exists, app.js imports it rather than defining chat
    inline, and the moved functions are actually gone from app.js -- an
    extraction that leaves a dead copy behind is not an extraction."""

    def test_chat_js_exists_and_is_syntactically_a_module(self):
        assert (NEXT / "chat.js").exists()
        src = _chat()
        assert "export function renderRail" in src
        assert "export function renderRailScope" in src
        assert "export async function submitAsk" in src
        assert "export async function promoteChatTurn" in src

    def test_app_js_imports_chat_rather_than_defining_it(self):
        app = _app()
        assert "from '/static/next/chat.js'" in app
        assert "renderRail" in app.split("from '/static/next/chat.js'")[0].splitlines()[-1] \
            or "import { renderRail, renderRailScope }" in app

    def test_the_old_inline_rail_functions_are_gone_from_app_js(self):
        app = _app()
        # These were app.js's own top-level chat functions before the
        # extraction; only the shared/generic ones (promoteToPane,
        # answerForm, copyAsEvidence) may remain, and only as exports other
        # modules (including chat.js) call into.
        assert "function renderChatLog(" not in app
        assert "function submitAsk(" not in app
        assert "function renderRail(" not in app
        assert "function sessionId(" not in app
        assert "function turnAsMarkdown(" not in app

    def test_shared_pane_machinery_stayed_in_app_js_and_is_exported(self):
        # promoteToPane/answerForm/copyAsEvidence are used by BOTH chat.js
        # and the Questions-checklist row-promotion path (showDiagram,
        # showEvidence) -- they must stay put and be real exports, not
        # duplicated into chat.js.
        app = _app()
        assert "export async function promoteToPane(turn) {" in app
        assert "export function answerForm(turn) {" in app
        assert "export async function copyAsEvidence(markdown, btn) {" in app
        chat = _chat()
        assert "promoteToPane, answerForm, copyAsEvidence" in chat.replace("\n", " ") \
            or ("promoteToPane" in chat and "answerForm" in chat and "copyAsEvidence" in chat)
        assert "function promoteToPane(" not in chat  # imported, not redefined
        assert "function answerForm(" not in chat


class TestPlacementRailKeepsProvenanceOnly:
    """SPEC-THE-STAGE-PAGE.md's rule, applied here: the rail is the turn
    list and provenance; the answer -- prose, chart, diagram, table -- opens
    in the pane. This is the placement move ASSESSMENT-CHAT.md §2 named."""

    def test_the_turn_list_renders_question_and_provenance_not_the_answer_body(self):
        chat = _chat()
        assert "function renderTurnList()" in chat
        body = chat[chat.index("function renderTurnList()"):chat.index("function feedbackHtml(")]
        # The old rail-bound renderer printed the answer text, the chart/
        # diagram "open in pane" button and the feedback bar all inside this
        # loop. None of that belongs in the turn list any more.
        assert "t.answer ?" not in body.replace(" ", "")  # no ternary rendering the answer prose
        assert "Open ${form" not in body  # the old chart/diagram promote button
        assert "data-vote" not in body
        assert "esc(t.question)" in body  # the question itself still shows

    def test_clicking_a_turn_opens_it_in_the_pane(self):
        chat = _chat()
        assert "data-open-turn=" in chat
        assert "promoteChatTurn(t)" in chat

    def test_every_answer_auto_opens_in_the_pane_on_arrival(self):
        """Not just chart/diagram (the old escape hatch) -- every form,
        because ASSESSMENT-CHAT.md §2 says "prose, chart, diagram, table --
        all four forms get the width they were built for.\""""
        chat = _chat()
        submit = chat[chat.index("export async function submitAsk("):]
        # promoteChatTurn is called once to open the pane immediately, and
        # again after the answer (streamed or not) has fully landed.
        assert submit.count("promoteChatTurn(turn)") >= 2

    def test_the_pane_does_not_repeat_the_source_line_promoteToPane_already_prints(self):
        chat = _chat()
        footer = chat[chat.index("function renderPromotedFooter("):]
        assert "two-copies-of-one-fact" in footer or "NOT repeated here" in footer


class TestOpenTheCompile:
    """ASSESSMENT-CHAT.md §3(a): the compile is captured, not just its id,
    and openable -- "the strongest provenance object in this codebase.\""""

    def test_the_full_compiled_object_is_kept_on_the_turn_not_just_its_id(self):
        chat = _chat()
        assert "turn.compiled = body.compiled || null;" in chat

    def test_a_link_opens_the_compile_into_the_rail_evidence_slot(self):
        chat = _chat()
        assert "function openCompile(turn)" in chat
        body = chat[chat.index("function openCompile(turn)"):chat.index("function openCompile(turn)") + 1800]
        assert "railFrame('Compile'" in body
        assert "ensureRailShowing()" in body
        assert "railClaim()" in body
        # A turn with no compile says so rather than opening an empty frame.
        assert "no compile" in body

    def test_the_footer_wires_the_open_compile_link(self):
        chat = _chat()
        assert 'data-act="open-compile"' in chat
        assert "openCompile(turn)" in chat


class TestUseTheStream:
    """ASSESSMENT-CHAT.md §3(b): POST /query/stream existed and was unused.
    submitAsk drives it, updates the pane as chunks arrive, and falls back
    to the blocking endpoint only if the stream fails before any text."""

    def test_re_api_exposes_an_sse_generator_for_the_stream_endpoint(self):
        api = _api()
        assert "export async function* askStream(" in api
        assert "/api/query/stream" in api
        assert "getReader()" in api

    def test_submitask_consumes_the_stream_by_default(self):
        chat = _chat()
        submit = chat[chat.index("export async function submitAsk("):]
        assert "for await (const evt of askStream(q, opts))" in submit
        assert "evt.t === 'chunk'" in submit
        assert "evt.t === 'done'" in submit

    def test_a_stream_failure_before_any_chunk_falls_back_to_the_blocking_ask(self):
        chat = _chat()
        submit = chat[chat.index("export async function submitAsk("):]
        assert "sawChunk" in submit
        assert "await ask(q, opts)" in submit

    def test_partial_text_is_kept_rather_than_discarded_on_a_late_failure(self):
        chat = _chat()
        submit = chat[chat.index("export async function submitAsk("):]
        assert "stream ended early" in submit

    def test_streaming_text_updates_the_open_pane_without_a_full_rerender(self):
        chat = _chat()
        assert "function updateStreamingText(turn)" in chat
        body = chat[chat.index("function updateStreamingText(turn)"):]
        # Only the 'inline' form is safe to redraw mid-stream -- a chart or
        # diagram cannot be drawn from a partial answer.
        assert "answerForm(turn) !== 'inline'" in body


class TestJoinTheVoteToTheGapsLoop:
    """ASSESSMENT-CHAT.md §3(c): item 8 built a disagreement-routes-to-`ours`
    mechanism (ITEM-8-FEEDBACK-IMPLEMENTED.md); check whether chat's vote
    reaches it, and wire the one call if not."""

    def test_re_api_exposes_the_answer_feedback_endpoint_chat_now_calls(self):
        api = _api()
        assert "export const submitAnswerFeedback" in api
        assert "/api/feedback/answer" in api

    def test_vote_still_records_to_the_metrics_collector_path(self):
        """The pre-existing sendFeedback(query_hash, vote) call -- a
        different consumer (MetricsCollector's tracing) than the gaps
        collection -- must keep working; this is additive, not a
        replacement."""
        chat = _chat()
        vote_fn = chat[chat.index("async function vote(i, value)"):chat.index("/** One chat turn, as markdown")]
        assert "await sendFeedback(turn.queryHash, value, turn.compileId || null);" in vote_fn

    def test_a_negative_vote_maps_to_the_disagree_verdict_gaps_dot_py_expects(self):
        chat = _chat()
        assert "const VOTE_VERDICT = { 1: 'agree', 0: 'partly', '-1': 'disagree' };" in chat

    def test_vote_calls_submit_answer_feedback_when_a_resource_is_in_scope(self):
        chat = _chat()
        vote_fn = chat[chat.index("async function vote(i, value)"):chat.index("/** One chat turn, as markdown")]
        assert "if (turn.slug) {" in vote_fn
        assert "await submitAnswerFeedback({" in vote_fn
        assert "verdict: VOTE_VERDICT[String(value)]" in vote_fn

    def test_an_unscoped_turn_states_the_boundary_rather_than_silently_skipping(self):
        """A chat question asked with no resource selected has no slug to
        attribute a gap to -- record_disagreement (gaps.py) is keyed per
        project. This is a real boundary, named on the turn, not a silent
        no-op."""
        chat = _chat()
        vote_fn = chat[chat.index("async function vote(i, value)"):chat.index("/** One chat turn, as markdown")]
        assert "not joined to the gaps loop" in vote_fn


class TestExtractCommentDefectsFromTheAssessmentAreClosed:
    """ASSESSMENT-CHAT.md §1 named two comments in the source as its
    strongest evidence that this was a placement bug. Both must be gone
    from the CURRENT rail rendering (they may still appear in a docstring
    explaining the history, which is fine and expected)."""

    def test_the_rail_no_longer_renders_a_chart_or_diagram_body_at_all(self):
        chat = _chat()
        turn_list = chat[chat.index("function renderTurnList()"):chat.index("function feedbackHtml(")]
        assert "Plotly" not in turn_list
        assert "mermaid" not in turn_list.lower().replace("no answer", "")


class TestNotBuildingTheDeferredRecordIdea:
    """ASSESSMENT-CHAT.md §4/§5: copyAsEvidence-as-a-Record is explicitly the
    owner's call, not an implementer's -- confirm this round did not sneak
    it in."""

    def test_no_record_or_savereport_wiring_was_added_to_chat(self):
        chat = _chat()
        assert "saveReport" not in chat
        assert "actOnRecord" not in chat
        assert "listRecords" not in chat


class TestGapsHeaderSaysNoStoredResultOnce:
    """The gaps header read "3 with no stored result would answer this, with
    no stored result": gapsHeadCue's cue already says it, so the trailing
    caption must not repeat it."""

    def test_the_caption_after_the_gaps_cue_does_not_repeat_no_stored_result(self):
        chat = _chat()
        head = chat[chat.index("${gapsHeadCue(gaps)}"):]
        head = head[:head.index("</div>")]
        assert "would answer this</span>" in head
        assert "no stored result" not in head

    def test_the_cue_itself_still_says_no_stored_result(self):
        chat = _chat()
        cue_fn = chat[chat.index("function gapsHeadCue("):chat.index("function evidenceStateHtml(")]
        assert "with no stored result" in cue_fn
