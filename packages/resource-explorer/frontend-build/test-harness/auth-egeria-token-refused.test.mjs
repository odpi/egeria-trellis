/** F58: the shell tells an RE-session 401 (`login_required`) from Egeria refusing the person's Egeria token
 *  (`egeria_token_refused`: Egeria restarted since they signed in, the RE session still valid). It branches on the
 *  marker, never on the words, and shows the sign-in form with the right words for each. The real auth.js with a
 *  stand-in window, document, sessionStorage and fetch; the caller's own body read is untouched. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const SRC = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  '../../resource_explorer/web/static/auth.js'), 'utf8');

const WORDS = 'your Egeria sign-in is no longer valid (Egeria restarted since you signed in) · sign in again · nothing was profiled';

function liveToken() {
  const claims = Buffer.from(JSON.stringify({ sub: 'peterprofile', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64');
  return `h.${claims}.s`;
}

function loadAuth({ status, body }) {
  const store = new Map([['re_token', liveToken()]]);
  const sessionStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  const els = new Map();
  const el = (id) => {
    if (!els.has(id)) {
      const classes = new Set(['hidden']);
      els.set(id, { id, textContent: '', style: {}, classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), has: (c) => classes.has(c) }, focus() {}, addEventListener() {} });
    }
    return els.get(id);
  };
  const doc = { getElementById: el, addEventListener() {}, dispatchEvent() {} };
  const win = {
    fetch: async () => new Response(body, { status, headers: { 'content-type': 'application/json' } }),
    location: { origin: 'http://localhost', href: 'http://localhost/next/', pathname: '/next/', search: '' },
    addEventListener() {},
  };
  const Auth = new Function('window', 'document', 'sessionStorage', 'history', 'setTimeout', `${SRC}\nreturn Auth;`)(
    win, doc, sessionStorage, { replaceState() {} }, (f) => f());
  return { Auth, win, store, message: () => el('login-message'), overlay: () => el('login-overlay') };
}

test('the egeria_token_refused marker shows the sign-in form with the server\'s Egeria words', async () => {
  const t = loadAuth({ status: 401, body: JSON.stringify({ error: 'egeria_token_refused', detail: WORDS }) });
  const r = await t.win.fetch('/api/filesystems/coco_data_lake/file-profiles', { method: 'POST' });
  assert.equal(r.status, 401);
  assert.deepEqual(await r.json(), { error: 'egeria_token_refused', detail: WORDS }, 'the caller still reads the body');
  assert.equal(t.overlay().style.display, 'flex');
  assert.equal(t.message().textContent, WORDS);
  assert.equal(t.message().classList.has('hidden'), false);
  assert.equal(t.store.has('re_token'), false, 'a new sign-in mints a new RE session with a live Egeria token');
});

test('the RE session\'s own 401 (login_required) still says the session expired', async () => {
  const t = loadAuth({ status: 401, body: JSON.stringify({ error: 'login_required' }) });
  await t.win.fetch('/api/filesystems/coco_data_lake/file-profiles');
  assert.equal(t.overlay().style.display, 'flex');
  assert.equal(t.message().textContent, 'Your session has expired. Please sign in again.');
});

test('a 401 whose body is not JSON is the session 401; a 200 shows nothing', async () => {
  const bare = loadAuth({ status: 401, body: 'not json' });
  await bare.win.fetch('/api/x');
  assert.equal(bare.message().textContent, 'Your session has expired. Please sign in again.');
  const ok = loadAuth({ status: 200, body: JSON.stringify({ ok: true }) });
  const r = await ok.win.fetch('/api/x');
  assert.deepEqual(await r.json(), { ok: true });
  assert.equal(ok.overlay().style.display, undefined);
  assert.equal(ok.message().textContent, '');
});

test('the shell branches on the marker, never on the words', () => {
  assert.ok(SRC.includes("const EGERIA_TOKEN_REFUSED = 'egeria_token_refused';"));
  assert.ok(SRC.includes('body.error === EGERIA_TOKEN_REFUSED'));
  const classify = SRC.slice(SRC.indexOf('async function classify401'), SRC.indexOf("return { kind: 'login_required'"));
  assert.ok(!classify.includes('no longer valid'));
});
