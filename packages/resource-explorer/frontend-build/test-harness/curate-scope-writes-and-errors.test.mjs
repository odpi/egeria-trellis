/** One write on the scope pane costs ONE scope read and ONE commit-preview read (it used to cost
 *  two scope reads), and an exception while drawing the pane shows a visible banner instead of a
 *  dead page. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, scopeEl, row, calls, flat, wait, schema, table } from './scope-test-kit.mjs';

const scopeReads = (server) => server.calls.filter((c) => c.method === 'GET' && /\/api\/catalogue-scope\/[^/]+$/.test(c.url));
const previewReads = (server) => server.calls.filter((c) => c.method === 'GET' && c.url.endsWith('/commit-preview'));

test('one choice write makes exactly one PUT, one scope GET and one commit-preview GET', async () => {
  const { document, server } = await setUp(baseView());
  server.calls.length = 0;
  document.querySelector('[data-scope-row="schema:sales"] [data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  assert.equal(calls(server, 'PUT', '/node').length, 1);
  assert.equal(scopeReads(server).length, 1, 'the scope is read once after a write');
  assert.equal(previewReads(server).length, 1, 'and so is the commit preview');
  assert.match(flat(row(document, 'schema:sales').querySelector('[data-scope-choice-cell]')), /include · set by me/);
});

test('a bulk action over schemas and tables also re-reads once', async () => {
  const { document, window, server } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  for (const sel of ['[data-scope-select="archive"]', '[data-scope-select-schema="sales"][data-scope-select-table="orders"]']) {
    const b = document.querySelector(sel); b.checked = true; b.dispatchEvent(new window.Event('change', { bubbles: true }));
  }
  server.calls.length = 0;
  document.querySelector('[data-scope-bulk-act="leave_out"]').click();
  await wait();
  assert.equal(scopeReads(server).length, 1);
  assert.equal(previewReads(server).length, 1);
});

test('an exception while drawing the pane shows a visible banner with the cause and a reload hint', async () => {
  const v = baseView();
  Object.defineProperty(v.schemas[0], 'table_count', { get() { throw new Error('boom while drawing'); }, enumerable: true });
  const { document } = await setUp(v);
  const banner = scopeEl(document).querySelector('[data-scope-render-error]');
  assert.ok(banner, 'a banner is drawn in the pane instead of a blank one');
  assert.match(flat(banner), /could not be drawn/);
  assert.match(flat(banner), /boom while drawing/);
  assert.match(flat(banner), /reload/i);
  assert.ok(document.querySelector('[data-curate-work="glossary"]'), 'the rest of the stage is still there');
});

test('a coco_pharma-sized scope draws its schemas collapsed: the tables are drawn when a schema is opened', async () => {
  const schemas = [];
  for (let i = 0; i < 29; i++) schemas.push(schema(`s${i}`, Array.from({ length: i < 28 ? 9 : 23 }, (_, j) => table(`s${i}`, `t${j}`))));
  const { document } = await setUp(baseView({ schemas }));
  assert.equal(document.querySelectorAll('[data-scope-row^="table:"]').length, 0, 'no table row until a schema is opened');
  assert.equal(document.querySelectorAll('[data-scope-row^="schema:"]').length, 29);
  document.querySelector('[data-scope-toggle="s3"]').click();
  assert.equal(document.querySelectorAll('[data-scope-row^="table:"]').length, 9);
});
