/** Brief S4: Understanding's leading tabs (At a glance / Contents / Over time) for repositories and databases.
 *  Real app.js, real router, real nav and tab buttons; only fetch and Plotly are stubbed. Pins: every section is on
 *  exactly its tab, the since-last-run sentence leads At a glance, a tab switch reads nothing again, a hidden
 *  tab reads nothing until it opens, a URL-style reopen lands on the named tab, a file system is unchanged. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SLUG = 'adv';
const RUN = { run_id: 7, surveyed_at: '2026-09-28T14:02:00', source: 'local', surveyed_as: 's' };
const env = (extra = {}) => ({ database: SLUG, run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...extra });
const DIFF = { prev_date: '2026-09-21T10:00:00', curr_date: '2026-09-28T14:02:00', deltas: { schemas: 0, tables: 1, columns: 14 },
  new_tables: ['sales.a'], removed_tables: [], table_diff_state: 'measured', curr_table_state: 'measured', prev_table_state: 'measured' };
const REPO_DIFF = { prev_date: '2026-10-03T09:00:00', curr_date: '2026-10-06T14:02:00', total_prev: 100, total_curr: 112, total_delta: 12, changes: [] };

const CURRENCY = { as_of: '2026-10-09T10:00:00', stale_days: 30,
  database: { total: 140, behind: 31, never: 3, stale: 28, reset_tables: 2, not_collected: 0,
    counters_reset: { state: 'reset_at', at: '2026-10-02T08:00:00' } },
  schemas: [{ schema: 'sales', total: 40, never: 3, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: 41, empty: false },
    { schema: 'scratch', total: 0, never: 0, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: null, empty: true }],
  tables: {} };
const HISTORY = [
  { run: { surveyed_at: '2026-10-01T10:00:00' }, measured: true, never: 5, stale: 10, reset_tables: 0, counters_reset: { state: 'never_reset' } },
  { run: { surveyed_at: '2026-10-05T10:00:00' }, measured: false },
  { run: { surveyed_at: '2026-10-09T10:00:00' }, measured: true, never: 3, stale: 28, reset_tables: 2, counters_reset: CURRENCY.database.counters_reset }];

function stub(calls) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (/\/statistics_currency/.test(u)) return ok(env({ currency: CURRENCY, history: HISTORY }));
    const m = u.match(/\/api\/stats\/databases\/[^/]+\/([a-z_]+)/);
    if (m) return ok(env({ points: [], runs: [], series: [], tables: [], row_counts: [], sizes_mb: [], schemas: [], table_counts: [], column_counts: [], types: [], counts: [] }));
    if (/\/api\/databases\/[^/]+\/diff/.test(u)) return ok(DIFF);
    if (/\/api\/egeria\/[^/]+\/diff/.test(u)) return ok(REPO_DIFF);
    if (/\/api\/databases\/[^/]+\/surveys/.test(u)) return ok([]);
    if (/\/api\/databases\/[^/]+\/views/.test(u)) return ok({ state: 'measured', run: RUN, views: [] });
    if (/\/api\/stats\/[^/]+\/charts\//.test(u)) return ok({ data: [] });
    if (u.includes('/survey-report')) return ok({});
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    return ok({});
  };
}

async function setUp(resourceType) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  stub(calls);
  window.Plotly = { async newPlot(el) { el.drawn = true; }, purge() {}, async toImage() { return ''; } };
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => { const r = append(el); if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0); return r; };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType, selectedSlug: SLUG, stage: 'scouting', subTab: 'questions', investigations: [], investigation: '', workListSlug: null, workListIndex: false });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, window, app, calls };
}

const wait = (ms = 300) => new Promise((r) => setTimeout(r, ms));
const open = async (document) => { document.querySelector('#intent-nav button[data-stage="understanding"]').click(); await wait(500); };
const tab = async (document, id) => { document.querySelector(`[data-subtab="understanding_${id}"]`).click(); await wait(); };
const panel = (d, id) => d.querySelector(`[data-understanding-tab-panel="${id}"]`);
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const railText = (d) => [...d.querySelectorAll('[data-subtab-rail] > *')].map((e) => norm(e));
const hits = (calls, re) => calls.filter((u) => re.test(u)).length;
const shown = (d) => [...d.querySelectorAll('[data-understanding-tab-panel]')].filter((p) => !p.hidden).map((p) => p.dataset.understandingTabPanel);
const sectionsOn = (d, id) => [...panel(d, id).querySelectorAll('details[data-collapsible]')].map((x) => x.dataset.collapsible);

for (const kind of ['db', 'repo']) {
  test(`${kind}: the rail leads with At a glance, Contents, Over time, then the shared tabs; it opens on At a glance`, async () => {
    const { document, app } = await setUp(kind);
    await open(document);
    const rail = railText(document);
    assert.deepEqual(rail.slice(0, 3), ['At a glance', 'Contents', 'Over time']);
    assert.ok(rail.includes('Questions') && rail.indexOf('Questions') > 2, `shared tabs follow: ${rail}`);
    assert.equal(app.state.subTab, 'understanding_glance');
    assert.deepEqual(shown(document), ['glance']);
    assert.equal(new URLSearchParams(location.search).get('tab'), 'understanding_glance');
    assert.doesNotMatch(norm(document.getElementById('content')), /Understanding has one pane/);
  });
}

test('db: every section is on exactly one tab; the since-last-run sentence leads At a glance and is nowhere else', async () => {
  const { document } = await setUp('db');
  await open(document);
  assert.deepEqual(sectionsOn(document, 'glance'), ['db-since']);
  assert.deepEqual(sectionsOn(document, 'contents'), ['db-now', 'db-views']);
  assert.deepEqual(sectionsOn(document, 'over_time'), ['db-time', 'db-survey-history']);
  const g = panel(document, 'glance');
  assert.equal(g.firstElementChild.querySelector('[data-since-last-run]'), g.querySelector('[data-since-last-run]'), 'the sentence is the first thing in the panel');
  assert.equal(document.querySelectorAll('[data-since-last-run]').length, 1);
  assert.match(norm(g.querySelector('[data-since-last-run]')), /^Since the run of 2026-09-21: 1 table added/);
  const cards = (id) => [...panel(document, id).querySelectorAll('[data-chart]')].map((c) => c.dataset.chart);
  assert.deepEqual(cards('contents'), ['schema_distribution', 'table_sizes', 'column_types']);
  assert.deepEqual(cards('over_time'), ['survey_history', 'table_growth', 'table_activity']);
  assert.equal(cards('glance').length, 0);
});

test('repo: since-last-run on At a glance, the survey report on Contents, the charts on Over time', async () => {
  const { document } = await setUp('repo');
  await open(document);
  assert.deepEqual(sectionsOn(document, 'glance'), ['repo-since']);
  assert.deepEqual(sectionsOn(document, 'contents'), ['repo-distribution', 'repo-report', 'repo-classification', 'repo-dependency-support']);
  assert.deepEqual(sectionsOn(document, 'over_time'), ['repo-charts']);
  // chips: the distribution kinds are indexed on Contents, the series on Over time, each kind in exactly one place
  await tab(document, 'contents');
  await tab(document, 'over_time');
  const kinds = (id) => [...document.querySelectorAll(`#${id} > *`)].map((e) => norm(e));
  const dist = kinds('chart-index-distribution').join('|');
  const time = kinds('chart-index').join('|');
  for (const l of ['Languages', 'File types', 'Top committers']) { assert.match(dist, new RegExp(l)); assert.doesNotMatch(time, new RegExp(l)); }
  for (const l of ['Stars over time', 'Commits over time', 'Commits by week', 'Health', 'Survey history']) { assert.match(time, new RegExp(l)); assert.doesNotMatch(dist, new RegExp(l)); }
  assert.match(norm(panel(document, 'glance').querySelector('[data-since-last-run]')), /^Since the run of 2026-10-03: 112 files, 12 more/);
});

test('db: a hidden tab reads nothing until it opens; a tab switch reads nothing again', async () => {
  const { document, calls } = await setUp('db');
  await open(document);
  assert.equal(hits(calls, /\/diff/), 1);
  // At a glance draws its headline from the three Now reads; the series and history stay unread.
  assert.equal(hits(calls, /\/api\/stats\/databases\/[^/]+\/(schema_distribution|table_sizes|column_types)/), 3, 'the headline reads the three Now charts');
  assert.equal(hits(calls, /survey_history|table_growth/), 0, 'no series read while only At a glance is open');
  assert.equal(hits(calls, /statistics_currency/), 1, 'the statistics line is the one read At a glance adds');
  assert.equal(hits(calls, /\/views|\/surveys/), 0);
  await tab(document, 'contents');
  assert.equal(hits(calls, /\/api\/stats\/databases\/[^/]+\/(schema_distribution|table_sizes|column_types)/), 3, 'Contents reuses the headline reads');
  assert.equal(hits(calls, /\/views/), 1);
  assert.equal(hits(calls, /survey_history|table_growth|\/surveys/), 0, 'Over time still unread');
  assert.equal(hits(calls, /\/diff/), 1, 'the diff is one read shared by the sentence and the ranked tables');
  await tab(document, 'over_time');
  assert.equal(hits(calls, /survey_history|table_growth/), 2);
  assert.equal(hits(calls, /\/surveys/), 1);
  const total = calls.length;
  for (const t of ['glance', 'contents', 'over_time', 'glance', 'over_time']) await tab(document, t);
  assert.equal(hits(calls, /statistics_currency/), 1, 'the statistics read is shared by all three tabs');
  assert.equal(calls.length, total, 'switching between tabs that were read fetches nothing');
  assert.deepEqual(shown(document), ['over_time']);
});

test('repo: the report and the charts read only when their tab opens, once', async () => {
  const { document, calls } = await setUp('repo');
  await open(document);
  assert.equal(hits(calls, /\/egeria\/[^/]+\/diff/), 1);
  assert.equal(hits(calls, /survey-report|\/charts\//), 0);
  await tab(document, 'over_time');
  const series = hits(calls, /\/charts\//);
  assert.equal(series, 5, 'Over time probes only the five series');
  assert.equal(hits(calls, /charts\/(languages|file_types|top_committers)/), 0);
  await tab(document, 'contents');
  assert.equal(hits(calls, /charts\/(languages|file_types|top_committers)/), 3, 'Contents probes the three distribution charts');
  const charts = hits(calls, /\/charts\//);
  assert.equal(hits(calls, /survey-report/), 1);
  for (const t of ['glance', 'over_time', 'contents']) await tab(document, t);
  assert.equal(hits(calls, /survey-report/), 1);
  assert.equal(hits(calls, /\/charts\//), charts);
  assert.equal(hits(calls, /\/egeria\/[^/]+\/diff/), 1);
});

test('a tab named in the URL reopens: only that tab reads', async () => {
  const { document, calls, app } = await setUp('db');
  await open(document);
  // readUrl leaves exactly this state; the stage button re-runs loadPane without resetting the tab
  app.state.subTab = 'understanding_over_time';
  calls.length = 0;
  await open(document);
  assert.deepEqual(shown(document), ['over_time']);
  assert.equal(hits(calls, /survey_history|table_growth/), 2);
  assert.equal(hits(calls, /\/diff/), 0, 'At a glance is not read when it is not the tab shown');
  assert.equal(hits(calls, /schema_distribution|\/views/), 0);
  assert.ok(norm(document.querySelector('[data-subtab-rail] > span')) === 'Over time');
});

test('the section index reveals the tab that holds its target', async () => {
  const { document, window } = await setUp('db');
  window.HTMLElement.prototype.scrollIntoView = function () {};
  await open(document);
  document.querySelector('[data-section-index] button[data-goto="db-survey-history"]').click();
  await wait();
  assert.deepEqual(shown(document), ['over_time']);
  assert.ok(document.querySelector('[data-survey-history-body]').textContent.length > 0);
  assert.match(document.querySelector('[data-chart-chip="survey_history"]').textContent, /Structure over runs · measured/);
});

test('a chart on a tab that has not opened is "not read yet", never a count or a zero', async () => {
  const { document } = await setUp('db');
  await open(document);
  assert.match(document.querySelector('[data-chart-chip="column_types"]').textContent, /Column types · not read yet/);
  await tab(document, 'contents');
  assert.match(document.querySelector('[data-chart-chip="column_types"]').textContent, /Column types · measured/);
});

test('a shared tab on the Understanding rail is a real pane and the leading tabs stay on the rail', async () => {
  const { document, app } = await setUp('db');
  await open(document);
  document.querySelector('[data-subtab="questions"]').click();
  await wait(500);
  assert.equal(app.state.subTab, 'questions');
  assert.equal(document.querySelectorAll('[data-understanding-tab-panel]').length, 0);
  assert.deepEqual(railText(document).slice(0, 3), ['At a glance', 'Contents', 'Over time']);
  document.querySelector('[data-subtab="understanding_contents"]').click();
  await wait(500);
  assert.deepEqual(shown(document), ['contents']);
});

test('a file system keeps its current Understanding: one pane, no rail', async () => {
  const { document, app } = await setUp('filesystem');
  await open(document);
  const txt = norm(document.getElementById('content'));
  assert.match(txt, /Understanding has one pane/);
  assert.match(txt, /No charts for file systems yet/);
  assert.equal(document.querySelector('[data-subtab-rail]'), null);
  assert.equal(document.querySelectorAll('[data-understanding-tab-panel]').length, 0);
  assert.notEqual(app.state.subTab, 'understanding_glance');
});

test('Understanding tabs carry no counts, and no "catalog" word appears on the pane', async () => {
  const { document } = await setUp('db');
  await open(document);
  await tab(document, 'contents');
  assert.equal(document.querySelector('[data-tab-counts]'), null);
  assert.doesNotMatch(norm(document.getElementById('content')), /catalog/i);
});

test('db: the statistics line is on At a glance after the sentence; schema rollups on Contents; counts across surveys on Over time', async () => {
  const { document } = await setUp('db');
  await open(document);
  const g = panel(document, 'glance');
  const line = g.querySelector('[data-stats-line]');
  assert.match(norm(line), /^statistics · at survey 10-09 · counters reset 10-02 · 31 of 140 tables not analyzed in 30 days · 2 unknowable since reset ◐$/);
  assert.ok(g.querySelector('[data-since-last-run]').compareDocumentPosition(line) & 4, 'the line follows the sentence');
  assert.equal(panel(document, 'contents').querySelector('[data-stats-line]'), null);
  await tab(document, 'contents');
  const c = panel(document, 'contents');
  assert.match(norm(c.querySelector('[data-stats-schema="sales"]')), /sales · ◐ 3 of 40 never · oldest 41d/);
  assert.match(norm(c.querySelector('[data-stats-schema="scratch"]')), /scratch · ∅ no tables/);
  await tab(document, 'over_time');
  const t = panel(document, 'over_time');
  const rows = [...t.querySelectorAll('[data-stats-time-row]')].map(norm);
  assert.equal(rows.length, 3);
  assert.match(rows[0], /5 never\s*10 stale\s*0 reset\s*counters never reset/);
  assert.match(rows[1], /not collected/);
  assert.doesNotMatch(norm(document.body), /catalog/i);
});
