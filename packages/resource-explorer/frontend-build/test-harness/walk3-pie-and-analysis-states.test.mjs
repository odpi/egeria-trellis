/** walk3 (2026-10-20): pie margins, the By-analysis result states, nested columns' own state, closed-card summaries. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';

ensureLoaderRegistered();
const text = (h) => h.replace(/<[^>]+>/g, '').replace(/&#39;|&#x27;|&apos;/g, "'").replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

/* ── 1. pies ─────────────────────────────────────────────────────────────────────────────────── */

test('a pie keeps its own top margin through the theme (the title gap), and fitFigure gives a pie the same gap', async () => {
  const { window } = makeDomEnvironment();
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  const app = await loadAppModule();
  const cl = await import('/static/next/chart-labels.js');
  const lay = app.chartLayout({ margin: { t: cl.PIE_TOP_MARGIN, l: 16 } });
  assert.equal(lay.margin.t, cl.PIE_TOP_MARGIN, 'the figure margin is merged, not overwritten by the fixed t:24');
  assert.equal(lay.margin.l, 16);
  assert.equal(lay.margin.b, 48, 'the keys the figure did not set keep the default');
  assert.ok(cl.PIE_TOP_MARGIN >= 56, 'room for a one-line title with a clear gap under it');
  const pie = { data: [{ type: 'pie', labels: ['Python 99.5%', 'CSS <0.01%'], values: [995, 0.025], text: ['99.5%', ''] }], layout: {} };
  assert.ok(cl.fitFigure(pie, 800).layout.margin.t >= cl.PIE_TOP_MARGIN);
  assert.equal(app.chartLayout({}).margin.t, 24, 'a chart with no margin of its own is unchanged');
});

test('a pie figure for a 99.5% / 0.0025% split: small slices carry no text, the legend carries percentages', async () => {
  const fig = { data: [{ type: 'pie', labels: ['Python 99.5%', 'JavaScript <0.01%'], values: [995, 0.025],
    text: ['99.5%', ''], textinfo: 'text', textposition: 'inside', insidetextorientation: 'horizontal' }], layout: {} };
  const t = fig.data[0];
  assert.equal(t.text[1], '');
  assert.ok(t.labels.every((l) => /\d%$/.test(l)));
  const { window } = makeDomEnvironment();
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  const app = await loadAppModule();
  const cl = await import('/static/next/chart-labels.js');
  const axes = app.chartAxes({ fig, kind: 'languages', label: 'Languages', first: '', last: '' });
  assert.ok(axes.margin.t >= cl.PIE_TOP_MARGIN, 'the pie asks for a clear gap under the title');
  assert.equal(app.chartLayout(axes).margin.t, axes.margin.t, 'and the theme keeps it');
});

/* ── 2. result states ────────────────────────────────────────────────────────────────────────── */

const CASES = [
  ['not_requested', "not in the last survey · its step wasn't requested"],
  ['ran_no_rows', 'ran in the last survey · produced no rows'],
  ['predates_storage', 'the last survey predates stored results for this analysis'],
  ['never_surveyed', 'not yet run'],
  ['no_results_view', 'runs as a step · no results view yet'],
];

test('each result state shows one line, and "Not yet run." belongs to never_surveyed alone', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  for (const [state, words] of CASES) {
    const board = { id: 'x', has_results: false, result_state: state, analyses: [], last_surveyed_at: state === 'never_surveyed' ? '' : '2026-10-18T10:00:00Z' };
    assert.equal(app.boardStateWords(board), words);
    const html = app.byAnalysisCardHtml('s', 'x', 'X', '', { status: 'done', board }, new Set(), true);
    assert.ok(text(html).includes(words), state);
    const said = /not yet run/i.test(text(html));
    assert.equal(said, state === 'never_surveyed', `${state}: "not yet run" only for never_surveyed`);
    assert.ok(!/no result for this step/.test(html));
  }
});

