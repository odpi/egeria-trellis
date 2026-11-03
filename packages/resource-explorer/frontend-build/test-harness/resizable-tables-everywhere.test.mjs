/** Every data table in /next is resizable, wherever and whenever it is drawn (owner, 2026-10-20 and 2026-10-30:
 *  "resizable columns should be pervasive for tables"). Three things are pinned:
 *   1. the mechanism catches late renders: a table or grid drawn after the watcher started, in a panel appended to
 *      the body, or redrawn by a screen that forgot to call attachGrids;
 *   2. div grids (`role="table"`) resize like `<table>`s;
 *   3. an inventory of every table/grid site in the source: each one either has a header row (so the watcher
 *      resizes it) or is named in EXCLUDED with the reason it is not a data table. A new headerless table fails
 *      this test until someone names it. */
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const NEXT = new URL('../../resource_explorer/web/static/next/', import.meta.url).pathname;

function setUp() {
  const env = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.getComputedStyle = env.window.getComputedStyle.bind(env.window);
  return env;
}
const drag = (window, document, handle, from, to) => {
  handle.dispatchEvent(new window.MouseEvent('pointerdown', { clientX: from, bubbles: true, cancelable: true }));
  document.dispatchEvent(new window.MouseEvent('pointermove', { clientX: to }));
  document.dispatchEvent(new window.MouseEvent('pointerup', { clientX: to }));
};

test('a table drawn into a panel appended to the body after the watcher started is resizable', async () => {
  const { document } = setUp();
  const { watchTables } = await import('/static/next/colresize.js');
  watchTables(document.body);
  const panel = document.createElement('div');                 // the Admin panel, drill-downs and bands are appended like this
  document.body.appendChild(panel);
  await tick();
  panel.innerHTML = '<div><table><thead><tr><th>Name</th><th>Kind</th><th>Size</th></tr></thead><tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody></table></div>';
  await tick();
  const t = panel.querySelector('table');
  assert.ok(t.hasAttribute('data-rc-auto'));
  assert.equal(t.querySelectorAll('thead th [data-rc-handle]').length, 3, 'one handle per header');
  // a redraw replaces the table; the new one is enhanced too
  panel.innerHTML = panel.innerHTML;
  await tick();
  assert.equal(panel.querySelectorAll('table [data-rc-handle]').length, 3);
  assert.equal(panel.querySelector('table').style.width, '', 'no widths are set before a drag');
});

test('a spec-driven grid (gridHtml) is wired when drawn late, with no attachGrids call (the quality-checks gap)', async () => {
  const { document, window } = setUp();
  const { watchTables } = await import('/static/next/colresize.js');
  const { gridHtml, RANKED_COLS } = await import('/static/next/stages/report-tables.js');
  watchTables(document.body);
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  slot.innerHTML = gridHtml(RANKED_COLS, 'Ranked tables', [{ cells: Object.fromEntries(RANKED_COLS.columns.map((c) => [c.key, 'x'])) }]);
  await tick();
  const host = slot.querySelector('[data-grid-host]');
  assert.equal(host.dataset.rcId, RANKED_COLS.id, 'the grid carries its data-rc-id');
  const handles = host.querySelectorAll('[data-rc-handle]');
  assert.equal(handles.length, RANKED_COLS.columns.length, 'one rc-handle per header');
  assert.equal(host.style.getPropertyValue(`--rc-${RANKED_COLS.columns[0].key}`), '', 'no widths before a drag');
  drag(window, document, handles[1], 100, 140);
  assert.equal(host.style.getPropertyValue(`--rc-${RANKED_COLS.columns[1].key}`), `${RANKED_COLS.columns[1].def + 40}px`);
});

