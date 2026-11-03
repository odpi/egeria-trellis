/** readUrl() keeps a `?type=` only when it names a kind (repo | db | filesystem). `?type=database` (the real value
 *  is `db`) is said and set aside: the kind is then resolved from the slug, and nothing paints a database over the
 *  file-system list's state. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load(search, { type = 'repo' } = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.history.replaceState(null, '', `/next${search}`);
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: type, selectedSlug: null, stage: 'scouting', subTab: 'questions', workListSlug: null, activePerspectives: new Set(), unknownType: '' });
  app.readUrl();
  return { app, window, document };
}
const qs = (window) => new URLSearchParams(window.location.search);

test('an unknown type is not taken as the kind, is remembered to be said, and the URL drops it', async () => {
  const { app, window } = await load('?type=database&resource=localhost_docker_coco_pharma', { type: 'filesystem' });
  assert.equal(app.state.resourceType, 'filesystem');
  assert.equal(app.state.unknownType, 'database');
  assert.equal(app.state.urlTypeMissing, true, 'the kind is resolved from the slug, not from the bad value');
  assert.notEqual(qs(window).get('type'), 'database', 'the bad value does not stay in the URL');
});

test('a real type is kept and nothing is said', async () => {
  const { app } = await load('?type=db&resource=aw');
  assert.equal(app.state.resourceType, 'db');
  assert.equal(app.state.unknownType, '');
  assert.equal(app.state.urlTypeMissing, false);
});
