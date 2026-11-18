/** EA Dashboards, B7a: every tile kind draws, a lower bound never shows as a bare number, tiles run by
 *  themselves (at most three at a time), and nothing from Egeria runs as script.
 *
 *  Runs the REAL vendored marked + DOMPurify, the REAL render.js and the REAL static/dashboards_view.js in
 *  a jsdom window. The tiles drawn are the server's own: tests/unit/fixtures/dashboards/tiles_for_js.json
 *  is made by advisor.egeria_dashboards._tile() from real-shape results (live laz captures and pyegeria
 *  6.2.34's own output), and a Python test fails if it drifts from what the server makes. Only fetch,
 *  Auth and vegaEmbed are stubbed.
 *
 *  Run:  node --test advisor/web/static/test/dashboards_view.test.mjs   (jsdom: see render.test.mjs)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');
const TILES = JSON.parse(fs.readFileSync(path.resolve(STATIC, '../../../tests/unit/fixtures/dashboards/tiles_for_js.json'), 'utf8'));

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';

test('the harness can run (jsdom found)', { skip: !REQUIRED && SKIP }, () => {
  assert.ok(jsdom, `EA_RENDER_HARNESS_REQUIRED=1 but ${SKIP}`);
});

const read = (rel) => fs.readFileSync(path.join(STATIC, rel), 'utf8');
// trellis-ui's vendored copy of the Portal's structured-TABLE renderer (T1), loaded as index.html loads it
const EGERIA_TABLE = fs.readFileSync(path.join(REPO, 'packages/trellis-ui/trellis_ui/static/egeria-table.js'), 'utf8');
const GE = '≥';

function makeWindow(opts = {}) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><div id="messages"></div>' +
    '<div id="dashboard-status"></div><div id="dashboard-list"></div></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(read('vendor/marked.min.js'));
  w.eval(read('vendor/purify.min.js'));
  w.eval(read('render.js'));
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.embedded = [];
  w.vegaEmbed = (el, spec, opts) => { w.embedded.push({ el, spec, opts }); return Promise.resolve({}); };
  if (opts.table !== false) w.eval(EGERIA_TABLE);
  w.eval(read('dashboards_view.js'));
  return w;
}

function draw(w, t, p = {}) {
  const out = w.document.createElement('div');
  w.document.body.appendChild(out);
  w.EADashboards.renderTileBody(out, t, p);
  return out;
}

/** All text a viewer can read in `node`, SVG titles included. */
const allText = (node) => node.textContent;

test('kpi: the number, as the server gave it', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.kpi_count);
  assert.equal(out.querySelector('.dash-kpi').textContent, '68739');
});

test('kpis: an unmeasured count reads "not measured", with why', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.kpis_unmeasured);
  const cells = [...out.querySelectorAll('.dash-kpis > div')].map(c => c.textContent);
  assert.deepEqual(cells, ['272contextualisedCount', 'not measuredassetTotal', 'not measuredcontextualisedPct']);
  assert.match(out.querySelector('.dash-unmeasured').textContent, /at least 500/);
});

test('chart: pyegeria\'s Vega-Lite spec is embedded as given; a spec that loads a URL is refused', { skip: SKIP }, () => {
  const w = makeWindow();
  draw(w, TILES.chart_pie);
  assert.equal(w.embedded.length, 1);
  assert.deepEqual(w.embedded[0].spec, TILES.chart_pie.spec);
  assert.equal(w.embedded[0].opts.actions, false);
  const hostile = { kind: 'chart', spec: { ...TILES.chart_pie.spec, data: { url: 'https://evil.example/x.json' } } };
  const out = draw(w, hostile);
  assert.equal(w.embedded.length, 1, 'vegaEmbed must not be called');
  assert.match(out.textContent, /asks to load data from a URL/);
});

test('trend: exact points, headline and sparkline', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.trend_exact);
  assert.equal(out.querySelector('.dash-kpi').textContent, '1065');
  assert.equal(out.querySelectorAll('circle.dash-point').length, 3);
  assert.equal(out.querySelectorAll('circle.dash-bound').length, 0);
  assert.ok(out.querySelector('svg.dash-spark path'));
});

