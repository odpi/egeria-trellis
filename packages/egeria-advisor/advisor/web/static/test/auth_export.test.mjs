/** EA: the REAL auth.js, loaded as a classic <script> the way index.html loads it, is what the Dashboards
 *  view reads its Authorization header from.
 *
 *  Found live on 8880, 2026-11-12: auth.js declares `const Auth` at the top level. That is a global binding
 *  but not a window property, so dashboards_view.js (`root.Auth`), dashboards_author.js (`root.Auth`) and
 *  specs_publish.js (`window.Auth`) all saw undefined and sent /api/dashboards… with NO Authorization
 *  header. The 401 went through auth.js's fetch wrapper (handle401), which cleared the token: opening
 *  Dashboards signed the user out of the whole of EA. Every other harness stubs `w.Auth = {…}` directly,
 *  which is exactly the assignment the real file never made, so none of them could see this.
 *
 *  Scripts run as <script> elements (runScripts: 'dangerously'), not w.eval: a classic script's top-level
 *  `const` is shared with later scripts but is not on window, and only real script elements reproduce that.
 *  Only fetch is stubbed (it records each request's headers and answers 401 when none is sent, as
 *  trellis-auth's middleware does).
 *
 *  Run:  node --test advisor/web/static/test/auth_export.test.mjs   (jsdom: see render.test.mjs)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';

test('the harness can run (jsdom found)', { skip: !REQUIRED && SKIP }, () => {
  assert.ok(jsdom, `EA_RENDER_HARNESS_REQUIRED=1 but ${SKIP}`);
});

const read = (rel) => fs.readFileSync(path.join(STATIC, rel), 'utf8');
const AUTH_JS = read('auth.js');
const EXPORT_LINE = 'window.Auth = Auth;';

// a token whose exp is in the future, so the wrapper's proactive expiry check does not fire first
const TOKEN = 'h.' + Buffer.from(JSON.stringify({ sub: 'erinoverview', exp: Math.floor(Date.now() / 1000) + 3600 }))
  .toString('base64') + '.s';

function makeWindow(authSource = AUTH_JS) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><div id="messages"></div>' +
    '<div id="dashboard-status"></div><div id="dashboard-list"></div></body></html>',
  { runScripts: 'dangerously', url: 'http://ea.test/' });
  const w = dom.window;
  w.sessionStorage.setItem('ea_token', TOKEN);
  w.requests = [];
  // installed BEFORE auth.js, which wraps whatever window.fetch it finds (index.html order)
  w.fetch = async (url, init = {}) => {
    const auth = (init.headers || {}).Authorization;
    w.requests.push({ url: String(url), auth });
    const status = auth === `Bearer ${TOKEN}` ? 200 : 401;
    const body = status === 200 ? { state: 'ok', sheets: [] } : { detail: 'Not authenticated' };
    return new w.Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  };
  if (typeof w.Response === 'undefined') w.Response = Response;   // jsdom has none of its own
  const run = (code) => {
    const s = w.document.createElement('script');
    s.textContent = code;
    w.document.body.appendChild(s);
  };
  run(authSource);
  run(read('dashboards_view.js'));
  return { w, run };
}

test('auth.js puts Auth on window, so root.Auth and window.Auth are the module itself', { skip: SKIP }, () => {
  const { w, run } = makeWindow();
  run('window.__same = (window.Auth === Auth);');
  assert.equal(w.__same, true);
  assert.equal(typeof w.Auth.getHeaders, 'function');
});

test('the Dashboards list is fetched with the signed-in user\'s token, and nobody is signed out', { skip: SKIP }, async () => {
  const { w } = makeWindow();
  await w.EADashboards.loadDashboards();
  const list = w.requests.filter(r => r.url.startsWith('/api/dashboards'));
  assert.equal(list.length, 1, JSON.stringify(w.requests));
  assert.equal(list[0].auth, `Bearer ${TOKEN}`);
  assert.equal(w.sessionStorage.getItem('ea_token'), TOKEN, 'the token must survive opening Dashboards');
  assert.doesNotMatch(w.document.getElementById('dashboard-status').textContent, /Sign in/);
});

test('control: without the export line the same load sends no header and signs the user out', { skip: SKIP }, async () => {
  assert.ok(AUTH_JS.includes(EXPORT_LINE), 'auth.js must export Auth on window');
  const { w } = makeWindow(AUTH_JS.replace(EXPORT_LINE, ''));
  await w.EADashboards.loadDashboards();
  const list = w.requests.filter(r => r.url.startsWith('/api/dashboards'));
  assert.equal(list[0].auth, undefined);
  assert.equal(w.sessionStorage.getItem('ea_token'), null, 'handle401 clears the token: the bug as found on 8880');
});

test('every module that reads root.Auth / window.Auth is loaded after auth.js in index.html', () => {
  const index = read('index.html');
  const at = (name) => index.indexOf(`/static/${name}`);
  assert.ok(at('auth.js') > 0);
  for (const f of fs.readdirSync(STATIC).filter(f => f.endsWith('.js') && f !== 'auth.js')) {
    if (!/\b(root|window)\.Auth\b/.test(read(f))) continue;
    assert.ok(at(f) > at('auth.js'), `${f} reads window.Auth but index.html does not load it after auth.js`);
  }
});
