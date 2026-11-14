/* Tiny DOM helpers: every text goes in as text (never innerHTML), so nothing from Egeria is interpreted. */
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

/** Read a response's JSON and its words: {ok, status, data, detail}. */
export async function readJson(r) {
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  const detail = (data && (data.detail || data.words)) || (r.ok ? '' : `HTTP ${r.status}`);
  return { ok: r.ok, status: r.status, data, detail: typeof detail === 'string' ? detail : JSON.stringify(detail) };
}

/** append() that skips null/undefined/false (DOM append would print "null"). */
export function add(el, ...kids) {
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k);
  return el;
}
