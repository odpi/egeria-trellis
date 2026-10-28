/** DESIGN-BLUEPRINT-NODE-ADMISSION.md: the class on each node, the left-out lines, the reclassify box. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  ensureLoaderRegistered();
  const { document } = makeDomEnvironment();
  const curate = await import(`/static/next/stages/curate.js?t=${Math.random()}`);
  const adm = await import('/static/next/stages/curate-admission.js');
  return { curate, adm, document };
}

const LEAF = { path: 'docker::ui', name: 'ui', type: 'Third Party Process', confidence: 75, proposals: [],
  ports: [], verdict: null, admission: 'shipped_here', admission_evidence: 'image odpi/egeria-ui · published by ci.yml:4' };

test('each node says its class with a cue and a short word, the evidence beside it', async () => {
  const { curate, document } = await load();
  const box = document.createElement('div');
  box.innerHTML = curate.leafRowHtml(LEAF);
  const a = box.querySelector('[data-admission]');
  assert.equal(a.dataset.admission, 'shipped_here');
  assert.match(a.textContent.replace(/\s+/g, ' '), /▲ shipped here · image odpi\/egeria-ui · published by ci.yml:4/);
  box.innerHTML = curate.leafRowHtml({ ...LEAF, admission: 'built_here', admission_evidence: 'from docker/platform/Dockerfile' });
  assert.match(box.querySelector('[data-admission]').textContent.replace(/\s+/g, ' '), /◼ built here · from docker\/platform\/Dockerfile/);
});

test('a reclassified node shows who moved it and why', async () => {
  const { curate, document } = await load();
  const box = document.createElement('div');
  box.innerHTML = curate.leafRowHtml({ ...LEAF, reclassified: { to: 'built_here', by: 'dan', reason: 'we maintain it' } });
  assert.match(box.textContent.replace(/\s+/g, ' '), /moved by dan: we maintain it/);
});

test('pressing "only referenced" at once turns into a reason box; empty reason is refused in place', async () => {
  const { curate, document } = await load();
  const box = document.createElement('div');
  document.body.appendChild(box);
  box.innerHTML = curate.leafRowHtml(LEAF);
  box.querySelector('[data-admission-open]').click();
  const input = box.querySelector('[data-admission-reason]');
  assert.ok(input, 'the press shows a reason box immediately');
  box.querySelector('[data-admission-submit]').click();
  assert.equal(box.querySelector('[data-admission-reason]').placeholder, 'a reason is needed');
  box.querySelector('[data-admission-cancel]').click();
  assert.ok(box.querySelector('[data-admission-open]'), 'cancel restores the button');
});

test('the zero-component sentence and the left-out lines render under the selector', async () => {
  const { adm, document } = await load();
  const box = document.createElement('div');
  box.innerHTML = adm.admissionNoteHtml({ sentence: 'this repository deploys other software and builds none of its own: see Dependencies · runtime',
    left_out: ['4 services referenced only · listed as runtime dependencies'] });
  assert.match(box.querySelector('[data-admission-sentence]').textContent, /builds none of its own/);
  assert.match(box.querySelector('[data-admission-left-out]').textContent, /left out · 4 services referenced only/);
  assert.equal(adm.admissionNoteHtml({ sentence: '', left_out: [] }), '');
  assert.equal(adm.admissionNoteHtml(undefined), '');
});