test('trend: a capped point is never rendered without its marker (W2)', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.trend_capped);
  const bounds = out.querySelectorAll('circle.dash-bound');
  assert.equal(bounds.length, 2, 'each lower bound is drawn as a bound');
  for (const b of bounds) {
    assert.equal(b.getAttribute('fill'), 'none');
    assert.match(b.querySelector('title').textContent, new RegExp(`: ${GE}500$`));
  }
  // Every place a point's NUMBER is shown: the headline, the points list, the marker titles. (The
  // not-measured note is pyegeria's sentence, "...so there are at least 500": words, not a value.)
  const shown = [out.querySelector('.dash-kpi'), out.querySelector('.dash-trend-points'),
    ...out.querySelectorAll('svg title')].map(allText).join(' | ');
  assert.equal((shown.match(new RegExp(`${GE}500`, 'g')) || []).length, 4, shown);   // 2 in the list, 2 titles
  assert.doesNotMatch(shown.replace(new RegExp(`${GE}500`, 'g'), ''), /(^|\D)500(\D|$)/, `a bare 500: ${shown}`);
  assert.match(allText(out), /2 of 3 points are lower bounds/);
  assert.match(out.querySelector('.dash-unmeasured').textContent, /at least 500/);
});

test('trend: an unmeasured point is a gap that says "not measured"', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.trend_not_measured);
  assert.match(out.querySelector('.dash-trend-points').textContent, /not measured · .* not measured · .* 1065/);
  assert.equal(out.querySelectorAll('circle').length, 0, 'one measured point: no line to draw');
});

test('absent and empty: the sentence is the answer', { skip: SKIP }, () => {
  const w = makeWindow();
  assert.match(draw(w, TILES.absent_trend).querySelector('.dash-absent').textContent, /not measured/);
  assert.equal(draw(w, TILES.empty).querySelector('.dash-empty').textContent, "'Collections' ran and found nothing.");
});

test('saved query (Route B): cached with its as-of time, live, and a lower bound', { skip: SKIP }, () => {
  const w = makeWindow();
  const cached = draw(w, TILES.saved_cached);
  assert.equal(cached.querySelector('.dash-kpi').textContent, '37');
  assert.equal(cached.querySelector('.dash-asof').textContent, 'cached · as of 2026-11-10T18:02:11+00:00');
  assert.match(draw(w, TILES.saved_live).querySelector('.dash-asof').textContent, /^live · as of /);
  const capped = draw(w, TILES.saved_capped);
  assert.equal(capped.querySelector('.dash-kpi').textContent, `${GE}30`);
  assert.match(capped.textContent, /a lower bound/);
});

test('markdown: tables and mermaid from Egeria draw, and hostile text is inert', { skip: SKIP }, () => {
  const w = makeWindow();
  assert.ok(draw(w, TILES.markdown_records).querySelector('table'));
  assert.ok(draw(w, TILES.markdown_mermaid).querySelector('div.mermaid'));
  const out = draw(w, { kind: 'markdown', markdown: '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>[x](javascript:window.__pwned=3)' });
  assert.equal(out.querySelectorAll('script, img[onerror]').length, 0);
  assert.equal(w.__pwned, undefined);
});

// ── a sheet: tiles run by themselves ─────────────────────────────────────────

function sheetOf(n) {
  const placements = [];
  for (let i = 1; i <= n; i++) {
    placements.push({ position: i, kind: 'report', title: `T${i}`, span: '1', emphasis: 'kpi', spec: 'S', source: 'builtin',
      runnable: true, params: { searchString: '*' }, analytic: false, report: { output_format: 'DICT' } });
  }
  placements.push({ position: n + 1, kind: 'restricted', title: 'Hidden', reason: 'not visible to you' });
  placements.push({ position: n + 2, kind: 'text', content: 'hello' });
  placements.push({ position: n + 3, kind: 'report', title: 'No', runnable: false, refusal: 'cannot run', spec: 'X' });
  return { name: 'sheet <x>', heading: 'Sheet', viewer: 'peterprofile', breaks: [], placements };
}

