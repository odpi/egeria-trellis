/** Real-DOM regression tests for the Shared Names block wording fix and the
 *  Preliminary Fit card/Shared-Names-block consistency bug, both found live
 *  on the owner's 2026-09-28 coco_pharma gate check (same round as
 *  by-analysis-headline-and-glyphs.test.mjs's two bugs) -- see
 *  BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md's
 *  follow-up section.
 *
 *  BUG 1 -- the block's header read "N DISAGREE", which the owner could not
 *  interpret ("I don't know what '1 DISAGREE' means"). The inclusion
 *  criterion was always "carried by more than one analysis"
 *  (`collectMeasures`'s `shared` map); whether the shared values actually
 *  differ is now stated in words per row (`sharedNamesHtml`), and the header
 *  names the criterion instead of a bare "DISAGREE" count.
 *
 *  BUG 2 -- the Shared Names block correctly rendered `preliminary_fit`'s
 *  no-lens confidence as "— (no lens declared)" (`measureDisplay`), but the
 *  Preliminary Fit CARD's own COUNTS table (`boardCountsHtml`) computed its
 *  display text separately with a bare `fmtScalar`, so the same value read
 *  as "0" there -- two renderings of one number disagreeing with each
 *  other on the same screen. Fixed by routing both through the same
 *  `measureDisplay` helper.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

/** Two boards that both report `confidence`, disagreeing in value, plus a
 *  third (`preliminary_fit`) that reports it as a no-lens 0 -- the exact
 *  shape from this branch's own IMPLEMENTED doc's coco_pharma live
 *  verification ("confidence · db_classification 60 · subject_signals 25 ·
 *  preliminary_fit — (no lens declared)"). */
function fixtureBoards() {
  const now = '2026-09-28T10:00:00Z';
  return [
    {
      id: 'db_classification', has_results: true, last_surveyed_at: now,
      analyses: [{ analysis_id: 'db_classification', last_surveyed_at: now, results: { confidence: 60 } }],
    },
    {
      id: 'subject_signals', has_results: true, last_surveyed_at: now,
      analyses: [{ analysis_id: 'subject_signals', last_surveyed_at: now, results: { confidence: 25 } }],
    },
    {
      id: 'preliminary_fit', has_results: true, last_surveyed_at: now,
      analyses: [{ analysis_id: 'preliminary_fit', last_surveyed_at: now, results: { confidence: 0, lens_declared: false } }],
    },
  ];
}

function collect(app, boards) {
  const boardState = new Map(boards.map((b) => [b.id, { status: 'done', board: b }]));
  return { ...app.collectMeasures(boards, boardState), boardState };
}

test('the Shared Names header names the criterion ("carried by more than one analysis"), never "DISAGREE"', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="host"></div>';

  const boards = fixtureBoards();
  const { shared, disagreeing } = collect(app, boards);

  assert.ok(shared.has('confidence'), 'confidence is carried by three analyses -- it must be in the shared set');
  assert.ok(disagreeing.has('confidence'), 'the three comparable/no-lens-excluded values genuinely differ (60 vs 25)');

  const host = document.getElementById('host');
  host.innerHTML = app.sharedNamesHtml(shared, disagreeing);

  assert.doesNotMatch(host.textContent, /DISAGREE/, 'no "DISAGREE" wording anywhere in the block');
  assert.match(host.textContent, /name carried by more than one analysis/, 'singular "name" -- this fixture has exactly one shared NAME ("confidence"), reported by three analyses');
  assert.match(host.textContent, /— \(no lens declared\)/, 'preliminary_fit\'s no-lens confidence must still read as "no lens declared", not a bare 0');
  assert.doesNotMatch(host.textContent, /confidence[^)]*\b0\b(?!\))/, 'the no-lens value must never render as a bare "0"');
});

test('the Preliminary Fit card\'s own COUNTS table renders the identical "no lens declared" text the Shared Names block uses for the same value', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="host"></div>';

  const boards = fixtureBoards();
  const { disagreeing } = collect(app, boards);
  const fitBoard = boards.find((b) => b.id === 'preliminary_fit');

  const host = document.getElementById('host');
  host.innerHTML = app.boardCountsHtml(fitBoard, disagreeing);

  assert.match(
    host.textContent,
    /confidence[\s\S]*— \(no lens declared\)/,
    'Preliminary Fit\'s own COUNTS row for confidence must say "— (no lens declared)", matching the Shared Names block, not a bare "0"',
  );
  assert.doesNotMatch(
    host.querySelector('td.tnum')?.textContent || '',
    /^0$/,
    'the confidence value cell must not be a bare "0"',
  );
});

test('a name reported by only one analysis is not in the shared set (the inclusion criterion is genuinely "more than one")', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const now = '2026-09-28T10:00:00Z';
  const boards = [{
    id: 'row_count_snapshot', has_results: true, last_surveyed_at: now,
    analyses: [{ analysis_id: 'row_count_snapshot', last_surveyed_at: now, results: { row_count: 12345 } }],
  }];
  const { shared } = collect(app, boards);
  assert.ok(!shared.has('row_count'), 'a name only one analysis reports must not appear in the Shared Names block');
});
