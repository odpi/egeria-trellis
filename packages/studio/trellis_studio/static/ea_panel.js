/* S4: the embedded Egeria Advisor panel (owner decision D6: Studio has no LLM; EA's chat is embedded).
 *
 * The contract (BRIEF-TRELLIS-STUDIO-V1-2026-11-14 §"The embedded EA panel", EA side in E1):
 *   - the iframe loads EGERIA_ADVISOR_URL + ?embed=studio; nothing secret is ever in its URL;
 *   - EA posts {type:'ea.ready'}; Studio checks event.source (the iframe) and event.origin (EA's exact origin) and
 *     answers {type:'trellis_auth', jwt} targeted at EA's exact origin, only while Studio's session is valid;
 *   - EA posts {type:'studio.openDraft', draft_ref:{kind, name}}: Studio checks source and origin and opens the
 *     draft by reference, read through Studio's own API as the user (nothing in the message is trusted as data);
 *   - Studio posts {type:'ea.askAbout', draft_ref} to EA's exact origin.
 *   - No ea.ready within `timeoutMs`: the panel says "EA isn't reachable at <url>" and Studio carries on.
 *
 * Dependency-free on purpose (no imports, a DOM passed in) so tests drive it under jsdom.
 */
export function createEaPanel(opts) {
  const { advisorUrl, getToken, onOpenDraft, doc = document, win = window, timeoutMs = 8000 } = opts;
  const KINDS = ['spec', 'report', 'sheet'];
  let origin = '';
  let src = '';
  try {
    const base = new URL(advisorUrl);
    origin = base.origin;
    const u = new URL(base.href.endsWith('/') ? base.href : `${base.href}/`);
    u.search = '?embed=studio';
    u.hash = '';
    src = u.href;
  } catch (e) { origin = ''; }

  const root = doc.createElement('section');
  root.className = 'st-ea';
  root.setAttribute('aria-label', 'Egeria Advisor');
  const status = doc.createElement('p');
  status.className = 'st-ea-status st-muted';
  status.setAttribute('role', 'status');
  const frame = doc.createElement('iframe');
  frame.className = 'st-ea-frame';
  frame.title = 'Egeria Advisor';
  frame.referrerPolicy = 'no-referrer';
  root.append(status, frame);

  const state = { ready: false, sent: false, timer: null, unreachable: false };
  const say = (text, cls = 'st-muted') => { status.textContent = text; status.className = `st-ea-status ${cls}`; status.hidden = !text; };

  function sendSession() {
    const jwt = getToken();
    if (!jwt) { say('Sign in to Studio to use EA here.', 'st-warn'); return false; }
    frame.contentWindow.postMessage({ type: 'trellis_auth', jwt }, origin);
    state.sent = true;
    say('');
    return true;
  }

  function onMessage(event) {
    if (!origin || event.origin !== origin) return;            // only EA's exact origin
    if (event.source !== frame.contentWindow) return;          // only our own iframe
    const d = event.data || {};
    if (d.type === 'ea.ready') {
      state.ready = true;
      state.unreachable = false;
      if (state.timer) { win.clearTimeout(state.timer); state.timer = null; }
      sendSession();
      return;
    }
    if (d.type === 'studio.openDraft') {
      const ref = d.draft_ref || {};
      if (!KINDS.includes(ref.kind) || typeof ref.name !== 'string' || !ref.name.trim()) return;
      onOpenDraft({ kind: ref.kind, name: ref.name });           // Studio re-reads it as the user
    }
  }

  function load() {
    if (!origin) { say(`EA's address (${advisorUrl || 'none'}) isn't a URL Studio can use.`, 'st-error'); return; }
    state.ready = false; state.sent = false;
    say(`Connecting to EA at ${advisorUrl}…`);
    frame.src = src;
    if (state.timer) win.clearTimeout(state.timer);
    state.timer = win.setTimeout(() => {
      if (state.ready) return;
      state.unreachable = true;
      say(`EA isn't reachable at ${advisorUrl}. Studio works without it.`, 'st-warn');
    }, timeoutMs);
  }

  function askAbout(ref) {
    if (!state.ready || !origin) { say(`EA isn't connected, so it can't be asked about ${ref.name}.`, 'st-warn'); return false; }
    frame.contentWindow.postMessage({ type: 'ea.askAbout', draft_ref: { kind: ref.kind, name: ref.name } }, origin);
    return true;
  }

  /** Studio's session changed (signed in again): hand EA the new one; signed out: EA gets nothing more. */
  function sessionChanged() { if (state.ready) sendSession(); }

  win.addEventListener('message', onMessage);
  return { root, frame, load, askAbout, sessionChanged, state, origin, src,
           destroy: () => win.removeEventListener('message', onMessage) };
}
