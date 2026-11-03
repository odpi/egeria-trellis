/** Real-DOM regression test for the By-analysis pane's 30-second give-up
 *  safety net (branch `re/board-summary-read-cost`,
 *  BOARD-SUMMARY-READ-COST-IMPLEMENTED.md).
 *
 *  THE GAP THIS CLOSES: that branch's own writeup flagged its 30s safety
 *  net -- `BY_ANALYSIS_BOARD_TIMEOUT_MS`, the `'timeout'` board state, and
 *  the manual `retryBoard()` trigger -- as verified only by STATIC SOURCE-
 *  TEXT assertions (`TestGiveUpAfter30SecondsSafetyNet` in
 *  `tests/test_next_by_analysis_progressive_and_graph.py`): "does the
 *  timer get armed/cleared near the right lines", never "does a real
 *  render actually show the timeout copy after 30s, and does clicking the
 *  retry button actually re-fetch." Design ruled (2026-09-28, PR #346's
 *  "every /next fix from here on adds its regression to the harness" rule)
 *  that this needs the real render harness, since only a rendered page
 *  shows a timing-dependent UI state transition.
 *
 *  THIS TEST drives the REAL, unmodified `loadByAnalysisPane()` (now
 *  exported -- see app.js's own comment above that export) with a stubbed
 *  `fetch`: the first request for one board's `getSurveyDashboards(...,
 *  { boardId })` read returns a promise that never settles, simulating a
 *  board whose read genuinely never comes back in time. It uses node:test's
 *  built-in fake timers (`t.mock.timers`) to advance past
 *  `BY_ANALYSIS_BOARD_TIMEOUT_MS` without a real 30-second wait, asserts the
 *  card renders the "still reading — open to load" copy, then simulates a
 *  real DOM click on the `[data-retry-board]` button the card renders and
 *  asserts that re-triggers a SECOND fetch for the same board -- exactly
 *  the mechanism `retryBoard()` implements -- which this time resolves, and
 *  the card leaves the timeout state once it does.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

/** A minimal `Response`-shaped object -- everything `re-api.js`'s
 *  `request()` reads off a fetch response (`.ok`, `.status`, `.json()`). */
function fakeJsonResponse(data, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => data };
}

/** Node's mock timers fake `setTimeout`; nothing else in this chain uses a
 *  real timer, so flushing the microtask queue (everything downstream of a
 *  resolved `fetch()` promise -- `.json()`, `request()`, `get()`,
 *  `getSurveyDashboards()`, the `await` in `retryBoard`/`worker`, `paintAll`)
 *  just needs a couple of real event-loop turns, not a mocked one. */
function flushTasks(times = 5) {
  return new Promise((resolve) => {
    let n = 0;
    const step = () => { if (++n >= times) resolve(); else setImmediate(step); };
    setImmediate(step);
  });
}

