/* A dead session, said once.
 *
 * A signed-in header and a pane that answers 401 `login_required` is a stale session (the server
 * restarted, or the token was revoked). Before this, every pane that happened to fetch showed the
 * server's sentence as its own error, one per pane, and the header kept saying "signed in".
 *
 * re-api.js raises `re:session-dead` on the first 401 `login_required` answer. This module turns it
 * into ONE banner at the top of the page: a short word, and a visible Sign in control. Repeated
 * events do not add banners. Signing in again reloads the page, which is what repaired it by hand. */

export const SESSION_BANNER_ID = 'session-dead-banner';

/** Install the listener on `doc`. Returns a function that removes it (a test uses it). */
export function installSessionBanner(doc = document, { onDead, reload = () => globalThis.location?.reload() } = {}) {
  const show = () => {
    try { onDead?.(); } catch { /* the banner matters more than the header refresh */ }
    const existing = doc.getElementById(SESSION_BANNER_ID);
    if (existing) { rearm(existing); return; }
    const bar = doc.createElement('div');
    bar.id = SESSION_BANNER_ID;
    bar.setAttribute('role', 'alert');
    bar.className = 'sticky top-0 z-40 flex flex-wrap items-baseline gap-s3 border-b border-rule-strong bg-paper px-s3 py-s2 text-caveat text-ink';
    bar.innerHTML = `<span class="text-state-warn" aria-hidden="true">⚠</span>
      <span data-session-word>Signed out</span>
      <button type="button" data-session-signin class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[2px] text-answer text-accent-ink">Sign in</button>
      <span class="text-provenance text-ink-muted" title="Your session ended on the server. Signing in again reloads this page.">session ended</span>`;
    doc.body.insertBefore(bar, doc.body.firstChild);
    bar.querySelector('[data-session-signin]').addEventListener('click', () => {
      // An immediate pressed cue; never a permanent dead control (re-armed below).
      bar.querySelector('[data-session-signin]').textContent = 'Opening…';
      const auth = globalThis.Auth;
      if (auth && typeof auth.showLogin === 'function') auth.showLogin('Your session ended. Sign in to continue.');
      else reload();
    });
  };
  const rearm = (bar) => {
    const btn = bar.querySelector('[data-session-signin]');
    if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
  };
  // Only a banner that is showing owns a reload; any other sign-in in a live session leaves the page alone.
  const authed = () => {
    const bar = doc.getElementById(SESSION_BANNER_ID);
    if (!bar) return;
    bar.remove();
    reload();
  };
  const closed = () => { const bar = doc.getElementById(SESSION_BANNER_ID); if (bar) rearm(bar); };
  doc.addEventListener('re:login-closed', closed);
  doc.addEventListener('re:session-dead', show);
  doc.addEventListener('re:authenticated', authed);
  return () => {
    doc.removeEventListener('re:session-dead', show);
    doc.removeEventListener('re:authenticated', authed);
    doc.removeEventListener('re:login-closed', closed);
  };
}
