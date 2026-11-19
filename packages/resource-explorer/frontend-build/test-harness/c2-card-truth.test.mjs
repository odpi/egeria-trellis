/** Behavior: item C2 (2026-11-19), the file-system survey card tells the truth and updates itself.
 *
 *  Real fs-survey.js, native-surveys.js, publish.js, native-report.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - TR-70: a run in flight is on the card ("Surveying… (Egeria, since HH:MM)", Survey off) and the pane is read again once
 *    the followed run settles, with no manual reload; Egeria's CSV reports waiting are one line with a time, and the
 *    read-back's failed count is not dropped;
 *  - D5: a report with no stated count says "annotations not counted", never "0 annotations";
 *  - D8: the CSV files still waiting are named (three at most) once some reports have come back;
 *  - D10: "Check again" that changed nothing says why, in place of the reach line's suffix;
 *  - D11: a disabled Survey says why on one line, only while it is disabled for a reason;
 *  - D12: every size on the card is decimal and labelled, one format;
 *  - D13: "Measure beside" has its price line, only where it is offered;
 *  - TR-71: the collapsed loading_bay card is no longer than before (one line added: D13's price).
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const EGERIA = { state: 'egeria', sentence: 'Egeria can read /deployments/loading-bay · verified 2026-10-25', checked_date: '2026-10-25',
  surveyor: "Egeria's Folder Survey", why: '', decided: true, path: '/deployments/loading-bay' };
const COUNTS = (over = {}) => ({ files: 56, profiled: 50, by_egeria: 35, by_re: 15, sampled: 0, not_read: 5, not_read_formats: ['json', 'xls'],
  not_permitted: 0, over_limit: 0, unreadable: 0, secret_name: 0, link_not_followed: 0, not_installed: 0, egeria_unchecked: 0,
  submit_failed: 0, submitted: 0, header_suspect: 2, submitted_names: [], submitted_more: 0, ...over });

/** The loading_bay card as the walk left it (2026-11-19): Egeria-led survey, walk measures, profiles, narratives, class match, derived. */
const LOADING_BAY = (over = {}) => ({
  state: 'measured', reach: EGERIA, leads: EGERIA.surveyor, surveyor: "Egeria's Folder Survey", surveyed_at: '2026-11-19T01:20:55+00:00',
  report_guid: '5b9f850a-0000', published: true, publish: null, pre_reset: null, report_sentence: "surveyed by Egeria's Folder Survey",
  detail_lines: ['analysis level: top level only', 'a file-name log is written by Egeria'], limits: null,
  egeria_asset: true, egeria_run_state: 'complete', egeria_run_since: '2026-11-19T01:20:49+00:00',
  results: { measured: true, counts: { files: 151, subdirectories: 24, total_size_bytes: 57_400_000, file_types: 10, unclassified: 14 },
    times: { last_modified: '2026-11-18' }, file_types: [{ name: 'CSV Data File', count: 36 }], extensions: [{ name: 'csv', count: 36 }],
    inaccessible: { count: 0, listed: [], requested: true } },
  price: { priced: false, words: "Egeria's Folder Survey leads here: its run time is Egeria's and is not priced by Resource Explorer; Resource Explorer's last walk beside it saw 151 files" },
  extensions: { run_id: '2026-11-19T01:32:00', surveyed_at: '2026-11-19T01:32:00', led_by: 'egeria', capped_words: '',
    report_sentence: 'surveyed by Resource Explorer', folders: { folders_with_files: 25, deepest_folder_depth: 2, largest_folders: [], depth_histogram: [] },
    owners: { distinct_owners: 1, distinct_groups: 1, world_readable_files: 151 }, modes: { '0644': 6 },
    hashing: { limit_bytes: 10_000_000, off: false, hashed_files: 150, over_limit_files: 1, secret_named_files: 0, symlink_files: 0, unreadable_files: 0 },
    narratives: [], narratives_not_shown: 0, not_walked_by_name: {}, rows: { kept: true, files: 151, runs_kept: 2, error: '' },
    report_guid: '', published: false, publish: null },
  file_profiles: { price: { walk: true, words: '56 data files, 12.0 MB to read; Egeria will profile 36 CSV files, RE will profile 20 others; about 1 s' },
    run: { run_id: '2026-11-19T01:37:15+00:00', counts: COUNTS(), words: '50 files profiled (35 by Egeria, 15 by RE), 0 sampled, 5 not read (formats: json, xls), 0 not permitted',
      files: [], files_not_shown: 0, publish: null } },
  narrative_docs: { price: { priced: true, files: 7, words: 'reads 7 narrative files in full (6.6 KB)' },
    run: { ran_at: '2026-11-19T01:45:00', words: '7 files, 18 passages', stale: false, stated: 'each passage is a proposal', }, kept: 'kept in the search index' },
  class_match: { price: { priced: true, columns: 120, files: 50, words: 'matches 120 columns of 50 profiled files' },
    run: { ran_at: '2026-11-19T01:46:00', words: '120 columns of 50 profiled files examined', matches: [], name_signals: [], name_signal_files: 0, stale: false, rule: 'every match is a proposal' } },
  derived: { steps: [{ step: 'fs_activity', analysis_id: 'fs_activity', name: 'Activity', installed: true, run: '2026-11-19T01:50:00',
    results: [{ measure: 'a', state: 'measured', state_words: 'measured', summary: '21 modified within 7 days' }] }] },
  ...over,
});