test('a sheet runs its runnable tiles by itself, at most three at a time, each once', { skip: SKIP }, async () => {
  const w = makeWindow();
  let inFlight = 0, most = 0;
  const calls = [];
  w.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    inFlight++; most = Math.max(most, inFlight);
    await new Promise(r => setTimeout(r, 5));
    inFlight--;
    return { ok: true, status: 200, json: async () => ({ state: 'ok', tile: TILES.kpi_count, cache: { state: 'hit', age_s: 42 } }) };
  };
  const wrap = w.EADashboards.renderDashboard(sheetOf(7));
  await wrap._ready;
  assert.equal(calls.length, 7);
  assert.ok(most <= 3 && most >= 2, `in flight at once: ${most}`);
  assert.deepEqual(calls.map(c => c.url).sort(), [1, 2, 3, 4, 5, 6, 7].map(i => `/api/dashboards/sheet%20%3Cx%3E/placements/${i}/run`).sort());
  assert.ok(calls.every(c => c.body.refresh === false && c.body.params === null));
  const tiles = wrap.querySelectorAll('.dash-tile');
  assert.equal(tiles[0].querySelector('.dash-kpi').textContent, '68739');
  assert.equal(tiles[0].querySelector('.dash-age').textContent, 'answer 42s old');
  assert.match(tiles[7].textContent, /restricted: not visible to you/);
  assert.match(tiles[9].textContent, /cannot run/);
  assert.equal(tiles[9].querySelector('.dash-refresh'), null);
});

test('↻ runs one tile now; a stale answer says it is refreshing; a refusal is shown in words', { skip: SKIP }, async () => {
  const w = makeWindow();
  const bodies = [];
  let answer = { state: 'ok', tile: TILES.kpi_count, cache: { state: 'stale', age_s: 125 } };
  w.fetch = async (url, init) => { bodies.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => answer }; };
  const wrap = w.EADashboards.renderDashboard(sheetOf(1));
  await wrap._ready;
  const tile = wrap.querySelector('.dash-tile');
  assert.equal(tile.querySelector('.dash-age').textContent, 'answer 2 min old · refreshing');
  answer = { state: 'refused', message: "'collectionGuid' is not a parameter of this placement" };
  await tile._run({ refresh: true });
  assert.equal(bodies[1].refresh, true);
  assert.match(tile.querySelector('.dash-out').textContent, /not a parameter of this placement/);
});

test('⚙ sends only what the viewer changed, for this view', { skip: SKIP }, async () => {
  const w = makeWindow();
  const bodies = [];
  w.fetch = async (url, init) => { bodies.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ({ state: 'ok', tile: TILES.kpi_count, cache: {} }) }; };
  const wrap = w.EADashboards.renderDashboard(sheetOf(1));
  await wrap._ready;
  const tile = wrap.querySelector('.dash-tile');
  tile.querySelector('.dash-adjust').click();
  const inp = tile.querySelector('.dash-adj-param[data-key="searchString"]');
  assert.equal(inp.value, '*');
  inp.value = 'Sales';
  tile.querySelector('.dash-adjust-panel button').click();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(bodies[1].params, { searchString: 'Sales' });
  assert.equal(bodies[1].output_format, null);
});

test('↗ drill-down runs the detail spec and shows its answer under the tile', { skip: SKIP }, async () => {
  const w = makeWindow();
  const urls = [];
  w.fetch = async (url) => {
    urls.push(url);
    const tile = url.endsWith('/detail') ? TILES.markdown_records : TILES.kpi_count;
    return { ok: true, status: 200, json: async () => ({ state: 'ok', tile, cache: {} }) };
  };
  const sheet = sheetOf(1);
  sheet.placements[0].detail_spec = 'Collections';
  const wrap = w.EADashboards.renderDashboard(sheet);
  await wrap._ready;
  const tile = wrap.querySelector('.dash-tile');
  tile.querySelector('.dash-drill').click();
  await new Promise(r => setTimeout(r, 10));
  assert.ok(urls.some(u => u.endsWith('/placements/1/detail')));
  assert.ok(tile.querySelector('.dash-detail table'));
  assert.match(tile.querySelector('.dash-detail').textContent, /Drill-down: Collections/);
});

