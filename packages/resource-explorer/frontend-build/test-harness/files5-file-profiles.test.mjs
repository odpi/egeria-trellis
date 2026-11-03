/** Behavior: Files build step 5 (2026-10-23), the data-file profiles on the file system's folder survey card.
 *
 *  Real fs-survey.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - the cost line is on the card BEFORE the press ("no run yet to price from" the first time);
 *  - "not profiled yet" is its own state, never zeros;
 *  - the card's words come from the view's stored counts: P profiled (E by Egeria, R by RE), S sampled, X not read
 *    (formats), U not permitted; a sampled file says "sampled" on its row;
 *  - Egeria's CSV half that is not installed says "not installed" on the card; submitted files offer "Read Egeria's reports";
 *  - the press posts to file-profiles and repaints from the re-read.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-23', surveyor: 'Resource Explorer', decided: true };
const PRICE = (words = '8 data files, 0.2 MB to read; Egeria will profile 0 CSV files, RE will profile 8 others; no run yet to price from') =>
  ({ walk: true, words });
const COUNTS = (over = {}) => ({ files: 8, profiled: 7, by_egeria: 0, by_re: 7, sampled: 1, not_read: 1, not_read_formats: ['pkl'],
  not_permitted: 0, over_limit: 0, unreadable: 0, secret_name: 0, link_not_followed: 0, not_installed: 0, egeria_unchecked: 0,
  submit_failed: 0, submitted: 0, ...over });
const RUN = (over = {}) => ({
  run_id: '2026-10-23T09:00:00+00:00', counts: COUNTS(), egeria_half: 'none', publish: null, files_not_shown: 0,
  words: '7 files profiled (0 by Egeria, 7 by RE), 1 sampled, 1 not read (formats: pkl), 0 not permitted',
  files: [
    { file_path: 'big.csv', state: 'sampled', stats_source: 'resource-explorer', columns: 2, sample_ratio: 0.42, format: 'csv' },
    { file_path: 'g.pkl', state: 'not_applicable', stats_source: '', columns: 0, sample_ratio: null, format: 'pkl' },
  ],
  ...over,
});
const VIEW = (fp) => ({ state: 'none', reach: REACH, leads: REACH.surveyor, surveyor: '', extensions: null, file_profiles: fp });

function backend(views) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    return ok({});
  };
  return calls;
}

async function setUp(views = [VIEW(null)]) {
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

test('before any run: the cost line and "not profiled yet", never zeros', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run: null }));
  const card = host.querySelector('[data-fp]');
  assert.equal(card.dataset.fpState, 'none');
  assert.match(text(card.querySelector('[data-fp-price]')), /8 data files, 0\.2 MB to read; .* no run yet to price from/);
  assert.match(text(card), /not profiled yet/);
  assert.doesNotMatch(text(card), /\b0 files profiled/);
  assert.ok(host.querySelector('[data-fs-act="profile"]'), 'the press is offered once a walk exists');
});

test('after a run: the counts come from the view, a sampled file says sampled, a format not read is named', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE('8 data files, 0.2 MB to read; Egeria will profile 0 CSV files, RE will profile 8 others; about 1 s'), run: RUN() }));
  const card = host.querySelector('[data-fp]');
  assert.equal(card.dataset.fpState, 'profiled');
  assert.match(text(card.querySelector('[data-fp-words]')), /7 files profiled \(0 by Egeria, 7 by RE\), 1 sampled, 1 not read \(formats: pkl\), 0 not permitted/);
  assert.match(text(card.querySelector('[data-fp-price]')), /about 1 s$/);
  const table = card.querySelector('table[data-rc-id="fs-measure-profiles"]');
  assert.ok(table && table.querySelector('thead'), 'a real table with a header row');
  const cells = [...table.querySelectorAll('tbody tr')].map((tr) => [...tr.children].map((td) => text(td)));
  assert.deepEqual(cells[0], ['big.csv', 'profiled · sampled', 'RE', '2', 'sampled 42.0%']);
  assert.deepEqual(cells[1], ['g.pkl', 'not read · format pkl', '', '–', ''], 'a file not read has no column count, never 0');
  assert.match(text(card.querySelector('[data-fp-published]')), /not published to Egeria/);
  assert.ok(host.querySelector('[data-fs-act="publish-profiles"]'));
});

test("Egeria's CSV half: not installed says so; submitted files offer the read-back", async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run: RUN({ counts: COUNTS({ not_installed: 4, by_re: 3, profiled: 3 }) }) }));
  assert.match(text(host.querySelector('[data-fp-egeria]')), /not installed · Egeria's CSV Survey Service is not installed .* 4 CSV files not profiled/);
  assert.equal(host.querySelector('[data-fs-act="read-egeria"]'), null);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run: RUN({ counts: COUNTS({ submitted: 2 }) }) }));
  assert.match(text(host.querySelector('[data-fp-egeria]')), /2 CSV files submitted to Egeria's CSV Survey Service · its reports not read back yet/);
  assert.ok(host.querySelector('[data-fs-act="read-egeria"]'));
});

test('a card without the profiles block renders nothing for it', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml({ ...VIEW(null), file_profiles: undefined });
  assert.equal(host.querySelector('[data-fp]'), null);
});

test('the press posts to file-profiles and repaints from the re-read', async () => {
  const { host, calls, fs } = await setUp([VIEW({ price: PRICE(), run: null }), VIEW({ price: PRICE(), run: RUN() })]);
  await fs.mountFolderSurvey(host, 'lake');
  host.querySelector('[data-fs-act="profile"]').click();
  await tick(60);
  assert.ok(calls.some((c) => c.method === 'POST' && /\/api\/filesystems\/lake\/file-profiles$/.test(c.url)));
  assert.equal(host.querySelector('[data-fp]').dataset.fpState, 'profiled');
});

test('fix round 1: no column profile, not reachable and a data-like header each have their own words; a partial publish still offers the press', async () => {
  const { host, fs } = await setUp();
  const run = RUN({ counts: COUNTS({ no_columns: 1, not_reachable: 2, header_suspect: 1 }),
    publish: { report_guid: 'rep12345-x', annotation_count: 4, annotations_in_egeria: 2, complete: false } });
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run }));
  const other = text(host.querySelector('[data-fp-other]'));
  assert.match(other, /1 read back from Egeria with no column profile/);
  assert.match(other, /2 not reachable from here/);
  assert.match(other, /1 with a header that looks like data · those names not published/);
  assert.doesNotMatch(other, /not permitted/);
  assert.ok(host.querySelector('[data-fs-act="publish-profiles"]'), 'a partial publish can be resumed');
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run: RUN({ publish: { report_guid: 'r', annotation_count: 2, annotations_in_egeria: 2, complete: true } }) }));
  assert.equal(host.querySelector('[data-fs-act="publish-profiles"]'), null, 'a complete publish is not offered again');
});

test('fix round 2: a file system whose name in Egeria is unknown says so on the card; header suspects ride on the words', async () => {
  const { host, fs } = await setUp();
  const run = RUN({ counts: COUNTS({ file_system_name_unknown: 3, header_suspect: 1 }),
    words: '7 files profiled (0 by Egeria, 7 by RE), 1 sampled, 1 not read (formats: pkl), 0 not permitted (1 header suspect)',
    files: [{ file_path: 'a.csv', state: 'file_system_name_unknown', stats_source: '', columns: 0, sample_ratio: null, format: 'csv' }] });
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: PRICE(), run }));
  assert.match(text(host.querySelector('[data-fp-egeria]')), /the name Egeria uses for this file system could not be established · 3 CSV files not read or created in Egeria/);
  assert.match(text(host.querySelector('[data-fp-words]')), /\(1 header suspect\)$/);
  const cells = [...host.querySelectorAll('table[data-rc-id="fs-measure-profiles"] tbody tr')].map((tr) => [...tr.children].map((td) => text(td)));
  assert.equal(cells[0][1], "file system's name in Egeria not known");
});
