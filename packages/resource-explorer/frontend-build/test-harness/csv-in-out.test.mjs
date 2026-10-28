/** CSV in and out, slice 2 of the discovery sources work (REPLY-DESIGNER-DISCOVERY-
 *  SOURCES-ALL-KINDS.md sections 3 and 4).
 *
 *  Real app.js, real router, real investigation pane, real Find dialog; only fetch
 *  is stubbed. The stub answers the preview with payloads that
 *  batch_io.preview_file produced (csv-in-out.fixture.json, generated from the
 *  Python side, not hand-written), so the shapes are the server's own.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FX = JSON.parse(readFileSync(new URL('./csv-in-out.fixture.json', import.meta.url), 'utf8'));
const CELL_SECRET = 'csv-cell-pw-do-not-leak';
const SECRET = 'hunter2-do-not-leak';

const SERVER = () => ({
  slug: 'regional_pg', display_name: 'Regional PG', db_type: 'postgresql', host: 'pg.regional', port: 5432,
  description: '', db_user: 'scout_ro', egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '',
  status: 'active', registered_at: '2026-09-01T00:00:00', group_slug: 'regional', databases: [],
  last_run_at: null, last_run_candidate_count: null,
});
const INVS = [
  { slug: 'c360', display_name: 'Customer 360', status: 'open' },
  { slug: 'other', display_name: 'Other one', status: 'open' },
];
const INV = (slug) => ({ slug, display_name: slug === 'c360' ? 'Customer 360' : 'Other one', status: 'open',
  description: `desc-${slug}`, project_classification: 'StudyProject', purposes: [], visibility: 'public' });
const CAND = (name) => ({
  name, key: `pg.regional:5432/${name}`, address: `pg.regional:5432/${name}`, server_slug: 'regional_pg',
  can_connect: true, size_pretty: '10 MB', size_bytes: 10485760, owner: 'postgres', description: '',
  encoding: 'UTF8', is_registered: false, registered_slug: null, verdict: null, is_new: null,
});

/** The file text -> the fixture payload the server would have produced. */
function previewFor(body) {
  if (!body.text.includes('resource_type') || !body.text.includes('address')) return FX.refused;
  if (body.text.startsWith('resource_type,address,group,disposition')) return FX.proposed;
  if (body.server_choices && Object.keys(body.server_choices).length) return FX.chosen;
  // c360 already holds the orders database (FX.reconfirm is the server's own payload for it)
  if (body.investigation === 'c360') return FX.reconfirm;
  return FX.preview;
}

