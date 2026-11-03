/** Slice C1 applied (PLAN-CARDS-AND-TILES.md section 3, rows 1 and 2): Understanding / At a glance and
 *  Analysis / Survey & analyses. Every tile opens what it counted; absent figures are states; a tile and the list it
 *  opens agree; one card per survey definition; the analyses list is ONE card. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

/* ── Survey & analyses ─────────────────────────────────────────────────── */

const def = (n, over = {}) => ({ qualified_name: `RE::def${n}`, display_name: `Definition ${n}`, survey_kind: 'discovery', description: 'Does a thing.',
  steps: [{}, {}], step_count: 2, last_run_at: '', ...over });
const ago = (days) => new Date(Date.now() - days * 86400000).toISOString();
const analysis = (n, over = {}) => ({ analysis_id: `a${n}`, name: `Analysis ${n}`, tier: 'discovery', last_run_at: ago(2), last_run_status: 'success',
  questions: [], serves: 'chat-only', cost: { basis: 'unknown' }, runnable: true, uses_credential: 'none', definitions: {}, ...over });

function stubFetch(routes) {
  globalThis.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const key = Object.keys(routes).find((k) => { const [m, sub] = k.split(' '); return m === method && String(url).includes(sub); });
    if (!key) throw new Error(`_UNSTUBBED_FETCH: ${method} ${url}`);
    const r = routes[key];
    if (r && r.__status) return { ok: false, status: r.__status, statusText: 'x', json: async () => r.body };
    return { ok: true, status: 200, json: async () => r };
  };
}

async function surveyPane({ defs, analyses, analysesFail = false }) {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  const scrolled = [];
  window.HTMLElement.prototype.scrollIntoView = function scroll() { scrolled.push(this); };
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'coco_pharma', stage: 'discovery', subTab: 'survey' });
  app.state.analysisRunFailures = new Map();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  stubFetch({
    'GET /api/survey-definitions/database/coco_pharma/candidates': { technology_type: 'PostgreSQL', scoping: 'questions', phase: 'discovery', egeria_native_processes: [], candidates: defs },
    'GET /api/survey-definitions/definitions': [],
    'GET /api/projects/coco_pharma/analyses-index': analysesFail ? { __status: 500, body: { detail: 'boom' } } : analyses,
    'GET /api/native-surveys/database/coco_pharma': { technology_type: 'x', surveys: [] },
  });
  await app.loadSurveyPane();
  await new Promise((r) => setTimeout(r, 30));
  return { content, scrolled, app, document };
}
const tileOf = (content, key) => content.querySelector(`[data-survey-tiles] [data-tile-key="${key}"]`);

test('Survey & analyses: "13 of 30", need you, and the last survey, each a tile that agrees with the list it opens', async () => {
  const rows = [...Array.from({ length: 13 }, (_, i) => analysis(i)),
    ...Array.from({ length: 17 }, (_, i) => analysis(100 + i, { last_run_at: '', last_run_status: '' }))];
  rows[0] = analysis(0, { last_run_status: 'failed' });
  rows[1] = analysis(1, { last_run_status: 'error' });
  const { content } = await surveyPane({
    defs: [def(1, { last_run_at: ago(5), last_run_status: 'ok' }), def(2, { last_run_at: ago(1), last_run_status: 'ok' })],
    analyses: { analyses: rows, counts: { never_run: 17, no_question: 30 } },
  });
  const run = tileOf(content, 'analyses_run');
  assert.equal(text(run.querySelector('[data-tile-figure]')), '● 13 of 30');
  // the same two figures the label above the list shows
  assert.match(text(content.querySelector('#analyses-index-section')), /Analyses · 30 · 17 not yet run/);
  const need = tileOf(content, 'need_you');
  assert.equal(need.dataset.tileState, 'count');
  assert.equal(text(need.querySelector('[data-tile-figure]')), '2');
  assert.equal(content.querySelectorAll('[data-analysis-row][data-needs-you]').length, 2, 'the tile counts exactly the rows it opens');
  const last = tileOf(content, 'last_survey');
  assert.match(text(last.querySelector('[data-tile-sub]')), /Definition 2/, 'the most recent of the definitions');
  assert.equal(content.querySelectorAll('[data-survey-tiles] [data-ui-tile]').length, 3);
});

