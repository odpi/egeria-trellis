/** Behavior: Files build step 1 (2026-10-21), the walk's extensions on the file system's Understanding.
 *
 *  Real fs-survey.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - "not measured yet" is its own state, never zeros;
 *  - what the screen says about the per-file rows and about publishing comes from the view's stored proof (rows kept = the
 *    registry has them; "N of M annotations in Egeria" = what landed), never from which button was pressed;
 *  - a capped walk says "capped at N" and never reads as the whole tree; noise directories are named; hashing says what it
 *    skipped and why;
 *  - narratives are proposals with their file and modified date as evidence;
 *  - every table is a real table with a header row (resizable by colresize.js) inside a box that scrolls, with cells that
 *    truncate, so nothing overruns at the largest text;
 *  - "Measure beside Egeria's survey" is offered only where Egeria leads, posts to walk-extensions, and repaints from the re-read.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = {
  re_only: { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-21', checked_date: '2026-10-21',
    surveyor: 'Resource Explorer', why: '', decided: true },
  egeria: { state: 'egeria', sentence: 'Egeria can read /deployments/data-lake · verified 2026-10-21', checked_date: '2026-10-21',
    surveyor: "Egeria's Folder Survey", why: '', decided: true, path: '/deployments/data-lake' },
};

const EXT = (over = {}) => ({
  run_id: '2026-10-21T10:00:00', surveyed_at: '2026-10-21T10:00:00', led_by: 're', capped_words: '',
  report_sentence: 'surveyed by Resource Explorer · Egeria could not reach the path',
  folders: { folders_with_files: 310, deepest_folder_depth: 9,
    largest_folders: [{ path: 'a/very/long/path/to/a/folder/whose/name/keeps/going/on/and/on/and/on/forever/node_data', depth: 12, files: 4000, bytes: 9_000_000 },
      { path: 'docs', depth: 1, files: 20, bytes: 1_000_000 }],
    depth_histogram: [{ depth: 0, folders: 1, files: 12, bytes: 3000 }, { depth: 1, folders: 40, files: 300, bytes: 70000 }] },
  owners: { distinct_owners: 2, distinct_groups: 1, world_readable_files: 77 },
  modes: { '0644': 900, '0755': 20 },
  hashing: { algorithm: 'SHA-256', limit_bytes: 10485760, off: false, hashed_files: 880, hashed_bytes: 12345, over_limit_files: 3,
    secret_named_files: 5, symlink_files: 2, unreadable_files: 0 },
  narratives_summary: { folders: 310, folders_with_narrative: 2, files_read: 3, stopped_at_read_cap: false, read_cap: 1000 },
  narratives: [{ folder: '', stated_by: 'README.md', modified_at: '2026-10-14T08:00:00', title: 'Walk Me',
    description: 'Payroll extracts for 2026.', licence: 'MIT', status: 'proposal' }],
  narratives_not_shown: 0, not_walked_by_name: { '.git': 3, node_modules: 12 },
  rows: { kept: true, files: 920, runs_kept: 2, error: '' }, report_guid: '', published: false, publish: null,
  ...over,
});

const VIEW = (reach, ext, over = {}) => ({
  state: ext ? 'measured' : 'none', reach, leads: reach.surveyor, surveyor: ext ? 'Resource Explorer' : '', surveyed_at: ext ? ext.surveyed_at : '',
  report_guid: '', published: false, publish: null, report_sentence: '', detail_lines: [], limits: null, extensions: ext,
  results: ext ? { measured: true, counts: { files: 920 }, times: {}, file_types: [], extensions: [], inaccessible: { count: 0, listed: [], requested: false } } : null,
  ...over,
});

function backend(views) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    return ok({});
  };
  return calls;
}

async function setUp(views = [VIEW(REACH.re_only, null)]) {
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
  return { document, host, calls, fs };
}

test('no walk measures is "not measured yet", never zeros', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, null));
  const m = host.querySelector('[data-fs-measures]');
  assert.equal(m.dataset.fsMeasuresState, 'none');
  assert.match(text(m), /Walk measures · not measured yet/);
  assert.doesNotMatch(text(m), /\b0\b/);
});

test('the measures: depth, owners, modes, hashes, noise directories, each from the stored block', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT()));
  const m = host.querySelector('[data-fs-measures]');
  const t = text(m);
  assert.match(text(m.querySelector('[data-fs-measures-by]')), /measured by Resource Explorer · 2026-10-21/);
  assert.match(t, /Deepest level\s?9/);
  assert.match(t, /Folders with files\s?310/);
  assert.match(t, /Owners\s?2/);
  assert.match(t, /World-readable\s?77/);
  assert.match(t, /0644 ×900, 0755 ×20/);
  assert.match(t, /Not walked\s?\.git ×3, node_modules ×12/, 'noise directories are counted and named');
  assert.match(text(m.querySelector('[data-fs-measures-hash]')), /880 files hashed \(SHA-256\) · files over 10(\.0)? MB are not hashed/);
  assert.match(text(m.querySelector('[data-fs-measures-hash]')), /3 over the size limit · 5 with a secret-looking name · 2 linked/);
  assert.match(t, /walked to the end of every folder it entered/);
});

test('a capped walk says capped at N and never reads as the whole tree', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ capped_words: 'capped at 100,000 entries' })));
  const cap = host.querySelector('[data-fs-measures-capped]');
  assert.match(text(cap), /capped at 100,000 entries · not the whole tree/);
  assert.equal(host.querySelector('[data-fs-measures-not-capped]'), null);
});

test('a spent hashing budget is said on the card, and absent when the budget held', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ hashing: { ...EXT().hashing, over_budget_files: 14,
    budget_words: 'hashing stopped at 2.0 GiB: 14 files not hashed' } })));
  assert.match(text(host.querySelector('[data-fs-measures-hash-budget]')), /hashing stopped at 2\.0 GiB: 14 files not hashed/);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT()));
  assert.equal(host.querySelector('[data-fs-measures-hash-budget]'), null);
});

test('hashing off is said, not shown as zero files hashed', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ hashing: { algorithm: 'SHA-256', limit_bytes: 0, off: true, hashed_files: 0 } })));
  assert.match(text(host.querySelector('[data-fs-measures-hash]')), /hashing off · no content hash taken/);
});

test('"rows kept" follows the stored proof: kept says how many, not kept says why', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT()));
  const kept = host.querySelector('[data-fs-measures-rows]');
  assert.equal(kept.dataset.fsRowsKept, 'yes');
  assert.match(text(kept), /per-file rows kept · 920 files · 2 runs held/);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ rows: { kept: false, files: 0, runs_kept: 0, error: 'RuntimeError: registry unavailable' } })));
  const not = host.querySelector('[data-fs-measures-rows]');
  assert.equal(not.dataset.fsRowsKept, 'no');
  assert.match(text(not), /per-file rows not kept · RuntimeError: registry unavailable/);
});

test('publish words come from what landed: N of M, or not published and why, or kept here', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT()));
  assert.match(text(host.querySelector('[data-fs-measures-published]')), /kept here · not published to Egeria/);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ published: true, report_guid: 'abcdef12345',
    publish: { published: true, landed_words: '12 of 12 annotations in Egeria', not_published_why: '' } })));
  assert.match(text(host.querySelector('[data-fs-measures-published]')), /published · 12 of 12 annotations in Egeria · report abcdef12/);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ publish: { published: false, landed_words: '', not_published_why: 'no annotation landed in Egeria' } })));
  assert.match(text(host.querySelector('[data-fs-measures-published]')), /not published · no annotation landed in Egeria/);
});

test('narratives are proposals with their file and modified date as evidence', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT()));
  const n = text(host.querySelector('[data-fs-narrative]'));
  assert.match(n, /\(root\) · described by README\.md · modified 2026-10-14 · license MIT/);
  assert.match(n, /Walk Me · Payroll extracts for 2026\. · a proposal · confirm it on Curate/);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, EXT({ narratives: [] })));
  assert.match(text(host.querySelector('[data-fs-narratives]')), /no README-type file found/);
});

test('tables are real tables inside a scrolling box, truncating cells, and resize like every /next table', async () => {
  const { host, fs } = await setUp([VIEW(REACH.re_only, EXT())]);
  await fs.mountFolderSurvey(host, 'walkme');
  // Walk measures holds tables, so it starts collapsed; its tables are made resizable when it first opens.
  const section = host.querySelector('details[data-fs-section="measures"]');
  assert.equal(section.open, false);
  section.open = true;
  section.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  const tables = [...host.querySelectorAll('[data-fs-measure-table] table')];
  assert.equal(tables.length, 2);
  for (const t of tables) {
    assert.ok(t.tHead && t.tHead.rows[0].cells.length >= 4, 'a header row');
    assert.match(t.parentElement.className, /overflow-x-auto/, 'scrolls inside its own box');
    assert.match(t.parentElement.className, /max-w-full/);
    assert.ok(t.querySelectorAll('[data-rc-handle]').length >= 4, 'colresize.js put a handle on each header cell');
  }
  const longCell = tables[0].querySelector('tbody td');
  assert.match(longCell.className, /truncate/, 'a long path truncates, it does not overrun');
  assert.ok(longCell.getAttribute('title').endsWith('node_data'), 'the whole path is in the cell title');
  assert.doesNotMatch(host.innerHTML, /style="[^"]*width:\s*\d{4,}px/, 'no fixed pixel width wider than any viewport');
});

test('Measure beside Egeria is offered only where Egeria leads, posts, and repaints from the re-read', async () => {
  const { host, calls, fs } = await setUp([VIEW(REACH.egeria, null), VIEW(REACH.egeria, EXT({ led_by: 'egeria',
    report_sentence: "measured by Resource Explorer · beside Egeria's Folder Survey" }), { state: 'none' })]);
  await fs.mountFolderSurvey(host, 'lake');
  const press = host.querySelector('[data-fs-act="measure"]');
  assert.ok(press, "Egeria leads: Resource Explorer's measures beside it are offered");
  press.click();
  await tick(80);
  const post = calls.find((c) => c.method === 'POST' && /\/lake\/walk-extensions$/.test(c.url));
  assert.ok(post, 'POST /api/filesystems/lake/walk-extensions');
  assert.deepEqual(post.body, { publish: false });
  assert.match(text(host.querySelector('[data-fs-measures-by]')), /beside Egeria's Folder Survey/, 'repainted from the re-read view');
  assert.ok(host.querySelector('[data-fs-act="publish-measures"]'), 'kept, led beside Egeria, not yet published: Publish is offered');
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(REACH.re_only, null));
  assert.equal(host.querySelector('[data-fs-act="measure"]'), null, 'where Resource Explorer leads, the walk already measures these');
});

test('publishing the beside-Egeria measures posts and the re-read says what landed', async () => {
  const beside = (over) => VIEW(REACH.egeria, EXT({ led_by: 'egeria', ...over }), { state: 'none' });
  const { host, calls, fs } = await setUp([beside({}), beside({ published: true, report_guid: 'abcdef12345',
    publish: { published: true, landed_words: '8 of 8 annotations in Egeria', not_published_why: '' } })]);
  await fs.mountFolderSurvey(host, 'lake');
  host.querySelector('[data-fs-act="publish-measures"]').click();
  await tick(80);
  assert.ok(calls.some((c) => c.method === 'POST' && /\/lake\/walk-extensions\/publish$/.test(c.url)));
  assert.match(text(host.querySelector('[data-fs-measures-published]')), /published · 8 of 8 annotations in Egeria/);
  assert.equal(host.querySelector('[data-fs-act="publish-measures"]'), null);
});
