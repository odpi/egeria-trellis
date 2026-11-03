/** Behaviour: PI-015, "Register one database…" inside + Find databases.
 *
 *  Test-then-register: Test connection runs first and shows the probe's
 *  sentence; Register is available only after a pass for the values on screen;
 *  the row afterwards reads "saved · you · just now" from the RE-READ
 *  registration, never from the click. The password is never rendered, never in
 *  a URL, never in text. Real app.js and router; only fetch is stubbed.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FAKE_PW = 'test-password-not-real';
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

function makeBackend({ tests = [], registerError = null, registeredBy = 'dan' } = {}) {
  const calls = [];
  const queue = [...tests];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (method === 'GET' && u === '/api/db-servers/') return ok([]);
    if (method === 'GET' && (u.startsWith('/api/projects/groups') || u.startsWith('/api/investigations/?'))) return ok([]);
    if (method === 'POST' && u === '/api/databases/_test-connection') {
      return ok(queue.shift() || { status: 'error', sentence: 'no scripted test' });
    }
    if (method === 'POST' && u === '/api/databases/register') {
      if (registerError) return err(400, registerError);
      return ok({ slug: body.slug, display_name: body.display_name });
    }
    const reg = u.match(/^\/api\/databases\/([^/]+)\/registration$/);
    if (method === 'GET' && reg) {
      return ok({ slug: reg[1], registered_at: new Date().toISOString(), registered_by: registeredBy });
    }
    return ok(u.includes('/groups') || u.includes('/projects/') || u.includes('/databases/') ? [] : {});
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
  app.state.databases = [];
  app.state.databasesLoaded = true;
  app.state.groups = [];
  app.state.me = { user_id: 'dan' };
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  app.renderSidebar();
  document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick();
  const dlg = document.getElementById('wl-detail');
  dlg.querySelector('[data-act="register-db"]').click();
  await tick();
  return { document, window, dlg, calls, app };
}

const fill = (dlg, key, value) => {
  const inp = dlg.querySelector(`[data-rdb="${key}"]`);
  assert.ok(inp, `the form has a ${key} field`);
  inp.value = value;
  inp.dispatchEvent(new inp.ownerDocument.defaultView.Event('input', { bubbles: true }));
};
const fillAll = (dlg) => {
  fill(dlg, 'slug', 'appdb'); fill(dlg, 'display_name', 'App DB'); fill(dlg, 'host', 'pg.example.invalid');
  fill(dlg, 'port', '5999'); fill(dlg, 'database_name', 'appdb'); fill(dlg, 'db_user', 'scout_ro');
  fill(dlg, 'db_password', FAKE_PW);
};

test('the Saved sources tab offers "Register one database…" and it opens a form with every field the brief names', async () => {
  const { dlg } = await setUp();
  for (const k of ['slug', 'display_name', 'host', 'port', 'database_name', 'group_slug', 'db_user', 'db_password']) {
    assert.ok(dlg.querySelector(`[data-rdb="${k}"]`), `field ${k}`);
  }
  assert.ok(dlg.querySelector('[data-act="rdb-test"]'));
  assert.match(text(dlg.querySelector('[data-act="rdb-register"]')), /Register/);
});

test('Register is unavailable until a passing test for the values on screen, and says why', async () => {
  const { dlg } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected to pg.example.invalid:5999/appdb as scout_ro · 12 table(s) readable with this credential.' }] });
  fillAll(dlg);
  const reg = () => dlg.querySelector('[data-act="rdb-register"]');
  assert.equal(reg().disabled, true, 'untested: nothing to register yet');
  assert.match(text(dlg.querySelector('[data-rdb-test-state]')), /not tested/);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  assert.match(text(dlg.querySelector('[data-rdb-test-state]')), /passed/);
  assert.match(text(dlg), /Connected to pg\.example\.invalid:5999\/appdb as scout_ro · 12 table\(s\) readable/);
  assert.equal(reg().disabled, false);
  // changing anything the test covered puts it back to untested
  fill(dlg, 'db_user', 'someone_else');
  assert.equal(reg().disabled, true);
  assert.match(text(dlg.querySelector('[data-rdb-test-state]')), /changed since the test/);
});

test('a failing test shows the sentence, keeps Register off, and registers nothing', async () => {
  const { dlg, calls } = await setUp({ tests: [{ status: 'error', sentence: 'Not registered: the server at pg.example.invalid:5999 refused this credential (FATAL)' }] });
  fillAll(dlg);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  assert.match(text(dlg.querySelector('[data-rdb-test-state]')), /failed/);
  assert.match(text(dlg), /refused this credential/);
  assert.equal(dlg.querySelector('[data-act="rdb-register"]').disabled, true);
  dlg.querySelector('[data-act="rdb-register"]').click();
  await tick();
  assert.equal(calls.filter((c) => c.url === '/api/databases/register').length, 0);
});

test('a passed test then Register writes through the existing route and the row says saved · you · just now, from the re-read', async () => {
  const { dlg, calls } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected to x' }] });
  fillAll(dlg);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  dlg.querySelector('[data-act="rdb-register"]').click();
  await tick(60);
  const post = calls.find((c) => c.url === '/api/databases/register');
  assert.ok(post, 'the existing register route was used');
  assert.equal(post.body.slug, 'appdb');
  assert.equal(post.body.database_name, 'appdb');
  assert.ok(calls.some((c) => c.method === 'GET' && c.url === '/api/databases/appdb/registration'), 'the row is read back');
  assert.match(text(dlg.querySelector('[data-rdb-saved]')), /^saved · you · just now/);
});

test('a refused register says not saved with the server sentence and offers no saved row', async () => {
  const { dlg } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected' }], registerError: "Database 'appdb' already exists" });
  fillAll(dlg);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  dlg.querySelector('[data-act="rdb-register"]').click();
  await tick(60);
  assert.equal(dlg.querySelector('[data-rdb-saved]'), null);
  assert.match(text(dlg.querySelector('[data-rdb-error]')), /not saved · Database 'appdb' already exists/);
});

test('a pressed control looks pending and ignores a second press', async () => {
  const { dlg, calls } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected' }, { status: 'ok', sentence: 'Connected' }] });
  fillAll(dlg);
  const t = dlg.querySelector('[data-act="rdb-test"]');
  t.click(); t.click();
  await tick();
  assert.equal(calls.filter((c) => c.url === '/api/databases/_test-connection').length, 1);
  const r = dlg.querySelector('[data-act="rdb-register"]');
  r.click(); r.click();
  await tick(60);
  assert.equal(calls.filter((c) => c.url === '/api/databases/register').length, 1);
});

test('the password is never in the DOM text or attributes, never in a URL, and is wiped once saved', async () => {
  const { dlg, calls, document } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected' }] });
  fillAll(dlg);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  assert.ok(!document.documentElement.outerHTML.includes(FAKE_PW), 'not in markup or attributes');
  dlg.querySelector('[data-act="rdb-register"]').click();
  await tick(60);
  assert.ok(!document.documentElement.outerHTML.includes(FAKE_PW));
  for (const c of calls) assert.ok(!c.url.includes(FAKE_PW), `not in the URL ${c.url}`);
  const mod = await import('/static/next/db-register.js');
  assert.equal(mod.holdsPassword(), false, 'the in-memory copy is dropped after the save');
});

test('a passed test is not enough with a required field empty (the slug is not part of the test)', async () => {
  const { dlg } = await setUp({ tests: [{ status: 'ok', sentence: 'Connected' }] });
  fillAll(dlg);
  dlg.querySelector('[data-act="rdb-test"]').click();
  await tick();
  assert.equal(dlg.querySelector('[data-act="rdb-register"]').disabled, false);
  fill(dlg, 'slug', '');
  assert.equal(dlg.querySelector('[data-act="rdb-register"]').disabled, true);
});
