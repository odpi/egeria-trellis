/** Value profile (DESIGN-VALUE-PROFILE-SCREEN-2026-10-30): the db-only tab, its header and filters, the table cards and
 *  column rows, the four absence cases, the Schema Inventory door, and the layout at 800 px and 1440 px. Real app.js and
 *  value-profile.js; fetch answers from value-profile.fixture.json, which the real Python builders wrote over the e2e
 *  test's registry (a measured table, an empty table, a not-measured table and a view). */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FIX = JSON.parse(readFileSync(new URL('./value-profile.fixture.json', import.meta.url), 'utf-8'));
const SLUG = 'coco_ods';
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

function makeBackend({ failTable = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    calls.push({ method: (options.method || 'GET').toUpperCase(), url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const base = `/api/databases/${SLUG}/value-profile`;
    if (u === base) return ok(FIX.summary);
    if (u.startsWith(`${base}/`)) {
      if (failTable) return { ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'registry down' }) };
      const [schema, table] = u.slice(base.length + 1).split('/').map(decodeURIComponent);
      return ok(FIX.tables[`${schema}.${table}`]);
    }
    if (u.includes('/api/projects/groups') || u.includes('/api/investigations/')) return ok([]);
    return ok({});
  };
  return calls;
}

async function setUp({ width = 1440, ...opts } = {}) {
  const { document, window } = makeDomEnvironment();
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() { window.__scrolledTo = this.dataset.vpTable; };
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  const calls = makeBackend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: SLUG, stage: 'scouting', subTab: 'questions', profileTable: '',
    databasesLoaded: true, databases: [{ slug: SLUG, display_name: 'Coco ODS', disposition: 'undecided' }],
    investigations: [], investigation: '', workingSet: new Set(), me: { user_id: 'me' } });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  }
  return { document, window, app, calls };
}

async function openTab(ctx, key = '') {
  ctx.app.openValueProfile(key);
  await tick(150);
  return ctx.document.getElementById('value-profile-host');
}
const card = (host, key) => host.querySelector(`[data-vp-table="${key}"]`);
const row = (host, key, col) => card(host, key).querySelector(`[data-vp-column="${col}"]`);
const cell = (host, key, col, k) => row(host, key, col).querySelector(`[data-vp-cell="${k}"]`);

test('Value profile is a database-only tab between Schema Inventory and Table preview', async () => {
  const ctx = await setUp();
  const ids = () => ctx.app.visibleSubTabs().map((t) => t.id);
  assert.equal(ids().indexOf('value_profile'), ids().indexOf('schema_inventory') + 1);
  assert.equal(ids().indexOf('table_preview'), ids().indexOf('value_profile') + 1);
  ctx.app.state.resourceType = 'repo';
  assert.ok(!ids().includes('value_profile'));
});

test('the header sentence, the filters with counts, and one card per table, the opened one in the URL', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx, 'sales.orders');
  assert.equal(text(host.querySelector('[data-vp-header]')), FIX.summary.header);
  const opts = (name) => [...host.querySelectorAll(`select[data-vp-filter="${name}"] option`)].map(text);
  assert.deepEqual(opts('state'), ['all', 'measured (7)', 'not measured (2)', 'not applicable (1)']);
  assert.deepEqual(opts('type'), ['all', 'text (3)', 'number (2)', 'date (1)', 'boolean (1)']);
  assert.deepEqual([...host.querySelectorAll('[data-vp-table]')].map((e) => e.dataset.vpTable),
    ['sales.empty_t', 'sales.orders', 'sales.big', 'sales.v_orders']);
  // A view is not counted as a table (VP-5).
  assert.equal(text(host.querySelector('[data-vp-match]')), '3 tables · 1 view');
  assert.equal(card(host, 'sales.orders').open, true, 'the table= card opens');
  assert.equal(card(host, 'sales.empty_t').open, false, 'every other card is collapsed');
  assert.equal(ctx.window.__scrolledTo, 'sales.orders');
  assert.match(ctx.window.location.search, /tab=value_profile/);
  assert.match(ctx.window.location.search, /table=sales\.orders/);
});

