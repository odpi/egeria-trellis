/** Parity P1: the backend-health banner (PI-138), the bootstrap banner (PI-139), the connection popover (PI-137). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));

async function load() {
  const { document, window } = makeDomEnvironment();
  const api = await import('/static/re-api.js');
  const mod = await import(`/static/next/shell-status.js?t=${Math.random()}`);
  return { document, window, api, mod };
}

/* ── PI-138 ────────────────────────────────────────────────────────────── */

test('health: a failed check shows one banner with a short word and Retry; a good check removes it', async () => {
  const { document, api, mod } = await load();
  let fail = new api.ApiError(503, 'connection refused', '/health/ready');
  const getHealth = async () => { if (fail) throw fail; return { status: 'ok' }; };
  assert.equal(await mod.checkBackendHealth(document, { getHealth }), false);
  assert.equal(await mod.checkBackendHealth(document, { getHealth }), false);
  assert.equal(document.querySelectorAll('#backend-health-banner').length, 1, 'repeats do not stack banners');
  const bar = document.getElementById('backend-health-banner');
  assert.equal(bar.querySelector('[data-health-word]').textContent, 'Database unreachable');
  assert.equal(bar.getAttribute('title'), 'connection refused', 'the sentence is on demand');
  fail = null;
  assert.equal(await mod.checkBackendHealth(document, { getHealth }), true);
  assert.equal(document.getElementById('backend-health-banner'), null);
});

test('health: the word tells a dead server from a dead database from another failure', async () => {
  const { api, mod } = await load();
  assert.equal(mod.healthWord(new api.ApiError(0, 'x', '/h')), 'Server not responding');
  assert.equal(mod.healthWord(new api.ApiError(503, 'x', '/h')), 'Database unreachable');
  assert.equal(mod.healthWord(new api.ApiError(500, 'x', '/h')), 'Server error 500');
  assert.equal(mod.healthWord(null), 'ok');
});

test('health: Retry shows an immediate checking state, re-checks, and leaves the banner when it passes', async () => {
  const { document, api, mod } = await load();
  let fail = true;
  const getHealth = async () => { if (fail) throw new api.ApiError(0, 'down', '/h'); return {}; };
  await mod.checkBackendHealth(document, { getHealth });
  const btn = document.querySelector('[data-health-retry]');
  btn.click();
  assert.equal(btn.disabled, true);
  assert.equal(btn.textContent, 'Checking…');
  await tick();
  assert.equal(btn.disabled, false, 'still failing: re-armed');
  assert.equal(btn.textContent, 'Retry');
  fail = false;
  btn.click();
  await tick();
  assert.equal(document.getElementById('backend-health-banner'), null);
});

test('health: a dead SESSION is not a dead backend (the session banner owns that)', async () => {
  const { document, mod } = await load();
  const err = Object.assign(new Error('sign-in needed'), { status: 401, loginRequired: true });
  assert.equal(await mod.checkBackendHealth(document, { getHealth: async () => { throw err; } }), true);
  assert.equal(document.getElementById('backend-health-banner'), null);
});

test('health: the server sentence is never put in the page as markup', async () => {
  const { document, api, mod } = await load();
  await mod.checkBackendHealth(document, { getHealth: async () => { throw new api.ApiError(503, '<img src=x onerror=alert(1)>', '/h'); } });
  assert.equal(document.querySelector('img'), null);
});

test('health: the real /health/ready call goes through re-api and a 503 body becomes the banner', async () => {
  const { document, mod } = await load();
  globalThis.fetch = async () => ({ ok: false, status: 503, statusText: 'x', json: async () => ({ status: 'error', detail: 'db is down' }) });
  assert.equal(await mod.checkBackendHealth(document), false);
  assert.equal(document.querySelector('[data-health-word]').textContent, 'Database unreachable');
  assert.equal(document.getElementById('backend-health-banner').title, 'db is down');
});

