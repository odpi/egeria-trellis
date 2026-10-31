/** D1-b (design §13): the plan's database row carries ONE press, "Link the pack's N schemas". It is off, with the
 *  reason, when nobody is signed in or there is nothing to add; a confirm names the count and the result before
 *  anything is sent; while it runs the button says "linking…" (aria-busy); after it the page reads the scope again,
 *  so the row acknowledges and validates: "26 members · 22 added · read back <time>". A partial press lists the
 *  schemas not linked, muted, and is never worded as a failure of the whole press. Real app.js and router, a fake
 *  fetch. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, flat, wait } from './scope-test-kit.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

const PACK = 'Coco PostgreSQL Server 1';
const toAdd = (n) => Array.from({ length: n }, (_, i) => ({ guid: `pack-${i}`, schema: `s${i}`, family: PACK }));
const dbRow = (n, now = 4) => ({
  re_guid: 'c990f349-36de', re_qualified_name: 'PostgreSQL Relational Database::host.docker.internal:5442::coco_pharma',
  link_type: 'DataSetContent', schema_end: 1, members_now: now, members_after: n ? now + n : now, to_add: toAdd(n),
  words: n ? `add DataSetContent from c990f349 to the pack's ${n} schemas under '${PACK}' · after the press: ${now + n} members`
    : `every other-family schema is already a DataSetContent member of c990f349 · nothing to add`,
});
const endpoint = (row, press = null) => ({
  at: '2026-10-31T09:00:00', error: '', endpoint: 'host.docker.internal:5442/coco_pharma', families: [],
  plan: { pairs: 0, rows: [], words: 'this endpoint: …', database_link_type: 'DataSetContent', database_row: row },
  neither: null, press, words: 'this endpoint: …',
});
const view = (ep) => {
  const v = baseView();
  v.commit = { ...v.commit, endpoint: ep };
  return v;
};
const btn = (d) => d.querySelector('[data-scope-reanchor-btn]');

test('the database row offers the press, naming the count', async () => {
  const { document } = await setUp(view(endpoint(dbRow(22))));
  const b = btn(document);
  assert.ok(b, 'the press is on the database row');
  assert.equal(flat(b), "Link the pack's 22 schemas");
  assert.equal(b.disabled, false);
  assert.ok(document.querySelector('[data-scope-plan-database]').contains(b));
});

test('signed out, the press is off and says why; nothing is sent', async () => {
  const { document, server } = await setUp(view(endpoint(dbRow(22))), { signedIn: false });
  const b = btn(document);
  assert.ok(b.disabled);
  assert.match(b.getAttribute('title'), /sign in/);
  b.click();
  await wait(50);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/reanchor')).length, 0);
});

test('off, with the reason, when the type or ends were not read', async () => {
  const { document } = await setUp(view(endpoint({ ...dbRow(22), members_after: null, link_type: 'not read', words: 'relationship type not read · nothing proposed' })));
  const b = btn(document);
  assert.ok(b && b.disabled);
  assert.match(b.getAttribute('title'), /not read/);
  assert.match(flat(document.querySelector('[data-scope-reanchor-why]')), /not read/);
});

test('spent (W26): nothing left to add reads "nothing to link", off, title "nothing to add", never "0 schemas"', async () => {
  const { document } = await setUp(view(endpoint(dbRow(0, 26))));
  const b = btn(document);
  assert.ok(b && b.disabled);
  assert.equal(flat(b), 'nothing to link');
  assert.equal(b.getAttribute('title'), 'nothing to add');
  assert.doesNotMatch(flat(document.querySelector('[data-scope-plan-database]')), /0 schemas/);
  assert.equal(document.querySelector('[data-scope-reanchor-why]'), null, 'the label already says it');
});

test('W25: the result line and its "pressed" time use the one form, MM-DD HH:MM', async () => {
  const words = '26 members · 22 added · read back 10-31 20:38';
  const press = { at: '2026-10-31T20:38:31.104512', by: 'dan', words, counts: { added: 22, already_linked: 0, failed: 0 }, failed: [], members_after: 26 };
  const { document } = await setUp(view(endpoint(dbRow(0, 26), press)));
  const line = flat(document.querySelector('[data-scope-reanchor-result]'));
  assert.equal(line, '26 members · 22 added · read back 10-31 20:38 · pressed 10-31 20:38 · dan');
  assert.doesNotMatch(line, /2026-|T20|\.104/);
});

test('cancel at the confirm sends nothing', async () => {
  const { document, server } = await setUp(view(endpoint(dbRow(22))));
  const asked = [];
  const kit = autoAnswerConfirm(document, { answer: false, asked });
  btn(document).click();
  await wait(100);
  kit.stop();
  assert.equal(asked.length, 1);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/reanchor')).length, 0);
});

test('confirm, "linking…" while it runs, press once, then the row acknowledges and validates', async () => {
  const ctx = await setUp(view(endpoint(dbRow(22))));
  const { document, server } = ctx;
  const base = globalThis.fetch;
  const words = '26 members · 22 added · read back 10-31 09:05';
  let release;
  const held = new Promise((r) => { release = r; });
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).endsWith('/reanchor') && opts.method === 'POST') {
      server.calls.push({ method: 'POST', url: String(url) });
      await held;
      server.view = view(endpoint(dbRow(0, 26), { at: '2026-10-31T09:05:00', by: 'me', words, counts: { added: 22, already_linked: 0, failed: 0 }, failed: [], members_after: 26 }));
      return { ok: true, status: 200, json: async () => ({ words, counts: { added: 22, already_linked: 0, failed: 0 }, members_after: 26 }) };
    }
    return base(url, opts);
  };
  const asked = [];
  const kit = autoAnswerConfirm(document, { answer: true, asked });
  btn(document).click();
  await wait(100);
  const busy = btn(document);
  assert.equal(busy.getAttribute('aria-busy'), 'true');
  assert.equal(flat(busy), 'linking…');
  assert.ok(busy.disabled, 'a second click while it runs sends nothing');
  release();
  await wait(300);
  kit.stop();
  globalThis.fetch = base;
  assert.match(asked[0], /^Link the pack's 22 schemas/);
  assert.match(asked[0], /each of the pack's 22 schemas/);
  assert.match(asked[0], /After the press: 26 members/);
  assert.match(asked[0], /nothing is deleted/);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/reanchor')).length, 1, 'one press');
  assert.equal(flat(document.querySelector('[data-scope-status]')), words);
  assert.match(flat(document.querySelector('[data-scope-reanchor-result]')), /^26 members · 22 added · read back 10-31 09:05 · pressed 10-31 09:05/);
  const after = btn(document);
  assert.ok(after.disabled && /nothing to add/.test(after.getAttribute('title')) && flat(after) === 'nothing to link', 'nothing left to press');
  assert.doesNotMatch(flat(document.querySelector('[data-scope-plan-database]')), /after the press/);
});

test('a partial press lists what was not linked, muted, and is never worded as failed for all', async () => {
  const words = '25 members · 21 added · 1 not linked: sec_admin (not authorized) · read back 10-31 09:05';
  const press = { at: '2026-10-31T09:05:00', by: 'me', words, counts: { added: 21, already_linked: 0, failed: 1 }, failed: [{ schema: 'sec_admin', error: 'not authorized' }], members_after: 25 };
  const { document } = await setUp(view(endpoint(dbRow(1, 25), press)));
  const line = document.querySelector('[data-scope-reanchor-result]');
  assert.equal(line.dataset.added, '21');
  assert.match(flat(line), /^25 members · 21 added · 1 not linked: sec_admin/);
  assert.doesNotMatch(flat(line), /failed/);
  const f = document.querySelector('[data-scope-reanchor-failed="sec_admin"]');
  assert.ok(f && f.className.includes('text-ink-muted'));
  assert.equal(flat(btn(document)), "Link the pack's 1 schema", 'the one left can be pressed again');
});
