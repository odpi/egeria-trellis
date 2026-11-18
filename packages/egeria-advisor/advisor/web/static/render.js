/* The one place EA turns Markdown into HTML (Phase 0, S2).
 *
 * Text from the server, from Egeria (dashboard text placements, report output) and from the LLM is
 * untrusted. It goes through renderMarkdown(), which parses it with the vendored `marked` and then
 * sanitises the HTML with the vendored DOMPurify before anything reaches innerHTML. Never call
 * marked.parse() anywhere else: a unit test fails if a page does.
 *
 * What the sanitiser removes: <script> and every other active or embedding element (style, iframe,
 * object, embed, form controls, svg, math, template, link, meta, base), every on* event attribute,
 * inline style, and any URL whose scheme is not http, https or mailto (so javascript:, vbscript: and
 * data: links are gone; a relative URL stays). Every link gets rel="noopener noreferrer", and an
 * absolute http(s) link opens in a new tab.
 *
 * Kept on purpose: class attributes, so the fenced-code detection that turns ```mermaid and
 * ```vega-lite blocks into diagrams (code.language-mermaid, code.language-vega-lite) keeps working,
 * and the <span class="todo-fill"> preprocessMarkdown() adds.
 *
 * If either library failed to load, the text is shown as written (escaped, line breaks kept), never
 * as raw HTML.
 */
(function (root) {
  'use strict';

  const FORBID_TAGS = ['script', 'style', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet',
    'form', 'input', 'button', 'textarea', 'select', 'option', 'link', 'meta', 'base', 'svg', 'math',
    'template', 'noscript', 'audio', 'video', 'source', 'track'];
  const FORBID_ATTR = ['style', 'srcset', 'action', 'formaction', 'xlink:href'];
  // http, https, mailto, or a relative URL (no scheme). DOMPurify applies it to href, src and friends.
  const ALLOWED_URI = /^(?:(?:https?|mailto):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i;
  const ABSOLUTE_HTTP = /^https?:/i;

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function asWritten(text) {
    return escapeHtml(text).replace(/\n/g, '<br>');
  }

  let purifier = null;
  function getPurifier() {
    if (purifier) return purifier;
    const factory = root.DOMPurify;
    if (typeof factory !== 'function' || !factory.isSupported) return null;
    // Our own instance, so the link hook below never affects anyone else's DOMPurify use.
    purifier = factory(root);
    purifier.addHook('afterSanitizeAttributes', (node) => {
      if (node.nodeName === 'A' && node.hasAttribute('href')) {
        node.setAttribute('rel', 'noopener noreferrer');
        if (ABSOLUTE_HTTP.test(node.getAttribute('href') || '')) node.setAttribute('target', '_blank');
        else node.removeAttribute('target');
      }
    });
    return purifier;
  }

  /** Sanitise an HTML string against the allow-list. Returns escaped text if DOMPurify is missing. */
  function sanitizeHtml(html) {
    const p = getPurifier();
    if (!p) return asWritten(html);
    return p.sanitize(String(html == null ? '' : html), {
      USE_PROFILES: { html: true },
      FORBID_TAGS,
      FORBID_ATTR,
      ALLOWED_URI_REGEXP: ALLOWED_URI,
      ALLOW_DATA_ATTR: false,
      ALLOW_UNKNOWN_PROTOCOLS: false,
    });
  }

  /** Markdown (untrusted) -> sanitised HTML string, safe for innerHTML. */
  function renderMarkdown(text) {
    const src = String(text == null ? '' : text);
    const m = root.marked;
    if (!m || typeof m.parse !== 'function' || !getPurifier()) return asWritten(src);
    return sanitizeHtml(m.parse(src));
  }

  root.renderMarkdown = renderMarkdown;
  root.sanitizeHtml = sanitizeHtml;
  root.escapeHtml = escapeHtml;
})(typeof window !== 'undefined' ? window : globalThis);
