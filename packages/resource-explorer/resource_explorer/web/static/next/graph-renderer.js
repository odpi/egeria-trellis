/* The generic GRAPH renderer: result_kind = "graph" (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md, §2).
 *
 * Keyed by result_kind, never by analysis name. It knows nothing about deployments, repositories or Egeria: it
 * draws what its input declares, and says what the input says about itself. One call draws ONE graph (a group);
 * a caller with several groups (one per deployment) calls it once per group, so groups are never mixed.
 *
 * INPUT (one group):
 *   nodes[]          { id, label, kind?, notes? }   notes: [{ text, title? }] short chips under the label
 *   edges[]          { from, to, label?, kind }     from/to are node ids; an edge to an unknown id is dropped and counted
 *   caption          what an edge MEANS, said under the graph every time (never only in a hover)
 *   as_of            when the declaration was read ("" = unknown, and the figure says so)
 *   absence_sentence what an empty graph (or no edges) does and does not mean, from the declaration
 *   group?           { id, title } the heading for this graph
 *   edge_word?       the verb read between two names in the list form ("depends on")
 *   categories?      { exclusive, total, items: [{ label, count }] }, built by `categoriesSpec` from the declaration. Overlapping
 *                    categories are never stacked and say so; a stated total is read from its field, never summed here.
 *
 * RULES (owner): a long label is cut with an ellipsis and its hover has all of it; a label never overruns its
 * box; counts are labelled; "nothing declared" is never drawn as a result; every string is escaped.
 */

import { tableResultHtml } from '/static/next/envelope.js';
import { glyphOf } from '/static/next/glyphs.js';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

export const NODE_W = 176;
export const NODE_H = 30;
export const NOTE_H = 15;
const COL_GAP = 84;
const ROW_GAP = 10;
const PAD = 8;
const CHAR_W = 6.4;                                  // average glyph at the 11px label font
export const LABEL_CHARS = Math.floor((NODE_W - 12) / CHAR_W);
const NOTE_CHARS = LABEL_CHARS;
const EDGE_LABEL_CHARS = 14;

/** `s` cut to `max` characters with a trailing ellipsis; the full text belongs in a hover. */
export function cut(s, max) {
  const t = String(s ?? '');
  return t.length <= max ? t : `${t.slice(0, Math.max(1, max - 1))}…`;
}

/** Layer each node so every edge runs from a lower layer to a higher one where it can. A cycle cannot be
 *  layered, so the relaxation is bounded: it stops after one pass per node and leaves the cycle's back edge
 *  to be drawn as a back edge. */
