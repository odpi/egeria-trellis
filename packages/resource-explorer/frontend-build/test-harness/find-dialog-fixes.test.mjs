/** Two defects found on the 8813 gate (2026-10-02) in the database Find dialog.
 *
 *  A. A failed load of the saved sources ("Sign in to load the saved sources.")
 *     stayed on screen after a later load succeeded.
 *  B. The investigation page, when it was the open pane, did not show members
 *     added by the dialog's confirm (or by the sidebar's scope buttons) until
 *     the person left and came back. Also: a late response for a different
 *     investigation overwrote the pane.
 *
 *  Real app.js, real router, real investigation pane; only fetch is stubbed.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

const SERVER = () => ({
  slug: 'regional_pg', display_name: 'Regional PG', db_type: 'postgresql', host: 'pg.regional', port: 5432,
  description: '', db_user: 'scout_ro', egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '',
  status: 'active', registered_at: '2026-09-01T00:00:00', group_slug: 'regional', databases: [],
  last_run_at: null, last_run_candidate_count: null,
});
const CAND = (name) => ({
  name, key: `pg.regional:5432/${name}`, address: `pg.regional:5432/${name}`, server_slug: 'regional_pg',
  can_connect: true, size_pretty: '10 MB', size_bytes: 10485760, owner: 'postgres', description: '',
  encoding: 'UTF8', is_registered: false, registered_slug: null, verdict: null, is_new: null,
});
const INVS = [
  { slug: 'c360', display_name: 'Customer 360', status: 'open' },
  { slug: 'other', display_name: 'Other one', status: 'open' },
];
const INV = (slug) => ({ slug, display_name: slug === 'c360' ? 'Customer 360' : 'Other one', status: 'open',
  description: `desc-${slug}`, project_classification: 'StudyProject', purposes: [], visibility: 'public' });

/** `members`: slug -> array, mutated by POST/DELETE. `gates`: slug -> promise the
 *  members GET awaits (to make a response late). `serversScript`: successive
 *  answers for GET /api/db-servers/ (number = http status, else a list). */
function makeBackend({ members = {}, gates = {}, serversScript = [[SERVER()]], runs = [] } = {}) {
  const calls = [];
  const servers = [...serversScript];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (method === 'GET' && u === '/api/db-servers/') {
      const next = servers.length > 1 ? servers.shift() : servers[0];
      return typeof next === 'number' ? err(next, 'Not authenticated') : ok(next);
    }
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok(INVS);
    let m = u.match(/^\/api\/investigations\/([^/?]+)\/members$/);
    if (m) {
      const slug = m[1];
      if (method === 'GET') { if (gates[slug]) await gates[slug]; return ok([...(members[slug] || [])]); }
      if (method === 'POST') {
        (members[slug] = members[slug] || []).push({ entity_type: body.entity_type, entity_slug: body.entity_slug });
        return ok([]);
      }
    }
    m = u.match(/^\/api\/investigations\/([^/?]+)\/members\/([^/]+)\/([^/]+)$/);
    if (m && method === 'DELETE') {
      members[m[1]] = (members[m[1]] || []).filter((x) => x.entity_slug !== decodeURIComponent(m[3]));
      return ok({});
    }
    m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m && method === 'GET' && !['purposes', 'classifications'].includes(m[1])) return ok(INV(m[1]));
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') {
      return ok({ classifications: [], bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' });
    }
    if (method === 'POST' && /\/run$/.test(u)) {
      const r = runs.shift();
      return ok(r);
    }
    const add = u.match(/^\/api\/db-servers\/([^/]+)\/add-database\?database_name=([^&]+)/);
    if (method === 'POST' && add) {
      return ok({ slug: decodeURIComponent(add[2]).replace(/_/g, '-'), database_name: decodeURIComponent(add[2]), server_slug: add[1] });
    }
    if (method === 'GET' && u.startsWith('/api/projects/groups')) return ok([]);
    return ok(u.includes('/groups') || u.includes('/projects/') || u.includes('/databases/') ? [] : {});
  };
  return calls;
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp(backendOpts = {}, { investigation = 'c360' } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(backendOpts);
  autoAnswerConfirm(document, { answer: true });   // the in-page confirm, answered yes
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
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
  app.state.selectMode = true;
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  for (const id of ['scope-slug', 'investigation-name', 'whoami', 'activity-count']) {
    const e = document.createElement('span'); e.id = id; document.body.appendChild(e);
  }
  app.renderSidebar();
  return { document, window, app, inv, calls, content };
}

