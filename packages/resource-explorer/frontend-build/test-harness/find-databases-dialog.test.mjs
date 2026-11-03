/** Behaviour: the sidebar's Find action for databases is a three-tab dialog
 *  (Saved sources, Discover on a server, From a file) whose every tab ends in
 *  the same candidate table and the same confirm.
 *
 *  Slice 1 of REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md. Source-text pins let
 *  blank/wrong panes through five times, so every test here runs the REAL app.js
 *  and the REAL router (clicks the real sidebar Find button with the database
 *  kind selected) and reads the DOM. Only the network (fetch) is stubbed, and the
 *  stub speaks the response shapes of web/routes/db_servers.py.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SECRET = 'hunter2-do-not-leak';

const SERVER = (over = {}) => ({
  slug: 'regional_pg', display_name: 'Regional PG', db_type: 'postgresql', host: 'pg.regional', port: 5432,
  description: '', db_user: 'scout_ro', egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '',
  status: 'active', registered_at: '2026-09-01T00:00:00', group_slug: 'regional', databases: [],
  last_run_at: null, last_run_candidate_count: null, ...over,
});

const CAND = (name, over = {}) => ({
  name, key: `pg.regional:5432/${name}`, address: `pg.regional:5432/${name}`, server_slug: 'regional_pg',
  can_connect: true, size_pretty: '10 MB', size_bytes: 10485760, owner: 'postgres', description: '',
  encoding: 'UTF8', is_registered: false, registered_slug: null, verdict: null, is_new: null, ...over,
});

const RUN = (cands, over = {}) => ({
  server_slug: 'regional_pg', run_at: '2026-10-01T12:00:00', previous_run_at: null, first_run: true,
  candidate_count: cands.length, new_count: null, candidates: cands, ...over,
});

/** A recording backend. `routes` maps "METHOD /path-prefix" to a handler; later
 *  entries in `script.run` are served one per Run call. */
function makeBackend({ servers = [SERVER()], runs = [], inline = null, investigations = [], groups = [] } = {}) {
  const calls = [];
  const runQueue = [...runs];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (method === 'GET' && u === '/api/db-servers/') return ok(servers);
    if (method === 'GET' && u.startsWith('/api/projects/groups')) return ok(groups);
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok(investigations);
    const run = u.match(/^\/api\/db-servers\/([^/]+)\/run$/);
    if (method === 'POST' && run) {
      const next = runQueue.shift();
      if (!next) return err(500, 'no scripted run');
      if (next.__http) return err(next.__http, next.detail || 'denied');
      return ok(next);
    }
    if (method === 'POST' && u === '/api/db-servers/_discover-inline') {
      if (inline && inline.__http) return err(inline.__http, inline.detail || 'denied');
      return ok(inline || { host: body.host, port: body.port, candidates: [] });
    }
    if (method === 'POST' && u === '/api/db-servers/register') {
      servers.push(SERVER({ slug: body.slug, display_name: body.display_name, host: body.host, db_user: body.db_user }));
      return ok(servers[servers.length - 1]);
    }
    const add = u.match(/^\/api\/db-servers\/([^/]+)\/add-database\?database_name=([^&]+)/);
    if (method === 'POST' && add) {
      return ok({ slug: `${add[1]}-${decodeURIComponent(add[2])}`.replace(/_/g, '-'), database_name: decodeURIComponent(add[2]), server_slug: add[1] });
    }
    if (method === 'POST' && /^\/api\/projects\/[^/]+\/group$/.test(u)) return ok({});
    if (method === 'POST' && /^\/api\/investigations\/[^/]+\/members$/.test(u)) return ok([]);
    return ok(u.includes('/groups') || u.includes('/projects/') || u.includes('/databases/') ? [] : {});
  };
  return calls;
}

