/* Resizable table columns, one helper for every /next table drawn as a grid of cells.
 *
 * A table gives this module a column spec; the module hands back the pieces:
 *   cellStyle(spec, key)   the inline style for one cell of that column (header and rows alike)
 *   handleHtml(spec, key, label)   the drag handle that sits at a header cell's right edge
 *   innerStyle(spec)       the inline style for the table's inner wrapper (its minimum width)
 *   attachColumnResize(host, spec)   once per scroll host: applies stored widths, wires drag,
 *                                    keyboard and double-click by delegation (so a re-draw of the
 *                                    host's innerHTML keeps working), returns {reset, isCustom}
 *
 * Widths live in CSS custom properties on the host (--rc-<key>), so a drag moves every row at
 * once without a re-draw; each cell's style carries the default as the var() fallback.
 * Rules kept here:
 *  - EVERY column, the name column included, is a fixed width (flex: none): dragging a handle
 *    changes that one column's width and nothing else, so the columns to its left do not move
 *    and the ones to its right shift by exactly the change. (An earlier version let one
 *    "name" column absorb the difference: growing a column right of it shrank the name and
 *    slid every column between them left, which looked like all of them resizing.) When the
 *    columns together outgrow the host, the host scrolls inside its own box; nothing is
 *    squeezed. A column never goes below its minimum nor above MAX_WIDTH;
 *  - double-click on a handle resets that column; Left/Right move a focused handle by 10px,
 *    Shift by 50px; the cue is a thin vertical bar on hover and on keyboard focus (see the
 *    `.rc-handle` rules in index.html);
 *  - widths persist per table id in localStorage, every access inside try/catch: with storage
 *    unavailable the table still resizes, it just forgets on reload.
 * A spec is { id, chrome, columns: [{ key, def, min, resizable, align }] }, `chrome` being the
 * px of gaps and borders around the cells; `align: 'left'` keeps a column's text left (all
 * others are centred). A row drawn indented passes the indent as `offset`, which is taken off
 * its first column so the cells still line up with the header.
 */
const MAX_WIDTH = 1200;
const VAR = (key) => `--rc-${key}`;
const STORE = (spec) => `re.colwidths.${spec.id}`;
const STEP = 10;
const STEP_BIG = 50;

const col = (spec, key) => spec.columns.find((c) => c.key === key);

export const alignStyle = (spec, key) => `text-align:${col(spec, key).align || 'center'}`;

export function cellStyle(spec, key, offset = 0) {
  const c = col(spec, key);
  const w = `var(${VAR(key)},${c.def}px)`;
  // Wrap at spaces only; a column's `min` keeps its longest word whole. A column that holds
  // identifiers (`breakLong`) may break an over-long one rather than run into its neighbour.
  const wrap = c.breakLong ? 'overflow-wrap:break-word' : 'overflow-wrap:normal;word-break:normal;hyphens:none';
  return `width:${offset ? `calc(${w} - ${offset}px)` : w};flex:0 0 auto;min-width:0;${wrap};${alignStyle(spec, key)}`;
}

export function totalFloor(spec, widths = {}) {
  return spec.columns.reduce((a, c) => a + (widths[c.key] ?? c.def), 0) + spec.chrome;
}

export function innerStyle(spec) {
  return `min-width:var(--rc-total,${totalFloor(spec)}px);padding-right:8px`;
}

/** The right-edge handle. Focusable, so the keyboard can reach it. */
export function handleHtml(spec, key, label) {
  const c = col(spec, key);
  return `<span class="rc-handle" data-rc-handle="${key}" role="separator" aria-orientation="vertical" tabindex="0" `
    + `aria-label="Resize the ${String(label).replace(/"/g, '&quot;')} column (arrow keys; double-click resets)" `
    + `aria-valuemin="${c.min}" aria-valuenow="${c.def}"></span>`;
}

/** Header cell style: one line, ellipsis, the full text in the cell's title. */
export const headCellStyle = (spec, key) => `position:relative;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:6px;${alignStyle(spec, key)}`;

function readStore(win, spec) {
  try {
    const raw = win.localStorage.getItem(STORE(spec));
    const o = raw ? JSON.parse(raw) : {};
    const out = {};
    for (const c of spec.columns) {
      const v = o && o[c.key];
      if (typeof v === 'number' && Number.isFinite(v) && v >= c.min) out[c.key] = Math.round(v);
    }
    return out;
  } catch (_e) { return {}; }
}