test('a board that never resolves shows the 30s timeout state, and clicking retry re-fetches it', async (t) => {
  const { document } = makeDomEnvironment();

  let boardFetchCalls = 0;
  let questionsFetchCalls = 0;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('board_id=')) {
      boardFetchCalls += 1;
      if (boardFetchCalls === 1) {
        // The board whose read never comes back -- exactly the case the
        // safety net exists for. Never resolves, never rejects.
        return new Promise(() => {});
      }
      // The retry's own fetch: resolves normally, as if the board finally
      // answered.
      return fakeJsonResponse({
        dashboards: [{
          id: 'test_board',
          title: 'Test Board',
          has_results: true,
          last_surveyed_at: '2026-09-28T00:00:00Z',
          analyses: [],
        }],
      });
    }
    if (u.includes('/boards')) {
      // The cheap catalog call -- not expected to fire on this path (the
      // database fast path below reads `state.analyses` instead), but
      // answered harmlessly if it does.
      return fakeJsonResponse({ boards: [] });
    }
    // getQuestions() (board ordering) and anything else -- irrelevant to
    // this test, answered harmlessly rather than left to hang or reject
    // loudly and pollute the test's own failure output.
    questionsFetchCalls += 1;
    return fakeJsonResponse({ questions: [] });
  };

  const app = await loadAppModule();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);

  // A database resource: `entityType !== 'repo'` with `state.analyses`
  // already populated takes loadByAnalysisPane's fast catalog path (no
  // `listSurveyResultBoards` network call needed), matching how a real
  // database pane reaches this code after boot's `listAnalyses`.
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'laz_local_adventureworks';
  app.state.stage = 'scouting';
  app.state.subTab = 'by_analysis';
  app.state.analyses = [
    { id: 'test_board', name: 'Test Board', description: '', intent: 'scouting' },
  ];

  t.mock.timers.enable({ apis: ['setTimeout'] });

  // Deliberately not awaited -- with one board's fetch never settling, the
  // worker loop's `await Promise.all(...)` never resolves, so awaiting this
  // call directly would hang the test forever. Everything up to and
  // including the first board's `fetch()` call runs synchronously (no real
  // async boundary sits between calling this function and that first
  // stubbed fetch), so by the time this line returns, `boardFetchCalls`
  // is already 1 and the card is already rendered in its 'loading' state.
  loadByAnalysisPaneUnawaited(app);

  assert.equal(boardFetchCalls, 1, 'the board\'s first fetch should have fired synchronously');
  assert.match(
    document.getElementById('by-analysis-cards').textContent,
    /reading/,
    'the card should show a loading/reading state before the timeout fires',
  );
  assert.doesNotMatch(
    document.getElementById('by-analysis-cards').textContent,
    /still reading — open to load/,
    'the timeout copy must not appear before BY_ANALYSIS_BOARD_TIMEOUT_MS has elapsed',
  );

  // Advance past the give-up threshold -- the fetch above still never
  // resolves, so if the safety net didn't exist (or regressed), this card
  // would still say 'loading' forever.
  t.mock.timers.tick(app.BY_ANALYSIS_BOARD_TIMEOUT_MS);

  const cardsHtml = document.getElementById('by-analysis-cards').innerHTML;
  assert.match(
    cardsHtml,
    /still reading — <button type="button" class="underline" data-retry-board="test_board">open to load<\/button>/,
    'after the 30s timeout, the card should render the "still reading — open to load" retry trigger',
  );
  assert.match(
    document.getElementById('by-analysis-contents').textContent,
    /still reading — open to load/,
    'the contents/table-of-contents row should also show the timeout copy, not just the card',
  );

  // Simulate the user clicking "open to load" -- a real DOM click, dispatched
  // through the same `[data-retry-board]` listener `paintAll()` wired up,
  // exercising `retryBoard()` exactly as a real click would (not calling it
  // directly, since it is a closure private to loadByAnalysisPane).
  const retryBtn = document.querySelector('[data-retry-board="test_board"]');
  assert.ok(retryBtn, 'retry button must exist in the rendered card');
  retryBtn.click();

  await flushTasks();

  assert.equal(boardFetchCalls, 2, 'clicking retry must re-trigger a fetch for the same board');
  const settledHtml = document.getElementById('by-analysis-cards').innerHTML;
  assert.doesNotMatch(
    settledHtml,
    /still reading — open to load/,
    'once the retried fetch resolves, the card must leave the timeout state',
  );
  assert.match(
    settledHtml,
    /data-by-analysis-card="test_board"/,
    'the board\'s card should still be present, now rendering its settled (done) state',
  );

  t.mock.timers.reset();
});

/** Fire-and-forget wrapper -- see the call site's own comment for why this
 *  is deliberately not awaited. Kept as a tiny named function (rather than
 *  inlining `app.loadByAnalysisPane()` at the call site) purely so a
 *  reader's eye lands on a name that explains the choice, not a bare call
 *  that looks like an accidentally-dropped `await`. */
function loadByAnalysisPaneUnawaited(app) {
  // eslint-disable-next-line no-void
  void app.loadByAnalysisPane();
}
