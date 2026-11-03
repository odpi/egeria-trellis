/** Table preview (REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19): the tab, the picker, the credential form, the masked header
 *  tags, reveal, "Mask all again", and the refusal states. Real app.js and table-preview.js; only fetch is stubbed in
 *  the shapes of web/routes/table_preview.py. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FAKE_PW = 'test-password-not-real-PV';
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const TREE = { schemas: [
  { schema: 'sales', classification: 'data', table_count: 2, row_total: 5, tables: [
    { name: 'orders', table_type: 'BASE TABLE', columns: [{ name: 'id' }, { name: 'email' }] },
    { name: 'v_orders', table_type: 'VIEW', columns: [{ name: 'id' }] },
  ] },
  { schema: 'pg_catalog', classification: 'system', system_count: 1 },
], sources: {} };

const COLS = [
  { name: 'id', masked: false, reason: null, tag: '', hover: '', revealed: false },
  { name: 'email', masked: true, reason: 'pii', tag: 'masked · email', hover: 'column profile matched the class email · run 10-06', revealed: false },
  { name: 'notes', masked: true, reason: 'not_profiled', tag: 'masked · not profiled', hover: "no column profile has read this column, so RE can't say it's safe", revealed: false },
  { name: 'tier', masked: true, reason: 'confidential', tag: 'masked · confidential', hover: 'Confidentiality classification · set by dwolfson 10-12 in Enrichment', revealed: false },
];
const COUNTS = { total: 4, masked: 3, pii: 1, confidential: 1, not_profiled: 1, undecided: 0 };
const RECORDED = { state: 'recorded', by: 'RE', at: '2026-10-20T14:03:00', id: 'a1' };

function rowsResult(over = {}) {
  return { outcome: 'rows', schema: 'sales', table: 'orders', kind: 'table', limit: 20, as_user: 'dan_ro', row_count: 2,
    columns: COLS, rows: [{ id: 1 }, { id: 2 }], counts: COUNTS, recorded: RECORDED, egeria: { state: 'not_built' }, ...over };
}

function makeBackend({ held = true, preview = () => rowsResult(), engine = true, forgetFails = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (u === '/api/databases/coco/preview/credential') {
      if (method === 'GET') return ok({ held, user: held ? 'dan_ro' : null, engine_supported: engine, unsupported: engine ? '' : 'Row preview is built for PostgreSQL only; this database is mysql, which has no adapter for it yet.' });
      if (method === 'PUT') { held = true; return ok({ held: true, user: body.user }); }
      if (method === 'DELETE' && forgetFails) return err(500, 'boom');
      if (method === 'DELETE') { held = false; return ok({ held: false, user: null }); }
    }
    if (method === 'POST' && u === '/api/databases/coco/preview') {
      const r = preview(body);
      return r && r.__err ? err(r.status, r.detail) : ok(r);
    }
    if (u.startsWith('/api/databases/coco/schema-inventory-tree')) return ok(TREE);
    if (method === 'GET' && (u.startsWith('/api/projects/groups') || u.startsWith('/api/investigations/?'))) return ok([]);
    return ok({});
  };
  return calls;
}

async function setUp(opts = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const tp = await import('/static/next/table-preview.js');
  app.state.resourceType = 'db';
  app.state.databasesLoaded = true;
  app.state.databases = [{ slug: 'coco', display_name: 'Coco', db_user: 'surveyor', credential_status: 'ok', disposition: 'undecided' }];
  app.state.selectedSlug = 'coco';
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  return { document, window, app, tp, calls };
}

async function mount(ctx, pick) {
  const host = ctx.document.createElement('div');
  ctx.document.body.appendChild(host);
  if (pick) ctx.tp.setPreviewPick(pick[0], pick[1]);
  await ctx.tp.mountTablePreview(host, { slug: 'coco', dbName: 'Coco' });
  await tick();
  return host;
}

async function press(host, n = 20) {
  host.querySelector(`[data-pv-run="${n}"]`).click();
  await tick(80);
}

test('Table preview is a database-only tab after Schema Inventory and Value profile, and not on a repo', async () => {
  const ctx = await setUp();
  const ids = () => ctx.app.visibleSubTabs().map((t) => t.id);
  assert.ok(ids().includes('table_preview'));
  // Value profile sits between the two (DESIGN-VALUE-PROFILE-SCREEN-2026-10-30 §2).
  assert.equal(ids().indexOf('table_preview'), ids().indexOf('schema_inventory') + 2);
  ctx.app.state.resourceType = 'repo';
  assert.ok(!ids().includes('table_preview'));
});

test('Schema Inventory tables carry "preview rows ›"', async () => {
  const ctx = await setUp();
  const host = ctx.document.createElement('div');
  host.innerHTML = ctx.app.tableHtml({ name: 'orders', table_type: 'BASE TABLE', columns: [], preview_schema: 'sales' });
  const b = host.querySelector('[data-preview-rows]');
  assert.equal(text(b), 'preview rows ›');
  assert.equal(b.dataset.previewSchema, 'sales');
  host.innerHTML = ctx.app.tableHtml({ name: 'orders', table_type: 'BASE TABLE', columns: [] });
  assert.equal(host.querySelector('[data-preview-rows]'), null, 'no schema, no door');
});

test('nothing runs on open: the picker, the cost words, and no preview call', async () => {
  const ctx = await setUp();
  const host = await mount(ctx);
  assert.ok(!ctx.calls.some((c) => c.url.endsWith('/preview') && c.method === 'POST'));
  assert.match(text(host.querySelector('[data-pv-intro]')), /Nothing runs until you press/);
  assert.deepEqual([...host.querySelectorAll('[data-pv-pick]')].map((b) => b.dataset.pvPick), ['sales.orders', 'sales.v_orders']);
  assert.ok(!text(host).includes('pg_catalog'), 'system schemas are not offered');
  host.querySelector('[data-pv-filter]').value = 'v_';
  host.querySelector('[data-pv-filter]').dispatchEvent(new ctx.window.Event('input', { bubbles: true }));
  assert.deepEqual([...host.querySelectorAll('[data-pv-pick]')].map((b) => b.dataset.pvPick), ['sales.v_orders']);
});

test('no credential held: the line says whose credential, and the form is "Your credential for Coco" with the session-only line', async () => {
  const ctx = await setUp({ held: false });
  const host = await mount(ctx, ['sales', 'orders']);
  assert.match(text(host.querySelector('[data-pv-controls]')), /Previews run as you, with your own credential for Coco, not the surveyor's\./);
  assert.equal(host.querySelector('[data-pv-run]'), null);
  host.querySelector('[data-pv-cred-open]').click();
  await tick();
  const slot = host.querySelector('[data-pv-cred-slot]');
  assert.match(text(slot), /Your credential for Coco/);
  assert.match(text(slot), /kept for this session only · never stored/);
  assert.equal(slot.querySelector('[data-cred-user]').value, '', "the surveyor's user is not prefilled");
  const set = (el, v) => { el.value = v; el.dispatchEvent(new ctx.window.Event('input', { bubbles: true })); };
  set(slot.querySelector('[data-cred-user]'), 'dan_ro');
  set(slot.querySelector('[data-cred-password]'), FAKE_PW);
  slot.querySelector('[data-cred-save]').click();
  await tick(80);
  const put = ctx.calls.find((c) => c.method === 'PUT');
  assert.deepEqual(put.body, { user: 'dan_ro', password: FAKE_PW });
  assert.match(text(slot.querySelector('[data-cred-saved]')), /kept for this session only · dan_ro/);
  assert.equal(slot.querySelector('[data-cred-password]').value, '');
  assert.ok(host.querySelector('[data-pv-run="20"]'), 'the press appears once a credential is held');
  assert.ok(!ctx.document.documentElement.outerHTML.includes(FAKE_PW), 'password not in the DOM');
  for (const c of ctx.calls) assert.ok(!c.url.includes(FAKE_PW));
});

test('the press: header names the database user, counts line above the grid, masked cells at fixed width, tags in the header', async () => {
  const ctx = await setUp();
  const host = await mount(ctx, ['sales', 'orders']);
  assert.match(text(host.querySelector('[data-pv-controls]')), /Show 20 rows.*Show 100 rows.*as you \(dan_ro\)/);
  await press(host, 20);
  const post = ctx.calls.find((c) => c.method === 'POST');
  assert.deepEqual(post.body, { schema: 'sales', table: 'orders', limit: 20, reveal: [] });
  const header = text(host.querySelector('[data-pv-header]'));
  assert.match(header, /^sales\.orders · first 20 rows as the database returned them · not a sample, not ordered · as you \(dan_ro\) · \d\d:\d\d/);
  assert.match(header, /recorded · RE \d\d:\d\d · Egeria record not built yet$/);
  assert.equal(text(host.querySelector('[data-pv-counts]')), '3 of 4 columns masked · 1 matched a data class · 1 confidential · 1 not profiled');
  const order = host.querySelector('[data-pv-counts]').compareDocumentPosition(host.querySelector('[data-pv-grid]'));
  assert.ok(order & 4, 'the count line is above the grid');
  const tags = [...host.querySelectorAll('[data-pv-tag]')].map((t) => text(t));
  assert.deepEqual(tags, ['masked · email', 'masked · confidential', 'masked · not profiled'].sort((a, b) => tags.indexOf(a) - tags.indexOf(b)));
  assert.equal(host.querySelector('[data-pv-tag="pii"]').title, 'column profile matched the class email · run 10-06');
  assert.match(host.querySelector('[data-pv-tag="not_profiled"]').className, /text-ink-muted/);
  assert.doesNotMatch(host.querySelector('[data-pv-tag="pii"]').className, /text-ink-muted/);
  const masked = [...host.querySelectorAll('[data-pv-masked]')];
  assert.equal(masked.length, 6);
  assert.ok(masked.every((c) => text(c) === '••••••'));
});

test('reveal one column: in-place confirm, recorded tag, Mask all again', async () => {
  const ctx = await setUp({
    preview: (b) => (b.reveal.length
      ? rowsResult({ columns: COLS.map((c) => (b.reveal.includes(c.name) ? { ...c, revealed: true } : c)), rows: [{ id: 1, email: 'a@x.test' }, { id: 2, email: 'b@x.test' }] })
      : rowsResult()),
  });
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  host.querySelector('[data-pv-reveal="email"]').click();
  assert.match(text(host.querySelector('[data-pv-confirm]')), /^Show email's values\? This is recorded with your name in RE\. Show · keep masked$/);
  host.querySelector('[data-pv-confirm-no]').click();
  assert.equal(host.querySelector('[data-pv-confirm]'), null);
  host.querySelector('[data-pv-reveal="email"]').click();
  host.querySelector('[data-pv-confirm-yes]').click();
  await tick(80);
  assert.deepEqual(ctx.calls.filter((c) => c.method === 'POST').pop().body.reveal, ['email']);
  assert.equal(text(host.querySelector('[data-pv-tag="revealed"]')), 'revealed by you · recorded');
  assert.ok(text(host.querySelector('[data-pv-grid]')).includes('a@x.test'));
  host.querySelector('[data-pv-mask-all]').click();
  assert.ok(!host.querySelector('[data-pv-grid]').textContent.includes('a@x.test'), 'revealed values are dropped');
  assert.equal(host.querySelector('[data-pv-tag="revealed"]'), null);
  assert.equal(host.querySelector('[data-pv-mask-all]'), null);
  assert.equal(host.querySelectorAll('[data-pv-masked]').length, 6);
});

test('a view says its cost before the press and its button says it runs the view', async () => {
  const ctx = await setUp();
  const host = await mount(ctx, ['sales', 'v_orders']);
  assert.match(text(host.querySelector('[data-pv-view-cost]')), /a view runs its query on every read · stopped after 10 s/);
  assert.equal(text(host.querySelector('[data-pv-run="20"]')), 'Run the view, 20 rows');
});

test('the three refusals each have their own words and next press, and none says "no rows" for a failure to read', async () => {
  let next;
  const ctx = await setUp({ preview: () => next });
  const host = await mount(ctx, ['sales', 'orders']);
  const base = { schema: 'sales', table: 'orders', kind: 'table', limit: 20, as_user: 'dan_ro', recorded: RECORDED, egeria: { state: 'not_built' } };

  next = { ...base, outcome: 'no_read', sentence: 'permission denied for table orders' };
  await press(host);
  assert.equal(text(host.querySelector('[data-pv-refusal="no_read"]')), "your credential can't read sales.orders · permission denied for table orders use another credential ›");
  assert.ok(host.querySelector('[data-pv-header]'), 'the header is kept');
  assert.equal(host.querySelector('[data-pv-grid]'), null);

  next = { ...base, outcome: 'timeout', sentence: 'no rows shown · the query ran longer than 10 s and was stopped · nothing was read' };
  await press(host);
  assert.equal(text(host.querySelector('[data-pv-refusal="timeout"]')), 'no rows shown · the query ran longer than 10 s and was stopped · nothing was read try again');
  assert.ok(host.querySelector('[data-pv-retry]'));
  assert.ok(!/(^|[^0-9])0 rows/.test(text(host.querySelector('[data-pv-result]'))));

  next = { ...base, outcome: 'empty', row_count: 0, columns: COLS, rows: [], counts: COUNTS };
  await press(host);
  assert.match(text(host.querySelector('[data-pv-refusal="empty"]')), /^∅ 0 rows · sales\.orders is empty, as of \d\d:\d\d$/);
});

test('"use another credential" forgets the held one and opens the form', async () => {
  const ctx = await setUp({ preview: () => ({ outcome: 'no_read', schema: 'sales', table: 'orders', kind: 'table', limit: 20, as_user: 'dan_ro', sentence: 'permission denied for table orders', recorded: RECORDED, egeria: { state: 'not_built' } }) });
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  host.querySelector('[data-pv-refusal] [data-pv-cred-open]').click();
  await tick(60);
  assert.ok(ctx.calls.some((c) => c.method === 'DELETE'));
  assert.match(text(host.querySelector('[data-pv-cred-slot]')), /Your credential for Coco/);
});

test('"use another credential" from the controls drops the held credential first, then opens the form', async () => {
  const ctx = await setUp();                                    // a credential is held
  const host = await mount(ctx, ['sales', 'orders']);
  assert.ok(host.querySelector('[data-pv-run]'), 'held: the presses are offered');
  host.querySelector('[data-pv-controls] [data-pv-cred-open]').click();
  await tick(60);
  const del = ctx.calls.findIndex((c) => c.method === 'DELETE' && c.url === '/api/databases/coco/preview/credential');
  assert.ok(del >= 0, 'the server was told to forget it');
  assert.match(text(host.querySelector('[data-pv-cred-slot]')), /Your credential for Coco/);
  assert.equal(host.querySelector('[data-pv-run]'), null, 'and the screen no longer says one is held');
  host.querySelector('[data-pv-cred-slot] [data-cred-cancel]').click();      // cancelling leaves nothing held
  await tick(40);
  assert.equal(host.querySelector('[data-pv-run]'), null);
  assert.ok(!ctx.calls.some((c) => c.method === 'PUT'), 'nothing was re-held');
});

test('if the server cannot forget the credential the screen keeps saying it is held, with a visible cue', async () => {
  const ctx = await setUp({ forgetFails: true });
  const host = await mount(ctx, ['sales', 'orders']);
  host.querySelector('[data-pv-controls] [data-pv-cred-open]').click();
  await tick(60);
  assert.ok(ctx.calls.some((c) => c.method === 'DELETE'), 'the forget was attempted');
  assert.ok(host.querySelector('[data-pv-run]'), 'still held: the presses are still offered');
  assert.match(text(host.querySelector('[data-pv-forget-failed]')), /couldn't forget the credential · try again/);
  assert.equal(host.querySelector('[data-pv-cred-slot] [data-cred-cancel]'), null, 'no form replaced a credential that is still held');
});

test('another engine says so and offers no press', async () => {
  const ctx = await setUp({ engine: false });
  const host = await mount(ctx, ['sales', 'orders']);
  assert.match(text(host.querySelector('[data-pv-unsupported]')), /PostgreSQL only/);
  assert.equal(host.querySelector('[data-pv-run]'), null);
});

test('a server refusal (no credential held after all) puts the credential line back', async () => {
  const ctx = await setUp({ preview: () => ({ __err: true, status: 409, detail: 'no credential of yours is held for coco; enter it first' }) });
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  assert.match(text(host.querySelector('[data-pv-error]')), /^no rows shown · no credential of yours is held/);
  assert.ok(host.querySelector('[data-pv-cred-open]') && !host.querySelector('[data-pv-run]'));
});

test('every non-success Egeria state is a word in the header, not only "not built"', async () => {
  for (const [state, words] of [['failed', /Egeria record failed$/], ['not_built', /Egeria record not built yet$/], ['queued_for_retry', /Egeria record queued for retry$/]]) {
    const ctx = await setUp({ preview: () => rowsResult({ egeria: { state } }) });
    const host = await mount(ctx, ['sales', 'orders']);
    await press(host);
    assert.match(text(host.querySelector('[data-pv-header]')), words, state);
  }
  const ctx = await setUp({ preview: () => rowsResult({ egeria: { state: 'recorded' } }) });
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  assert.doesNotMatch(text(host.querySelector('[data-pv-header]')), /Egeria record/);
});

test('the preview grid carries the colresize hook: a handle per column, keyboard resize, widths kept per table', async () => {
  const ctx = await setUp();
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  const grid = host.querySelector('[data-pv-grid]');
  assert.equal(grid.dataset.rcId, 'table-preview.sales.orders');
  assert.ok(grid.hasAttribute('data-rc-auto'), 'enhanced by colresize.js');
  const handles = [...grid.querySelectorAll('[data-rc-handle]')];
  assert.equal(handles.length, COLS.length);
  const before = grid.style.width;
  handles[0].dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.ok(grid.hasAttribute('data-rc-fixed'), 'the first change freezes the drawn widths');
  assert.notEqual(grid.style.width, before);
  // a redraw (a reveal prompt) replaces the table; the new one is wired too
  host.querySelector('[data-pv-reveal="email"]').click();
  assert.ok(host.querySelector('[data-pv-grid]').hasAttribute('data-rc-auto'));
});

test('columns past the server cap say how many are not shown', async () => {
  const ctx = await setUp({ preview: () => rowsResult({ columns_not_shown: 7 }) });
  const host = await mount(ctx, ['sales', 'orders']);
  await press(host);
  assert.equal(text(host.querySelector('[data-pv-more-columns]')), '7 more columns not shown');
  const ctx2 = await setUp({ preview: () => rowsResult({ columns_not_shown: 0 }) });
  const host2 = await mount(ctx2, ['sales', 'orders']);
  await press(host2);
  assert.equal(host2.querySelector('[data-pv-more-columns]'), null);
});
