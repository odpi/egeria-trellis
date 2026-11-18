/* Egeria Advisor's look picker: Dark (the default), Light, Paper, shared with Resource Explorer (A-U3).
 *
 * The look itself is trellis-ui's: themes.css's variables per `data-look` on <html>, set before first paint by the
 * few lines in each page's <head>, and switched by look.js with no reload. EA's colours follow it through
 * ea_tailwind.js and ea_looks.css. What this module adds is EA's own part:
 *
 *   - the control, in the header element `[data-look-host]`;
 *   - no activity row and no "Tell us ›": EA has no look-change log and no feedback form listening for it;
 *   - Mermaid and Vega figures, which are DRAWN with a theme rather than styled by variables, are drawn again on
 *     `trellis-look-change`. Their sources are remembered as they are first drawn (mermaid.run and vegaEmbed are
 *     wrapped once), so a redraw needs nothing from the code that made them.
 */
import { configureLook, getLook, LOOK_EVENT, wireLook } from '/static/trellis-ui/look.js';

configureLook({ recordUrl: null, feedback: false });

const mermaidTheme = (look) => (look === 'dark' ? 'dark' : 'default');
const vegaTheme = (look) => (look === 'dark' ? 'dark' : undefined);

/** Mermaid: set the theme for the look, and remember each diagram's source as it is first run. */
function wrapMermaid(win) {
  const m = win.mermaid;
  if (!m || m.__eaLook) return;
  m.initialize({ startOnLoad: false, theme: mermaidTheme(getLook()), securityLevel: 'strict' });
  const run = m.run.bind(m);
  m.run = (opts = {}) => {
    for (const node of opts.nodes || []) {
      if (!node.dataset.eaMermaidSource) node.dataset.eaMermaidSource = node.textContent;
    }
    return run(opts);
  };
  m.__eaLook = true;
}

const vegaDrawn = [];   // [{ el, spec, opts }]; an element no longer in the page is dropped on the next redraw

/** Vega: remember each embed, and draw it with the look's theme (EA asked for 'dark' everywhere). */
function wrapVega(win) {
  const embed = win.vegaEmbed;
  if (typeof embed !== 'function' || embed.__eaLook) return;
  const wrapped = (el, spec, opts = {}) => {
    vegaDrawn.push({ el, spec, opts });
    return embed(el, spec, { ...opts, theme: vegaTheme(getLook()) });
  };
  wrapped.__eaLook = true;
  wrapped.__original = embed;
  win.vegaEmbed = wrapped;
}

/** Draw every remembered figure again in the look now on the page. */
export function redrawFigures(doc = document) {
  const win = doc.defaultView;
  const look = getLook();
  const m = win.mermaid;
  if (m) {
    m.initialize({ startOnLoad: false, theme: mermaidTheme(look), securityLevel: 'strict' });
    const nodes = [...doc.querySelectorAll('[data-ea-mermaid-source]')];
    for (const n of nodes) {
      n.removeAttribute('data-processed');
      n.textContent = n.dataset.eaMermaidSource;
    }
    if (nodes.length) m.run({ nodes });
  }
  const embed = win.vegaEmbed && win.vegaEmbed.__original;
  if (embed) {
    for (let i = vegaDrawn.length - 1; i >= 0; i -= 1) {
      const { el, spec, opts } = vegaDrawn[i];
      if (!doc.contains(el)) { vegaDrawn.splice(i, 1); continue; }
      el.innerHTML = '';
      embed(el, spec, { ...opts, theme: vegaTheme(look) }).catch(() => {});
    }
  }
}

/** Wire the control into the page's `[data-look-host]` and keep its figures in step with the look. */
export function startLook(doc = document) {
  const win = doc.defaultView;
  wrapMermaid(win);
  wrapVega(win);
  const host = doc.querySelector('[data-look-host]');
  if (host) wireLook(host);
  doc.addEventListener(LOOK_EVENT, () => redrawFigures(doc));
}

startLook();
