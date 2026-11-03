/** Parity P1, PI-124: the Activity unread badge, and the toast that jumps to an entry. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = () => new Promise((r) => setTimeout(r, 15));

const E = (id, ts, status = 'ok', summary = `sum-${id}`) => ({ id, ts, status, summary, operation: 'survey', entity_slug: 'r', entity_type: 'repo' });

async function env() {
  delete globalThis.localStorage;
  const { window, document } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  document.body.innerHTML = '<button id="activity-open-btn">Activity <span id="activity-unread" class="hidden"></span></button>';
  const u = await import(`/static/next/activity-unread.js?t=${Math.random()}`);
  u.resetSeenForTests();
  return { window, document, u };
}

test('pure: unread is strictly newer than the mark; no mark flags nothing; unreadable times are never counted', async () => {
  const { u } = await env();
  const es = [E('a', '2026-10-08T10:00:00'), E('b', '2026-10-08T11:00:00'), { id: 'c', ts: 'garbage' }];
  assert.deepEqual(u.unreadEntries(es, '2026-10-08T10:00:00').map((e) => e.id), ['b']);
  assert.deepEqual(u.unreadEntries(es, null), []);
  assert.equal(u.newestTs(es), '2026-10-08T11:00:00');
  assert.equal(u.newestTs([]), null);
});

test('badge label: none is empty, 1 to 9 exact, more is 9+', async () => {
  const { u } = await env();
  assert.deepEqual([0, 1, 9, 10, 250].map(u.badgeLabel), ['', '1', '9', '9+', '9+']);
});

test('first visit sets a baseline: nothing is flagged and no toast appears', async () => {
  const { document, u } = await env();
  const st = {};
  const r = u.applyPoll(document, [E('a', '2026-10-08T10:00:00'), E('b', '2026-10-08T11:00:00')], st);
  assert.equal(r.unread, 0);
  assert.equal(document.getElementById('activity-unread').classList.contains('hidden'), true);
  assert.equal(document.getElementById('activity-toast'), null);
});

test('a new entry after the mark shows the count with a word and raises a toast once', async () => {
  const { document, u } = await env();
  const st = {};
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st);
  const r = u.applyPoll(document, [E('b', '2026-10-08T11:00:00', 'error', 'Publish failed'), E('a', '2026-10-08T10:00:00')], st);
  assert.equal(r.unread, 1);
  const badge = document.getElementById('activity-unread');
  assert.equal(badge.textContent, '1 new');
  assert.equal(badge.classList.contains('hidden'), false);
  const toast = document.getElementById('activity-toast');
  assert.match(toast.textContent, /Publish failed/);
  document.getElementById('activity-toast').remove();
  const again = u.applyPoll(document, [E('b', '2026-10-08T11:00:00', 'error'), E('a', '2026-10-08T10:00:00')], st);
  assert.equal(again.announced, 0, 'the same entry is not announced twice');
  assert.equal(document.getElementById('activity-toast'), null);
});

test('a still-running entry counts as unread but does not raise a toast', async () => {
  const { document, u } = await env();
  const st = {};
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st);
  const r = u.applyPoll(document, [E('b', '2026-10-08T11:00:00', 'running'), E('a', '2026-10-08T10:00:00')], st);
  assert.equal(r.unread, 1);
  assert.equal(r.announced, 0);
  assert.equal(document.getElementById('activity-toast'), null);
});

test('the toast View button opens the panel on exactly that entry', async () => {
  const { document, u } = await env();
  const opened = [];
  const st = {};
  const opts = { onView: (e) => opened.push(e.id) };
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st, opts);
  u.applyPoll(document, [E('b', '2026-10-08T11:00:00'), E('c', '2026-10-08T12:00:00', 'error'), E('a', '2026-10-08T10:00:00')], st, opts);
  const toast = document.getElementById('activity-toast');
  assert.match(toast.textContent, /\+1 more/);
  toast.querySelector('[data-toast-view]').click();
  assert.deepEqual(opened, ['c'], 'the newest of the new entries');
  assert.equal(document.getElementById('activity-toast'), null);
});

test('opening the panel marks what it showed as seen and clears the badge, and the next poll agrees', async () => {
  const { document, u } = await env();
  const st = {};
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st);
  const page = [E('b', '2026-10-08T11:00:00'), E('a', '2026-10-08T10:00:00')];
  u.applyPoll(document, page, st);
  assert.equal(document.getElementById('activity-unread').textContent, '1 new');
  u.markSeen(document, page);
  assert.equal(document.getElementById('activity-unread').classList.contains('hidden'), true);
  const r = u.applyPoll(document, page, st);
  assert.equal(r.unread, 0);
});

test('storage that throws: the badge still clears in memory and nothing crashes', async () => {
  const { window, document, u } = await env();
  Object.defineProperty(globalThis, 'localStorage', { get() { throw new window.DOMException('denied', 'SecurityError'); }, configurable: true });
  const st = {};
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st);
  const page = [E('b', '2026-10-08T11:00:00'), E('a', '2026-10-08T10:00:00')];
  assert.equal(u.applyPoll(document, page, st).unread, 1);
  u.markSeen(document, page);
  assert.equal(u.applyPoll(document, page, st).unread, 0);
  delete globalThis.localStorage;
});

test('the toast writes the stored summary as text, never as markup', async () => {
  const { document, u } = await env();
  u.showActivityToast(document, [E('x', '2026-10-08T11:00:00', 'ok', '<img src=x onerror=alert(1)>')]);
  assert.equal(document.querySelector('#activity-toast img'), null);
  assert.match(document.getElementById('activity-toast').textContent, /<img src=x/);
});

test('no toast while the Activity panel is already open', async () => {
  const { document, u } = await env();
  const st = {};
  u.applyPoll(document, [E('a', '2026-10-08T10:00:00')], st);
  const panel = document.createElement('div'); panel.id = 'activity-panel'; document.body.appendChild(panel);
  u.applyPoll(document, [E('b', '2026-10-08T11:00:00'), E('a', '2026-10-08T10:00:00')], st);
  assert.equal(document.getElementById('activity-toast'), null);
});

test('startActivityWatch: a poll after boot raises the badge, and the entries on the page at boot are not announced', async () => {
  const { document, u } = await env();
  u.writeSeen('2026-10-08T10:00:00');
  let feed = [E('b', '2026-10-08T11:00:00', 'ok'), E('a', '2026-10-08T10:00:00')];
  const w = u.startActivityWatch({ doc: document, first: feed, fetchEntries: async () => feed, openPanel() {}, intervalMs: 1e9 });
  assert.equal(document.getElementById('activity-unread').textContent, '1 new');
  assert.equal(document.getElementById('activity-toast'), null, 'boot-time entries are badged, not toasted');
  feed = [E('c', '2026-10-08T12:00:00', 'ok'), ...feed];
  await w.tick();
  assert.equal(document.getElementById('activity-unread').textContent, '2 new');
  assert.ok(document.getElementById('activity-toast'));
  w.stop();
});

test('opening the panel with a focus id scrolls to that entry, opens its detail and marks it', async () => {
  const { window, document } = await env();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => [
    { ...E('b', '2026-10-08T11:00:00'), detail: JSON.stringify({ error: 'x' }) }, E('a', '2026-10-08T10:00:00')] });
  window.HTMLElement.prototype.scrollIntoView = function () { this.dataset.scrolled = '1'; };
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel({ focusId: 'b' });
  const row = [...document.querySelectorAll('[data-activity-id]')].find((r) => r.dataset.activityId === 'b');
  assert.ok(row.hasAttribute('data-activity-focused'));
  assert.equal(row.dataset.scrolled, '1');
  assert.equal(row.querySelector('[id^="activity-d-"]').classList.contains('hidden'), false, 'detail opened');
});

test('a focus id that is not in the loaded page is said, not ignored', async () => {
  const { document } = await env();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => [E('a', '2026-10-08T10:00:00')] });
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel({ focusId: 'gone' });
  assert.ok(document.querySelector('[data-activity-focus-missing]'));
});