/** The card's visible lines: the leaf blocks (div, summary, li) that hold text, a closed <details> counting only its summary. */
function visibleLines(host) {
  const root = host.cloneNode(true);
  root.querySelectorAll('details:not([open])').forEach((d) => [...d.children].filter((c) => c.tagName !== 'SUMMARY').forEach((c) => c.remove()));
  return [...root.querySelectorAll('div, summary, li')]
    .filter((e) => !e.querySelector('div, summary, li, details') && text(e) !== '').map((e) => text(e));
}

async function setUp(fetchImpl) {
  const { document, window } = makeDomEnvironment();
  if (fetchImpl) globalThis.fetch = fetchImpl;
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const fs = await import('/static/next/fs-survey.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { document, host, fs };
}

const ok = (b) => ({ ok: true, status: 200, json: async () => b });

test('TR-70: a run in flight is on the button, with the time it was submitted, and Survey is off', async () => {
  const { host, fs } = await setUp();
  for (const state of fs.IN_FLIGHT_STATES) {
    host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY({ egeria_run_state: state }));
    const b = host.querySelector('[data-fs-act="survey"]');
    assert.match(text(b), /^Surveying… \(Egeria, since \S+/, state);
    assert.equal(b.disabled, true, state);
    assert.equal(host.querySelector('[data-fs-survey-why]'), null, 'the label is the reason: no second line');
  }
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY({ egeria_run_state: 'complete' }));
  assert.equal(text(host.querySelector('[data-fs-act="survey"]')), 'Survey');
  assert.equal(host.querySelector('[data-fs-act="survey"]').disabled, false);
});

test('TR-70: the card paints the run in flight from its own read, and "Surveying…" outlasts the press', async () => {
  const views = [LOADING_BAY({ egeria_run_state: 'complete' }), LOADING_BAY({ egeria_run_state: 'submitted' })];
  let v = 0;
  const { host, fs } = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    if (m === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, 1)]);
    if (m === 'POST' && /\/folder-survey$/.test(u)) return ok({ led_by: 'egeria' });
    return ok({});
  });
  await fs.mountFolderSurvey(host, 'loading_bay');
  host.querySelector('[data-fs-act="survey"]').click();
  await tick(60);
  assert.match(text(host.querySelector('[data-fs-act="survey"]')), /^Surveying… \(Egeria, since /);   // after the POST returned
  assert.equal(host.querySelector('[data-fs-act="survey"]').disabled, true);
});

