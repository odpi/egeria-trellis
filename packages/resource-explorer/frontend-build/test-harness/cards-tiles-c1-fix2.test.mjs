/** C1 second walk (2026-10-20): charts have room for their labels, every data table resizes by default, Light
 *  detail text is darker, and no tile or label box overruns. jsdom does no layout, so geometry is asserted as the
 *  numbers and the classes that produce it, evaluated at the three text steps. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';

ensureLoaderRegistered();
const NEXT = new URL('../../resource_explorer/web/static/next/', import.meta.url);
const read = (p) => fs.readFileSync(new URL(p, NEXT), 'utf8');
const ROOT_PX = { A: 16, 'A+': 17.9, 'A++': 20 };
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const cls = (el) => [...el.classList];

/* ── 1. charts ─────────────────────────────────────────────────────────────────────────────────── */

const bars = (n, two = false) => {
  const names = Array.from({ length: n }, (_, i) => `schema_${String(i).padStart(2, '0')}`);
  const data = [{ type: 'bar', orientation: 'h', y: names, x: names.map((_, i) => i + 1) }];
  const layout = { xaxis: { title: { text: 'Tables' } }, yaxis: {} };
  if (two) { data.push({ type: 'bar', orientation: 'h', y: names, x: names.map((_, i) => i * 3), xaxis: 'x2', yaxis: 'y' }); layout.xaxis2 = { title: { text: 'Columns' } }; }
  return { data, layout };
};

test('horizontal bars: height gives every bar its own ~22px row (30 bars, 20 bars), never under the base height', async () => {
  const cl = await import('/static/next/chart-labels.js');
  assert.equal(cl.BAR_ROW_H, 22);
  for (const n of [30, 20, 3]) {
    const f = cl.fitFigure(bars(n), 900);
    const need = n * 22 + f.layout.margin.t + f.layout.margin.b;
    assert.ok(f.height >= Math.max(260, need), `${n} bars: ${f.height}px for ${need}px of rows`);
    assert.equal(f.layout.height, f.height);
    assert.ok(f.height / n >= 22, `${n} bars: ${(f.height / n).toFixed(1)}px per bar`);
  }
  assert.equal(cl.fitFigure(bars(3), 900).height, 260, 'a short chart keeps the base height');
  const two = cl.fitFigure(bars(30, true), 900);
  assert.ok(two.height >= 30 * 22, 'two panels share the rows');
});

test('horizontal bars: the y axis margin grows with its labels (automargin on) and the tick font is 13px, 2px up', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const f = cl.fitFigure(bars(30), 900);
  assert.equal(f.layout.yaxis.automargin, true);
  assert.equal(f.layout.yaxis.tickmode, 'array');
  const widest = Math.max(...f.layout.yaxis.ticktext.map((t) => t.length)) * cl.CHAR_W;
  assert.ok(f.layout.margin.l >= Math.min(Math.ceil(900 * 0.4), widest + 14) - 1, 'room for the widest shown label');
  const { document, window } = makeDomEnvironment();
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  const app = await loadAppModule();
  const lay = app.chartLayout({});
  assert.equal(lay.xaxis.tickfont.size, 13);
  assert.equal(lay.yaxis.tickfont.size, 13);
  assert.equal(lay.yaxis.tickfont.color, lay.xaxis.tickfont.color, 'tokens() colours kept');
  assert.ok(document);
});

test('the chart card takes its height from the fit, in the plot box and in Plotly, at every text size', () => {
  const src = read('stages/understanding.js');
  assert.match(src, /plot\.style\.height = `\$\{fit\.height\}px`/);
  assert.match(src, /height: fit\.height \}\)/);
  assert.doesNotMatch(src, /height: 260/);
  // The chart font is px, so the row is too: at the largest text step a row is still the same 22px, and the 13px
  // label is under it. The label never needs more than its own row.
  for (const [, px] of Object.entries(ROOT_PX)) assert.ok(13 + 4 <= 22, `a 13px label fits a 22px row at root ${px}px`);
});