test('Survey & analyses: every tile opens what it counted', async () => {
  const rows = [analysis(0, { last_run_status: 'failed' }), analysis(1)];
  const { content, scrolled } = await surveyPane({
    defs: [def(1, { last_run_at: ago(1), last_run_status: 'ok' })],
    analyses: { analyses: rows, counts: { never_run: 0, no_question: 2 } },
  });
  tileOf(content, 'analyses_run').click();
  assert.equal(scrolled.pop(), content.querySelector('[data-analyses-card]'), 'analyses run opens the analyses list');
  tileOf(content, 'need_you').click();
  assert.equal(scrolled.pop(), content.querySelector('[data-analysis-row="a0"]'), 'need you opens the first of those rows');
  tileOf(content, 'last_survey').click();
  assert.equal(scrolled.pop(), content.querySelector('[data-def-row="RE::def1"]'), 'last survey opens that survey definition');
});

test('Survey & analyses: a failed analyses read is "? not read" on both tiles, never 0 of 0', async () => {
  const { content } = await surveyPane({ defs: [def(1)], analyses: null, analysesFail: true });
  for (const key of ['analyses_run', 'need_you']) {
    const t = tileOf(content, key);
    assert.equal(t.dataset.tileState, 'not_read');
    assert.equal(text(t.querySelector('[data-tile-figure]')), '? not read');
  }
  assert.doesNotMatch(text(content.querySelector('[data-survey-tiles]')), /\b0\b/);
});

test('Survey & analyses: nothing run yet is "○ not measured · never run", and no failures is a measured "∅ 0"', async () => {
  const { content } = await surveyPane({
    defs: [def(1)], analyses: { analyses: [analysis(0, { last_run_at: '', last_run_status: '' })], counts: { never_run: 1, no_question: 1 } },
  });
  const last = tileOf(content, 'last_survey');
  assert.equal(last.dataset.tileState, 'not_measured');
  assert.match(text(last), /not measured/);
  assert.match(text(last.querySelector('[data-tile-sub]')), /not yet run/);
  assert.equal(text(tileOf(content, 'analyses_run').querySelector('[data-tile-figure]')), '● 0 of 1', 'a real 0 of 1, measured');
  assert.equal(tileOf(content, 'need_you').dataset.tileState, 'zero');
});

test('Survey & analyses: the server not saying how many never ran is not measured, not a number', async () => {
  const { content } = await surveyPane({ defs: [def(1)], analyses: { analyses: [analysis(0)], counts: {} } });
  assert.equal(tileOf(content, 'analyses_run').dataset.tileState, 'not_measured');
});

test('Survey & analyses: one card per survey definition, a failed run is the left edge, and the analyses are ONE card', async () => {
  const { content } = await surveyPane({
    defs: [def(1, { last_run_at: ago(1), last_run_status: 'ok' }), def(2, { last_run_at: ago(1), last_run_status: 'failed' }), def(3)],
    analyses: { analyses: Array.from({ length: 5 }, (_, i) => analysis(i)), counts: { never_run: 0, no_question: 5 } },
  });
  const defCards = [...content.querySelectorAll('[data-def-row]')];
  assert.equal(defCards.length, 3);
  assert.ok(defCards.every((c) => c.hasAttribute('data-ui-card')), 'each definition is a card');
  assert.deepEqual(defCards.map((c) => c.dataset.uiCardState), ['ordinary', 'needs_you', 'ordinary']);
  assert.equal(content.querySelectorAll('[data-analyses-card]').length, 1);
  const card = content.querySelector('[data-analyses-card]');
  assert.equal(card.querySelectorAll('[data-analysis-row]').length, 5, 'five rows in one card');
  assert.equal(card.querySelectorAll('[data-ui-card]').length, 0, 'never a card inside a card');
  assert.ok(card.querySelector('header [data-card-actions] [data-analyses-sort]'), 'sort controls at the top right of the card');
  // the section labels sit OUTSIDE the cards, above them
  const label = [...content.querySelectorAll('div')].find((d) => /^Analyses ·/.test(text(d)) && !d.closest('[data-ui-card]'));
  assert.ok(label && !card.contains(label));
});