test('TR-70: when the followed Folder Survey row settles the pane is read again, once, with no manual reload', async (t) => {
  const mine = globalThis.fetch;
  const { document } = makeDomEnvironment();
  globalThis.fetch = mine;
  const realSet = globalThis.setTimeout;
  let now = 0; const queue = [];
  globalThis.setTimeout = (fn, ms = 0) => { const id = queue.length + 1; queue.push({ id, at: now + ms, fn }); return id; };
  globalThis.clearTimeout = (id) => { const q = queue.find((x) => x.id === id); if (q) q.fn = null; };
  t.after(() => { globalThis.setTimeout = realSet; });
  const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
  const advance = async (ms) => {
    const end = now + ms;
    for (;;) {
      const due = queue.filter((q) => q.fn && q.at <= end).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      now = due.at; const f = due.fn; due.fn = null; f(); await settle();
    }
    now = end; await settle();
  };
  const row = (state) => ({ qualified_name: 'FileSurvey::survey-folder', display_name: 'Folder Survey', kind: 'survey_existing',
    runnable: true, in_flight: ['submitted', 'running'].includes(state),
    run: { state, egeria_status: '', engine_action_guid: 'g-1', submitted_at: new Date().toISOString(), read_at: '', error: '', message: '' } });
  const answers = [[row('running')], [row('complete')]];
  let reads = 0; const posts = [];
  globalThis.fetch = async (u, o = {}) => { posts.push(`${(o.method || 'GET')} ${u}`); return ok({ surveys: answers[Math.min(reads++, 1)] }); };
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  const fs = await import('/static/next/fs-survey.js');
  Object.assign(app.state, { resourceType: 'filesystem', selectedSlug: 'loading_bay', stage: 'discovery', subTab: 'survey' });
  const content = document.createElement('div'); document.body.appendChild(content);
  const first = [row('submitted')];
  content.innerHTML = `<div id="native-surveys">${first.map(ns.nativeSurveyRowHtml).join('')}</div>`;
  let reloads = 0;
  const settled = fs.reloadWhenFolderSurveySettles(first, 'filesystem', () => { reloads += 1; });
  ns.bindNativeSurveys(content, 'loading_bay', first, { onRows: (rows) => settled(rows) });
  await advance(2000);
  assert.equal(reloads, 0, 'still running: the card is not re-read yet');
  await advance(2000);
  assert.equal(reloads, 1, 'the row settled: the pane is read again');
  assert.ok(posts.some((p) => /^POST .*\/refresh$/.test(p)), 'the follow read the run back from Egeria (the refresh POST)');
  const n = reads;
  await advance(120000);
  assert.equal(reads, n, 'no reads after it settled');
  assert.equal(reloads, 1);
  // a definition row is not this watcher's business; another resource type is never reloaded by it
  const quiet = fs.reloadWhenFolderSurveySettles([{ kind: 're_definition', in_flight: true }], 'filesystem', () => { reloads += 1; });
  quiet([{ kind: 're_definition', in_flight: false }]);
  const db = fs.reloadWhenFolderSurveySettles([row('running')], 'db', () => { reloads += 1; });
  db([row('complete')]);
  assert.equal(reloads, 1);
  app.state.subTab = 'questions';
});

test("TR-70/D8: Egeria's CSV reports still to come are one line with the time; once some are back the waiting files are named", async () => {
  const { host, fs } = await setUp();
  const waiting = (over) => LOADING_BAY({ file_profiles: { ...LOADING_BAY().file_profiles,
    run: { ...LOADING_BAY().file_profiles.run, counts: COUNTS(over) } } });
  host.innerHTML = fs.folderSurveyCardHtml(waiting({ submitted: 36, by_egeria: 0, by_re: 15 }));
  const line = text(host.querySelector('[data-fp-egeria]'));
  assert.match(line, /^⚠ waiting for 36 reports from Egeria · since \S+/);
  assert.doesNotMatch(line, /\.csv/, 'before any read-back nothing is named');
  host.innerHTML = fs.folderSurveyCardHtml(waiting({ submitted: 1, submitted_names: ['loading_bay/c.csv'], submitted_more: 0 }));
  assert.match(text(host.querySelector('[data-fp-egeria]')), /^⚠ no report from Egeria yet: loading_bay\/c\.csv · since [\d:]+( [AP]M)?$/);
  host.innerHTML = fs.folderSurveyCardHtml(waiting({ submitted: 5, submitted_names: ['a.csv', 'b.csv', 'c.csv'], submitted_more: 2 }));
  assert.match(text(host.querySelector('[data-fp-egeria]')), /no report from Egeria yet: a\.csv, b\.csv, c\.csv \(\+2 more\)/);
  assert.equal(host.querySelectorAll('[data-fp-egeria]').length, 1, 'one line');
});