function writeStore(win, spec, widths) {
  try {
    if (Object.keys(widths).length) win.localStorage.setItem(STORE(spec), JSON.stringify(widths));
    else win.localStorage.removeItem(STORE(spec));
  } catch (_e) { /* storage unavailable: widths last for this page only */ }
}

const attached = new WeakMap();

export function attachColumnResize(host, spec, { onChange } = {}) {
  if (attached.has(host)) return attached.get(host);
  const win = host.ownerDocument.defaultView;
  let widths = readStore(win, spec);
  const widthOf = (key) => widths[key] ?? col(spec, key).def;

  const clamp = (key, w) => Math.round(Math.min(MAX_WIDTH, Math.max(col(spec, key).min, w)));

  const apply = () => {
    for (const c of spec.columns) {
      if (widths[c.key] != null) host.style.setProperty(VAR(c.key), `${widths[c.key]}px`);
      else host.style.removeProperty(VAR(c.key));
    }
    host.style.setProperty('--rc-total', `${totalFloor(spec, widths)}px`);
    host.querySelectorAll('[data-rc-handle]').forEach((h) => {
      h.setAttribute('aria-valuenow', String(widthOf(h.dataset.rcHandle)));
    });
    if (onChange) onChange(api.isCustom());
  };
  const persist = () => writeStore(win, spec, widths);
  const set = (key, w) => { widths = { ...widths, [key]: clamp(key, w) }; apply(); };
  const resetOne = (key) => { const { [key]: _gone, ...rest } = widths; widths = rest; apply(); persist(); };

  const api = {
    isCustom: () => Object.keys(widths).length > 0,
    width: widthOf,
    reset: () => { widths = {}; apply(); persist(); },
    apply,
  };
  attached.set(host, api);

  let drag = null;
  host.addEventListener('pointerdown', (e) => {
    const h = e.target.closest && e.target.closest('[data-rc-handle]');
    if (!h || (e.button != null && e.button > 0)) return;
    e.preventDefault();
    drag = { key: h.dataset.rcHandle, x: e.clientX, w: widthOf(h.dataset.rcHandle), el: h };
    h.setAttribute('data-rc-active', '');
    const doc = host.ownerDocument;
    const move = (m) => { if (drag) set(drag.key, drag.w + (m.clientX - drag.x)); };
    const up = () => {
      doc.removeEventListener('pointermove', move);
      doc.removeEventListener('pointerup', up);
      doc.removeEventListener('pointercancel', up);
      if (drag) { drag.el.removeAttribute('data-rc-active'); persist(); }
      drag = null;
    };
    doc.addEventListener('pointermove', move);
    doc.addEventListener('pointerup', up);
    doc.addEventListener('pointercancel', up);
  });
  host.addEventListener('dblclick', (e) => {
    const h = e.target.closest && e.target.closest('[data-rc-handle]');
    if (h) { e.preventDefault(); resetOne(h.dataset.rcHandle); }
  });
  host.addEventListener('keydown', (e) => {
    const h = e.target.closest && e.target.closest('[data-rc-handle]');
    if (!h || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    const key = h.dataset.rcHandle;
    set(key, widthOf(key) + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? STEP_BIG : STEP));
    persist();
  });
  apply();
  return api;
}

/* ── every other data table: the default ───────────────────────────────────────────────────────────
 * The grid-of-cells tables above are drawn with a column spec. Every ordinary `<table>` that has a header row is
 * made resizable here instead, by one helper applied where tables render (`watchTables`, started once by app.js),
 * so a table nobody remembered to wire is resizable anyway. Rules:
 *  - a data table is a `<table>` whose header row (the `thead` row, or a first row of only `th`) has two or more
 *    cells. A table with no header row is a key/value or layout table and is left alone, as is any table with
 *    `data-no-resize` on it or on an ancestor (the opt-out);
 *  - each header cell gets the same `.rc-handle` as above: drag, Left/Right by 10px (Shift 50px), double-click to
 *    reset that column; the other columns do not move;
 *  - the first change freezes every column at its drawn width (`table-layout: fixed`) so only the dragged column
 *    changes; the table's parent scrolls sideways when the columns together outgrow it;
 *  - widths persist per table in localStorage (`re.colwidths.auto.<id>`, the id being `data-rc-id` or the header
 *    labels), every access inside try/catch.
 */
const AUTO_MIN = 40;
const AUTO_STORE = (id) => `re.colwidths.auto.${id}`;

/** The header row of a data table, or null when the table is not one. A table with no header row but three or more
 *  columns (a small grid of figures) still is one: its first full row carries the handles. */
