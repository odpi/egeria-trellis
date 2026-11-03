/** The Curate scope tree: a name filter over schemas AND tables, a tick on every table row,
 *  select all / clear on the VISIBLE rows, and bulk actions that cover the whole selection
 *  (schemas and tables in ONE request to the bulk route, each node recorded and signed by the
 *  server like a single-row write). The server is a stateful fake. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, scopeEl, row, calls, flat, wait } from './scope-test-kit.mjs';

const typeFilter = async (document, window, text) => {
  const box = document.querySelector('[data-scope-filter]');
  box.value = text;
  box.dispatchEvent(new window.Event('input', { bubbles: true }));
  await wait(300);   // the filter is debounced (about 150 ms)
};
const tick = (document, window, sel, on = true) => {
  const b = document.querySelector(sel);
  b.checked = on;
  b.dispatchEvent(new window.Event('change', { bubbles: true }));
};
const tableBox = (d, s, t) => d.querySelector(`[data-scope-select-schema="${s}"][data-scope-select-table="${t}"]`);

test('the filter keeps a schema when one of its tables matches, expands it, and counts what is shown', async () => {
  const { document, window } = await setUp(baseView());
  assert.match(flat(document.querySelector('[data-scope-filter-count]')), /^showing 6 of 6$/);
  await typeFilter(document, window, 'CUST');   // case-insensitive
  assert.ok(row(document, 'schema:sales'), 'sales stays: its table customers matches');
  assert.ok(row(document, 'table:sales.customers'), 'and it is expanded to show the match');
  assert.equal(row(document, 'table:sales.orders'), null, 'a table that does not match is not drawn');
  assert.equal(row(document, 'schema:archive'), null);
  assert.equal(row(document, 'schema:empty_one'), null);
  assert.match(flat(document.querySelector('[data-scope-filter-count]')), /^showing 2 of 6$/);
  await typeFilter(document, window, '');
  assert.ok(row(document, 'schema:archive') && row(document, 'schema:empty_one'), 'clearing the filter brings everything back');
  assert.match(flat(document.querySelector('[data-scope-filter-count]')), /^showing 6 of 6$/);
});

test('a schema whose own name matches is shown with its tables', async () => {
  const { document, window } = await setUp(baseView());
  await typeFilter(document, window, 'sale');
  assert.ok(row(document, 'schema:sales') && row(document, 'table:sales.orders') && row(document, 'table:sales.customers'));
  assert.equal(row(document, 'schema:archive'), null);
  assert.match(flat(document.querySelector('[data-scope-filter-count]')), /^showing 3 of 6$/);
});

test('typing does not re-render the filter box or the page: only the tree is redrawn, after the debounce', async () => {
  const { document, window } = await setUp(baseView());
  const box = document.querySelector('[data-scope-filter]');
  box.value = 'o';
  box.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert.ok(row(document, 'schema:empty_one'), 'nothing is redrawn synchronously on a keystroke');
  await wait(300);
  assert.equal(document.querySelector('[data-scope-filter]'), box, 'the same input element stays (focus is kept)');
});

test('table rows carry a tick; ticking one counts it as a table, schemas stay counted apart', async () => {
  const { document, window } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^0 of 3 schemas · 0 of 3 tables selected$/);
  tick(document, window, '[data-scope-select-schema="sales"][data-scope-select-table="customers"]');
  tick(document, window, '[data-scope-select="empty_one"]');
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^1 of 3 schemas · 1 of 3 tables selected$/);
  assert.equal(tableBox(document, 'sales', 'customers').checked, true);
});

test('select all and clear act on the VISIBLE rows only', async () => {
  const { document, window } = await setUp(baseView());
  await typeFilter(document, window, 'orders');
  const all = document.querySelector('[data-scope-all-box]');
  all.checked = true;
  all.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^2 of 3 schemas · 2 of 3 tables selected$/);
  assert.equal(tableBox(document, 'sales', 'orders').checked, true);
  assert.equal(tableBox(document, 'archive', 'orders').checked, true);
  await typeFilter(document, window, '');   // widen again: the hidden ones were never selected
  assert.equal(document.querySelector('[data-scope-select="empty_one"]').checked, false);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(tableBox(document, 'sales', 'customers').checked, false, 'customers was not visible when select all ran');
  // clear acts on the visible rows too
  await typeFilter(document, window, 'archive');
  document.querySelector('[data-scope-clear-selection]').click();
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^1 of 3 schemas · 1 of 3 tables selected$/,
    'only the archive rows (visible) were cleared; sales stays selected');
});

test('catalogue selected writes the schemas and the tables in one bulk request', async () => {
  const { document, window, server } = await setUp(baseView());
  await typeFilter(document, window, 'orders');
  const all = document.querySelector('[data-scope-all-box]');
  all.checked = true;
  all.dispatchEvent(new window.Event('change', { bubbles: true }));
  server.calls.length = 0;
  document.querySelector('[data-scope-bulk-act="catalogue"]').click();
  await wait();
  const bulks = calls(server, 'POST', '/nodes');
  assert.equal(bulks.length, 1, 'one request for the whole selection');
  assert.deepEqual(bulks[0].body, { nodes: [
    { schema_name: 'sales', table_name: '' }, { schema_name: 'archive', table_name: '' },
    { schema_name: 'sales', table_name: 'orders' }, { schema_name: 'archive', table_name: 'orders' },
  ], choice: 'catalogue', all_schemas: false });
  assert.equal(calls(server, 'PUT', '/node').length, 0, 'no table is written on its own');
  assert.match(flat(document.querySelector('[data-scope-status]')), /^2 schemas and 2 tables now set to include by me$/);
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^0 of 3 schemas · 0 of 3 tables selected$/, 'a finished bulk action spends the selection');
});

test('back to undecided on selected tables is one bulk request with an empty choice', async () => {
  const { document, window, server } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  tick(document, window, '[data-scope-select-schema="sales"][data-scope-select-table="customers"]');
  document.querySelector('[data-scope-bulk-act="clear"]').click();
  await wait();
  assert.deepEqual(calls(server, 'POST', '/nodes').map((c) => c.body),
    [{ nodes: [{ schema_name: 'sales', table_name: 'customers' }], choice: '', all_schemas: false }]);
  assert.equal(calls(server, 'POST', '/node/clear').length, 0, 'no per-row clear: a row with no choice is skipped, not refused');
});

test('the selection survives the re-render that follows a single-row write', async () => {
  const { document, window } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  tick(document, window, '[data-scope-select-schema="sales"][data-scope-select-table="customers"]');
  tick(document, window, '[data-scope-select="archive"]');
  document.querySelector('[data-scope-row="schema:empty_one"] [data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  assert.match(flat(row(document, 'schema:empty_one').querySelector('[data-scope-choice-cell]')), /include · set by me/);
  assert.equal(tableBox(document, 'sales', 'customers').checked, true);
  assert.equal(document.querySelector('[data-scope-select="archive"]').checked, true);
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /^1 of 3 schemas · 1 of 3 tables selected$/);
});

test('the filter text survives that re-render too', async () => {
  const { document, window } = await setUp(baseView());
  await typeFilter(document, window, 'cust');
  document.querySelector('[data-scope-row="schema:sales"] [data-scope-act="set"][data-scope-choice="leave_out"]').click();
  await wait();
  assert.equal(document.querySelector('[data-scope-filter]').value, 'cust');
  assert.equal(row(document, 'schema:archive'), null);
});

test('signed out: the table ticks, the filter-driven select all and the bulk buttons are disabled with the reason', async () => {
  const { document } = await setUp(baseView(), { signedIn: false });
  document.querySelector('[data-scope-toggle="sales"]').click();
  const b = tableBox(document, 'sales', 'orders');
  assert.ok(b.disabled);
  assert.match(b.title, /sign in to change what gets published/);
  assert.ok(!document.querySelector('[data-scope-filter]').disabled, 'filtering is a read: it stays usable');
  assert.ok(scopeEl(document));
});
