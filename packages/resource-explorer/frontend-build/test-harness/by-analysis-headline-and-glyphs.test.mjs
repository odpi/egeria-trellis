/** Real-DOM regression tests for two By-analysis panel bugs found live on
 *  the owner's 2026-09-28 timed gate (8813), both fixed on this branch
 *  (`re/by-analysis-progressive-and-graph`) -- see
 *  BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md's
 *  follow-up section for the full write-up.
 *
 *  BUG 1 -- the contents-board row's headline came ONLY from the board's own
 *  (slow, 10-26s-per-board) dashboard read: `entry.status === 'done' ?
 *  boardHeadlineText(entry.board) : ''`. While a read was in flight the row
 *  showed mark + name + "reading…" with no headline, even when the
 *  matching question's envelope had ALREADY been resolved (the Questions
 *  tab's own `state.answers` cache, populated by `getAnswer`/`loadAnswer`).
 *  Fixed by `envelopeHeadlineText`/`boardQuestionMap` (app.js): the row now
 *  reads that cache as a fallback while the board's own read is still
 *  outstanding. This is a real DOM-after-two-renders assertion, not a
 *  source-text one, because the bug was never about which functions app.js
 *  called -- `getQuestions()` was already being called, for board
 *  ordering -- it was about a value that was already sitting in memory
 *  never reaching the row that needed it.
 *
 *  BUG 2 -- `boardStateKey` never recognised `result_status.py`'s
 *  NOT_ESTABLISHED state on a board's own analyses: a board that measured
 *  something but could not settle a result (`has_results: true`,
 *  `results.state === 'not_established'`) fell through to the plain
 *  `measured` case and showed a ✓, not the `?` the Questions tab's own
 *  glyph vocabulary (glyphs.js's `STATES.not_established`) gives the same
 *  condition elsewhere. Neither live gate database
 *  (laz_local_adventureworks, localhost_docker_coco_pharma) has a
 *  not-established analysis today, so this is verified here with a fixture
 *  rather than a live screenshot -- design's own call, see the coordinating
 *  session's dispatch on this fix round.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

function fixtureBoards() {
  return [{ id: 'db_relationship_graph', title: 'Relationship Graph', description: 'How tables relate.' }];
}

test('contents-board row shows the matching question\'s already-resolved envelope headline while the board\'s own read is still loading', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="by-analysis-contents"></div>';

  const boards = fixtureBoards();
  // The board's own (slow) read has NOT settled -- exactly the case the
  // owner's screenshot showed stuck at "reading…" for 60s+.
  const boardState = new Map(boards.map((b) => [b.id, { status: 'loading', board: null }]));

  const question = {
    question: 'How do the tables relate to each other?',
    analysis_ids: ['db_relationship_graph'],
    note: 'db_relationship_graph (design §7) answers this directly.',
  };
  const boardQuestions = app.boardQuestionMap(boards, [question]);

  // Simulate the Questions tab having already resolved this question's
  // envelope earlier in this browser session -- the real, cheap, no-new-
  // fetch source this fix reads from (state.answers, populated by
  // getAnswer()/loadAnswer() when the Questions tab itself was visited).
  app.state.answers.set(question.question, {
    facts: [{
      analysis_id: 'db_relationship_graph',
      is_known: true,
      headline: '91 foreign keys connect 67 of 68 key-captured tables into 2 components. The largest holds 67 tables.',
    }],
  });

  app.renderByAnalysisContents('laz_local_adventureworks', boards, boardState, 0, boards.length, boardQuestions);

  const rowText = document.getElementById('by-analysis-contents').textContent;
  assert.match(
    rowText,
    /91 foreign keys connect 67 of 68 key-captured tables/,
    'the row must show the envelope headline while the board\'s own fetch is stubbed to never resolve',
  );
  assert.doesNotMatch(
    rowText,
    /The largest holds 67 tables/,
    'firstSentence truncation must still apply to the envelope headline -- one sentence per row, same rule the board\'s own headline follows',
  );
});

test('the row stays blank, not a fabricated line, when no envelope has resolved for the matching question yet', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="by-analysis-contents"></div>';

  const boards = fixtureBoards();
  const boardState = new Map(boards.map((b) => [b.id, { status: 'loading', board: null }]));
  const question = {
    question: 'How do the tables relate to each other?',
    analysis_ids: ['db_relationship_graph'],
    note: '',
  };
  const boardQuestions = app.boardQuestionMap(boards, [question]);
  // state.answers has nothing for this question -- a cold load that goes
  // straight to By-analysis without visiting Questions first. The honest
  // floor: no headline text, same "reading…" placeholder as before this
  // fix, never a manufactured line.
  app.renderByAnalysisContents('laz_local_adventureworks', boards, boardState, 0, boards.length, boardQuestions);

  const row = document.querySelector('[data-jump-board="db_relationship_graph"]');
  assert.ok(row, 'the contents row should still render');
  const headlineSlot = row.querySelector('.truncate');
  assert.equal(headlineSlot.textContent.trim(), '', 'no envelope resolved yet -- the headline slot must stay empty, not invent text');
});

test('a board with a not_established analysis renders the "?" glyph and names the reason in its headline', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="by-analysis-contents"></div>';

  const boards = [{ id: 'grain_determination', title: 'Table Grain', description: 'Time grain per table.' }];
  const board = {
    id: 'grain_determination',
    has_results: true,
    last_surveyed_at: '2026-09-28T10:00:00Z',
    analyses: [{
      analysis_id: 'grain_determination',
      results: { state: 'not_established', message: 'Could not determine a single grain: multiple candidate key sets found.' },
      headline: { label: 'Could not determine a single grain: multiple candidate key sets found.' },
      last_surveyed_at: '2026-09-28T10:00:00Z',
    }],
  };
  const boardState = new Map([['grain_determination', { status: 'done', board }]]);

  // Unit-level: the state classifier itself.
  assert.equal(
    app.boardStateKey({ status: 'done', board }),
    'not_established',
    'a board with a not_established analysis result must not fall through to the plain "measured" case',
  );

  // DOM-level: the row actually shows the "?" glyph (glyphs.js STATES.not_established) and the reason.
  app.renderByAnalysisContents('localhost_docker_coco_pharma', boards, boardState, 1, boards.length, new Map());
  const row = document.querySelector('[data-jump-board="grain_determination"]');
  assert.ok(row, 'the contents row should render');
  assert.equal(row.querySelector('.font-glyph').textContent, '?', 'a not-established board must show the "?" glyph, not a plain ✓');
  assert.match(
    row.textContent,
    /Could not determine a single grain/,
    'the row\'s headline must name WHY the analysis is not established, not just flag it',
  );
});