test('Survey & analyses: no definition card sits inside another card', async () => {
  const { content } = await surveyPane({
    defs: [def(1), def(2)], analyses: { analyses: [analysis(0)], counts: { never_run: 0, no_question: 1 } },
  });
  for (const c of content.querySelectorAll('[data-ui-card]')) assert.equal(c.parentElement.closest('[data-ui-card]'), null);
});

/* ── Understanding / At a glance ───────────────────────────────────────── */

const RUN = { run_id: 7, surveyed_at: '2026-10-03T18:07:00', source: 'local', surveyed_as: 'surveyor' };
const env = (x = {}) => ({ database: 'adv', run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...x });
const DIST = env({ schemas: ['a', 'b', 'c'], table_counts: [5, 3, 2], column_counts: [20, 10, 8] });
const SIZES = env({ tables: ['a.big'], ranked: [{ name: 'a.big', row_count: 1200000, size_bytes: 9e6 }], row_counts: [1200000], sizes_mb: [9],
  totals: { tables: 10, rows: 1500000, rows_tables: 10, size_bytes: 3 * 1048576, size_tables: 8 }, views_excluded: 2 });
const TYPES = env({ types: ['int'], counts: [38], column_count_total: 38 });
const NO_ACTIVITY = env({ state: 'not_measured', reasons: ['no_table_activity'], currency: null, history: [] });

async function glance(over = {}) {
  const { document, window } = makeDomEnvironment();
  const scrolled = [];
  window.HTMLElement.prototype.scrollIntoView = function scroll() { scrolled.push(this); };
  const responses = { statistics_currency: NO_ACTIVITY, schema_distribution: DIST, table_sizes: SIZES, column_types: TYPES, ...over };
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    for (const k of Object.keys(responses)) if (u.includes(k)) {
      const r = responses[k];
      return r && r.__status ? { ok: false, status: r.__status, statusText: 'boom', json: async () => ({ detail: 'boom' }) } : ok(r);
    }
    if (/\/api\/stats\/databases\//.test(u)) return ok(env({ points: [], tables: [], types: [] }));
    if (/\/diff/.test(u)) return ok({ prev_date: '2026-09-21T10:00:00', curr_date: '2026-10-03T18:07:00', deltas: {}, new_tables: [], removed_tables: [], table_diff_state: 'measured' });
    if (u.includes('/views')) return ok({ state: 'measured', run: RUN, views: [] });
    if (u.includes('/surveys')) return ok([]);
    return ok({});
  };
  window.Plotly = { async newPlot() {}, purge() {}, async toImage() { return ''; } };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'adv', stage: 'scouting', subTab: 'questions', investigations: [], investigation: '', workListSlug: null, workListIndex: false });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await new Promise((r) => setTimeout(r, 600));
  return { document, scrolled };
}

test('At a glance (database): schemas, tables, rows, size and the last survey are tiles, and each opens what it counted', async () => {
  const { document, scrolled } = await glance();
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  const keys = [...g.querySelectorAll('[data-ui-tile]')].map((t) => t.dataset.tileKey);
  assert.deepEqual(keys, ['schemas', 'tables', 'rows', 'size', 'surveyed']);
  const target = (key) => { g.querySelector(`[data-tile-key="${key}"]`).click(); return scrolled.pop(); };
  const card = (kind) => document.querySelector(`[data-slot="${kind}"], [data-card-kind="${kind}"]`);
  const sizesCard = target('tables');
  assert.ok(sizesCard, 'a tile scrolled something into view');
  assert.equal(target('rows'), sizesCard);
  assert.equal(target('size'), sizesCard);
  assert.notEqual(target('schemas'), sizesCard, 'schemas opens the schema chart, not the table ranking');
  const history = target('surveyed');
  assert.equal(history.dataset.collapsible, 'db-survey-history', 'the last survey opens the survey history');
  assert.equal(document.querySelector('[data-understanding-tab-panel="over_time"]').hidden, false, 'on the tab that holds it');
  void card;
});