test('a nested sheet opens in place; no inline handlers anywhere in the module', { skip: SKIP }, async () => {
  const w = makeWindow();
  const opened = [];
  w.fetch = async (url) => { opened.push(url); return { ok: true, status: 200, json: async () => ({ state: 'ok', sheet: { ...sheetOf(0), name: 'inner' } }) }; };
  const sheet = { name: 'outer', heading: 'Outer', viewer: 'v', breaks: [], placements: [
    { position: 1, kind: 'sheet', title: 'Inner', sheet_ref: 'inner', note: 'nested sheet: Inner (1 placements)' }] };
  const wrap = w.EADashboards.renderDashboard(sheet);
  wrap.querySelector('.dash-nested').click();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(opened, ['/api/dashboards/inner']);
  assert.doesNotMatch(read('dashboards_view.js'), /\son[a-z]+\s*=\s*["']/);
});

// ── the list: a configured folder's own reason (Architect F3) ─────────────────

const LISTING = JSON.parse(fs.readFileSync(path.resolve(STATIC,
  '../../../tests/unit/fixtures/dashboards/listing_folder_not_found_2026-11-11.json'), 'utf8'));

test('a folder that is not found says its reason, not "no sheets"', { skip: SKIP }, async () => {
  const w = makeWindow();
  const message = `No Dashboard Sheets to show for erinoverview: ${LISTING.notes[0]}.`;
  w.fetch = async () => ({ ok: true, status: 200, json: async () => ({ state: 'no_sheets', sheets: [], viewer: 'erinoverview',
    message, notes: LISTING.notes, folders: LISTING.folders }) });
  await w.EADashboards.loadDashboards(false);
  const status = w.document.getElementById('dashboard-status');
  assert.equal(status.textContent, message);
  assert.match(status.textContent, /DashboardFolder::Consumer::EgeriaAdvisor: not found, or not visible to erinoverview/);
});

test('with sheets, the folder notes show under the count', { skip: SKIP }, async () => {
  const w = makeWindow();
  w.fetch = async () => ({ ok: true, status: 200, json: async () => ({ state: 'ok', viewer: 'erinoverview',
    sheets: [{ name: 'a', heading: 'A', description: '', placement_count: 1, breaks: [] }],
    message: LISTING.notes[0], notes: LISTING.notes, folders: LISTING.folders }) });
  await w.EADashboards.loadDashboards(false);
  const status = w.document.getElementById('dashboard-status');
  assert.match(status.textContent, /^1 sheet visible to erinoverview/);
  assert.equal(status.querySelector('.dash-list-notes').textContent, LISTING.notes[0]);
});

test('with sheets and no notes, only the count shows', { skip: SKIP }, async () => {
  const w = makeWindow();
  w.fetch = async () => ({ ok: true, status: 200, json: async () => ({ state: 'ok', viewer: 'erinoverview',
    sheets: [{ name: 'a', heading: 'A', description: '', placement_count: 1, breaks: [] }], message: null, notes: [], folders: [] }) });
  await w.EADashboards.loadDashboards(false);
  const status = w.document.getElementById('dashboard-status');
  assert.equal(status.textContent, '1 sheet visible to erinoverview');
  assert.equal(status.querySelector('.dash-list-notes'), null);
});

// ── large tables scroll inside their tile (the Portal's .tbl-scroll, #833) ─────

function mdTable(nRows, nCols) {
  const head = '| ' + Array.from({ length: nCols }, (_, c) => `Col ${c}`).join(' | ') + ' |';
  const sep = '| ' + Array.from({ length: nCols }, () => '---').join(' | ') + ' |';
  const rows = Array.from({ length: nRows }, (_, r) => '| ' + Array.from({ length: nCols }, (_, c) => `r${r}c${c}`).join(' | ') + ' |');
  return [head, sep, ...rows].join('\n');
}

/** A window with EA's looks loaded, so computed styles are the ones a browser would cascade. */
function styledWindow() {
  const w = makeWindow();
  const style = w.document.createElement('style');
  style.textContent = read('ea_looks.css');
  w.document.head.appendChild(style);
  return w;
}

function tileWith(w, markdown) {
  const tile = w.document.createElement('div');
  tile.className = 'dash-tile';
  w.document.body.appendChild(tile);
  w.EADashboards.renderTileBody(tile, { kind: 'markdown', markdown }, {});
  return tile;
}

test('a large table is wrapped, scrolls both ways inside the tile, and keeps its header in view', { skip: SKIP }, () => {
  const w = styledWindow();
  const tile = tileWith(w, mdTable(120, 7) + '\n\n*+3 more rows; +1 more column: Extra*');
  const wrap = tile.querySelector('.dash-tbl-scroll');
  assert.ok(wrap, 'the table is wrapped');
  const table = wrap.querySelector('table');
  assert.equal(table.parentElement, wrap);
  assert.equal(table.querySelectorAll('tbody tr').length, 120);       // every row is there, to scroll to
  const cs = w.getComputedStyle(wrap);
  assert.equal(cs.overflow, 'auto');
  assert.equal(cs.maxHeight, '420px');
  const th = w.getComputedStyle(table.querySelector('thead th'));
  assert.equal(th.position, 'sticky');
  assert.equal(th.top, '0px');
  assert.match(th.background || th.backgroundColor, /rgb\(var\(--ea-card\)\)|rgb\(/);   // opaque: a solid colour
  assert.equal(w.getComputedStyle(table).borderCollapse, 'separate');  // no strip above the sticky header
  // 5+ columns: a minimum column width, so it scrolls sideways rather than squeezing.
  assert.ok(table.classList.contains('dash-tbl-wide'));
  assert.equal(table.style.getPropertyValue('--tbl-cols'), '7');
  // The counted overflow the server wrote reads as words under the table, not a row in it.
  assert.match(tile.textContent, /\+3 more rows; \+1 more column: Extra/);
  assert.ok(!wrap.textContent.includes('more rows'));
});

test('a small table is laid out as before: wrapped, but not widened', { skip: SKIP }, () => {
  const w = styledWindow();
  const tile = tileWith(w, mdTable(2, 3));
  const table = tile.querySelector('table');
  assert.equal(table.parentElement.className, 'dash-tbl-scroll');
  assert.ok(!table.classList.contains('dash-tbl-wide'));
  assert.equal(table.getAttribute('style'), null);
  assert.deepEqual([...table.querySelectorAll('td')].map(td => td.textContent), ['r0c0', 'r0c1', 'r0c2', 'r1c0', 'r1c1', 'r1c2']);
  assert.equal(tile.querySelectorAll('.dash-tbl-scroll').length, 1);
});

test('every table in a tile is wrapped once; the Firefox scroll-bar rules are scoped to Firefox', { skip: SKIP }, () => {
  const w = styledWindow();
  const tile = tileWith(w, mdTable(1, 2) + '\n\ntext\n\n' + mdTable(1, 6));
  assert.equal(tile.querySelectorAll('.dash-tbl-scroll').length, 2);
  assert.equal(tile.querySelectorAll('.dash-tbl-scroll .dash-tbl-scroll').length, 0);
  const css = read('ea_looks.css');
  const ff = css.indexOf('@supports (-moz-appearance: none)');
  assert.ok(ff > 0, 'Firefox rules are inside a Firefox-only feature query');
  const outside = css.slice(0, ff);
  assert.ok(!/scrollbar-(width|color)\s*:/.test(outside), 'Chrome would drop ::-webkit-scrollbar if these applied to it');
  assert.match(css, /\.dash-tbl-scroll::-webkit-scrollbar\s*\{/);
});

test('a line break the server wrote as <br> in a cell draws as a break in that cell, not a new row', { skip: SKIP }, () => {
  const w = styledWindow();
  const tile = tileWith(w, '| Name | Note |\n| --- | --- |\n| first<br>second | x |');
  const cells = tile.querySelectorAll('tbody td');
  assert.equal(tile.querySelectorAll('tbody tr').length, 1);
  assert.equal(cells.length, 2);
  assert.equal(cells[0].querySelectorAll('br').length, 1);
  assert.equal(cells[0].textContent, 'firstsecond');
});


// ── T1: TABLE tiles draw through the shared renderer ────────────────────────────

test('a TABLE tile is drawn by egeria-table.js, never as a markdown table', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.table_risk);
  const box = out.querySelector('.dash-etable');
  assert.ok(box && box.querySelector('table.et-table'));
  assert.equal(out.querySelectorAll('.msg-body table, .dash-tbl-scroll table').length, 0);
  // the author's columns, all of them (no cap), and one row per result row
  const heads = [...box.querySelectorAll('table.et-table > thead th')].map(th => th.textContent);
  assert.deepEqual(heads, TILES.table_risk.table.columns.map(c => c.name));
  assert.equal(box.querySelectorAll('table.et-table > tbody > tr:not(.et-sub)').length, TILES.table_risk.table.rows.length);
  // a list column is bullets in the cell
  assert.ok(box.querySelector('ul.et-bullets li'));
  // the footer: never "All N of N" on one page
  assert.equal(box.querySelector('.et-count').textContent, "12 rows (one page; Egeria doesn't promise it is the last).");
});

test('a TABLE tile shows every pyegeria note and "more may exist"', { skip: SKIP }, () => {
  const dp = draw(makeWindow(), TILES.table_digital_products);
  const notes = [...dp.querySelectorAll('.et-notes li')].map(li => li.textContent);
  assert.deepEqual(notes, TILES.table_digital_products.table.notes);
  const col = draw(makeWindow(), TILES.table_collections);
  assert.equal(col.querySelector('.et-count').textContent, 'First 100; more may exist.');
  assert.ok([...col.querySelectorAll('.et-notes li')].some(li => /more may exist/.test(li.textContent)));
});

test('related elements are names with their qualified name, and no link (EA has no element view)', { skip: SKIP }, () => {
  const out = draw(makeWindow(), TILES.table_digital_products);
  assert.equal(out.querySelectorAll('.dash-etable a').length, 0);
  const first = TILES.table_digital_products.table.rows[0].used_by_digital_products[0];
  const span = [...out.querySelectorAll('.dash-etable span[title]')].find(s => s.textContent === first.displayName);
  assert.ok(span && span.title.startsWith(first.qualifiedName) && /No link/.test(span.title));
});

test('every column of a TABLE tile can be dragged wider, and a double-click resets it', { skip: SKIP }, () => {
  const w = makeWindow();
  const out = draw(w, TILES.table_risk);
  const box = out.querySelector('.dash-etable');
  const ths = [...box.querySelectorAll('table.et-table > thead th')];
  ths.forEach((th, i) => { th.getBoundingClientRect = () => ({ left: i * 100, right: (i + 1) * 100, width: 100 }); });
  const table = box.querySelector('table.et-table');
  const target = ths[1];
  const fire = (node, type, x) => node.dispatchEvent(new w.MouseEvent(type, { bubbles: true, clientX: x, button: 0 }));
  fire(target, 'mousedown', 198);                 // 2px inside its right edge
  fire(w.document, 'mousemove', 258);
  fire(w.document, 'mouseup', 258);
  assert.equal(target.style.width, '160px');
  assert.equal(ths[0].style.width, '100px');      // the others are frozen where they were
  assert.equal(table.style.tableLayout, 'fixed');
  assert.equal(table.style.width, (ths.length * 100 + 60) + 'px');
  fire(target, 'mousedown', 150);                 // the middle of a header is not an edge
  fire(w.document, 'mousemove', 400);
  fire(w.document, 'mouseup', 400);
  assert.equal(target.style.width, '160px');
  fire(target, 'dblclick', 198);
  assert.equal(target.style.width, '');
  assert.equal(table.style.tableLayout, '');
});

test('a sub-table opens under its row, in this tile only', { skip: SKIP }, () => {
  const w = makeWindow();
  const t = { kind: 'table', table: { kind: 'table', total: 1, total_basis: 'counted', shown: 1, notes: [],
    columns: [{ key: 'n', name: 'Name', type: 'scalar', render: 'text' },
              { key: 's', name: 'Steps', type: 'table', render: 'subtable' }],
    rows: [{ n: 'Plan', s: [{ Step: 'one' }, { Step: 'two' }] }] } };
  const a = draw(w, t), b = draw(w, t);
  b.querySelector('[data-et-sub]').click();
  assert.equal(b.querySelector('tr.et-sub').hidden, false);
  assert.equal(a.querySelector('tr.et-sub').hidden, true);
});

test('without the renderer a TABLE tile says so, never blank', { skip: SKIP }, () => {
  const out = draw(makeWindow({ table: false }), TILES.table_risk);
  assert.match(out.textContent, /table renderer \(egeria-table\.js\) did not load/);
});

test('a drill-down that answers with a structured table draws it', { skip: SKIP }, async () => {
  const w = makeWindow();
  w.fetch = async (url) => ({ ok: true, status: 200,
    json: async () => ({ state: 'ok', tile: url.endsWith('/detail') ? TILES.table_collections : TILES.kpi_count, cache: {} }) });
  const sheet = sheetOf(1);
  sheet.placements[0].detail_spec = 'Collections';
  const wrap = w.EADashboards.renderDashboard(sheet);
  await wrap._ready;
  const tile = wrap.querySelector('.dash-tile');
  tile.querySelector('.dash-drill').click();
  await new Promise(r => setTimeout(r, 10));
  assert.ok(tile.querySelector('.dash-detail .dash-etable table.et-table'));
  assert.equal(tile.querySelector('.dash-detail .et-count').textContent, 'First 100; more may exist.');
});