function makeBackend({ members = {}, onImport = null } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body, raw: options.body || '' });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const csv = (text, filename) => ({
      ok: true, status: 200, text: async () => text,
      headers: { get: (h) => (h.toLowerCase() === 'content-disposition' ? `attachment; filename="${filename}"` : '') },
    });
    if (method === 'GET' && u === '/api/db-servers/') return ok([SERVER()]);
    if (method === 'GET' && u.startsWith('/api/projects/groups')) return ok([{ slug: 'regional', display_name: 'Regional' }]);
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok(INVS);
    if (method === 'POST' && u === '/api/discovery/from-file/preview') return ok(previewFor(body));
    if (method === 'POST' && u === '/api/discovery/from-file/import') {
      if (onImport) return onImport(body, ok);
      const ready = FX.preview.lines.new.filter((i) => !i.needs_person).map((i) => i.line);
      const known = FX.preview.lines.already_registered.map((i) => i.line);
      const registered = body.lines.filter((l) => ready.includes(l));
      const scoped = body.investigation ? body.lines.filter((l) => ready.includes(l) || known.includes(l)) : [];
      if (body.investigation) {
        const slugOf = { 2: 'regional_pg_sales', 8: 'pg_regional_5432_ref_only', 3: 'regional_pg_orders' };
        scoped.forEach((l) => {
          (members[body.investigation] = members[body.investigation] || [])
            .push({ entity_type: 'database', entity_slug: slugOf[l] || `row_${l}` });
        });
      }
      return ok({ registered: registered.map((l) => ({ line: l })), scoped: scoped.map((l) => ({ line: l })),
        changed: (body.accept_changes || []).map((c) => ({ line: c.line, field: c.field })), failures: [],
        counts: { registered: registered.length, scoped: scoped.length, changed: (body.accept_changes || []).length, failed: 0 } });
    }
    if (method === 'POST' && u === '/api/discovery/candidates.csv') {
      return csv('resource_type,address\ndatabase,pg.regional:5432/a_db\n', 're-candidates-regional-pg-2026-10-02.csv');
    }
    if (method === 'GET' && /\/api\/investigations\/[^/]+\/scope\.csv$/.test(u)) {
      return csv('resource_type,address\n', 're-scope-c360-2026-10-02.csv');
    }
    if (method === 'POST' && /\/run$/.test(u)) {
      return ok({ server_slug: 'regional_pg', run_at: '2026-10-02T12:00:00', previous_run_at: null, first_run: true,
        candidate_count: 2, new_count: null, candidates: [CAND('a_db'), CAND('b_db')] });
    }
    let m = u.match(/^\/api\/investigations\/([^/?]+)\/members$/);
    if (m && method === 'GET') return ok([...(members[m[1]] || [])]);
    m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m && method === 'GET' && !['purposes', 'classifications'].includes(m[1])) return ok(INV(m[1]));
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') {
      return ok({ classifications: [], bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' });
    }
    return ok(u.includes('/groups') || u.includes('/projects/') || u.includes('/databases/') ? [] : {});
  };
  return calls;
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp(backendOpts = {}, { investigation = '' } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(backendOpts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const downloads = [];
  window.HTMLAnchorElement.prototype.click = function click() { downloads.push({ name: this.download, href: this.href }); };
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const inv = await import('/static/next/stages/investigation.js');
  app.state.resourceType = 'db';
  app.state.databases = [];
  app.state.databasesLoaded = true;
  app.state.groups = [];
  app.state.investigations = INVS;
  app.state.investigation = investigation;
  app.state.selected = new Set();
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  for (const id of ['scope-slug', 'investigation-name', 'whoami', 'activity-count']) {
    const e = document.createElement('span'); e.id = id; document.body.appendChild(e);
  }
  const sw = document.createElement('a'); sw.id = 'switch-ui'; document.body.appendChild(sw);
  app.renderSidebar();
  return { document, window, app, inv, calls, content, downloads };
}

async function openFileTab(ctx) {
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  const dlg = ctx.document.getElementById('wl-detail');
  dlg.querySelector('[data-tab="file"]').click();
  await tick();
  return dlg;
}

/** Choose a file the way the browser does: input.files, then a change event. */
async function chooseFile(ctx, dlg, csv, name = 'regional.csv') {
  const input = dlg.querySelector('[data-file-input]');
  assert.ok(input, 'the From-a-file tab has a file chooser');
  Object.defineProperty(input, 'files', { value: [{ name, text: async () => csv }], configurable: true });
  input.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  await tick(60);
}

const previews = (ctx) => ctx.calls.filter((c) => c.url === '/api/discovery/from-file/preview');
const imports = (ctx) => ctx.calls.filter((c) => c.url === '/api/discovery/from-file/import');

/* ── the preview ─────────────────────────────────────────────────────────── */

test('choosing a file shows the five counts, and each count opens its lines', async () => {
  const ctx = await setUp();
  const dlg = await openFileTab(ctx);
  assert.equal(dlg.querySelector('[data-counts]'), null, 'nothing is previewed before a file is chosen');
  await chooseFile(ctx, dlg, FX.csv);
  assert.equal(previews(ctx).length, 1);
  assert.equal(previews(ctx)[0].body.text.split('\n')[0], FX.csv.split('\n')[0], 'the server is sent the file text');

  const counts = text(dlg.querySelector('[data-counts]'));
  assert.equal(counts, '9 rows · 3 new (⚠ 1 need a person) · 1 already registered · 1 duplicate in the file · 2 invalid · 2 of a kind not importable here');

  // "new" is open by default: its lines carry their line numbers
  const newRows = [...dlg.querySelectorAll('[data-lines="new"] [data-file-row]')].map((r) => r.dataset.fileRow);
  assert.deepEqual(newRows, ['2', '8', '4']);

  // each of the other four opens its own lines, with the reason
  for (const [key, lines, reason] of [
    ['already_registered', ['3'], /already registered/],
    ['duplicate_in_file', ['5'], /duplicate of an earlier row/],
    ['invalid', ['6', '10'], /host:port\/name/],
    ['not_importable', ['7', '9'], /no file-system import path/],
  ]) {
    dlg.querySelector(`[data-count="${key}"]`).click();
    await tick(5);
    const rows = [...dlg.querySelectorAll(`[data-lines="${key}"] [data-file-row]`)];
    assert.deepEqual(rows.map((r) => r.dataset.fileRow), lines, key);
    assert.match(text(dlg.querySelector(`[data-lines="${key}"]`)), reason, key);
    assert.match(text(dlg.querySelector(`[data-lines="${key}"]`)), /line \d+/);
  }
  assert.doesNotMatch(text(dlg), /file share/i, 'the kind is "file system", never "file share"');
});

test('known-negative: a count of zero is plain text, not a button that opens nothing', async () => {
  const ctx = await setUp();
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  // a payload with no duplicates and no invalid lines
  const clean = JSON.parse(JSON.stringify(FX.preview));
  clean.counts.duplicate_in_file = 0; clean.counts.invalid = 0;
  clean.lines.duplicate_in_file = []; clean.lines.invalid = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, o) => (String(url).endsWith('/from-file/preview')
    ? { ok: true, status: 200, json: async () => clean } : real(url, o));
  await chooseFile(ctx, dlg, FX.csv);
  assert.equal(dlg.querySelector('[data-count="duplicate_in_file"]'), null);
  assert.ok(dlg.querySelector('[data-count-zero="duplicate_in_file"]'));
  assert.match(text(dlg.querySelector('[data-counts]')), /0 duplicates in the file · 0 invalid/);
});

