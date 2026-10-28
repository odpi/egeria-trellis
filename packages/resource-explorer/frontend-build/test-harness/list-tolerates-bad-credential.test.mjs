/** Behaviour: one database whose stored credential is unreadable is listed
 *  beside the good rows as 'credential unreadable · re-enter credentials', and
 *  every control that would connect with it is disabled with that reason.
 *
 *  Runs the REAL app.js and the REAL router (clicks the real sidebar Find
 *  button) and reads the DOM. The marker comes from the response
 *  (`credential_status`), never inferred; only fetch is stubbed. No network.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const WORDS = 'credential unreadable · re-enter credentials';
const SECRET = 'hunter2-do-not-leak';

const DB = (slug, over = {}) => ({
  slug, display_name: slug.toUpperCase(), db_type: 'postgresql', host: 'pg.regional', port: 5432,
  database_name: slug, description: '', status: 'active', last_surveyed_at: '', schema_count: null,
  table_count: null, column_count: null, server_slug: 'regional_pg', egeria_asset_guid: '', db_user: 'u',
  group_slug: '', disposition: 'undecided', working_set_hidden: false, is_published: false,
  credential_status: 'ok', credential_reason: '', ...over,
});
const BAD = () => DB('bad_one', { credential_status: 'unreadable', credential_reason: WORDS });

const SERVER = {
  slug: 'regional_pg', display_name: 'Regional PG', db_type: 'postgresql', host: 'pg.regional', port: 5432,
  description: '', db_user: 'scout_ro', egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '',
  status: 'active', registered_at: '2026-09-01T00:00:00', group_slug: '', databases: [],
  last_run_at: null, last_run_candidate_count: null,
};
const CAND = (name, over = {}) => ({
  name, key: `pg.regional:5432/${name}`, address: `pg.regional:5432/${name}`, server_slug: 'regional_pg',
  can_connect: true, size_pretty: '10 MB', size_bytes: 1, owner: 'postgres', description: '', encoding: 'UTF8',
  is_registered: false, registered_slug: null, verdict: null, is_new: null, credential_status: null, ...over,
});

function stub(run) {
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && u === '/api/db-servers/') return ok([SERVER]);
    if (method === 'GET' && u.startsWith('/api/investigations/')) return ok([]);
    if (method === 'POST' && /\/api\/db-servers\/[^/]+\/run$/.test(u)) return ok(run);
    return ok(u.includes('/groups') || u.includes('/projects/') || u.includes('/databases/') ? [] : {});
  };
}

async function setUp(rows, run) {
  const { document, window } = makeDomEnvironment();
  stub(run);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.databases = rows;
  app.state.databasesLoaded = true;
  app.state.groups = [];
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.scope = '';
  app.state.filter = '';
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  app.renderSidebar();
  return { document, window, app };
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('the DBs sidebar lists the unreadable row beside the good rows, marked from the response', async () => {
  const ctx = await setUp([DB('good_one'), BAD(), DB('good_two')]);
  const rows = [...ctx.document.querySelectorAll('#sidebar [data-slug]')];
  assert.deepEqual(rows.map((r) => r.dataset.slug).sort(), ['bad_one', 'good_one', 'good_two']);
  const bad = ctx.document.querySelector('#sidebar [data-slug="bad_one"]');
  assert.match(text(bad), new RegExp(WORDS));
  assert.equal(bad.querySelector('[data-credential="unreadable"]').textContent, WORDS);
  for (const g of ['good_one', 'good_two']) {
    const el = ctx.document.querySelector(`#sidebar [data-slug="${g}"]`);
    assert.equal(el.querySelector('[data-credential]'), null, `${g} must not carry the marker`);
  }
  assert.doesNotMatch(ctx.document.body.textContent, new RegExp(SECRET));
});

test('the marker is read from the response, not inferred: a row without it is not marked', async () => {
  const noField = DB('plain');
  delete noField.credential_status; delete noField.credential_reason;
  const ctx = await setUp([noField]);
  assert.equal(ctx.document.querySelector('#sidebar [data-credential]'), null);
});

test('the selected unreadable database shows the marker and its survey/Run controls are disabled with the reason', async () => {
  const ctx = await setUp([DB('good_one'), BAD()]);
  ctx.app.state.selectedSlug = 'bad_one';
  const header = ctx.document.createElement('div');
  header.innerHTML = ctx.app.resourceHeaderHtml('bad_one');
  assert.match(text(header.querySelector('[data-credential-banner]')), new RegExp(WORDS));

  const holder = ctx.document.createElement('div');
  holder.innerHTML = ctx.app.surveyRowHtml({ qualified_name: 'q::one', display_name: 'One', steps: [] })
    + ctx.app.enrichmentAnalysisRowHtml({ id: 'a1', name: 'a1', unlocked: true, runnable: true, state: 'unrun' });
  for (const sel of ['[data-run-survey]', '[data-run-enrichment-analysis]']) {
    const b = holder.querySelector(sel);
    assert.ok(b, sel);
    assert.equal(b.disabled, true, `${sel} must be disabled`);
    assert.equal(b.title, WORDS);
  }

  ctx.app.state.selectedSlug = 'good_one';
  holder.innerHTML = ctx.app.surveyRowHtml({ qualified_name: 'q::one', display_name: 'One', steps: [] });
  assert.equal(holder.querySelector('[data-run-survey]').disabled, false, 'a good database keeps its Run');
});

test('the Find dialog marks a registered database whose credential is unreadable, beside the good ones', async () => {
  const run = {
    server_slug: 'regional_pg', run_at: '2026-10-01T12:00:00', previous_run_at: null, first_run: true,
    candidate_count: 3, new_count: null,
    candidates: [
      CAND('bad_one', { is_registered: true, registered_slug: 'bad_one', credential_status: 'unreadable' }),
      CAND('good_one', { is_registered: true, registered_slug: 'good_one', credential_status: 'ok' }),
      CAND('fresh'),
    ],
  };
  const ctx = await setUp([DB('good_one'), BAD()], run);
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  const dlg = ctx.document.getElementById('wl-detail');
  dlg.querySelector('[data-run="regional_pg"]').click();
  await tick();
  const row = (n) => dlg.querySelector(`[data-cand="pg.regional:5432/${n}"]`);
  assert.match(text(row('bad_one')), new RegExp(WORDS));
  assert.equal(row('bad_one').querySelector('input').disabled, true);
  assert.equal(row('good_one').querySelector('[data-credential]'), null);
  assert.equal(row('fresh').querySelector('[data-credential]'), null);
  assert.equal(row('fresh').querySelector('input').disabled, false, 'the new candidate stays selectable');
});
