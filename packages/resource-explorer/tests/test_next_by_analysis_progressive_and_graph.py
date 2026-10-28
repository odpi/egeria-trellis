"""By-analysis panel rebuild -- progressive render + headline-first cards +
grouped COUNTS + shared-names-once + inline Relationship Graph card body
(BRIEF-BY-ANALYSIS-PANEL-USABILITY.md +
REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2, 2026-09-28).

Two problems this rebuild exists for, both live-confirmed by the project
owner: `loadByAnalysisPane` sat on "Reading the dashboards…" for up to 109s
with nothing else visible (the OLD code's own comment named this cost and
did nothing about it), and the Relationship Graph card showed only numbers,
never the graph `factGraphviz` already knows how to render.

No browser verification with a signed-in session is asserted by these
tests -- same static-source-assertion pattern `test_next_db_server_
discovery.py` and `test_next_schema_inventory_filter.py` established; see
this branch's own IMPLEMENTED doc for what was verified live instead.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _fn(name: str, terminator: str = "\n}\n") -> str:
    src = _app()
    start = src.index(name)
    end = src.index(terminator, start)
    return src[start:end]


class TestContentsBoardRendersBeforeAnyDashboardRead:
    """The core fix for the 109s blank wait: the contents board must be on
    screen before a single board's own (slow) dashboard read has even been
    requested, let alone resolved."""

    def test_load_by_analysis_pane_paints_before_the_progressive_fetch(self):
        body = _fn("async function loadByAnalysisPane()")
        paint_now = body.index("paintAll();   // visible now")
        slow_fetch = body.index("const queue = [...catalogBoards];")
        assert paint_now < slow_fetch, (
            "the unconditional first paint must happen textually (and therefore "
            "temporally -- nothing async separates them) before the per-board "
            "dashboards fetch is even started"
        )

    def test_the_fast_path_reads_state_analyses_already_fetched_at_boot(self):
        body = _fn("async function loadByAnalysisPane()")
        assert "state.analyses" in body
        # No `await` sits between reading state.analyses and the first paint --
        # this is genuinely free, not merely started-early.
        idx_state_analyses = body.index("state.analyses")
        idx_paint = body.index("paintAll();   // visible now")
        between = body[idx_state_analyses:idx_paint]
        assert "await getSurveyDashboards" not in between

    def test_never_calls_get_survey_dashboards_for_the_initial_catalog(self):
        # The initial board list comes from state.analyses or the cheap
        # /survey-results/boards endpoint (listSurveyResultBoards) -- never
        # from getSurveyDashboards, which is the slow, per-board call.
        body = _fn("async function loadByAnalysisPane()")
        before_slow_fetch = body[:body.index("const queue = [...catalogBoards];")]
        assert "listSurveyResultBoards" in before_slow_fetch
        assert "await getSurveyDashboards" not in before_slow_fetch


class TestProgressiveFetchIsOnePerBoard:
    def test_each_board_fetches_independently_via_board_id(self):
        body = _fn("async function loadByAnalysisPane()")
        assert "getSurveyDashboards(slug, stage, { includeEmpty: true, entityType, boardId: b.id })" in body

    def test_boards_fetch_several_at_once_not_one_at_a_time_in_sequence(self):
        body = _fn("async function loadByAnalysisPane()")
        assert "const queue = [...catalogBoards];" in body
        assert "Array.from({ length: Math.min(BY_ANALYSIS_MAX_CONCURRENT_READS, catalogBoards.length) }, worker)" in body

    def test_concurrency_is_bounded_not_unbounded(self):
        # Found necessary live: firing every board's (expensive, db_derived-
        # backed) read at once starved even the cheap boards-catalog call
        # behind them when a user switched stages quickly.
        app = _app()
        assert "const BY_ANALYSIS_MAX_CONCURRENT_READS = 3;" in app

    def test_a_worker_checks_live_before_starting_each_new_read(self):
        # Switching away must stop NEW reads from being queued, not just
        # discard the results of ones already in flight.
        body = _fn("async function loadByAnalysisPane()")
        worker_body = body[body.index("const worker = async () => {"):body.index("await Promise.all(")]
        assert "if (!live()) return;" in worker_body

    def test_still_reading_n_of_m_line_exists(self):
        body = _fn("function renderByAnalysisContents(")
        assert "still reading" in body
        assert "stillReading" in body

    def test_dash_token_still_guards_a_stale_pane_switch(self):
        body = _fn("async function loadByAnalysisPane()")
        assert "const token = ++dashToken;" in body
        assert "const live = () => token === dashToken" in body


class TestContentsBoardStructure:
    def test_contents_board_is_sticky(self):
        body = _fn("function renderByAnalysisContents(")
        assert "sticky top-0" in body

    def test_rows_carry_glyph_headline_and_run_time(self):
        body = _fn("function renderByAnalysisContents(")
        assert "glyph.glyph" in body
        assert "headlineText" in body
        assert "runWhen" in body

    def test_clicking_a_row_jumps_to_and_opens_its_card(self):
        body = _fn("function renderByAnalysisContents(")
        assert "data-jump-board" in body
        assert "card.open = true;" in body
        assert "card.scrollIntoView(" in body

    def test_summary_line_counts_ran_never_run_and_total(self):
        body = _fn("function renderByAnalysisContents(")
        assert "analyses ·" in body
        assert "ran ·" in body
        assert "never run" in body


class TestRowHeadlineFallsBackToTheQuestionsEnvelope:
    """The owner's 8813 live finding, 2026-09-28: every board row stayed
    mark + name + "reading…" with NO headline for 60s+, even though
    `getQuestions()` was already being fetched (for board ordering) and a
    matching question's envelope may already sit resolved in `state.
    answers` (the Questions tab's own cache) with no new fetch required.
    DOM-level proof this actually renders lives in frontend-build/
    test-harness/by-analysis-headline-and-glyphs.test.mjs; these are the
    accompanying source-shape assertions, same pattern this file's other
    classes use."""

    def test_envelope_headline_helper_exists_and_reuses_lead_analysis_id_and_first_sentence(self):
        # Reuses the exact same pair `boardHeadlineText`/`readEnvelope`
        # already use for "the one sentence this row shows" -- not a third,
        # bespoke extraction.
        app = _app()
        assert "function envelopeHeadlineText(question, answers)" in app
        body = _fn("function envelopeHeadlineText(question, answers)")
        assert "leadAnalysisId(question)" in body
        assert "firstSentence(f.headline)" in body
        assert "firstSentence(p)" in body

    def test_envelope_headline_reads_state_answers_not_a_new_fetch(self):
        # Genuinely free: no getAnswer()/await appears in this function --
        # it only reads a Map already populated by loadAnswer() elsewhere.
        body = _fn("function envelopeHeadlineText(question, answers)")
        assert "answers.get(question.question)" in body
        assert "await" not in body
        assert "getAnswer(" not in body

    def test_board_question_map_reuses_the_same_matching_rule_order_boards_by_questions_uses(self):
        app = _app()
        assert "function boardQuestionMap(boards, questions)" in app
        body = _fn("function boardQuestionMap(boards, questions)")
        assert "leadAnalysisId(q)" in body

    def test_contents_row_falls_back_to_the_envelope_only_while_the_board_is_not_done(self):
        body = _fn("function renderByAnalysisContents(")
        assert "entry.status === 'done'\n      ? boardHeadlineText(entry.board)\n      : envelopeHeadlineText(boardQuestions.get(b.id), state.answers);" in body

    def test_load_by_analysis_pane_builds_the_board_question_map_from_the_same_getquestions_call(self):
        # No SECOND network call -- boardQuestionMap is built from the exact
        # checklist getQuestions() already returned for ordering, not a new
        # fetch of its own.
        body = _fn("async function loadByAnalysisPane()")
        getq_idx = body.index("getQuestions(slug, { phase: stage, entityType })")
        after = body[getq_idx:]
        assert "boardQuestions = boardQuestionMap(orderedBoards, questions);" in after


class TestNotEstablishedBoardsReadAsSuch:
    """BRIEF-BY-ANALYSIS-PANEL-USABILITY.md gate task 4: "the not-established
    cards must read as such in the table of contents (◐)". Neither live gate
    database has a not-established analysis today (design's own call, this
    fix round) -- covered by a harness fixture
    (frontend-build/test-harness/by-analysis-headline-and-glyphs.test.mjs)
    instead of a live screenshot; these pin the source shape."""

    def test_board_state_key_recognizes_not_established_results(self):
        body = _fn("function boardStateKey(entry)")
        assert "a.results && a.results.state === 'not_established'" in body
        assert "return 'not_established';" in body

    def test_not_established_check_runs_before_the_plain_measured_fallthrough(self):
        body = _fn("function boardStateKey(entry)")
        not_established_idx = body.index("notEstablished")
        measured_idx = body.index("return 'measured';")
        assert not_established_idx < measured_idx


class TestSharedNamesRenderedOnce:
    def test_shared_names_block_is_a_single_function_called_from_one_mount_point(self):
        app = _app()
        # sharedNamesHtml is assigned into exactly one element's innerHTML per
        # paint -- the Shared Names block is a pane-level singleton, not
        # rendered inside each card.
        assert app.count("$('by-analysis-shared').innerHTML = sharedNamesHtml(shared, disagreeing);") == 1
        # And boardCountsHtml (the per-card COUNTS table) never renders the
        # cross-analysis sentence itself -- only a `≠` mark referring back.
        counts_body = _fn("function boardCountsHtml(board, disagreeing)")
        assert "analyses report this name with different values" not in counts_body

    def test_shared_names_header_names_the_criterion_never_disagree(self):
        # Owner's live coco_pharma gate feedback, 2026-09-28: "I don't know
        # what '1 DISAGREE' means" -- the header now names the actual
        # inclusion criterion (shared by more than one analysis) instead of
        # a bare "DISAGREE" count, and no row uses the word "DISAGREE" either.
        body = _fn("function sharedNamesHtml(shared, disagreeing)")
        assert "DISAGREE" not in body
        assert "carried by more than one analysis" in body
        # Singular/plural handled -- a lone shared name reads "1 name", not "1 names".
        assert "shared.size === 1 ? '' : 's'" in body

    def test_shared_set_is_every_name_carried_by_more_than_one_analysis(self):
        # The Shared Names block's inclusion criterion is "shared" --
        # `rec.length > 1` -- not "disagrees". `disagreeing` is a SEPARATE,
        # narrower set, still used to decide the per-card `≠` mark so that
        # mark keeps meaning "actually different values elsewhere".
        body = _fn("function collectMeasures(boards, boardState)")
        assert "if (rec.length > 1) shared.set(k, rec);" in body
        assert "disagreeing.add(k)" in body

    def test_preliminary_fit_zero_confidence_renders_as_no_lens_declared_not_a_bare_number(self):
        collect_body = _fn("function collectMeasures(boards, boardState)")
        assert "noLens" in collect_body
        assert "analysis_id === 'preliminary_fit' && k === 'confidence' && v === 0" in collect_body
        display_body = _fn("function measureDisplay(x, key)")
        assert "— (no lens declared)" in display_body
        assert "x.noLens ? '— (no lens declared)'" in display_body

    def test_no_lens_placeholder_is_excluded_from_the_disagreement_comparison(self):
        # REPLY §2.3: "leave it out of the comparison" -- the comparable set
        # used to decide whether a name IS disagreeing filters noLens out.
        body = _fn("function collectMeasures(boards, boardState)")
        assert "rec.filter((x) => !x.noLens)" in body

    def test_board_counts_table_reuses_measure_display_not_a_bare_fmt_scalar(self):
        # Owner's live coco_pharma finding, 2026-09-28: the Shared Names
        # block said "— (no lens declared)" for preliminary_fit's confidence
        # while the card's own COUNTS table, formatting the identical value
        # with a bare fmtScalar, showed "0" right next to it. Fixed by
        # routing both through the same measureDisplay helper.
        body = _fn("function boardCountsHtml(board, disagreeing)")
        assert "measureDisplay(c, c.key)" in body
        assert "noLens" in body


class TestCardAnatomyHeadlineFirstDescriptionCollapsed:
    def test_headline_renders_before_the_description_disclosure(self):
        body = _fn("function byAnalysisCardHtml(")
        headline_idx = body.index("mt-s2 max-w-[70ch] text-answer text-ink")
        details_idx = body.index("what it does ▸")
        assert headline_idx < details_idx

    def test_description_lives_inside_a_details_element_closed_by_default(self):
        body = _fn("function byAnalysisCardHtml(")
        # The description's own <details> carries no `open` attribute --
        # only the outer card <details> (bound to the per-viewer localStorage
        # preference) may be open.
        assert '<details class="mt-s2">' in body
        assert "what it does ▸" in body

    def test_card_is_a_band_not_a_box_top_rule_only(self):
        # REPLY §2.1: space + a strong top rule + name-size heading, not a
        # bordered box (which would double every row's own hairline into a
        # grid).
        body = _fn("function byAnalysisCardHtml(")
        assert "border-t-2 border-rule-strong" in body
        assert "font-heading text-name font-normal" in body

    def test_card_uses_the_same_headline_function_as_the_contents_board(self):
        app = _app()
        assert "function boardHeadlineHtml(board)" in app
        assert "function boardHeadlineText(board)" in app
        card_body = _fn("function byAnalysisCardHtml(")
        contents_body = _fn("function renderByAnalysisContents(")
        assert "boardHeadlineHtml(board)" in card_body
        assert "boardHeadlineText(entry.board)" in contents_body


class TestCountsAreGrouped:
    def test_three_groups_declared(self):
        app = _app()
        assert "const COUNT_GROUP_LABELS = { result: 'Result', coverage: 'Coverage', diagnostics: 'Diagnostics' };" in app

    def test_grouping_is_a_pure_function_of_the_key_name(self):
        body = _fn("function countGroupFor(key)")
        assert "coverage" in body
        assert "result" in body
        assert "diagnostics" in body


class TestCardsInQuestionOrder:
    def test_order_boards_by_questions_helper_exists(self):
        app = _app()
        assert "function orderBoardsByQuestions(boards, questions)" in app

    def test_uses_lead_analysis_id_not_bare_first_entry(self):
        body = _fn("function orderBoardsByQuestions(boards, questions)")
        assert "leadAnalysisId(q)" in body

    def test_never_run_boards_move_to_the_end_once_settled(self):
        app = _app()
        assert "function neverRunLast(boards, boardState)" in app
        body = _fn("function neverRunLast(boards, boardState)")
        assert "entry.status === 'done' && entry.board && !entry.board.has_results" in body

    def test_pane_applies_never_run_last_only_after_everything_settles(self):
        body = _fn("async function loadByAnalysisPane()")
        after_await = body[body.index("const queue = [...catalogBoards];"):]
        assert "orderedBoards = neverRunLast(orderedBoards, boardState);" in after_await


class TestGraphvizCardBodyReusesTheEvidenceRailRenderer:
    """The gate: the Relationship Graph card's body must show the actual
    graph, and via the SAME rendering path the Questions-tab Evidence rail
    uses -- not a second implementation."""

    def test_render_diagram_into_is_declared_exactly_once(self):
        app = _app()
        assert app.count("async function renderDiagramInto(container, turn, uid = 'promoted')") == 1

    def test_promote_to_pane_calls_the_shared_renderer(self):
        body = _fn("export async function promoteToPane(turn)")
        assert "await renderDiagramInto(body, turn, 'promoted');" in body
        # The old inline fetch/pan-zoom block must be gone from promoteToPane
        # -- not duplicated alongside the extracted function.
        assert "svgPanZoom(svgEl" not in body

    def test_board_diagram_html_reads_factgraphviz_over_the_boards_own_results(self):
        body = _fn("function boardDiagramHtml(board)")
        assert "factGraphviz(envelope)" in body
        assert "factMermaid(envelope)" in body

    def test_card_body_mounts_the_shared_renderer_not_a_new_one(self):
        body = _fn("function bindGraphvizCard(root, boardId, gv)")
        assert "renderDiagramInto(mount, { graphviz: source }, uid)" in body

    def test_graphviz_card_offers_the_same_three_zoom_levels_as_the_evidence_rail(self):
        body = _fn("function graphvizCardHtml(boardId, gv)")
        assert "data-graphviz-level=\"schema_map\"" in body
        assert "data-graphviz-level=\"full\"" in body
        assert "data-graphviz-schema" in body

    def test_schema_map_renders_immediately_on_mount_no_extra_click(self):
        body = _fn("function bindGraphvizCard(root, boardId, gv)")
        idx_render_call = body.index("render(gv.schemaMap);")
        idx_listeners = body.index("card.querySelectorAll('[data-graphviz-level]')")
        assert idx_render_call < idx_listeners

    def test_the_pattern_generalizes_to_other_diagram_carrying_analyses(self):
        # "Any OTHER analysis that also carries a diagram ... should get the
        # same treatment -- don't hardcode this to relationship-graph".
        body = _fn("function boardDiagramHtml(board)")
        assert "db_relationship_graph" not in body
        assert "factMermaid(envelope)" in body


class TestGiveUpAfter30SecondsSafetyNet:
    """Design requirement added 2026-09-28 (owner gate check on
    laz_local_adventureworks, settle time 5+ minutes deemed unacceptable):
    a card that has not settled after BY_ANALYSIS_BOARD_TIMEOUT_MS must stop
    spinning and show a manual "open to load" trigger instead -- a safety
    net independent of whether the board_summary fast path is effective for
    that particular board (it also covers "summary missing/stale AND the
    fallback recompute is still slow")."""

    def test_timeout_constant_is_thirty_seconds(self):
        app = _app()
        assert "const BY_ANALYSIS_BOARD_TIMEOUT_MS = 30000;" in app

    def test_worker_arms_a_give_up_timer_per_board(self):
        body = _fn("async function loadByAnalysisPane()")
        worker_body = body[body.index("const worker = async () => {"):body.index("await Promise.all(")]
        assert "setTimeout(" in worker_body
        assert "BY_ANALYSIS_BOARD_TIMEOUT_MS" in worker_body
        assert "status: 'timeout'" in worker_body

    def test_worker_clears_the_timer_once_the_fetch_settles(self):
        # A late timer firing after the real result already landed must not
        # overwrite it back to 'timeout' -- clearTimeout (or the `already`
        # guard) must run before boardState is set to its real outcome.
        body = _fn("async function loadByAnalysisPane()")
        worker_body = body[body.index("const worker = async () => {"):body.index("await Promise.all(")]
        assert "clearTimeout(giveUpTimer)" in worker_body
        # clearTimeout happens before the 'done'/'error' state is written,
        # in BOTH the success and failure branches.
        try_idx = worker_body.index("try {")
        done_idx = worker_body.index("status: 'done'")
        catch_idx = worker_body.index("} catch (err) {")
        error_idx = worker_body.index("status: 'error'", catch_idx)
        first_clear = worker_body.index("clearTimeout(giveUpTimer)", try_idx)
        second_clear = worker_body.index("clearTimeout(giveUpTimer)", first_clear + 1)
        assert first_clear < done_idx
        assert second_clear < error_idx

    def test_timeout_does_not_stop_a_late_result_from_still_being_shown(self):
        # The fetch itself is not aborted -- only the UI's wait for it gives
        # up. A guard variable (not a cancelled promise) is what prevents
        # the timer from clobbering a real result that arrives just after.
        body = _fn("async function loadByAnalysisPane()")
        worker_body = body[body.index("const worker = async () => {"):body.index("await Promise.all(")]
        assert "AbortController" not in worker_body
        assert "let already = false;" in worker_body
        assert "already = true;" in worker_body

    def test_board_state_key_treats_timeout_like_loading_not_error(self):
        body = _fn("function boardStateKey(entry)")
        idx_timeout = body.index("entry.status === 'timeout'")
        idx_error = body.index("entry.status === 'error'")
        assert idx_timeout < idx_error
        assert "return null;" in body.split("entry.status === 'timeout'")[1].split("\n")[0]

    def test_card_shows_open_to_load_copy_and_a_retry_trigger(self):
        body = _fn("function byAnalysisCardHtml(")
        assert "still reading" in body
        assert "open to load" in body
        assert "data-retry-board=" in body

    def test_contents_row_also_shows_open_to_load_for_a_timed_out_board(self):
        body = _fn("function renderByAnalysisContents(")
        assert "still reading — open to load" in body

    def test_a_manual_retry_function_exists_and_is_bound_to_the_trigger(self):
        app = _app()
        assert "const retryBoard = async (boardId) => {" in app
        assert "n.addEventListener('click', () => retryBoard(n.dataset.retryBoard));" in app

    def test_retry_also_arms_its_own_give_up_timer(self):
        body = _fn("async function loadByAnalysisPane()")
        retry_body = body[body.index("const retryBoard = async (boardId) => {"):body.index("const worker = async () => {")]
        assert "BY_ANALYSIS_BOARD_TIMEOUT_MS" in retry_body
        assert "status: 'timeout'" in retry_body

    def test_still_reading_count_does_not_include_timed_out_cards_as_loading(self):
        # `settled` (paintAll) is anything whose status is not 'loading' --
        # a timed-out card must count as settled so "still reading N of M"
        # stops incrementing for it once it has given up.
        body = _fn("async function loadByAnalysisPane()")
        assert "e.status !== 'loading'" in body