export function layerNodes(nodes, edges) {
  const layer = new Map(nodes.map((n) => [n.id, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const e of edges) {
      if (e.from === e.to) continue;
      const want = layer.get(e.from) + 1;
      if (layer.get(e.to) < want && want < nodes.length) { layer.set(e.to, want); moved = true; }
    }
    if (!moved) break;
  }
  return layer;
}

/** Positions for every node: columns by layer, rows in the order given within a layer. */
export function layoutGraph(nodes, edges) {
  const layer = layerNodes(nodes, edges);
  const cols = new Map();
  for (const n of nodes) {
    const l = layer.get(n.id);
    if (!cols.has(l)) cols.set(l, []);
    cols.get(l).push(n);
  }
  const pos = new Map();
  let height = 0;
  const colKeys = [...cols.keys()].sort((a, b) => a - b);
  colKeys.forEach((l, ci) => {
    let y = PAD;
    for (const n of cols.get(l)) {
      const h = NODE_H + (n.notes && n.notes.length ? NOTE_H : 0);
      pos.set(n.id, { x: PAD + ci * (NODE_W + COL_GAP), y, h });
      y += h + ROW_GAP;
    }
    height = Math.max(height, y - ROW_GAP + PAD);
  });
  return { pos, width: PAD * 2 + colKeys.length * NODE_W + Math.max(0, colKeys.length - 1) * COL_GAP, height };
}

function nodeSvg(n, p) {
  const full = String(n.label ?? n.id);
  const notes = n.notes || [];
  const noteText = notes.map((x) => x.text).join(' · ');
  const noteTitle = notes.map((x) => x.title || x.text).join('\n');
  // The clip keeps text inside the box even if a font runs wider than the estimate.
  const clip = `gr-clip-${esc(String(n.id)).replace(/[^A-Za-z0-9_-]/g, '_')}-${Math.round(p.x)}-${Math.round(p.y)}`;
  return `<g data-graph-node="${esc(n.id)}"${n.kind ? ` data-node-kind="${esc(n.kind)}"` : ''}>
    <title>${esc(full)}${noteTitle ? `\n${esc(noteTitle)}` : ''}</title>
    <clipPath id="${clip}"><rect x="${p.x + 4}" y="${p.y}" width="${NODE_W - 8}" height="${p.h}"/></clipPath>
    <rect x="${p.x}" y="${p.y}" width="${NODE_W}" height="${p.h}" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.45"/>
    <g clip-path="url(#${clip})" fill="currentColor">
      <text x="${p.x + 6}" y="${p.y + 19}" font-size="11" data-node-label>${esc(cut(full, LABEL_CHARS))}</text>
      ${noteText ? `<text x="${p.x + 6}" y="${p.y + NODE_H + 10}" font-size="9.5" fill-opacity="0.65" data-node-notes>${esc(cut(noteText, NOTE_CHARS))}</text>` : ''}
    </g>
  </g>`;
}

function edgeSvg(e, pos, mid) {
  const a = pos.get(e.from);
  const b = pos.get(e.to);
  const forward = b.x > a.x;
  const x1 = forward ? a.x + NODE_W : a.x;
  const y1 = a.y + a.h / 2;
  const x2 = forward ? b.x : b.x + NODE_W;
  const y2 = b.y + b.h / 2;
  const dx = Math.max(24, Math.abs(x2 - x1) / 2);
  const c1 = forward ? x1 + dx : x1 - dx;
  const c2 = forward ? x2 - dx : x2 + dx;
  const label = e.label ? String(e.label) : '';
  const tip = `${e.from} → ${e.to}${label ? ` · ${label}` : ''}${e.kind ? ` (${e.kind})` : ''}`;
  return `<g data-graph-edge data-edge-from="${esc(e.from)}" data-edge-to="${esc(e.to)}" data-edge-kind="${esc(e.kind || '')}">
    <title>${esc(tip)}</title>
    <path d="M${x1},${y1} C${c1},${y1} ${c2},${y2} ${x2},${y2}" fill="none" stroke="currentColor" stroke-opacity="0.55" marker-end="url(#${mid})"/>
    ${label ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 3}" font-size="9" text-anchor="middle" fill="currentColor" fill-opacity="0.7" data-edge-label>${esc(cut(label, EDGE_LABEL_CHARS))}</text>` : ''}
  </g>`;
}


/** The words a legend says when the categories overlap (the same as the Chat compile's, declared_words.OVERLAP_NOTE). */
export const OVERLAP_NOTE = 'categories overlap';

const readPath = (obj, path) => {
  if (obj == null || !path) return undefined;
  if (Object.prototype.hasOwnProperty.call(obj, path)) return obj[path];
  let got = obj;
  for (const part of String(path).split('.')) {
    if (got == null || typeof got !== 'object' || !(part in got)) return undefined;
    got = got[part];
  }
  return got;
};

/** A declaration's `categories` and one result into what `categoriesHtml` draws. The total is the result's `sums_to`
 *  field as stated (null when none is declared or the result does not hold it): this function adds nothing up. */
export function categoriesSpec(declaration, result, items) {
  const c = declaration && declaration.categories;
  if (!c) return null;
  const stated = c.sums_to ? readPath(result, c.sums_to) : undefined;
  const n = stated == null || stated === '' ? NaN : Number(String(stated).replace(/,/g, ''));
  return { exclusive: !!c.exclusive, total: Number.isFinite(n) ? n : null, items: items || [] };
}

/** The category legend, and a stacked bar only for exclusive categories. Bars are scaled to the stated total when there
 *  is one (a remainder the categories do not explain stays blank, and the total shown is the stated one); with no stated
 *  total no total is shown. Overlapping categories get the note and no bar: a stack would count an item twice. */
export function categoriesHtml(cats) {
  if (!cats) return '';
  const items = (cats.items || []).filter((i) => i && Number.isFinite(Number(i.count)));
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const legend = items.map((i) => `<li data-category class="inline-block mr-s2"><span class="tnum">${fmt(i.count)}</span> ${esc(i.label)}</li>`).join('');
  let body = '';
  if (!cats.exclusive) {
    body = `<div data-categories-note class="text-provenance text-ink-muted">${esc(OVERLAP_NOTE)}: an item can be in more than one, so they are not stacked or added up.</div>`;
  } else if (items.length) {
    const basis = cats.total != null ? cats.total : items.reduce((a, i) => a + Number(i.count), 0);
    const segs = items.map((i, k) => `<div data-stack-segment="${esc(i.label)}" style="width:${basis > 0 ? (100 * Number(i.count) / basis).toFixed(2) : 0}%;height:10px;background:currentColor;opacity:${k % 2 ? 0.35 : 0.65}" title="${esc(i.label)}: ${fmt(i.count)}"></div>`).join('');
    body = `<div data-categories-stack class="mt-s1 rounded-sm border border-rule text-ink" style="display:flex;width:100%;overflow:hidden" role="img" aria-label="${items.length} categories">${segs}</div>`
      + (cats.total != null ? `<div data-categories-total class="text-provenance text-ink-muted">total <span class="tnum">${fmt(cats.total)}</span> (stated by the result)</div>` : '');
  }
  return `<div data-graph-categories="${cats.exclusive ? 'exclusive' : 'overlapping'}" class="mt-s1"><ul class="m-0 list-none p-0 text-provenance text-ink-muted">${legend}</ul>${body}</div>`;
}

let uid = 0;

/** One graph, as HTML: heading, the figure, the counts, the caption, the as-of, the absence sentence. */
export function graphHtml(spec) {
  const s = spec || {};
  const ids = new Set((s.nodes || []).map((n) => n.id));
  const nodes = s.nodes || [];
  const edges = (s.edges || []).filter((e) => ids.has(e.from) && ids.has(e.to));
  const dropped = (s.edges || []).length - edges.length;
  const g = s.group || null;
  const counts = `<span data-graph-counts class="tnum">${nodes.length} node${nodes.length === 1 ? '' : 's'} · ${edges.length} edge${edges.length === 1 ? '' : 's'}</span>`
    + (dropped ? ` <span data-graph-dropped>· ${dropped} edge${dropped === 1 ? '' : 's'} named a node that is not in the list and ${dropped === 1 ? 'is' : 'are'} not drawn</span>` : '');
  const asOf = s.as_of
    ? `<span data-graph-as-of>as of ${esc(s.as_of)}</span>`
    : '<span data-graph-as-of="unknown">as-of not recorded</span>';

  let figure = '';
  if (nodes.length) {
    const { pos, width, height } = layoutGraph(nodes, edges);
    const mid = `gr-arrow-${++uid}`;
    figure = `<div data-graph-figure class="mt-s1 max-w-full overflow-auto rounded-sm border border-rule text-ink">
      <svg role="img" aria-label="${esc((g && g.title) || 'graph')}: ${nodes.length} nodes, ${edges.length} edges" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" style="max-width:none">
        <defs><marker id="${mid}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" fill-opacity="0.7"/></marker></defs>
        ${edges.map((e) => edgeSvg(e, pos, mid)).join('')}
        ${nodes.map((n) => nodeSvg(n, pos.get(n.id))).join('')}
      </svg></div>`;
  }
  return `<div data-graph${g ? ` data-graph-group="${esc(g.id ?? g.title)}"` : ''} class="mb-s3">
    ${g ? `<div data-graph-title class="break-words text-caveat text-ink" title="${esc(g.title)}">${esc(g.title)}</div>` : ''}
    <div class="text-provenance text-ink-muted">${counts} · ${asOf}</div>
    ${figure}
    ${categoriesHtml(s.categories)}
    ${s.caption ? `<div data-graph-caption class="mt-s1 text-provenance text-ink-muted">${esc(s.caption)}</div>` : ''}
    ${s.absence_sentence ? `<div data-graph-absence class="text-provenance text-ink-muted">${esc(s.absence_sentence)}</div>` : ''}
  </div>`;
}

/** The renderers, keyed by result_kind. A kind with no renderer here is SAID, never drawn as an empty result. */
// B1: a table result is one block for every kind of member (envelope.js `tableResultHtml`).
export const RESULT_RENDERERS = { graph: graphHtml, table: (p) => tableResultHtml(p, esc, glyphOf) };

export function renderByKind(kind, payload) {
  const r = RESULT_RENDERERS[kind];
  return r ? r(payload) : `<div data-no-renderer="${esc(kind)}" class="text-provenance text-ink-muted">No renderer for result kind "${esc(kind)}" yet.</div>`;
}
