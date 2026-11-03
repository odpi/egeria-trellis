/** Routing-level test: a 400 "not built yet" from the members route renders as
 *  that sentence, not as "could not be read".
 *
 *  WHY THIS EXISTS: /members, /children, /promote and /trend are repo-built.
 *  For a database the server now answers 400 with an honest sentence naming the
 *  kind. The members rail wrapped every failure as "The members of X could not
 *  be read: ..." -- a fault claim for what is a capability answer. This opens the
 *  rail the way the numbers click does (openMembers) and reads the rail DOM.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SENTENCE = "Members aren't built for databases yet; today they list repository findings (advisories, dependencies, symbols, components).";

async function openWith(status, detail) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return { ok: false, status, statusText: 'x', json: async () => ({ detail }) };
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  await app.openMembers({ slug: 'adventureworks', analysisId: 'schema_inventory', metric: 'table_count', title: 'Schema inventory' });
  await new Promise((r) => setTimeout(r, 30));
  return { text: document.getElementById('rail-evidence').textContent, document, calls };
}

test('a 400 from /members renders the server sentence, not "could not be read"', async () => {
  const { text, document, calls } = await openWith(400, SENTENCE);
  assert.ok(calls.some((u) => u.includes('/members/')), 'the members route was requested');
  assert.ok(text.includes(SENTENCE), text);
  assert.ok(!text.includes('could not be read'), text);
  assert.ok(document.querySelector('[data-members-not-built]'));
});

test('a real failure (500) still reads as a fault', async () => {
  const { text } = await openWith(500, 'boom');
  assert.ok(text.includes('could not be read'), text);
});
