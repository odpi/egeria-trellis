/** Real-DOM regression test for the engine-note persistence bug (fixed
 *  2026-09-28, branch `re/engine-note-persist`,
 *  ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md).
 *
 *  THE BUG: `launchSurvey()` used to append the "which engine ran this"
 *  line as a transient DOM write into a `#survey-note` div, then call
 *  `loadSurveyPane()` a few lines later -- which re-renders the WHOLE
 *  pane, including a fresh, empty `#survey-note`, wiping the note right
 *  after writing it. Every pre-existing test in this package (grep
 *  `tests/test_next_*.py`) asserts only against app.js's source TEXT --
 *  "does the code call the right function" -- which cannot catch this: the
 *  code called the right functions, in the right order, and the bug was
 *  still real. Only rendering the DOM twice and checking what survives the
 *  SECOND render catches it.
 *
 *  THE FIX: the note is now derived from data on every render
 *  (`engineNoteHtml(c)`, reading `c.last_run_engine_note`) inside
 *  `surveyRowHtml(c)` -- the definition's own row -- rather than being a
 *  one-time side-effecting append. Deriving it from data means it survives
 *  ANY re-render, by construction, because there is no transient state to
 *  lose.
 *
 *  THIS TEST exercises the real, unmodified `surveyRowHtml` (now exported
 *  -- see app.js's own comment above that export) against a fixture
 *  candidate object, rendered into a real jsdom container, then simulates
 *  exactly what `loadSurveyPane()` does on a reload/pane-switch: replace
 *  the container's `innerHTML` wholesale with a freshly rendered pass over
 *  the same candidate list. It asserts the engine-note line is present,
 *  correct, AND survives that second render -- the exact property the bug
 *  broke and the fix restores.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

function fixtureCandidate(overrides = {}) {
  return {
    display_name: 'Database Analysis Survey',
    qualified_name: 'SurveyDefinition::database-analysis',
    steps: [],
    last_run_at: '2026-09-28T03:15:42Z',
    last_run_engine_note: 'ran via Prefect (flow-run 89904e0c-1234-5678-9abc-def012345678)',
    ...overrides,
  };
}

/** Mimics `loadSurveyPane()`'s own re-render: wipe the pane container and
 *  render every candidate's row fresh from the same data. app.js's real
 *  `loadSurveyPane` is not called directly (it fetches over the network,
 *  which this harness deliberately has no server for -- see dom-harness.mjs's
 *  header comment) -- but the DOM operation under test is exactly this one:
 *  `el.innerHTML = candidates.map(surveyRowHtml).join('')`, which is what
 *  `loadSurveyPane` itself does with the rows `getSurveyCandidates` returns. */
function renderSurveyPane(container, surveyRowHtml, candidates) {
  container.innerHTML = candidates.map(surveyRowHtml).join('');
}

test('engine note survives a pane re-render (candidate carries it via data, not a transient DOM append)', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate();

  // First render -- the initial pane load.
  renderSurveyPane(container, app.surveyRowHtml, [candidate]);
  assert.match(
    container.textContent,
    /ran via Prefect \(flow-run 89904e0c…, 03:15Z\)/,
    'engine note missing on the FIRST render -- surveyRowHtml/engineNoteHtml regressed',
  );

  // Second render -- what a pane switch or `loadSurveyPane()`'s post-launch
  // reload does: the container is replaced wholesale from the same
  // candidate data. This is the exact operation the bug broke: pre-fix,
  // the note lived only in a `#survey-note` div a different code path had
  // written into, which this replacement wipes with no way to recover it.
  renderSurveyPane(container, app.surveyRowHtml, [candidate]);
  assert.match(
    container.textContent,
    /ran via Prefect \(flow-run 89904e0c…, 03:15Z\)/,
    'engine note did NOT survive the re-render -- this is the engine-note-persistence bug',
  );
});

test('the fallback (local-execution) engine note also survives a re-render, with its own styling', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate({
    last_run_engine_note: 'Prefect API unreachable at http://localhost:4200: ran locally',
  });

  renderSurveyPane(container, app.surveyRowHtml, [candidate]);
  renderSurveyPane(container, app.surveyRowHtml, [candidate]);

  assert.match(container.textContent, /ran locally: Prefect dispatch failed/);
  const warnLine = [...container.querySelectorAll('.text-state-warn')]
    .find((el) => /ran locally/.test(el.textContent));
  assert.ok(warnLine, 'fallback note should render with warn styling and survive the re-render');
});

test('no last_run_engine_note (never run, or a run before this field existed) renders no note line and no error', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = document.createElement('div');
  document.body.appendChild(container);

  const candidate = fixtureCandidate({ last_run_engine_note: '' });
  renderSurveyPane(container, app.surveyRowHtml, [candidate]);
  renderSurveyPane(container, app.surveyRowHtml, [candidate]);

  assert.doesNotMatch(container.textContent, /ran via Prefect|ran locally/);
});
