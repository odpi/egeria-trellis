/** W69: the Q-0043 answer row ("What kind of thing is this repository") says the ROLE, not the word "Role".
 *  Live (deep_causality, 2026-11-05): "Answered by: repo_classification Role" — the stored repo_role finding carries
 *  label "Role" (the fixed word for what the finding IS) and the role words in summary / detail.roles. The headline
 *  the server now builds leads with the role words ("samples (also library) · architecture recovery: run"); the row
 *  shows its first sentence and the W63 kind line still follows it. Understanding's own role line reads the roles the
 *  same way, never the label. Real app.js and repo-analysis-views.js; fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SLUG = 'deep_causality';
const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();

//: deep_causality's stored finding shape (2026-11-05).
const LIVE_ROLE = { check_name: 'repo_role', label: 'Role', summary: 'samples (also library) — architecture recovery: run',
  confidence: 60, detail: { roles: ['samples', 'library'], notes: [] } };
const GATE = { check_name: 'architecture_recovery_gate', label: 'run', summary: 'enough structure', confidence: 60,
  detail: { primary_role: 'samples', roles: ['samples', 'library'] } };
const DOI_SENTENCE = 'a DOI names it (10.5281/zenodo.13750369) · whether it is a dataset is not established until the DOI is resolved (Import resolves it)';
const DATASET_KIND = { check_name: 'dataset_kind', label: 'DOI only', summary: DOI_SENTENCE, confidence: 60,
  detail: { state: 'doi_only', descriptors: [{ kind: 'readme-doi', path: 'README.md', identifier: '10.5281/zenodo.13750369',
    identifier_kind: 'doi', doi: '10.5281/zenodo.13750369' }], descriptors_read_at: '2026-11-03T09:12:00',
  data_files: 0, price: 'one DataCite call · a few seconds', help: 'Registers it as open data from its DOI.',
  can_import: true, no_identifier: 'No descriptor carries an identifier.' } };
const ROLE_NOT_DETERMINED = 'role: not determined (classification ran without a role verdict)';
// What the server's `_repo_classification_headline` now writes for each shape (tests/test_repo_classification_headline_w69.py).
const fact = (findings, headline) => ({ analysis_id: 'repo_classification', state: 'measured', is_known: true,
  headline, value: { findings }, last_run_at: '2026-11-04T10:00:00', as_of: '',
  can_run: ['repo_classification'], note: '', runnable: true, runnable_reason: '', library: false, numbers_at_level: false });
const Q0043 = { question: 'What kind of thing is this repository — a library, an application, a tool, or samples?',
  id: 'Q-0043', kind: 'analysis', stage: 'Discovery', levels: ['resource'], analysis_ids: ['repo_classification'],
  answering_mechanism: 'Code Analysis', perspectives: ['Consumer'] };
const env = (facts) => ({ answerable: true, facts, caveat: '', sources: ['repo_classification'] });

async function setUp(facts) {
  const { document, window } = makeDomEnvironment();
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: SLUG, stage: 'discovery', subTab: 'questions',
    investigations: [], investigation: '', workingSet: new Set(), me: { user_id: 'me' } });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  }
  const row = document.createElement('div');
  row.id = 'qrow-0';
  document.getElementById('content').appendChild(row);
  row.innerHTML = app.rowInner(Q0043, 0, env(facts));
  app.bindRowActions(row, Q0043, 0);
  return { document, app, row };
}

test('W69 the Q-0043 row reads the role words, then the W63 kind line; never the word "Role"', async () => {
  const { row } = await setUp([fact([LIVE_ROLE, GATE, DATASET_KIND], 'samples (also library) · architecture recovery: run')]);
  assert.match(text(row), /Answered by: repo_classification samples \(also library\) Kind /);
  assert.doesNotMatch(text(row), /repo_classification Role\b/);
  const kind = row.querySelector('[data-question-dataset-kind] [data-dataset-kind]');
  assert.equal(kind.dataset.datasetKind, 'doi_only', 'the W63 kind line is unchanged');
  assert.equal(text(kind.querySelector('[data-dataset-sentence]')), DOI_SENTENCE);
});

test('W69 a classification with no role verdict reads the absence words, never "Role" or nothing', async () => {
  const noRole = { ...LIVE_ROLE, summary: '', detail: { roles: [], notes: [] } };
  const { row } = await setUp([fact([noRole, GATE, DATASET_KIND], `${ROLE_NOT_DETERMINED} · architecture recovery: run`)]);
  assert.match(text(row), /Answered by: repo_classification role: not determined \(classification ran without a role verdict\) Kind /);
  assert.doesNotMatch(text(row), /repo_classification Role\b/);
});

test('W69 Understanding\'s role line reads detail.roles, else the summary words, else the absence words — never the label', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const v = await import(`/static/next/stages/repo-analysis-views.js?t=${Math.random()}`);
  const words = (html) => String(html).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const said = (findings) => words(v.repoClassificationHtml({ findings, surveyed_at: '2026-11-04T10:00:00' }));
  const ranked = said([LIVE_ROLE, GATE]);
  assert.match(ranked, /Role samples library/, 'the ranked roles, primary first');
  const bySummary = said([{ ...LIVE_ROLE, detail: { notes: [] } }, GATE]);
  assert.match(bySummary, /Role samples \(also library\)/);
  assert.doesNotMatch(bySummary, /Role Role/);
  const none = said([{ ...LIVE_ROLE, summary: '', detail: { roles: [], notes: [] } }, GATE]);
  assert.match(none, new RegExp(ROLE_NOT_DETERMINED.replace(/[()]/g, '\\$&')));
  assert.doesNotMatch(none, /Role Role/);
  assert.equal(v.roleSummaryWords({ label: 'Role', summary: 'tool — architecture recovery: skip' }), 'tool');
  assert.equal(v.roleSummaryWords({ label: 'Role', summary: '' }), v.ROLE_NOT_DETERMINED_WORDS);
});
