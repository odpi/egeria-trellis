/* The look of /next: Dark (the default), Light, Paper.
 * RULING-THEMES-PORTAL-DARK-LIGHT.md (2026-10-20), section 1.
 *
 * A look is the `data-look` attribute on <html>. themes.css turns it into the
 * colour, font and radius variables everything else reads, so switching is one
 * attribute write: no reload, no re-render of the page. What was DRAWN from the
 * tokens at draw time (Plotly figures, server-rendered diagrams) repaints on the
 * `re-look-change` event, which app.js listens for.
 *
 * Remembered per viewer in localStorage and read BEFORE first paint by the few
 * lines in index.html's <head>; this module only writes it. Storage can throw
 * (private window, blocked site data), so every access is wrapped, and no
 * storage simply means Dark.
 *
 * Imports nothing, on purpose: the render harness loads it on its own.
 */

export const LOOK_KEY = 're-next.look';
export const DEFAULT_LOOK = 'dark';

export const LOOKS = [
  { id: 'dark', name: 'Dark', blurb: "the Portal's dark look" },
  { id: 'light', name: 'Light', blurb: "the Portal's light look" },
  { id: 'paper', name: 'Paper', blurb: "RE's paper look, kept for this release" },
];

const byId = (id) => LOOKS.find((l) => l.id === id);

/** The look now on the page. Anything unrecognised is Dark. */
export function getLook() {
  const v = document.documentElement.getAttribute('data-look');
  return byId(v) ? v : DEFAULT_LOOK;
}

/** Switch look. Applies at once (no reload), remembers it, fires
 *  `re-look-change` on `document` (bubbling; `detail.look`, `detail.previous`),
 *  and records one activity row ("look changed to Light"). Returns true when
 *  the look actually changed. An unknown name changes nothing. */
export function setLook(id, { record = true } = {}) {
  if (!byId(id)) return false;
  const previous = getLook();
  if (id === previous && document.documentElement.getAttribute('data-look') === id) return false;
  document.documentElement.setAttribute('data-look', id);
  try { localStorage.setItem(LOOK_KEY, id); } catch (_) { /* not fatal: it just will not be remembered */ }
  document.dispatchEvent(new CustomEvent('re-look-change', {
    bubbles: true, detail: { look: id, previous },
  }));
  if (record && id !== previous) recordLookChange(id, previous);
  return id !== previous;
}

/** One activity row per change, through the existing activity log. Best effort:
 *  the look has already changed, and a failed write must not undo that. */
function recordLookChange(look, previous) {
  try {
    const p = fetch('/api/activity/look-change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ look, previous }),
    });
    if (p && typeof p.catch === 'function') p.catch(() => { /* see above */ });
  } catch (_) { /* see above */ }
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

let wired = null;

/** The menu is anchored to the control's LEFT edge (left-0). If it would still run past either side of the viewport,
 *  shift it back inside, so Dark / Light / Paper and "Tell us ›" can always be read and picked. */
function clampToViewport(menu) {
  menu.style.transform = '';
  if (typeof menu.getBoundingClientRect !== 'function') return;
  const r = menu.getBoundingClientRect();
  const vw = (typeof window !== 'undefined' && window.innerWidth) || 0;
  if (!vw || !r.width) return;
  const margin = 8;
  let dx = 0;
  if (r.right > vw - margin) dx = vw - margin - r.right;
  if (r.left + dx < margin) dx = margin - r.left;
  if (dx) menu.style.transform = `translateX(${Math.round(dx)}px)`;
}

/** Build the "Look: Dark ▾" control into `host` (an empty element beside the
 *  text-size control). Wired once per host; calling again is harmless. */
export function wireLook(host) {
  if (!host || wired === host) return;
  wired = host;
  host.classList.add('relative');
  host.innerHTML = `
    <button type="button" data-look-toggle aria-haspopup="true" aria-expanded="false"
      class="cursor-pointer bg-transparent px-[5px] text-chrome-muted" title="Choose the look">
      Look: <strong data-look-current class="font-semibold text-chrome-ink"></strong> ▾</button>
    <div data-look-menu role="menu" hidden
      class="absolute left-0 top-full z-[80] mt-[4px] w-[min(320px,86vw)] rounded-md border border-chrome-line bg-chrome-alt p-s2 shadow-md">
      ${LOOKS.map((l) => `
        <button type="button" role="menuitemradio" data-look-option="${esc(l.id)}" aria-checked="false"
          class="flex w-full cursor-pointer items-baseline gap-s2 rounded-sm border-0 bg-transparent px-s2 py-[5px] text-left text-chrome-ink hover:bg-chrome-surface">
          <span data-look-mark class="w-[1em] text-accent-on-dark" aria-hidden="true"></span>
          <span><span class="font-semibold">${esc(l.name)}</span>
            <span class="block text-provenance text-chrome-muted">${esc(l.blurb)}</span></span>
        </button>`).join('')}
      <div class="mt-s1 border-t border-chrome-line-soft px-s2 pt-s2 text-provenance text-chrome-muted">
        Which reads best for you?
        <button type="button" data-look-tell
          class="cursor-pointer border-0 bg-transparent p-0 text-accent-on-dark underline">Tell us ›</button>
      </div>
    </div>`;

  const toggle = host.querySelector('[data-look-toggle]');
  const menu = host.querySelector('[data-look-menu]');
  const open = (on) => {
    menu.hidden = !on;
    if (on) clampToViewport(menu);
    toggle.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  const paint = () => {
    const now = getLook();
    host.querySelector('[data-look-current]').textContent = byId(now).name;
    host.querySelectorAll('[data-look-option]').forEach((b) => {
      const on = b.dataset.lookOption === now;
      b.setAttribute('aria-checked', on ? 'true' : 'false');
      b.querySelector('[data-look-mark]').textContent = on ? '●' : '';
    });
  };

  toggle.addEventListener('click', (e) => { e.stopPropagation(); open(menu.hidden); });
  host.querySelectorAll('[data-look-option]').forEach((b) =>
    b.addEventListener('click', () => { setLook(b.dataset.lookOption); open(false); }));
  host.querySelector('[data-look-tell]').addEventListener('click', () => {
    open(false);
    const name = byId(getLook()).name;
    // feedback.js listens for this; it is not imported here (it imports app.js).
    document.dispatchEvent(new CustomEvent('re-open-feedback', { detail: { subject: `Look: ${name}` } }));
  });
  document.addEventListener('click', (e) => { if (!menu.hidden && !host.contains(e.target)) open(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) { open(false); toggle.focus(); } });
  // Anything that changes the look (this menu, or another tab's write to the
  // same page through setLook) repaints the label and the mark.
  document.addEventListener('re-look-change', paint);
  paint();
}
