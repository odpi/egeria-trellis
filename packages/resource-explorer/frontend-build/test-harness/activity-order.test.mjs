/** Activity log: order-by-time control. Fake fetch, real activity.js. The route has no
 *  order parameter, so "earliest first" reorders the loaded set and says so when cut off. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

function entries(n, { start = Date.UTC(2026, 0, 1) } = {}) {
  // server order: latest first
  return Array.from({ length: n }, (_, i) => ({
    id: `e${String(n - i).padStart(4, '0')}`, operation: 'survey', status: 'ok',
    summary: `s${n - i}`, ts: new Date(start + (n - i) * 60000).toISOString(),
  }));
}

async function open({ data, storage = 'ok', seed } = {}) {
  delete globalThis.localStorage; // a previous test may have left a throwing getter
  const { window, document } = makeDomEnvironment();
  if (storage === 'throws') {
    Object.defineProperty(window, 'localStorage', { get() { throw new window.DOMException('denied', 'SecurityError'); } });
    Object.defineProperty(globalThis, 'localStorage', { get() { throw new window.DOMException('denied', 'SecurityError'); }, configurable: true });
  } else {
    Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
    if (seed) window.localStorage.setItem('re.activity.order', seed);
  }
  const urls = [];
  globalThis.fetch = async (u) => { urls.push(u); return { ok: true, status: 200, json: async () => data }; };
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel();
  const q = (s) => document.querySelector(s);
  const summaries = () => [...document.querySelectorAll('#activity-panel-body .text-ink')]
    .filter((e) => /^s\d+$|^x/.test(e.textContent.trim())).map((e) => e.textContent.trim());
  return { window, document, q, summaries, urls, btn: () => q('#activity-order-btn'), note: () => q('#activity-order-note') };
}

test('default order is unchanged: latest first, control says so', async () => {
  const t = await open({ data: entries(3) });
  assert.deepEqual(t.summaries(), ['s3', 's2', 's1']);
  assert.match(t.btn().textContent, /↓ latest first/);
  assert.equal(t.btn().tagName, 'BUTTON');
  assert.ok(t.btn().getAttribute('aria-label'));
});

test('pressing flips the list and the label at once, and again back', async () => {
  const t = await open({ data: entries(3) });
  t.btn().click();
  assert.deepEqual(t.summaries(), ['s1', 's2', 's3']);
  assert.match(t.btn().textContent, /↑ earliest first/);
  t.btn().click();
  assert.deepEqual(t.summaries(), ['s3', 's2', 's1']);
});

test('order persists across a fresh module instance (storage present)', async () => {
  const a = await open({ data: entries(3) });
  a.btn().click();
  const stored = a.window.localStorage.getItem('re.activity.order');
  const b = await open({ data: entries(3), seed: stored });
  assert.deepEqual(b.summaries(), ['s1', 's2', 's3']);
  assert.match(b.btn().textContent, /earliest first/);
});

test('storage that throws: page works, flips, defaults to latest first', async () => {
  const t = await open({ data: entries(3), storage: 'throws' });
  assert.deepEqual(t.summaries(), ['s3', 's2', 's1']);
  t.btn().click();
  assert.deepEqual(t.summaries(), ['s1', 's2', 's3']);
});

test('equal timestamps: stable by id in the chosen direction', async () => {
  const ts = '2026-02-01T00:00:00Z';
  const data = [{ id: 'b', ts, summary: 's2' }, { id: 'c', ts, summary: 's3' }, { id: 'a', ts, summary: 's1' }];
  const t = await open({ data });
  assert.deepEqual(t.summaries(), ['s3', 's2', 's1']);
  t.btn().click();
  assert.deepEqual(t.summaries(), ['s1', 's2', 's3']);
});

test('missing timestamps go last in either order', async () => {
  const data = [
    { id: 'n1', summary: 's9' },
    { id: 'a', ts: '2026-02-01T00:00:00Z', summary: 's2' },
    { id: 'b', ts: '2026-03-01T00:00:00Z', summary: 's3' },
  ];
  const t = await open({ data });
  assert.deepEqual(t.summaries(), ['s3', 's2', 's9']);
  t.btn().click();
  assert.deepEqual(t.summaries(), ['s2', 's3', 's9']);
});

test('truncated-list line: only when cut off and only for earliest first', async () => {
  const full = await open({ data: entries(300) });
  assert.equal(full.note(), null, 'latest first: no line');
  full.btn().click();
  const note = full.note();
  assert.ok(note, 'truncated + earliest first: line shown');
  assert.match(note.textContent, /300 most recent entries, oldest first/);
  full.btn().click();
  assert.equal(full.note(), null);

  const short = await open({ data: entries(5), seed: 'asc' });
  assert.match(short.btn().textContent, /earliest first/);
  assert.equal(short.note(), null, 'not truncated: no line');
});
