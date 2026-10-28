/** Behaviour: Understanding on a database draws the designer's database
 *  charts from the new stats routes, and says what each response says.
 *
 *  Slice: REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §3-§5 and
 *  UNDERSTANDING-DB-CHARTS-IMPLEMENTED.md (route shapes). Source-text pins let
 *  blank/wrong panes through four times, so every test here runs the REAL
 *  app.js and the REAL router (clicks the real Understanding nav button) and
 *  reads the DOM. Only the network (fetch) and Plotly (a recording stub) are
 *  stubbed.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { openAllUnderstandingTabs } from './understanding-tabs-kit.mjs';

const SLUG = 'adventureworks';
const RUN = { run_id: 7, surveyed_at: '2026-09-28T14:02:00', source: 'local', surveyed_as: 'survey_user' };
const OLDER = { run_id: 6, surveyed_at: '2026-09-28T09:00:00', source: 'local', surveyed_as: 'survey_user' };
const OLDEST = { run_id: 5, surveyed_at: '2026-09-21T10:00:00', source: 'local', surveyed_as: 'survey_user' };

const envelope = (extra = {}) => ({
  database: SLUG, run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false },
  figure: null, ...extra,
});

const DIST = envelope({
  schemas: ['person', 'sales'], table_counts: [13, 19], column_counts: [96, 160], schema_scope: {},
  figure: { data: [{ type: 'bar', orientation: 'h', y: ['person', 'sales'], x: [13, 19] }], layout: { title: { text: 't' } } },
});
const SIZES = envelope({
  measure: 'rows', tables: ['sales.salesorderdetail', 'person.person'], row_counts: [121317, 19972], sizes_mb: [10, 5],
  ranked_count: 2, truncated_by_limit: 0, table_count_total: 14, views_excluded: 0,
  not_established_count: 0, not_established_tables: [], provenance: "rows, as estimated by the server's statistics",
  figure: { data: [{ type: 'bar', orientation: 'h', y: ['sales.salesorderdetail', 'person.person'], x: [121317, 19972] }], layout: {} },
});
const TYPES = envelope({
  types: ['integer', 'character varying'], counts: [201, 125], type_not_recorded: 0, column_count_total: 326,
  figure: { data: [{ type: 'bar', x: ['integer', 'character varying'], y: [201, 125] }], layout: {} },
});
const HIST = envelope({
  points: [
    { run: OLDEST, schema_count: 5, table_count: 50, column_count: 400, states: {} },
    { run: OLDER, schema_count: 5, table_count: 55, column_count: 410, states: {} },
    { run: RUN, schema_count: 5, table_count: null, column_count: 420, states: { tables: 'not_collected' } },
  ],
  state: 'partial', reasons: ['runs_without_table_measurement'],
  figure: { data: [{ type: 'scatter', mode: 'lines+markers', name: 'Tables',
    x: [OLDEST.surveyed_at, OLDER.surveyed_at, RUN.surveyed_at], y: [50, 55, null], connectgaps: false }],
  layout: { xaxis: { type: 'date' } } },
});
const GROWTH = envelope({
  runs: [OLDEST, OLDER, RUN], series: [{ table: 'sales.salesorderdetail', row_counts: [100, 110, 121317] }],
  figure: { data: [{ type: 'scatter', mode: 'lines+markers', name: 'sales.salesorderdetail',
    x: [OLDEST.surveyed_at, OLDER.surveyed_at, RUN.surveyed_at], y: [100, 110, 121317] }], layout: {} },
});
const DIFF = {
  prev_date: '2026-09-21T10:00:00', curr_date: '2026-09-28T14:02:00',
  deltas: { schemas: 0, tables: 1, columns: 14 },
  new_tables: ['sales.a', 'sales.b'], removed_tables: ['hr.old'], table_diff_state: 'measured',
  curr_table_state: 'measured', prev_table_state: 'measured',
};

function makeStub(overrides = {}) {
  const calls = [];
  const responses = { schema_distribution: DIST, table_sizes: SIZES, column_types: TYPES,
    survey_history: HIST, table_growth: GROWTH, diff: DIFF, ...overrides };
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    const m = u.match(/\/api\/stats\/databases\/[^/]+\/([a-z_]+)/);
    if (m) {
      const key = (m[1] === 'table_sizes' && u.includes('measure=size') && responses.table_sizes_size)
        ? 'table_sizes_size' : m[1];
      const r = responses[key];
      if (r && r.__http) return { ok: false, status: r.__http, statusText: 'boom', json: async () => ({ detail: 'boom' }) };
      return ok(r);
    }
    if (u.includes('/diff')) return ok(responses.diff);
    if (/\/api\/stats\/[^/]+\/charts\//.test(u)) {
      const kind = u.split('/charts/')[1];
      return ok(responses.repoCharts?.[kind] ?? { data: [] });
    }
    return ok({});
  };
  return calls;
}

function makePlotly() {
  const rec = { newPlot: [], toImage: [], purged: 0 };
  const Plotly = {
    async newPlot(el, data, layout) { el.data = data; el.layout = layout; rec.newPlot.push({ el, data, layout }); },
    async toImage(el, opts) { rec.toImage.push({ el, opts }); return 'data:image/png;base64,AAAA'; },
    purge() { rec.purged += 1; },
  };
  return { Plotly, rec };
}

async function setUp({ resourceType = 'db', stubs = {}, subTab = 'questions' } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeStub(stubs);
  const { Plotly, rec } = makePlotly();
  window.Plotly = Plotly;
  const downloads = [];
  window.HTMLAnchorElement.prototype.click = function click() { downloads.push({ href: this.href, name: this.download }); };
  // loadScript() waits for a real <script> load, which jsdom never does.
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => {
    const r = append(el);
    if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0);
    return r;
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window); // tokens() reads theme colours
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = resourceType;
  app.state.selectedSlug = SLUG;
  app.state.stage = 'scouting';
  app.state.subTab = subTab;
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, window, app, calls, rec, downloads };
}

async function openUnderstanding(document) {
  const btn = document.querySelector('#intent-nav button[data-stage="understanding"]');
  assert.ok(btn, 'nav must offer an Understanding button');
  btn.click();
  await new Promise((r) => setTimeout(r, 400));
  await openAllUnderstandingTabs(document);
}

const card = (document, kind) => document.querySelector(`[data-chart="${kind}"]`);
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
// The sentence only: names behind a count live in hidden [data-names] blocks.
const sentence = (el) => {
  const c = el.cloneNode(true);
  c.querySelectorAll('[data-names]').forEach((n) => n.remove());
  return text(c);
};

test('a database shows the six charts in two sections, from the new routes', async () => {
  const { document, calls, rec } = await setUp();
  await openUnderstanding(document);
  const now = [...document.querySelectorAll('[data-section="now"] [data-chart]')].map((c) => c.dataset.chart);
  const time = [...document.querySelectorAll('[data-section="time"] [data-chart]')].map((c) => c.dataset.chart);
  assert.deepEqual(now, ['schema_distribution', 'table_sizes', 'column_types']);
  assert.deepEqual(time, ['survey_history', 'table_growth', 'table_activity']);
  assert.match(text(document.querySelector('[data-section="now"]')), /^Now/);
  assert.match(text(document.querySelector('[data-section="time"]')), /^Over time/);
  for (const k of ['schema_distribution', 'table_sizes', 'column_types', 'survey_history', 'table_growth']) {
    assert.ok(calls.some((u) => u.includes(`/api/stats/databases/${SLUG}/${k}`)), `${k} route must be called`);
  }
  assert.ok(calls.some((u) => u.endsWith(`/api/databases/${SLUG}/diff`)), 'the diff route feeds "since the last run"');
  assert.equal(rec.newPlot.length, 5, 'five charts have a figure; activity has no route');
  // The repo-kinds list and its 'does not apply' tiles are gone.
  assert.doesNotMatch(text(document.getElementById('content')), /does not apply to this resource type|Stars over time/);
  // Activity per table is present and explained, with no fetch of its own.
  assert.match(text(card(document, 'table_activity')), /not wired up yet/);
});

test('"now" header names its run and source; "over time" counts runs; chips carry the response state', async () => {
  const { document } = await setUp();
  await openUnderstanding(document);
  assert.match(text(document.querySelector('[data-now-header]')), /from the survey of 09-28 14:02 · local/);
  assert.match(text(document.querySelector('[data-time-header]')), /3 runs since 09-21 10:00/);
  assert.match(text(document.querySelector('[data-chart-chip="survey_history"]')), /Structure over runs · partial/);
  assert.match(text(document.querySelector('[data-chart-chip="column_types"]')), /Column types · measured/);
});

test('a not_measured response says so and draws no figure and no zero', async () => {
  const notMeasured = envelope({
    state: 'not_measured', reasons: ['row_counts_not_established'],
    notes: ['No table has an established row count in this run.'],
    tables: [], row_counts: [], sizes_mb: [], not_established_count: 14, not_established_tables: ['a.b'],
    figure: null,
  });
  const { document, rec } = await setUp({ stubs: { table_sizes: notMeasured } });
  await openUnderstanding(document);
  const c = card(document, 'table_sizes');
  assert.equal(c.dataset.cardState, 'not_measured');
  assert.match(text(c), /○ not measured/);
  assert.match(text(c), /No table has an established row count/);
  assert.doesNotMatch(text(c), /(^|[^\d])0([^\d]|$)/, 'no zero anywhere in a not-measured card');
  assert.ok(!rec.newPlot.some((p) => card(document, 'table_sizes').contains(p.el)), 'no chart drawn into it');
  assert.ok(c.querySelector('[data-measure="size"]'), 'a labelled by-size view is offered, not a silent fallback');
});

test('never surveyed: "not run" with a Run survey control, never "no data"', async () => {
  const never = (extra) => envelope({ run: null, state: 'not_measured', reasons: ['never_surveyed'], ...extra });
  const { document } = await setUp({
    stubs: { schema_distribution: never(), table_sizes: never(), column_types: never(),
      survey_history: never({ points: [] }), table_growth: never(), diff: {} },
  });
  await openUnderstanding(document);
  const c = card(document, 'column_types');
  assert.match(text(c), /○ not run: needs a database survey/);
  assert.match(text(c), /Run survey/);
  assert.doesNotMatch(text(document.getElementById('content')), /no data/i);
  assert.match(text(document.querySelector('[data-since-last-run]')), /Only one run so far — nothing to compare/);
});

test('a partial table_sizes response names the not-established count and the tables', async () => {
  const partial = { ...SIZES, state: 'partial', reasons: ['row_counts_not_established'],
    not_established_count: 12, not_established_tables: ['s.t1', 's.t2'], truncated_by_limit: 3, ranked_count: 5 };
  const { document, rec } = await setUp({ stubs: { table_sizes: partial } });
  await openUnderstanding(document);
  const c = card(document, 'table_sizes');
  assert.equal(c.dataset.cardState, 'partial');
  assert.match(text(c), /◐ partial/);
  assert.match(sentence(c), /\? 12 tables' row counts not established — statistics not gathered on the server/);
  assert.match(text(c), /by size instead/);
  const names = c.querySelector('[data-names]');
  assert.equal(names.hidden, true);
  c.querySelector('[data-names-toggle]').click();
  assert.equal(names.hidden, false, 'the count opens the names it counted');
  assert.match(names.textContent, /s\.t1/);
  assert.match(text(c), /Showing the largest 2 of 5/);
  const drawn = rec.newPlot.find((p) => c.contains(p.el));
  assert.deepEqual(drawn.data[0].x, [121317, 19972], 'unestablished tables are not drawn as zero bars');
});

test('by size instead refetches table_sizes with measure=size and relabels', async () => {
  const partial = { ...SIZES, state: 'partial', reasons: ['row_counts_not_established'],
    not_established_count: 2, not_established_tables: ['s.t1', 's.t2'] };
  const bySize = { ...SIZES, measure: 'size', not_established_count: 0, not_established_tables: [] };
  const { document, calls } = await setUp({ stubs: { table_sizes: partial, table_sizes_size: bySize } });
  await openUnderstanding(document);
  card(document, 'table_sizes').querySelector('[data-measure="size"]').click();
  await new Promise((r) => setTimeout(r, 200));
  assert.ok(calls.some((u) => u.includes('table_sizes?measure=size')));
  assert.match(text(card(document, 'table_sizes')), /Largest tables, by size/);
});

test('column types: a missing type is "type not recorded", never a bar named unknown', async () => {
  const t = { ...TYPES, state: 'partial', reasons: ['type_not_recorded'], type_not_recorded: 3 };
  const { document } = await setUp({ stubs: { column_types: t } });
  await openUnderstanding(document);
  assert.match(text(card(document, 'column_types')), /\? type not recorded · 3/);
  assert.doesNotMatch(text(card(document, 'column_types')), /unknown/);
});

test('two runs on one day are two points, and a null point is a gap (not zero)', async () => {
  const { document, rec } = await setUp();
  await openUnderstanding(document);
  const c = card(document, 'survey_history');
  const drawn = rec.newPlot.find((p) => c.contains(p.el));
  const day = (s) => String(s).slice(0, 10);
  assert.equal(drawn.data[0].x.length, 3);
  assert.equal(day(drawn.data[0].x[1]), day(drawn.data[0].x[2]), 'two runs share a day');
  assert.notEqual(drawn.data[0].x[1], drawn.data[0].x[2], 'but keep their own timestamps');
  assert.equal(drawn.data[0].y[2], null, 'the unmeasured run is a null, not a 0');
  assert.equal(drawn.data[0].connectgaps, false);
  assert.match(text(c), /partial — some runs did not measure tables/);
});

test('every chart shows its provenance line: resource, run, source', async () => {
  const { document } = await setUp();
  await openUnderstanding(document);
  for (const k of ['schema_distribution', 'table_sizes', 'column_types', 'survey_history', 'table_growth']) {
    const prov = text(card(document, k).querySelector('[data-card-provenance]'));
    assert.match(prov, new RegExp(`^${SLUG} · run 2026-09-28T14:02:00 · source local`), k);
    assert.match(prov, /Save image/, `${k} has its own Save image`);
  }
  assert.match(text(card(document, 'table_sizes').querySelector('[data-card-provenance]')),
    /rows, as estimated by the server's statistics/);
});

test('credential scope: a partial scope names itself in the Now header', async () => {
  const scoped = { ...DIST, state: 'partial', reasons: ['credential_scope'],
    scope: { checked: true, partial: true, fraction: '3 of 5', schema_fraction: '3 of 5 schemas' },
    schema_scope: { person: 'structure_only', sales: 'readable' } };
  const { document } = await setUp({ stubs: { schema_distribution: scoped } });
  await openUnderstanding(document);
  assert.match(text(document.querySelector('[data-now-header]')), /◐ credential scope: 3 of 5 schemas readable/);
  assert.match(text(card(document, 'schema_distribution')), /Not fully readable with this credential: person \(structure only\)/);
});

test('"since the last run" is a sentence from the diff route, counts open names', async () => {
  const { document } = await setUp();
  await openUnderstanding(document);
  const s = document.querySelector('[data-since-last-run]');
  assert.match(sentence(s), /^Since the run of 2026-09-21: 2 tables added, 1 removed, 14 columns net added\.$/);
  const toggles = [...s.querySelectorAll('[data-names-toggle]')];
  assert.equal(toggles.length, 2);
  toggles[0].click();
  assert.match(s.querySelector('[data-names]').textContent, /sales\.a.*sales\.b|sales\.asales\.b/);
  assert.equal(s.querySelector('[data-names]').hidden, false);
  assert.match(text(s), /hr\.old/);
});

test('"since the last run" with not_comparable gives the route\'s own note, not a count', async () => {
  const diff = { ...DIFF, table_diff_state: 'not_comparable', new_tables: [], removed_tables: [],
    table_diff_note: 'Run of 09-21 predates the structured tables; run the backfill.' };
  const { document } = await setUp({ stubs: { diff } });
  await openUnderstanding(document);
  const s = text(document.querySelector('[data-since-last-run]'));
  assert.match(s, /run the backfill/);
  assert.doesNotMatch(s, /tables added|14 columns/);
});

test('a route that fails says so in the card, never a blank or zero', async () => {
  const { document } = await setUp({ stubs: { column_types: { __http: 500 } } });
  await openUnderstanding(document);
  const c = card(document, 'column_types');
  assert.match(text(c), /could not be read: boom/);
  assert.equal(c.dataset.cardState, 'error');
  assert.ok(text(card(document, 'table_sizes')).length > 20, 'the others still draw');
});

test('Save image: the footer is composed from the response provenance fields', async () => {
  const { document, rec, downloads } = await setUp();
  await openUnderstanding(document);
  const c = card(document, 'table_sizes');
  c.querySelector('[data-save-image]').click();
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(rec.toImage.length, 1, "Plotly's own toImage is used");
  assert.equal(rec.toImage[0].opts.format, 'png');
  // Expected text is built here from the RESPONSE FIXTURE, not from the page.
  const expected = `${SLUG} · Largest tables, by rows · run ${SIZES.run.surveyed_at} · source ${SIZES.run.source}`;
  const exported = rec.newPlot.find((p) => p.el === rec.toImage[0].el);
  assert.ok(exported, 'toImage was called on the off-screen figure carrying the footer');
  assert.equal(exported.layout.annotations.at(-1).text, expected);
  assert.equal(c.dataset.lastFooter, expected);
  assert.equal(downloads.length, 1);
  assert.match(downloads[0].name, /^adventureworks-largest-tables-by-rows\.png$/);
  assert.ok(rec.purged >= 1, 'the off-screen figure is purged');
  assert.equal(rec.newPlot.filter((p) => c.contains(p.el)).length, 1, 'the visible chart was not redrawn');
});

test('KNOWN-NEGATIVE: change the response source and the footer changes with it', async () => {
  const egeria = { ...SIZES, run: { ...RUN, source: 'egeria', surveyed_at: '2026-10-01T08:30:00' } };
  const { document, rec } = await setUp({ stubs: { table_sizes: egeria } });
  await openUnderstanding(document);
  card(document, 'table_sizes').querySelector('[data-save-image]').click();
  await new Promise((r) => setTimeout(r, 200));
  const exported = rec.newPlot.find((p) => p.el === rec.toImage[0].el);
  const footer = exported.layout.annotations.at(-1).text;
  assert.match(footer, /run 2026-10-01T08:30:00 · source egeria$/);
  assert.doesNotMatch(footer, /source local/);
});

test('chart-export: no run in the response is written as unknown, and missing Plotly throws', async () => {
  await setUp();
  const { composeFooter, provenanceFromResponse, saveChartImage } = await import('/static/next/chart-export.js');
  assert.equal(composeFooter(provenanceFromResponse('db1', 'T', { run: null })),
    'db1 · T · run unknown · source unknown');
  await assert.rejects(() => saveChartImage({ Plotly: null, figure: {}, provenance: {} }), /Plotly is not loaded/);
});

test('a file system gets the not-charted sentence and fetches no chart', async () => {
  const { document, calls } = await setUp({ resourceType: 'filesystem' });
  await openUnderstanding(document);
  assert.match(text(document.getElementById('content')),
    /No charts for file systems yet\. Their survey isn't charted anywhere today\./);
  assert.ok(!calls.some((u) => u.includes('/api/stats/')), 'nothing is fetched for a file system');
  assert.equal(document.querySelectorAll('[data-chart]').length, 0);
});

test('a repo still gets its repo charts', async () => {
  const fig = { data: [{ type: 'scatter', x: ['2026-09-01', '2026-09-08'], y: [1, 2] }], layout: {} };
  const { document, calls } = await setUp({ resourceType: 'repo', stubs: { repoCharts: { stars: fig, commits: fig } } });
  await openUnderstanding(document);
  assert.ok(calls.some((u) => u.includes(`/api/stats/${SLUG}/charts/stars`)));
  assert.ok(!calls.some((u) => u.includes('/api/stats/databases/')), 'a repo never calls the database routes');
  const chips = [...document.querySelectorAll('#chart-index [data-chart]')].map((b) => b.dataset.chart);
  assert.deepEqual(chips, ['stars', 'commits'], 'kinds with points are selectable; the rest say "nothing recorded yet"');
  assert.match(text(document.getElementById('chart-index')), /Survey history · nothing recorded yet/);
  assert.match(text(document.querySelector('#chart-index [data-chart="stars"]')), /Stars over time/);
});

test('frame stages route before sub-tab branches: Understanding draws even with subTab left on context', async () => {
  const { document } = await setUp({ subTab: 'context' });
  await openUnderstanding(document);
  assert.ok(card(document, 'table_sizes'), 'the database charts are drawn');
});

test('KNOWN-NEGATIVE: a missing host is a visible failure, not nothing', async () => {
  await setUp();
  const { renderDatabaseUnderstanding, loadChartsPane } = await import('/static/next/stages/understanding.js');
  await assert.rejects(() => renderDatabaseUnderstanding(SLUG), /Understanding pane host missing/);
  document.getElementById('content').remove();
  await assert.rejects(() => loadChartsPane(), /Understanding pane host missing: #content/);
});

/* ── chart labels: fitted, shortened, thinned (real-browser geometry is in
 *    tests/test_understanding_chart_labels_browser.py) ───────────────────── */

