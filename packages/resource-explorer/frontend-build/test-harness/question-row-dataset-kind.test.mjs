/** W63: the dataset sentence and its Import press live on the Q-0043 answer row ("What kind of thing is this
 *  repository"), drawn from the stored dataset_kind finding — the same finding and the same datasetKindHtml the
 *  Understanding copy uses. The fixture is deep_causality's stored finding shape (2026-11-04): a README DOI and no
 *  descriptor naming a dataset, so the state is doi_only. Real app.js; fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SLUG = 'deep_causality';
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();

const DOI_SENTENCE = 'a DOI names it (10.5281/zenodo.13750369) · whether it is a dataset is not established until the DOI is resolved (Import resolves it)';
const DATASET_KIND = { check_name: 'dataset_kind', label: 'DOI only', summary: DOI_SENTENCE, confidence: 60,
  detail: { state: 'doi_only', descriptors: [{ kind: 'readme-doi', path: 'README.md', identifier: '10.5281/zenodo.13750369',
    identifier_kind: 'doi', doi: '10.5281/zenodo.13750369' }], descriptors_read_at: '2026-11-03T09:12:00',
  data_files: 0, price: 'one DataCite call · a few seconds', help: 'Registers it as open data from its DOI.',
  can_import: true, no_identifier: 'No descriptor carries an identifier.' } };
const ROLE = { check_name: 'repo_role', label: 'library', summary: 'library', confidence: 80, detail: { roles: ['library'], notes: [] } };
const fact = (findings, over = {}) => ({ analysis_id: 'repo_classification', state: 'measured', is_known: true,
  headline: 'library · architecture recovery: run', value: { findings }, last_run_at: '2026-11-04T10:00:00', as_of: '',
  can_run: ['repo_classification'], note: '', runnable: true, runnable_reason: '', library: false, numbers_at_level: false, ...over });
const Q0043 = { question: 'What kind of thing is this repository — a library, an application, a tool, or samples?',
  id: 'Q-0043', kind: 'analysis', stage: 'Discovery', levels: ['resource'], analysis_ids: ['repo_classification'],
  answering_mechanism: 'Code Analysis', perspectives: ['Consumer'] };
const env = (facts) => ({ answerable: true, facts, caveat: '', sources: ['repo_classification'] });

async function setUp(facts) {
  const { document, window } = makeDomEnvironment();
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  const posts = [];
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.includes('/api/open-data/import-from-repository') || (init.method === 'POST' && u.includes('import'))) {
      posts.push({ url: u, body: JSON.parse(init.body || '{}') });
      return { ok: true, status: 200, json: async () => ({ outcome: 'imported', words: 'imported · registered as open data from its DOI' }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
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
  return { document, app, row, posts };
}

test('Q-0043 row: the DOI-only sentence, when the descriptors were read, and the Import press with its price are on the answer row', async () => {
  const { row } = await setUp([fact([ROLE, DATASET_KIND])]);
  assert.match(text(row), /Answered by: repo_classification library Kind /, 'the role answer stays, the kind follows it');
  const kind = row.querySelector('[data-question-dataset-kind] [data-dataset-kind]');
  assert.ok(kind, 'the kind block is on the row itself');
  assert.equal(kind.dataset.datasetKind, 'doi_only');
  assert.equal(text(kind.querySelector('[data-dataset-sentence]')), DOI_SENTENCE);
  assert.match(text(kind.querySelector('[data-dataset-read-at]')), /^descriptors read /);
  const press = kind.querySelector('[data-dataset-import]');
  assert.ok(press && !press.hasAttribute('disabled'), 'a DOI-only repository keeps the press');
  assert.equal(press.getAttribute('title'), 'Registers it as open data from its DOI.');
  assert.equal(text(kind.querySelector('[data-dataset-import-price]')), 'one DataCite call · a few seconds');
});

test('Q-0043 row: the press posts the repo slug once and shows the answer in words', async () => {
  const { row, posts } = await setUp([fact([ROLE, DATASET_KIND])]);
  row.querySelector('[data-dataset-import]').click();
  await tick();
  assert.equal(posts.length, 1, 'one POST');
  assert.deepEqual(posts[0].body, { repo_slug: SLUG, descriptor_path: '' });
  assert.equal(text(row.querySelector('[data-dataset-import-result]')), 'imported · registered as open data from its DOI');
});

test('Q-0043 row: a classification with no dataset_kind finding says "not checked" (W59), never nothing; an unrun row draws no kind', async () => {
  const { row, app } = await setUp([fact([ROLE])]);
  const kind = row.querySelector('[data-question-dataset-kind] [data-dataset-kind]');
  assert.equal(kind.dataset.datasetKind, 'not_checked');
  assert.match(text(kind), /dataset signal: not checked \(classification ran before descriptors were read\) · re-run reads it/);
  assert.equal(row.querySelector('[data-dataset-import]'), null);
  assert.equal(app.datasetKindRowHtml(Q0043, env([fact([ROLE], { is_known: false, state: 'never_run' })])), '');
  assert.equal(app.datasetKindRowHtml({ ...Q0043, analysis_ids: ['license_classification'] }, env([fact([ROLE, DATASET_KIND])])), '',
    'only the row that repo_classification answers');
});

test('Q-0043 row: a dataset repository shows its sentence and the press; a repository with data files only shows the sentence alone', async () => {
  const dataset = { ...DATASET_KIND, label: 'dataset repository',
    summary: 'a dataset repository · identity DOI 10.5281/zenodo.1 · licence CC-BY-4.0 (from datapackage.json)',
    detail: { ...DATASET_KIND.detail, state: 'dataset' } };
  const { row } = await setUp([fact([ROLE, dataset])]);
  assert.equal(row.querySelector('[data-dataset-kind]').dataset.datasetKind, 'dataset');
  assert.ok(row.querySelector('[data-dataset-import]'));
  const files = { ...DATASET_KIND, label: 'data files only',
    summary: 'a repository with data files, not an open dataset by Resource Explorer’s rule (no descriptor names it)',
    detail: { ...DATASET_KIND.detail, state: 'data_files', can_import: false } };
  const second = await setUp([fact([ROLE, files])]);
  assert.equal(second.row.querySelector('[data-dataset-kind]').dataset.datasetKind, 'data_files');
  assert.equal(second.row.querySelector('[data-dataset-import]'), null, 'no press without a descriptor');
  assert.match(text(second.row.querySelector('[data-dataset-sentence]')), /^a repository with data files, not an open dataset/);
});
