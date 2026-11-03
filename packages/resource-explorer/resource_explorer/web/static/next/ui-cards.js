/* Cards and tiles: the two containers of /next (PLAN-CARDS-AND-TILES.md, slice C1).
 *
 * Defined once and used everywhere; Paper uses the same markup with its own tokens (`card`, `card-line`, the look's
 * radius), so there is no second markup path. Pure render functions: no DOM, no network, no module state.
 *
 * A CARD is one thing a person reads or acts on as a unit. Header (title, meta, actions at the top right), body,
 * footer (the provenance line). One level only: never a card inside a card. A list of 30 analyses is ONE card of 30
 * rows. Section labels sit outside the card, above it.
 *
 * A TILE is one count that is the answer, drawn large, and the whole tile is the control that opens what it counted.
 * Rules the code enforces rather than hopes for:
 *   - a tile with no `opens` target is refused (a figure that opens nothing goes in a sentence);
 *   - an absent figure is a state, never 0: in `not_read` and `not_measured` the figure argument is not drawn at
 *     all, so a caller cannot pass 0 (or '') through by accident and have it shown;
 *   - colour only from a state word: the figure is ink, except a state count (`count`), which takes `state-warn`;
 *   - at most six tiles in a row.
 *
 * Every text argument is HTML the caller has already escaped (the callers move existing markup in unchanged).
 */
import { STATES } from '/static/next/glyphs.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** The card states. `off` is the card UNCHANGED: it is the control that is dashed (see actions below). */
export const CARD_STATES = ['ordinary', 'needs_you', 'lowered', 'off', 'chosen'];

/** A card. `title`, `meta`, `actions`, `body` and `footer` are all optional HTML; `state` is one of CARD_STATES.
 *  `attrs` is extra attributes for the root (data hooks), already escaped.
 *
 *  Look: the `card` fill, a visible `card-line` border, the look's card radius, 14px padding.
 *   needs_you  a 3px LEFT EDGE in state-warn. It means "this needs you" and nothing else.
 *   lowered    every line of text in ink-muted (never lighter); the border stays solid.
 *   off        the card is unchanged; a button in its actions is drawn with a dashed border.
 *   chosen     accent-tint fill, as a selected row has today. */
export function cardHtml({ title = '', meta = '', actions = '', body = '', footer = '', state = 'ordinary', attrs = '' } = {}) {
  if (!CARD_STATES.includes(state)) throw new Error(`cardHtml: unknown state "${state}"`);
  const hasHeader = !!(title || meta || actions);
  return `<section data-ui-card data-ui-card-state="${state}" ${attrs}
    class="min-w-0 rounded-md border border-card-line p-[14px] ${state === 'chosen' ? 'bg-accent-tint' : 'bg-card'} ${
    // needs_you: the 3px left edge in state-warn, which means "needs you" and nothing else.
    state === 'needs_you' ? 'border-l-[3px] border-l-state-warn' : ''} ${
    // lowered: every line of text in ink-muted; text set in ink inside is pulled down to ink-muted too, never lighter.
    state === 'lowered' ? 'text-ink-muted [&_.text-ink]:text-ink-muted' : ''} ${
    // off: the CONTROL is dashed; the card is unchanged.
    state === 'off' ? '[&_[data-card-actions]_button]:border-dashed' : ''}">${hasHeader ? `
    <header class="flex flex-wrap items-baseline gap-x-s2 gap-y-[2px]">${title
    ? `<h3 data-card-title class="m-0 font-heading text-answer font-semibold text-ink">${title}</h3>` : ''}${meta
    ? `<span data-card-meta class="text-caveat text-ink-muted">${meta}</span>` : ''}${actions
    ? `<div data-card-actions class="ml-auto flex flex-wrap items-baseline gap-s2">${actions}</div>` : ''}</header>` : ''}${body
    ? `
    <div data-card-body class="${hasHeader ? 'mt-s2' : ''}">${body}</div>` : ''}${footer
    ? `
    <footer data-card-footer class="mt-s2 text-provenance text-ink-muted">${footer}</footer>` : ''}</section>`;
}

/** A stack of cards: 12px between them. */
export function cardStackHtml(cardsHtml) {
  return `<div data-ui-card-stack class="flex flex-col gap-[12px]">${(cardsHtml || []).join('')}</div>`;
}

/* ── Tiles ───────────────────────────────────────────────────────────────── */

/** Tile states and what the figure slot shows for each:
 *    measured      the figure, with the measured cue
 *    partial       ◐, the figure and the word "partial": measured within a limit
 *    not_read      "? not read": a read was tried and failed, so nothing is known
 *    not_measured  "○ not measured": nothing measured it
 *    zero          "∅ 0": a real zero that a measurement established
 *    count         the figure in state-warn: a count that IS a state ("Need you 2")
 *  The glyphs are the ones understanding-glance.js already uses (● ◐ ○), plus ? and ∅ from glyphs.js. */