test('the screenshot contradiction: a surveyed resource never reads "Not yet run." in body or header', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  // a server that sent no result_state at all: falls back to what the board's own fields prove
  const board = { id: 'data_class_match', has_results: false, analyses: [], last_surveyed_at: '2026-10-18T10:00:00Z' };
  const html = app.byAnalysisCardHtml('s', 'data_class_match', 'Data Class Match', '', { status: 'done', board }, new Set(), true);
  assert.ok(!/not yet run/i.test(text(html)));
  assert.match(app.boardRunWords(board), /^last survey /);
});

test('glyphs: no result state is a ✕; no_results_view and predates are the unknown glyph, ✕ stays for a real error', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  for (const [state] of CASES) {
    const key = app.boardStateKey({ status: 'done', board: { has_results: false, result_state: state, analyses: [] } });
    assert.notEqual(key, 'error', state);
  }
  assert.equal(app.boardStateKey({ status: 'done', board: { has_results: false, result_state: 'no_results_view', analyses: [] } }), 'not_measured');
  assert.equal(app.boardStateKey({ status: 'done', board: null }), 'not_measured');
  assert.equal(app.boardStateKey({ status: 'error', board: null, error: 'x' }), 'error');
});

/* ── 3. header counts ────────────────────────────────────────────────────────────────────────── */

test('the header counts every analysis: the parts sum to the total, with and without boards still reading', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const mk = (id, over) => [id, { status: 'done', board: { id, analyses: [], has_results: false, last_surveyed_at: 'x', ...over } }];
  const ids = Array.from({ length: 13 }, (_, i) => `a${i}`);
  const entries = ids.map((id, i) => i < 10 ? mk(id, { has_results: true, result_state: 'has_results' })
    : i < 12 ? mk(id, { result_state: 'not_requested' }) : mk(id, { result_state: 'no_results_view' }));
  const state = new Map(entries);
  const boards = ids.map((id) => ({ id, title: id }));
  const c = app.byAnalysisCounts(boards, state);
  assert.equal(c.total, 13);
  assert.equal(c.ran + c.parts.reduce((n, p) => n + p.n, 0), 13);
  // with a loading board, an errored one and a timeout in the mix
  state.set('a0', { status: 'loading', board: null });
  state.set('a1', { status: 'error', board: null, error: 'x' });
  state.set('a2', { status: 'timeout', board: null });
  const d = app.byAnalysisCounts(boards, state);
  assert.equal(d.ran + d.parts.reduce((n, p) => n + p.n, 0), 13);
  // and on screen
  const document = globalThis.document;
  const el = document.createElement('div'); el.id = 'by-analysis-contents'; document.body.appendChild(el);
  app.renderByAnalysisContents('s', boards, new Map(entries), 13, 13, new Map());
  assert.ok(text(el.querySelector('div > div').innerHTML).startsWith('13 analyses · 10 ran · 2 not in the last survey · 1 no results view yet'));
});

/* ── 4. closed cards ─────────────────────────────────────────────────────────────────────────── */

test('a closed card shows its headline in the summary, full text on hover; open, the body carries it', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const board = { id: 'schema_conventions', has_results: true, result_state: 'has_results', last_surveyed_at: 'x',
    analyses: [{ analysis_id: 'schema_conventions', results: {}, headline: { label: '32 of 263 base tables declare no primary key. 54 more.' } }] };
  const html = app.byAnalysisCardHtml('s', 'schema_conventions', 'Schema Conventions', '', { status: 'done', board }, new Set(), false);
  const m = html.match(/<summary[\s\S]*?<\/summary>/)[0];
  assert.match(m, /data-card-summary-line/);
  assert.match(text(m), /32 of 263 base tables declare no primary key\./);
  assert.match(m, /title="32 of 263 base tables declare no primary key\./);
  assert.match(m, /group-open:hidden/, 'hidden once the card is open, where the body shows it');
  assert.match(html, /<details[^>]*class="group /);
});