/* ── PI-139 ────────────────────────────────────────────────────────────── */

const B = (over = {}) => ({ display_name: 'Foundations', present: true, last_healed_at: '', last_heal_result: '', ...over });
const STATUS = (batches, over = {}) => ({ reinitializing: false, egeria_reachable: true, batches, ...over });

test('bootstrap: nothing verified-missing means no banner; unchecked (null) is not missing', async () => {
  const { mod } = await load();
  assert.equal(mod.bootstrapAttention(STATUS({ a: B(), b: B({ present: true }) })), null);
  assert.equal(mod.bootstrapAttention(STATUS({ a: B({ present: null }) })), null, 'never verified is not missing');
  assert.equal(mod.bootstrapAttention(null), null, 'a status that could not be read claims nothing');
});

test('bootstrap: missing, restoring and unreachable are three different words', async () => {
  const { mod } = await load();
  assert.deepEqual(mod.bootstrapAttention(STATUS({ a: B({ present: false }), b: B({ present: false }) })),
    { kind: 'missing', word: '2 definition batches missing', missing: 2 });
  assert.equal(mod.bootstrapAttention(STATUS({ a: B({ present: false }) })).word, '1 definition batch missing');
  assert.equal(mod.bootstrapAttention(STATUS({ a: B() }, { reinitializing: true })).kind, 'restoring');
  assert.equal(mod.bootstrapAttention(STATUS({ a: B() }, { egeria_reachable: false })).kind, 'unreachable');
});

test('bootstrap: lastHeal picks the newest heal and says so when none has run', async () => {
  const { mod } = await load();
  const s = STATUS({
    a: B({ display_name: 'Old', last_healed_at: '2026-10-08T01:00:00Z', last_heal_result: 'ok' }),
    b: B({ display_name: 'New', last_healed_at: '2026-10-08T05:00:00Z', last_heal_result: 'dr_egeria exit 1' }),
    c: B({ display_name: 'Never' }),
  });
  assert.deepEqual(mod.lastHeal(s), { at: '2026-10-08T05:00:00Z', result: 'dr_egeria exit 1', batch: 'New' });
  assert.equal(mod.lastHeal(STATUS({ a: B() })), null);
});

test('bootstrap: the banner shows the cue, the word, Run bootstrap now, and the last heal result and time', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false, last_healed_at: '2026-10-08T05:00:00Z', last_heal_result: 'ok' }) }));
  const bar = document.getElementById('bootstrap-banner');
  assert.equal(bar.dataset.bootstrapKind, 'missing');
  assert.match(bar.querySelector('[data-bootstrap-word]').textContent, /1 definition batch missing/);
  assert.match(bar.querySelector('[data-bootstrap-heal]').textContent, /Last heal: Foundations · ok ·/);
  assert.ok(bar.querySelector('[data-bootstrap-run]'));
});

test('bootstrap: with no heal on record the banner says that, rather than leaving a blank', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }));
  assert.match(document.querySelector('[data-bootstrap-heal]').textContent, /not read in this process/);
  assert.doesNotMatch(document.querySelector('[data-bootstrap-heal]').textContent, /since (the server )?start/);
});

test('bootstrap: restoring and unreachable offer no run button', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }, { reinitializing: true }));
  assert.equal(document.querySelector('[data-bootstrap-run]'), null);
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }, { egeria_reachable: false }));
  assert.equal(document.querySelector('[data-bootstrap-run]'), null);
  assert.match(document.querySelector('[data-bootstrap-word]').textContent, /Egeria not reachable/);
});

test('bootstrap: when the status is healthy the banner is removed', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }));
  assert.ok(document.getElementById('bootstrap-banner'));
  mod.renderBootstrapBanner(document, STATUS({ a: B() }));
  assert.equal(document.getElementById('bootstrap-banner'), null);
});

