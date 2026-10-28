/** Activity list: a running row turns finished, and new rows appear, while the panel stays open.
 *  Owner 2026-10-09: a Database Scouting Scan finished but the open list never showed it. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function open(rows) {
  const { document } = makeDomEnvironment();
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; return { ok: true, status: 200, json: async () => rows.current }; };
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  if (mod.refreshConfig) { mod.refreshConfig.intervalMs = 20; mod.refreshConfig.maxTicks = 5; }
  await mod.openActivityPanel();
  return { document, mod, fetches: () => fetches };
}
const row = (id, status, summary) => ({ id, operation: 'scout', status, summary, ts: '2026-10-09T10:00:00' });

test('an open list turns a running row finished without reload, then stops polling', async () => {
  const rows = { current: [row('a1', 'running', 'Database Scouting Scan')] };
  const t = await open(rows);
  const body = () => t.document.getElementById('activity-panel-body').textContent;
  assert.match(body(), /running/);
  rows.current = [row('a2', 'ok', 'Newer entry'), row('a1', 'ok', 'Database Scouting Scan')];
  await sleep(80);
  assert.doesNotMatch(body(), /running/);
  assert.match(body(), /Newer entry/);
  const n = t.fetches();
  await sleep(80);
  assert.equal(t.fetches(), n, 'no polling once nothing is running');
  t.document.querySelector('[data-act="close"]')?.click();
});

test('polling is bounded', async () => {
  const t = await open({ current: [row('a1', 'running', 'stuck')] });
  await sleep(250);
  assert.ok(t.fetches() <= 1 + 5, `bounded, got ${t.fetches()}`);
  t.document.querySelector('[data-act="close"]')?.click();
});

test('polling stops when the panel closes', async () => {
  const u = await open({ current: [row('b1', 'running', 'x')] });
  u.document.querySelector('[data-act="close"]').click();
  const n = u.fetches();
  await sleep(80);
  assert.equal(u.fetches(), n);
});

test('an all-finished list never polls', async () => {
  const t = await open({ current: [row('a1', 'ok', 'done')] });
  await sleep(80);
  assert.equal(t.fetches(), 1);
  t.document.querySelector('[data-act="close"]')?.click();
});

/* Backlog 7f.6, the case the running-row poll cannot see: the list was opened while nothing ran (so it does
 * not poll), then a scan started. The header's 30 s activity watch hands its page to noticeActivity. */
test('an idle open list picks up a scan that started after it opened, then follows it to the end', async () => {
  const rows = { current: [row('a1', 'ok', 'earlier')] };
  const t = await open(rows);
  const body = () => t.document.getElementById('activity-panel-body').textContent;
  await sleep(60);
  assert.equal(t.fetches(), 1, 'an all-finished list does not poll');
  rows.current = [row('s1', 'running', 'Database Scouting Scan'), row('a1', 'ok', 'earlier')];
  assert.equal(await t.mod.noticeActivity(rows.current), true);
  assert.match(body(), /Database Scouting Scan/);
  assert.match(body(), /running/);
  rows.current = [row('s1', 'error', 'Database Scouting Scan: 3 of 3 step(s) ran, 1 error(s)'), row('a1', 'ok', 'earlier')];
  await sleep(80);
  assert.doesNotMatch(body(), /running/);
  assert.match(body(), /3 of 3 step/);
  t.document.querySelector('[data-act="close"]')?.click();
});

test('noticeActivity does nothing for a page the list already shows, or when the panel is closed', async () => {
  const rows = { current: [row('a1', 'ok', 'done')] };
  const t = await open(rows);
  assert.equal(await t.mod.noticeActivity(rows.current), false);
  assert.equal(t.fetches(), 1);
  t.document.querySelector('[data-act="close"]').click();
  assert.equal(await t.mod.noticeActivity([row('z9', 'running', 'x')]), false);
  assert.equal(t.fetches(), 1);
});

test('an entry the open list filters out costs one re-read, not one per watch tick', async () => {
  const rows = { current: [row('a1', 'ok', 'done')] };
  const t = await open(rows);
  const page = [row('n1', 'ok', 'filtered elsewhere'), row('a1', 'ok', 'done')];
  // the server keeps answering without n1 (as a filtered list would)
  assert.equal(await t.mod.noticeActivity(page), true);
  assert.equal(await t.mod.noticeActivity(page), false);
  assert.equal(t.fetches(), 2);
  t.document.querySelector('[data-act="close"]')?.click();
});

test('after the poll budget runs out, a finished scan still turns the row finished', async () => {
  const rows = { current: [row('s1', 'running', 'Database Scouting Scan')] };
  const t = await open(rows);   // maxTicks 5, intervalMs 20
  const body = () => t.document.getElementById('activity-panel-body').textContent;
  await sleep(250);             // budget spent; the list still says running
  assert.match(body(), /running/);
  const n = t.fetches();
  rows.current = [row('s1', 'ok', 'Database Scouting Scan')];
  assert.equal(await t.mod.noticeActivity(rows.current), true);
  assert.doesNotMatch(body(), /running/);
  assert.equal(t.fetches(), n + 1);
  assert.equal(await t.mod.noticeActivity(rows.current), false, 'one re-read per change');
  t.document.querySelector('[data-act="close"]')?.click();
});

test('closing the panel forgets what it noticed', async () => {
  const rows = { current: [row('a1', 'ok', 'done')] };
  const t = await open(rows);
  const page = [row('n1', 'ok', 'filtered elsewhere'), row('a1', 'ok', 'done')];
  assert.equal(await t.mod.noticeActivity(page), true);
  t.document.querySelector('[data-act="close"]').click();
  await t.mod.openActivityPanel();
  assert.equal(await t.mod.noticeActivity(page), true, 'a reopened list reacts again');
  t.document.querySelector('[data-act="close"]')?.click();
});