const LONG_TYPES = ['character varying', 'timestamp without time zone', 'integer', 'numeric', 'text',
  'date', 'double precision', 'boolean', 'bytea', 'character', 'uuid', 'smallint', 'timestamp with time zone'];

test('chart labels: thinIndexes keeps first and last and never lets two boxes touch', async () => {
  const { thinIndexes } = await import('/static/next/chart-labels.js');
  const pos = Array.from({ length: 30 }, (_, i) => (i * 600) / 29);
  const widths = pos.map(() => 70);
  const keep = thinIndexes(pos, widths, 12);
  assert.equal(keep[0], 0);
  assert.equal(keep[keep.length - 1], 29);
  assert.ok(keep.length < 30 && keep.length > 2);
  for (let k = 1; k < keep.length; k += 1) {
    assert.ok(pos[keep[k]] - 35 >= pos[keep[k - 1]] + 35 + 12 - 1e-9, `labels ${keep[k - 1]} and ${keep[k]} are apart`);
  }
});

test('chart labels: long category names are shortened in ticktext only, rotated, with room below', async () => {
  const { fitFigure, ellipsize } = await import('/static/next/chart-labels.js');
  assert.equal(ellipsize('timestamp without time zone', 16), 'timestamp witho…');
  const data = [{ type: 'bar', x: LONG_TYPES, y: LONG_TYPES.map((_, i) => 100 - i) }];
  const fit = fitFigure({ data, layout: {} }, 760, 'Column types');
  const ax = fit.layout.xaxis;
  assert.ok(ax.ticktext.every((s) => s.length <= 16), 'no label is longer than 16 characters');
  assert.ok(ax.ticktext.includes('timestamp witho…'));
  assert.deepEqual(data[0].x, LONG_TYPES, 'the data keeps the full names');
  assert.ok(ax.tickvals.includes('timestamp without time zone'), 'ticks are keyed on the full names');
  assert.notEqual(ax.tickangle, 0, 'long labels are rotated at this width');
  assert.ok(fit.layout.margin.b >= 60, `bottom margin ${fit.layout.margin.b} holds the rotated labels`);
  assert.match(fit.summary, /timestamp without time zone/, 'the full name is in the text alternative');
});

