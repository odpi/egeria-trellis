/** Behaviour: "Runs on this resource" asks for this resource's runs only -- slug AND entity type.
 *  The activity route used to drop ?entity_slug=; a database and a repo can share a slug, so the type goes too.
 *  Real app.js and re-api.js; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

test('getResourceRuns sends entity_type when given, and omits it when not', async () => {
  const { window } = makeDomEnvironment();
  const urls = [];
  globalThis.fetch = async (url) => { urls.push(String(url)); return { ok: true, status: 200, json: async () => [] }; };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  const api = await import('/static/re-api.js');
  api.clearCache();
  await api.getResourceRuns('a', 40, 'database');
  api.clearCache();
  await api.getResourceRuns('a');
  assert.equal(urls[0], '/api/activity/?entity_slug=a&entity_type=database&limit=40');
  assert.equal(urls[1], '/api/activity/?entity_slug=a&limit=40');
});

test('openRunsList sends the current resource type', async () => {
  const { document, window } = makeDomEnvironment();
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return { ok: true, status: 200, json: async () => (String(url).includes('/groups') || String(url).startsWith('/api/activity/') ? [] : {}) };
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  await app.openRunsList('coco');
  const call = urls.find((u) => u.startsWith('/api/activity/?entity_slug=coco'));
  assert.ok(call, `no activity call in ${urls}`);
  assert.match(call, /entity_type=\w+/);
  assert.ok(document);
});
