/** Behaviour: PI-112 (dismiss with a reason), PI-113 (restore) and PI-114 (a free-text note) in the RFA
 *  drawer. Real rfa.js against a recording fetch in the shape of web/routes/activity.py.
 *  A dismissal is a record: the row is hidden behind "show suppressed (N)", never dropped, and a restore
 *  keeps the record. Nothing here talks to Egeria. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const base = (id, summary, over = {}) => ({
  id, entry_id: id.split('::')[0], annotation_index: 0, ts: '2026-10-01T00:00:00Z', entity_type: 'repo', entity_slug: 'r1',
  entity_name: 'R1', annotation_type: 'RequestForAction', analysis_name: 'SecurityHygieneCheck', summary,
  explanation: '', action_requested: '', action_target_name: '', rfa_status: 'open', assignee: '', defer_until: '',
  resolution_note: '', notes: '', dismissal_key: `k-${summary}`, dismissed: false, dismissal: null, ...over,
});

async function open({ rows, failDismiss = false } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  const data = rows.map((r) => ({ ...r }));
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && u === '/api/activity/rfas') return ok(data);
    if (method === 'POST' && u.endsWith('/dismiss')) {
      if (failDismiss) return { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'store down' }) };
      return ok({ status: 'success', dismissal: { id: 'dm1', reason: body.reason, note: body.note, created_by: 'dan', created_at: new Date().toISOString() } });
    }
    if (method === 'POST' && u.endsWith('/clear')) return ok({ status: 'success', dismissal: { id: 'dm1', cleared_at: 'now' } });
    if (method === 'PATCH' && u.endsWith('/notes')) return ok({ status: 'success', notes: body.notes });
    return ok({});
  };
  ensureLoaderRegistered();
  const api = await import('/static/re-api.js');
  api.clearCache();
  const mod = await import(`/static/next/rfa.js?t=${Math.random()}`);
  mod.openRfaDrawer('');
  await tick();
  const q = (s) => document.querySelector(s);
  return { document, window, calls, mod, q, rowFor: (id) => [...document.querySelectorAll('[data-rfa-row]')].find((n) => n.dataset.rfaRow === id) };
}

const ROWS = [base('e1::0::0', 'No SECURITY.md found'), base('e1::0::1', 'No CI configuration detected')];

test('Dismiss… opens a form that needs a reason before it can be pressed', async () => {
  const t = await open({ rows: ROWS });
  t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss]').click();
  const form = t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss-form]');
  assert.ok(form);
  assert.equal(form.querySelector('[data-rfa-dismiss-go]').disabled, true);
  assert.deepEqual([...form.querySelectorAll('[data-rfa-reason] option')].map((o) => o.value), ['', 'not_applicable', 'wont_do']);
  const sel = form.querySelector('[data-rfa-reason]');
  sel.value = 'wont_do';
  sel.dispatchEvent(new t.window.Event('change'));
  assert.equal(form.querySelector('[data-rfa-dismiss-go]').disabled, false);
});

test('dismissing posts the reason and note for that row, hides it, and counts it under show suppressed', async () => {
  const t = await open({ rows: ROWS });
  t.rowFor('e1::0::1').querySelector('[data-rfa-dismiss]').click();
  const f = t.rowFor('e1::0::1').querySelector('[data-rfa-dismiss-form]');
  f.querySelector('[data-rfa-reason]').value = 'not_applicable';
  f.querySelector('[data-rfa-reason]').dispatchEvent(new t.window.Event('change'));
  f.querySelector('[data-rfa-dismiss-note]').value = 'we do not own CI';
  f.querySelector('[data-rfa-dismiss-go]').click();
  await tick();
  const post = t.calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/activity/rfas/e1%3A%3A0%3A%3A1/dismiss');
  assert.deepEqual(post.body, { reason: 'not_applicable', note: 'we do not own CI' });
  assert.equal(t.rowFor('e1::0::1'), undefined, 'hidden');
  assert.ok(t.rowFor('e1::0::0'), 'its sibling stays');
  assert.match(t.q('#next-rfa-suppressed-label').textContent, /show suppressed \(1\)/);
  assert.match(t.q('#next-rfa-flash').textContent, /dismissed · Not applicable/);
  t.q('#next-rfa-show-suppressed').click();
  const row = t.rowFor('e1::0::1');
  assert.match(text(row.querySelector('[data-rfa-dismissed]')), /dismissed · Not applicable · we do not own CI · dan/);
  assert.equal(row.querySelector('[data-rfa-act]'), null, 'no defer/complete on a dismissed request');
});

test('Restore posts the clear for that dismissal and the request is back, record kept', async () => {
  const dismissed = base('e1::0::0', 'No SECURITY.md found', { dismissed: true, dismissal: { id: 'dm1', reason: 'wont_do', note: '', created_by: 'dan', created_at: '2026-10-02T00:00:00Z' } });
  const t = await open({ rows: [dismissed, ROWS[1]] });
  assert.equal(t.rowFor('e1::0::0'), undefined);
  t.q('#next-rfa-show-suppressed').click();
  t.rowFor('e1::0::0').querySelector('[data-rfa-restore]').click();
  await tick();
  assert.equal(t.calls.filter((c) => c.method === 'POST')[0].url, '/api/activity/rfas/dismissals/dm1/clear');
  t.q('#next-rfa-show-suppressed').click(); // back to hiding suppressed
  assert.ok(t.rowFor('e1::0::0'), 'restored row shows with suppressed hidden');
  assert.equal(t.rowFor('e1::0::0').querySelector('[data-rfa-dismissed]'), null);
  assert.match(t.q('#next-rfa-suppressed-label').textContent, /\(0\)/);
});

test('a failed dismiss says so on the form and the row stays', async () => {
  const t = await open({ rows: ROWS, failDismiss: true });
  t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss]').click();
  const f = t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss-form]');
  f.querySelector('[data-rfa-reason]').value = 'wont_do';
  f.querySelector('[data-rfa-reason]').dispatchEvent(new t.window.Event('change'));
  f.querySelector('[data-rfa-dismiss-go]').click();
  await tick();
  assert.match(text(f.querySelector('[data-rfa-form-status]')), /not recorded: .*store down/);
  assert.ok(t.rowFor('e1::0::0'));
  assert.equal(t.rowFor('e1::0::0').querySelector('[data-rfa-dismissed]'), null);
});

test('a note saves through the notes route and shows on the row, escaped', async () => {
  const t = await open({ rows: ROWS });
  t.rowFor('e1::0::0').querySelector('[data-rfa-note-open]').click();
  const f = t.rowFor('e1::0::0').querySelector('[data-rfa-note-form]');
  f.querySelector('[data-rfa-note-text]').value = 'asked <b>maintainers</b>';
  f.querySelector('[data-rfa-note-save]').click();
  await tick();
  const patch = t.calls.find((c) => c.method === 'PATCH');
  assert.equal(patch.url, '/api/activity/rfas/e1%3A%3A0%3A%3A0/notes');
  assert.deepEqual(patch.body, { notes: 'asked <b>maintainers</b>' });
  const line = t.rowFor('e1::0::0').querySelector('[data-rfa-note]');
  assert.equal(line.textContent, 'note: asked <b>maintainers</b>');
  assert.equal(line.querySelector('b'), null, 'escaped, not parsed');
  assert.match(t.rowFor('e1::0::0').querySelector('[data-rfa-note-open]').textContent, /Edit note/);
});

test('the dismiss flow never touches an Egeria route', async () => {
  const t = await open({ rows: ROWS });
  t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss]').click();
  const f = t.rowFor('e1::0::0').querySelector('[data-rfa-dismiss-form]');
  f.querySelector('[data-rfa-reason]').value = 'wont_do';
  f.querySelector('[data-rfa-reason]').dispatchEvent(new t.window.Event('change'));
  f.querySelector('[data-rfa-dismiss-go]').click();
  await tick();
  assert.ok(t.calls.every((c) => c.url.startsWith('/api/activity/')), JSON.stringify(t.calls.map((c) => c.url)));
});

test('the note form says the note also goes to Egeria only when the request already has a ToDo', async () => {
  const t = await open({ rows: [base('e1::0::0', 'has a todo', { egeria_todo_guid: 'todo-1' }), base('e1::0::1', 'no todo yet')] });
  t.rowFor('e1::0::0').querySelector('[data-rfa-note-open]').click();
  assert.match(text(t.rowFor('e1::0::0').querySelector('[data-rfa-note-egeria]')), /also sent to Egeria/);
  t.rowFor('e1::0::1').querySelector('[data-rfa-note-open]').click();
  assert.equal(t.rowFor('e1::0::1').querySelector('[data-rfa-note-egeria]'), null);
});
