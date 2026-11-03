/** A schema's open/closed state is exactly what the person last set: the triangle before
 *  'coco_sus · 30 tables' opens and closes it, also after a write redraws the pane, while a read
 *  is still in flight, and whatever the server says about name conflicts (an older server still
 *  sends them; before 2026-10-06 a conflict forced the trees open and made the toggle dead). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, wait, schema, table, person } from './scope-test-kit.mjs';

const names = (n, p) => Array.from({ length: n }, (_, i) => `t${i}`).map((t) => table(p, t));
function cocoView({ legacyConflict = false } = {}) {
  const sus = schema('coco_sus', names(30, 'coco_sus')); const ods = schema('coco_ods', names(23, 'coco_ods'));
  for (const s of [sus, ods]) {
    s.explicit = person('catalogue'); s.effective = 'catalogue'; s.effective_from = 'self'; s.state = 'chosen';
    s.tables.forEach((t) => { t.effective = 'catalogue'; t.effective_from = 'schema'; t.inherited = 'catalogue'; });
  }
  const v = baseView({ schemas: [sus, ods] });
  if (legacyConflict) {
    const c = { name: 't0', text: 't0 is chosen differently in coco_sus and coco_ods', catalogue_in: ['coco_sus'], leave_out_in: ['coco_ods'] };
    sus.tables.concat(ods.tables).forEach((t) => { t.conflict = c; });
    v.conflicts = { count: 23, names: ['t0'], pairs: [] };
  }
  return v;
}
const tables = (d, s) => d.querySelectorAll(`[data-scope-row^="table:${s}."]`).length;
const toggle = (d, s) => d.querySelector(`[data-scope-toggle="${s}"]`);

test('the coco_sus triangle opens and closes its 30 tables, again and again', async () => {
  const { document } = await setUp(cocoView());
  assert.equal(tables(document, 'coco_sus'), 0);
  for (let i = 0; i < 3; i++) {
    toggle(document, 'coco_sus').click();
    assert.equal(tables(document, 'coco_sus'), 30, `open #${i}`);
    assert.equal(toggle(document, 'coco_sus').getAttribute('aria-expanded'), 'true');
    toggle(document, 'coco_sus').click();
    assert.equal(tables(document, 'coco_sus'), 0, `closed #${i}`);
  }
});

test('the open state survives a write that redraws the pane, and the triangle still works after it', async () => {
  const { document } = await setUp(cocoView());
  toggle(document, 'coco_sus').click();
  row(document, 'schema:coco_ods').querySelector('[data-scope-act="set"][data-scope-choice="leave_out"]').click();
  await wait();
  assert.equal(tables(document, 'coco_sus'), 30, 'still open after the redraw');
  assert.equal(tables(document, 'coco_ods'), 0, 'the other schema stays closed');
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 0, 'and the click after the redraw closes it');
  row(document, 'schema:coco_ods').querySelector('[data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  assert.equal(tables(document, 'coco_sus'), 0, 'closed stays closed after a second redraw');
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 30);
});

test('a read still in flight does not eat the click', async () => {
  const { document } = await setUp(cocoView());
  const real = globalThis.fetch;
  let release;
  const gate = new Promise((r) => { release = r; });
  globalThis.fetch = async (url, opts) => { if (String(url).endsWith('/commit-preview')) await gate; return real(url, opts); };
  row(document, 'schema:coco_ods').querySelector('[data-scope-act="set"][data-scope-choice="leave_out"]').click();
  await wait(60);   // the write is done, the scope re-read is back, the preview is pending
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 30);
  release();
  await wait(100);
  assert.equal(tables(document, 'coco_sus'), 30, 'the preview landing does not close it');
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 0);
});

test('an older server that still sends name conflicts neither forces a tree open nor kills the triangle', async () => {
  const { document } = await setUp(cocoView({ legacyConflict: true }));
  assert.equal(tables(document, 'coco_sus'), 0, 'not forced open');
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 30);
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 0, 'the click closes it even though the conflict data is still there');
});

test('open state is never derived: a redraw after the conflict data goes away leaves a closed schema closed', async () => {
  const { document, server } = await setUp(cocoView({ legacyConflict: true }));
  server.view.schemas.forEach((s) => s.tables.forEach((t) => { t.conflict = null; }));
  delete server.view.conflicts;
  row(document, 'schema:coco_ods').querySelector('[data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  assert.equal(tables(document, 'coco_sus'), 0);
  assert.equal(tables(document, 'coco_ods'), 0);
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 30, 'the FIRST click after the redraw opens it');
});

test('a fresh pane (a reload) starts closed and its triangle works', async () => {
  const { document } = await setUp(cocoView());
  assert.equal(tables(document, 'coco_sus'), 0);
  toggle(document, 'coco_sus').click();
  assert.equal(tables(document, 'coco_sus'), 30);
});