test('unknown, status_ and credential columns are said once; a credential cell never leaves the browser', async () => {
  const ctx = await setUp();
  const dlg = await openFileTab(ctx);
  assert.match(FX.csv, new RegExp(CELL_SECRET), 'the fixture file really does carry a password cell');
  await chooseFile(ctx, dlg, FX.csv);
  const msgs = [...dlg.querySelectorAll('[data-file-message]')].map(text);
  assert.deepEqual(msgs, [
    'Ignored columns: region',
    '1 status_ column ignored: those are written by RE, never read',
    'Credential column(s) ignored: db_password. A CSV names where a credential comes from (server or connection_ref), never the credential.',
  ]);
  // nothing posted anywhere contains the cell value, and the header is still sent
  for (const c of ctx.calls) assert.ok(!c.raw.includes(CELL_SECRET), `${c.method} ${c.url} carried the credential cell`);
  assert.match(previews(ctx)[0].body.text, /db_password/, 'the header stays so the server can name the column');
  assert.ok(!text(dlg).includes(CELL_SECRET) && !text(dlg).includes(SECRET));
});

test('csv-guard blanks only credential cells, keeps line numbers, and mirrors the server pattern', async () => {
  {
    const { blankCredentialCells, isCredentialColumn } = await import('/static/next/csv-guard.js');
    const src = 'resource_type,address,Password,connection_ref,"api_token",notes\n'
      + '# a comment\n\n'
      + 'database,h:1/x,p1,corp-entry,t1,"multi\nline"\n'
      + 'database,h:1/y,"p,2",,t2,ok\r\n';
    const out = blankCredentialCells(src);
    assert.deepEqual(out.columns, ['Password', 'api_token']);
    assert.equal(out.text, 'resource_type,address,Password,connection_ref,"api_token",notes\n'
      + '# a comment\n\n'
      + 'database,h:1/x,,corp-entry,,"multi\nline"\n'
      + 'database,h:1/y,,,,ok\r\n');
    assert.equal(src.split('\n').length, out.text.split('\n').length, 'line numbers do not move');
    assert.equal(isCredentialColumn('connection_ref'), false);
    for (const n of ['password', 'db_password', 'client_secret', 'credentials', 'auth_token', 'api_key', 'dsn', 'connection_string']) {
      assert.equal(isCredentialColumn(n), true, n);
    }
    for (const n of ['server', 'address', 'disposition_reason', 'group', 'status_slug']) assert.equal(isCredentialColumn(n), false, n);
    const js = readFileSync(new URL('../../resource_explorer/web/static/next/csv-guard.js', import.meta.url), 'utf8');
    const py = readFileSync(new URL('../../resource_explorer/batch_io.py', import.meta.url), 'utf8');
    const jsPat = js.match(/const CREDENTIAL_COLUMN = \/(.*)\/i;/)[1];
    const pyPat = (py.match(/_CREDENTIAL_COLUMN_RE = _re\.compile\(\s*((?:r"[^\n]*"\s*)+),/s)[1]
      .match(/r"([^"]*)"/g) || []).map((s) => s.slice(2, -1)).join('');
    assert.equal(jsPat, pyPat, 'the JS and Python credential-column patterns are the same string');
  }
});

test('a file missing a required column is refused, naming the column and showing the header row', async () => {
  const ctx = await setUp();
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, 'name,url\nfoo,bar\n');
  const refused = dlg.querySelector('[data-file-refused]');
  assert.ok(refused, 'the file is refused');
  assert.match(text(refused), /missing the required column\(s\): resource_type, address/);
  assert.equal(text(dlg.querySelector('[data-file-header]')), 'name, url');
  assert.equal(dlg.querySelector('[data-counts]'), null, 'no counts for a refused file');
  assert.equal(dlg.querySelector('[data-confirm]'), null, 'and nothing to confirm');
});