export function headerRowOf(table) {
  if (!table || table.hasAttribute('data-rc-auto') || table.closest('[data-no-resize]')) return null;
  let row = table.tHead && table.tHead.rows[0];
  if (!row) {
    const first = table.querySelector('tr');
    if (first && first.cells.length && [...first.cells].every((c) => c.tagName === 'TH')) row = first;
  }
  if (!row) {
    row = [...table.querySelectorAll('tr')].find((r) => r.cells.length >= 3
      && [...r.cells].every((c) => !(c.colSpan > 1))) || null;
    return row;
  }
  return row.cells.length >= 2 ? row : null;
}

const cellWidth = (c) => Math.round((c.getBoundingClientRect && c.getBoundingClientRect().width) || c.offsetWidth || 0);

/** The shared body of both default enhancers: handles on `cells` (the header cells), widths kept per column index.
 *  `target` says how a column is sized: `setWidth(i, px)` for one column and `setTotal(px)` for the whole. */
function makeResizable({ owner, cells, id, scrollHost, freezeLayout, setWidth, setTotal }) {
  const win = owner.ownerDocument.defaultView;
  let widths = {};
  try { widths = JSON.parse(win.localStorage.getItem(AUTO_STORE(id)) || '{}') || {}; } catch (_e) { widths = {}; }
  let base = null;                                  // the drawn widths, taken at the first change
  const save = () => {
    try {
      if (Object.keys(widths).length) win.localStorage.setItem(AUTO_STORE(id), JSON.stringify(widths));
      else win.localStorage.removeItem(AUTO_STORE(id));
    } catch (_e) { /* storage unavailable: widths last for this page only */ }
  };
  const freeze = () => {
    if (base) return;
    base = cells.map((c) => Math.max(AUTO_MIN, cellWidth(c) || 120));
    owner.setAttribute('data-rc-fixed', '');
    freezeLayout();
    if (scrollHost) scrollHost.style.overflowX = 'auto';
  };
  const paint = () => {
    freeze();
    cells.forEach((c, i) => setWidth(i, widths[i] ?? base[i]));
    setTotal(cells.reduce((a, c, i) => a + (widths[i] ?? base[i]), 0));
    cells.forEach((c, i) => {
      const h = c.querySelector('[data-rc-handle]');
      if (h) h.setAttribute('aria-valuenow', String(widths[i] ?? base[i]));
    });
  };
  const widthOf = (i) => widths[i] ?? (base ? base[i] : Math.max(AUTO_MIN, cellWidth(cells[i]) || 120));
  const set = (i, w) => { freeze(); widths = { ...widths, [i]: Math.round(Math.min(MAX_WIDTH, Math.max(AUTO_MIN, w))) }; paint(); };

  cells.forEach((c, i) => {
    const label = c.textContent.trim() || `column ${i + 1}`;
    if (win.getComputedStyle(c).position === 'static') c.style.position = 'relative';
    const h = c.ownerDocument.createElement('span');
    h.className = 'rc-handle';
    h.setAttribute('data-rc-handle', String(i));
    h.setAttribute('role', 'separator');
    h.setAttribute('aria-orientation', 'vertical');
    h.setAttribute('tabindex', '0');
    h.setAttribute('aria-label', `Resize the ${label} column (arrow keys; double-click resets)`);
    h.setAttribute('aria-valuemin', String(AUTO_MIN));
    c.appendChild(h);
    // A press on the handle is a resize, never a click on the header (a sortable header would re-sort).
    h.addEventListener('click', (e) => e.stopPropagation());
    h.addEventListener('pointerdown', (e) => {
      if (e.button != null && e.button > 0) return;
      e.preventDefault();
      const start = { x: e.clientX, w: widthOf(i) };
      h.setAttribute('data-rc-active', '');
      const doc = owner.ownerDocument;
      const move = (m) => set(i, start.w + (m.clientX - start.x));
      const up = () => {
        doc.removeEventListener('pointermove', move);
        doc.removeEventListener('pointerup', up);
        doc.removeEventListener('pointercancel', up);
        h.removeAttribute('data-rc-active');
        save();
      };
      doc.addEventListener('pointermove', move);
      doc.addEventListener('pointerup', up);
      doc.addEventListener('pointercancel', up);
    });
    h.addEventListener('dblclick', (e) => {
      e.preventDefault();
      if (!base) return;
      const { [i]: _gone, ...rest } = widths;
      widths = rest;
      paint();
      save();
    });
    h.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      set(i, widthOf(i) + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? STEP_BIG : STEP));
      save();
    });
  });
  if (Object.keys(widths).length) paint();
}

