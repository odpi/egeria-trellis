/* Studio's page: sign in, then the app. S0 is the shell; S1+ fill #st-main. */
import { api, init, signOut } from '/static/studio_auth.js';
import { mountDrafts } from '/static/studio_drafts.js';

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
  await mountDrafts(main);
}

document.addEventListener('studio:signed-out', () => { main.innerHTML = ''; document.getElementById('st-user').textContent = ''; });
start();
