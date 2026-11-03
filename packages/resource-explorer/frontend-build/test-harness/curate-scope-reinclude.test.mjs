/** S19 (owner ruling 2026-10-14): an archived element is re-included only on confirmation. Its row in the commit panel
 *  is the archived cue with a short word ("archived · <date>"), the sentence on hover, and a Re-include control that
 *  re-submits the press naming the archived GUID. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, flat, wait, calls } from './scope-test-kit.mjs';

const SENTENCE = 'archived in Egeria · 2026-10-09 · re-including creates a new element; the archived record stays';
const ROW = {
  node: 'schema', name: 'sales', archived_guid: 'ad2724e6-40e9-46a9-aaff-e3352c6b04bb',
  archived_qualified_name: 'x::shop.sales_archivedOn_Fri Oct 09 01:28:08 GMT 2026', archived_on: 'Fri Oct 09 01:28:08 GMT 2026',
  archived_at: '2026-10-09T01:28:08', date: '2026-10-09', words: 'archived · 2026-10-09', text: `sales: ${SENTENCE}`,
};
const PREVIEW = (over = {}) => ({
  manifest: { lines: [{ id: 're_publishes', mechanism: 1, text: 'RE publishes the server and database assets.' }] },
  can_commit: false, button: 'Publish · 0 schemas', blockers: ['nothing to commit: choose at least one schema to publish'],
  leave_out: [], refused: [], collisions: [], needs_confirmation: [ROW], ...over,
});
const declaredView = () => baseView({ declared: { declared: true, by: 'me', at: '2026-10-04T08:00:00', kind: 'declare', baseline_survey_at: '' } });
async function open(preview, opts) {
  const ctx = await setUp(declaredView(), { ...opts, beforeOpen: ({ server }) => { server.preview = preview; } });
  const fold = ctx.document.querySelector('[data-scope-collapse]');
  if (fold && fold.getAttribute('aria-expanded') === 'false') { fold.click(); await wait(); }
  return ctx;
}

test('an archived schema shows the archived cue and a short word, the sentence on hover, and a Re-include control', async () => {
  const { document } = await open(PREVIEW());
  const row = document.querySelector('[data-scope-reinclude="sales"]');
  assert.ok(row, 'the row is in the commit panel');
  const c = row.querySelector('[data-cue="archived"]');
  assert.ok(c, 'a cue, not a sentence');
  assert.equal(flat(c), '□ archived · 2026-10-09');
  assert.equal(c.getAttribute('title'), `sales: ${SENTENCE}`);
  const b = row.querySelector('[data-scope-reinclude-btn]');
  assert.equal(b.tagName, 'BUTTON');
  assert.equal(flat(b), 'Re-include');
  assert.equal(b.disabled, false);
  assert.equal(b.getAttribute('data-archived-guid'), ROW.archived_guid);
  assert.ok(!flat(row).includes('re-including creates a new element'), 'the sentence is on demand, not on the row');
});

test('Re-include re-submits the press with confirm_reinclude naming the archived GUID', async () => {
  const ctx = await open(PREVIEW());
  let release; ctx.server.holdCommit = new Promise((r) => { release = r; });
  const b = ctx.document.querySelector('[data-scope-reinclude-btn]');
  b.click();
  await wait(20);
  assert.equal(flat(b), 're-including…');
  assert.equal(b.disabled, true);
  release();
  await wait(60);
  const posts = calls(ctx.server, 'POST', '/commit').filter((c) => c.url.endsWith('/commit'));
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body.confirm_reinclude, [{ node: 'schema', name: 'sales', archived_guid: ROW.archived_guid }]);
  assert.match(flat(ctx.document.querySelector('[data-scope-commit-sent]')), /^sent · waiting for Egeria$/);
});

test('the Publish button never sends a confirmation', async () => {
  const ctx = await open(PREVIEW({ can_commit: true, button: 'Publish · 1 schema', blockers: [] }));
  ctx.document.querySelector('[data-scope-commit-btn]').click();
  await wait(60);
  const post = calls(ctx.server, 'POST', '/commit').find((c) => c.url.endsWith('/commit'));
  assert.deepEqual(post.body, { refresh_now: true });
});

test('a database row says the database, and signed out the control is off and says why', async () => {
  const db = { ...ROW, node: 'database', name: '', text: `the database: ${SENTENCE}` };
  const { document } = await open(PREVIEW({ needs_confirmation: [db] }), { signedIn: false });
  const row = document.querySelector('[data-scope-reinclude=""][data-node="database"]');
  assert.ok(row && flat(row).includes('the database'));
  const b = row.querySelector('[data-scope-reinclude-btn]');
  assert.equal(b.disabled, true);
  assert.match(b.getAttribute('title'), /sign in/i);
});

test('no row and no control when nothing is archived', async () => {
  const { document } = await open(PREVIEW({ needs_confirmation: [] }));
  assert.equal(document.querySelector('[data-scope-reinclude]'), null);
  assert.equal(document.querySelector('[data-scope-reinclude-btn]'), null);
});
