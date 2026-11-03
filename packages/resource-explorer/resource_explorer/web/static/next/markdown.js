/** Markdown for text people wrote (journal entries): the vendored `marked` (the same library the legacy page
 *  loads) parsed with raw HTML turned into visible text, then walked against an allow-list. The allow-list is
 *  the real guard: only these tags survive, only `href`/`title`/`colspan`/`rowspan` on them, and a link only if
 *  its scheme is http, https or mailto. Images, scripts, styles and event attributes never reach the page.
 *  If the library cannot load, the text is shown as written (escaped, line breaks kept) -- never as raw HTML. */
import { loadScript } from '/static/next/app.js';

const ALLOWED = new Set(['p', 'br', 'strong', 'b', 'em', 'i', 'del', 's', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td']);
const DROP = new Set(['script', 'style', 'iframe', 'object', 'embed', 'img', 'svg', 'math', 'template', 'noscript', 'link', 'meta', 'form', 'input', 'button', 'textarea', 'select']);
const KEEP_ATTR = { a: ['href', 'title'], th: ['colspan', 'rowspan'], td: ['colspan', 'rowspan'] };
const CLASSES = {
  ul: 'my-1 list-disc pl-s4', ol: 'my-1 list-decimal pl-s4', p: 'my-1', blockquote: 'my-1 border-l-2 border-rule pl-s2',
  pre: 'my-1 overflow-x-auto', a: 'text-accent-ink underline',
  th: 'border border-rule px-2 py-[2px] text-left font-semibold', td: 'border border-rule px-2 py-[2px]',
  table: 'border-collapse',
};
const SAFE_HREF = /^(https?:|mailto:)/i;

const escText = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const asWritten = (s) => `<div class="whitespace-pre-wrap">${escText(s)}</div>`;

let parser = null;
async function parserReady() {
  if (parser) return parser;
  if (!globalThis.marked) await loadScript('/static/vendor/marked.min.js');
  const M = globalThis.marked;
  const m = new M.Marked({ gfm: true, breaks: false, async: false,
    renderer: { html: (tok) => escText(tok.text ?? tok.raw ?? '') } });
  parser = (text) => m.parse(text);
  return parser;
}

function clean(node, doc) {
  for (const child of [...node.childNodes]) {
    if (child.nodeType === 3) continue;
    if (child.nodeType !== 1) { child.remove(); continue; }
    const tag = child.tagName.toLowerCase();
    if (DROP.has(tag)) { child.remove(); continue; }
    clean(child, doc);
    if (!ALLOWED.has(tag)) { child.replaceWith(...child.childNodes); continue; }
    const keep = KEEP_ATTR[tag] || [];
    for (const a of [...child.attributes]) if (!keep.includes(a.name)) child.removeAttribute(a.name);
    if (tag === 'a') {
      const href = (child.getAttribute('href') || '').trim();
      if (!SAFE_HREF.test(href)) child.removeAttribute('href');
      else { child.setAttribute('rel', 'noopener noreferrer'); child.setAttribute('target', '_blank'); }
    }
    if (CLASSES[tag]) child.setAttribute('class', CLASSES[tag]);
    if (tag === 'table') {
      // A wide table scrolls inside its entry rather than pushing the page wider.
      const wrap = doc.createElement('div');
      wrap.setAttribute('class', 'my-1 max-w-full overflow-x-auto');
      child.replaceWith(wrap);
      wrap.appendChild(child);
    }
  }
}

/** Sanitise an HTML string against the allow-list. Exported so a test can feed it hostile markup directly. */
export function sanitiseHtml(html) {
  const doc = document.implementation.createHTMLDocument('');
  const box = doc.createElement('div');
  box.innerHTML = html;
  clean(box, doc);
  return box.innerHTML;
}

/** Markdown text -> safe HTML. Never throws; falls back to the text as written. */
export async function renderMarkdown(text) {
  try {
    const parse = await parserReady();
    return sanitiseHtml(parse(String(text ?? '')));
  } catch {
    return asWritten(text);
  }
}

/** Show `text` as markdown inside `el`. The element is first filled with the text as written (escaped), so the
 *  surface is never empty while the parser loads; then it is replaced by the sanitised rendering, unless the
 *  element has left the page or been given other text in the meantime. The stored text is never touched. */
export async function showMarkdown(el, text) {
  if (!el) return;
  const raw = String(text ?? '');
  el.dataset.raw = raw;
  const html = await renderMarkdown(raw);
  if (el.isConnected && el.dataset.raw === raw) {
    el.classList.remove('whitespace-pre-wrap');
    el.innerHTML = html;
  }
}
