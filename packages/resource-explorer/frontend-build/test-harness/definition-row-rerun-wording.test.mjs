/** Real-DOM regression test for the definition-row "re-run" wording bug
 *  (Backlog.md, logged 2026-09-27, dispatched 2026-09-29,
 *  DEFINITION-ROW-RERUN-WORDING-IMPLEMENTED.md).
 *
 *  THE BUG: on the Survey & analyses pane, the SURVEY DEFINITION row's own
 *  run button (`surveyRowHtml(c)`) always read "Run", even once a run had
 *  already happened for that definition on the current resource. The
 *  analyses list directly below it (`analysisIndexRowHtml(row)`) already
 *  got this right: `${row.last_run_at ? 're-run' : 'run'} →`. The
 *  definition row rendered `c.last_run_at` elsewhere on the same row (via
 *  `lastRunHtml(c)`, e.g. "✓ ran 3 hours ago") but never consulted it for
 *  its own button label -- it always emitted the literal string "Run →".
 *
 *  THE FIX: `surveyRowHtml`'s button now applies the exact same rule the
 *  analyses list already uses, reading the exact same field
 *  (`c.last_run_at`, already present on the candidate object and already
 *  used by this row's own `lastRunHtml`/`engineNoteHtml`) --
 *  `${c.last_run_at ? 're-run' : 'run'} →`.
 *
 *  ONE FUNCTION, BOTH PANES: `surveyRowHtml` is the single render function
 *  used for survey-definition rows on both the Scouting and Discovery
 *  panes -- both go through the same `loadSurveyPane()` -> `byTier.get(t)
 *  .map(surveyRowHtml)` path (app.js), which pane is showing is decided
 *  only by which `stage`/`phase` value was fetched, not by a different
 *  render function. So one test of `surveyRowHtml` covers both panes;
 *  there is no separate Discovery-only or Scouting-only rendering path to
 *  test.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

function fixtureCandidate(overrides = {}) {
  return {
    display_name: 'Database Analysis Survey',
    qualified_name: 'SurveyDefinition::database-analysis',
    steps: [],
    ...overrides,
  };
}

test('definition row shows "run" (not re-run) when this definition has never run on this resource', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate({ last_run_at: '' });
  container.innerHTML = app.surveyRowHtml(candidate);

  const btn = container.querySelector('[data-run-survey]');
  assert.ok(btn, 'expected the definition row run button to render');
  assert.equal(btn.textContent.trim(), 'Run…', `expected "Run…", got ${JSON.stringify(btn.textContent)}`);
  assert.doesNotMatch(btn.textContent, /re-run/i);
});

test('definition row shows "re-run" once ANY run exists for this definition on this resource', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate({
    last_run_at: '2026-09-28T03:15:42Z',
    last_run_status: 'ok',
  });
  container.innerHTML = app.surveyRowHtml(candidate);

  const btn = container.querySelector('[data-run-survey]');
  assert.ok(btn, 'expected the definition row run button to render');
  assert.equal(btn.textContent.trim(), 'Re-run…', `expected "Re-run…", got ${JSON.stringify(btn.textContent)}`);
});

test('definition row re-run wording survives a pane re-render, same as the rest of the row', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate({ last_run_at: '2026-09-20T10:00:00Z', last_run_status: 'ok' });

  // First render.
  container.innerHTML = app.surveyRowHtml(candidate);
  // Second render -- what a pane switch (Scouting <-> Discovery) or
  // loadSurveyPane()'s post-launch reload does: replace the row wholesale
  // from the same candidate data, exactly like engine-note-persistence.test.mjs
  // exercises for the engine note on this same row.
  container.innerHTML = app.surveyRowHtml(candidate);

  const btn = container.querySelector('[data-run-survey]');
  assert.equal(btn.textContent.trim(), 'Re-run…');
});

/* Brief section 8 (owner, 2026-10-07): "same word, same effect". The definition row's button OPENS A
 * DIALOG, so it ends in an ellipsis; the analyses rows' button runs at once and keeps "re-run →". */