test('a chat chart that is horizontal bars gets the same rows and automargin', () => {
  const src = read('app.js');
  assert.match(src, /barChartHeight\(barCount/);
  assert.match(src, /yaxis, \{ automargin: true \}/);
});

/* ── 2. every data table resizes ───────────────────────────────────────────────────────────────── */

const SCHEMAS = [{ schema: 'public', classification: 'data', table_count: 1, row_total: 5, is_estimate: false, reason: '',
  tables: [{ name: 'orders', table_type: 'BASE TABLE', row_count: 5, row_count_state: 'measured', size_bytes: 1, column_count: 1,
    columns: [{ name: 'id', type: 'integer', nullable: false, key_role: 'PK', comment: '' }] }] }];

const dataTables = (root) => [...root.querySelectorAll('table')].filter((t) => !t.closest('[data-no-resize]'));
function assertAllResizable(root, where, min = 1) {
  const tables = dataTables(root);
  assert.ok(tables.length >= min, `${where}: expected at least ${min} table(s), found ${tables.length}`);
  for (const t of tables) {
    assert.ok(t.hasAttribute('data-rc-auto'), `${where}: a table with no resize handles: ${t.outerHTML.slice(0, 120)}`);
    assert.ok(t.querySelectorAll('[data-rc-handle]').length >= 2, `${where}: handles on the columns`);
  }
}

const RUN = { run_id: 7, surveyed_at: '2026-09-28T14:02:00', source: 'local', surveyed_as: 's' };
const env = (extra = {}) => ({ database: 'adv', run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...extra });
const CURRENCY = { as_of: '2026-10-09T10:00:00', stale_days: 30,
  database: { total: 140, behind: 31, never: 3, stale: 28, reset_tables: 2, not_collected: 0, counters_reset: { state: 'reset_at', at: '2026-10-02T08:00:00' } },
  schemas: [{ schema: 'sales', total: 40, never: 3, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: 41, empty: false }], tables: {} };
const HISTORY = [
  { run: { surveyed_at: '2026-10-01T10:00:00' }, measured: true, never: 5, stale: 10, reset_tables: 0, counters_reset: { state: 'never_reset' } },
  { run: { surveyed_at: '2026-10-09T10:00:00' }, measured: true, never: 3, stale: 28, reset_tables: 2, counters_reset: CURRENCY.database.counters_reset }];

test('At a glance, Contents and Over time (the Understanding tabs): every data table has resize handles', async () => {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (/\/statistics_currency/.test(u)) return ok(env({ currency: CURRENCY, history: HISTORY }));
    if (/\/api\/stats\/databases\/[^/]+\/([a-z_]+)/.test(u)) return ok(env({ points: [], runs: [], series: [], tables: [], row_counts: [], sizes_mb: [], schemas: [], table_counts: [], column_counts: [], types: [], counts: [] }));
    if (/\/api\/databases\/[^/]+\/diff/.test(u)) return ok({ prev_date: '2026-09-21T10:00:00', curr_date: '2026-09-28T14:02:00', deltas: { schemas: 0, tables: 1, columns: 14 }, new_tables: [], removed_tables: [], table_diff_state: 'measured', curr_table_state: 'measured', prev_table_state: 'measured' });
    if (/\/api\/databases\/[^/]+\/surveys/.test(u)) return ok([]);
    if (/\/api\/databases\/[^/]+\/views/.test(u)) return ok({ state: 'measured', run: RUN, views: [] });
    if (/\/api\/analyses\//.test(u)) return ok([]);
    if (u.includes('/questions')) return ok({ questions: [] });
    return ok({});
  };
  window.Plotly = { async newPlot() {}, purge() {}, async toImage() { return ''; } };
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => { const r = append(el); if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0); return r; };
  globalThis.location = window.location; globalThis.getComputedStyle = window.getComputedStyle.bind(window); globalThis.history = window.history;
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'adv', stage: 'scouting', subTab: 'questions', investigations: [], investigation: '', workListSlug: null, workListIndex: false });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  const { watchTables } = await import('/static/next/colresize.js');
  watchTables(document.body);
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await tick(500);
  let seen = 0;
  for (const t of ['glance', 'contents', 'over_time']) {
    const btn = document.querySelector(`[data-subtab="understanding_${t}"]`);   // At a glance is the open tab: no button to press
    if (btn) { btn.click(); await tick(400); }
    const panel = document.querySelector(`[data-understanding-tab-panel="${t}"]`);
    seen += dataTables(panel).length;
    assertAllResizable(panel, `Understanding ${t}`, 0);
  }
  assert.ok(seen >= 1, 'the statistics-currency grid of figures is a data table and was met');
});

