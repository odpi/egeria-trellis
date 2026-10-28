/** Understanding (owner feedback 2026-10-07, item 11): every section folds, survey history comes last,
 *  and each section's open/closed state is remembered per viewer in localStorage (always in try/catch). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { openAllUnderstandingTabs } from './understanding-tabs-kit.mjs';

const RUN = { run_id: 7, surveyed_at: '2026-09-28T14:02:00', source: 'local', surveyed_as: 'u' };
const env = (x = {}) => ({ database: 'db1', run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...x });
const SURVEYS = [{ surveyed_at: '2026-09-28T14:02:00', schema_count: 2, table_count: 3, column_count: 9, source: 'local', invalid_at: null }];

function stub() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/\/api\/stats\/databases\//.test(u)) return ok(env({ schemas: [], table_counts: [], column_counts: [], tables: [], row_counts: [], sizes_mb: [], types: [], counts: [], points: [], runs: [], series: [], schema_scope: {} }));
    if (u.includes('/surveys')) return ok(SURVEYS);
    if (u.includes('/views')) return ok({ state: 'measured', run: RUN, views: [] });
    if (u.includes('/diff')) return ok({});
    return ok({});
  };
}

async function setUp({ storage } = {}) {
  const { document, window } = makeDomEnvironment();
  stub();
  if (storage !== undefined) Object.defineProperty(window, 'localStorage', { configurable: true, get: storage });
  window.Plotly = { async newPlot() {}, async toImage() { return ''; }, purge() {} };
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => { const r = append(el); if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0); return r; };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'db1', stage: 'scouting', subTab: 'questions', investigations: [], investigation: '', workListSlug: null, workListIndex: false });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await new Promise((r) => setTimeout(r, 500));
  await openAllUnderstandingTabs(document);
  return { document, window };
}
const chips = (d) => [...d.querySelectorAll('[data-chart-chip]')];
const sectionBtns = (d) => [...d.querySelectorAll('[data-section-index] button[data-goto]')];
function spy(window) {
  const calls = [];
  window.HTMLElement.prototype.scrollIntoView = function () { calls.push(this); };
  return calls;
}
const press = async (el) => { el.click(); await new Promise((r) => setTimeout(r, 20)); };

test('a wired chart chip is a button; pressing it scrolls its card into view and marks it pressed', async () => {
  const { document, window } = await setUp();
  const calls = spy(window);
  const chip = document.querySelector('[data-chart-chip="column_types"]');
  assert.equal(chip.tagName, 'BUTTON');
  assert.match(chip.textContent, /Column types · /, 'the state word stays on the chip');
  await press(chip);
  assert.equal(calls.length, 1);
  assert.equal(calls[0], document.querySelector('#understanding-tabs-host [data-chart="column_types"]'));
  assert.equal(chip.getAttribute('aria-pressed'), 'true');
  assert.equal(chips(document).filter((c) => c.getAttribute('aria-pressed') === 'true').length, 1);
});

test('pressing a chip whose section is folded opens the section first', async () => {
  const { document, window } = await setUp();
  const calls = spy(window);
  const sec = document.querySelector('details[data-collapsible="db-time"]');
  sec.open = false;
  sec.dispatchEvent(new window.Event('toggle'));
  assert.equal(window.localStorage.getItem('re.understanding.collapsed.db-time'), '1');
  await press(document.querySelector('[data-chart-chip="survey_history"]'));
  assert.ok(sec.open, 'section opened');
  assert.equal(window.localStorage.getItem('re.understanding.collapsed.db-time'), null);
  assert.equal(calls[0], document.querySelector('#understanding-tabs-host [data-chart="survey_history"]'));
});

test('a chart that is not wired up is muted text, not a control', async () => {
  const { document } = await setUp();
  const el = document.querySelector('[data-chart-chip="table_activity"]');
  assert.notEqual(el.tagName, 'BUTTON');
  assert.ok(!el.className.includes('border '), 'no border: it must not look like a control');
  assert.equal(el.getAttribute('tabindex'), null);
  assert.match(el.textContent, /Activity per table · not wired up yet/);
  assert.ok(el.getAttribute('title'), 'the sentence is on demand');
});

test('a section index sits at the top in page order and scrolls to each section, opening a folded one', async () => {
  const { document, window } = await setUp();
  const calls = spy(window);
  const btns = sectionBtns(document);
  // The index lives with what it indexes: Contents holds Now and Views, Over time the series and the history;
  // At a glance has no index bar and no chart chips.
  assert.deepEqual(btns.map((b) => b.dataset.goto), ['db-now', 'db-views', 'db-time', 'db-survey-history']);
  assert.deepEqual(btns.map((b) => b.textContent.trim()), ['Now', 'Views', 'Over time', 'Survey history']);
  const g = document.querySelector('[data-understanding-tab-panel="glance"]');
  assert.equal(g.querySelector('[data-section-index], [data-chart-index], [data-chart-chip]'), null, 'no index bar on At a glance');
  const inPanel = (id) => [...document.querySelectorAll(`[data-understanding-tab-panel="${id}"] [data-chart-chip]`)].map((c) => c.dataset.chartChip);
  assert.deepEqual(inPanel('contents'), ['schema_distribution', 'table_sizes', 'column_types']);
  assert.deepEqual(inPanel('over_time'), ['survey_history', 'table_growth', 'table_activity']);
  const sec = document.querySelector('details[data-collapsible="db-views"]');
  sec.open = false;
  sec.dispatchEvent(new window.Event('toggle'));
  await press(btns[1]);
  assert.ok(sec.open);
  assert.equal(calls.at(-1), sec);
  assert.equal(btns[1].getAttribute('aria-pressed'), 'true');
  assert.equal(document.querySelectorAll('#understanding-tabs-host h3').length, 5, 'no heading doubled');
});

test('with localStorage throwing, pressing a chip still opens and scrolls', async () => {
  const { document, window } = await setUp({ storage: () => { throw new Error('blocked'); } });
  const calls = spy(window);
  const sec = document.querySelector('details[data-collapsible="db-now"]');
  sec.open = false;
  await press(document.querySelector('[data-chart-chip="table_sizes"]'));
  assert.ok(sec.open);
  assert.equal(calls.length, 1);
});
