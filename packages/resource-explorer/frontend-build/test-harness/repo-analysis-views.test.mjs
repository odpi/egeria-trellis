/** Behaviour: repo_classification and dependency_support rebuilt on /next (owner 2026-10-21).
 *  Pure renderers against fixture rows, plus the real Understanding > Contents mount with only fetch stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { openAllUnderstandingTabs } from './understanding-tabs-kit.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));

const CLASSIFICATION = { findings: [
  { check_name: 'repo_role', label: 'tutorial', summary: 's', confidence: 'high',
    detail: { roles: ['tutorial', 'application', 'library'], notes: ['declined: <b>middleware</b>'] } },
  { check_name: 'architecture_recovery_gate', label: 'skip', summary: 'too small', detail: {} },
  { check_name: 'expected_readme', label: 'in-repo', summary: '', detail: { kind: 'readme', evidence: 'README.md', date: '' } },
  { check_name: 'expected_license', label: 'not-found', summary: '', detail: { kind: 'license' } },
  { check_name: 'expected_xss', label: 'sibling-repo', summary: '', detail: { kind: '<img src=x onerror=alert(1)>', evidence: '"quoted" & more' } },
  { check_name: 'size_band', label: 'medium', summary: 'about 400 files', detail: {} },
] };
const ENTRY = [{ id: 'repo_classification', name: 'Repo Classification', description: 'd' }, { id: 'dependency_support', name: 'Dependency Support', description: 'd' }];
const DEPS = {
  technologies: [
    { technology: 'PostgreSQL', dependencies: ['psycopg2', 'asyncpg'], egeria_technology_type: 'PostgreSQL Server', egeria_state: 'present' },
    { technology: '<Kafka>', dependencies: ['k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'k7', 'k8', 'k9', 'k10'], egeria_technology_type: null, egeria_state: 'absent' },
  ],
  coverage: { egeria_check: 'checked', summary: '2 technologies indicated', label: 'ok' },
  unmatched: ['left-pad', 'zzz<script>'], unmatched_count: 2, surveyed_at: '2026-10-20T10:00:00',
  message: 'A starting point.',
};

async function views() {
  makeDomEnvironment();
  ensureLoaderRegistered();
  return import('/static/next/stages/repo-analysis-views.js');
}

test('classification: ranked roles with secondaries, gate, expected artifacts by location, other findings', async () => {
  const v = await views();
  const { document } = makeDomEnvironment();
  document.body.innerHTML = v.repoClassificationHtml(CLASSIFICATION, ENTRY[0]);
  assert.deepEqual([...document.querySelectorAll('[data-role-rank]')].map((e) => [e.dataset.roleRank, e.textContent]), [['1', 'tutorial'], ['2', 'application'], ['3', 'library']]);
  assert.match(text(document.querySelector('[data-role-confidence]')), /high/);
  assert.match(text(document.querySelector('[data-analysis-head]')), /Repo Classification/);
  assert.equal(document.querySelector('[data-classification-gate]').dataset.classificationGate, 'skip');
  assert.match(text(document.querySelector('[data-classification-gate]')), /not applicable.*too small/);
  assert.deepEqual([...document.querySelectorAll('[data-expected]')].map((r) => r.dataset.location), ['in-repo', 'sibling-repo', 'not-found']);
  assert.match(text(document.querySelector('[data-finding="size_band"]')), /medium.*about 400 files/);
  assert.ok(document.querySelector('[data-classification-expected] thead th'), 'a header row, so colresize applies');
});

test('classification: escaping', async () => {
  const v = await views();
  const { document } = makeDomEnvironment();
  document.body.innerHTML = v.repoClassificationHtml(CLASSIFICATION, null);
  assert.equal(document.querySelector('img'), null);
  assert.equal(document.querySelector('[data-classification-notes] b'), null);
  assert.match(text(document.querySelector('[data-expected="<img src=x onerror=alert(1)>"]')), /"quoted" & more/);
});

test('classification: absent and error each say a sentence', async () => {
  const v = await views();
  const { document } = makeDomEnvironment();
  document.body.innerHTML = v.repoClassificationHtml({ findings: [] }, null);
  assert.equal(text(document.querySelector('[data-analysis-absent]')).includes(v.NO_CLASSIFICATION_SENTENCE), true);
  document.body.innerHTML = v.repoClassificationHtml({ findings: [], _status: { state: 'nothing_found', hint: 'Scanned, nothing found.' } }, null);
  assert.match(text(document.querySelector('[data-analysis-absent]')), /Scanned, nothing found/);
  document.body.innerHTML = v.repoClassificationHtml({ findings: [{ check_name: 'classification_error', label: 'error', summary: 'boom <i>x</i>' }, ...CLASSIFICATION.findings] }, null);
  assert.match(text(document.querySelector('[data-classification-error]')), /boom <i>x<\/i>/);
  assert.equal(document.querySelector('[data-classification-roles]'), null, 'the error replaces the verdict');
});

test('dependency support: check state leads, technologies, not-yet-classified count, escaping', async () => {
  const v = await views();
  const { document } = makeDomEnvironment();
  document.body.innerHTML = v.dependencySupportHtml(DEPS, ENTRY[1]);
  assert.equal(document.querySelector('[data-egeria-check]').dataset.egeriaCheck, 'checked');
  assert.match(text(document.querySelector('[data-egeria-check]')), /Egeria checked/);
  assert.ok(document.querySelector('[data-egeria-check]').compareDocumentPosition(document.querySelector('[data-depsupport-technologies]')) & 4, 'check state comes first');
  assert.match(text(document.querySelector('[data-analysis-head]')), /as of 2026-10-20 10:00/);
  assert.equal(document.querySelector('[data-technology="PostgreSQL"]').dataset.egeriaState, 'present');
  assert.match(text(document.querySelector('[data-technology="<Kafka>"]')), /not in Egeria.*\+2 more/);
  const un = document.querySelector('[data-depsupport-unmatched]');
  assert.equal(un.dataset.depsupportUnmatched, '2');
  assert.match(text(un.querySelector('summary')), /2 dependencies not yet classified/);
  assert.doesNotMatch(text(document.body), /unsupported/i);
  assert.equal(document.querySelector('script'), null);
  assert.match(text(document.querySelector('[data-depsupport-unmatched-list]')), /zzz<script>/);
});

test('dependency support: unreachable, skipped, singular count, could-not-derive and none', async () => {
  const v = await views();
  const { document } = makeDomEnvironment();
  const draw = (o) => { document.body.innerHTML = v.dependencySupportHtml({ ...DEPS, ...o }, null); };
  draw({ coverage: { egeria_check: 'unreachable', egeria_check_detail: 'timed out', summary: 's' } });
  assert.match(text(document.querySelector('[data-egeria-check="unreachable"]')), /could not be checked/);
  draw({ coverage: { summary: 's' } });
  assert.match(text(document.querySelector('[data-egeria-check="skipped"]')), /not consulted/);
  draw({ unmatched: ['one'] });
  assert.match(text(document.querySelector('summary')), /1 dependency not yet classified/);
  draw({ unmatched: null });
  assert.match(text(document.querySelector('[data-depsupport-unmatched="unknown"]')), /could not be derived/);
  draw({ unmatched: [] });
  assert.match(text(document.querySelector('[data-depsupport-unmatched="0"]')), /Every dependency matched/);
  draw({ technologies: [] });
  assert.match(text(document.querySelector('[data-depsupport-technologies="none"]')), /No dependency matched/);
  draw({ coverage: { label: 'no-dependencies' } });
  assert.equal(text(document.querySelector('[data-dependency-none]')).includes(v.NO_DEPENDENCIES_SENTENCE), true);
  assert.equal(document.querySelector('[data-depsupport-technologies]'), null);
  document.body.innerHTML = v.dependencySupportHtml({ _status: { state: 'never_run', hint: 'No dependency-support assessment yet — run the analysis.' } }, null);
  assert.match(text(document.querySelector('[data-analysis-absent]')), /run the analysis/);
});

test('catalogue fields: an absent field reads empty, a declared one is shown', async () => {
  const v = await views();
  assert.deepEqual(v.catalogueFields(null, {}), { name: '', description: '', unit: '', asOf: '', confidenceBasis: '', absence: '' });
  const { document } = makeDomEnvironment();
  document.body.innerHTML = v.dependencySupportHtml({ ...DEPS, unmatched: [] }, { id: 'dependency_support', name: 'N', unit: 'technologies', absence_sentence: 'unused' });
  assert.match(text(document.querySelector('[data-analysis-head]')), /N .* technologies/);
});

async function setUp(routes) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const hit = Object.keys(routes).find((k) => u.endsWith(k));
    if (hit && routes[hit] instanceof Error) return { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'boom' }) };
    return { ok: true, status: 200, json: async () => (hit ? routes[hit] : {}) };
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
  await tick(200);
  return { document, calls, app };
}

test('Understanding > Contents mounts both views from the same routes Classic read', async () => {
  const { document, calls } = await setUp({
    '/analyses/repo_classification/results': CLASSIFICATION, '/analyses/dependency_support/results': DEPS, '/api/analyses/repo': ENTRY });
  assert.ok(calls.includes('/api/projects/r1/analyses/repo_classification/results'));
  assert.ok(calls.includes('/api/projects/r1/analyses/dependency_support/results'));
  assert.ok(document.querySelector('[data-repo-analysis="repo_classification"] [data-repo-classification="rows"]'));
  assert.ok(document.querySelector('[data-repo-analysis="dependency_support"] [data-dependency-support="rows"]'));
  assert.match(text(document.querySelector('[data-analysis-head="repo_classification"]')), /Repo Classification/);
});

test('Understanding: a failed read says so rather than drawing an empty view', async () => {
  const { document } = await setUp({ '/analyses/repo_classification/results': new Error('x'), '/analyses/dependency_support/results': { coverage: { label: 'no-dependencies' } } });
  assert.match(text(document.querySelector('[data-analysis-error="repo_classification"]')), /could not be read/);
  assert.match(text(document.querySelector('[data-dependency-none]')), /run manifest_parse first/);
});
