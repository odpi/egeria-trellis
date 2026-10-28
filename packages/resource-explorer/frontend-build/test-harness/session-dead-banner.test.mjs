/** A dead session (401 login_required from any fetch) shows ONE sign-in prompt with a visible control,
 *  not a sentence per pane; the header stops claiming to be signed in. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const dead = () => ({
  ok: false, status: 401, statusText: 'Unauthorized',
  json: async () => ({ detail: 'Authentication required. Sign in with your Egeria user id and password to continue.', error: 'login_required' }),
});

test('re-api marks a login_required 401 and raises re:session-dead once per call', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => dead();
  const api = await import(`/static/re-api.js?t=${Math.random()}`);
  let events = 0;
  document.addEventListener('re:session-dead', () => { events += 1; });
  let err;
  try { await api.getMe(); } catch (e) { err = e; }
  assert.ok(err, 'still throws');
  assert.equal(err.status, 401);
  assert.equal(err.loginRequired, true);
  assert.doesNotMatch(err.message, /Authentication required/, 'the server sentence is not each pane\'s error');
  assert.ok(err.message.length < 30, 'a short word');
  assert.equal(events, 1);
});

test('a 401 that is not login_required is an ordinary error', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => ({ ok: false, status: 401, statusText: 'x', json: async () => ({ detail: 'bad credentials' }) });
  const api = await import(`/static/re-api.js?t=${Math.random()}`);
  let events = 0;
  document.addEventListener('re:session-dead', () => { events += 1; });
  let err;
  try { await api.getMe(); } catch (e) { err = e; }
  assert.equal(err.loginRequired, undefined);
  assert.equal(err.message, 'bad credentials');
  assert.equal(events, 0);
});

test('three panes failing show ONE banner with a visible Sign in control', async () => {
  const { document } = makeDomEnvironment();
  const { installSessionBanner } = await import(`/static/next/session-banner.js?t=${Math.random()}`);
  let headerRefreshed = 0;
  installSessionBanner(document, { onDead: () => { headerRefreshed += 1; }, reload: () => {} });
  for (let i = 0; i < 3; i += 1) document.dispatchEvent(new document.defaultView.CustomEvent('re:session-dead'));
  const banners = document.querySelectorAll('#session-dead-banner');
  assert.equal(banners.length, 1);
  const btn = banners[0].querySelector('button[data-session-signin]');
  assert.ok(btn);
  assert.match(btn.textContent, /Sign in/);
  assert.ok(headerRefreshed >= 1, 'the header is told the session is gone');
});

test('Sign in opens the login overlay; signing in removes the banner and reloads', async () => {
  const { document, window } = makeDomEnvironment();
  const { installSessionBanner } = await import(`/static/next/session-banner.js?t=${Math.random()}`);
  let reloads = 0; const shown = [];
  globalThis.Auth = { showLogin: (m) => shown.push(m) };
  installSessionBanner(document, { reload: () => { reloads += 1; } });
  document.dispatchEvent(new window.CustomEvent('re:session-dead'));
  document.querySelector('[data-session-signin]').click();
  assert.equal(shown.length, 1);
  document.dispatchEvent(new window.CustomEvent('re:authenticated'));
  assert.equal(document.querySelectorAll('#session-dead-banner').length, 0);
  assert.equal(reloads, 1);
  delete globalThis.Auth;
});

test('re:authenticated with no banner showing does nothing (no reload)', async () => {
  const { document, window } = makeDomEnvironment();
  const { installSessionBanner } = await import(`/static/next/session-banner.js?t=${Math.random()}`);
  let reloads = 0;
  installSessionBanner(document, { reload: () => { reloads += 1; } });
  document.dispatchEvent(new window.CustomEvent('re:authenticated'));
  assert.equal(reloads, 0);
});

test('the Sign in button is never dead: pressed cue, then re-enabled on the next session-dead and when the overlay closes', async () => {
  const { document, window } = makeDomEnvironment();
  const { installSessionBanner } = await import(`/static/next/session-banner.js?t=${Math.random()}`);
  globalThis.Auth = { showLogin: () => {} };
  installSessionBanner(document, { reload: () => {} });
  document.dispatchEvent(new window.CustomEvent('re:session-dead'));
  const btn = () => document.querySelector('[data-session-signin]');
  btn().click();
  assert.match(btn().textContent, /opening/i, 'immediate pressed cue');
  document.dispatchEvent(new window.CustomEvent('re:session-dead'));
  assert.equal(btn().disabled, false);
  assert.match(btn().textContent, /^Sign in$/);
  btn().click();
  document.dispatchEvent(new window.CustomEvent('re:login-closed'));
  assert.equal(btn().disabled, false);
  assert.match(btn().textContent, /^Sign in$/);
  delete globalThis.Auth;
});

test('session-dead bursts within a second raise the event once', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => dead();
  const api = await import(`/static/re-api.js?t=${Math.random()}`);
  let events = 0;
  document.addEventListener('re:session-dead', () => { events += 1; });
  await Promise.allSettled([api.getMe(), api.getMe(), api.getMe()]);
  assert.equal(events, 1);
});

test('the CSV, inventory and stream helpers also answer a dead session with the same marker', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => dead();
  const api = await import(`/static/re-api.js?t=${Math.random()}`);
  let events = 0;
  document.addEventListener('re:session-dead', () => { events += 1; });
  for (const call of [() => api.fetchCsvFile('/x.csv'), () => api.fetchInventoryCsv(),
    async () => { for await (const _ of api.askStream('q', { entityType: 'repo' })) { /* none */ } }]) {
    let err; try { await call(); } catch (e) { err = e; }
    assert.equal(err.loginRequired, true);
    assert.equal(err.message, 'sign-in needed');
  }
  assert.equal(events, 1, 'one event for the burst');
});