test('Schema Inventory: the column table of an expanded table has resize handles', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const { watchTables } = await import('/static/next/colresize.js');
  const host = document.createElement('div'); document.body.appendChild(host);
  watchTables(host);
  host.innerHTML = app.schemaTreeHtml(SCHEMAS);
  await tick();
  assertAllResizable(host, 'Schema Inventory');
});

test('Survey & analyses: the chosen files-and-folders table has resize handles', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.includes('/sub-resources') || u.includes('sub_resources')) return ok([{ locator: 'docs', kind: 'folder', egeria_guid: '' }]);
    if (u.includes('/api/analyses/')) return ok([{ id: 'a', name: 'A', target_shape: 'container' }]);
    return ok({});
  };
  const app = await import('/static/next/app.js');
  const { mountSubResourcePanel } = await import('/static/next/stages/analysis.js');
  const { watchTables } = await import('/static/next/colresize.js');
  const host = document.createElement('div'); document.body.appendChild(host);
  watchTables(host);
  await mountSubResourcePanel('repo_one', host);
  await tick(100);
  assert.ok(app);
  assertAllResizable(host, 'Survey & analyses');
});

test('Automate: the subscriptions and the schedules tables have resize handles', async () => {
  const { document } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push(String(url));
    const ok = (json) => ({ ok: true, status: 200, json: async () => json });
    const u = String(url);
    if (u === '/api/schedules/') return ok([{ entity_type: 'repo', entity_slug: 'r', analysis_id: 'lic', schedule: 'daily', enabled: 1, target_kind: 'analysis', last_run_status: '', last_run_activity_id: '', last_run: '', next_run: '' }]);
    if (u.startsWith('/api/analyses/')) return ok([{ id: 'lic', name: 'Licence' }]);
    if (u === '/api/survey-definitions/definitions') return ok([]);
    if (u.includes('subscriptions')) return ok([{ id: 1, entity_type: 'repo', entity_slug: 'r', analysis_id: 'lic', status: 'active', last_checked: '', notify: 1 }]);
    return ok([]);
  };
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'repo'; app.state.selectedSlug = 'r';
  const host = document.createElement('div'); host.id = 'content'; document.body.appendChild(host);
  const { watchTables } = await import('/static/next/colresize.js');
  watchTables(document.body);
  const automate = await import(`/static/next/stages/automate.js?t=${Math.random()}`);
  await automate.renderAutomate();
  await tick(); await tick();
  assertAllResizable(host, 'Automate subscriptions', 0);
  const subs = dataTables(host).length;
  host.querySelector('[data-automate-tab="schedules"]').click();
  await tick(); await tick();
  assertAllResizable(host, 'Automate schedules', 1);
  assert.ok(subs >= 0);
});