export const TILE_STATES = ['measured', 'partial', 'not_read', 'not_measured', 'zero', 'count'];

const TILE_CUE = {
  measured: { glyph: '●', word: 'measured' },
  partial: { glyph: STATES.partial.glyph, word: 'partial' },
  not_read: { glyph: STATES.unknown.glyph, word: 'not read' },
  not_measured: { glyph: '○', word: 'not measured' },
  zero: { glyph: STATES.measured_zero.glyph, word: 'measured, nothing found' },
  count: { glyph: '', word: '' },
};

// Two tones only: muted for a measured cue, gap for "something is missing here". Literal classes, so the scan sees them.
const glyphSpan = (glyph, muted) => (glyph
  ? (muted ? `<span class="font-glyph text-ink-muted" aria-hidden="true">${glyph}</span>`
    : `<span class="font-glyph text-state-gap" aria-hidden="true">${glyph}</span>`) : '');

/** One big-number tile. `opens` is the target it opens (a key the caller binds with bindTiles); a tile without one
 *  is refused. `title` is the hover text: source and as-of, one hover away. */
export function tileHtml({ label, figure, sub = '', opens, state = 'measured', title = '', key = '' } = {}) {
  if (!opens) throw new Error('tileHtml: a tile opens what it counted; a figure that opens nothing goes in a sentence');
  if (!TILE_STATES.includes(state)) throw new Error(`tileHtml: unknown state "${state}"`);
  const cue = TILE_CUE[state];
  let figureHtml;
  let subHtml = sub;
  if (state === 'not_read' || state === 'not_measured') {
    // The state's glyph and word in place of a number. The `figure` argument is deliberately not drawn.
    figureHtml = `${glyphSpan(cue.glyph, false)} <span class="text-answer font-normal text-ink-muted" data-tile-absent>${cue.word}</span>`;
  } else if (state === 'zero') {
    figureHtml = `${glyphSpan(cue.glyph, true)} <span>0</span>`;
  } else if (state === 'partial') {
    figureHtml = `${glyphSpan(cue.glyph, false)} <span>${figure}</span>`;
    subHtml = `<span data-tile-partial-word>partial</span>${sub ? ` · ${sub}` : ''}`;
  } else if (state === 'count') {
    figureHtml = `<span class="text-state-warn">${figure}</span>`;
  } else {
    figureHtml = `${glyphSpan(cue.glyph, true)} <span>${figure}</span>`;
  }
  return `<button type="button" data-ui-tile${key ? ` data-tile-key="${esc(key)}"` : ''} data-tile-state="${state}" data-tile-opens="${esc(opens)}"${title ? ` title="${esc(title)}"` : ''}
    class="flex min-w-[9rem] max-w-full cursor-pointer flex-col items-start gap-[2px] rounded-md border border-card-line bg-card p-[14px] text-left text-ink">
    <span data-tile-label class="text-caps uppercase tracking-caps text-ink-muted">${label}</span>
    <span data-tile-figure class="tnum whitespace-nowrap text-figure font-bold text-ink">${figureHtml}</span>${subHtml
    ? `
    <span data-tile-sub class="text-provenance text-ink-muted">${subHtml}</span>` : ''}</button>`;
}

/** A row of tiles: at most six. Each sizes to its content with a 9rem floor, the row wraps rather than squeezing a
 *  tile, and tiles are not stretched to equal height. */
export function tileRowHtml(tiles) {
  const list = tiles || [];
  if (list.length > 6) throw new Error(`tileRowHtml: at most six tiles in a row, got ${list.length}`);
  return `<div data-ui-tile-row class="flex flex-wrap items-start gap-s3">${list.join('')}</div>`;
}

/** A fact tile: small, for a value that is not a count (Website, Language, Last pushed). Today's scouting-tile
 *  size, with the card border and radius, and without the big figure. */
export function factTileHtml({ label, value, title = '', key = '' } = {}) {
  return `<div data-ui-fact-tile${key ? ` data-tile-key="${esc(key)}"` : ''}${title ? ` title="${esc(title)}"` : ''}
    class="min-w-0 rounded-md border border-card-line bg-card px-2 py-[2px]">
    <div data-tile-label class="text-caps uppercase tracking-caps text-ink-muted">${label}</div>
    <div data-tile-value class="text-caveat text-ink">${value}</div></div>`;
}

/** Wire every tile under `root`. `open(target, tile)` is called with the tile's `opens` value. Idempotent. */
export function bindTiles(root, open) {
  root.querySelectorAll('[data-ui-tile]').forEach((t) => {
    if (t.dataset.tileBound) return;
    t.dataset.tileBound = '1';
    t.addEventListener('click', () => open(t.dataset.tileOpens, t));
  });
}
