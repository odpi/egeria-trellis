/** A question whose analysis ran and could not settle an answer (a CVE scan that queried none of its dependencies)
 *  is neither ticked as answered nor called "not run". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  delete globalThis.localStorage;
  const { window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  return import('/static/next/app.js');
}
const entry = { kind: 'analysis', question: 'Are there outstanding CVEs?' };
const env = (state, known) => ({ answerable: known, facts: [{ analysis_id: 'cve_scan', state, is_known: known, last_run_at: '2026-10-28T10:00:00' }] });

test('a not-established fact that ran reads not_established, not answered and not unrun', async () => {
  const app = await load();
  assert.equal(app.rowState(entry, env('not_established', false)), 'not_established');
});

test('a measured fact is still answered and a never-run one still unrun', async () => {
  const app = await load();
  assert.equal(app.rowState(entry, env('measured', true)), 'answered');
  assert.equal(app.rowState(entry, { answerable: false, facts: [{ analysis_id: 'cve_scan', state: 'never_run', is_known: false }] }), 'unrun');
});