async function openFind(ctx) {
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  return ctx.document.getElementById('wl-detail');
}

async function openPane(ctx, slug) {
  ctx.app.state.stage = 'investigation';
  ctx.inv.openInvestigationDetail(slug);
  await ctx.inv.renderInvestigation();
}

const memberSlugs = (ctx) => [...ctx.content.querySelectorAll('[data-remove-member]')]
  .map((b) => b.dataset.removeMember.split('|')[1]);

/* ── A: stale banner ─────────────────────────────────────────────────────── */

test('A: a refused load shows the sign-in message; a later successful load clears it and shows the list', async () => {
  const ctx = await setUp({ serversScript: [401, [SERVER()]] });
  const dlg = await openFind(ctx);
  assert.match(text(dlg), /Sign in to load the saved sources\./);
  assert.equal(dlg.querySelector('[data-source]'), null, 'no list while the load is refused');
  dlg.querySelector('[data-act="reload"]').click();
  await tick();
  assert.doesNotMatch(text(dlg), /Sign in to load the saved sources/, 'the stale banner is gone');
  assert.equal(dlg.querySelectorAll('[data-source]').length, 1, 'the list is shown');
});

test('A: known-negative: a failed load that is still failing keeps its message', async () => {
  const ctx = await setUp({ serversScript: [401] });
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-act="reload"]').click();
  await tick();
  assert.match(text(dlg), /Sign in to load the saved sources\./);
});

test('A: an unrelated status survives a successful load', async () => {
  const ctx = await setUp({ serversScript: [[SERVER()]] });
  const dlg = await openFind(ctx);
  // Registering a server sets "Registered server X." and then reloads the list.
  dlg.querySelector('[data-act="new-server"]')?.click();
  await tick();
  const set = (sel, v) => { const e = dlg.querySelector(sel); assert.ok(e, sel); e.value = v; };
  set('[data-f="slug"]', 'newsrv'); set('[data-f="display_name"]', 'New'); set('[data-f="host"]', 'h'); set('[data-f="db_user"]', 'u');
  dlg.querySelector('[data-act="submit-register"]').click();
  await tick(60);
  assert.match(text(dlg), /Registered server "newsrv"\./, 'a status that is not the load failure is kept after the reload');
});

/* ── B: investigation pane refresh ───────────────────────────────────────── */

async function runAndConfirm(ctx, names) {
  const dlg = await openFind(ctx);
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  dlg.querySelector('[data-act="select-all-new"]').click();
  await tick(5);
  const btn = dlg.querySelector('[data-act="confirm"]');
  assert.match(text(btn), new RegExp(`^Add these ${names.length} to Customer 360$`));
  btn.click();
  await tick(80);
  return dlg;
}

const RUN2 = () => ({
  server_slug: 'regional_pg', run_at: '2026-10-02T12:00:00', previous_run_at: null, first_run: true,
  candidate_count: 2, new_count: null, candidates: [CAND('a_db'), CAND('b_db')],
});

test('B: confirm in the Find dialog adds members and the OPEN investigation pane lists them without navigating', async () => {
  const members = { c360: [{ entity_type: 'database', entity_slug: 'old-db' }] };
  const ctx = await setUp({ members, runs: [RUN2()] });
  await openPane(ctx, 'c360');
  assert.deepEqual(memberSlugs(ctx), ['old-db']);
  await runAndConfirm(ctx, ['a_db', 'b_db']);
  assert.deepEqual(memberSlugs(ctx).sort(), ['a-db', 'b-db', 'old-db']);
  assert.match(text(ctx.content), /Scope · 3/);
});

