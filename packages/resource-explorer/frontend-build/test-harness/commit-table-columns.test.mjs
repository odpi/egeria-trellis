/** "What this commit does": the same column treatment as the scope table (colresize.js).
 *  jsdom does no layout, so the real-box checks (header and cell edges equal, no overlaps, one
 *  column changing on a drag) were measured in headless Chrome against this same markup; here
 *  the structure that produces them is pinned: fixed px widths from ONE spec for header and
 *  cells (a ch differs between a small header font and its cells, which is what misaligned them),
 *  handles, centred text but the label column, notes inside the table, the refresh box in a
 *  fixed-width column so it cannot squeeze the table. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const env = makeDomEnvironment();
await loadAppModule();
const { commitPanelHtml } = await import('/static/next/stages/curate-scope.js');
const { MANIFEST_COLS } = await import('/static/next/stages/scope-columns.js');
const { attachColumnResize } = await import('/static/next/colresize.js');

const L = (id, mechanism, text) => ({ id, mechanism, text });
const preview = {
  can_commit: true, button: 'Publish · 2 schemas', blockers: [], attach: ['sales', 'archive'], refused: [],
  leave_out: [{ schema: 'eu_sales', form: 'none', blocked: false, text: 'eu_sales: nothing to remove · not in Egeria yet' }], collisions: [],
  survey: { schemas: ['sales', 'archive'] },
  manifest: { lines: [L('re_publishes', 1, 'RE publishes.'), L('cataloguer_creates', 2, 'Creates.'), L('survey_measures', 3, 'Survey.'), L('whole_schemas', 0, 'Whole.')] },
};
const panel = () => {
  const d = env.document;
  const div = d.createElement('div');
  div.innerHTML = commitPanelHtml(preview, { user_id: 'x' }, null, true, { counts: { schemas_undecided: 3 } });
  d.body.append(div);
  return div;
};
const widthOf = (e) => e.getAttribute('style').match(/width:var\(--rc-(\w+),(\d+)px\)/).slice(1, 3).join(':');

test('header and cells take their widths from the same spec, in px, never ch', () => {
  const p = panel();
  const rows = [...p.querySelectorAll('[data-scope-manifest] [role=row]')].filter((r) => r.children.length === 4);
  assert.equal(rows.length, 6, 'header + five mechanism rows');
  const head = rows[0].children;
  for (const r of rows) {
    [...r.children].forEach((c, i) => {
      assert.equal(widthOf(c), widthOf(head[i]), 'a cell is exactly as wide as its header');
      assert.match(c.getAttribute('style'), /flex:0 0 auto/);
      assert.doesNotMatch(c.outerHTML.split('>')[0], /\bw-\[\d+ch\]|min-w-\[|flex-1/);
    });
  }
  assert.deepEqual([...head].map((h) => h.textContent.trim()), ['', 'what', 'how many', 'when']);
});

test('text is centred in every column except the row label', () => {
  const p = panel();
  for (const r of [...p.querySelectorAll('[data-scope-manifest] [role=row]')].filter((x) => x.children.length === 4)) {
    const a = [...r.children].map((c) => c.getAttribute('style').match(/text-align:(\w+)/)[1]);
    assert.deepEqual(a, ['left', 'center', 'center', 'center']);
  }
});

test('every header but none other has a handle; drag, keyboard and reset change one column only', () => {
  const p = panel();
  const host = p.querySelector('[data-scope-manifest-host]');
  assert.ok(host.className.includes('overflow-x-auto'), 'the table scrolls inside its own box');
  assert.deepEqual([...host.querySelectorAll('[data-rc-handle]')].map((h) => h.dataset.rcHandle), ['who', 'what', 'many', 'when']);
  const api = attachColumnResize(host, MANIFEST_COLS);
  const h = host.querySelector('[data-rc-handle="many"]');
  const win = env.window;
  h.dispatchEvent(new win.MouseEvent('pointerdown', { clientX: 300, bubbles: true, cancelable: true }));
  win.document.dispatchEvent(new win.MouseEvent('pointermove', { clientX: 340 }));
  win.document.dispatchEvent(new win.MouseEvent('pointerup', { clientX: 340 }));
  assert.equal(host.style.getPropertyValue('--rc-many'), '270px');
  for (const k of ['who', 'what', 'when']) assert.equal(host.style.getPropertyValue(`--rc-${k}`), '', `${k} untouched`);
  h.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }));
  assert.equal(host.style.getPropertyValue('--rc-many'), '260px');
  h.dispatchEvent(new win.MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  assert.equal(host.style.getPropertyValue('--rc-many'), '');
  assert.equal(api.isCustom(), false);
});

test('the notes (nothing to remove...) are rows inside the table; the refresh box keeps its id in a fixed-width column', () => {
  const p = panel();
  const table = p.querySelector('[data-scope-manifest]');
  const note = p.querySelector('[data-scope-leave-out="eu_sales"]');
  assert.ok(table.contains(note), 'the note sits inside the table, not floating beneath it');
  assert.ok(table.querySelector('[data-scope-manifest-notes]').contains(note));
  const row = p.querySelector('[data-scope-commit-row]');
  assert.match(row.getAttribute('style'), /width:260px/);
  assert.ok(row.querySelector('input[data-scope-refresh-now]'), 'checkbox behaviour and id kept');
  assert.ok(!table.contains(row), 'the checkbox is not inside the table columns');
  const when = p.querySelector('[data-scope-manifest-row="publishes"] [role=cell]:last-child');
  assert.ok(!when.contains(row));
});

test('details sit on their own line inside the cell; reset widths is its own line UNDER the heading, starting disabled', () => {
  const p = panel();
  for (const d of p.querySelectorAll('[data-scope-details]')) assert.ok(d.classList.contains('block'));
  assert.equal(p.querySelector('[data-rc-reset-commit]').disabled, true);
  assert.equal(p.querySelector('[data-rc-reset-commit]').textContent, 'reset widths');
  const head = [...p.querySelectorAll('div')].find((d) => d.textContent === 'What this commit does');
  const resetLine = p.querySelector('[data-rc-reset-commit]').parentElement;
  assert.ok(!head.contains(p.querySelector('[data-rc-reset-commit]')), 'not beside the heading');
  assert.equal(head.nextElementSibling, resetLine, 'directly under it, on its own line');
});

test('no cell breaks a word mid-word, each column keeps its longest word whole, and the label and containers wrap and shrink', () => {
  const p = panel();
  for (const c of p.querySelectorAll('[data-scope-manifest] [role=cell], [data-scope-manifest] [role=columnheader]')) {
    if (c.closest('[data-scope-manifest-notes]')) continue;
    assert.match(c.getAttribute('style'), /overflow-wrap:normal;word-break:normal;hyphens:none/);
  }
  assert.ok(MANIFEST_COLS.columns.every((c) => c.min >= 100), 'a minimum that keeps the longest word whole');
  const label = p.querySelector('[data-scope-refresh-now]').closest('label');
  for (const k of ['min-w-0', 'max-w-full', 'break-words']) assert.ok(label.classList.contains(k), k);
  const panelEl = p.querySelector('[data-scope-commit-panel]');
  assert.ok(panelEl.classList.contains('min-w-0') && panelEl.classList.contains('max-w-full'), 'the panel can shrink inside the page column');
  assert.ok(p.querySelector('[data-scope-manifest-host]').className.includes('max-w-full'));
});
