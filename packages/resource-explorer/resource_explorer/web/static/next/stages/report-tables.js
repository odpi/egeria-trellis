/* Small real tables for the database report (parity slice G3): survey history, ranked tables,
 * views. One helper draws them all in the resizable-column pattern of colresize.js:
 *  - every column is a fixed width the person can drag; the first column is left-aligned, the
 *    others are centred, headers use the same alignment and widths as their cells;
 *  - a word is never broken mid-word (colresize's `min` keeps the longest word whole);
 *  - when the columns outgrow the page the table scrolls inside its own box, never the page.
 *
 * Imports colresize.js only, so a plain page or a node test can load it. */
import { cellStyle, handleHtml, innerStyle, headCellStyle, attachColumnResize } from '/static/next/colresize.js';

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* Column specs (colresize spec shape, plus a `label` for the header cell). */
const HISTORY_COLUMNS = [
  { key: 'when', label: 'Date', def: 150, min: 110, align: 'left' },
  { key: 'schemas', label: 'Schemas', def: 90, min: 70 },
  { key: 'tables', label: 'Tables', def: 90, min: 70 },
  { key: 'columns', label: 'Columns', def: 100, min: 80 },
  { key: 'source', label: 'Source', def: 130, min: 100 },
  { key: 'invalid', label: 'Marked invalid', def: 360, min: 150 },
];
/** The history table's columns; "Marked invalid" exists only while invalid rows are shown. */
export const historyCols = (withInvalid) => ({
  id: 'db-survey-history', chrome: 50,
  columns: HISTORY_COLUMNS.filter((c) => withInvalid || c.key !== 'invalid'),
});

export const RANKED_COLS = {
  id: 'db-ranked-tables', chrome: 60,
  columns: [
    { key: 'name', label: 'Table', def: 200, min: 120, align: 'left', breakLong: true },
    { key: 'rows', label: 'Rows', def: 90, min: 70 },
    { key: 'size', label: 'Size', def: 90, min: 70 },
    { key: 'analyzed', label: 'Last analyzed', def: 150, min: 110 },
    { key: 'pending', label: 'Pending changes', def: 130, min: 100 },
    { key: 'diff', label: 'Since last run', def: 130, min: 100 },
  ],
};

export const VIEWS_COLS = {
  id: 'db-views', chrome: 60,
  columns: [
    { key: 'name', label: 'View', def: 230, min: 120, align: 'left', breakLong: true },
    { key: 'complexity', label: 'Complexity', def: 110, min: 90 },
    { key: 'portability', label: 'Portability', def: 110, min: 90 },
    { key: 'joins', label: 'Joins / CTEs', def: 110, min: 90 },
    { key: 'depends', label: 'Depends on', def: 360, min: 140, breakLong: true },
  ],
};

/** A table as a grid of cells. `rows` are `{ cells: {colKey: html}, attrs, tone, after }`;
 *  `after` is full-width html under the row (a disclosure), `tone` is '' or 'lowered'. */
export function gridHtml(spec, label, rows, attrs = '') {
  const head = spec.columns.map((c) => `<div role="columnheader" class="shrink-0" style="${cellStyle(spec, c.key)};${headCellStyle(spec, c.key)}" title="${esc(c.label)}">${esc(c.label)}${handleHtml(spec, c.key, c.label)}</div>`).join('');
  const body = rows.map((r) => `<div data-grid-row ${r.attrs || ''} class="border-t border-rule py-[3px] ${r.tone === 'lowered' ? 'opacity-60' : ''}">
    <div role="row" class="flex items-start gap-s2 text-ink">${spec.columns.map((c) => `<div role="cell" class="${c.key === spec.columns[0].key ? '' : 'tnum'}" style="${cellStyle(spec, c.key)}">${r.cells[c.key] ?? ''}</div>`).join('')}</div>${r.after || ''}</div>`).join('');
  return `<div data-grid-host class="min-w-0 max-w-full overflow-x-auto" ${attrs}><div role="table" aria-label="${esc(label)}" class="text-caveat" style="${innerStyle(spec)}">
    <div role="row" class="flex items-start gap-s2 text-provenance text-ink-muted">${head}</div>${body}</div></div>`;
}

/** Wire the drag, keyboard and double-click resize on every grid under `root`. */
export function attachGrids(root, specFor) {
  root.querySelectorAll('[data-grid-host]').forEach((h) => {
    const spec = specFor(h);
    if (spec) attachColumnResize(h, spec);
  });
}

/** "2.0 MB"; '—' words are the caller's: a missing size is never formatted here. */
export function fmtSize(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes; let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? v : v.toFixed(1)} ${u[i]}`;
}

/** "2026-10-06 14:02" from an ISO-ish timestamp. */
export const stamp = (iso) => String(iso || '').replace('T', ' ').slice(0, 16);
