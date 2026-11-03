/* Chart label fitting — where tick labels go so nothing overlaps or is clipped.
 *
 * Pure geometry on the figure's LAYOUT. The data is never touched: a long
 * category name is shortened in `ticktext` only (the trace keeps the full
 * name, so hover shows it), and the full names are also returned as a text
 * alternative. Label widths are estimated from the 13px chart font; the
 * real-browser test measures the rendered boxes to keep the estimate honest.
 */

export const TICK_FONT = 13;     // tick label size: 2px up from the 11px of the first walk
export const CHAR_W = 7.6;       // average glyph width at the 13px chart font
export const LINE_H = 17;        // one tick-label line
export const BAR_ROW_H = 22;     // one horizontal bar's own row, so every category label has its own line
export const BASE_H = 260;       // a chart never draws shorter than this
/** A pie has no axes; its title sits in the top margin, so the margin is what keeps slices and any label clear of it. */
export const PIE_TOP_MARGIN = 72;
const MAX_CAT_CHARS = 16;        // x categories (rotated, so cheap in width)
const MAX_Y_CHARS = 24;          // y categories (horizontal, in the left margin)
const TICK_GAP = 12;             // minimum clear space between two labels
const ANGLE = 40;                // degrees, counter-clockwise (labels end at their tick)

export function ellipsize(s, max) {
  const t = String(s == null ? '' : s);
  return t.length <= max ? t : `${t.slice(0, Math.max(1, max - 1))}…`;
}

/** Keep the head and the tail of `s` with a middle ellipsis, `max` characters in all. */
export function middleEllipsize(s, max, headShare = 0.4) {
  const t = String(s == null ? '' : s);
  if (t.length <= max) return t;
  const room = Math.max(2, max - 1);
  const head = Math.min(room - 1, Math.floor(room * headShare));
  return `${t.slice(0, head)}…${t.slice(t.length - (room - head))}`;
}

/** Shorten every name to `max` characters. A name that stays unique keeps the head ellipsis; names that would
 *  shorten to the same text keep their distinguishing tail instead (middle ellipsis). */
export function shortenNames(names, max) {
  const shown = names.map((s) => ellipsize(s, max));
  const groups = new Map();
  shown.forEach((t, i) => groups.set(t, [...(groups.get(t) || []), i]));
  groups.forEach((idx) => {
    if (idx.length < 2 || new Set(idx.map((i) => names[i])).size < 2) return;
    for (const share of [0.4, 0.25, 0.1, 0]) {
      const tries = idx.map((i) => middleEllipsize(names[i], max, share));
      if (new Set(tries).size === new Set(idx.map((i) => names[i])).size) {
        idx.forEach((i, k) => { shown[i] = tries[k]; });
        return;
      }
    }
  });
  return shown;
}

const textW = (s) => Math.ceil(String(s).length * CHAR_W);

/** Indices to label: first and last always, others only when their box (width
 *  `widths[i]`, centred on `pos[i]`) clears the previously kept one by `gap`. */
export function thinIndexes(pos, widths, gap = TICK_GAP) {
  const n = pos.length;
  if (n === 0) return [];
  const kept = [0];
  const right = (i) => pos[i] + widths[i] / 2;
  const left = (i) => pos[i] - widths[i] / 2;
  for (let i = 1; i < n - 1; i += 1) {
    if (left(i) >= right(kept[kept.length - 1]) + gap
        && right(i) + gap <= left(n - 1)) kept.push(i);
  }
  if (n > 1) {
    while (kept.length > 1 && left(n - 1) < right(kept[kept.length - 1]) + gap) kept.pop();
    kept.push(n - 1);
  }
  return kept;
}

const clone = (o) => JSON.parse(JSON.stringify(o));

