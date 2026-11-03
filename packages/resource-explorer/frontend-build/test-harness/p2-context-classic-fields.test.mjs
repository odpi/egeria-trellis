/** Behaviour: PI-098, the Context tab carries Classic's remaining fields (steward, location, backup
 *  status, notes). Real app.js, context.js, enrichment.js; only fetch is stubbed.
 *  Each row saves alone through the one enrichment PATCH; the feeds line says "kept in Resource Explorer"
 *  rather than claiming Curate reads it; the owner row is unchanged. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp({ enrichment = {} } = {}) {
  makeDomEnvironment();
  await loadAppModule();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'PATCH' && u.endsWith('/field')) {
      const b = JSON.parse(options.body);
      return ok({ key: b.key, field: { value: b.value, kind: b.kind, author: 'dan', set_at: new Date().toISOString(), evidence: {}, note: b.note || '' } });
    }
    if (u.includes('/enrichment-analyses')) return ok({ analyses: [] });
    if (u.includes('/api/context/')) return ok({ enrichment, question_answers: {} });
    if (u.includes('/api/analyses')) return ok({ subjects: {} });
    return ok({});
  };
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'r1', investigation: '', investigations: [], questions: [], me: { user_id: 'dan' } });
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';
  await renderContext('r1');
  return { app, calls, form: document.getElementById('context-form'), renderContext };
}

test('the Context tab has steward and notes under "What we judge" and location and backup status under "What we record"', async () => {
  const t = await setUp();
  const j = t.form.querySelector('#context-judgements');
  const o = t.form.querySelector('#context-observations');
  for (const k of ['steward', 'notes']) assert.ok(j.querySelector(`[data-field="${k}"]`), k);
  for (const k of ['location', 'backup_status']) assert.ok(o.querySelector(`[data-field="${k}"]`), k);
  assert.equal(j.querySelector('[data-field="notes"]').tagName, 'TEXTAREA');
  assert.deepEqual([...o.querySelector('[data-field="backup_status"]').options].map((x) => x.value), ['', 'yes', 'no', 'partial', 'unknown']);
  assert.ok(j.querySelector('[data-field="owner"]'), 'the owner row is still there, once');
  assert.equal(t.form.querySelectorAll('[data-field="owner"]').length, 1);
});

test('the new rows say they are kept in Resource Explorer, not that Curate reads them', async () => {
  const t = await setUp();
  const row = (k) => t.form.querySelector(`[data-field="${k}"]`).closest('.grid').nextElementSibling;
  for (const k of ['steward', 'notes', 'location', 'backup_status']) {
    assert.match(text(row(k)), /kept in Resource Explorer · no publish step reads it yet/, k);
  }
  assert.match(text(row('sensitivity')), /feeds → Curate \(publish record\)/);
});

test('saving a new row sends one PATCH with the key, value and kind, and the row re-reads signed', async () => {
  const t = await setUp();
  const ctl = t.form.querySelector('[data-field="steward"]');
  ctl.value = 'alice@example.com';
  t.form.querySelector('[data-save="steward"]').click();
  await tick();
  const patches = t.calls.filter((c) => c.method === 'PATCH');
  assert.equal(patches.length, 1);
  assert.equal(patches[0].url, '/api/context/repo/r1/field');
  assert.equal(patches[0].body.key, 'steward');
  assert.equal(patches[0].body.value, 'alice@example.com');
  assert.equal(patches[0].body.kind, 'judgement');
  const o = await (async () => { t.form.querySelector('[data-field="backup_status"]').value = 'partial'; t.form.querySelector('[data-save="backup_status"]').click(); await tick(); return t.calls.filter((c) => c.method === 'PATCH').pop(); })();
  assert.equal(o.body.kind, 'observation');
  assert.equal(o.body.value, 'partial');
});

test('a stored value shows in the control with its signature', async () => {
  const t = await setUp({ enrichment: { location: { value: 'eu-west-1', kind: 'observation', author: 'dan', set_at: new Date().toISOString(), source: 'user', evidence: {} } } });
  assert.equal(t.form.querySelector('[data-field="location"]').value, 'eu-west-1');
  assert.match(text(t.form.querySelector('[data-field="location"]').closest('.grid')), /dan/);
});
