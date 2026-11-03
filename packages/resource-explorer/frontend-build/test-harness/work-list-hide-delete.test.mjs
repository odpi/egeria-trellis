/** Behavior: W38. Hide is a per-person view preference drawn under "Show hidden N" (visible + hidden = all on screen);
 *  Delete lives on the list's own page behind a confirmation that says what stays and that it cannot be undone.
 *  Real app.js (sidebar arithmetic), worklist.js (words) and re-api.js (the two calls) against jsdom; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function setUp() {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ method: (options.method || 'GET').toUpperCase(), url: String(url), body: options.body });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const wl = await import('/static/next/work-list-delete.js');
  return { app, wl, api, calls, document };
}
const L = (slug, hidden, extra = {}) => ({ slug, display_name: slug, hidden, member_count: 1, investigation: '', ...extra });

test('the sidebar counts follow the visible lists and "Show hidden N" carries the rest', async () => {
  const { app } = await setUp();
  const all = [L('a', false), L('b', true), L('c', true), L('d', false), L('suggested-to-x', false)];
  app.state.workLists = all;
  app.state.showHiddenLists = false;
  let s = app.splitWorkLists();
  assert.equal(s.benches.length, 2);
  assert.equal(s.hiddenCount, 2);
  assert.equal(s.benches.length + s.hiddenCount, all.filter((w) => !w.slug.startsWith('suggested-to-')).length, 'visible + hidden = all');
  app.state.showHiddenLists = true;
  s = app.splitWorkLists();
  assert.equal(s.benches.length, 4, 'Show hidden brings them back');
  app.state.showHiddenLists = false;
});

test('every list hidden: the section says none are shown and keeps the Show hidden line', async () => {
  const { app, document } = await setUp();
  app.state.workLists = [L('a', true), L('b', true)];
  app.state.showHiddenLists = false;
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  app.renderSidebar();
  assert.match(side.textContent, /no work lists shown/);
  assert.match(side.querySelector('[data-act="show-hidden-lists"]').textContent.replace(/\s+/g, ' '), /Show hidden 2/);
});

test('the delete confirmation says what stays, what the Egeria Collection does, and that it cannot be undone', async () => {
  const { wl } = await setUp();
  const published = wl.deleteConfirmMessage({ display_name: 'Test list', entity_type: 'database', egeria_guid: 'abcdef1234567' }, 3);
  assert.equal(published, "Removes 'Test list' from Resource Explorer. Its 3 databases stay registered. The Egeria Collection it published stays in Egeria (abcdef12). This cannot be undone.");
  const never = wl.deleteConfirmMessage({ display_name: 'T', entity_type: 'filesystem', egeria_guid: '' }, 1);
  assert.match(never, /Its 1 file systems stay registered\. It was never published to Egeria\. This cannot be undone\.$/);
});

test('hide and delete call the two routes', async () => {
  const { api, calls } = await setUp();
  await api.setWorkListHidden('wl-1', true);
  await api.deleteWorkList('wl-1');
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [['PUT', '/api/work-lists/wl-1/hidden'], ['DELETE', '/api/work-lists/wl-1']]);
  assert.deepEqual(JSON.parse(calls[0].body), { hidden: true });
});