test('a not-measured table and a view are one line each with their reason, and nothing to open', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx);
  for (const key of ['sales.big', 'sales.v_orders']) {
    const el = card(host, key);
    assert.equal(el.tagName, 'DIV', `${key} has no opener`);
    assert.equal(el.querySelector('[data-vp-body]'), null);
  }
  assert.equal(text(card(host, 'sales.v_orders')), 'sales.v_orders · not applicable · a view, not a table: only tables are read');
  assert.match(text(card(host, 'sales.big')), /not measured · profile incomplete · exceeded 5 min/);
});

test('a card reads its columns when it opens; cells are words with their absence titles', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx);
  assert.ok(!ctx.calls.some((c) => c.url.includes('/value-profile/sales/')), 'no column read until a card opens');
  const el = card(host, 'sales.orders');
  el.open = true; el.dispatchEvent(new ctx.window.Event('toggle'));
  await tick();
  assert.ok(ctx.calls.some((c) => c.url.endsWith('/value-profile/sales/orders')));
  assert.match(ctx.window.location.search, /table=sales\.orders/);
  assert.deepEqual([...el.querySelectorAll('table[data-vp-grid] thead th')].map(text),
    ['Column', 'Type', 'Filled', 'Distinct', 'Range', 'Most common', 'Flags', 'Checks']);
  // The type's title is words, never TestGen's letter (VP-4).
  assert.equal(cell(host, 'sales.orders', 'id', 'type').title, 'number: integer and decimal values');
  assert.equal(text(cell(host, 'sales.orders', 'id', 'filled')), '4 of 4 · 100%');
  assert.equal(text(cell(host, 'sales.orders', 'id', 'distinct')), '4 · all distinct');
  // case 2: not a measure of this type -- a muted dash with the type in its title
  const dash = cell(host, 'sales.orders', 'id', 'common');
  assert.equal(text(dash), '—');
  assert.ok(dash.classList.contains('text-ink-muted'));
  assert.equal(dash.title, 'most common values are read for text columns only');
  // case 3: applies, not taken
  const asked = cell(host, 'sales.orders', 'status', 'common');
  assert.equal(text(asked), 'not asked for');
  assert.equal(asked.title, 'most common values were not collected on this run');
  assert.equal(text(cell(host, 'sales.orders', 'placed', 'flags')), '1 future date · 1 before 2006');
  assert.equal(text(row(host, 'sales.orders', 'id').querySelector('[data-vp-checks]')), '2 proposed · 1 not established');
});

test('an empty table: "0 rows · empty table", its zeros plain, no flags', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx, 'sales.empty_t');
  assert.match(text(card(host, 'sales.empty_t').querySelector('summary')), /0 rows · empty table/);
  const zero = cell(host, 'sales.empty_t', 'id', 'distinct');
  assert.equal(text(zero), '0');
  assert.ok(!zero.classList.contains('text-ink-muted'), 'a measured 0 is not drawn as an absence');
  assert.equal(text(cell(host, 'sales.empty_t', 'label', 'flags')), '');
});

test('a column that was not profiled is one row: its name, its cue and its reason', async () => {
  const ctx = await setUp();
  const vp = await import('/static/next/value-profile.js');
  const host = ctx.document.createElement('div');
  host.innerHTML = vp.bodyHtml(FIX.tables['sales.big'], {});
  const r = host.querySelector('[data-vp-column="id"]');
  assert.equal(r.dataset.vpState, 'not_measured');
  assert.match(text(r.querySelector('[data-vp-sentence]')), /^◌ not measured · profile incomplete/);
  assert.equal(r.querySelectorAll('[data-vp-cell]').length, 0, 'no cells are drawn');
});

test('"+N more" opens the whole list in place, and closes it again', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx, 'sales.orders');
  const c = row(host, 'sales.orders', 'channel');
  const btn = c.querySelector('[data-vp-more]');
  assert.equal(text(btn), '+2 more');
  assert.equal(c.querySelector('[data-vp-values]').hidden, true);
  btn.click();
  assert.equal(c.querySelector('[data-vp-values]').hidden, false);
  assert.match(text(c.querySelector('[data-vp-values]')), /\(empty\) ×1/);
  btn.click();
  assert.equal(text(btn), '+2 more');
});