export function enhanceTable(table) {
  const row = headerRowOf(table);
  if (!row) return false;
  const cells = [...row.cells];
  const id = table.dataset.rcId || cells.map((c) => c.textContent.trim()).join('|');
  table.setAttribute('data-rc-auto', '');
  makeResizable({
    owner: table, cells, id, scrollHost: table.parentElement,
    freezeLayout: () => { table.style.tableLayout = 'fixed'; },
    setWidth: (i, w) => { cells[i].style.width = `${w}px`; },
    setTotal: (w) => { table.style.width = `${w}px`; table.style.maxWidth = 'none'; },
  });
  return true;
}

/* ── div grids (`role="table"` drawn with flex rows) ───────────────────────────────────────────────
 * A grid is the same thing as a table to a person: a header row of `role="columnheader"` cells over rows of cells.
 * Two ways in, neither needing the screen to remember anything:
 *  - a grid drawn by report-tables.js `gridHtml` registers its column spec here and carries `data-rc-spec="<id>"`;
 *    `attachRegistered` wires the spec-driven resize (attachColumnResize) on it as it appears, so a redraw that
 *    forgot to call attachGrids still resizes (this is the late-render gap: quality checks, drill-downs);
 *  - any other `role="table"` with a header row of two or more columnheaders gets the default resize: every row's
 *    cells follow the header's widths. */
const SPECS = new Map();
export function registerSpec(spec) { SPECS.set(spec.id, spec); }

export function attachRegistered(root) {
  let n = 0;
  root.querySelectorAll('[data-rc-spec]').forEach((h) => {
    const spec = SPECS.get(h.getAttribute('data-rc-spec'));
    if (spec && !attached.has(h)) { attachColumnResize(h, spec); n += 1; }
  });
  return n;
}

const rowsOfGrid = (grid) => [...grid.querySelectorAll('[role="row"]')];
export function gridHeaderOf(grid) {
  if (!grid || grid.hasAttribute('data-rc-auto') || grid.closest('[data-no-resize]') || grid.querySelector('[data-rc-handle]')) return null;
  const row = rowsOfGrid(grid).find((r) => r.querySelector('[role="columnheader"]'));
  const cells = row ? [...row.children].filter((c) => c.getAttribute('role') === 'columnheader') : [];
  return cells.length >= 2 ? { row, cells } : null;
}

export function enhanceGrid(grid) {
  const found = gridHeaderOf(grid);
  if (!found) return false;
  const { cells } = found;
  const id = grid.dataset.rcId || (grid.getAttribute('aria-label') || '') + '|' + cells.map((c) => c.textContent.trim()).join('|');
  grid.setAttribute('data-rc-auto', '');
  const colCells = (i) => rowsOfGrid(grid).map((r) => r.children[i]).filter(Boolean);
  makeResizable({
    owner: grid, cells, id, scrollHost: grid,
    freezeLayout: () => {},
    setWidth: (i, w) => colCells(i).forEach((c) => {
      c.style.width = `${w}px`; c.style.flex = '0 0 auto'; c.style.minWidth = '0'; c.style.maxWidth = 'none';
    }),
    setTotal: (w) => rowsOfGrid(grid).forEach((r) => { r.style.width = `${w}px`; r.style.minWidth = `${w}px`; }),
  });
  return true;
}

/** Make every data table (a `<table>` or a `role="table"` grid) under `root` resizable. Idempotent. Returns how many it enhanced this time. */
export function enhanceTables(root) {
  let n = 0;
  if (!root || !root.querySelectorAll) return n;
  root.querySelectorAll('table').forEach((t) => { if (enhanceTable(t)) n += 1; });
  n += attachRegistered(root);
  root.querySelectorAll('[role="table"]').forEach((g) => { if (enhanceGrid(g)) n += 1; });
  return n;
}

/** Enhance what is there now and every table drawn under `root` later (a redraw replaces the table, so the new one
 *  is enhanced as it appears). Returns the observer so a caller can disconnect it. */
export function watchTables(root) {
  enhanceTables(root);
  const Obs = root.ownerDocument.defaultView.MutationObserver;
  if (!Obs) return null;
  const obs = new Obs(() => { enhanceTables(root); obs.takeRecords(); });
  obs.observe(root, { childList: true, subtree: true });
  return obs;
}
