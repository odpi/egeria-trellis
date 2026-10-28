/** Rehearsal walk 2026-10-13, wording fixes. Old words must not come back.
 *  1 run-anyway dialog · 2 Activity dialog rule numbers · 4 dismissed RFA corner · 5 views-only schema row. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';

ensureLoaderRegistered();
const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT = path.resolve(HERE, '../../resource_explorer/web/static/next');
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (b) => ({ ok: true, status: 200, json: async () => b });

test('item 1: the run-anyway dialog shows the short server words and never an API parameter', async () => {
  const { document } = makeDomEnvironment();
  const mod = await import('/static/next/run-anyway.js');
  mod.offerRunAnyway({ status: 'skipped', detail: "'secret_scan' has data from 3 minutes ago. A re-run costs about 40s." },
    { slug: 's', analysisId: 'secret_scan', onForce: async () => {} });
  const t = text(document.querySelector('[data-run-anyway-detail]'));
  assert.match(t, /has data from 3 minutes ago/);
  assert.doesNotMatch(t, /force=true|not running it again/);
  assert.ok(document.querySelector('[data-run-anyway]'), 'the Run anyway button is still offered');
});

test('item 2: no design-rule number ("rule 16") reaches the Activity dialog, empty or full', async () => {
  const src = fs.readFileSync(path.join(NEXT, 'stages/activity.js'), 'utf8');
  // user-facing template text only: comments may cite a rule, rendered strings may not
  const rendered = src.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.doesNotMatch(rendered, /\(rule \d+\)/);
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => ok([]);
  const mod = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await mod.openActivityPanel();
  await tick();
  assert.doesNotMatch(document.body.textContent, /rule \d+/i);
});

test('item 4: a dismissed RFA shows "dismissed" and its reason in the corner, not "Open"; a restored one shows Open', async () => {
  const { document } = makeDomEnvironment();
  const row = (id, over) => ({
    id, entry_id: id, annotation_index: 0, ts: '2026-10-01T00:00:00Z', entity_type: 'repo', entity_slug: 'r1', entity_name: 'R1',
    annotation_type: 'RequestForAction', analysis_name: 'X', summary: id, explanation: '', action_requested: '', action_target_name: '',
    rfa_status: 'open', assignee: '', defer_until: '', resolution_note: '', notes: '', dismissal_key: id, dismissed: false, dismissal: null, ...over });
  globalThis.fetch = async () => ok([
    row('d1', { dismissed: true, dismissal: { id: 'dm', reason: 'not_applicable', note: '', created_by: 'dan', created_at: '2026-10-01T00:00:00Z' } }),
    row('o1', {}),
  ]);
  const api = await import('/static/re-api.js'); api.clearCache();
  const mod = await import(`/static/next/rfa.js?t=${Math.random()}`);
  mod.openRfaDrawer('');
  await tick();
  document.querySelector('#next-rfa-show-suppressed').click();
  const corner = (id) => text([...document.querySelectorAll('[data-rfa-row]')].find((n) => n.dataset.rfaRow === id).querySelector('[data-rfa-corner]'));
  assert.match(corner('d1'), /dismissed · Not applicable/);
  assert.doesNotMatch(corner('d1'), /Open/);
  assert.match(corner('o1'), /^Open$/);
});

test('item 5: a views-only schema row says views, and "no storage" instead of a missing size', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const html = app.schemaTreeHtml([{ schema: 'pr', classification: 'views_only', table_count: 25, tables: [] }]);
  assert.match(html, /25 view\(s\) · views only · no storage/);
  assert.doesNotMatch(html, /size not read|table\(s\)/);
});
