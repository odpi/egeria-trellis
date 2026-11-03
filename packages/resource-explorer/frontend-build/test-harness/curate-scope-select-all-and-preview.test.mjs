/** Contents, found live on coco_pharma 2026-10-30.
 *  C1: "select all" then a bulk choice must CHOOSE every row it says it chose, in one request, and the tree must then
 *  read them as chosen. The live run sent a schema's tables one request at a time (1.5-4 s each); a refusal part-way
 *  stopped the loop with half of it written. And a table that follows its schema's choice must say which choice.
 *  C2: the commit preview (tens of seconds on a large database) must not hold the page: a choice reads as saved when
 *  its write returns, the Publish control is there and off with "checking what a commit would do…", and a run of
 *  clicks reads the preview once after they stop, not once per click. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, calls, flat, wait } from './scope-test-kit.mjs';

const tickAll = (document, window) => {
  const all = document.querySelector('[data-scope-all-box]');
  all.checked = true;
  all.dispatchEvent(new window.Event('change', { bubbles: true }));
};
const previews = (server) => calls(server, 'GET', '/commit-preview');

test('C1: select all shown, then include selected: ONE request naming exactly those rows, and the tree reads them as chosen', async () => {
  const { document, window, server } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();          // sales's tables are shown, so they are ticked too
  tickAll(document, window);
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^3 of 3 schemas · 2 of 3 tables selected$/);
  server.calls.length = 0;
  document.querySelector('[data-scope-bulk-act="catalogue"]').click();
  await wait();
  const writes = server.calls.filter((c) => c.method !== 'GET');
  assert.equal(writes.length, 1, `one write, got ${writes.map((w) => `${w.method} ${w.url}`).join(', ')}`);
  assert.ok(writes[0].url.endsWith('/nodes'));
  assert.deepEqual(writes[0].body, { nodes: [
    { schema_name: 'sales', table_name: '' }, { schema_name: 'archive', table_name: '' }, { schema_name: 'empty_one', table_name: '' },
    { schema_name: 'sales', table_name: 'orders' }, { schema_name: 'sales', table_name: 'customers' },
  ], choice: 'catalogue', all_schemas: false });
  assert.equal(calls(server, 'POST', '/node/clear').length, 0, 'an include never sends a clear');
  for (const k of ['schema:sales', 'schema:archive', 'schema:empty_one', 'table:sales.orders', 'table:sales.customers']) {
    const cell = row(document, k).querySelector('[data-scope-choice-cell]');
    assert.ok(cell.querySelector('[data-scope-state-word="chosen"]'), `${k} reads as chosen`);
    assert.equal(cell.querySelector('[data-scope-choice="catalogue"]').getAttribute('aria-pressed'), 'true', `${k}: Include is pressed`);
  }
  assert.match(flat(document.querySelector('[data-scope-status]')), /^3 schemas and 2 tables now set to include by me$/);
});

test('C1: a bulk choice the re-read does not confirm says so, with the counts, never "set"', async () => {
  const { document, window, server } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  tickAll(document, window);
  server.dropWrites = true;                                                  // the server answers 200 and keeps nothing
  document.querySelector('[data-scope-bulk-act="catalogue"]').click();
  await wait();
  assert.match(flat(document.querySelector('[data-scope-status]')),
    /^the write returned, but the re-read scope shows only 0 of 3 schemas and 0 of 2 tables set to include$/);
});

test('C1: the bulk clear is "back to undecided", apart in words from "clear selection"', async () => {
  const { document } = await setUp(baseView());
  assert.equal(flat(document.querySelector('[data-scope-bulk-act="clear"]')), 'back to undecided');
  assert.equal(flat(document.querySelector('[data-scope-clear-selection]')), 'clear selection');
});

test('C1: a table that follows its schema says which choice it follows, not just "(schema)"', async () => {
  const v = baseView();
  const s = v.schemas[0];
  s.explicit = { choice: 'catalogue', action: 'set', source: 'person', by: 'me', at: '2026-10-04T10:00:00', reason: '' };
  s.effective = 'catalogue'; s.state = 'chosen';
  s.tables.forEach((t) => { t.effective = 'catalogue'; t.effective_from = 'schema'; t.inherited = 'catalogue'; });
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.match(flat(row(document, 'table:sales.orders').querySelector('[data-scope-state-word="inherited"]')), /^include \(schema\)/);
});

/** Hold every /commit-preview until `release()`; count them. */
function holdPreviews() {
  const inner = globalThis.fetch;
  const held = [];
  globalThis.fetch = async (url, opts = {}) => {
    if (String(url).endsWith('/commit-preview')) await new Promise((r) => held.push(r));
    return inner(url, opts);
  };
  return { release: () => held.splice(0).forEach((r) => r()), get waiting() { return held.length; } };
}