test('bootstrap: it takes two presses to run; the first only arms', async () => {
  const { document, mod } = await load();
  let runs = 0;
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), { run: async () => { runs += 1; return { batches: { a: { action: 'healed' } } }; } });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click();
  assert.equal(runs, 0);
  assert.equal(btn.textContent, 'Press again to run');
  btn.click();
  assert.equal(btn.textContent, 'Running…');
  assert.equal(btn.disabled, true);
  await tick();
  assert.equal(runs, 1);
  assert.match(document.querySelector('[data-bootstrap-state]').textContent, /a: healed/);
});

test('bootstrap: the real run call posts force=false, and only ever force=false', async () => {
  const { document, mod, api } = await load();
  const posts = [];
  globalThis.fetch = async (u, o = {}) => {
    if (o.method === 'POST') { posts.push([String(u), JSON.parse(o.body)]); return { ok: true, status: 200, json: async () => ({ batches: {} }) }; }
    return { ok: true, status: 200, json: async () => STATUS({ a: B({ present: true }) }) };
  };
  assert.equal(api.runBootstrapMissingOnly.length, 0, 'the helper takes no force argument');
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), { onDone: async () => {} });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick();
  assert.deepEqual(posts, [['/api/bootstrap/run', { force: false }]]);
  assert.equal(JSON.stringify(posts).includes('"force":true'), false);
});

test('bootstrap: a status poll during a run does not rebuild the button or allow a second run', async () => {
  const { document, mod } = await load();
  let release;
  const gate = new Promise((r) => { release = r; });
  let runs = 0;
  const st = STATUS({ a: B({ present: false }) });
  mod.renderBootstrapBanner(document, st, { run: async () => { runs += 1; await gate; return { batches: {} }; } });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick();
  mod.renderBootstrapBanner(document, st, { run: async () => { runs += 1; } });
  assert.equal(document.querySelector('[data-bootstrap-run]'), btn, 'the same button, still running');
  assert.equal(btn.textContent, 'Running…');
  release();
  await tick();
  assert.equal(runs, 1);
});

test('bootstrap: a failed run is said on the banner and the button comes back', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), { run: async () => { throw new Error('bootstrap run failed: boom'); } });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick();
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Run bootstrap now');
  assert.match(document.querySelector('[data-bootstrap-state]').textContent, /Not run: bootstrap run failed: boom/);
});

test('bootstrap: after a run the status is read again and the banner follows it', async () => {
  const { document, mod } = await load();
  let status = STATUS({ a: B({ present: false }) });
  globalThis.fetch = async (u, o = {}) => (String(u).includes('admin-status')
    ? { ok: true, status: 200, json: async () => ({ admin: true }) }
    : o.method === 'POST'
    ? { ok: true, status: 200, json: async () => { status = STATUS({ a: B({ present: true, last_healed_at: '2026-10-08T05:00:00Z', last_heal_result: 'ok' }) }); return { batches: {} }; } }
    : { ok: true, status: 200, json: async () => status });
  await mod.refreshBootstrapBanner(document);
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick(40);
  assert.equal(document.getElementById('bootstrap-banner'), null, 'healed: the banner is gone');
});

test('bootstrap: a status that cannot be read draws no banner and removes none', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }));
  const r = await mod.refreshBootstrapBanner(document, { getStatus: async () => { throw new Error('401'); } });
  assert.equal(r, null);
  assert.ok(document.getElementById('bootstrap-banner'), 'unchanged: a failed read is not a clean bill of health');
});

test('bootstrap: batch names and results are escaped', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false, display_name: '<img src=x onerror=alert(1)>', last_healed_at: '2026-10-08T05:00:00Z', last_heal_result: '<b id="pwn">' }) }));
  assert.equal(document.querySelector('img'), null);
  assert.equal(document.querySelector('#pwn'), null);
});

/* ── PI-137 ────────────────────────────────────────────────────────────── */