test('a div grid with columnheaders (dependencies, the repo manifest) resizes like a table', async () => {
  const { document, window } = setUp();
  const { watchTables } = await import('/static/next/colresize.js');
  const { dependencyTableHtml } = await import('/static/next/stages/dependencies.js');
  watchTables(document.body);
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  slot.innerHTML = dependencyTableHtml({ rows: [{ kind: 'runtime', source: 'a', target: 'b', target_ref: 'b', status: 'derived' }], counts: {} }, {}, {});
  await tick();
  const grid = slot.querySelector('[role="table"]');
  const heads = grid.querySelectorAll('[role="columnheader"]');
  assert.ok(heads.length >= 2);
  assert.equal(grid.querySelectorAll('[role="columnheader"] [data-rc-handle]').length, heads.length, 'one handle per header');
  assert.equal(grid.style.width, '', 'no widths before a drag');
  const before = [...grid.querySelectorAll('[role="row"]')].map((r) => r.children[1].style.width);
  assert.ok(before.every((w) => w === '' || /rem|px|%/.test(w)));
  drag(window, document, heads[1].querySelector('[data-rc-handle]'), 100, 160);
  const rows = [...grid.querySelectorAll('[role="row"]')];
  const w = rows[0].children[1].style.width;
  assert.ok(/px$/.test(w));
  for (const r of rows) assert.equal(r.children[1].style.width, w, 'every row follows the header');
});

test('a table with no header row and under three columns is not a data table and gets no handles', async () => {
  const { document } = setUp();
  const { enhanceTables } = await import('/static/next/colresize.js');
  const host = document.createElement('div');
  host.innerHTML = '<table><tbody><tr><td>key</td><td>value</td></tr></tbody></table>';
  document.body.appendChild(host);
  assert.equal(enhanceTables(host), 0);
});

/* ── the inventory ─────────────────────────────────────────────────────────────────────────────── */

/** Table sites with no header row. Each is a key/value list or a one-line-per-item list, not a grid of columns. */
const EXCLUDED = {
  'admin/logs.js': 'log lines: timestamp + message rows, no columns to size',
  'chat.js': 'a two-column key/value list inside a chat answer',
  'db-server-discovery.js': 'candidate detail list: key/value rows',
  'stages/activity.js': 'one-line-per-event list',
  'stages/automate.js': 'a list of rows, each with its own controls',
  'stages/native-report.js': 'a step list: scope + sentence',
  'app.js': 'key/value fact lists and two-column series lists',
  'stages/understanding-stats.js': 'already carries data-rc-id and resizes when it has three or more columns',
};

function walk(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) { if (n !== 'fonts') walk(p, out); } else if (n.endsWith('.js')) out.push(p);
  }
  return out;
}

test('every table site in the source has a header row or is named as excluded', () => {
  const unnamed = [];
  const seen = {};
  for (const f of walk(NEXT)) {
    const rel = relative(NEXT, f);
    if (rel === 'colresize.js') continue;
    const src = readFileSync(f, 'utf-8');
    for (const m of src.matchAll(/`<table\b|<table\b[^>]*>|role="table"/g)) {
      const seg = src.slice(m.index, m.index + 6000);
      const isGrid = m[0].includes('role="table"');
      // a grid's header row is built just before its role="table" element; a table's follows its tag
      const end = isGrid ? -1 : seg.indexOf('</table>');
      const body = isGrid ? src.slice(Math.max(0, m.index - 2500), m.index + 2500) : (end > 0 ? seg.slice(0, end) : seg);
      if (/<thead|<th[\s>]|role="columnheader"|columnheader|handleHtml|gridHtml/.test(body)) continue;
      if (/\bdata-no-resize\b/.test(body)) continue;
      if (/^\s*[`'"]?<table>`|^<table>`/.test(seg.slice(0, 12))) continue;   // prose mentioning `<table>`
      if (!(rel in EXCLUDED)) unnamed.push(`${rel}: ${seg.slice(0, 80).replace(/\s+/g, ' ')}`);
      seen[rel] = true;
    }
  }
  assert.deepEqual(unnamed, [], 'a table with no header row must be named in EXCLUDED with its reason');
  for (const k of Object.keys(EXCLUDED)) assert.ok(seen[k] || k === 'stages/understanding-stats.js', `${k} is in EXCLUDED but has no headerless table any more`);
});
