/** Resizable columns (next/colresize.js): drag, minimum, double-click reset, keyboard,
 *  persistence with storage present and with storage throwing, and never wider than the host.
 *  jsdom does no layout, so the host's visible width is stated with clientWidth. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';
import { ensureLoaderRegistered } from './dom-harness.mjs';
import { baseView, setUp, scopeEl } from './scope-test-kit.mjs';

ensureLoaderRegistered();
const { attachColumnResize, handleHtml, cellStyle } = await import('/static/next/colresize.js');

const SPEC = {
  id: 't1', chrome: 20,
  columns: [{ key: 'a', def: 100, min: 40 }, { key: 'n', def: 100, min: 40, align: 'left' }, { key: 'b', def: 100, min: 40 }],
};

function make({ clientWidth = 0, storage = 'ok' } = {}) {
  const dom = new JSDOM(`<div id="host">${handleHtml(SPEC, 'a', 'A')}${handleHtml(SPEC, 'b', 'B')}</div>`, { url: 'http://localhost/' });
  const { window } = dom;
  if (storage === 'throws') {
    Object.defineProperty(window, 'localStorage', { get() { throw new window.DOMException('denied', 'SecurityError'); } });
  }
  const host = window.document.getElementById('host');
  if (clientWidth) Object.defineProperty(host, 'clientWidth', { value: clientWidth });
  const api = attachColumnResize(host, SPEC);
  const handle = (k) => host.querySelector(`[data-rc-handle="${k}"]`);
  const px = (k) => host.style.getPropertyValue(`--rc-${k}`);
  const ptr = (type, x, target = window.document) => target.dispatchEvent(new window.MouseEvent(type, { clientX: x, bubbles: true, cancelable: true }));
  const drag = (k, from, to) => { ptr('pointerdown', from, handle(k)); ptr('pointermove', to); ptr('pointerup', to); };
  const key = (k, shiftKey = false) => handle('a').dispatchEvent(new window.KeyboardEvent('keydown', { key: k, shiftKey, bubbles: true, cancelable: true }));
  return { window, host, api, handle, px, drag, key, ptr };
}

test('dragging a handle changes that column\'s width and not the other\'s', () => {
  const t = make();
  t.drag('a', 100, 160);
  assert.equal(t.px('a'), '160px');
  assert.equal(t.px('b'), '', 'the other column keeps its default');
  assert.equal(t.api.isCustom(), true);
});

test('a column never goes below its minimum width', () => {
  const t = make();
  t.drag('a', 100, -500);
  assert.equal(t.px('a'), '40px');
});

test('double-click on a handle resets that column only', () => {
  const t = make();
  t.drag('a', 100, 180); t.drag('b', 100, 150);
  t.handle('a').dispatchEvent(new t.window.MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  assert.equal(t.px('a'), '', 'a is back to its default');
  assert.equal(t.px('b'), '150px', 'b is untouched');
});

test('a focused handle moves with Left/Right, Shift for larger steps', () => {
  const t = make();
  t.key('ArrowRight'); assert.equal(t.px('a'), '110px');
  t.key('ArrowRight', true); assert.equal(t.px('a'), '160px');
  t.key('ArrowLeft'); assert.equal(t.px('a'), '150px');
  t.key('ArrowLeft', true); t.key('ArrowLeft', true); t.key('ArrowLeft', true); t.key('ArrowLeft', true);
  assert.equal(t.px('a'), '40px', 'keyboard stops at the minimum too');
  assert.equal(t.handle('a').getAttribute('tabindex'), '0', 'reachable by keyboard');
  assert.equal(t.handle('a').getAttribute('aria-valuenow'), '40');
});

test('widths persist per table id, and a fresh table reads them back', () => {
  const t = make();
  t.drag('a', 100, 170);
  assert.deepEqual(JSON.parse(t.window.localStorage.getItem('re.colwidths.t1')), { a: 170 });
  // a second host in the same window (a re-drawn page) starts from the stored width
  const host2 = t.window.document.createElement('div');
  t.window.document.body.append(host2);
  attachColumnResize(host2, SPEC);
  assert.equal(host2.style.getPropertyValue('--rc-a'), '170px');
  t.api.reset();
  assert.equal(t.window.localStorage.getItem('re.colwidths.t1'), null, 'reset forgets them');
  assert.equal(t.api.isCustom(), false);
});

test('with storage throwing, the table still resizes and nothing throws', () => {
  const t = make({ storage: 'throws' });
  t.drag('a', 100, 150); t.key('ArrowRight'); t.api.reset(); t.drag('b', 100, 130);
  assert.equal(t.px('b'), '130px');
  assert.equal(t.px('a'), '');
});

test('growing a column past the host scrolls the host: no other column is squeezed or moved', () => {
  const t = make({ clientWidth: 400 });
  t.drag('b', 100, 900);
  assert.equal(t.px('b'), '900px', 'the dragged column takes the width asked for');
  assert.equal(t.px('a'), '', 'a keeps its exact width');
  assert.equal(t.px('n'), '', 'the name column keeps its exact width');
  assert.equal(t.host.style.getPropertyValue('--rc-total'), '1120px', 'the table floor grows so the host scrolls (100+100+900+20)');
});

test('the drag delta is applied to the width at drag start, not accumulated per event', () => {
  const t = make();
  t.ptr('pointerdown', 100, t.handle('a'));
  for (const x of [110, 120, 130, 140]) t.ptr('pointermove', x);
  assert.equal(t.px('a'), '140px', 'start 100 + (140-100), not 100 + 10+20+30+40');
  t.ptr('pointerup', 140);
});

test('every column is fixed (flex 0 0 auto), only the dragged one changes, text centred except where align is left', () => {
  assert.equal(cellStyle(SPEC, 'a'), 'width:var(--rc-a,100px);flex:0 0 auto;min-width:0;overflow-wrap:normal;word-break:normal;hyphens:none;text-align:center');
  assert.equal(cellStyle(SPEC, 'n'), 'width:var(--rc-n,100px);flex:0 0 auto;min-width:0;overflow-wrap:normal;word-break:normal;hyphens:none;text-align:left');
  assert.equal(cellStyle(SPEC, 'n', 14), 'width:calc(var(--rc-n,100px) - 14px);flex:0 0 auto;min-width:0;overflow-wrap:normal;word-break:normal;hyphens:none;text-align:left');
});

test('Curate scope table: handles on the resizable headers, widths move every row, reset is live only when changed', async () => {
  const { document, window } = await setUp(baseView());
  const el = scopeEl(document);
  const host = el.querySelector('[data-scope-tree]');
  const handles = [...host.querySelectorAll('[data-scope-tree-head] [data-rc-handle]')].map((h) => h.dataset.rcHandle);
  assert.deepEqual(handles, ['choice', 'name', 'rows', 'size', 'act', 'cls', 'egeria']);
  const reset = el.querySelector('[data-rc-reset]');
  assert.equal(reset.disabled, true, 'nothing changed yet');
  const h = host.querySelector('[data-rc-handle="egeria"]');
  h.dispatchEvent(new window.MouseEvent('pointerdown', { clientX: 500, bubbles: true, cancelable: true }));
  document.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 540 }));
  document.dispatchEvent(new window.MouseEvent('pointerup', { clientX: 540 }));
  assert.equal(host.style.getPropertyValue('--rc-egeria'), '280px');
  const cell = host.querySelector('[data-scope-row="schema:sales"] [data-scope-state-cell]');
  assert.match(cell.getAttribute('style'), /width:var\(--rc-egeria,240px\)/);
  assert.equal(reset.disabled, false, 'the reset control is live');
  host.querySelector('[data-scope-toggle="sales"]').click();   // a redraw keeps the width
  assert.equal(host.style.getPropertyValue('--rc-egeria'), '280px');
  assert.equal(host.querySelectorAll('[data-rc-handle]').length, 7);
  reset.click();
  assert.equal(host.style.getPropertyValue('--rc-egeria'), '');
  assert.equal(reset.disabled, true);
});

test('Curate scope table: no cell flexes, text is centred except Schema / table, table rows give the indent back', async () => {
  const { document } = await setUp(baseView());
  const host = scopeEl(document).querySelector('[data-scope-tree]');
  host.querySelector('[data-scope-toggle="sales"]').click();
  const rows = [host.querySelector('[data-scope-tree-head]'), host.querySelector('[data-scope-row="schema:sales"]'), host.querySelector('[data-scope-row="table:sales.orders"]')];
  for (const r of rows) {
    const cells = [...r.children];
    assert.equal(cells.length, 8);
    for (const c of cells) {
      assert.ok(!c.classList.contains('flex-1'), 'no flexible cell: one column absorbing space moves the others');
      assert.match(c.getAttribute('style'), /flex:0 0 auto/);
    }
    const aligns = cells.map((c) => c.getAttribute('style').match(/text-align:(\w+)/)[1]);
    assert.deepEqual(aligns, ['center', 'center', 'left', 'center', 'center', 'center', 'center', 'center']);
  }
  assert.match(rows[1].querySelector('[data-scope-name-cell]').getAttribute('style'), /^width:var\(--rc-name,280px\)/);
  assert.match(rows[2].querySelector('[data-scope-name-cell]').getAttribute('style'), /^width:calc\(var\(--rc-name,280px\) - 13\.8px\)/);
});

test('an identifier column may break an over-long word; every other column breaks at spaces only', () => {
  const spec = { id: 'w', chrome: 0, columns: [{ key: 'id', def: 100, min: 40, breakLong: true }, { key: 'o', def: 100, min: 40 }] };
  assert.match(cellStyle(spec, 'id'), /overflow-wrap:break-word/);
  assert.match(cellStyle(spec, 'o'), /overflow-wrap:normal;word-break:normal;hyphens:none/);
});