test('At a glance (database): the Contents tab opens when a tile for a chart there is pressed', async () => {
  const { document } = await glance();
  assert.equal(document.querySelector('[data-understanding-tab-panel="contents"]').hidden, true);
  document.querySelector('[data-understanding-tab-panel="glance"] [data-tile-key="tables"]').click();
  assert.equal(document.querySelector('[data-understanding-tab-panel="contents"]').hidden, false);
  assert.equal(document.querySelector('[data-understanding-tab-panel="glance"]').hidden, true);
});

test('At a glance (database): the tile figures are the ones the sentence figures used to show', async () => {
  const { document } = await glance();
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  const fig = (k) => text(g.querySelector(`[data-tile-key="${k}"] [data-tile-figure]`));
  assert.equal(fig('schemas'), '● 3');
  assert.equal(fig('tables'), '● 10');
  assert.equal(fig('rows'), '● ~1,500,000');
  assert.equal(fig('size'), '◐ ≥ 3.0 MB', 'summed over 8 of 10 tables: partial, a lower bound');
  assert.equal(g.querySelector('[data-tile-key="size"]').dataset.tileState, 'partial');
  assert.match(text(g.querySelector('[data-glance-fig="columns"]')), /38 columns/, 'columns stays a figure in the sentence');
});

test('At a glance (database): a read that failed is "? not read"; a read that answered not-measured is "○ not measured"; never 0', async () => {
  const { document } = await glance({ schema_distribution: { __status: 500 }, table_sizes: env({ state: 'not_measured', reasons: ['never_surveyed'], run: null }), column_types: TYPES });
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  const t = (k) => g.querySelector(`[data-tile-key="${k}"]`);
  assert.equal(t('schemas').dataset.tileState, 'not_read');
  assert.equal(text(t('schemas').querySelector('[data-tile-figure]')), '? not read');
  assert.equal(t('rows').dataset.tileState, 'not_measured');
  assert.equal(text(t('rows').querySelector('[data-tile-figure]')), '○ not measured');
  assert.doesNotMatch(text(g.querySelector('[data-ui-tile-row]')), /\b0\b/);
});

test('At a glance (database): a measured zero is "∅ 0"', async () => {
  const { document } = await glance({ schema_distribution: env({ schemas: [], table_counts: [], column_counts: [] }) });
  const s = document.querySelector('[data-understanding-tab-panel="glance"] [data-tile-key="schemas"]');
  assert.equal(s.dataset.tileState, 'zero');
  assert.equal(text(s.querySelector('[data-tile-figure]')), '∅ 0');
});

test('At a glance (repository): the file count and the survey date are tiles that open the Distribution and the Survey report', async () => {
  const { makeDomEnvironment: _unused } = await import('./dom-harness.mjs');
  void _unused;
  ensureLoaderRegistered();
  makeDomEnvironment();
  const g = await import('/static/next/stages/understanding-glance.js');
  const html = g.repoHeadlineHtml({ total_curr: 1200, curr_date: '2026-10-03T18:07:00' });
  const box = document.createElement('div'); box.innerHTML = html;
  const tiles = [...box.querySelectorAll('[data-ui-tile]')];
  assert.deepEqual(tiles.map((t) => [t.dataset.tileKey, t.dataset.tileOpens]), [['files', 'repo-distribution'], ['surveyed', 'repo-report']]);
  assert.equal(text(tiles[0].querySelector('[data-tile-figure]')), '● 1,200');
  assert.equal(g.repoHeadlineHtml({ __error: 'x' }), '', 'an unread diff draws no tile');
  assert.equal(box.querySelector('[data-tile-state="not_measured"]'), null);
  const zero = document.createElement('div'); zero.innerHTML = g.repoHeadlineHtml({ total_curr: 0 });
  assert.equal(zero.querySelector('[data-tile-key="files"]').dataset.tileState, 'zero');
});
