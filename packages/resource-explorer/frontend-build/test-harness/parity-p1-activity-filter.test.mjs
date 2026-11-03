/** Parity P1, PI-122: the Activity advanced filter (resource kind, stage, operation, status, since), sent to the
 *  server so it applies before the page limit. Real activity.js against a fake fetch that records the query. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = () => new Promise((r) => setTimeout(r, 15));

const ALL = [
  { id: 'a1', operation: 'survey', intent: 'scouting', entity_type: 'repo', entity_slug: 'r1', status: 'ok', summary: 's-ok', ts: '2026-10-08T10:00:00' },
  { id: 'a2', operation: 'publish', intent: 'curate', entity_type: 'database', entity_slug: 'd1', status: 'error', summary: 's-bad', ts: '2026-10-08T09:00:00' },
];

async function open({ responder } = {}) {
  delete globalThis.localStorage;
  const { window, document } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  const queries = [];
  globalThis.fetch = async (u) => {
    const url = new URL(String(u), 'https://x.invalid');
    const q = Object.fromEntries(url.searchParams);
    queries.push(q);
    const data = responder ? responder(q) : ALL;
    return { ok: true, status: 200, json: async () => data };
  };
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel();
  return { window, document, queries, mod, q: (s) => document.querySelector(s) };
}

const pick = async (t, key, value) => {
  const sel = t.q(`[data-activity-adv="${key}"]`);
  sel.value = value;
  sel.dispatchEvent(new t.window.Event('change'));
  await tick();
};

test('the filters are offered under a disclosure, none set at first, and the first read is unfiltered', async () => {
  const t = await open();
  assert.ok(t.q('#activity-advanced'));
  for (const k of ['entityType', 'intent', 'operation', 'since']) assert.ok(t.q(`[data-activity-adv="${k}"]`), k);
  assert.deepEqual(t.queries, [{ limit: '300' }]);
  assert.equal(t.q('#activity-advanced').hasAttribute('open'), false);
});

test('choosing each filter sends its server parameter, and the others stay out of the query', async () => {
  const t = await open();
  await pick(t, 'entityType', 'database');
  assert.deepEqual(t.queries.at(-1), { limit: '300', entity_type: 'database' });
  await pick(t, 'intent', 'curate');
  assert.deepEqual(t.queries.at(-1), { limit: '300', entity_type: 'database', intent: 'curate' });
  await pick(t, 'operation', 'publish');
  assert.equal(t.queries.at(-1).operation, 'publish');
  assert.match(t.q('#activity-advanced summary').textContent, /3 set/);
  assert.equal(t.q('#activity-advanced').hasAttribute('open'), true, 'a set filter keeps the disclosure open');
});

test('a status chip is sent to the server as status; "all" sends none', async () => {
  const t = await open();
  t.q('[data-activity-status="error"]').click();
  await tick();
  assert.equal(t.queries.at(-1).status, 'error');
  t.q('[data-activity-status="all"]').click();
  await tick();
  assert.equal('status' in t.queries.at(-1), false);
});

test('since sends a log-shaped timestamp (no zone suffix) in the past', async () => {
  const t = await open();
  const before = Date.now();
  await pick(t, 'since', '24h');
  const since = t.queries.at(-1).since;
  assert.match(since, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/);
  const ms = Date.parse(`${since}Z`);
  assert.ok(Math.abs(before - 86400e3 - ms) < 5000, 'about 24 hours before now');
});

test('an empty filtered answer says nothing matches the filters, not that nothing was ever recorded', async () => {
  const t = await open({ responder: (q) => (q.entity_type ? [] : ALL) });
  await pick(t, 'entityType', 'file');
  assert.ok(t.q('[data-activity-none-match]'));
  assert.doesNotMatch(t.q('#activity-panel-body').textContent, /No operations recorded yet/);
});

test('clear filters resets every filter and reads the unfiltered log again', async () => {
  const t = await open();
  await pick(t, 'intent', 'curate');
  t.q('[data-activity-adv-clear]').click();
  await tick();
  assert.deepEqual(t.queries.at(-1), { limit: '300' });
  assert.equal(t.q('[data-activity-adv-clear]'), null);
});

test('the text filter still works on the loaded page and does not re-ask the server', async () => {
  const t = await open();
  const n = t.queries.length;
  const input = t.q('#activity-filter-text');
  input.value = 'd1';
  input.dispatchEvent(new t.window.Event('input'));
  assert.equal(t.queries.length, n);
  assert.match(t.q('#activity-panel-body').textContent, /s-bad/);
  assert.doesNotMatch(t.q('#activity-panel-body').textContent, /s-ok/);
});

test('sinceValue is empty for any time', async () => {
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  assert.equal(mod.sinceValue(''), '');
  assert.equal(mod.sinceValue('bogus'), '');
});
