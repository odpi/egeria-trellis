/** Behaviour: PI-021 (change a stored credential, test-before-save, then the
 *  omsecrets read-back) and PI-014 (the saved server's Egeria fields and each
 *  database's surveyed state, read only).
 *
 *  Real app.js and router, only fetch stubbed in the shapes of
 *  web/routes/databases.py and db_servers.py. The fake password must never
 *  reach the DOM, a URL, a row or a message. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FAKE_PW = 'test-password-not-real-NEW';
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const DRIFT_OK = { slug: 'coco', collection_name: 'coco::PostgreSQL Secret', in_registry: true, in_omsecrets: true, omsecrets_configured: true, in_sync: true };

function makeBackend({ patch = 'ok', drift = DRIFT_OK, servers = [] } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (method === 'PATCH' && u === '/api/databases/coco/credentials') {
      if (patch === 'refused') return err(400, 'Credential not saved: the server at pg:5432 refused this credential (FATAL: password authentication failed)');
      return ok({ slug: 'coco', db_user: body.db_user, credential_changed_at: new Date().toISOString() });
    }
    if (method === 'GET' && u === '/api/databases/coco/credential-drift') return ok(drift);
    if (method === 'GET' && u === '/api/db-servers/') return ok(servers);
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
  app.state.resourceType = 'db';
  app.state.databasesLoaded = true;
  app.state.databases = [{ slug: 'coco', display_name: 'Coco', db_user: 'old_user', credential_status: 'ok', is_published: true, disposition: 'undecided' }];
  app.state.selectedSlug = 'coco';
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  return { document, window, app, calls };
}

function typeInto(el, value) {
  el.value = value;
  el.dispatchEvent(new el.ownerDocument.defaultView.Event('input', { bubbles: true }));
}
const leakFree = (ctx) => {
  assert.ok(!ctx.document.documentElement.outerHTML.includes(FAKE_PW), 'password not in the DOM');
  for (const c of ctx.calls) assert.ok(!c.url.includes(FAKE_PW), `password not in URL ${c.url}`);
};

async function openChange(ctx) {
  const host = ctx.document.createElement('div');
  host.id = 'resource-header';
  ctx.document.body.appendChild(host);
  host.innerHTML = ctx.app.resourceHeaderHtml('coco');
  ctx.app.bindResourceHeader();
  const open = host.querySelector('[data-act="change-credentials"]');
  assert.ok(open, 'a database header offers Change credentials…');
  open.click();
  await tick();
  return host;
}

test('a database header offers "Change credentials…"; a repo header does not', async () => {
  const ctx = await setUp();
  const host = ctx.document.createElement('div');
  host.innerHTML = ctx.app.resourceHeaderHtml('coco');
  assert.match(text(host.querySelector('[data-act="change-credentials"]')), /Change credentials…/);
  ctx.app.state.resourceType = 'repo';
  ctx.app.state.projects = [{ slug: 'coco', display_name: 'Coco' }];
  host.innerHTML = ctx.app.resourceHeaderHtml('coco');
  assert.equal(host.querySelector('[data-act="change-credentials"]'), null);
});

test('the form prefills the current user, never a password, and Test and save patches then reads back the drift check', async () => {
  const ctx = await setUp();
  const host = await openChange(ctx);
  assert.equal(host.querySelector('[data-cred-user]').value, 'old_user');
  assert.equal(host.querySelector('[data-cred-password]').value, '');
  typeInto(host.querySelector('[data-cred-user]'), 'new_user');
  typeInto(host.querySelector('[data-cred-password]'), FAKE_PW);
  host.querySelector('[data-cred-save]').click();
  await tick(80);
  const patch = ctx.calls.find((c) => c.method === 'PATCH');
  assert.deepEqual(patch.body, { db_user: 'new_user', db_password: FAKE_PW });
  assert.match(text(host.querySelector('[data-cred-saved]')), /^✓ credential changed · new_user · just now/);
  assert.equal(text(host.querySelector('[data-cred-drift]')), 'in registry · in .omsecrets · in sync');
  assert.equal(host.querySelector('[data-cred-password]').value, '', 'the field is emptied once saved');
  assert.equal(ctx.app.state.databases[0].db_user, 'new_user', 'the list row follows the save');
  leakFree(ctx);
});

test('a refused credential says not saved with the server sentence and reads no drift', async () => {
  const ctx = await setUp({ patch: 'refused' });
  const host = await openChange(ctx);
  typeInto(host.querySelector('[data-cred-password]'), FAKE_PW);
  host.querySelector('[data-cred-save]').click();
  await tick(80);
  assert.match(text(host.querySelector('[data-cred-error]')), /^not saved · Credential not saved: the server at pg:5432 refused this credential/);
  assert.equal(host.querySelector('[data-cred-saved]'), null);
  assert.equal(ctx.calls.filter((c) => c.url.endsWith('/credential-drift')).length, 0);
  assert.equal(ctx.app.state.databases[0].db_user, 'old_user');
  leakFree(ctx);
});

test('drift is said as drift, and an unconfigured secrets path is said as not checked, never as in sync', async () => {
  let ctx = await setUp({ drift: { ...DRIFT_OK, in_omsecrets: false, in_sync: false } });
  let host = await openChange(ctx);
  typeInto(host.querySelector('[data-cred-password]'), FAKE_PW);
  host.querySelector('[data-cred-save]').click();
  await tick(80);
  assert.equal(text(host.querySelector('[data-cred-drift]')), 'in registry · not in .omsecrets · drift');

  ctx = await setUp({ drift: { ...DRIFT_OK, in_omsecrets: false, omsecrets_configured: false, in_sync: null } });
  host = await openChange(ctx);
  typeInto(host.querySelector('[data-cred-password]'), FAKE_PW);
  host.querySelector('[data-cred-save]').click();
  await tick(80);
  const line = text(host.querySelector('[data-cred-drift]'));
  assert.match(line, /\.omsecrets not checked/);
  assert.doesNotMatch(line, /in sync/);
});

test('both fields are required and a second press while pending is ignored', async () => {
  const ctx = await setUp();
  const host = await openChange(ctx);
  host.querySelector('[data-cred-save]').click();
  await tick();
  assert.match(text(host.querySelector('[data-cred-error]')), /both required/);
  assert.equal(ctx.calls.filter((c) => c.method === 'PATCH').length, 0);
  typeInto(host.querySelector('[data-cred-password]'), FAKE_PW);
  const btn = host.querySelector('[data-cred-save]');
  btn.click(); btn.click();
  await tick(80);
  assert.equal(ctx.calls.filter((c) => c.method === 'PATCH').length, 1);
});

// ── PI-014 + the per-database door in Find databases → Saved sources ─────────

const SERVER = {
  slug: 'regional_pg', display_name: 'Regional PG', db_type: 'postgresql', host: 'pg.regional', port: 5432,
  description: '', db_user: 'scout_ro', egeria_host: 'host.docker.internal', egeria_url: 'https://localhost:9443',
  egeria_server: 'view-server', egeria_user: 'erinoverview', status: 'active', registered_at: '2026-09-01T00:00:00',
  group_slug: '', last_run_at: null, last_run_candidate_count: null,
  databases: [
    { slug: 'coco', display_name: 'Coco', database_name: 'coco', last_surveyed_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(), status: 'active' },
    { slug: 'fresh', display_name: 'Fresh', database_name: 'fresh', last_surveyed_at: '', status: 'active' },
  ],
};

async function openSaved(servers) {
  const ctx = await setUp({ servers });
  const side = ctx.document.createElement('div'); side.id = 'sidebar'; ctx.document.body.appendChild(side);
  ctx.app.renderSidebar();
  ctx.document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  ctx.dlg = ctx.document.getElementById('wl-detail');
  return ctx;
}

test('a saved server shows its Egeria fields and each database\'s surveyed state, read only', async () => {
  const ctx = await openSaved([SERVER]);
  const row = ctx.dlg.querySelector('[data-source="regional_pg"]');
  const egeria = text(row.querySelector('[data-server-egeria]'));
  assert.match(egeria, /https:\/\/localhost:9443/);
  assert.match(egeria, /view server view-server/);
  // Per-resource Egeria credentials are no longer shown or used (owner, 2026-10-09).
  assert.doesNotMatch(egeria, /user erinoverview/);
  assert.match(egeria, /host host\.docker\.internal/);
  assert.doesNotMatch(egeria, /password/i, 'there is no Egeria password anywhere in the view');
  const dbs = [...row.querySelectorAll('[data-server-db]')];
  assert.equal(dbs.length, 2);
  assert.match(text(dbs[0]), /Coco · surveyed 3h ago/);
  assert.match(text(dbs[1]), /Fresh · never surveyed/);
  assert.equal(row.querySelectorAll('input').length, 0, 'read only: nothing to type into');
});

test('a server with no Egeria fields says so rather than showing blanks', async () => {
  const ctx = await openSaved([{ ...SERVER, egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '' }]);
  assert.match(text(ctx.dlg.querySelector('[data-server-egeria]')), /no Egeria connection recorded/);
});

test('a registered database under a server opens the same Change credentials form', async () => {
  const ctx = await openSaved([SERVER]);
  const btn = ctx.dlg.querySelector('[data-server-db="coco"] [data-change-cred]');
  assert.ok(btn, 'each registered database has the door');
  btn.click();
  await tick();
  const form = ctx.dlg.querySelector('[data-cred-change="coco"]');
  assert.ok(form);
  assert.equal(form.querySelector('[data-cred-user]').value, '', 'the server list does not carry a user to prefill');
  typeInto(form.querySelector('[data-cred-user]'), 'new_user');
  typeInto(form.querySelector('[data-cred-password]'), FAKE_PW);
  form.querySelector('[data-cred-save]').click();
  await tick(80);
  assert.equal(text(form.querySelector('[data-cred-drift]')), 'in registry · in .omsecrets · in sync');
  leakFree(ctx);
});
