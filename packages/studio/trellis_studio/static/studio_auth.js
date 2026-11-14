/* Studio sign-in: trellis-auth's session JWT in sessionStorage, sent as Authorization: Bearer.
 *
 * Ways in: the password form; a Portal hand-over as a URL fragment `#pt=<token>` (cleared from the address bar
 * at once) or as postMessage({type:'egeria_auth', portal_token}) from an origin in STUDIO_PORTAL_ORIGINS
 * (exact match; anything else is ignored). The JWT itself is never put in a URL.
 */
const TOKEN_KEY = 'studio_token';

function store() { try { return window.sessionStorage; } catch (e) { return null; } }

export function getToken() { const s = store(); try { return s ? s.getItem(TOKEN_KEY) : null; } catch (e) { return null; } }
function setToken(t) { const s = store(); try { if (s) s.setItem(TOKEN_KEY, t); } catch (e) { /* no storage: session lasts this page */ } memo = t;
  document.dispatchEvent(new CustomEvent('studio:session')); }
function clearToken() { const s = store(); try { if (s) s.removeItem(TOKEN_KEY); } catch (e) {} memo = null; }
let memo = null;

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
  if (r.status === 401) { clearToken(); showLogin('Your session has ended. Sign in again.'); }
  return r;
}

async function exchange(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) return { ok: false, detail: data.detail || `HTTP ${r.status}` };
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

export function signOut() {
  clearToken();
  fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  document.dispatchEvent(new CustomEvent('studio:signed-out'));
  showLogin();
}

/** Resolve once a session exists (already, by fragment, by postMessage, or by the form). */
export async function init() {
  let portalOrigins = [];
  try { const r = await fetch('/api/auth/policy'); if (r.ok) portalOrigins = (await r.json()).portal_origins || []; } catch (e) {}

  return new Promise((resolve) => {
    const done = () => { hideLogin(); resolve(payload()); };

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