async function setUp(backendOpts = {}, { investigation = '', investigations = [] } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend({ investigations, ...backendOpts });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();   // listGroups() is memoised at module level; each test brings its own groups
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.databases = [];
  app.state.databasesLoaded = true;
  app.state.groups = [];
  app.state.investigations = investigations;
  app.state.investigation = investigation;
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  app.renderSidebar();
  return { document, window, app, calls };
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function openFind(ctx) {
  const btn = ctx.document.querySelector('#sidebar [data-act="find-repos"]');
  assert.ok(btn, 'the sidebar must offer the Find action for databases');
  btn.click();
  await tick();
  const dlg = ctx.document.getElementById('wl-detail');
  assert.ok(dlg, 'clicking Find must open the dialog');
  return dlg;
}

const tab = (dlg, id) => dlg.querySelector(`[data-tab="${id}"]`);
async function clickTab(dlg, id) { tab(dlg, id).click(); await tick(); }

test('Find on databases opens a three-tab dialog: Saved sources, Discover on a server, From a file', async () => {
  const ctx = await setUp({ servers: [SERVER(), SERVER({ slug: 'second', display_name: 'Second' })] });
  const dlg = await openFind(ctx);
  assert.match(text(dlg), /Find databases/);
  const labels = [...dlg.querySelectorAll('[data-tab]')].map((b) => b.dataset.tab);
  assert.deepEqual(labels, ['saved', 'discover', 'file']);
  assert.match(text(tab(dlg, 'saved')), /^Saved sources 2$/, 'the tab counts the registered servers');
  assert.match(text(tab(dlg, 'discover')), /^Discover on a server$/);
  assert.match(text(tab(dlg, 'file')), /^From a file$/);
  // Saved sources is the landing tab: each registered server is a row with Run.
  const rows = [...dlg.querySelectorAll('[data-source]')];
  assert.equal(rows.length, 2);
  assert.ok(rows.every((r) => r.querySelector('[data-run]')), 'every saved source has a Run');
  assert.match(text(rows[0]), /not yet run/, 'a source that never ran says so');
  assert.doesNotMatch(text(dlg), /file share/i, 'the kind is "file system", never "file share"');
});

test('From a file is the real door now (slice 2): a file chooser, no placeholder sentence, no table until a file is read', async () => {
  const ctx = await setUp();
  const dlg = await openFind(ctx);
  await clickTab(dlg, 'file');
  assert.equal(dlg.querySelector('[data-file-placeholder]'), null, 'the "coming in the next slice" sentence is gone');
  assert.ok(dlg.querySelector('[data-file-input]'), 'there is a file chooser');
  assert.equal(dlg.querySelector('[data-candidate-table]'), null);
  assert.equal(dlg.querySelector('[data-confirm]'), null, 'no confirm until a file has been read');
});

test('every tab that produces candidates ends in the same table and the same confirm', async () => {
  const cands = [CAND('a_forecast'), CAND('b_forecast')];
  const ctx = await setUp({ runs: [RUN(cands)], inline: { host: 'pg.new', port: 5432, candidates: cands.map((c) => ({ ...c, server_slug: null })) } },
    { investigations: [{ slug: 'c360', display_name: 'Customer 360', status: 'active' }] });
  const dlg = await openFind(ctx);
  // 1. Saved sources: Run.
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  const sig = (d) => ({
    heads: [...d.querySelectorAll('[data-candidate-table] thead th')].map((th) => text(th)),
    confirm: [...d.querySelectorAll('[data-confirm] [data-act]')].map((e) => e.dataset.act),
  });
  const saved = sig(dlg);
  assert.deepEqual(saved.heads, ['', 'Database', 'Size', 'Owner role', 'Description',
    'Connect with this credential', 'Prior verdict', 'activity · after registration']);
  assert.deepEqual(saved.confirm, ['group', 'investigation', 'confirm']);
  // 2. Discover on a server (one-off).
  await clickTab(dlg, 'discover');
  assert.equal(dlg.querySelector('[data-candidate-table]'), null, 'candidates belong to the source that produced them');
  dlg.querySelector('[data-oo="host"]').value = 'pg.new';
  dlg.querySelector('[data-oo="db_user"]').value = 'u';
  dlg.querySelector('[data-oo="db_password"]').value = SECRET;
  dlg.querySelector('[data-act="discover-inline"]').click();
  await tick();
  assert.deepEqual(sig(dlg), saved, 'the one-off tab ends in the identical table and confirm');
});

test('candidate rows: every state is worded from the response, never a 0 or a blank', async () => {
  const cands = [
    CAND('ok_db', { description: 'Monthly forecast', owner: 'forecast_owner', size_pretty: '2.1 GB' }),
    CAND('region_east', { can_connect: false, size_pretty: null, size_bytes: null, owner: 'east_dba', description: '' }),
    CAND('no_owner', { owner: null, description: null }),
    CAND('hr_archive', { verdict: { disposition: 'ignored', reason: 'archive', decided_at: '2026-09-30' } }),
    CAND('already_in', { is_registered: true, registered_slug: 'x', verdict: { disposition: 'recommended', reason: '', decided_at: '' } }),
  ];
  const ctx = await setUp({ runs: [RUN(cands)] });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  const row = (n) => dlg.querySelector(`[data-cand="pg.regional:5432/${n}"]`);

  const ok = row('ok_db');
  assert.match(text(ok), /2\.1 GB/);
  assert.match(text(ok), /forecast_owner/);
  assert.match(text(ok), /Monthly forecast/);
  assert.match(text(ok), /✓ yes/);
  assert.match(text(ok), /undecided/, 'nobody decided: said as undecided');
  assert.match(text(ok), /after registration/, 'activity is never guessed before registration');
  assert.equal(ok.querySelector('input').disabled, false);

  const east = row('region_east');
  assert.equal(east.dataset.state, 'no-connect');
  assert.match(text(east), /\? not readable with this credential/);
  assert.match(text(east), /\? can't connect with this credential/);
  assert.match(text(east), /none set/, 'a measured-empty description reads "none set"');
  assert.match(text(east), /east_dba/);
  assert.doesNotMatch(text(east), /(^|[^\d])0 ?(B|bytes|MB)/, 'no zero stands in for the unread size');
  assert.equal(east.querySelector('input').disabled, true, 'listed and marked, but not registrable with this credential');
  assert.equal(east.querySelector('input').checked, false);
  assert.equal(east.classList.contains('opacity-50'), false, 'a marked row is shown, not dimmed away');

  const noOwner = row('no_owner');
  assert.match(text(noOwner), /not read/, 'an owner the source did not return is "not read", not blank');
  assert.match(text(noOwner), /not reported by this source/, 'a description never returned is not "none set"');
  assert.doesNotMatch(text(noOwner), /none set/);

  const hr = row('hr_archive');
  assert.match(text(hr), /ignored/);
  assert.match(text(hr), /archive/);
  assert.equal(hr.querySelector('input').disabled, false, 'a prior verdict informs, it does not block');

  const reg = row('already_in');
  assert.equal(reg.dataset.state, 'registered');
  assert.ok(reg.classList.contains('opacity-50'), 'already registered is dimmed');
  const box = reg.querySelector('input');
  assert.ok(box.checked && box.disabled, 'already registered is pre-checked and not re-addable');
  assert.match(text(reg), /already registered/);
  assert.match(text(reg), /recommended/);
});

test('"n new since" comes from the response: first run, then n new, then 0 new on an immediate re-run', async () => {
  const a = CAND('a'); const b = CAND('b');
  const first = RUN([a, b]);
  const second = RUN([a, b, CAND('c', { is_new: true })].map((c) => ({ ...c, is_new: c.is_new === true })), {
    first_run: false, previous_run_at: '2026-09-28T12:00:00', run_at: '2026-10-01T12:00:00', new_count: 1 });
  const third = RUN([a, b, CAND('c')].map((c) => ({ ...c, is_new: false })), {
    first_run: false, previous_run_at: '2026-10-01T12:00:00', run_at: '2026-10-01T12:05:00', new_count: 0 });
  const ctx = await setUp({ runs: [first, second, third] });
  const dlg = await openFind(ctx);
  const run = async () => { dlg.querySelector('[data-run="regional_pg"]').click(); await tick(); return text(dlg.querySelector('[data-run-meta]')); };

  const m1 = await run();
  assert.match(m1, /First run of Regional PG: 2 found/);
  assert.doesNotMatch(m1, /new since/, 'a first run has nothing to be new since');
  assert.match(text(dlg.querySelector('[data-source]')), /run \d\d-\d\d \d\d:\d\d · 2 found/, 'the saved row remembers the run');
  assert.match(text(dlg.querySelector('[data-source]')), /Run again/);

  const m2 = await run();
  assert.match(m2, /^1 new since \d\d-\d\d · 3 found/, 'another day: a date');
  assert.equal(dlg.querySelectorAll('[data-new]').length, 1, 'only the new database carries the mark');
  assert.match(text(dlg.querySelector('[data-cand$="/c"]')), /\bnew\b/);

  const m3 = await run();
  assert.match(m3, /^0 new since \d\d:\d\d · 3 found/, 'the same day: a time');
  assert.equal(dlg.querySelectorAll('[data-new]').length, 0);
});

test('no stored credential: the source says so and Run is disabled and never calls the server', async () => {
  const ctx = await setUp({ servers: [SERVER({ db_user: '' })] });
  const dlg = await openFind(ctx);
  assert.match(text(dlg.querySelector('[data-no-credential]')), /no credentials stored/);
  const run = dlg.querySelector('[data-run="regional_pg"]');
  assert.equal(run.disabled, true);
  run.click();
  await tick();
  assert.equal(ctx.calls.filter((c) => c.url.endsWith('/run')).length, 0);
  assert.equal(dlg.querySelector('[data-candidate-table]'), null);
});

test('signed out: a 401 is said as "Sign in to ...", for Run, one-off discover and the confirm', async () => {
  const ctx = await setUp({
    runs: [{ __http: 401, detail: 'Not authenticated' }],
    inline: { __http: 401, detail: 'Not authenticated' },
  });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  assert.match(text(dlg.querySelector('[data-run-error]')), /^Sign in to run "Regional PG"\.$/);
  assert.equal(dlg.querySelector('[data-candidate-table]'), null, 'no table, and no stale rows, after a refusal');
  await clickTab(dlg, 'discover');
  dlg.querySelector('[data-oo="host"]').value = 'h';
  dlg.querySelector('[data-oo="db_user"]').value = 'u';
  dlg.querySelector('[data-act="discover-inline"]').click();
  await tick();
  assert.match(text(dlg.querySelector('[data-run-error]')), /^Sign in to discover on this server\.$/);
});

test('a refused load of the saved sources is a message, not an empty list', async () => {
  const ctx = await setUp();
  const real = globalThis.fetch;
  globalThis.fetch = async (u, o) => (String(u) === '/api/db-servers/'
    ? { ok: false, status: 401, statusText: 'x', json: async () => ({ detail: 'Not authenticated' }) } : real(u, o));
  const dlg = await openFind(ctx);
  assert.match(text(dlg), /Sign in to load the saved sources\./);
  assert.doesNotMatch(text(dlg), /No saved sources yet/, 'a refusal is not "you have none"');
});

test('the one-off password is sent once, never rendered, and dropped when saved as a source', async () => {
  const ctx = await setUp({ inline: { host: 'pg.new', port: 5432, candidates: [CAND('a', { server_slug: null })] } });
  const dlg = await openFind(ctx);
  await clickTab(dlg, 'discover');
  dlg.querySelector('[data-oo="host"]').value = 'pg.new';
  dlg.querySelector('[data-oo="db_user"]').value = 'u';
  dlg.querySelector('[data-oo="db_password"]').value = SECRET;
  dlg.querySelector('[data-act="discover-inline"]').click();
  await tick();
  const sent = ctx.calls.find((c) => c.url === '/api/db-servers/_discover-inline');
  assert.equal(sent.body.db_password, SECRET, 'the connection itself needs it');
  assert.equal(dlg.outerHTML.includes(SECRET), false, 'but it is never in the rendered markup');
  assert.equal(dlg.querySelector('[data-oo="db_password"]').value, SECRET, 'the box keeps it as a property across re-renders');
  assert.match(text(dlg.querySelector('[data-needs-save]')), /Save this server as a source/);
  assert.equal(dlg.querySelector('[data-act="confirm"]').disabled, true, 'cannot register from an unsaved server');

  dlg.querySelector('[data-oo="slug"]').value = 'regional-pg-2';
  dlg.querySelector('[data-oo="display_name"]').value = 'Regional 2';
  dlg.querySelector('[data-act="save-source"]').click();
  await tick();
  const reg = ctx.calls.find((c) => c.url === '/api/db-servers/register');
  assert.equal(reg.body.slug, 'regional-pg-2');
  assert.equal(reg.body.db_password, SECRET);
  assert.equal(dlg.outerHTML.includes(SECRET), false);
  assert.equal(dlg.querySelector('[data-oo="db_password"]').value, '', 'dropped from memory once stored with the source');
  assert.match(text(dlg), /Saved as the source "regional-pg-2"/);
  assert.equal(dlg.querySelector('[data-needs-save]'), null);
});

test('confirm: registers the selected, skips registered and unconnectable, offers the destination, adds to the investigation', async () => {
  const cands = [
    CAND('a_forecast'), CAND('b_forecast'),
    CAND('region_east', { can_connect: false, size_pretty: null, size_bytes: null }),
    CAND('already_in', { is_registered: true, registered_slug: 'x' }),
  ];
  const ctx = await setUp({ runs: [RUN(cands)], groups: [{ slug: 'regional', display_name: 'Regional sales' }] },
    { investigation: 'c360', investigations: [
      { slug: 'c360', display_name: 'Customer 360', status: 'active' },
      { slug: 'old', display_name: 'Old one', status: 'closed' }] });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();

  assert.match(text(dlg.querySelector('[data-selection-count]')), /^0 selected · 1 already registered · 1 can't connect with this credential$/);
  const sel = dlg.querySelector('[data-act="investigation"]');
  assert.deepEqual([...sel.options].map((o) => o.value), ['', 'c360'], 'a closed investigation is not offered');
  assert.equal(sel.value, 'c360', 'the sidebar\'s investigation is the default destination');
  assert.equal(dlg.querySelector('[data-act="group"]').value, 'regional', 'the source\'s group is the default group');
  assert.equal(dlg.querySelector('[data-act="confirm"]').disabled, true, 'nothing selected, nothing to confirm');

  dlg.querySelector('[data-act="select-all-new"]').click();
  await tick(5);
  assert.match(text(dlg.querySelector('[data-selection-count]')), /^2 selected/);
  const btn = dlg.querySelector('[data-act="confirm"]');
  assert.equal(text(btn), 'Add these 2 to Customer 360');
  btn.click();
  await tick(60);

  const adds = ctx.calls.filter((c) => c.url.includes('/add-database'));
  assert.deepEqual(adds.map((c) => decodeURIComponent(c.url.split('database_name=')[1])).sort(), ['a_forecast', 'b_forecast'],
    'the unconnectable and the already-registered are never sent');
  const members = ctx.calls.filter((c) => /\/api\/investigations\/c360\/members$/.test(c.url));
  assert.equal(members.length, 2);
  assert.ok(members.every((m) => m.body.entity_type === 'database' && m.body.state === 'in-scope'));
  assert.match(members[0].body.membership_rationale, /^Found by Regional PG on \d{4}-\d\d-\d\d$/);
  assert.equal(ctx.calls.filter((c) => /\/group$/.test(c.url)).length, 2, 'the chosen group is applied to each');
  assert.match(text(dlg.querySelector('[data-outcome]')), /Registered 2 database\(s\) from "Regional PG"\. Added 2 to Customer 360\./);
  assert.ok(ctx.calls.every((c) => !/egeria/i.test(c.url) || /db-servers\/(register)/.test(c.url)), 'nothing is written to Egeria');
});

test('confirm with no investigation chosen says Register these N and adds to no scope', async () => {
  const ctx = await setUp({ runs: [RUN([CAND('a')])] }, { investigation: '' });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  dlg.querySelector('[data-act="select-all-new"]').click();
  await tick(5);
  assert.equal(text(dlg.querySelector('[data-act="confirm"]')), 'Register these 1');
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(60);
  assert.equal(ctx.calls.filter((c) => c.url.includes('/members')).length, 0);
  assert.match(text(dlg.querySelector('[data-outcome]')), /^Registered 1 database\(s\)/);
});

test('a refused registration is reported as a failure, not as success', async () => {
  const ctx = await setUp({ runs: [RUN([CAND('a')])] });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  dlg.querySelector('[data-act="select-all-new"]').click();
  await tick(5);
  const real = globalThis.fetch;
  globalThis.fetch = async (u, o) => (String(u).includes('/add-database')
    ? { ok: false, status: 401, statusText: 'x', json: async () => ({ detail: 'Not authenticated' }) } : real(u, o));
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(60);
  const out = dlg.querySelector('[data-outcome]');
  assert.match(text(out), /No databases were registered\./);
  assert.match(text(out), /sign in to register/);
  assert.ok(out.classList.contains('text-state-warn'));
});

test('Admin → the GitHub-only pane is labelled "Repository discovery sources"', async () => {
  const { document } = makeDomEnvironment();
  makeBackend();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  const admin = await import('/static/next/admin/index.js');
  await admin.openAdminPanel();
  const opt = [...document.querySelectorAll('#admin-panel-subnav option')]
    .find((o) => o.value === 'admin-discovery-sources');
  assert.ok(opt, 'the pane is still reachable under its id');
  assert.equal(opt.textContent.trim(), '🔍 Repository discovery sources');
  assert.doesNotMatch(document.getElementById('admin-panel-subnav').textContent, /🔍 Discovery Sources/);
  const sel = opt.parentElement;
  sel.value = 'admin-discovery-sources';
  sel.dispatchEvent(new window.Event('change'));
  await tick(80);
  assert.match(text(document.getElementById('admin-panel-body').querySelector('h3')), /^🔍 Repository discovery sources$/);
});
