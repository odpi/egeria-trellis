/** readUrl() keeps a `?tab=` only when the strip for that stage and kind offers it; anything else lands on the
 *  stage's first tab and the URL is rewritten. The old "deferred" stub pane (data-deferred) is gone. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, readdirSync } from 'node:fs';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load(search, { type = 'repo' } = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.history.replaceState(null, '', `/next${search}`);
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: type, selectedSlug: null, stage: 'scouting', subTab: 'questions', workListSlug: null, activePerspectives: new Set() });
  app.readUrl();
  return { app, window };
}
const qs = (window) => new URLSearchParams(window.location.search);

test('an unknown tab on a tabbed stage lands on tab 1 and the URL is rewritten', async () => {
  const { app, window } = await load('?resource=egeria_git&stage=curate&tab=nonsense');
  assert.equal(app.state.subTab, 'curate_describe');
  assert.equal(qs(window).get('tab'), 'curate_describe');
});

test('an unknown tab on a plain stage lands on that stage\'s landing tab and the URL drops it', async () => {
  const a = await load('?resource=egeria_git&stage=assessment&tab=nonsense');
  assert.equal(a.app.state.subTab, 'questions');
  assert.equal(qs(a.window).get('tab'), null);
  const b = await load('?resource=egeria_git&stage=enrichment&tab=nonsense');
  assert.equal(b.app.state.subTab, 'context');
});

test('a known tab is kept', async () => {
  const { app, window } = await load('?resource=egeria_git&stage=curate&tab=curate_contents');
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(qs(window).get('tab'), 'curate_contents');
  const b = await load('?resource=egeria_git&stage=assessment&tab=survey');
  assert.equal(b.app.state.subTab, 'survey');
});

test('a tab from another stage or another kind is not kept', async () => {
  const other = await load('?resource=egeria_git&stage=assessment&tab=curate_contents');
  assert.equal(other.app.state.subTab, 'questions');
  const ctx = await load('?resource=egeria_git&stage=assessment&tab=context');
  assert.equal(ctx.app.state.subTab, 'questions');
  const dbOnly = await load('?resource=egeria_git&stage=assessment&tab=schema_inventory');
  assert.equal(dbOnly.app.state.subTab, 'questions');
  const dbOk = await load('?type=db&resource=aw&stage=assessment&tab=schema_inventory', { type: 'db' });
  assert.equal(dbOk.app.state.subTab, 'schema_inventory');
});

test('the retired "dashboard" id is still passed on for loadPane() to alias', async () => {
  const { app } = await load('?resource=egeria_git&stage=assessment&tab=dashboard');
  assert.equal(app.state.subTab, 'dashboard');
});

test('nothing renders data-deferred, and the deferred stub is gone from the source', async () => {
  const { app } = await load('?resource=egeria_git&stage=assessment');
  for (const stage of ['scouting', 'assessment', 'enrichment', 'curate', 'understanding']) {
    app.state.stage = stage; app.state.subTab = app.stageDefaultSubTab();
    assert.ok(!app.subTabsHtml().includes('data-deferred'), stage);
  }
  const dir = new URL('../../resource_explorer/web/static/next/', import.meta.url);
  const walk = (u) => readdirSync(u, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(new URL(`${e.name}/`, u)) : e.name.endsWith('.js') || e.name.endsWith('.html') ? [new URL(e.name, u)] : []);
  for (const f of walk(dir)) {
    const src = readFileSync(f, 'utf8');
    assert.ok(!src.includes('deferredPaneHtml') && !src.includes('data-deferred'), String(f));
  }
});
