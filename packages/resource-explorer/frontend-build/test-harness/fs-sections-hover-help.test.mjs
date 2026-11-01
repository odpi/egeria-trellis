/** Behavior: the Folder survey card's collapsed result sections and its presses' hover help (2026-11-01;
 *  DESIGN-COLLAPSED-RESULT-SECTIONS and DESIGN-HOVER-HELP-PRESSES §3, first slices).
 *
 *  Real fs-survey.js, sections.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - every press carries a title that is not its label; the Survey press, when off, says why;
 *  - a section starts collapsed above 8 rows or with a table, open at 8 or fewer; its summary answers while collapsed;
 *  - the person's open/collapsed choice is remembered per resource; a press that fills a section opens it once,
 *    without writing a choice;
 *  - the absence words stay what they were ("no folder survey yet", "not run yet").
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-28', surveyor: 'Resource Explorer', decided: true };
const STEP = (step, over = {}) => ({ analysis_id: step, step, name: step.replace(/_/g, ' '), description: '', intent: 'analysis',
  installed: true, missing_reason: '', run: '', results: [], ...over });
const RES = (measure, state) => ({ measure, scope: 'tree', state, summary: `${measure} summary`, reason: '',
  state_words: { measured: 'measured', nothing_found: 'nothing found', not_established: 'not established' }[state] });
const RAN = (step, results) => STEP(step, { run: '2026-11-01T09:00:00', results });
const types = (n) => Array.from({ length: n }, (_, i) => ({ name: `type${i}`, count: i + 1 }));
const EXT = { rows: { kept: true, files: 4 }, led_by: 're', surveyed_at: '2026-10-28T08:00:00',
  folders: { deepest_folder_depth: 3, folders_with_files: 7, largest_folders: [{ path: 'a', depth: 1, files: 2, bytes: 10 }], depth_histogram: [] },
  hashing: { off: true }, owners: {}, narratives: [] };
const MEASURED = (nTypes) => ({ state: 'measured', surveyor: 'Resource Explorer', surveyed_at: '2026-10-28T08:00:00',
  results: { counts: { files: 134, subdirectories: 9, total_size_bytes: 60000000, file_types: nTypes }, times: {},
    inaccessible: { count: 0 }, file_types: types(nTypes), extensions: [] } });
const VIEW = ({ results = MEASURED(10), steps = [STEP('fs_duplicates'), STEP('fs_activity')], extensions = EXT } = {}) => ({
  reach: REACH, leads: REACH.surveyor, ...results, extensions, file_profiles: null, price: { priced: false, words: 'not priced' },
  derived: { steps } });

function backend(views) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    const run = /\/analyses\/([^/]+)\/run$/.exec(u);
    if (method === 'POST' && run) return ok({ status: 'started', activity_id: `act-${run[1]}`, run_id: `r-${run[1]}` });
    const act = /\/api\/activity\/act-([^/?]+)/.exec(u);
    if (method === 'GET' && act) return ok({ id: `act-${act[1]}`, status: 'ok', summary: '1 annotation(s).' });
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
  const app = await import('/static/next/app.js');
  app.state.me = { user_id: 'alice' };
  window.localStorage.clear();
  const fs = await import('/static/next/fs-survey.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, calls, fs, window };
}
const section = (host, key) => host.querySelector(`details[data-fs-section="${key}"]`);
const toggle = (host, d, open) => { d.open = open; d.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle')); };

test('every press carries a title that says more than its label; the Survey press, off, says why', async () => {
  const { host, fs } = await setUp();
  const v = VIEW();
  host.innerHTML = fs.folderSurveyCardHtml({ ...v, file_profiles: { price: { walk: 'w', words: 'p' }, run: { counts: { submitted: 1, by_re: 1 }, files: [] } },
    narrative_docs: { price: { files: 2, words: 'p' } }, class_match: { price: { columns: 3, words: 'p' } } });
  const presses = [...host.querySelectorAll('[data-fs-act]')];
  assert.ok(presses.length >= 7, `the card draws its presses (${presses.length})`);
  for (const b of presses) {
    const title = b.getAttribute('title') || '';
    assert.ok(title.trim() && title !== text(b), `${b.dataset.fsAct} has a title of its own`);
    assert.ok(title.length <= 170, `${b.dataset.fsAct}'s title shows whole (${title.length})`);
    assert.doesNotMatch(title, /\d{4}-\d{2}-\d{2}T/, 'no ISO time in a title');
  }
  assert.match(host.querySelector('[data-fs-act="derive"]').title, /kept rows.*Publishes nothing/);
  host.innerHTML = fs.folderSurveyCardHtml({ ...v, reach: { state: 'nobody', sentence: 'nobody can read this path' } });
  const off = host.querySelector('[data-fs-act="survey"]');
  assert.ok(off.disabled);
  assert.match(off.title, /^Off: no one can be named to lead/);
});

test('above 8 rows or with a table a section starts collapsed, and its summary answers', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  const files = section(host, 'files');
  assert.equal(files.open, false, '10 file types: more than 8 rows');
  assert.ok(files.hasAttribute('data-fs-results'), 'the section keeps its own data attributes');
  assert.equal(text(files.querySelector('summary')), 'Files · 134 files · 57.2 MB · 10 types · surveyed by Resource Explorer · 2026-10-28');
  const measures = section(host, 'measures');
  assert.equal(measures.open, false, 'a table: collapsed');
  assert.match(text(measures.querySelector('summary')), /^Walk measures · 7 folders with files · deepest level 3 · measured · 2026-10-28$/);
  const derived = section(host, 'derived');
  assert.equal(derived.open, true, '2 steps not run: 2 rows, open');
  assert.equal(text(derived.querySelector('summary')), 'Derived from the walk · 2 steps · 2 not yet run');
});

test('at 8 rows or fewer a section starts open; absence words are unchanged', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ results: MEASURED(8) }));
  assert.equal(section(host, 'files').open, true);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ results: { state: 'none' }, extensions: null }));
  assert.equal(section(host, 'files'), null);
  assert.equal(text(host.querySelector('[data-fs-results]')), 'no folder survey yet');
  assert.equal(text(host.querySelector('[data-fs-measures]')), 'Walk measures · not measured yet');
});

test('the summary counts derived results by state and says when the steps last ran', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [RAN('fs_activity', [RES('fs_activity', 'measured'), RES('fs_access_activity', 'not_established')]),
    RAN('fs_duplicates', [RES('fs_duplicates', 'nothing_found')]), STEP('fs_layout', { installed: false, missing_reason: 'm' })] }));
  assert.equal(text(section(host, 'derived').querySelector('summary')),
    'Derived from the walk · 3 steps · 1 measured · 1 nothing found · 1 not established · 1 not installed · derived · 2026-11-01');
});

test("the person's choice is remembered per resource; the page's own open writes nothing", async () => {
  const { host, fs, window } = await setUp();
  await fs.mountFolderSurvey(host, 'loading_bay');
  assert.equal(window.localStorage.length, 0, 'drawing the card stores nothing');
  toggle(host, section(host, 'files'), true);
  assert.equal(window.localStorage.getItem('re-next.section.alice.loading_bay.files'), 'open');
  await fs.mountFolderSurvey(host, 'loading_bay');
  assert.equal(section(host, 'files').open, true, 'opened again as the person left it');
  await fs.mountFolderSurvey(host, 'other_tree');
  assert.equal(section(host, 'files').open, false, 'another resource starts by its own rows');
});

test('a press that fills a section opens it once, without writing a choice', async () => {
  const many = Array.from({ length: 5 }, (_, i) => RAN(`fs_step${i}`, [RES(`m${i}`, 'measured')]));
  const { host, fs, window } = await setUp([VIEW({ steps: many }), VIEW({ steps: many })]);
  const repaint = await fs.mountFolderSurvey(host, 'tree');
  assert.equal(section(host, 'derived').open, false, '10 rows: collapsed');
  host.querySelector('[data-fs-act="derive"]').click();
  await tick(200);
  assert.equal(section(host, 'derived').open, true, 'Derive filled it: open for this paint');
  assert.equal(window.localStorage.getItem('re-next.section.alice.tree.derived'), null, 'no choice written');
  repaint();
  assert.equal(section(host, 'derived').open, false, 'the next paint starts by its rows again');
});

test('a table in a collapsed section is made resizable when the section first opens', async () => {
  const { host, fs } = await setUp();
  await fs.mountFolderSurvey(host, 'tree');
  const measures = section(host, 'measures');
  const table = measures.querySelector('table[data-rc-id]');
  assert.equal(table.querySelectorAll('[data-rc-handle]').length, 0, 'not measured while closed');
  toggle(host, measures, true);
  assert.ok(table.querySelectorAll('[data-rc-handle]').length >= 4, 'handles once open');
  toggle(host, measures, false);
  toggle(host, measures, true);
  assert.equal(table.querySelectorAll('[data-rc-handle]').length, 4, 'enhanced once, not again on each open');
});

test('sectionStartsOpen: a fill opens, a remembered choice wins, else no table and 8 rows or fewer', async () => {
  ensureLoaderRegistered();
  const { sectionStartsOpen, SECTION_ROWS_OPEN } = await import('/static/next/sections.js');
  assert.equal(SECTION_ROWS_OPEN, 8);
  assert.equal(sectionStartsOpen({ rows: 8 }), true);
  assert.equal(sectionStartsOpen({ rows: 9 }), false);
  assert.equal(sectionStartsOpen({ rows: 1, hasTable: true }), false);
  assert.equal(sectionStartsOpen({ rows: 50, pref: 'open' }), true);
  assert.equal(sectionStartsOpen({ rows: 1, pref: 'collapsed' }), false);
  assert.equal(sectionStartsOpen({ rows: 50, hasTable: true, pref: 'collapsed', justFilled: true }), true);
  assert.equal(sectionStartsOpen(), true, 'nothing known: open');
});

test('the pref helpers never throw: no person, no storage or a throwing storage reads "" and writes false', async () => {
  ensureLoaderRegistered();
  const m = await import('/static/next/sections.js');
  const d = {};
  const s = { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = v; } };
  assert.equal(m.writeSectionPref(s, 'alice', 'tree', 'files', true), true);
  assert.deepEqual(Object.keys(d), ['re-next.section.alice.tree.files']);
  assert.equal(m.readSectionPref(s, 'alice', 'tree', 'files'), 'open');
  assert.equal(m.readSectionPref(s, 'alice', 'other', 'files'), '', 'per resource');
  assert.equal(m.readSectionPref(s, 'bob', 'tree', 'files'), '', 'per person');
  m.writeSectionPref(s, 'alice', 'tree', 'files', false);
  assert.equal(m.readSectionPref(s, 'alice', 'tree', 'files'), 'collapsed');
  const boom = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  assert.deepEqual([m.readSectionPref(boom, 'a', 'd', 'f'), m.writeSectionPref(boom, 'a', 'd', 'f', true)], ['', false]);
  assert.deepEqual([m.readSectionPref(null, 'a', 'd', 'f'), m.writeSectionPref(undefined, 'a', 'd', 'f', true)], ['', false]);
  assert.deepEqual([m.writeSectionPref(s, '', 'd', 'f', true), m.readSectionPref({ getItem: () => 'banana' }, 'a', 'd', 'f')], [false, '']);
});

test('the card still paints when storage throws', async () => {
  const { host, fs } = await setUp();
  const real = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: () => { throw new Error('denied'); } });
  try {
    await fs.mountFolderSurvey(host, 'tree');
    assert.equal(section(host, 'files').open, false, 'drawn by its rows');
    toggle(host, section(host, 'files'), true);
    assert.equal(section(host, 'files').open, true);
  } finally { Object.defineProperty(globalThis, 'localStorage', real); }
});
