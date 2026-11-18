/** Behaviour: Admin → Feedback triage (docs/trellis-feedback-extraction.md §6 step 3 part C). Page items and rated
 *  answers carry a status control with a note; Save PATCHes the item by id and reloads; a refusal is said on the row;
 *  the status filter asks the server; history loads from the item route. Real module; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const STATUSES = ['new', 'triaged', 'known_issue', 'actioned', 'deferred', 'not_an_issue'];

const LISTING = {
  feedback: [
    { id: 'p1', source: 'page', page: '/scouting', category: 'bug', message: 'broken', rating: null,
      created_at: '2026-11-09T10:00:00+00:00', triage_status: 'triaged',
      triage: { status: 'triaged', note: 'looking', actor: 'erinoverview', updated_at: '2026-11-09T11:00:00+00:00', events: 1 } },
    { id: 'a1', source: 'answer', vote: -1, query_text: 'what changed?', response_text: 'it did', user_id: 'peterprofile',
      created_at: '2026-11-09T09:00:00+00:00', triage_status: 'new',
      triage: { status: 'new', note: '', actor: '', updated_at: '', events: 0 }, extra: { text_from: 'stored exchange' } },
    { id: 'r7', source: 'resource', entity_type: 'repo', entity_slug: 'r1', message: 'solid', rating: 4,
      created_at: '2026-11-08T09:00:00+00:00', triage_status: 'new',
      triage: { status: 'new', note: '', actor: '', updated_at: '', events: 0 } },
  ],
  gone: [{ ref_id: 'r0', source: 'resource_feedback',
    triage: { status: 'deferred', note: 'later', actor: 'erinoverview', updated_at: '2026-11-01T10:00:00+00:00', events: 1 } }],
  counts: { resource: { total: 1, resources: 1 }, page: { total: 1 }, answer: { total: 1 } },
  filtered: false,
  statuses: STATUSES,
};

async function setUp(routes) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    for (const [prefix, answer] of routes) {
      if (u.startsWith(prefix) && (answer.method || 'GET') === (opts.method || 'GET')) return answer.reply(u, opts);
    }
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.sessionStorage = window.sessionStorage;
  const pane = await import('/static/next/admin/feedback.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, pane, calls };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

test('every row gets a status control set to its status, Curate rows through their own route', async () => {
  const { host, pane } = await setUp([['/api/curate/feedback', { reply: () => ok(LISTING) }]]);
  await pane.renderFeedback(host);
  const page = host.querySelector('[data-triage="p1"]');
  assert.ok(page, 'no triage cell for the page item');
  assert.equal(page.querySelector('[data-triage-status]').value, 'triaged');
  assert.match(text(page), /triaged · erinoverview/);
  assert.match(text(page), /“looking”/);
  assert.match(text(page), /history \(1\)/);
  const answer = host.querySelector('[data-triage="a1"]');
  assert.equal(answer.querySelector('[data-triage-status]').value, 'new');
  assert.equal(answer.querySelector('[data-triage-history]'), null, 'no history link for an item never changed');
  assert.equal(host.querySelectorAll('[data-triage]').length, 3);
  assert.equal(host.querySelector('[data-triage="r7"]').dataset.triageSource, 'resource_feedback');
  assert.equal(host.querySelector('[data-triage="p1"]').dataset.triageSource, 'item');
  const options = [...page.querySelectorAll('[data-triage-status] option')].map((o) => o.value);
  assert.deepEqual(options, STATUSES);
});

test('Save sends the status and note to the item by id, then reloads the listing', async () => {
  let listings = 0;
  const { host, pane, calls } = await setUp([
    ['/api/curate/feedback', { reply: () => { listings += 1; return ok(LISTING); } }],
    ['/api/feedback/a1', { method: 'PATCH', reply: () => ok({ id: 'a1', triage_status: 'known_issue' }) }],
  ]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-status]').value = 'known_issue';
  cell.querySelector('[data-triage-note]').value = '  dup of the parser bug  ';
  cell.querySelector('[data-triage-save]').click();
  await settle(); await settle(); await settle();
  const patch = calls.find((c) => c.method === 'PATCH');
  assert.ok(patch, 'no PATCH sent');
  assert.equal(patch.url, '/api/feedback/a1');
  assert.deepEqual(patch.body, { triage_status: 'known_issue', note: 'dup of the parser bug' });
  assert.equal(listings, 2, 'the listing was not reloaded after the save');
});

test('a refused save says why on that row and re-enables the button', async () => {
  const { host, pane } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/p1', { method: 'PATCH', reply: () => ({ ok: false, status: 403, json: async () => ({ detail: 'Admin credential required' }) }) }],
  ]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="p1"]');
  cell.querySelector('[data-triage-status]').value = 'actioned';
  cell.querySelector('[data-triage-save]').click();
  await settle(); await settle(); await settle();
  assert.equal(text(cell.querySelector('[data-triage-error]')), 'Not saved: Admin credential required');
  assert.equal(cell.querySelector('[data-triage-save]').disabled, false);
});

test('the status filter asks the server for that status', async () => {
  const { host, pane, calls } = await setUp([['/api/curate/feedback', { reply: () => ok(LISTING) }]]);
  await pane.renderFeedback(host);
  host.querySelector('[data-filter-key="status"][data-filter-val="known_issue"]').click();
  await settle(); await settle();
  assert.ok(calls.some((c) => c.url.includes('status=known_issue')), 'no request carried the status filter');
});

test('history loads every change from the item route', async () => {
  const { host, pane } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/p1', { reply: () => ok({ id: 'p1', history: [
      { status: 'triaged', note: 'looking', actor: 'erinoverview', created_at: '2026-11-09T11:00:00+00:00' }] }) }],
  ]);
  await pane.renderFeedback(host);
  const box = host.querySelector('[data-triage="p1"] [data-triage-history]');
  box.open = true;
  box.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  await settle(); await settle(); await settle();
  assert.match(text(box.querySelector('[data-history-body]')), /2026-11-09 11:00 · triaged · erinoverview — looking/);
});

test('saving a Curate row goes to its by-reference route', async () => {
  const { host, pane, calls } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/resource_feedback/r7', { method: 'PATCH', reply: () => ok({ id: 'r7', triage_status: 'actioned' }) }],
  ]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="r7"]');
  cell.querySelector('[data-triage-status]').value = 'actioned';
  cell.querySelector('[data-triage-save]').click();
  await settle(); await settle(); await settle();
  const patch = calls.find((c) => c.method === 'PATCH');
  assert.equal(patch.url, '/api/feedback/resource_feedback/r7');
  assert.deepEqual(patch.body, { triage_status: 'actioned', note: '' });
});

test('a deleted Curate row that was triaged is listed as gone, and Clear deletes its history', async () => {
  const { host, pane, calls } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/resource_feedback/r0', { method: 'DELETE', reply: () => ok({ cleared: 1 }) }],
  ]);
  await pane.renderFeedback(host);
  const gone = host.querySelector('[data-gone="r0"]');
  assert.ok(gone, 'no item-gone entry');
  assert.match(text(gone), /resource_feedback r0 last: deferred · erinoverview “later”/);
  gone.querySelector('[data-gone-clear]').click();
  await settle(); await settle(); await settle();
  assert.ok(calls.some((c) => c.method === 'DELETE' && c.url === '/api/feedback/resource_feedback/r0'));
});

test('a refused clear says why on that entry', async () => {
  const { host, pane } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/resource_feedback/r0', { method: 'DELETE', reply: () => ({ ok: false, status: 409, json: async () => ({ detail: 'The row still exists' }) }) }],
  ]);
  await pane.renderFeedback(host);
  const gone = host.querySelector('[data-gone="r0"]');
  gone.querySelector('[data-gone-clear]').click();
  await settle(); await settle(); await settle();
  assert.equal(text(gone.querySelector('[data-gone-error]')), 'Not cleared: The row still exists');
  assert.equal(gone.querySelector('[data-gone-clear]').disabled, false);
});

test('a status read that came back full says older resource items may be missing', async () => {
  const limited = { ...LISTING, filtered: true, resource_limited: true, resource_read: 2000 };
  const { host, pane } = await setUp([['/api/curate/feedback', { reply: () => ok(limited) }]]);
  await pane.renderFeedback(host);
  const line = host.querySelector('[data-resource-limited]');
  assert.ok(line, 'no limited line');
  assert.match(text(line), /only the newest 2000 were checked for this status, so older ones with it may be missing/);
});

test('no limited line when the read was not cut short', async () => {
  const { host, pane } = await setUp([['/api/curate/feedback', { reply: () => ok({ ...LISTING, resource_limited: false }) }]]);
  await pane.renderFeedback(host);
  assert.equal(host.querySelector('[data-resource-limited]'), null);
});
