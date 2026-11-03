/** Behavior: W19 of the Files review (the Architect's Files review of 2026-10-30, §2), the survey card says each sentence once.
 *
 *  Real fs-survey.js against a jsdom DOM. The FOLDER SURVEY line read "surveyed by Egeria's Folder Survey · 2026-10-25 ·
 *  surveyed by Egeria's Folder Survey" and the WALK MEASURES header "measured by Resource Explorer · 2026-10-25 · beside
 *  Egeria's Folder Survey · measured by Resource Explorer · beside Egeria's Folder Survey": the stored report sentence was
 *  appended after a line that already said it. The stored sentence still adds what the line does not say.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const count = (s, part) => s.split(part).length - 1;

async function setUp() {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const fs = await import('/static/next/fs-survey.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, fs };
}

const VIEW = (over = {}) => ({
  state: 'measured', surveyor: "Egeria's Folder Survey", surveyed_at: '2026-10-25T23:46:06+00:00', report_guid: 'cc91503f00',
  published: true, report_sentence: "surveyed by Egeria's Folder Survey", detail_lines: [], limits: null,
  results: { measured: true, counts: { files: 134 }, times: {}, file_types: [], extensions: [],
    inaccessible: { count: 0, listed: null, requested: true } },
  ...over,
});

test("the FOLDER SURVEY line names Egeria's Folder Survey once", async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderResultsHtml(VIEW());
  const by = text(host.querySelector('[data-fs-surveyed-by]'));
  assert.equal(by, "surveyed by Egeria's Folder Survey · 2026-10-25");
});

test('an RE-led line keeps the part of the stored sentence it does not already say', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderResultsHtml(VIEW({ surveyor: 'Resource Explorer',
    report_sentence: 'surveyed by Resource Explorer · Egeria could not reach the path' }));
  const by = text(host.querySelector('[data-fs-surveyed-by]'));
  assert.equal(by, 'surveyed by Resource Explorer · 2026-10-25 · Egeria could not reach the path');
  assert.equal(count(by, 'surveyed by Resource Explorer'), 1);
});

test('the WALK MEASURES header says "measured by Resource Explorer" and "beside Egeria\'s Folder Survey" once', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.walkMeasuresHtml({
    run_id: '2026-10-25T23:47:39', surveyed_at: '2026-10-25T23:47:39', led_by: 'egeria', capped_words: '',
    report_sentence: "measured by Resource Explorer · beside Egeria's Folder Survey",
    folders: { folders_with_files: 18, deepest_folder_depth: 2, largest_folders: [], depth_histogram: [] },
    owners: {}, modes: {}, hashing: { off: true }, narratives_summary: {}, narratives: [], not_walked_by_name: {},
    rows: { kept: true, files: 134, runs_kept: 1 }, publish: null,
  });
  const by = text(host.querySelector('[data-fs-measures-by]'));
  assert.equal(by, "measured by Resource Explorer · 2026-10-25 · beside Egeria's Folder Survey");
});

test('sentenceBeyond drops only the parts the line already says', async () => {
  const { fs } = await setUp();
  assert.equal(fs.sentenceBeyond("surveyed by Egeria's Folder Survey · 2026-10-25", "surveyed by Egeria's Folder Survey"), '');
  assert.equal(fs.sentenceBeyond('surveyed by Resource Explorer', ''), '');
  assert.equal(fs.sentenceBeyond('a · b', 'b · c · d'), 'c · d');
});