test("TR-70: pressing 'Read Egeria's reports' keeps the answer: its failed count joins the same line", async () => {
  const wait = LOADING_BAY({ file_profiles: { ...LOADING_BAY().file_profiles,
    run: { ...LOADING_BAY().file_profiles.run, counts: COUNTS({ submitted: 2, submitted_names: ['a.csv', 'b.csv'] }) } } });
  const { host, fs } = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    if (m === 'GET' && /\/folder-survey$/.test(u)) return ok(wait);
    if (m === 'POST' && /read-egeria$/.test(u)) return ok({ read: 33, waiting: 1, failed: 1, no_columns: 0 });
    return ok({});
  });
  await fs.mountFolderSurvey(host, 'loading_bay');
  assert.doesNotMatch(text(host.querySelector('[data-fp-egeria]')), /could not be read/);
  host.querySelector('[data-fs-act="read-egeria"]').click();
  await tick(60);
  assert.match(text(host.querySelector('[data-fp-egeria]')), /a\.csv, b\.csv · since [\d:]+( [AP]M)? · 1 could not be read$/);
});

test('D5: a report that states no annotation count is "annotations not counted", never "0 annotations"', async () => {
  await setUp();
  const publish = await import('/static/next/stages/publish.js');
  const base = { guid: 'g-1', display_name: 'Survey', surveyed_at: '2026-11-19T01:20:55', schema_count: 0, table_count: 0, column_count: 0 };
  const words = (r) => text(new (globalThis.window.DOMParser)().parseFromString(`<body>${publish.reportRowListHtml(r)}</body>`, 'text/html').body);
  const none = words({ ...base, annotation_count: null });
  assert.match(none, /annotations not counted/);
  assert.doesNotMatch(none, /0 annotations/);
  assert.match(words({ ...base, annotation_count: 6 }), /6 annotations/);
  assert.match(words({ ...base, annotation_count: 0 }), /0 annotations/, 'a stated zero is still a zero');
});

test("D10: 'Check again' that kept a settled survey says so in place of the reach line's suffix, until the next press", async () => {
  let kept = true;
  const { host, fs } = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    if (m === 'GET' && /\/folder-survey$/.test(u)) return ok(LOADING_BAY());
    if (m === 'POST' && /\/reach$/.test(u)) return ok({ ...EGERIA, kept });
    return ok({});
  });
  await fs.mountFolderSurvey(host, 'loading_bay');
  assert.match(text(host.querySelector('[data-fs-reach]')), /led by Egeria's Folder Survey/);
  host.querySelector('[data-fs-act="check"]').click();
  await tick(60);
  const reach = text(host.querySelector('[data-fs-reach]'));
  assert.match(reach, /Egeria can read \/deployments\/loading-bay · verified 2026-10-25 · still verified by the 2026-10-25 survey · a path check doesn't replace it/);
  assert.doesNotMatch(reach, /led by/, 'the suffix is replaced, not added to');
  kept = false;
  host.querySelector('[data-fs-act="check"]').click();
  await tick(60);
  assert.doesNotMatch(text(host.querySelector('[data-fs-reach]')), /still verified by/);
});

test('D11: a disabled Survey says why on one line, beside the action that resolves it, and only while it is disabled', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY({ egeria_asset: false, egeria_run_state: '' }));
  assert.equal(host.querySelector('[data-fs-act="survey"]').disabled, true);
  assert.match(text(host.querySelector('[data-fs-survey-why]')), /Survey is off: Egeria has no asset for this folder yet · attach the root folder first/);
  assert.ok(host.querySelector('[data-fs-act="attach"]'), 'the action that resolves it is on the same card');
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY());
  assert.equal(host.querySelector('[data-fs-survey-why]'), null, 'Survey is on: no reason line');
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY({ reach: { state: 'not_checked', sentence: 'not checked yet', surveyor: '', decided: false } }));
  assert.match(text(host.querySelector('[data-fs-survey-why]')), /no one can be named to lead yet/);
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY(), { busy: 'check' });
  assert.equal(host.querySelector('[data-fs-survey-why]'), null, 'busy with another press: no reason line');
});

