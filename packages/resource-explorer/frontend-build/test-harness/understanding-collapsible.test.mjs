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
const sections = (d) => [...d.querySelectorAll('#understanding-tabs-host [data-understanding-tab-panel] > details[data-collapsible]')];
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('every database section is collapsible, open by default, and the survey history is last', async () => {
  const { document } = await setUp();
  const ids = sections(document).map((d) => d.dataset.collapsible);
  // Page order across the three panels: At a glance, Contents (Now, Views), Over time (Over time, Survey history).
  assert.deepEqual(ids, ['db-since', 'db-now', 'db-views', 'db-time', 'db-survey-history']);
  for (const d of sections(document)) {
    assert.ok(d.open, `${d.dataset.collapsible} defaults open`);
    assert.match(norm(d.querySelector(':scope > summary [data-collapse-cue]')), /▾ hide/);
  }
  assert.ok(sections(document).at(-1).hasAttribute('data-survey-history'));
  assert.match(norm(sections(document).at(-1)), /Survey history/);
  assert.equal(document.querySelectorAll('#understanding-tabs-host h3').length, 5, 'one heading per section, none doubled');
});

test('folding a section changes its cue to "show" and is remembered in localStorage', async () => {
  const { document, window } = await setUp();
  const d = document.querySelector('details[data-collapsible="db-time"]');
  d.open = false;
  d.dispatchEvent(new window.Event('toggle'));
  assert.match(norm(d.querySelector(':scope > summary [data-collapse-cue]')), /▸ show/);
  assert.equal(window.localStorage.getItem('re.understanding.collapsed.db-time'), '1');
  d.open = true;
  d.dispatchEvent(new window.Event('toggle'));
  assert.equal(window.localStorage.getItem('re.understanding.collapsed.db-time'), null);
});

test('a remembered closed section is closed on the next visit', async () => {
  const { document, window } = await setUp();
  window.localStorage.setItem('re.understanding.collapsed.db-views', '1');
  document.querySelector('#intent-nav button[data-stage="scouting"]').click();
  await new Promise((r) => setTimeout(r, 200));
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await new Promise((r) => setTimeout(r, 500));
  await openAllUnderstandingTabs(document);
  const byId = Object.fromEntries(sections(document).map((d) => [d.dataset.collapsible, d]));
  assert.equal(byId['db-views'].open, false);
  assert.match(norm(byId['db-views'].querySelector(':scope > summary [data-collapse-cue]')), /▸ show/);
  assert.equal(byId['db-now'].open, true);
});

test('KNOWN-NEGATIVE: with localStorage throwing, the page still draws, every section open', async () => {
  const { document, window } = await setUp({ storage: () => { throw new Error('blocked'); } });
  assert.equal(sections(document).length, 5);
  assert.ok(sections(document).every((d) => d.open));
  const d = sections(document)[0];
  d.open = false;
  d.dispatchEvent(new window.Event('toggle'));   // must not throw
  assert.match(norm(d.querySelector(':scope > summary [data-collapse-cue]')), /show/);
});