test('a row that names no server needs a person: not importable until a server is chosen in the preview', async () => {
  const ctx = await setUp();
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  assert.match(text(dlg.querySelector('[data-lines="new"] [data-file-row="4"]')), /needs a person: name a server or a credential/);
  assert.equal(dlg.querySelector('[data-lines="new"] [data-file-row="4"] input[type="checkbox"]'), null,
    'a needs-a-person row cannot be ticked');
  const sel = dlg.querySelector('[data-choose-server="4"]');
  assert.ok(sel);
  assert.match(text(sel), /Regional PG \(pg\.regional:5432\) · same host/);
  sel.value = 'regional_pg';
  sel.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  await tick(40);
  const last = previews(ctx).at(-1);
  assert.deepEqual(last.body.server_choices, { 4: 'regional_pg' }, 'the server re-plans with the choice');
  assert.equal(dlg.querySelector('[data-needs-person-count]'), null);
  assert.ok(dlg.querySelector('[data-lines="new"] [data-file-row="4"] input[type="checkbox"]'), 'now it can be ticked');
});

/* ── the confirm ─────────────────────────────────────────────────────────── */

test('confirm: "Add these N to <investigation>" through the shared confirm; the open investigation page then shows the members', async () => {
  const members = { c360: [] };
  const ctx = await setUp({ members }, { investigation: 'c360' });
  ctx.app.state.stage = 'investigation';
  ctx.inv.openInvestigationDetail('c360');
  await ctx.inv.renderInvestigation();
  assert.equal(ctx.content.querySelectorAll('[data-remove-member]').length, 0);

  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  // 2 ready new rows; the 1 already-registered row is ALREADY in Customer 360, so it is not offered
  const btn = dlg.querySelector('[data-act="confirm"]');
  assert.match(text(btn), /^Add these 2 to Customer 360$/);
  assert.equal(dlg.querySelector('[data-act="investigation"]').value, 'c360', 'the sidebar investigation is the default destination');

  btn.click();
  await tick(120);
  const imp = imports(ctx);
  assert.equal(imp.length, 1);
  assert.deepEqual(imp[0].body.lines.sort((a, b) => a - b), [2, 8]);
  assert.equal(imp[0].body.investigation, 'c360');
  for (const c of ctx.calls) assert.ok(!c.raw.includes(CELL_SECRET), 'no request carried the credential cell');
  assert.match(text(dlg.querySelector('[data-outcome]')), /Registered 2 database\(s\)\. Added 2 to Customer 360\./);
  // the open page shows them, with no navigation
  const shown = [...ctx.content.querySelectorAll('[data-remove-member]')].map((b) => b.dataset.removeMember.split('|')[1]).sort();
  assert.deepEqual(shown, ['pg_regional_5432_ref_only', 'regional_pg_sales']);
});

test('known-negative: with no investigation chosen the button says Register and no investigation is sent', async () => {
  const ctx = await setUp({}, { investigation: '' });
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  const btn = dlg.querySelector('[data-act="confirm"]');
  assert.match(text(btn), /^Register these 2$/, 'already-registered rows only count once there is a scope to add them to');
  btn.click();
  await tick(80);
  assert.equal(imports(ctx)[0].body.investigation, '');
  assert.deepEqual(imports(ctx)[0].body.lines.sort((a, b) => a - b), [2, 8]);
});