test('C2: while the preview is read, Publish is there and off with the reason, and a choice reads as saved at once', async () => {
  let gate;
  const { document } = await setUp(baseView(), { beforeOpen: ({ mod }) => { mod.setPreviewDebounceMs(60); gate = holdPreviews(); } });
  const btn = document.querySelector('[data-scope-commit-loading] [data-scope-commit-btn]');
  assert.ok(btn, 'the Publish control is on the page while the preview is read');
  assert.equal(btn.disabled, true);
  assert.equal(btn.title, 'checking what a commit would do…');
  assert.match(flat(document.querySelector('[data-scope-commit-loading]')), /checking what a commit would do…/);
  document.querySelector('[data-scope-row="schema:archive"] [data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  const cell = row(document, 'schema:archive').querySelector('[data-scope-choice-cell]');
  assert.equal(cell.querySelector('[data-scope-saving]'), null, 'the preview still running does not keep the row on "saving…"');
  assert.match(flat(cell), /saved · you · just now/);
  assert.ok(document.querySelector('[data-scope-commit-loading] [data-scope-commit-btn][disabled]'), 'Publish stays off while it checks');
  gate.release(); await wait(200); gate.release(); await wait(200);
  const live = document.querySelector('[data-scope-commit-panel] [data-scope-commit-btn]');
  assert.ok(live, 'the panel replaces the checking line when the preview answers');
});

test('C2: five clicks while a preview runs read the preview twice in all (the first, then one after the clicks), not six times', async () => {
  let gate;
  const { document, server } = await setUp(baseView(), { beforeOpen: ({ mod }) => { mod.setPreviewDebounceMs(80); gate = holdPreviews(); } });
  assert.equal(previews(server).length, 0, 'held: not yet answered');
  assert.equal(gate.waiting, 1, 'the first read is out');
  const press = (k, c) => document.querySelector(`[data-scope-row="${k}"] [data-scope-act="set"][data-scope-choice="${c}"]`).click();
  for (const [k, c] of [['schema:sales', 'catalogue'], ['schema:archive', 'catalogue'], ['schema:empty_one', 'leave_out'],
    ['schema:sales', 'leave_out'], ['schema:archive', 'leave_out']]) {
    press(k, c);
    await wait(30);                                                        // inside the debounce of the next click
  }
  await wait(300);
  assert.equal(gate.waiting, 1, 'nothing more is asked while the first read is out');
  gate.release(); await wait(300);
  assert.equal(gate.waiting, 1, 'then ONE read, for every click made meanwhile');
  gate.release(); await wait(300);
  assert.equal(previews(server).length, 2);
  assert.ok(document.querySelector('[data-scope-commit-panel] [data-scope-commit-btn]'));
});

test('C2: a redraw with no new choice reuses the preview already read', async () => {
  const { document, server } = await setUp(baseView(), { beforeOpen: ({ mod }) => mod.setPreviewDebounceMs(40) });
  await wait(100);
  const n = previews(server).length;
  assert.equal(n, 1);
  document.querySelector('[data-subtab="curate_describe"]')?.click(); await wait(100);
  document.querySelector('[data-subtab="curate_contents"]')?.click(); await wait(200);
  assert.equal(previews(server).length, 1, 'opening Contents again asks Egeria nothing new');
});

test('F28: a read-back that could not read Egeria says so on the page, never "cataloged by Egeria 0"', async () => {
  const { document } = await setUp(baseView(), { beforeOpen: ({ mod }) => mod.setPreviewDebounceMs(20) });
  await wait(100);
  const inner = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => (String(url).endsWith('/read-back')
    ? { ok: true, status: 200, json: async () => ({ catalogued: 0, attached_waiting: 0, read_failed: 1,
      chips_words: 'published by RE 2 ✓ · attached 4 schemas ✓ · cataloged by Egeria 0 ◌',
      line: "could not read Egeria · the cataloguer's connector: TIMEOUT_ERROR_408 · nothing changed: the counts are from the last read that worked" }) }
    : inner(url, opts));
  document.querySelector('[data-scope-read-back]').click();
  await wait(300);
  const status = flat(document.querySelector('[data-scope-status]'));
  assert.match(status, /^could not read Egeria · the cataloguer's connector: TIMEOUT_ERROR_408 · nothing changed/);
  assert.ok(!/cataloged by Egeria 0/.test(status));
  globalThis.fetch = inner;
});
