/**
 * Egeria Advisor — Auth module
 *
 * Manages JWT-based authentication state in sessionStorage.
 * Handles standalone login, portal SSO (postMessage + URL fragment), and logout.
 *
 * Usage:
 *   Auth.getHeaders()          — object with Authorization header for fetch calls
 *   Auth.isAuthenticated()     — boolean
 *   Auth.showLogin()           — programmatically show the login overlay
 *   Auth.init(onReady)         — call on DOMContentLoaded; fires onReady() when done
 */
const Auth = (() => {
  const TOKEN_KEY = 'ea_token';

  // ── Embed mode (E1): EA inside Trellis Studio, /?embed=studio ────────────
  // No password form. The session arrives only by postMessage {type:'trellis_auth', jwt} from an origin in
  // ADVISOR_EMBED_ORIGINS (GET /api/embed/config), from the parent window, and EA checks it with its own token check
  // (GET /api/embed/whoami) before keeping it. Owner decision A: a Studio JWT is valid at EA.
  const EMBED = (() => { try { return new URLSearchParams(window.location.search).get('embed') === 'studio'; } catch { return false; } })();
  const EMBED_WAIT_MS = 5000;
  let embedOrigins = [];      // exact origins allowed to frame EA and talk to it by postMessage
  let embedParent = null;     // the origin whose trellis_auth EA accepted: every message to Studio targets it

  // ── Token helpers ────────────────────────────────────────────────────────

  function getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
  }

  function setToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  }

  function clearToken() {
    sessionStorage.removeItem(TOKEN_KEY);
  }

  function isAuthenticated() {
    const token = getToken();
    if (!token) return false;
    try {
      // Decode the JWT payload (no signature verification — server handles that)
      const payload = JSON.parse(atob(token.split('.')[1]));
      return payload.exp > Math.floor(Date.now() / 1000);
    } catch {
      return false;
    }
  }

  function getUser() {
    const token = getToken();
    if (!token) return null;
    try {
      return JSON.parse(atob(token.split('.')[1]));
    } catch {
      return null;
    }
  }

  function getHeaders() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  }

  // ── UI helpers ───────────────────────────────────────────────────────────

  // Whether the server requires a signed-in user. Fetched once from the public
  // /api/auth/policy route at init; assumed true until then, because assuming
  // "login optional" and being wrong means starting the app into a page where
  // every panel is a failed fetch — the broken page this is here to prevent.
  let loginRequired = true;

  async function loadPolicy() {
    try {
      const r = await fetch('/api/auth/policy');
      if (!r.ok) return;
      const data = await r.json();
      loginRequired = data.login_required !== false;
    } catch {
      // Server unreachable — keep the safe default (login required).
    }
  }

  function showEmbedSignIn(message) {
    let el = document.getElementById('embed-signin');
    if (!el) {
      el = document.createElement('div');
      el.id = 'embed-signin';
      el.setAttribute('role', 'status');
      el.className = 'fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/90 text-slate-200 text-sm p-6 text-center';
      document.body.appendChild(el);
    }
    el.textContent = message || 'Sign in to Studio to use EA here.';
    el.hidden = false;
  }

  function hideEmbedSignIn() {
    const el = document.getElementById('embed-signin');
    if (el) el.hidden = true;
  }

  function showLogin(message) {
    if (EMBED) { showEmbedSignIn(message && /Studio/.test(message) ? message : 'Sign in to Studio to use EA here.'); return; }
    // The "continue without signing in" footer only makes sense in the dev-only
    // anonymous-read mode; with login required it leads straight to a 401.
    const anonFooter = document.getElementById('login-anon-footer');
    if (anonFooter) anonFooter.hidden = loginRequired;
    const overlay = document.getElementById('login-overlay');
    if (!overlay) return;
    if (message) {
      const msg = document.getElementById('login-message');
      if (msg) { msg.textContent = message; msg.classList.remove('hidden'); }
    }
    overlay.classList.remove('hidden');
    setTimeout(() => document.getElementById('login-username')?.focus(), 50);
  }

  function hideLogin() {
    // When the server requires login, the overlay is not dismissible: hiding it
    // would reveal a shell whose every API call 401s. Dismissing is only a
    // choice in the dev-only TRELLIS_ANONYMOUS_READ mode, where reads work.
    if (loginRequired && !isAuthenticated()) return;
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.classList.add('hidden');
    const msg = document.getElementById('login-message');
    if (msg) msg.classList.add('hidden');
    const err = document.getElementById('login-error');
    if (err) { err.textContent = ''; err.classList.add('hidden'); }
    // Show the "Sign in" button in the header so user can get back to login
    const signinBtn = document.getElementById('login-header-btn');
    if (signinBtn && !isAuthenticated()) signinBtn.classList.remove('hidden');
  }

  function updateUserDisplay() {
    const el = document.getElementById('current-user');
    if (!el) return;
    const user = getUser();
    const signinBtn = document.getElementById('login-header-btn');
    if (user) {
      el.textContent = user.egeria_user || user.sub || '';
      el.classList.remove('hidden');
      document.getElementById('logout-btn')?.classList.remove('hidden');
      if (signinBtn) signinBtn.classList.add('hidden');
    } else {
      el.textContent = '';
      el.classList.add('hidden');
      document.getElementById('logout-btn')?.classList.add('hidden');
    }
  }

  function setLoginLoading(loading) {
    const btn = document.getElementById('login-submit-btn');
    if (!btn) return;
    btn.disabled = loading;
    btn.textContent = loading ? 'Signing in…' : 'Sign in';
  }

  // Best-effort prefill of the username field from the server's configured
  // default (.env EGERIA_USER), for local/dev convenience. Never touches the
  // password field, and never overwrites anything the user has already typed.
  async function prefillLoginDefaults() {
    const userEl = document.getElementById('login-username');
    if (!userEl || userEl.value) return;
    try {
      const r = await fetch('/api/auth/defaults');
      if (!r.ok) return;
      const data = await r.json();
      if (!userEl.value && data.username) userEl.value = data.username;
    } catch {
      // Best-effort only — leave the field blank on any failure.
    }
  }

  // ── Login flow ───────────────────────────────────────────────────────────

  async function doLogin() {
    const username = document.getElementById('login-username')?.value.trim();
    const password = document.getElementById('login-password')?.value;
    const errEl    = document.getElementById('login-error');

    if (!username || !password) {
      if (errEl) { errEl.textContent = 'Please enter username and password.'; errEl.classList.remove('hidden'); }
      return;
    }

    setLoginLoading(true);
    if (errEl) errEl.classList.add('hidden');

    try {
      const r = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await r.json();
      if (!r.ok) {
        const msg = data.detail || 'Login failed.';
        if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
        return;
      }
      setToken(data.access_token);
      hideLogin();
      updateUserDisplay();
      // Signal the app that auth succeeded
      document.dispatchEvent(new CustomEvent('ea:authenticated', { detail: { user: data.egeria_user } }));
    } catch (e) {
      if (errEl) { errEl.textContent = `Connection error: ${e.message}`; errEl.classList.remove('hidden'); }
    } finally {
      setLoginLoading(false);
    }
  }

  function doLogout() {
    clearToken();
    updateUserDisplay();
    fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    showLogin();
  }

  // ── Portal SSO ───────────────────────────────────────────────────────────

  async function exchangePortalToken(portalToken) {
    try {
      const r = await fetch('/api/auth/portal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ portal_token: portalToken }),
      });
      if (!r.ok) return false;
      const data = await r.json();
      setToken(data.access_token);
      updateUserDisplay();
      document.dispatchEvent(new CustomEvent('ea:authenticated', { detail: { user: data.egeria_user } }));
      return true;
    } catch {
      return false;
    }
  }

  async function loadEmbedOrigins() {
    try {
      const r = await _origFetch('/api/embed/config');
      if (r.ok) embedOrigins = (await r.json()).embed_origins || [];
    } catch {
      embedOrigins = [];   // unreachable: accept no postMessage at all (fail closed)
    }
  }

  async function acceptEmbedToken(jwt, origin) {
    let r;
    try {
      r = await _origFetch('/api/embed/whoami', { headers: { 'Authorization': `Bearer ${jwt}` } });
    } catch {
      showEmbedSignIn("EA couldn't check your Studio session. Try again from Studio.");
      return false;
    }
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).detail || ''; } catch {}
      clearToken();
      updateUserDisplay();
      showEmbedSignIn(r.status === 403 ? detail : 'Sign in to Studio to use EA here.');
      return false;
    }
    // Studio refreshes its short-lived JWT while the user is active (TR-10) and hands each new one over. The same
    // user's same session (same sid) just swaps the token in: no re-start, no sidebar reload.
    const before = getUser();
    setToken(jwt);
    embedParent = origin;
    hideEmbedSignIn();
    const after = getUser();
    if (before && after && before.sid && before.sid === after.sid && before.sub === after.sub) return true;
    updateUserDisplay();
    document.dispatchEvent(new CustomEvent('ea:authenticated', { detail: { user: after?.sub, embed: true } }));
    return true;
  }

  function postToParent(message) {
    // Only ever to an exact, allowed origin: the one Studio's session came from, else each listed origin (a
    // postMessage to an origin the parent doesn't have is dropped by the browser, so only the real parent hears it).
    if (!EMBED || window.parent === window) return;
    const targets = embedParent ? [embedParent]
      : (() => { const a = window.location.ancestorOrigins?.[0]; return a && embedOrigins.includes(a) ? [a] : embedOrigins; })();
    for (const t of targets) window.parent.postMessage(message, t);
  }

  function listenForPortalMessage() {
    window.addEventListener('message', async (event) => {
      // Only configured origins (ADVISOR_EMBED_ORIGINS) and EA itself; before E1 one Portal origin was hard-coded.
      const allowed = [...embedOrigins, window.location.origin];
      if (!allowed.includes(event.origin)) return;
      const data = event.data || {};
      if (EMBED && data.type === 'trellis_auth') {
        if (event.source !== window.parent || !embedOrigins.includes(event.origin)) return;
        if (typeof data.jwt !== 'string' || !data.jwt) return;
        await acceptEmbedToken(data.jwt, event.origin);
        return;
      }
      if (data.type !== 'egeria_auth' || !data.portal_token) return;
      await exchangePortalToken(data.portal_token);
    });
  }

  async function checkUrlFragment() {
    const hash = window.location.hash;
    if (!hash.startsWith('#pt=')) return false;
    const portalToken = hash.slice(4);
    // Clear the fragment immediately so credentials don't stay in the URL bar
    history.replaceState(null, '', window.location.pathname + window.location.search);
    return await exchangePortalToken(portalToken);
  }

  // ── Handle 401 responses ─────────────────────────────────────────────────

  function handle401() {
    clearToken();
    updateUserDisplay();
    showLogin('Your session has expired. Please sign in again.');
  }

  // Wrap window.fetch once so ANY API call that gets a 401 triggers the
  // re-login overlay — not just the couple of call sites that used to check
  // this manually. Individual call sites can still check r.status === 401
  // themselves for their own early-return/spinner-reset behavior; this just
  // guarantees the overlay always appears too, even from call sites that
  // never checked. Auth's own login/portal-exchange endpoints are excluded:
  // a 401 there means "wrong credentials", not "your session expired", and
  // they already show their own inline error message — firing handle401
  // there would clear the in-progress login attempt and show a confusing
  // "session expired" message instead of "invalid credentials".
  const _origFetch = window.fetch.bind(window);
  const _AUTH_ENDPOINTS_EXCLUDED_FROM_401_HANDLING = ['/api/auth/login', '/api/auth/portal'];
  window.fetch = async (...args) => {
    // Proactive check: most endpoints (e.g. /api/query) never actually
    // return 401 for an expired token — get_current_user() in auth.py
    // deliberately never raises, it just silently falls back to anonymous
    // and callers decide what "anonymous" means for them (often a plain
    // 200 response with an in-band "please sign in" message). A reactive
    // 401 check alone would miss this entirely, so also check locally: if
    // we're sending a token that's already expired by its own `exp` claim,
    // treat that the same as a 401 — no need to wait for a round-trip that
    // may never signal failure. Only fires once per expiry (guarded by
    // whether a token is still present — handle401 clears it).
    const token = getToken();
    if (token && !isAuthenticated()) {
      handle401();
    }
    const response = await _origFetch(...args);
    if (response.status === 401) {
      const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
      const isExcluded = _AUTH_ENDPOINTS_EXCLUDED_FROM_401_HANDLING.some(p => url.includes(p));
      if (!isExcluded) handle401();
    }
    return response;
  };

  // ── Init ─────────────────────────────────────────────────────────────────

  async function init(onReady) {
    // Wire up login form submit
    document.getElementById('login-submit-btn')?.addEventListener('click', doLogin);
    document.getElementById('login-password')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') doLogin();
    });
    document.getElementById('login-username')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById('login-password')?.focus();
    });

    // Wire up logout button
    document.getElementById('logout-btn')?.addEventListener('click', doLogout);

    // Deferred app start, for the login-required case below: registered here,
    // BEFORE the sidebar-refresh listener, because listeners fire in
    // registration order and `loadReports`/`loadPlans`/`loadDrafts` expect the
    // app to have been started. `once` — a later re-login must not start it
    // twice. The flag is what makes this a no-op on every other path.
    let appStartDeferred = false;
    document.addEventListener('ea:authenticated', () => {
      if (appStartDeferred) onReady && onReady();
    }, { once: true });

    // After login, refresh Egeria-dependent sidebar sections
    document.addEventListener('ea:authenticated', () => {
      updateUserDisplay();
      if (typeof loadReports === 'function') loadReports();
      if (typeof loadPlans   === 'function') loadPlans();
      if (typeof loadDrafts  === 'function') loadDrafts();
    });

    // The postMessage listener accepts only the configured origins, so load them first.
    await loadEmbedOrigins();
    listenForPortalMessage();

    if (EMBED) {
      // No password form in embed mode: remove it, so it can't be shown or filled in.
      document.documentElement.classList.add('ea-embed');
      document.getElementById('login-overlay')?.remove();
      document.getElementById('login-header-btn')?.remove();
      await loadPolicy();
      // Never reuse a session stored earlier in this frame: only the one Studio hands over now counts, so signing
      // out of Studio (or signing in as someone else) is what the panel follows.
      clearToken();
      updateUserDisplay();
      appStartDeferred = true;
      postToParent({ type: 'ea.ready' });
      setTimeout(() => { if (!isAuthenticated()) showEmbedSignIn('Sign in to Studio to use EA here.'); }, EMBED_WAIT_MS);
      return;
    }

    // Ask the server whether login is required before deciding what to do with
    // an unauthenticated visitor. Runs alongside the Portal fragment check
    // rather than before it, so the SSO path pays nothing for it.
    const [, fromPortal] = await Promise.all([loadPolicy(), checkUrlFragment()]);

    // Already have a valid token (or just got one from portal — the Portal
    // exchange route is public, so an embedded Advisor never sees this form).
    if (isAuthenticated()) {
      updateUserDisplay();
      onReady && onReady();
      return;
    }

    // No valid token. When the server requires login (the default since
    // 2026-09-04), show the form and do NOT start the app: every panel it
    // would populate is a 401, which is exactly the broken page this replaces.
    // `ea:authenticated` starts it once sign-in succeeds.
    await prefillLoginDefaults();
    if (loginRequired) {
      appStartDeferred = true;
      showLogin('Sign in with your Egeria user id to continue.');
      return;
    }

    // Dev-only anonymous-read mode: reads work without a token, so start the
    // app and leave the (dismissible) overlay up for whoever wants to sign in.
    onReady && onReady();
    showLogin();
  }

  const embed = { active: EMBED, parentOrigin: () => embedParent, post: postToParent, origins: () => embedOrigins.slice() };
  return { init, isAuthenticated, getToken, getHeaders, getUser, showLogin, hideLogin, doLogout, handle401, updateUserDisplay, embed };
})();

// A top-level `const` is a global binding but NOT a property of window, so `window.Auth` / `root.Auth` were
// undefined. dashboards_view.js, dashboards_author.js and specs_publish.js read it that way and sent their
// requests with no Authorization header; the 401 that followed went through handle401 above and signed the
// user out of the whole of EA. Export it explicitly so both spellings name the same object.
window.Auth = Auth;
