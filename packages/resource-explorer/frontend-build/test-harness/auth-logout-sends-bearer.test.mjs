/** Sign-out must send the bearer token on POST /api/auth/logout BEFORE the token is cleared: the server drops the
 *  table-preview credential this sign-in held only if the request says who is signing out. The real auth.js, with a
 *  stand-in window, sessionStorage and fetch. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const SRC = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  '../../resource_explorer/web/static/auth.js'), 'utf8');

function loadAuth(token) {
  const store = new Map(token ? [['re_token', token]] : []);
  const sessionStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  const calls = [];
  let reloaded = false;
  const win = {
    fetch: async (url, init = {}) => { calls.push({ url: String(url), method: init.method, headers: { ...(init.headers || {}) }, tokenAtCall: store.get('re_token') ?? null }); return { ok: true, status: 200, json: async () => ({}) }; },
    location: { reload: () => { reloaded = true; }, origin: 'http://localhost', href: 'http://localhost/', pathname: '/', search: '' },
    addEventListener() {},
  };
  const doc = { getElementById: () => null, addEventListener() {}, dispatchEvent() {} };
  const Auth = new Function('window', 'document', 'sessionStorage', 'history', `${SRC}\nreturn Auth;`)(win, doc, sessionStorage, { replaceState() {} });
  return { Auth, calls, store, reloaded: () => reloaded };
}

test('sign-out posts the bearer token, then clears it, then reloads', async () => {
  const t = loadAuth('header.payload.sig');
  await t.Auth.doLogout();
  assert.equal(t.calls.length, 1);
  assert.equal(t.calls[0].url, '/api/auth/logout');
  assert.equal(t.calls[0].method, 'POST');
  assert.equal(t.calls[0].headers.Authorization, 'Bearer header.payload.sig');
  assert.equal(t.store.has('re_token'), false, 'the token is cleared');
  assert.equal(t.reloaded(), true);
});

test('sign-out with no token still signs out, with no Authorization header', async () => {
  const t = loadAuth(null);
  await t.Auth.doLogout();
  assert.equal(t.calls[0].headers.Authorization, undefined);
  assert.equal(t.reloaded(), true);
});
