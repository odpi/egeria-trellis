/** Activity log: a cancelled step must not read as an error (TR-93). Fake fetch, real activity.js. The run's stored
 *  status and summary (workflows/survey_definition.py) decide the row: a cancelled-only run is stored `ok` with
 *  "Completed · N step(s) cancelled"; a run with errors and cancellations is stored `error` and says both; a run with only
 *  errors is unchanged. The row must show those words in the matching tone (ok vs the error tone and ✗). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const row = (id, status, summary) => ({
  id, operation: 'survey', status, summary, entity_type: 'filesystem', slug: 'loading_bay', ts: new Date().toISOString(),
});

async function render(entries) {
  delete globalThis.localStorage;
  const { window, document } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => entries });
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel();
  const of = (id) => {
    const el = document.querySelector(`[data-activity-id="${id}"]`);
    assert.ok(el, `row ${id} is rendered`);
    const badge = [...el.querySelectorAll('span')].find((s) => /ok|error/.test(s.textContent) && /state-/.test(s.className));
    return { el, badge, text: el.textContent.replace(/\s+/g, ' '), tone: badge ? badge.className : '' };
  };
  return { of };
}

test('a run with only a cancelled step shows the cancelled wording and the ok tone, not the error style', async () => {
  const { of } = await render([row('c1', 'ok', 'Completed · 1 step cancelled')]);
  const r = of('c1');
  assert.match(r.text, /Completed · 1 step cancelled/);
  assert.match(r.tone, /text-state-ok/);
  assert.doesNotMatch(r.tone, /text-state-warn/);
  assert.doesNotMatch(r.text, /✗|error/i, 'nothing on the row reads as an error');
});

test('several cancelled steps are counted in the plural, still not an error', async () => {
  const { of } = await render([row('c2', 'ok', 'Completed · 2 steps cancelled')]);
  const r = of('c2');
  assert.match(r.text, /Completed · 2 steps cancelled/);
  assert.doesNotMatch(r.tone, /text-state-warn/);
});

test('a run with an error and a cancelled step says both and keeps the error style', async () => {
  const { of } = await render([row('m1', 'error', 'Completed with 1 error · 1 step cancelled')]);
  const r = of('m1');
  assert.match(r.text, /Completed with 1 error · 1 step cancelled/);
  assert.match(r.tone, /text-state-warn/);
  assert.match(r.text, /✗ error/);
});

test('a run with only an error is unchanged: the error style and the old words', async () => {
  const { of } = await render([row('e1', 'error', 'Completed with 1 error(s)')]);
  const r = of('e1');
  assert.match(r.text, /Completed with 1 error\(s\)/);
  assert.match(r.tone, /text-state-warn/);
  assert.doesNotMatch(r.text, /cancelled/);
});

test('the status filter treats a cancelled-only run as ok, so it is not found under "error"', async () => {
  const { of } = await render([row('c3', 'ok', 'Completed · 1 step cancelled'), row('e2', 'error', 'Completed with 1 error(s)')]);
  assert.match(of('c3').tone, /text-state-ok/);
  assert.match(of('e2').tone, /text-state-warn/);
});

test('a run where all steps are cancelled is stored with status "cancelled" and shows neutral tone', async () => {
  const { of } = await render([row('c4', 'cancelled', 'Cancelled · 1 step cancelled, nothing measured')]);
  const r = of('c4');
  assert.match(r.text, /Cancelled · 1 step cancelled, nothing measured/);
  // Neutral tone (not ok, not warn/error)
  assert.doesNotMatch(r.tone, /text-state-ok/);
  assert.doesNotMatch(r.tone, /text-state-warn/);
});
