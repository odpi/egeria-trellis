/* Studio's page: sign in, then the app. S0 is the shell; S1+ fill #st-main. */
import { api, init, signOut } from '/static/studio_auth.js';

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
  if (!document.querySelector('[data-studio-app]')) {
    main.innerHTML = '<p class="st-muted">Signed in. Nothing to author yet in this build.</p>';
  }
}

document.addEventListener('studio:signed-out', () => { main.innerHTML = ''; document.getElementById('st-user').textContent = ''; });
start();
