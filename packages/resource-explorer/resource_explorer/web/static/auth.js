/**
 * Resource Explorer — Auth module
 *
 * Deliberately the same shape as Egeria Advisor's `advisor/web/static/auth.js`:
 * a token in sessionStorage, one `window.fetch` wrapper, a non-dismissible
 * login overlay, and Portal SSO by postMessage or URL fragment. Two apps with
 * two different browser-side auth designs is the drift this project has been
 * bitten by before.
 *
 * **Why the fetch wrapper rather than editing call sites.** index.html makes
 * over two hundred `fetch()` calls. Threading an Authorization header through
 * every one of them would be a large diff whose failure mode is silent — one
 * missed call site 401s at some point in the future, in some panel nobody
 * opened during review. Wrapping `fetch` once means every call site, including
 * ones added tomorrow, is authenticated by construction.
 *
 * Usage:
 *   Auth.init(onReady)       — call from DOMContentLoaded; fires onReady() when
 *                              the app may start (immediately when a token is
 *                              already held, after sign-in otherwise)
 *   Auth.getUser()           — decoded token claims, or null
 *   Auth.getHeaders()        — { Authorization } for a hand-built request
 */
const Auth = (() => {
  const TOKEN_KEY = 're_token';

  // ── Token helpers ────────────────────────────────────────────────────────

  function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
  }

  function setToken(token) {
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
  }

  function clearToken() {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
  }

  function getUser() {
    const token = getToken();
    if (!token) return null;
    try {
      // Payload only, no signature check — the server verifies on every
      // request. This is for display and for the local expiry check below.
      return JSON.parse(atob(token.split('.')[1]));
    } catch {
      return null;
    }
  }

  function isAuthenticated() {
    const claims = getUser();
    if (!claims) return false;
    return claims.exp > Math.floor(Date.now() / 1000);
  }

  function getHeaders() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  }

  // ── Policy ───────────────────────────────────────────────────────────────

  // Assumed true until the server says otherwise: assuming "login optional"
  // and being wrong means starting the app into a page where every panel is a
  // failed fetch, which is the broken page this exists to prevent.
  let loginRequired = true;

  async function loadPolicy() {
    try {
      const r = await _origFetch('/api/auth/policy');
      if (!r.ok) return;
      const data = await r.json();
      loginRequired = data.login_required !== false;
    } catch {
      // Server unreachable — keep the safe default.
    }
  }

  // ── Overlay ──────────────────────────────────────────────────────────────

  function showLogin(message) {
    const overlay = document.getElementById('login-overlay');
    if (!overlay) return;
    if (message) {
      const msg = document.getElementById('login-message');
      if (msg) { msg.textContent = message; msg.classList.remove('hidden'); }
    }
    // `style.display`, not a class: the overlay's layout is inline (see its
    // comment in index.html), and an inline `display` beats any class rule —
    // so a class-based toggle silently does nothing here.
    overlay.style.display = 'flex';
    setTimeout(() => document.getElementById('login-username')?.focus(), 50);
  }

  function hideLogin() {
    // Not dismissible while the server requires login and nobody is signed in:
    // hiding it would reveal a shell whose every API call 401s.
    if (loginRequired && !isAuthenticated()) return;
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.style.display = 'none';
    document.dispatchEvent(new CustomEvent('re:login-closed'));
    document.getElementById('login-message')?.classList.add('hidden');
    const err = document.getElementById('login-error');
    if (err) { err.textContent = ''; err.classList.add('hidden'); }
  }

  function setLoginLoading(loading) {
    const btn = document.getElementById('login-submit-btn');
    if (!btn) return;
    btn.disabled = loading;
    btn.textContent = loading ? 'Signing in…' : 'Sign in';
  }

  // Prefill from the server's configured default user, for local convenience.
  // Never touches the password field and never overwrites what was typed.
  async function prefillLoginDefaults() {
    const userEl = document.getElementById('login-username');
    if (!userEl || userEl.value) return;
    try {
      const r = await _origFetch('/api/auth/defaults');
      if (!r.ok) return;
      const data = await r.json();
      if (!userEl.value && data.username) userEl.value = data.username;
    } catch { /* best effort */ }
  }

  // ── Login flow ───────────────────────────────────────────────────────────

  async function doLogin() {
    const username = document.getElementById('login-username')?.value.trim();
    const password = document.getElementById('login-password')?.value;
    const errEl = document.getElementById('login-error');

    if (!username || !password) {
      if (errEl) { errEl.textContent = 'Please enter user id and password.'; errEl.classList.remove('hidden'); }
      return;
    }

    setLoginLoading(true);
    if (errEl) errEl.classList.add('hidden');
    try {
      const r = await _origFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await r.json();
      if (!r.ok) {
        if (errEl) { errEl.textContent = data.detail || 'Sign-in failed.'; errEl.classList.remove('hidden'); }
        return;
      }
      setToken(data.access_token);
      // Clear the password field before anything else can read it back.
      const pwEl = document.getElementById('login-password');
      if (pwEl) pwEl.value = '';
      hideLogin();
      document.dispatchEvent(new CustomEvent('re:authenticated', { detail: { user: data.egeria_user } }));
    } catch (e) {
      if (errEl) { errEl.textContent = `Connection error: ${e.message}`; errEl.classList.remove('hidden'); }
    } finally {
      setLoginLoading(false);
    }
  }

  function doLogout() {
    clearToken();
    _origFetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    // A full reload rather than a re-render: the page is full of data the
    // signed-out user should no longer be looking at, and clearing it panel by
    // panel is a list nobody keeps up to date.
    window.location.reload();
  }

  // ── Portal SSO ───────────────────────────────────────────────────────────

  async function exchangePortalToken(portalToken) {
    try {
      const r = await _origFetch('/api/auth/portal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ portal_token: portalToken }),
      });
      if (!r.ok) return false;
      const data = await r.json();
      setToken(data.access_token);
      document.dispatchEvent(new CustomEvent('re:authenticated', { detail: { user: data.egeria_user } }));
      return true;
    } catch {
      return false;
    }
  }

  function listenForPortalMessage() {
    window.addEventListener('message', async (event) => {
      const allowed = ['http://localhost:8885', window.location.origin];
      if (!allowed.includes(event.origin)) return;
      if (event.data?.type !== 'egeria_auth' || !event.data?.portal_token) return;
      await exchangePortalToken(event.data.portal_token);
    });
  }

  async function checkUrlFragment() {
    const hash = window.location.hash;
    if (!hash.startsWith('#pt=')) return false;
    const portalToken = hash.slice(4);
    // Clear the fragment immediately so a credential does not sit in the URL bar.
    history.replaceState(null, '', window.location.pathname + window.location.search);
    return await exchangePortalToken(portalToken);
  }

  // ── The fetch wrapper ────────────────────────────────────────────────────

  function handle401() {
    clearToken();
    showLogin('Your session has expired. Please sign in again.');
  }

  const _origFetch = window.fetch.bind(window);
  // Auth's own endpoints are excluded from the 401 handler: a 401 there means
  // "wrong credentials", not "your session expired", and they show their own
  // inline error. Firing handle401 would replace that with a misleading
  // "session expired" and clear the in-progress attempt.
  const _AUTH_ENDPOINTS = ['/api/auth/login', '/api/auth/portal'];

  function _isSameOrigin(url) {
    // Only OUR requests get the token. A relative URL is ours by definition;
    // an absolute one has to match this origin. Sending the app JWT to a third
    // party because some panel fetched an external URL would be handing out a
    // credential, and the wrapper is exactly the place that would happen
    // invisibly.
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch {
      return false;
    }
  }

  window.fetch = async (...args) => {
    const input = args[0];
    const url = typeof input === 'string' ? input : (input?.url || '');
    const token = getToken();

    // Proactive expiry check: a token that has already lapsed by its own `exp`
    // will 401, and asking the server first buys a round trip and a flash of
    // broken UI. Guarded by the token still being present — handle401 clears
    // it — so this fires once per expiry, not once per request.
    if (token && !isAuthenticated() && !_AUTH_ENDPOINTS.some(p => url.includes(p))) {
      handle401();
    }

    if (token && _isSameOrigin(url) && !_AUTH_ENDPOINTS.some(p => url.includes(p))) {
      const init = { ...(args[1] || {}) };
      const headers = new Headers(init.headers || (typeof input === 'object' ? input.headers : undefined) || {});
      if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${getToken()}`);
      init.headers = headers;
      args = [input, init];
    }

    const response = await _origFetch(...args);
    if (response.status === 401 && !_AUTH_ENDPOINTS.some(p => url.includes(p))) {
      handle401();
    }
    return response;
  };

  // ── Init ─────────────────────────────────────────────────────────────────

  async function init(onReady) {
    document.getElementById('login-submit-btn')?.addEventListener('click', doLogin);
    document.getElementById('login-password')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') doLogin();
    });
    document.getElementById('login-username')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') document.getElementById('login-password')?.focus();
    });
    document.getElementById('logout-btn')?.addEventListener('click', doLogout);

    // Deferred app start, for the login-required case. `once`, and guarded by
    // the flag, so a later re-login after an expiry does not start the app a
    // second time on top of itself.
    let appStartDeferred = false;
    document.addEventListener('re:authenticated', () => {
      if (appStartDeferred) onReady && onReady();
    }, { once: true });

    listenForPortalMessage();

    // Alongside the Portal fragment check rather than before it, so the SSO
    // path pays nothing for the policy read.
    await Promise.all([loadPolicy(), checkUrlFragment()]);

    if (isAuthenticated()) {
      onReady && onReady();
      return;
    }

    await prefillLoginDefaults();
    if (loginRequired) {
      appStartDeferred = true;
      showLogin('Sign in with your Egeria user id to continue.');
      return;
    }

    // Dev-only anonymous-read mode: reads work, so start the app and leave a
    // dismissible overlay for whoever wants to sign in.
    onReady && onReady();
    showLogin();
  }

  return { init, isAuthenticated, getToken, getHeaders, getUser, showLogin, hideLogin, doLogout, handle401 };
})();