test('chart labels: the two schema panels have a gutter and the captions sit below the ticks', async () => {
  const { fitFigure } = await import('/static/next/chart-labels.js');
  const names = ['public', 'clinical', 'regulatory_affairs', 'manufacturing'];
  const fig = { data: [
    { type: 'bar', orientation: 'h', y: names, x: [1, 2, 3, 4], xaxis: 'x', yaxis: 'y' },
    { type: 'bar', orientation: 'h', y: names, x: [10, 20, 30, 40], xaxis: 'x2', yaxis: 'y' }],
  layout: { xaxis: { domain: [0, 0.45], title: { text: 'Tables' } },
    xaxis2: { domain: [0.55, 1], title: { text: 'Columns' } }, yaxis: { autorange: 'reversed' } } };
  for (const width of [1100, 760]) {
    const { layout } = fitFigure(fig, width);
    const plotW = width - layout.margin.l - layout.margin.r;
    const gutterPx = (layout.xaxis2.domain[0] - layout.xaxis.domain[1]) * plotW;
    assert.ok(gutterPx >= 55, `${width}px: ${gutterPx.toFixed(1)}px between the panels`);
    assert.ok(layout.xaxis.nticks <= Math.floor(plotW / 2 / 50), 'fewer ticks than the panel can hold');
    assert.ok(layout.xaxis.title.standoff > 0 && layout.xaxis2.title.standoff > 0, 'caption stands off the ticks');
    assert.equal(layout.yaxis.autorange, 'reversed', 'the axis direction is unchanged');
  }
});

