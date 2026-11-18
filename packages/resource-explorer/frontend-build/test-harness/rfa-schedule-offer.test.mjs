/** Behaviour: Automate's schedule proposal in the RFA drawer (closed-loop step 2c). A row carrying a schedule offer
 *  has a "Schedule <analysis> <cadence>" button that posts the same body the per-card "⏱ Schedule" control posts;
 *  a failure is said, and a row with no offer (or a completed one) has no button. Real rfa.js, fetch stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const OFFER = { kind: 'schedule', entity_type: 'repo', entity_slug: 'r1', analysis_id: 'licenses', cadence: 'weekly' };
const base = (id, over = {}) => ({
  id, entry_id: id.split('::')[0], annotation_index: 0, ts: '2026-11-10T00:00:00Z', entity_type: 'repo', entity_slug: 'r1',
  entity_name: 'r1', annotation_type: 'RequestForActionAnnotation', analysis_name: 'Automate: schedule proposal',
  summary: 'Schedule licenses weekly on r1: an answer drew on its out-of-date result', explanation: '',
  action_requested: '', action_target_name: '', rfa_status: 'open', assignee: '', defer_until: '', resolution_note: '',
  notes: '', dismissal_key: `k-${id}`, dismissed: false, dismissal: null, offer: OFFER, ...over,
});

async function open({ rows, failSchedule = false, unchanged = false }) {
  const { document } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && u === '/api/activity/rfas') return ok(rows);
    if (method === 'POST' && u.startsWith('/api/schedules/')) {
      if (failSchedule) return { ok: false, status: 403, statusText: 'Forbidden', json: async () => ({ detail: 'you may not publish r1' }) };
      if (unchanged) return ok({ status: 'unchanged', schedule: 'daily', reason: 'already scheduled daily, which is at least as often as weekly' });
      return ok({ status: 'ok' });
    }
    return ok({});
  };
  ensureLoaderRegistered();
  const api = await import('/static/re-api.js');
  api.clearCache();
  const mod = await import(`/static/next/rfa.js?t=${Math.random()}`);
  mod.openRfaDrawer('');
  await tick();
  const rowFor = (id) => [...document.querySelectorAll('[data-rfa-row]')].find((n) => n.dataset.rfaRow === id);
  return { document, calls, rowFor };
}

test('a schedule offer is a button that posts what the per-card Schedule control posts, only ever faster', async () => {
  const t = await open({ rows: [base('e1::0')] });
  const button = t.rowFor('e1::0').querySelector('[data-rfa-schedule]');
  assert.equal(text(button), 'Schedule licenses weekly');
  button.click();
  await tick();
  const post = t.calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/schedules/repo/r1');
  assert.deepEqual(post.body,
    { analysis_id: 'licenses', schedule: 'weekly', enabled: true, target_kind: 'analysis', only_if_faster: true });
  assert.match(text(t.document.querySelector('#next-rfa-flash')), /Scheduled licenses weekly on r1/);
});

test('a refused schedule is said, and the button can be pressed again', async () => {
  const t = await open({ rows: [base('e1::0')], failSchedule: true });
  const button = t.rowFor('e1::0').querySelector('[data-rfa-schedule]');
  button.click();
  await tick();
  assert.match(text(t.document.querySelector('#next-rfa-flash')), /Not scheduled/);
  assert.equal(button.disabled, false);
});

test('no offer, or a completed RFA, has no schedule button', async () => {
  const t = await open({ rows: [base('e1::0', { offer: null }), base('e2::0', { rfa_status: 'completed' })] });
  assert.equal(t.rowFor('e1::0').querySelector('[data-rfa-schedule]'), null);
  // A completed RFA is hidden until "show closed"; shown or not, it offers no action.
  const done = t.rowFor('e2::0');
  assert.equal(done ? done.querySelector('[data-rfa-schedule]') : null, null);
});

test('a schedule already as fast is left alone, and the drawer says so', async () => {
  const t = await open({ rows: [base('e1::0')], unchanged: true });
  t.rowFor('e1::0').querySelector('[data-rfa-schedule]').click();
  await tick();
  assert.match(text(t.document.querySelector('#next-rfa-flash')),
    /Not changed: licenses on r1 is already scheduled daily/);
});
