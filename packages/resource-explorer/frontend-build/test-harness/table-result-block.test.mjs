/** B1 (DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §5): a table result draws its summary as the headline, then its
 *  rows under a <details> that starts closed, its summary line the declared truncation sentence; an empty cell reads its
 *  absence word (never blank), a state column is drawn as the house marks, and "the numbers behind this" opens the
 *  summary measures. Registered under result_kind "table" with the generic renderers. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const PAYLOAD = {
  headline: '1,846 of 1,916 columns · 12,040 nulls',
  measures: [{ name: 'columns_measured', words: '1,846 of 1,916 columns' }, { name: 'nulls_total', words: '12,040 nulls' }],
  band: 'rows stay in Resource Explorer · summary published',
  table: {
    stored: true, total: 1916, shown: 2,
    truncation: 'the first 2 of 1,916 columns; every column is stored and the summary counts them all',
    columns: [{ name: 'column', type: 'text', key: true }, { name: 'state', type: 'state', absence: 'not profiled' },
      { name: 'null_count', type: 'integer', absence: 'not counted' }],
    rows: [{ column: 'a', state: 'measured', null_count: 1200, absent: [] },
      { column: 'b', state: 'not_permitted', null_count: 'not counted', absent: ['null_count'] }],
  },
};

async function render(p = PAYLOAD) {
  makeDomEnvironment();
  await loadAppModule();
  const { tableResultHtml } = await import('/static/next/envelope.js');
  const { glyphOf } = await import('/static/next/glyphs.js');
  const host = document.createElement('div');
  host.innerHTML = tableResultHtml(p, esc, glyphOf);
  return host;
}

test('the headline is the summary, and the table starts closed under its truncation sentence', async () => {
  const host = await render();
  assert.equal(host.querySelector('[data-table-headline]').textContent, '1,846 of 1,916 columns · 12,040 nulls');
  const details = host.querySelector('details[data-table-result]');
  assert.equal(details.open, false);
  assert.equal(details.querySelector('summary').textContent,
    'the first 2 of 1,916 columns; every column is stored and the summary counts them all');
  assert.equal(host.querySelector('[data-table-band]').textContent, 'rows stay in Resource Explorer · summary published');
});

test('no cell is blank: an empty cell reads its absence word, muted', async () => {
  const host = await render();
  const cells = [...host.querySelectorAll('table[data-table-grid] tbody td')];
  assert.equal(cells.length, 6);
  assert.ok(cells.every((td) => td.textContent.trim() !== ''));
  assert.equal(host.querySelector('[data-table-absent]').textContent, 'not counted');
  assert.ok(cells.some((td) => td.textContent.trim() === '1,200'));
});

test('a state column is drawn as the house marks', async () => {
  const host = await render();
  const marks = [...host.querySelectorAll('[data-table-state]')].map((e) => e.textContent.trim());
  assert.deepEqual(marks, ['✓ measured', '? not permitted']);
});

test('"the numbers behind this" opens the summary measures', async () => {
  const host = await render();
  const behind = host.querySelector('details[data-table-numbers]');
  assert.equal(behind.querySelector('summary').textContent, 'the numbers behind this ›');
  assert.deepEqual([...behind.querySelectorAll('li')].map((li) => li.textContent),
    ['1,846 of 1,916 columns', '12,040 nulls']);
});

test('nothing stored is said in the declared words, never as an empty table', async () => {
  const host = await render({ ...PAYLOAD, table: { stored: false }, not_stored: 'Not profiled yet.' });
  assert.equal(host.querySelector('[data-table-not-stored]').textContent, 'Not profiled yet.');
  assert.equal(host.querySelector('details[data-table-result]'), null);
});
