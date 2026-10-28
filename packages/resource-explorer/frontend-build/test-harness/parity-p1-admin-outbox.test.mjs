/** Parity P1, PI-131: Admin → Publish Queue. The retry is drawn only where it is safe, and the server's refusal
 *  of a destructive write is shown when it is asked anyway. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = () => new Promise((r) => setTimeout(r, 15));

const R = (id, status, extra = {}) => ({ id, entity_type: 'repo', entity_slug: 'egeria_git', element_kind: 'annotation',
  qualified_name: `Q::${id}`, status, attempts: 2, last_error: '', next_attempt_at: '', completed_at: '',
  created_at: '2026-10-08T01:00:00', destructive: false, ...extra });

async function setUp({ rows, counts, retryStatus = 200, admin = true, configured = true, open = undefined } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  const data = rows || [
    R(1, 'dead', { last_error: 'OPEN-METADATA-SECURITY-403-007 not authorized' }),
    R(2, 'dead', { element_kind: 'doc_source_unpublish', destructive: true, last_error: 'not retried: destructive write · x' }),
    R(3, 'failed', { next_attempt_at: '2026-10-08T02:00:00' }),
    R(4, 'done', { completed_at: '2026-10-08T01:30:00' }),
    R(5, 'pending'),
  ];
  globalThis.fetch = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const u = String(url);
    calls.push({ url: u, method });
    if (u === '/api/egeria/admin-status') return { ok: true, status: 200, json: async () => ({ admin, configured, open }) };
    const m = u.match(/^\/api\/outbox\/(\d+)\/retry$/);
    if (m) {
      if (retryStatus !== 200) return { ok: false, status: retryStatus, statusText: 'Conflict', json: async () => ({ detail: 'a destructive write: never retried from here' }) };
      return { ok: true, status: 200, json: async () => ({ retried: true, id: Number(m[1]) }) };
    }
    if (u.startsWith('/api/outbox/')) {
      const status = new URL(u, 'https://x.invalid').searchParams.get('status');
      const shown = status ? data.filter((r) => r.status === status) : data;
      return { ok: true, status: 200, json: async () => ({ counts: counts || { dead: 2, failed: 1, done: 1, pending: 1 }, rows: shown, max_attempts: 8 }) };
    }
    throw new Error(`_UNSTUBBED_FETCH ${method} ${u}`);
  };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mod = await import(`/static/next/admin/outbox.js?t=${Math.random()}`);
  await mod.renderOutbox(host);
  return { host, calls, mod, window };
}

test('each row shows its state as a cue and a word, with attempts out of the real ceiling', async () => {
  const { host } = await setUp();
  const dead = host.querySelector('[data-outbox-row="1"]');
  assert.match(dead.textContent, /✗ dead/);
  assert.match(dead.textContent, /2\/8/);
  assert.match(host.querySelector('[data-outbox-row="3"]').textContent, /↻ retrying/);
  assert.match(host.querySelector('[data-outbox-row="4"]').textContent, /✓ done/);
});

test('Retry is drawn on a dead, non-destructive row and nowhere else', async () => {
  const { host } = await setUp();
  const withRetry = [...host.querySelectorAll('[data-outbox-row]')].filter((r) => r.querySelector('[data-outbox-retry]')).map((r) => r.dataset.outboxRow);
  assert.deepEqual(withRetry, ['1']);
});

test('a dead DESTRUCTIVE row has no Retry and says it is not retried, and why', async () => {
  const { host } = await setUp();
  const row = host.querySelector('[data-outbox-row="2"]');
  assert.equal(row.querySelector('[data-outbox-retry]'), null);
  const note = row.querySelector('[data-outbox-no-retry]');
  assert.match(note.textContent, /not retried · destructive/);
  assert.match(note.getAttribute('title'), /original control/);
});

test('canRetry: only dead and not destructive', async () => {
  const { mod } = await setUp();
  assert.equal(mod.canRetry(R(1, 'dead')), true);
  assert.equal(mod.canRetry(R(1, 'dead', { destructive: true })), false);
  for (const s of ['pending', 'running', 'failed', 'done', 'superseded']) assert.equal(mod.canRetry(R(1, s)), false, s);
  // A row from an older server that carries no flag is treated as retryable only when dead; the server still refuses.
  assert.equal(mod.canRetry({ id: 9, status: 'dead' }), true);
});

test('pressing Retry posts to that row only, shows an immediate state, and reloads the queue', async () => {
  const { host, calls } = await setUp();
  const btn = host.querySelector('[data-outbox-retry="1"]');
  btn.click();
  assert.equal(btn.disabled, true);
  assert.equal(btn.textContent, 'Retrying…');
  await tick();
  const posts = calls.filter((c) => c.method === 'POST');
  assert.deepEqual(posts, [{ url: '/api/outbox/1/retry', method: 'POST' }]);
  assert.ok(calls.filter((c) => c.url.startsWith('/api/outbox/?')).length >= 2, 'the queue was read again');
});

test('a retry the server refuses (409) leaves the button re-armed and shows the server sentence', async () => {
  const { host } = await setUp({ retryStatus: 409 });
  const btn = host.querySelector('[data-outbox-retry="1"]');
  btn.click();
  await tick();
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Retry');
  assert.match(host.querySelector('[data-outbox-retry-state="1"]').textContent, /never retried from here/);
});

test('counts come from the whole table and the filter chips read the server', async () => {
  const { host, calls } = await setUp();
  const dead = host.querySelector('[data-outbox-filter="dead"]');
  assert.match(dead.textContent, /dead\s*2/);
  dead.click();
  await tick();
  assert.ok(calls.some((c) => c.url.includes('status=dead')));
  assert.equal(host.querySelectorAll('[data-outbox-row]').length, 2);
  assert.match(host.querySelector('[data-outbox-filter="dead"]').getAttribute('aria-pressed'), /true/);
  assert.match(host.querySelector('[data-outbox-filter="done"]').textContent, /done\s*1/, 'other counts stay whole-table');
});

test('an empty queue says nothing was queued; an empty filter says rows exist in other states', async () => {
  const a = await setUp({ rows: [], counts: {} });
  assert.match(a.host.querySelector('[data-outbox-empty]').textContent, /nothing has been queued/);
  const b = await setUp({ rows: [R(4, 'done')], counts: { done: 1 } });
  b.host.querySelector('[data-outbox-filter="dead"]').click();
  await tick();
  assert.match(b.host.querySelector('[data-outbox-empty]').textContent, /exist in other states/);
});

test('Egeria error text and element names are escaped', async () => {
  const { host } = await setUp({ rows: [R(1, 'dead', { last_error: '<img src=x onerror=alert(1)>', qualified_name: '"><b id="pwn">' })] });
  assert.equal(host.querySelector('img'), null);
  assert.equal(host.querySelector('#pwn'), null);
  assert.match(host.querySelector('[data-outbox-error]').textContent, /<img src=x/);
});

test('a non-admin sees Retry disabled with "admin only" and pressing it sends nothing', async () => {
  const { host, calls } = await setUp({ admin: false });
  const btn = host.querySelector('[data-outbox-retry="1"]');
  assert.equal(btn.disabled, true);
  assert.ok(btn.hasAttribute('data-admin-only'));
  assert.match(btn.parentElement.textContent, /admin only/);
  btn.click();
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
});

test('an admin Retry sends the admin token header', async () => {
  const { host, window } = await setUp();
  window.sessionStorage.setItem('re_admin_token', 'tok-9');
  const seen = [];
  const inner = globalThis.fetch;
  globalThis.fetch = async (u, o = {}) => { if (o.method === 'POST') seen.push(o.headers); return inner(u, o); };
  host.querySelector('[data-outbox-retry="1"]').click();
  await tick();
  assert.equal(seen[0]['X-Admin-Token'], 'tok-9');
});

test('no admin configured (owner ruling 2026-10-14): Retry reads admin only, since the server refuses it', async () => {
  const { host } = await setUp({ admin: false, configured: false, open: false });
  const btn = host.querySelector('[data-outbox-retry="1"]');
  assert.ok(!btn || btn.disabled, 'no live Retry');
  assert.match(host.textContent, /admin only/);
});

test('no admin configured, local-dev flag on: Retry is enabled for a signed-in non-admin (nothing says admin only)', async () => {
  const { host } = await setUp({ admin: false, configured: false, open: true });
  const btn = host.querySelector('[data-outbox-retry="1"]');
  assert.equal(btn.disabled, false);
  assert.doesNotMatch(btn.parentElement.textContent, /admin only/);
});

test('a dead membership whose confidence RE refused has no Retry and says to fix the value', async () => {
  const why = 'the stored confidence is not a number from 0 to 100, so a retry would refuse it again: correct it on the work list and publish again';
  const { host, mod } = await setUp({ rows: [
    R(7, 'dead', { element_kind: 'collection_membership', last_error: "not sent: confidence 'high' is not a number", not_retryable: why }),
    R(8, 'dead', { element_kind: 'collection_membership', last_error: 'Egeria accepted the membership but did not keep: confidence (sent 80, read back 0)', not_retryable: '' }),
  ] });
  const refused = host.querySelector('[data-outbox-row="7"]');
  assert.equal(refused.querySelector('[data-outbox-retry]'), null);
  const note = refused.querySelector('[data-outbox-no-retry]');
  assert.match(note.textContent, /not retried · fix the value/);
  assert.equal(note.getAttribute('title'), why);
  assert.ok(host.querySelector('[data-outbox-row="8"] [data-outbox-retry]'), 'a "did not keep" row keeps its Retry');
  assert.equal(mod.canRetry(R(7, 'dead', { not_retryable: why })), false);
});