test('D12: one size format on the card, decimal and labelled, at the 1000 boundaries', async () => {
  const { host, fs } = await setUp();
  const { fmtBytesDecimal: f } = await import('/static/next/stages/native-report.js');
  assert.deepEqual([0, 999, 1000, 1500, 999_949, 999_950, 57_400_000, 8_400_000_000, 2_500_000_000_000].map(f),
    ['0 B', '999 B', '1.0 KB', '1.5 KB', '999.9 KB', '1.0 MB', '57.4 MB', '8.4 GB', '2,500.0 GB']);
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY());
  const t = text(host);
  assert.match(t, /Total size\s?57\.4 MB/);
  assert.match(t, /files over 10\.0 MB are not hashed/);
  assert.doesNotMatch(t, /KiB|MiB|GiB/);
});

test('D13: "Measure beside" is priced on the card, only where it is offered', async () => {
  const { host, fs } = await setUp();
  const priced = LOADING_BAY({ measure_price: { priced: true, words: 'last walk (2026-11-19): 151 files, 57.4 MB · about 1 s to walk again on this machine' } });
  host.innerHTML = fs.folderSurveyCardHtml(priced);
  assert.match(text(host.querySelector('[data-fs-measure-price]')), /151 files, 57\.4 MB · about 1 s/);
  host.innerHTML = fs.folderSurveyCardHtml({ ...priced, reach: { ...EGERIA, state: 're_only', surveyor: 'Resource Explorer' } });
  assert.equal(host.querySelector('[data-fs-act="measure"]'), null);
  assert.equal(host.querySelector('[data-fs-measure-price]'), null);
});

test('TR-71: the collapsed loading_bay card is no longer than before (only D13\'s price line is added)', async () => {
  const { host, fs } = await setUp();
  const COLLAPSED = { open: { files: false, measures: false, profiles: false, classes: false, derived: false } };
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY({ measure_price: { priced: true, words: 'last walk: 151 files, 57.4 MB · about 1 s' } }), COLLAPSED);
  const lines = visibleLines(host);
  const BEFORE = 15;                 // the same fixture, collapsed, drawn by the card at origin/main 7d3d1eba (measured 2026-11-19)
  const ALLOWED = BEFORE + 1;        // D13
  assert.ok(lines.length <= ALLOWED, `${lines.length} lines > ${ALLOWED}:\n${lines.join('\n')}`);
  host.innerHTML = fs.folderSurveyCardHtml(LOADING_BAY(), COLLAPSED);
  assert.ok(visibleLines(host).length <= BEFORE, 'without the measure price the card is not one line longer');
});

test('item 5: when a survey settles the stage header is redrawn from the list read again (today, published), no reload', async () => {
  const { document } = makeDomEnvironment();
  const stale = { slug: 'loading_bay', display_name: 'Loading bay', last_surveyed_at: '2026-10-25T10:00:00+00:00', is_published: false, status: 'active' };
  const fresh = { ...stale, last_surveyed_at: new Date().toISOString(), is_published: true };
  globalThis.fetch = async (url) => ok(/\/api\/filesystems\/?$/.test(String(url)) ? [fresh] : {});
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'filesystem', selectedSlug: 'loading_bay', filesystems: [stale], filesystemsLoaded: true, subTab: 'survey' });
  const host = document.createElement('div'); host.id = 'resource-header'; document.body.appendChild(host);
  host.innerHTML = app.resourceHeaderHtml('loading_bay');
  assert.match(text(host), /surveyed \d+d ago/);
  assert.match(text(host), /not published to Egeria/);
  await app.refreshFilesystemSummary('loading_bay');
  assert.match(text(host), /surveyed just now/);
  assert.match(text(host), /published to Egeria/);
  assert.doesNotMatch(text(host), /not published/);
  app.state.subTab = 'questions';
});