test('connection: connectionRows says "not read" (null) for what could not be read, and never an empty or zero', async () => {
  const { mod } = await load();
  const rows = Object.fromEntries(mod.connectionRows({ me: null, whoami: null, status: null }));
  for (const v of Object.values(rows)) assert.equal(v, null);
  const ok = Object.fromEntries(mod.connectionRows({
    me: { user_id: 'dan' }, whoami: { user_id: 'svc', view_server: 'view-server', platform_url: 'https://e:9443', build_sha: 'abc1234567890' },
    status: STATUS({ a: B() }),
  }));
  assert.equal(ok['Signed in as'], 'dan');
  assert.equal(ok['View server'], 'view-server');
  assert.equal(ok.Build, 'abc1234567890');
  assert.equal(ok['Last bootstrap heal'], 'not read in this process');
});

test('connection: a server that could not report its build says not read for Build only', async () => {
  const { mod } = await load();
  const rows = Object.fromEntries(mod.connectionRows({ me: { user_id: 'dan' }, whoami: { user_id: 'svc', view_server: 'v', platform_url: 'p', build_sha: null }, status: null }));
  assert.equal(rows.Build, null);
  assert.equal(rows['View server'], 'v');
});

test('connection: the popover shows user, view server, platform, a short build with the full sha on hover, and the last heal', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: 'dan' },
    getInfo: async () => ({ user_id: 'svc', view_server: 'qs-view-server', platform_url: 'https://egeria:9443', build_sha: 'abcdef0123456789' }),
    getStatus: async () => STATUS({ a: B({ last_healed_at: '2026-10-08T05:00:00Z', last_heal_result: 'ok' }) }),
  });
  const get = (k) => document.querySelector(`[data-conn="${k}"]`);
  assert.equal(get('Signed in as').textContent, 'dan');
  assert.equal(get('View server').textContent, 'qs-view-server');
  assert.equal(get('Platform').textContent, 'https://egeria:9443');
  assert.equal(get('Build').textContent, 'abcdef0123');
  assert.equal(get('Build').title, 'abcdef0123456789');
  assert.match(get('Last bootstrap heal').textContent, /Foundations · ok ·/);
});

test('connection: when the reads fail every value says not read, and the popover still opens', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, { me: null, getInfo: async () => { throw new Error('x'); }, getStatus: async () => { throw new Error('y'); } });
  const values = [...document.querySelectorAll('[data-conn]')].map((e) => e.textContent);
  // Brief L: "Egeria user" (the configured EGERIA_USER_ID, i.e. the daemon, mislabelled as a user)
  // became "Background work as". 7h added "RE identity in Egeria".
  assert.equal(values.length, 8);
  assert.ok(values.every((v) => v === 'not read'), values.join('|'));
});

test('connection: Escape closes it, and opening again does not stack a second one', async () => {
  const { document, window, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  const opts = { me: { user_id: 'dan' }, getInfo: async () => ({}), getStatus: async () => STATUS({}) };
  await mod.openConnectionPopover(document, anchor, opts);
  await mod.openConnectionPopover(document, anchor, opts);
  assert.equal(document.querySelectorAll('#connection-popover').length, 1);
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(document.getElementById('connection-popover'), null);
});

test('connection: values are escaped', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: '<img src=x onerror=alert(1)>' },
    getInfo: async () => ({ user_id: '<b id="pwn">', view_server: 'v', platform_url: 'p', build_sha: 'a' }),
    getStatus: async () => STATUS({}),
  });
  assert.equal(document.querySelector('img'), null);
  assert.equal(document.querySelector('#pwn'), null);
});

test('bootstrap: a non-admin sees Run bootstrap now disabled with "admin only"', async () => {
  const { document, mod } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), { locked: true });
  const btn = document.querySelector('[data-bootstrap-run]');
  assert.equal(btn.disabled, true);
  assert.match(document.getElementById('bootstrap-banner').textContent, /admin only/);
  btn.click(); btn.click();
  assert.equal(btn.textContent, 'Run bootstrap now', 'a disabled button never arms');
});

