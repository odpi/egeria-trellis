/* Studio's page: sign in, then the app. S0 is the shell; S1+ fill #st-main. */
import { api, init, signOut, sessionToken } from '/static/studio_auth.js';
import { mountDrafts, studioDrafts } from '/static/studio_drafts.js';
import { createEaPanel } from '/static/ea_panel.js';
import { makeSplit } from '/static/panes.js';

const main = document.getElementById('st-main');

async function start() {
  const who = await init();
  document.getElementById('st-user').textContent = (who && (who.display_name || who.user_id || who.sub)) || '';
  const out = document.getElementById('st-signout');
  out.hidden = false;
  out.onclick = () => signOut();

  const r = await api('/api/config');
  if (!r.ok) { main.innerHTML = '<p class="st-error">Studio could not load its settings.</p>'; return; }
  window.STUDIO_CONFIG = await r.json();
  document.dispatchEvent(new CustomEvent('studio:ready', { detail: window.STUDIO_CONFIG }));
  // The app on the left, EA's chat (D6: Studio's only LLM help) in a resizable panel on the right.
  const appHost = document.createElement('div');
  const ea = createEaPanel({
    advisorUrl: window.STUDIO_CONFIG.advisor_url,
    getToken: sessionToken,
    onOpenDraft: (ref) => studioDrafts.openDraft(ref.kind, ref.name),   // by reference, read as you
  });
  main.innerHTML = '';
  main.append(makeSplit('studio-ea', [{ el: appHost, grow: true }, { el: ea.root, width: 380, min: 240 }]));
  await mountDrafts(appHost);
  wireEaToggle(ea);
  document.addEventListener('studio:session', () => ea.sessionChanged());
  document.addEventListener('studio:session-ended', () => ea.unload());
  document.addEventListener('studio:ask-ea', (e) => { showEa(ea, true); ea.askAbout(e.detail); });
}

const EA_KEY = 'studio.ea.open';
function showEa(ea, open) {
  ea.root.hidden = !open;
  const gutter = ea.root.previousElementSibling;
  if (gutter && gutter.classList.contains('st-gutter')) gutter.hidden = !open;
  try { localStorage.setItem(EA_KEY, open ? '1' : '0'); } catch (e) {}
  const btn = document.getElementById('st-ea-toggle');
  if (btn) btn.setAttribute('aria-pressed', String(open));
  if (open && !ea.frame.getAttribute('src')) ea.load();
}

function wireEaToggle(ea) {
  const btn = document.getElementById('st-ea-toggle');
  btn.hidden = false;
  btn.onclick = () => showEa(ea, ea.root.hidden);
  let open = true;
  try { open = localStorage.getItem(EA_KEY) !== '0'; } catch (e) {}
  showEa(ea, open);
}

start();
