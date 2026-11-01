/** W29 (owner's screenshot of Find open data): a 60-character portal id in mono pushed the candidate row past the
 *  dialog's right edge and the Portal column off-screen. The identifier cell breaks anywhere, the table is fixed
 *  layout with a width for Title, and the results area scrolls sideways as the backstop. The dataset page's
 *  identifier breaks the same way. Real open_data.js, a fake fetch. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered, makeDomEnvironment } from './dom-harness.mjs';

ensureLoaderRegistered();
const { document } = makeDomEnvironment();
const LONG = 'ca-' + 'f'.repeat(57);                         // 60 characters, no break opportunity of its own
const row = { title: 'Water quality', publisher: 'Env', licence_id: 'ca-ogl-lgo',
  licence: { attribution: true, share_alike: false, commercial: true }, modified: '2026-10-01', formats: 'CSV',
  size: 10, identifier: LONG, doi: 'not_reported', portal_name: 'open.canada.ca' };

test('the results area scrolls sideways (the pane source carries the class)', async () => {
  const src = (await import('node:fs')).readFileSync(new URL('../../resource_explorer/web/static/next/admin/open_data.js', import.meta.url), 'utf8');
  assert.match(src, /<div data-od-results class="overflow-x-auto"><\/div>`;\n  const form = q\(pane, '\[data-od-search\]'\);/);
});

test('a 60-character identifier in a candidate row breaks inside its cell; the table is fixed with a Title width', async () => {
  assert.equal(LONG.length, 60);
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '{}' });
  const od = await import('/static/next/admin/open_data.js');
  const host = document.createElement('div');
  host.innerHTML = '<div data-od-pane><div data-od-results class="overflow-x-auto"></div></div>';
  document.body.appendChild(host);
  const out = host.querySelector('[data-od-results]');
  od.drawCandidates(host, out, 'canada', { rows: [row], total: 1, calls_made: 1, licence_dropped: 0 }, 'water', '', '');
  const cell = host.querySelector('[data-od-row="0"] [data-od-identifier]');
  assert.ok(cell, 'the identifier cell is drawn');
  assert.ok(cell.textContent.includes(LONG) && cell.classList.contains('font-mono'));
  assert.ok(cell.classList.contains('break-all'), 'a long mono id wraps inside its cell');
  const table = host.querySelector('[data-rc-id="od-candidates"]');
  assert.ok(table.classList.contains('table-fixed'));
  assert.match(table.querySelector('thead th:nth-child(2)').className, /w-\[14rem\]/, 'Title keeps a width');
  // Canadian rows show the three flags, not "?" (W30's table row, as the server sends it)
  assert.doesNotMatch(host.querySelector('[data-od-row="0"] td:nth-child(5)').textContent, /\?/);
});

test("the dataset page's identifier breaks the same way", async () => {
  const od = await import('/static/next/admin/open_data.js');
  const html = od.datasetPageHtml({ title: 'Water', identifier_kind: 'portal', identifier: LONG, imported_at: '2026-10-31T10:00:00',
    work_list_slug: 'w', publisher: 'Env', licence_id: 'ca-ogl-lgo', licence: row.licence, record: {} });
  const el = document.createElement('div');
  el.innerHTML = html;
  const id = el.querySelector('[data-od-identifier]');
  assert.ok(id && id.textContent === LONG && id.classList.contains('break-all'));
});