test('a table drawn after the watcher starts is enhanced; a header press resizes and never sorts', async () => {
  const { document, window } = makeDomEnvironment();
  const { watchTables } = await import('/static/next/colresize.js');
  const host = document.createElement('div'); document.body.appendChild(host);
  watchTables(host);
  let sorted = 0;
  host.innerHTML = '<table><thead><tr><th data-sort>Name</th><th>Kind</th><th>Size</th></tr></thead><tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody></table>';
  host.querySelector('[data-sort]').addEventListener('click', () => { sorted += 1; });
  await tick();
  const t = host.querySelector('table');
  assert.ok(t.hasAttribute('data-rc-auto'));
  const h = t.querySelector('[data-rc-handle="1"]');
  h.dispatchEvent(new window.MouseEvent('pointerdown', { clientX: 100, bubbles: true, cancelable: true }));
  document.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 160 }));
  document.dispatchEvent(new window.MouseEvent('pointerup', { clientX: 160 }));
  assert.equal(t.style.tableLayout, 'fixed');
  const ths = [...t.querySelectorAll('th')];
  assert.equal(parseInt(ths[1].style.width, 10), 120 + 60, 'the dragged column took the delta (120px drawn width in jsdom + 60)');
  assert.equal(ths[0].style.width, '120px', 'the others keep their drawn width');
  h.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.equal(sorted, 0, 'the handle click does not reach the sortable header');
  h.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(parseInt(ths[1].style.width, 10), 190);
  h.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true }));
  assert.equal(ths[1].style.width, '120px', 'double-click resets that column');
});

test('layout and key/value tables are left alone: no header row, or the opt-out', async () => {
  const { document } = makeDomEnvironment();
  const { enhanceTables } = await import('/static/next/colresize.js');
  const host = document.createElement('div');
  host.innerHTML = '<table id="kv"><tr><td>Key</td><td>Value</td></tr></table>'
    + '<div data-no-resize><table id="opt"><thead><tr><th>A</th><th>B</th></tr></thead></table></div>';
  document.body.appendChild(host);
  assert.equal(enhanceTables(host), 0);
  assert.equal(host.querySelectorAll('[data-rc-handle]').length, 0);
});

/* ── 3. Light detail text ─────────────────────────────────────────────────────────────────────── */

test('Light: ink-muted and chrome-muted are #3f4658 (was #4c5468); Dark and Paper are untouched', () => {
  const css = read('themes.css');
  const block = (look) => css.slice(css.indexOf(`:root[data-look="${look}"]`)).split('}')[0];
  assert.match(block('light'), /--c-ink-muted: 63 70 88;/);
  assert.match(block('light'), /--c-chrome-muted: 63 70 88;/);
  assert.doesNotMatch(block('light'), /76 84 104/);
  assert.match(block('paper'), /--c-ink-muted: 74 70 62;/);
  assert.match(block('paper'), /--c-chrome-muted: 155 151 151;/);
  assert.match(css, /--c-ink-muted: 163 171 192;/);
});

/* ── 4. label over-runs ───────────────────────────────────────────────────────────────────────── */

const OV = { slug: 'r1', display_name: 'R1', homepage: 'https://www.egeria-project.org/guides/', primary_language: 'Python', stars: 8, forks: 4,
  contributors_count: 7, last_pushed_at: '2026-10-02T10:00:00', repo_size_kb: 91341,
  security_and_analysis: { secret_scanning: { status: 'enabled' }, a: { status: 'disabled' }, b: 'disabled', c: 'disabled' },
  deployments_count: 19, latest_deployment_at: '2026-10-01T10:00:00', latest_deployment_environment: 'egeria-prod-environment', latest_deployment_ref: 'main',
  stats_unread: [], egeria_link_stale: false };

