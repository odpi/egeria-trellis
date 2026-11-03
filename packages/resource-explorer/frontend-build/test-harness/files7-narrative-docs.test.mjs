/** Behavior: Files build step 7 (2026-10-28) on the file system's folder survey card: "Narrative files, read in full"
 *  (F11's long form) and fs_layout_signals under "Derived from the walk".
 *
 *  Real fs-survey.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - the price is the server's words before the press, "not priced" when no walk has looked, never a number of its own;
 *  - never read says "not read yet", never zero passages;
 *  - a run shows its stored words, that each passage is a proposal stated by its file, and that it is not published;
 *  - a run older than the newest walk is said to be stale;
 *  - the press is offered only when the walk found narrative files, posts once, and repaints from the re-read;
 *  - fs_layout_signals is one more derived step: its own words, nothing special-cased;
 *  - a derived result the walk's survey report carried says "in Egeria" with the report; one it did not, nothing.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-28', surveyor: 'Resource Explorer', decided: true };
const NOT_PRICED = { priced: false, files: null, bytes: null, walk: '', words: 'not priced · no walk has looked for narrative files yet' };
const PRICED = { priced: true, files: 2, bytes: 540, walk: '2026-10-28T09:00:00',
  words: 'reads 2 narrative files in full (540 B), each under 524.3 KB · found by the walk of 2026-10-28 · opens no other file' };
const RUN = (over = {}) => ({ walk_run: '2026-10-28T09:00:00', ran_at: '2026-10-28T09:05:00', collection: 'fs_tree_narratives',
  files: 2, outcomes: { read: 2 }, sections: 4, chunks: 4, stored: 4,
  words: "2 files read in full · 4 sections · 4 passages in Resource Explorer's search index",
  stale: false, stated: 'each passage is stated by its file · a proposal, never a fact, until a person confirms it', ...over });
const ND = (price, run = null) => ({ price, run, words: run ? '' : 'not read yet',
  kept: "kept in Resource Explorer's search index · not published to Egeria" });
const VIEW = ({ narrative = ND(NOT_PRICED), steps = [] } = {}) =>
  ({ state: 'none', reach: REACH, leads: REACH.surveyor, surveyor: '', extensions: { rows: { kept: true, files: 4 }, led_by: 're' },
    file_profiles: null, price: { priced: false, words: 'not priced' }, derived: { steps }, narrative_docs: narrative });

function backend(views) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    if (method === 'POST' && /\/narrative-docs$/.test(u)) return ok({ outcomes: { read: 2 }, stored: 4 });
    return ok({});
  };
  return calls;
}

async function setUp(views = [VIEW()]) {
  const { document, window } = makeDomEnvironment();
  const calls = backend(views);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const fs = await import('/static/next/fs-survey.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, calls, fs };
}

test('before any walk the price says "not priced" and nothing says read; no press is offered', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  const sec = host.querySelector('[data-fs-narr]');
  assert.equal(sec.dataset.fsNarrState, 'none');
  assert.equal(host.querySelector('[data-fs-narr-price]').dataset.fsNarrPriced, 'no');
  assert.match(text(sec), /not priced · no walk has looked for narrative files yet/);
  assert.match(text(sec), /not read yet/);
  assert.doesNotMatch(text(sec), /\b0 (files|passages|sections)\b/);
  assert.equal(host.querySelector('[data-fs-act="read-narratives"]'), null);
});

test('a walk that found narrative files prices the read before the press, and offers it', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ narrative: ND(PRICED) }));
  assert.equal(host.querySelector('[data-fs-narr-price]').dataset.fsNarrPriced, 'yes');
  assert.match(text(host.querySelector('[data-fs-narr-price]')), /reads 2 narrative files in full \(540 B\)/);
  assert.ok(host.querySelector('[data-fs-act="read-narratives"]'));
  // a walk that found none is priced ("nothing to read") but has nothing to press
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ narrative: ND({ ...PRICED, files: 0, words: 'nothing to read · the walk of 2026-10-28 found no narrative file' }) }));
  assert.equal(host.querySelector('[data-fs-act="read-narratives"]'), null);
  assert.match(text(host.querySelector('[data-fs-narr]')), /found no narrative file/);
});

test('a stored run shows its own words, that every passage is a proposal, and that it is not published', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ narrative: ND(PRICED, RUN()) }));
  const sec = host.querySelector('[data-fs-narr]');
  assert.equal(sec.dataset.fsNarrState, 'read');
  assert.match(text(host.querySelector('[data-fs-narr-run]')), /read · 2026-10-28 · 2 files read in full · 4 sections · 4 passages/);
  assert.match(text(sec), /a proposal, never a fact, until a person confirms it/);
  assert.match(text(sec), /not published to Egeria/);
  assert.equal(host.querySelector('[data-fs-narr-stale]'), null);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ narrative: ND(PRICED, RUN({ stale: true })) }));
  assert.match(text(host.querySelector('[data-fs-narr-stale]')), /a newer walk found these files/);
});

test('the press posts once and repaints from the re-read', async () => {
  const { host, calls, fs } = await setUp([VIEW({ narrative: ND(PRICED) }), VIEW({ narrative: ND(PRICED, RUN()) })]);
  await fs.mountFolderSurvey(host, 'tree');
  host.querySelector('[data-fs-act="read-narratives"]').click();
  await tick(80);
  assert.equal(calls.filter((c) => c.method === 'POST' && /\/narrative-docs$/.test(c.url)).length, 1);
  assert.equal(host.querySelector('[data-fs-narr]').dataset.fsNarrState, 'read');
  assert.equal(host.querySelector('[data-fs-error]'), null);
});

test('fs_layout_signals is one more derived step, in its own stored words', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [{ analysis_id: 'fs_layout_signals', step: 'fs_layout_signals',
    name: 'File System Layout Signals', description: '', intent: 'discovery', installed: true, missing_reason: '', run: '2026-10-28T09:00:00',
    results: [{ measure: 'fs_layout_signals', scope: 'tree', state: 'measured', state_words: 'measured', reason: '', values: {},
      summary: 'recognised: a project (1 project manifest, 2 .git directories not walked); backups (3 files, 4.2 KB)' }] }] }));
  const step = host.querySelector('[data-fs-step="fs_layout_signals"]');
  assert.equal(step.dataset.fsStepState, 'run');
  assert.match(text(step), /recognised: a project \(1 project manifest, 2 \.git directories not walked\)/);
});

test('a derived result the walk report carried says it is in Egeria; one it did not says nothing of Egeria', async () => {
  const { host, fs } = await setUp();
  const res = (measure, published) => ({ measure, scope: 'tree', state: 'measured', state_words: 'measured', reason: '', values: {},
    summary: `${measure} summary`, published });
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [{ analysis_id: 'fs_duplicates', step: 'fs_duplicates', name: 'Duplicates',
    description: '', intent: 'analysis', installed: true, missing_reason: '', run: '2026-10-28T09:00:00',
    results: [res('fs_duplicates', { report_guid: 'abcd1234ffff', guid: 'g1', at: '2026-10-28T10:00:00' }), res('fs_duplicates_elsewhere', null)] }] }));
  const sent = host.querySelector('[data-fs-result="fs_duplicates"]');
  assert.match(text(sent.querySelector('[data-fs-result-published]')), /in Egeria · report abcd1234$/);
  assert.equal(host.querySelector('[data-fs-result="fs_duplicates_elsewhere"] [data-fs-result-published]'), null);
});
