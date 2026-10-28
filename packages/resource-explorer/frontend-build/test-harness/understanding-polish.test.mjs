/** Understanding polish: the At a glance headline (figures from reads already made), the index bar and chart
 *  chips off At a glance, and the "not measured" reason on the statistics line. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const RUN = { run_id: 7, surveyed_at: '2026-10-03T18:07:00', source: 'local', surveyed_as: 'surveyor' };
const env = (x = {}) => ({ database: 'adv', run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...x });
const DIST = env({ schemas: ['a', 'b', 'c'], table_counts: [5, 3, 2], column_counts: [20, 10, 8] });
const SIZES = env({ tables: ['a.big'], ranked: [{ name: 'a.big', row_count: 1200000, size_bytes: 9e6 }], row_counts: [1200000], sizes_mb: [9],
  totals: { tables: 10, rows: 1500000, rows_tables: 10, size_bytes: 3 * 1048576, size_tables: 8 }, views_excluded: 2 });
const TYPES = env({ types: ['int'], counts: [38], column_count_total: 38 });
const NO_ACTIVITY = env({ state: 'not_measured', reasons: ['no_table_activity'], currency: null, history: [],
  scope: { checked: true, partial: true, fraction: '3 of 61 relation(s) in 6 of 8 schemas', schema_fraction: 'sees 6 of 8 schemas, SELECT on 3 of 61 relations' } });

function stub() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/statistics_currency/.test(u)) return ok(NO_ACTIVITY);
    if (/schema_distribution/.test(u)) return ok(DIST);
    if (/table_sizes/.test(u)) return ok(SIZES);
    if (/column_types/.test(u)) return ok(TYPES);
    if (/\/api\/stats\/databases\//.test(u)) return ok(env({ points: [], tables: [], types: [] }));
    if (/\/diff/.test(u)) return ok({ prev_date: '2026-09-21T10:00:00', curr_date: '2026-10-03T18:07:00', deltas: {}, new_tables: [], removed_tables: [], table_diff_state: 'measured' });
    if (u.includes('/views')) return ok({ state: 'measured', run: RUN, views: [] });
    if (u.includes('/surveys')) return ok([]);
    return ok({});
  };
}

async function setUp() {
  const { document, window } = makeDomEnvironment();
  stub();
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
  return document;
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('At a glance: the headline figures, each with a cue and a short word, from the reads already made', async () => {
  const document = await setUp();
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  const fig = (n) => g.querySelector(`[data-glance-fig="${n}"]`);
  assert.match(text(fig('schemas')), /● 3 schemas/);
  assert.match(text(fig('tables')), /● 10 tables/);
  assert.match(text(fig('columns')), /● 38 columns/);
  assert.match(text(fig('rows')), /● ~1,500,000 rows/);
  assert.match(text(fig('size')), /◐ ≥ 3\.0 MB size/, 'size summed over 8 of 10 tables is a lower bound, marked partial');
  assert.match(text(fig('largest')), /a\.big largest · 1,200,000 rows/);
  assert.match(text(fig('surveyed')), /10-03 18:07 surveyed · RE's own survey, as surveyor/);
  // order: the sentence first, then the headline, then the statistics line
  const kids = [...g.children].map((c) => (c.matches('details') ? 'since' : c.hasAttribute('data-glance-slot') ? 'headline' : c.hasAttribute('data-stats-glance') ? 'stats' : '?'));
  assert.deepEqual(kids, ['since', 'headline', 'stats']);
});

test('At a glance has no section index bar and no chart chips', async () => {
  const document = await setUp();
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  assert.equal(g.querySelector('[data-section-index], [data-chart-index], [data-chart-chip], [data-index-bar]'), null);
  assert.ok(document.querySelector('[data-understanding-tab-panel="contents"] [data-section-index]'), 'Contents has its own');
  assert.ok(document.querySelector('[data-understanding-tab-panel="over_time"] [data-section-index]'), 'Over time has its own');
});

test('"not measured" says why, only from what the stored rows show', async () => {
  const document = await setUp();
  const line = text(document.querySelector('[data-stats-not-measured]'));
  assert.match(line, /not measured · no table activity/);
  assert.match(line, /the survey of 10-03 \(RE's own\) stored no table activity/);
  assert.match(line, /latest credential probe: sees 6 of 8 schemas, SELECT on 3 of 61 relations/);
  const m = await import('/static/next/stages/understanding-stats.js');
  const bare = text({ textContent: m.statsLineHtml({ state: 'not_measured', reasons: ['no_table_activity'], currency: null }).replace(/<[^>]+>/g, '') });
  assert.doesNotMatch(bare, /credential/, 'no probe on record: no cause is named');
});

test('glance headline: an unmeasured figure is a dash with an open cue, never zero', async () => {
  ensureLoaderRegistered();
  makeDomEnvironment();
  const m = await import('/static/next/stages/understanding-glance.js');
  const none = { state: 'not_measured', reasons: ['never_surveyed'], run: null };
  const h = m.databaseHeadlineHtml({ dist: none, sizes: none, types: none });
  assert.doesNotMatch(h, />0</);
  assert.match(h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' '), /○ – rows/);
  assert.match(h.replace(/<[^>]+>/g, ' '), /never surveyed/);
  assert.equal(m.surveyedByWords({ source: 'egeria-survey' }), "Egeria's survey");
});

test('the not-measured reason calls the scope the latest probe, with the probe note on hover', async () => {
  ensureLoaderRegistered(); makeDomEnvironment();
  const m = await import('/static/next/stages/understanding-stats.js');
  const h = m.statsLineHtml({ ...NO_ACTIVITY, notes: ['n'], scope: { ...NO_ACTIVITY.scope, probe_note: 'may not be this run' } });
  assert.match(h.replace(/<[^>]+>/g, ' '), /latest credential probe: sees 6 of 8 schemas/);
  assert.doesNotMatch(h, /credential scope:/);
  assert.match(h, /title="may not be this run · n"/);
});

test('glance: a complete tables or columns count is measured even when the read is partial; a placeholder shows until it resolves', async () => {
  ensureLoaderRegistered(); makeDomEnvironment();
  const g = await import('/static/next/stages/understanding-glance.js');
  const h = g.databaseHeadlineHtml({ dist: { ...DIST, state: 'partial' }, sizes: { ...SIZES, state: 'partial' }, types: TYPES });
  const st = (n) => h.match(new RegExp(`data-glance-fig="${n}" data-glance-state="(\\w+)"`))[1];
  assert.equal(st('tables'), 'measured'); assert.equal(st('columns'), 'measured');
  const document = await setUp();
  assert.ok(document.querySelector('[data-glance-slot]'));
});