test('unticking a row removes it from N and from the lines sent', async () => {
  const ctx = await setUp({}, { investigation: 'c360' });
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  dlg.querySelector('[data-file-line="8"]').click();
  await tick(5);
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Add these 1 to Customer 360$/);
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(80);
  assert.deepEqual(imports(ctx)[0].body.lines.sort((a, b) => a - b), [2]);
});

test('proposed changes on already-registered rows are shown, unticked, and applied only if ticked at confirm', async () => {
  const ctx = await setUp({}, { investigation: '' });
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, 'resource_type,address,group,disposition\nx');
  const box = dlg.querySelector('[data-proposed]');
  assert.ok(box);
  assert.match(text(box), /1 row would change group · 1 row would change disposition/);
  assert.match(text(box), /group regional → other/);
  assert.match(text(box), /Proposed, not applied/);
  const boxes = [...box.querySelectorAll('[data-accept-change]')];
  assert.equal(boxes.length, 2);
  assert.ok(boxes.every((b) => !b.checked), 'nothing is applied unless the person ticks it');
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Nothing to add$/);
  assert.equal(dlg.querySelector('[data-act="confirm"]').disabled, true);

  box.querySelector('[data-accept-change="2|group"]').click();
  await tick(5);
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Apply these 1 change$/);
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(80);
  assert.deepEqual(imports(ctx)[0].body.accept_changes, [{ line: 2, field: 'group' }]);
  assert.match(text(dlg.querySelector('[data-outcome]')), /Changed 1 row\(s\)\./);
});

/* ── the door on the investigation page ──────────────────────────────────── */

test('Scope "＋ add…" offers "from a file": the Find dialog opens on its From-a-file tab with the destination preset', async () => {
  const ctx = await setUp({}, { investigation: '' });
  ctx.app.state.stage = 'investigation';
  ctx.inv.openInvestigationDetail('other');
  await ctx.inv.renderInvestigation();
  const addBtn = ctx.content.querySelector('[data-act="inv-add-menu"]');
  assert.ok(addBtn, 'the Members section has a "＋ add…" control');
  assert.match(text(addBtn), /＋ add…/);
  assert.equal(ctx.content.querySelector('[data-act="inv-add-from-file"]'), null, 'the menu is closed until asked');
  addBtn.click();
  await tick(5);
  const door = ctx.content.querySelector('[data-act="inv-add-from-file"]');
  assert.match(text(door), /from a file/);
  door.click();
  await tick(80);
  const dlg = ctx.document.getElementById('wl-detail');
  assert.ok(dlg, 'the dialog opened');
  assert.match(text(dlg), /Find databases/);
  assert.ok(dlg.querySelector('[data-file-input]'), 'on the From-a-file tab');
  assert.equal(dlg.querySelector('[data-tab="file"]').className.includes('border-accent'), true);
  // destination preset: investigation "other" is the one chosen, though the sidebar has none
  assert.equal(ctx.app.state.investigation, '');
  await chooseFile(ctx, dlg, FX.csv);
  assert.equal(dlg.querySelector('[data-act="investigation"]').value, 'other');
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Add these 3 to Other one$/);
});

test('known-negative: the sidebar Find action still opens on Saved sources, not on the file tab', async () => {
  const ctx = await setUp();
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  const dlg = ctx.document.getElementById('wl-detail');
  assert.ok(dlg.querySelector('[data-source]'));
  assert.equal(dlg.querySelector('[data-file-input]'), null);
});

/* ── exports ─────────────────────────────────────────────────────────────── */

