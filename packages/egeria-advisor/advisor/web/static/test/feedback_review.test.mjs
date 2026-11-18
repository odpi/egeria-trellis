/** A-F1: Admin → Feedback Review over the trellis-feedback router.
 *
 *  Runs the REAL feedback_review.js in a jsdom window. Only fetch is stubbed, with rows shaped as
 *  trellis_feedback.FeedbackStore.list() returns them (item fields + `triage`).
 *
 *  Run:  node --test advisor/web/static/test/feedback_review.test.mjs
 *  jsdom is found as in specs_publish.test.mjs; without it the tests are skipped with that reason,
 *  unless EA_RENDER_HARNESS_REQUIRED=1.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const opts = { skip: !REQUIRED && (jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)') };
const HOSTILE = '<img src=x onerror=alert(1)>';
const tick = () => new Promise(r => setTimeout(r, 0));

const NEW = { status: 'new', note: '', actor: '', updated_at: '', events: 0, cause: '', secondary_cause: '' };
function item(id, query, vote, triage = NEW) {
  return { app: 'egeria-advisor', kind: 'answer', id, query_text: query, vote, response_text: `answer to ${query}`,
    user_id: 'peterprofile', perspective: 'Data Steward', intent_label: 'explain', routing_agent: 'doc_agent',
    created_at: '2026-11-11T10:00:00+00:00', extra: { query_type: 'explanation' }, triage };
}

function setup(listReply, posts = []) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><div id="fb"></div></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : null;
    posts.push({ url, body, headers: init.headers });
    if (url.startsWith('/api/feedback/v2/items')) return { ok: listReply.ok, status: listReply.status, json: async () => listReply.data };
    if (url === '/api/feedback/v2/triage') {
      return { ok: true, status: 200, json: async () => ({ event: { ref_id: body.ref_id },
        triage: { status: body.status, note: body.note, actor: 'garygeeke', updated_at: '2026-11-11T11:00:00+00:00', events: 1 } }) };
    }
    return { ok: false, status: 404, json: async () => ({ detail: 'no route' }) };
  };
  w.eval(fs.readFileSync(path.join(STATIC, 'feedback_review.js'), 'utf8'));
  return w;
}

const ITEMS = [item('a1', 'first question', 1), item('b2', 'second question', -1), item('c3', 'third question', 0)];
const OK = { ok: true, status: 200, data: { items: ITEMS, total: 3, matched: 3, filtered: false } };

test('each row posts triage for ITS item id, and shows that item', opts, async () => {
  const posts = [];
  const w = setup(OK, posts);
  const el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, { headers: () => ({ Authorization: 'Bearer t' }) });
  const rows = [...el.querySelectorAll('tr[data-item-id]')];
  assert.deepEqual(rows.map(r => r.dataset.itemId), ['a1', 'b2', 'c3']);
  for (const r of rows) {
    const shown = r.querySelector('[data-query]').textContent;
    assert.equal(shown, ITEMS.find(i => i.id === r.dataset.itemId).query_text);
  }
  const second = rows[1];
  second.querySelector('select').value = 'known_issue';
  second.querySelector('textarea').value = 'wrong collection';
  second.querySelector('button').click();
  await tick(); await tick();
  const sent = posts.find(p => p.url === '/api/feedback/v2/triage');
  assert.deepEqual(sent.body, { ref_id: 'b2', status: 'known_issue', note: 'wrong collection' });
  assert.equal(sent.headers.Authorization, 'Bearer t');
  assert.match(second.querySelector('[data-triage-state]').textContent, /latest known_issue by garygeeke.*wrong collection/);
  assert.equal(rows[0].querySelector('[data-triage-state]').textContent, 'No triage yet.');
});

test('the vote filter hides rows without changing any row\'s target', opts, async () => {
  const posts = [];
  const w = setup(OK, posts);
  const el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, { vote: '-1' });
  const rows = [...el.querySelectorAll('tr[data-item-id]')];
  assert.deepEqual(rows.map(r => r.dataset.itemId), ['b2']);
  rows[0].querySelector('button').click();
  await tick(); await tick();
  assert.equal(posts.find(p => p.url === '/api/feedback/v2/triage').body.ref_id, 'b2');
  assert.match(el.textContent, /1 of 3 votes/);
});

test('the status filter is sent to the server', opts, async () => {
  const posts = [];
  const w = setup(OK, posts);
  await w.FeedbackReview.load(w.document.getElementById('fb'), { status: 'triaged' });
  assert.match(posts[0].url, /status=triaged/);
  assert.match(posts[0].url, /kind=answer/);
});

test('empty and failed states say why', opts, async () => {
  let w = setup({ ok: true, status: 200, data: { items: [], total: 0, matched: 0 } });
  let el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  assert.equal(el.textContent, 'No votes have been recorded yet.');

  w = setup({ ok: true, status: 200, data: { items: [], total: 7, matched: 0 } });
  el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, { status: 'deferred' });
  assert.equal(el.textContent, 'No votes match this filter (7 in all).');

  w = setup({ ok: false, status: 403, data: { detail: 'Admin only' } });
  el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  assert.match(el.textContent, /Feedback not read: Admin only: sign in as an admin/);

  w = setup({ ok: false, status: 503, data: { detail: 'feedback store not opened (postgresql://u:***@h/db): OperationalError: refused' } });
  el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  assert.match(el.textContent, /Feedback not read: feedback store not opened .*refused/);
});

test('hostile question and answer text are text, never markup', opts, async () => {
  const w = setup({ ok: true, status: 200, data: { items: [{ ...item('h1', HOSTILE, -1), response_text: HOSTILE }], total: 1, matched: 1 } });
  const el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  assert.equal(el.querySelectorAll('img').length, 0);
  assert.equal(el.querySelector('[data-query]').textContent, HOSTILE);
});

test('N1: an earlier vote on the same answer by the same user is marked superseded, the latest is not', opts, async () => {
  const at = (id, vote, t, ex = 'x1', user = 'peterprofile') => ({ ...item(id, 'same answer', vote), user_id: user,
    session_id: 's1', created_at: t, extra: { exchange_id: ex, intent_selected: 'auto' }, intent_label: 'explanation' });
  const items = [at('n2', -1, '2026-11-11T10:00:02+00:00'), at('n1', 1, '2026-11-11T10:00:01+00:00'),
    at('o1', 1, '2026-11-11T10:00:01+00:00', 'x2'), at('u1', 1, '2026-11-11T10:00:00+00:00', 'x1', 'garygeeke')];
  const w = setup({ ok: true, status: 200, data: { items, total: 4, matched: 4 } });
  const el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  const marked = [...el.querySelectorAll('tr[data-item-id]')].filter(r => r.querySelector('[data-superseded]'))
    .map(r => r.dataset.itemId);
  assert.deepEqual(marked, ['n1']);
});

test('N2: the row says which intent was selected and which was routed, for old and new items', opts, async () => {
  const fresh = { ...item('a', 'q', 1), intent_label: 'explanation', extra: { intent_selected: 'auto' } };
  const old = { ...item('b', 'q', 1), intent_label: 'explain', extra: { query_type: 'code_search' } };
  const w = setup({ ok: true, status: 200, data: { items: [fresh, old], total: 2, matched: 2 } });
  const el = w.document.getElementById('fb');
  await w.FeedbackReview.load(el, {});
  const [a, b] = [...el.querySelectorAll('tr[data-item-id]')].map(r => r.textContent);
  assert.match(a, /selected auto → routed explanation/);
  assert.match(b, /selected explain → routed code_search/);
});
