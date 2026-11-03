/** Behaviour: a run_quality_checks step that had no accepted check shows "not run · no accepted checks", never the
 *  internal state `skipped_by_design`, never as a failure, and the run's first-failure sentence skips it.
 *  Real app.js; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SENTENCE = 'no accepted checks: accept proposals in Curate first; nothing was run';
const STEPS = [
  { step: 'GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile', status: 'ok', answered_by: 'local' },
  { step: 'GovActionProcessStep::DatabaseQualitySurvey::run_quality_checks', status: 'skipped_by_design',
    detail: SENTENCE, declined_by_step: true },
];

async function setUp(steps) {
  const { document } = makeDomEnvironment();
  const runs = [{ id: 'r1', operation: 'survey', ts: new Date().toISOString(), status: 'survey-invalid', summary: 'Done',
    detail: JSON.stringify({ steps }) }];
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity/?entity_slug=')) return ok(runs);
    return ok(u.includes('/groups') ? [] : {});
  };
  ensureLoaderRegistered();
  globalThis.location = document.defaultView.location;
  globalThis.getComputedStyle = document.defaultView.getComputedStyle.bind(document.defaultView);
  globalThis.history = document.defaultView.history;
  document.defaultView.matchMedia = document.defaultView.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  return { document, app };
}

test('the step reads "not run · no accepted checks", with the full sentence on demand', async () => {
  const { document, app } = await setUp(STEPS);
  await app.openRunsList('coco');
  const li = document.getElementById('wl-detail').querySelector('[data-step-outcome="not_run"]');
  assert.ok(li, 'the declined step has its own outcome');
  assert.match(text(li), /run_quality_checks/);
  assert.match(text(li), /not run · no accepted checks/);
  assert.equal(li.querySelector('[data-step-not-run]').getAttribute('title'), SENTENCE);
  assert.doesNotMatch(document.getElementById('wl-detail').textContent, /skipped_by_design/);
});

test('it is counted as not run, not as a step that did not run', async () => {
  const { document, app } = await setUp(STEPS);
  await app.openRunsList('coco');
  const head = text(document.getElementById('wl-detail'));
  assert.match(head, /1 ran · 1 not run/);
  assert.doesNotMatch(head, /did not/);
  assert.equal(document.getElementById('wl-detail').querySelectorAll('[data-step-outcome="failed"]').length, 0);
});

test('a real failure beside it is still a failure', async () => {
  const { document, app } = await setUp([...STEPS, { step: 'S::boom', status: 'error', detail: 'it broke' }]);
  await app.openRunsList('coco');
  const d = document.getElementById('wl-detail');
  assert.equal(d.querySelectorAll('[data-step-outcome="failed"]').length, 1);
  assert.match(text(d), /1 did not/);
});

test('the first failure sentence of a run never picks the declined step', async () => {
  const { app } = await setUp(STEPS);
  const entry = { detail: JSON.stringify({ steps: STEPS }) };
  assert.equal(app.firstFailureSentence(entry), '');
  const withError = { detail: JSON.stringify({ steps: [...STEPS, { step: 'S::x', status: 'error', detail: 'Postgres refused.' }] }) };
  assert.equal(app.firstFailureSentence(withError), 'Postgres refused.');
});

test('stepOutcome: another declined step is "not run" with its sentence in the title, not our words', async () => {
  ensureLoaderRegistered();
  makeDomEnvironment();
  const { stepOutcome, statusToken } = await import('/static/next/format.js');
  assert.deepEqual(stepOutcome({ status: 'ok' }), { kind: 'ok', words: '', title: '' });
  assert.deepEqual(stepOutcome({ status: 'skipped_by_design', detail: 'ruleset file is missing' }),
    { kind: 'not_run', words: 'not run', title: 'ruleset file is missing' });
  assert.equal(stepOutcome({ status: 'error', detail: 'x' }).kind, 'failed');
  assert.equal(statusToken('skipped_by_design'), 'not run');
  assert.equal(statusToken('error'), 'error');
});