test('B: known-negative: confirm into an investigation that is not the open one leaves the open pane alone and does not refetch it', async () => {
  const members = { c360: [{ entity_type: 'database', entity_slug: 'old-db' }], other: [] };
  const ctx = await setUp({ members, runs: [RUN2()] });
  await openPane(ctx, 'other');
  const before = ctx.calls.filter((c) => /\/investigations\/c360\/members$/.test(c.url) && c.method === 'GET').length;
  await runAndConfirm(ctx, ['a_db', 'b_db']);   // dialog default is the sidebar's c360
  assert.match(text(ctx.content), /desc-other/, 'still showing the other investigation');
  assert.equal(ctx.calls.filter((c) => /\/investigations\/c360\/members$/.test(c.url) && c.method === 'GET').length, before,
    'the open pane is not c360, so c360 is not fetched for display');
});

test('B: known-negative: confirm when the investigation pane is not the open stage does not paint into the page', async () => {
  const members = { c360: [] };
  const ctx = await setUp({ members, runs: [RUN2()] });
  await openPane(ctx, 'c360');
  ctx.app.state.stage = 'scouting';
  ctx.content.innerHTML = '<p id="scout">scouting pane</p>';
  await runAndConfirm(ctx, ['a_db', 'b_db']);
  assert.ok(ctx.content.querySelector('#scout'), 'another stage\'s pane is not overwritten');
});

test('B: the sidebar "＋ scope" bulk action refreshes the open investigation pane; "− scope" too', async () => {
  const members = { c360: [{ entity_type: 'database', entity_slug: 'old-db' }] };
  const ctx = await setUp({ members });
  await openPane(ctx, 'c360');
  ctx.app.state.selected = new Set(['new-db']);
  ctx.app.renderSidebar();
  ctx.document.querySelector('[data-act="sel-scope-add"]').click();
  await tick(60);
  assert.deepEqual(memberSlugs(ctx).sort(), ['new-db', 'old-db']);
  ctx.app.state.selected = new Set(['old-db']);
  ctx.app.renderSidebar();
  ctx.document.querySelector('[data-act="sel-scope-remove"]').click();
  await tick(60);
  assert.deepEqual(memberSlugs(ctx), ['new-db']);
});

test('B: the pane\'s own Add form and Remove button still update the list', async () => {
  const members = { c360: [{ entity_type: 'database', entity_slug: 'old-db' }] };
  const ctx = await setUp({ members });
  await openPane(ctx, 'c360');
  ctx.content.querySelector('#inv-add-slug').value = 'typed-db';
  ctx.content.querySelector('[data-act="inv-add-member"]').click();
  await tick(60);
  assert.deepEqual(memberSlugs(ctx).sort(), ['old-db', 'typed-db']);
  ctx.content.querySelector('[data-remove-member="database|old-db"]').click();
  await tick(60);
  assert.deepEqual(memberSlugs(ctx), ['typed-db']);
});

test('B: a late members response for a different investigation is dropped', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const members = { c360: [{ entity_type: 'database', entity_slug: 'c360-db' }], other: [{ entity_type: 'database', entity_slug: 'other-db' }] };
  const ctx = await setUp({ members, gates: { c360: gate } });
  ctx.app.state.stage = 'investigation';
  ctx.inv.openInvestigationDetail('c360');
  const slow = ctx.inv.renderInvestigation();      // c360's members are held back
  await tick();
  ctx.inv.openInvestigationDetail('other');
  await ctx.inv.renderInvestigation();
  assert.deepEqual(memberSlugs(ctx), ['other-db']);
  release();
  await slow;
  await tick(30);
  assert.deepEqual(memberSlugs(ctx), ['other-db'], 'c360\'s late response must not repaint the pane');
  assert.match(text(ctx.content), /desc-other/);
});