function dateLabels(isoList) {
  const ts = isoList.map((s) => Date.parse(String(s).replace(' ', 'T')));
  const days = isoList.map((s) => String(s).slice(0, 10));
  const sameDay = new Set(days).size < days.length;
  const span = Math.max(...ts) - Math.min(...ts);
  return isoList.map((s) => {
    const m = String(s).match(/^(\d{4})-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    if (!m) return String(s);
    const d = span > 300 * 864e5 ? `${m[1]}-${m[2]}` : m[2];
    return sameDay ? `${d} ${m[3]}` : d;
  });
}

function legendWidth(fig) {
  const traces = fig.data || [];
  if (traces.length < 2 || (fig.layout || {}).showlegend === false) return 0;
  const longest = Math.max(...traces.map((t) => String(t.name || '').length));
  return Math.min(260, textW('x'.repeat(longest)) + 44);
}

/** Height of a horizontal bar chart: one ~22px row per bar plus the margins, never under BASE_H. */
export function barChartHeight(nBars, margin = { t: 12, b: 48 }) {
  return Math.max(BASE_H, Math.ceil(nBars * BAR_ROW_H + (margin.t || 0) + (margin.b || 0)));
}

/** Fit one figure to `width` px.
 *  `title` names the chart in its text alternative. Returns { layout, alt: [{shown, full}], summary, height }. `layout` is `fig.layout`
 *  with margins, tick arrays, angles and panel domains set; `alt` lists every
 *  label that was shortened or thinned away, with its full text. */
export function fitFigure(fig, width, title = '') {
  const layout = clone((fig && fig.layout) || {});
  const data = (fig && fig.data) || [];
  const alt = [];
  const ticks = { x: [], y: [] };   // per axis, parallel to tickvals/ticktext: { shown, full }
  const horizontal = data.some((t) => t.orientation === 'h');
  const dateX = !!(layout.xaxis && layout.xaxis.type === 'date');
  const margin = { l: 56, r: 16, t: 12, b: 40 };
  layout.xaxis = layout.xaxis || {};
  layout.yaxis = layout.yaxis || {};

  if (data.some((t) => t.type === 'pie')) {
    margin.l = 16; margin.r = 16; margin.t = PIE_TOP_MARGIN; margin.b = 16;
  } else if (horizontal) {
    const names = (data[0].y || []).map(String);
    const shown = shortenNames(names, MAX_Y_CHARS);
    names.forEach((full, i) => { if (shown[i] !== full) alt.push({ shown: shown[i], full }); });
    ticks.y = names.map((full, i) => ({ shown: shown[i], full }));
    layout.yaxis.tickmode = 'array';
    layout.yaxis.tickvals = names;
    layout.yaxis.ticktext = shown;
    layout.yaxis.automargin = true;
    margin.l = Math.min(Math.ceil(width * 0.4), Math.max(56, ...shown.map(textW)) + 14);
    margin.r = 24;
    margin.b = LINE_H + 8 + LINE_H + 12;          // tick row + caption row
    layout.height = barChartHeight(names.length, margin);
    const plotW = Math.max(120, width - margin.l - margin.r);
    const twoPanels = !!layout.xaxis2;
    // Gutter: the left panel's last tick label is centred on its right edge and
    // the right panel's first on its left edge; each overhangs by half a label.
    const gutter = twoPanels ? Math.max(56, Math.round(plotW * 0.1)) : 0;
    const panelW = twoPanels ? (plotW - gutter) / 2 : plotW;
    const f = (px) => Math.round((px / plotW) * 1000) / 1000;
    const axes = twoPanels ? ['xaxis', 'xaxis2'] : ['xaxis'];
    axes.forEach((k, idx) => {
      const a = layout[k];
      a.nticks = Math.max(2, Math.floor(panelW / (CHAR_W * 6 + TICK_GAP)));
      a.tickformat = ',d';
      a.tickangle = 0;
      a.automargin = true;
      if (a.title) a.title = Object.assign({ standoff: 6 }, a.title);
      if (twoPanels) a.domain = idx === 0 ? [0, f(panelW)] : [f(panelW + gutter), 1];
    });
  } else if (dateX) {
    // Candidate ticks: every distinct run time on any trace.
    const all = [...new Set(data.flatMap((t) => (t.x || []).map(String)))]
      .sort((a, b) => Date.parse(a.replace(' ', 'T')) - Date.parse(b.replace(' ', 'T')));
    margin.r = 24;
    margin.b = LINE_H + 8 + 12;
    const plotW = Math.max(120, width - margin.l - margin.r - legendWidth(fig));
    if (all.length) {
      const labels = dateLabels(all);
      const t0 = Date.parse(all[0].replace(' ', 'T'));
      const t1 = Date.parse(all[all.length - 1].replace(' ', 'T'));
      const pos = all.map((s) => (t1 === t0 ? plotW / 2
        : ((Date.parse(s.replace(' ', 'T')) - t0) / (t1 - t0)) * plotW));
      const keep = thinIndexes(pos, labels.map(textW));
      layout.xaxis.tickmode = 'array';
      layout.xaxis.tickvals = keep.map((i) => all[i]);
      layout.xaxis.ticktext = keep.map((i) => labels[i]);
      layout.xaxis.tickangle = 0;
      layout.xaxis.automargin = true;
      all.forEach((full, i) => { if (!keep.includes(i)) alt.push({ shown: '', full }); });
    }
  } else if (data.length && Array.isArray(data[0].x) && data[0].x.some((v) => typeof v === 'string')) {
    const names = data[0].x.map(String);
    const shown = shortenNames(names, MAX_CAT_CHARS);
    names.forEach((full, i) => { if (shown[i] !== full) alt.push({ shown: shown[i], full }); });
    const maxW = Math.max(...shown.map(textW));
    let plotW = width - margin.l - margin.r;
    const slot = plotW / names.length;
    let angle = 0;
    if (maxW + TICK_GAP > slot) angle = -ANGLE;
    // Rotated labels end at their tick and reach left by maxW*cos; make room
    // from the margin plus half a slot (the first tick sits half a slot in).
    if (angle) {
      margin.l = Math.max(margin.l, Math.ceil(maxW * Math.cos((ANGLE * Math.PI) / 180)) + 8 - Math.floor(slot / 2));
      plotW = width - margin.l - margin.r;
    }
    const slot2 = plotW / names.length;
    const need = angle ? LINE_H / Math.sin((ANGLE * Math.PI) / 180) + 2 : maxW + TICK_GAP;
    const every = Math.max(1, Math.ceil(need / slot2));
    const keep = names.map((_, i) => i).filter((i) => i % every === 0 || i === names.length - 1);
    // never leave the last label on top of its neighbour
    if (every > 1 && keep.length > 1
        && (names.length - 1) % every !== 0
        && (names.length - 1 - keep[keep.length - 2]) * slot2 < need) keep.splice(keep.length - 2, 1);
    layout.xaxis.tickmode = 'array';
    layout.xaxis.tickvals = keep.map((i) => names[i]);
    layout.xaxis.ticktext = keep.map((i) => shown[i]);
    ticks.x = keep.map((i) => ({ shown: shown[i], full: names[i] }));
    layout.xaxis.tickangle = angle;
    layout.xaxis.automargin = true;
    names.forEach((full, i) => { if (!keep.includes(i)) alt.push({ shown: '', full }); });
    margin.b = angle
      ? Math.ceil(maxW * Math.sin((ANGLE * Math.PI) / 180)) + LINE_H + 16
      : LINE_H + 24;
  }
  layout.margin = margin;
  title = title || (layout.title && layout.title.text);
  const summary = `${title || 'Chart'}${alt.length
    ? `. Full labels: ${alt.map((a) => a.full).join('; ')}` : ''}`;
  return { layout, alt, ticks, summary, height: layout.height || BASE_H };
}

/** After Plotly draws: put each shortened label's full name in a <title> on its SVG text, and the chart's text
 *  alternative on the plot element. Ticks are matched by POSITION (the n-th tick of an axis is the n-th entry of
 *  `ticks[axis]`, which is parallel to tickvals), never by the shortened text, which two names can share. The tick
 *  labels get pointer-events so the browser's tooltip can reach them; the drag layer is a sibling and is untouched. */
export function annotateTicks(plot, alt, summary, ticks) {
  if (!plot) return;
  try {
    plot.setAttribute('role', 'img');
    plot.setAttribute('aria-label', summary);
    if (!plot.querySelectorAll || !ticks) return;
    ['x', 'y'].forEach((axis) => {
      const list = ticks[axis] || [];
      const nodes = [...plot.querySelectorAll(`.${axis}tick text`)];
      if (nodes.length !== list.length) return;       // cannot pair them with certainty: no tooltip beats a wrong one
      nodes.forEach((t, n) => {
        const { shown, full } = list[n];
        if (shown === full) return;
        t.style.pointerEvents = 'all';
        const old = t.querySelector('title');
        if (old) old.remove();
        const title = t.ownerDocument.createElementNS('http://www.w3.org/2000/svg', 'title');
        title.textContent = full;
        t.appendChild(title);
      });
    });
  } catch (_) { /* a tooltip is never worth a failed chart */ }
}