test('the candidate table exports CSV: the rows on screen are posted, no credential, the server names the file', async () => {
  const ctx = await setUp();
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  const dlg = ctx.document.getElementById('wl-detail');
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick(60);
  dlg.querySelector('[data-act="export-candidates"]').click();
  await tick(60);
  const post = ctx.calls.find((c) => c.url === '/api/discovery/candidates.csv');
  assert.ok(post, 'the export was requested');
  assert.deepEqual(post.body.candidates.map((c) => c.address), ['pg.regional:5432/a_db', 'pg.regional:5432/b_db']);
  assert.equal(post.body.server_slug, 'regional_pg');
  assert.ok(!post.raw.includes(SECRET) && !/password/i.test(post.raw));
  assert.equal(ctx.downloads.length, 1);
  assert.equal(ctx.downloads[0].name, 're-candidates-regional-pg-2026-10-02.csv');
  assert.match(text(dlg.querySelector('[data-outcome]')), /Exported 2 candidate\(s\) as re-candidates-regional-pg-2026-10-02\.csv\./);
});

test('the investigation page exports its scope as CSV under the server-named file', async () => {
  const ctx = await setUp({ members: { c360: [{ entity_type: 'database', entity_slug: 'regional_pg_orders' }] } }, { investigation: 'c360' });
  ctx.app.state.stage = 'investigation';
  ctx.inv.openInvestigationDetail('c360');
  await ctx.inv.renderInvestigation();
  ctx.content.querySelector('[data-act="inv-export-scope"]').click();
  await tick(60);
  assert.ok(ctx.calls.find((c) => c.method === 'GET' && c.url === '/api/investigations/c360/scope.csv'));
  assert.equal(ctx.downloads[0].name, 're-scope-c360-2026-10-02.csv');
  assert.match(text(ctx.content.querySelector('#inv-scope-note')), /Downloaded re-scope-c360-2026-10-02\.csv\./);
});

/* ── re-confirming a file into the investigation it is already in ───────── */

test('re-confirm: rows already in the chosen investigation are said so, not counted, not offered', async () => {
  const ctx = await setUp({}, { investigation: 'c360' });
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  assert.equal(previews(ctx)[0].body.investigation, 'c360', 'the preview is asked about the chosen investigation');
  assert.match(text(dlg.querySelector('[data-counts]')), /1 already registered \(1 already in Customer 360\)/);
  dlg.querySelector('[data-count="already_registered"]').click();
  await tick(5);
  const row = dlg.querySelector('[data-lines="already_registered"] [data-file-row="3"]');
  assert.equal(row.querySelector('[data-file-line]'), null, 'no tick box: there is nothing to add for it');
  assert.match(text(row), /already in Customer 360, nothing to add/);
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Add these 2 to Customer 360$/);
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(80);
  assert.ok(!imports(ctx)[0].body.lines.includes(3), 'the already-member line is not sent');
});

test('re-confirm: when every row is already there the confirm says "Nothing to add" and is disabled', async () => {
  const ctx = await setUp({}, { investigation: 'c360' });
  const dlg = await openFileTab(ctx);
  const allThere = JSON.parse(JSON.stringify(FX.reconfirm));
  allThere.lines.new = []; allThere.counts.new = 0; allThere.counts.ready = 0; allThere.counts.needs_person = 0;
  const real = globalThis.fetch;
  globalThis.fetch = async (url, o) => (String(url).endsWith('/from-file/preview')
    ? { ok: true, status: 200, json: async () => allThere } : real(url, o));
  await chooseFile(ctx, dlg, FX.csv);
  const btn = dlg.querySelector('[data-act="confirm"]');
  assert.equal(text(btn), 'Nothing to add');
  assert.equal(btn.disabled, true);
  assert.doesNotMatch(text(dlg.querySelector('[data-confirm]')), /Add these/);
});

test('known-negative: a scope export into a SECOND investigation still offers its already-registered rows', async () => {
  const ctx = await setUp({}, { investigation: 'c360' });
  const dlg = await openFileTab(ctx);
  await chooseFile(ctx, dlg, FX.csv);
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Add these 2 to Customer 360$/);
  // choose the other investigation: the file is planned again against it
  const sel = dlg.querySelector('[data-act="investigation"]');
  sel.value = 'other';
  sel.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  await tick(80);
  assert.equal(previews(ctx).at(-1).body.investigation, 'other');
  assert.doesNotMatch(text(dlg.querySelector('[data-counts]')), /already in/);
  assert.match(text(dlg.querySelector('[data-act="confirm"]')), /^Add these 3 to Other one$/);
  dlg.querySelector('[data-act="confirm"]').click();
  await tick(80);
  assert.deepEqual(imports(ctx)[0].body.lines.sort((a, b) => a - b), [2, 3, 8]);
});
