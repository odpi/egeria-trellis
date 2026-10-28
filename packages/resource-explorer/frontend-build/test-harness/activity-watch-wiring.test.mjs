/** Backlog 7f.6: the header's activity watch hands each page it reads to the open Activity list. Drives the real
 *  wiring: app.js's `activityWatchEntries` as `startActivityWatch`'s `fetchEntries`, the real activity.js panel
 *  (the same module instance app.js imports), only fetch stubbed. Removing the noticeActivity call fails this. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const row = (id, status, summary) => ({ id, operation: 'survey', status, summary, ts: '2026-10-09T10:00:00' });

test('a watch tick tells an open, idle Activity list that a scan started', async () => {
  const { document, window } = makeDomEnvironment();
  const rows = { current: [row('a1', 'ok', 'earlier')] };
  let fetches = 0;
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity')) { fetches += 1; return ok(rows.current); }
    return ok(u.includes('/groups') ? [] : {});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const activity = await import('/static/next/stages/activity.js');
  const { startActivityWatch } = await import('/static/next/activity-unread.js');

  await activity.openActivityPanel();
  const body = () => document.getElementById('activity-panel-body').textContent;
  assert.doesNotMatch(body(), /Database Scouting Scan/);

  const watch = startActivityWatch({ doc: document, first: rows.current,
    fetchEntries: app.activityWatchEntries(), openPanel: () => {}, intervalMs: 1e9 });
  try {
    rows.current = [row('s1', 'running', 'Database Scouting Scan'), row('a1', 'ok', 'earlier')];
    api.clearCache();
    const before = fetches;
    await watch.tick();
    await sleep(30);
    assert.ok(fetches >= before + 2, `the open list did not re-read (fetches ${before} -> ${fetches})`);
    assert.match(body(), /Database Scouting Scan/);
  } finally {
    watch.stop();
    document.querySelector('#activity-panel [data-act="close"]')?.click();
  }
});