test('bootstrap: locked only when an admin is configured and the caller is not one', async () => {
  const { document, mod } = await load();
  const draw = async (st) => {
    document.getElementById('bootstrap-banner')?.remove();
    await mod.refreshBootstrapBanner(document, { getStatus: async () => STATUS({ a: B({ present: false }) }), getAdmin: async () => st });
    return document.querySelector('[data-bootstrap-run]').disabled;
  };
  assert.equal(await draw({ admin: false, configured: true }), true);
  assert.equal(await draw({ admin: false, configured: false }), false, 'no admin configured: any signed-in user may act');
  assert.equal(await draw({ admin: true, configured: true }), false);
  assert.equal(await draw(null), false, 'a status that could not be read locks nothing; the server still enforces');
});

test('bootstrap: a 409 from the server is shown as running, and the button comes back', async () => {
  const { document, mod, api } = await load();
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), {
    run: async () => { throw new api.ApiError(409, 'A bootstrap heal is already running', '/api/bootstrap/run'); } });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick();
  assert.match(document.querySelector('[data-bootstrap-state]').textContent, /running/);
  assert.doesNotMatch(document.querySelector('[data-bootstrap-state]').textContent, /Not run/);
  assert.equal(btn.disabled, false);
});

test('bootstrap: the real run call carries the admin token', async () => {
  const { document, window, mod } = await load();
  window.sessionStorage.setItem('re_admin_token', 'tok-b');
  const seen = [];
  globalThis.fetch = async (u, o = {}) => { seen.push(o.headers); return { ok: true, status: 200, json: async () => ({ batches: {} }) }; };
  mod.renderBootstrapBanner(document, STATUS({ a: B({ present: false }) }), { onDone: async () => {} });
  const btn = document.querySelector('[data-bootstrap-run]');
  btn.click(); btn.click();
  await tick();
  assert.equal(seen[0]['X-Admin-Token'], 'tok-b');
});

/* ── Brief I: who the last Egeria call ran as, and refusals ─────────────── */

test('connection: "Last Egeria call as" shows the RECORDED value, and says when nothing was recorded', async () => {
  const { mod } = await load();
  assert.equal(mod.lastCallWords(null), null, 'whoami not read');
  assert.equal(mod.lastCallWords({ last_egeria_call: null }), 'not recorded in this process');
  assert.match(mod.lastCallWords({ last_egeria_call: { as: 'you', purpose: 'publish', at: '2026-10-09T10:00:00Z' } }),
    /^you · publish · /);
  assert.match(mod.lastCallWords({ last_egeria_call: { as: 'service account (background)', purpose: 'survey' } }),
    /^service account \(background\) · survey$/);
});

test('connection: the popover carries the last-call row from whoami', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: 'dan' },
    getInfo: async () => ({ user_id: 'svc', view_server: 'v', platform_url: 'p', build_sha: 'a',
      last_egeria_call: { as: 'service account (background)', purpose: 'survey' } }),
    getStatus: async () => STATUS({}),
  });
  assert.equal(document.querySelector('[data-conn="Last Egeria call as"]').textContent,
    'service account (background) · survey');
});

test('api: an Egeria refusal is a refusal, with the short word first and Egeria\'s sentence', async () => {
  const { api } = await load();
  globalThis.fetch = async () => ({ ok: false, status: 403, statusText: 'Forbidden',
    json: async () => ({ detail: 'OPEN-METADATA-SECURITY-403-007 User dan is not authorized', refused_by: 'egeria' }) });
  const err = await api.getWhoami().then(() => null, (e) => e);
  assert.equal(err.refusedBy, 'egeria');
  assert.equal(err.message, 'refused by Egeria: OPEN-METADATA-SECURITY-403-007 User dan is not authorized');
  assert.equal(err.sentence, 'OPEN-METADATA-SECURITY-403-007 User dan is not authorized');
});

