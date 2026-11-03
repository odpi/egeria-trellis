/** Behaviour: PI-053, the full survey report for a repository inside Understanding.
 *  Real app.js, understanding.js and repo-report.js; only fetch is stubbed in the shape of
 *  GET /api/egeria/{slug}/survey-report. Unread figures say "not read", never 0. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { openAllUnderstandingTabs } from './understanding-tabs-kit.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));

const REPORT = {
  slug: 'r1', display_name: 'R1', github_url: 'https://github.com/o/r1', primary_language: 'Python',
  health: { stars: 0, forks: 4, open_issues: 2, contributors: 3, license: 'MIT' }, health_read: true,
  file_types: [{ label: 'Python', count: 30, source: 'extension', extensions: { '.py': 30 } }, { label: 'Markdown', count: 10, source: 'extension', extensions: { '.md': 10 } }],
  total_files: 40, local_surveyed_at: '2026-10-01T00:00:00',
  dependencies: [{ ecosystem: 'pypi', count: 12, direct: 5 }],
  data_profiles: [{ file_path: 'data/a.csv', format: 'csv', row_count: 100, col_count: 3, file_size_bytes: 2048, columns: [] },
    { file_path: 'data/b.parquet', format: 'parquet', row_count: null, col_count: null, file_size_bytes: 0, columns: [] }],
  latest_survey: { surveyed_at: '2026-10-01T00:00:00', egeria_report_guid: 'g', published_at: '2026-10-01T01:00:00', annotation_count: 25 },
  has_egeria_annotations: true,
};

async function setUp(report, { fail = false } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.endsWith('/survey-report')) return fail ? { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'boom' }) } : ok(report);
    return ok({});
  };
  window.Plotly = { async newPlot() {}, async toImage() { return ''; }, purge() {} };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'r1', stage: 'scouting', subTab: 'questions', investigations: [], investigation: '', workListSlug: null, workListIndex: false,
    projects: [{ slug: 'r1', display_name: 'R1', github_url: 'https://github.com/o/r1', disposition: 'undecided' }], workingSet: new Set() });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await tick(400);
  await openAllUnderstandingTabs(document, 300);
  return { document, window, calls };
}

test('Understanding for a repo has a Survey report section with metrics, file types, data files and dependencies', async () => {
  const { document, calls } = await setUp(REPORT);
  assert.ok(calls.includes('/api/egeria/r1/survey-report'));
  const body = document.querySelector('[data-survey-report]');
  assert.ok(body);
  assert.equal(document.querySelector('[data-report-card="stars"] .tnum').textContent, '0', 'a read zero stays 0');
  assert.equal(document.querySelector('[data-report-card="stars"] [data-unread]'), null);
  assert.match(text(document.querySelector('[data-report-card="license"]')), /MIT/);
  assert.deepEqual([...document.querySelectorAll('[data-file-type]')].map((r) => r.dataset.fileType), ['Python', 'Markdown']);
  assert.equal(document.querySelector('[data-file-type="Python"] td:nth-child(3)').textContent, '75%');
  assert.deepEqual([...document.querySelectorAll('[data-data-file="data/a.csv"] td')].map((c) => c.textContent), ['data/a.csv', 'csv', '100', '3', '2 KB']);
  assert.match(text(document.querySelector('[data-data-file="data/b.parquet"]')), /not read/, 'an unprofiled file is not 0 rows');
  assert.deepEqual([...document.querySelectorAll('[data-ecosystem="pypi"] td')].map((c) => c.textContent), ['pypi', '12', '5']);
});

test('a repo with no stats row and no kept survey reads "not read", not 0', async () => {
  const { document } = await setUp({ ...REPORT, health: { stars: 0, forks: 0, open_issues: 0, contributors: 0, license: '' }, health_read: false,
    primary_language: '', local_surveyed_at: '', file_types: [], total_files: 0, dependencies: [], data_profiles: [], latest_survey: null });
  for (const k of ['stars', 'forks', 'open_issues', 'contributors', 'language', 'license', 'total_files']) {
    assert.match(text(document.querySelector(`[data-report-card="${k}"]`)), /not read/, k);
  }
  assert.ok(document.querySelector('[data-report-never]'));
  assert.ok(document.querySelector('[data-report-file-types="unread"]'));
  assert.match(text(document.querySelector('[data-report-dependencies="none"]')), /no dependencies recorded/);
});

test('surveyed with nothing found is a different statement from never surveyed', async () => {
  const { document } = await setUp({ ...REPORT, file_types: [], total_files: 0 });
  assert.match(text(document.querySelector('[data-report-file-types="none"]')), /surveyed: no file types recorded/);
});

test('a failed read says so and draws no report', async () => {
  const { document } = await setUp(REPORT, { fail: true });
  assert.match(text(document.querySelector('[data-report-error]')), /could not be read/);
  assert.equal(document.querySelector('[data-survey-report]'), null);
});

test('a NULL figure on a row that exists reads "not read", while a read zero stays 0', async () => {
  const { document } = await setUp({ ...REPORT, health: { stars: null, forks: 0, open_issues: null, contributors: null, license: 'MIT' }, health_read: true });
  assert.match(text(document.querySelector('[data-report-card="stars"]')), /not read/);
  assert.match(text(document.querySelector('[data-report-card="contributors"]')), /not read/);
  assert.equal(document.querySelector('[data-report-card="forks"] .tnum').textContent, '0');
  assert.equal(document.querySelector('[data-report-card="forks"] [data-unread]'), null);
});
