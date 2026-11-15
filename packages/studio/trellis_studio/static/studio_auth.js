/* Studio sign-in: trellis-auth's session JWT in sessionStorage, sent as Authorization: Bearer.
 *
 * Ways in: the password form; a Portal hand-over as a URL fragment `#pt=<token>` (cleared from the address bar
 * at once) or as postMessage({type:'egeria_auth', portal_token}) from an origin in STUDIO_PORTAL_ORIGINS
 * (exact match; anything else is ignored). The JWT itself is never put in a URL.
 *
 * TR-10: the access JWT is short-lived. Sign-in also gives a refresh token (opaque, works once). While you use
 * the page, the refresh token is traded before the JWT ends (POST /api/auth/refresh, which re-checks your sign-in
 * with Egeria) for a new JWT and a new refresh token; an untouched tab lets it lapse. A refused refresh, or a JWT
 * that has run out, ends the session here. The new JWT reaches the EA panel through the 'studio:session' event;
 * the refresh token never leaves this page.
 */
import { refreshDue } from '/static/session_refresh.js';

const TOKEN_KEY = 'studio_token';
const REFRESH_KEY = 'studio_refresh';
const TICK_MS = 20000;
let lastActive = null;
let refreshing = false;
let ticking = null;

function store() { try { return window.sessionStorage; } catch (e) { return null; } }

export function getToken() { const s = store(); try { return s ? s.getItem(TOKEN_KEY) : null; } catch (e) { return null; } }
function setToken(t) { const s = store(); try { if (s) s.setItem(TOKEN_KEY, t); } catch (e) { /* no storage: session lasts this page */ } memo = t;
  document.dispatchEvent(new CustomEvent('studio:session')); }
function clearToken() { const s = store(); try { if (s) { s.removeItem(TOKEN_KEY); s.removeItem(REFRESH_KEY); } } catch (e) {} memo = null; refreshMemo = null; }
function getRefresh() { const s = store(); try { return (s && s.getItem(REFRESH_KEY)) || refreshMemo; } catch (e) { return refreshMemo; } }
function setRefresh(t) { const s = store(); refreshMemo = t || null; try { if (s) { if (t) s.setItem(REFRESH_KEY, t); else s.removeItem(REFRESH_KEY); } } catch (e) {} }
let memo = null;
let refreshMemo = null;

export function payload(token = getToken() || memo) {
  if (!token) return null;
  try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); } catch (e) { return null; }
}

/** The session JWT, or null when there is none or it has expired by its own exp. */
export function sessionToken() {
  const t = getToken() || memo;
  const p = payload(t);
  if (!p || !p.exp || p.exp <= Math.floor(Date.now() / 1000)) return null;
  return t;
}

export function headers(extra = {}) {
  const t = sessionToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : { ...extra };
}

/** fetch with the session; a 401 drops the session and shows the sign-in form. */
export async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: headers(opts.headers || {}) });
  if (r.status === 401) endSession('Your session has ended. Sign in again.');
  return r;
}

async function exchange(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) return { ok: false, detail: data.detail || `HTTP ${r.status}` };
  setRefresh(data.refresh_token);
  setToken(data.access_token);
  return { ok: true };
}

function showLogin(message) {
  const el = document.getElementById('st-login');
  const err = document.getElementById('st-login-error');
  if (err) { err.textContent = message || ''; err.hidden = !message; }
  if (el) el.hidden = false;
}
function hideLogin() { const el = document.getElementById('st-login'); if (el) el.hidden = true; }

function endSession(message) {
  clearToken();
  document.dispatchEvent(new CustomEvent('studio:session-ended'));
  showLogin(message);
}

const nowSec = () => Math.floor(Date.now() / 1000);

async function tick() {
  const t = getToken() || memo;
  const what = refreshDue(payload(t), nowSec(), lastActive);
  if (what === 'none') { if (t) endSession('Your session has ended. Sign in again.'); return; }
  const rt = getRefresh();
  if (what !== 'refresh' || refreshing || !rt) return;
  refreshing = true;
  try {
    const r = await fetch('/api/auth/refresh', { method: 'POST',
      headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: rt }) });
    if (r.ok) { const data = await r.json(); if (data.access_token) { setRefresh(data.refresh_token); setToken(data.access_token); } }
    else if (r.status === 401 || r.status === 403) {
      let detail = '';
      try { detail = (await r.json()).detail || ''; } catch (e) {}
      endSession(detail || 'Your session has ended. Sign in again.');
    }
    // anything else (Egeria couldn't be asked): keep this JWT and try again next tick
  } catch (e) { /* network: try again next tick */ } finally { refreshing = false; }
}

function startRefresh() {
  if (ticking) return;
  const seen = () => { lastActive = nowSec(); };
  for (const ev of ['pointerdown', 'keydown', 'wheel', 'touchstart']) window.addEventListener(ev, seen, { passive: true, capture: true });
  ticking = window.setInterval(tick, TICK_MS);
}

export function signOut() {
  const t = getToken() || memo;
  const rt = getRefresh();
  clearToken();
  document.dispatchEvent(new CustomEvent('studio:session-ended'));   // unload EA's frame first
  // Both tokens go with it, so Studio refuses (and won't refresh) this session from now on.
  const h = { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  fetch('/api/auth/logout', { method: 'POST', headers: h, body: JSON.stringify({ refresh_token: rt || null }) }).catch(() => {}).finally(() => {
    // A fresh page: nothing of the previous user's work or EA stays in this tab.
    window.location.replace(window.location.pathname);
  });
}

/** Resolve once a session exists (already, by fragment, by postMessage, or by the form). */
export async function init() {
  let portalOrigins = [];
  try { const r = await fetch('/api/auth/policy'); if (r.ok) portalOrigins = (await r.json()).portal_origins || []; } catch (e) {}

  return new Promise((resolve) => {
    const done = () => { hideLogin(); lastActive = nowSec(); startRefresh(); resolve(payload()); };

    window.addEventListener('message', async (ev) => {
      if (!portalOrigins.includes(ev.origin)) return;
      const d = ev.data || {};
      if (d.type !== 'egeria_auth' || !d.portal_token) return;
      const res = await exchange('/api/auth/portal', { portal_token: d.portal_token });
      if (res.ok) done(); else showLogin(res.detail);
    });

    const form = document.getElementById('st-login-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('st-login-user').value.trim();
      const pwdEl = document.getElementById('st-login-pwd');
      const res = await exchange('/api/auth/login', { username, password: pwdEl.value });
      pwdEl.value = '';
      if (res.ok) done(); else showLogin(res.detail);
    });

    (async () => {
      const h = window.location.hash;
      if (h.startsWith('#pt=')) {
        const pt = decodeURIComponent(h.slice(4));
        history.replaceState(null, '', window.location.pathname + window.location.search);
        const res = await exchange('/api/auth/portal', { portal_token: pt });
        if (res.ok) return done();
        return showLogin(res.detail);
      }
      if (sessionToken()) return done();
      showLogin();
    })();
  });
}