test('api: an expired Egeria sign-in is a dead session carrying the server\'s sentence', async () => {
  const { api, window } = await load();
  let dead = 0;
  globalThis.document.addEventListener('re:session-dead', () => { dead += 1; });
  globalThis.fetch = async () => ({ ok: false, status: 401, statusText: 'Unauthorized',
    json: async () => ({ detail: 'your Egeria sign-in expired; sign in again', refused_by: 'sign-in' }) });
  const err = await api.getWhoami().then(() => null, (e) => e);
  assert.equal(err.loginRequired, true);
  assert.equal(err.message, 'your Egeria sign-in expired; sign in again');
  assert.ok(dead <= 1);
  void window;
});

/* ── Brief L: who background work runs as ─────────────────────────────── */

test('connection: "Background work as" shows the daemon userId the server loaded and its source, never inferred', async () => {
  const { mod } = await load();
  const row = (whoami) => Object.fromEntries(mod.connectionRows({ me: null, whoami, status: null }))['Background work as'];
  assert.equal(row(null), null, 'whoami not read');
  assert.equal(row({ user_id: 'svc' }), 'not reported by this server', 'an older server: never inferred from user_id');
  assert.ok(!Object.keys(Object.fromEntries(mod.connectionRows({ me: null, whoami: { user_id: 'svc' }, status: null }))).includes('Egeria user'),
    'the daemon id is never shown as "Egeria user"');
  assert.equal(row({ daemon: { user_id: 'resourceexplorernpa', source: 'env' } }),
    'resourceexplorernpa · from .env (development bootstrap)');
  assert.equal(row({ daemon: { user_id: 'erinoverview', source: 'default' } }),
    'erinoverview · code default (EGERIA_USER_ID unset)', 'never labelled .env when it is not');
});

test('connection: the popover renders the Background work as row', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: 'dan' },
    getInfo: async () => ({ user_id: 'svc', view_server: 'v', platform_url: 'p', build_sha: 'abc',
      daemon: { user_id: 'resourceexplorernpa', source: 'env' } }),
    getStatus: async () => STATUS({ a: B() }),
  });
  assert.equal(document.querySelector('[data-conn="Background work as"]').textContent,
    'resourceexplorernpa · from .env (development bootstrap)');
});

/* ── 7h: RE's own identity in Egeria ──────────────────────────────────── */

test('connection: "RE identity in Egeria" shows the words the startup check recorded, never inferred', async () => {
  const { mod } = await load();
  const row = (whoami) => Object.fromEntries(mod.connectionRows({ me: null, whoami, status: null }))['RE identity in Egeria'];
  assert.equal(row(null), null, 'whoami not read');
  assert.equal(row({ user_id: 'svc' }), 'not reported by this server', 'an older server');
  assert.equal(row({ identity_in_egeria: { status: 'present', words: 'present' } }), 'present');
  assert.equal(row({ identity_in_egeria: { status: 'could not check', words: 'could not check (refused: 2 profile elements)' } }),
    'could not check (refused: 2 profile elements)');
  assert.equal(row({ identity_in_egeria: { status: 'present', words: 'present' },
                     owner_lookup: { user_id: 'dan', reason: 'no answer within 15s' } }),
    'present · last owner-profile lookup failed (dan): no answer within 15s');
});

test('connection: the popover renders the RE identity in Egeria row', async () => {
  const { document, mod } = await load();
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: 'dan' },
    getInfo: async () => ({ user_id: 'svc', view_server: 'v', platform_url: 'p', build_sha: 'abc',
      identity_in_egeria: { status: 'created', words: 'created (ProfileIdentity)' } }),
    getStatus: async () => STATUS({ a: B() }),
  });
  assert.equal(document.querySelector('[data-conn="RE identity in Egeria"]').textContent, 'created (ProfileIdentity)');
});
