/** F59: the Egeria words survive the 401 cascade. Live 2026-11-05 12:10:53Z: the egeria_token_refused 401 showed the
 *  sign-in form with the Egeria sentence, then the page's own follow-up reads in the same second answered the plain
 *  session 401 (login_required, the RE token having just been cleared) and handle401 replaced the words with "Your
 *  session has expired". The overlay now remembers how strong its reason is: a weaker reason may re-show it, never
 *  replace stronger words; a successful sign-in clears the state. The real auth.js with a stand-in window, document,
 *  sessionStorage and a fetch that answers a queue of responses (auth endpoints answered by URL). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const SRC = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  '../../resource_explorer/web/static/auth.js'), 'utf8');

const EGERIA_WORDS = 'your Egeria sign-in is no longer valid (Egeria restarted since you signed in) · sign in again · nothing was profiled';
const SESSION_WORDS = 'Your session has expired. Please sign in again.';
const BANNER_WORDS = 'Your session ended. Sign in to continue.';   // session-banner.js's own Sign-in button
const MARKER_401 = () => ({ status: 401, body: JSON.stringify({ error: 'egeria_token_refused', detail: EGERIA_WORDS }) });
const SESSION_401 = () => ({ status: 401, body: JSON.stringify({ error: 'login_required' }) });

function liveToken() {
  const claims = Buffer.from(JSON.stringify({ sub: 'peterprofile', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64');
  return `h.${claims}.s`;
}

/** Loads auth.js. `queue` answers non-auth fetches in order; /api/auth/policy and /api/auth/login answer by URL. */
function loadAuth(queue) {
  const store = new Map([['re_token', liveToken()]]);
  const sessionStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  const els = new Map();
  const listeners = new Map();
  const el = (id) => {
    if (!els.has(id)) {
      const classes = new Set(['hidden']);
      els.set(id, { id, textContent: '', value: '', disabled: false, style: {},
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), has: (c) => classes.has(c) },
        focus() {}, addEventListener(type, fn) { listeners.set(`${id}:${type}`, fn); } });
    }
    return els.get(id);
  };
  const events = [];
  const doc = { getElementById: el, addEventListener() {}, dispatchEvent(e) { events.push(e.type); } };
  const logins = [];
  const win = {
    fetch: async (url, init) => {
      const u = String(url);
      if (u.includes('/api/auth/policy')) return new Response(JSON.stringify({ login_required: true }), { status: 200 });
      if (u.includes('/api/auth/login')) {
        logins.push(JSON.parse(init.body));
        return new Response(JSON.stringify({ access_token: liveToken(), egeria_user: 'peterprofile' }), { status: 200 });
      }
      const next = queue.shift();
      assert.ok(next, `no queued answer for ${u}`);
      return new Response(next.body, { status: next.status, headers: { 'content-type': 'application/json' } });
    },
    location: { origin: 'http://localhost', href: 'http://localhost/next/', pathname: '/next/', search: '', hash: '' },
    addEventListener() {},
  };
  class CustomEvent { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
  const Auth = new Function('window', 'document', 'sessionStorage', 'history', 'setTimeout', 'CustomEvent', `${SRC}\nreturn Auth;`)(
    win, doc, sessionStorage, { replaceState() {} }, (f) => f(), CustomEvent);
  return { Auth, win, store, el, listeners, events, logins,
    message: () => el('login-message').textContent, overlay: () => el('login-overlay').style.display };
}

test('F59 the marker 401 then a login_required 401 in the same tick: the Egeria words stay', async () => {
  const t = loadAuth([MARKER_401(), SESSION_401(), SESSION_401(), SESSION_401()]);
  // the press, and the page's follow-up reads, all in flight at once (the live cascade)
  await Promise.all([
    t.win.fetch('/api/filesystems/coco_data_lake/file-profiles', { method: 'POST' }),
    t.win.fetch('/api/filesystems/coco_data_lake/folder-survey'),
    t.win.fetch('/api/activity/?limit=20'),
  ]);
  assert.equal(t.overlay(), 'flex');
  assert.equal(t.message(), EGERIA_WORDS);
  assert.equal(t.store.has('re_token'), false, 'the dead sign-in is still cleared: a new sign-in mints a new one');
  // and a later plain 401 (the token is gone, so the wrapper's own proactive check never fires; the server answers)
  t.store.set('re_token', liveToken());
  await t.win.fetch('/api/native-surveys/filesystem/coco_data_lake', {});
  assert.equal(t.message(), EGERIA_WORDS, 'a later weaker 401 does not downgrade the words either');
});

test('F59 a login_required 401 alone still says the session expired', async () => {
  const t = loadAuth([SESSION_401()]);
  await t.win.fetch('/api/filesystems/coco_data_lake/folder-survey');
  assert.equal(t.overlay(), 'flex');
  assert.equal(t.message(), SESSION_WORDS);
});

test('F59 the session-dead banner\'s own Sign-in button does not downgrade the Egeria words', async () => {
  const t = loadAuth([MARKER_401()]);
  await t.win.fetch('/api/filesystems/coco_data_lake/file-profiles', { method: 'POST' });
  t.Auth.showLogin(BANNER_WORDS);                      // what session-banner.js calls on the button
  assert.equal(t.overlay(), 'flex');
  assert.equal(t.message(), EGERIA_WORDS);
  t.Auth.handle401();                                  // and the direct session handler
  assert.equal(t.message(), EGERIA_WORDS);
});

test('F59 after a successful sign-in the state is cleared: a later login_required reads the session words again', async () => {
  const t = loadAuth([MARKER_401(), SESSION_401(), SESSION_401()]);
  let started = 0;
  await t.Auth.init(() => { started += 1; });          // a live token: the app starts, the form's handlers are bound
  assert.equal(started, 1);
  await Promise.all([t.win.fetch('/api/filesystems/x/file-profiles', { method: 'POST' }), t.win.fetch('/api/activity/')]);
  assert.equal(t.message(), EGERIA_WORDS);
  assert.equal(t.Auth.overlayReason(), 2);
  // the person signs in on the form
  t.el('login-username').value = 'peterprofile';
  t.el('login-password').value = 'secret';
  await t.listeners.get('login-submit-btn:click')();
  assert.equal(t.logins.length, 1);
  assert.equal(t.store.has('re_token'), true);
  assert.equal(t.overlay(), 'none');
  assert.equal(t.Auth.overlayReason(), 0, 'cleared when the overlay hid');
  assert.ok(t.events.includes('re:authenticated'));
  // later, in the new session, a plain session 401
  await t.win.fetch('/api/activity/');
  assert.equal(t.overlay(), 'flex');
  assert.equal(t.message(), SESSION_WORDS);
});

test('F59 the words are decided by reason strength, never by comparing message text', () => {
  const show = SRC.slice(SRC.indexOf('function showLogin'), SRC.indexOf('function hideLogin'));
  assert.ok(show.includes('reason >= overlayReason'));
  assert.ok(!show.includes('no longer valid'), 'the words are not parsed');
  assert.ok(SRC.includes('showLogin(detail || EGERIA_TOKEN_REFUSED_WORDS, REASON_EGERIA)'));
});