test('chart labels: a 30-run date axis is thinned to labels that fit, first and last kept', async () => {
  const { fitFigure } = await import('/static/next/chart-labels.js');
  const runs = Array.from({ length: 30 }, (_, i) => `2026-09-${String(i + 1).padStart(2, '0')}T09:00:00`);
  const fit = fitFigure({ data: [{ type: 'scatter', x: runs, y: runs.map((_, i) => i) }],
    layout: { xaxis: { type: 'date' } } }, 760);
  const t = fit.layout.xaxis;
  assert.equal(t.tickvals[0], runs[0]);
  assert.equal(t.tickvals[t.tickvals.length - 1], runs[runs.length - 1]);
  assert.ok(t.tickvals.length < 30 && t.tickvals.length <= 16, `${t.tickvals.length} labels`);
  assert.equal(new Set(t.ticktext).size, t.ticktext.length, 'no two labels read the same');
});

test('chart labels: Understanding hands Plotly the fitted layout and keeps the full names', async () => {
  const types = envelope({
    types: LONG_TYPES, counts: LONG_TYPES.map((_, i) => 100 - i), type_not_recorded: 0, column_count_total: 999,
    figure: { data: [{ type: 'bar', x: LONG_TYPES, y: LONG_TYPES.map((_, i) => 100 - i) }], layout: {} },
  });
  const { document, rec } = await setUp({ stubs: { column_types: types } });
  await openUnderstanding(document);
  const c = card(document, 'column_types');
  const drawn = rec.newPlot.find((p) => c.contains(p.el));
  assert.ok(drawn.layout.xaxis.ticktext.includes('timestamp witho…'));
  assert.deepEqual(drawn.data[0].x, LONG_TYPES, 'the bars are keyed on the full names');
  assert.ok(drawn.layout.margin.b >= 60);
  assert.match(drawn.el.getAttribute('aria-label'), /timestamp without time zone/);
  assert.ok(c.querySelector('[data-save-image]'), 'Save image is still offered');
});