test('filters narrow the cards from the counts and the rows inside an open card', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx, 'sales.orders');
  const pick = (name, value) => {
    const sel = host.querySelector(`select[data-vp-filter="${name}"]`);
    sel.value = value; sel.dispatchEvent(new ctx.window.Event('change'));
  };
  pick('type', 'boolean');
  await tick();
  assert.deepEqual([...host.querySelectorAll('[data-vp-table]')].map((e) => e.dataset.vpTable), ['sales.orders']);
  assert.deepEqual([...card(host, 'sales.orders').querySelectorAll('[data-vp-column]')].map((e) => e.dataset.vpColumn), ['paid']);
  assert.equal(text(host.querySelector('[data-vp-match]')), '1 of 3 tables · 0 of 1 view match');
  // The open card stays open, and its line says what the filter shows, not "5 columns measured" (VP-5).
  assert.equal(card(host, 'sales.orders').open, true);
  assert.equal(text(card(host, 'sales.orders').querySelector('summary')), 'sales.orders · 4 rows · 1 boolean column of 5 shown');
  pick('type', '');
  assert.equal(text(card(host, 'sales.orders').querySelector('summary')), 'sales.orders · 4 rows · 5 columns measured · profiled 11:12');
  pick('state', 'not_applicable');
  assert.deepEqual([...host.querySelectorAll('[data-vp-table]')].map((e) => e.dataset.vpTable), ['sales.v_orders']);
  assert.equal(text(host.querySelector('[data-vp-match]')), '0 of 3 tables · 1 of 1 view match', 'the count stays under a filter');
  pick('state', '');
  const input = host.querySelector('input[data-vp-filter="text"]');
  input.value = 'EMPTY'; input.dispatchEvent(new ctx.window.Event('input'));
  assert.deepEqual([...host.querySelectorAll('[data-vp-table]')].map((e) => e.dataset.vpTable), ['sales.empty_t']);
  input.value = 'nothing'; input.dispatchEvent(new ctx.window.Event('input'));
  assert.equal(text(host.querySelector('[data-vp-no-match]')), 'No tables match these filters.');
  assert.equal(text(host.querySelector('[data-vp-match]')), '0 of 3 tables · 0 of 1 view match');
});

test('a failed column read says so on the card', async () => {
  const ctx = await setUp({ failTable: true });
  const host = await openTab(ctx, 'sales.orders');
  assert.match(text(card(host, 'sales.orders').querySelector('[data-vp-error]')), /could not be read/);
});

test('a database never profiled says so, and draws no filters', async () => {
  const ctx = await setUp();
  const vp = await import('/static/next/value-profile.js');
  const html = vp.summaryHtml({ profiled: false, sentence: 'This database has no value profile yet.' }, { state: '', type: '', text: '' });
  const host = ctx.document.createElement('div'); host.innerHTML = html;
  assert.equal(text(host), 'This database has no value profile yet.');
  assert.equal(host.querySelector('select'), null);
});

test('Schema Inventory: "profile ›" opens the tab on that table; a table with no profile row says "not profiled"', async () => {
  const ctx = await setUp();
  const doors = ctx.app.profileDoorsFrom(FIX.summary);
  const host = ctx.document.createElement('div');
  ctx.document.getElementById('content').appendChild(host);
  host.innerHTML = ctx.app.schemaTreeHtml([{ schema: 'sales', classification: 'data', table_count: 3, row_total: 4, tables: [
    { name: 'orders', table_type: 'BASE TABLE', columns: [] },
    { name: 'v_orders', table_type: 'VIEW', columns: [] },
    { name: 'never', table_type: 'BASE TABLE', columns: [] },
  ] }], doors);
  const b = host.querySelector('[data-profile-table="orders"]');
  assert.equal(text(b), 'profile ›');
  assert.equal(text(host.querySelector('[data-tree-text="v_orders"] [data-profile-none]')), 'not profiled');
  assert.equal(host.querySelector('[data-tree-text="v_orders"] [data-profile-none]').title, 'a view, not a table: only tables are read');
  assert.equal(text(host.querySelector('[data-tree-text="never"] [data-profile-none]')), 'not profiled');
  // The door is wired on the real Schema Inventory pane; its handler opens the tab on the table it was pressed on.
  ctx.app.openValueProfile(`${b.dataset.profileSchema}.${b.dataset.profileTable}`);
  await tick(150);
  assert.equal(ctx.app.state.subTab, 'value_profile');
  assert.equal(ctx.app.state.profileTable, 'sales.orders');
  assert.equal(ctx.document.querySelector('[data-vp-table="sales.orders"]').open, true);
  // A profile that could not be read draws no door at all: unknown is not "not profiled".
  const none = ctx.document.createElement('div');
  none.innerHTML = ctx.app.schemaTreeHtml([{ schema: 'sales', classification: 'data', table_count: 1, row_total: 4,
    tables: [{ name: 'orders', table_type: 'BASE TABLE', columns: [] }] }], ctx.app.profileDoorsFrom(null));
  assert.equal(none.querySelector('[data-profile-table], [data-profile-none]'), null);
});

