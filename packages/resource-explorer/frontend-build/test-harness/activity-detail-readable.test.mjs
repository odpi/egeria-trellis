/** Activity dialog: a run's detail reads as a summary line plus structured detail, with the raw JSON
 *  behind a "raw" disclosure -- never a huge raw blob. The order control is untouched. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const RAW = JSON.stringify({
  analysis_id: 'dependency_analysis', published: null, error: 'connection refused',
  step_keys: ['fetch', 'parse', 'write'], nested: { retries: 3, host: 'localhost' },
});

async function open(entries) {
  const { document } = makeDomEnvironment();
  delete globalThis.localStorage;
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => entries });
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel();
  return document;
}
const entry = (detail, extra = {}) => ({ id: 'e1', operation: 'analysis_run', status: 'error', summary: 'ran', ts: '2026-10-01T00:00:00Z', detail, ...extra });

test('JSON detail: summary line, structured rows, raw behind a collapsed disclosure', async () => {
  const doc = await open([entry(RAW)]);
  const body = doc.querySelector('[id^="activity-d-"]');
  assert.ok(body.classList.contains('hidden'), 'detail stays collapsed until asked for');
  const sum = body.querySelector('[data-activity-detail-summary]');
  assert.ok(sum, 'a summary line');
  assert.match(sum.textContent, /dependency_analysis/);
  assert.match(sum.textContent, /connection refused/);
  const rows = [...body.querySelectorAll('[data-activity-detail-row]')];
  assert.ok(rows.length >= 4, 'one row per key');
  assert.ok(rows.some((r) => /nested/.test(r.textContent) && /retries/.test(r.textContent)), 'nested values are shown, compactly');
  const raw = body.querySelector('details[data-activity-raw]');
  assert.ok(raw, 'raw disclosure');
  assert.equal(raw.hasAttribute('open'), false);
  assert.equal(raw.querySelector('summary').textContent.trim(), 'raw');
  assert.equal(JSON.parse(raw.querySelector('pre').textContent).analysis_id, 'dependency_analysis');
  // the raw blob is not shown outside the disclosure
  const outside = body.cloneNode(true); outside.querySelector('[data-activity-raw]').remove();
  assert.doesNotMatch(outside.textContent, /\{"analysis_id"/);
});

test('plain-text detail keeps its old rendering and gets no raw disclosure', async () => {
  const doc = await open([entry('3 items cataloged\nall fine')]);
  const body = doc.querySelector('[id^="activity-d-"]');
  assert.match(body.textContent, /3 items cataloged/);
  assert.equal(body.querySelector('[data-activity-raw]'), null);
});

test('malformed JSON falls back to text; the detail toggle still opens it', async () => {
  const doc = await open([entry('{"a": ')]);
  const body = doc.querySelector('[id^="activity-d-"]');
  assert.match(body.textContent, /\{"a":/);
  doc.querySelector('[data-activity-toggle]').click();
  assert.equal(body.classList.contains('hidden'), false);
});

test('the order control is still there and still flips', async () => {
  const doc = await open([entry(RAW, { id: 'a', summary: 's1', ts: '2026-10-01T00:00:00Z' }), entry(RAW, { id: 'b', summary: 's2', ts: '2026-10-02T00:00:00Z' })]);
  const btn = doc.querySelector('#activity-order-btn');
  assert.match(btn.textContent, /latest first/);
  btn.click();
  assert.match(doc.querySelector('#activity-order-btn').textContent, /earliest first/);
});

test('rows are capped, with the rest pointed at raw; an empty error key adds nothing to the summary', async () => {
  const big = {}; for (let i = 0; i < 100; i += 1) big[`k${i}`] = i;
  const doc = await open([entry(JSON.stringify({ ...big, error: '' }))]);
  const body = doc.querySelector('[id^="activity-d-"]');
  assert.equal(body.querySelectorAll('[data-activity-detail-row]').length, 40);
  assert.match(body.textContent, /61 more in raw/);
  assert.doesNotMatch(body.querySelector('[data-activity-detail-summary]').textContent, /error/);
});