test('scouting tiles: a wrapping row of content-sized tiles with a 7rem floor, label wraps in its own tile, value on one line with the full text on hover', async () => {
  const { document } = makeDomEnvironment();
  const { scoutingTilesHtml } = await import('/static/next/stages/repo-header.js');
  const d = document.createElement('div');
  d.innerHTML = scoutingTilesHtml(OV);
  const row = d.querySelector('[data-scouting-tiles]');
  for (const c of ['flex', 'flex-wrap', 'items-start']) assert.ok(cls(row).includes(c), c);
  assert.ok(!cls(row).some((c) => /grid/.test(c)), 'no fixed column grid');
  const tiles = [...d.querySelectorAll('[data-scouting-tile]')];
  assert.equal(tiles.length, 9);
  for (const t of tiles) {
    assert.ok(cls(t).includes('min-w-[7rem]') && cls(t).includes('max-w-full'), 'floor and cap');
    assert.ok(!cls(t).some((c) => /^w-|^basis-|^grow|^flex-1/.test(c)), 'content-sized');
    const label = t.querySelector('[data-scouting-label]');
    assert.ok(cls(label).includes('break-words') && cls(label).includes('[overflow-wrap:anywhere]'), 'a label wraps inside its tile');
    const v = t.querySelector('[data-scouting-value]');
    assert.ok(cls(v).includes('whitespace-nowrap') && cls(v).includes('truncate'), 'a value stays on one line');
    assert.ok(t.getAttribute('title'), `${t.dataset.scoutingTile}: the full value is on hover`);
  }
  assert.equal(d.querySelector('[data-scouting-tile="security"]').title, 'secret_scanning: enabled, a: disabled, b: disabled, c: disabled');
  assert.match(d.querySelector('[data-scouting-tile="deployments"]').title, /Latest: egeria-prod-environment @ main/);
  assert.equal(d.querySelector('[data-scouting-tile="website"]').title, 'https://www.egeria-project.org/guides/');
  assert.equal(d.querySelector('[data-scouting-tile="contributors"]').title, '7');
  const floor = Object.entries(ROOT_PX).map(([k, px]) => `${k}:${(7 * px).toFixed(0)}px`);
  assert.deepEqual(floor, ['A:112px', 'A+:125px', 'A++:140px']);
});

test('the survey report metric cards follow the same fit rules', async () => {
  const { document } = makeDomEnvironment();
  const { repoReportHtml } = await import('/static/next/stages/repo-report.js');
  const d = document.createElement('div');
  d.innerHTML = repoReportHtml({ health: { stars: 1, forks: 2, open_issues: 3, contributors: 4, license: 'Apache-2.0-with-a-long-name' }, health_read: true,
    primary_language: 'Python', local_surveyed_at: '2026-10-01T00:00:00', total_files: 2125, file_types: [], data_files: [], dependencies: [] });
  const row = d.querySelector('[data-report-metrics]');
  assert.ok(cls(row).includes('flex-wrap') && !cls(row).some((c) => /grid/.test(c)));
  const cards = [...d.querySelectorAll('[data-report-card]')];
  assert.equal(cards.length, 7);
  for (const c of cards) {
    assert.ok(cls(c).includes('min-w-[7rem]'));
    assert.ok(cls(c.firstElementChild).includes('break-words'));
    assert.ok(c.title, 'hover has the whole value');
  }
  assert.equal(d.querySelector('[data-report-card="license"]').title, 'Apache-2.0-with-a-long-name');
});

test('sweep: the fixed-pixel label and value boxes became rem or content-sized, so they follow the text size', () => {
  for (const f of ['stages/context.js', 'stages/enrichment.js']) {
    const s = read(f);
    assert.doesNotMatch(s, /grid-cols-\[130px_1fr\]/, `${f}: label column no longer 130px`);
    assert.match(s, /grid-cols-\[minmax\(8\.5rem,14rem\)_1fr\]/, `${f}: label column is rem-based and grows with the text`);
  }
  const wl = read('worklist.js');
  assert.doesNotMatch(wl, /tnum w-\[(18|22|34)px\]/);
  assert.match(wl, /tnum min-w-\[1\.2rem\]/);
  assert.match(wl, /tnum min-w-\[2\.2rem\]/);
  assert.match(read('app.js'), /w-\[18rem\] max-w-\[90vw\]/);
  assert.match(read('shell-status.js'), /w-\[22\.5rem\] max-w-\[92vw\]/);
  // 130px was 8.1rem at the base step; the new floor is wider than the old box at every step.
  for (const [, px] of Object.entries(ROOT_PX)) assert.ok(8.5 * px >= 130);
});