for (const width of [800, 1440]) {
  test(`at ${width} px the column table fits its panel: a real table, laid out to the width, resizable, names whole`, async () => {
    const ctx = await setUp({ width });
    const host = await openTab(ctx, 'sales.orders');
    const body = card(host, 'sales.orders').querySelector('[data-vp-body]');
    const table = body.querySelector('table[data-vp-grid]');
    assert.ok(table, 'a real <table> (VP-3), not a grid of divs');
    assert.ok(table.classList.contains('w-full'), 'laid out to the panel width');
    // No fixed widths until a person drags: no inner horizontal scroll at 1440 (VP-1). The wrapper only scrolls
    // once a drag has made the table wider than its panel.
    assert.equal(table.style.width, '', 'no drawn width on the table');
    assert.equal(table.style.tableLayout, '', 'auto layout: cells wrap to fit');
    assert.equal(table.style.minWidth, '');
    assert.ok(![...table.querySelectorAll('th, td')].some((c) => c.style.width || c.style.minWidth), 'no cell carries a width');
    assert.ok(!body.querySelector('[data-grid-host], [role="table"]'), 'not the flex grid');
    assert.equal(body.querySelector('[data-vp-scroll]').scrollWidth <= body.querySelector('[data-vp-scroll]').clientWidth, true);
    // colresize.js like every other table (VP-3): one handle per header.
    assert.equal(table.querySelectorAll('thead th [data-rc-handle]').length, 8);
    assert.ok(table.hasAttribute('data-rc-auto'));
    // A column name never breaks inside a word (VP-2), nor do the short counts; the long cells wrap at their spaces.
    for (const tr of table.querySelectorAll('tbody tr[data-vp-state="measured"]')) {
      const nowrap = [...tr.cells].map((c) => c.classList.contains('whitespace-nowrap'));
      assert.deepEqual(nowrap, [true, true, true, true, false, false, false, false]);
    }
    assert.ok(!/break-all|break-words|overflow-wrap/.test(table.outerHTML), 'nothing breaks mid-word');
    assert.ok(!/\bnull\b|undefined|NaN/.test(text(host)), 'no null, undefined or NaN on screen');
    assert.ok(!/\d{4}-\d\d-\d\dT\d/.test(text(host)), 'no raw ISO timestamp');
  });
}

test('the Checks door lands on that table\'s group in Curate › Structure, opened, not at the top of the page', async () => {
  const ctx = await setUp();
  const host = await openTab(ctx, 'sales.orders');
  const scrolled = [];
  ctx.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.dataset.qcGroup || ''); };
  // Curate's Structure tab is quality-checks.js's; here it is stood in for by the group it draws, after its read.
  assert.ok(row(host, 'sales.orders', 'id').querySelector('[data-vp-checks]'), 'the door is a button on the row');
  setTimeout(() => {
    const g = ctx.document.createElement('details');
    g.dataset.qcGroup = 'sales.orders';
    g.addEventListener('toggle', () => { g.dataset.toggled = String(g.open); });
    ctx.document.getElementById('content').appendChild(g);
  }, 300);
  const landed = ctx.app.openChecksForTable('sales.orders', { waitMs: 2000 });
  const g = await landed;
  assert.equal(ctx.app.state.stage, 'curate');
  assert.ok(g, 'the group was found');
  assert.equal(g.open, true);
  assert.equal(g.dataset.toggled, 'true', 'its rows are read (toggle fired)');
  assert.deepEqual(scrolled.slice(-1), ['sales.orders']);
});
