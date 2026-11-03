/** Behavior: Files build step 8 (2026-10-28) on the file system's folder survey card: "Personal or sensitive data",
 *  data_class_match over the file profiles (F7), proposals only.
 *
 *  Real fs-survey.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - before any profile: the server's "not priced" words, "not matched yet", no press, never a zero;
 *  - a run shows its stored words, the rule that every match is a proposal and nothing is published, the matches worth a
 *    look (established or a pattern no class has) by file and column with their statements, and the names apart;
 *  - an unread catalogue is said in the run's words, never as "no match";
 *  - files Egeria profiled are said to be not opened; stale after newer profiles;
 *  - the press posts once and repaints from the re-read.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-28', surveyor: 'Resource Explorer', decided: true };
const NOT_PRICED = { priced: false, files: null, columns: null, run: '', words: 'not priced · no data file has been profiled yet · profile the data files first' };
const PRICED = { priced: true, files: 2, columns: 3, run: '2026-10-28T10:00:00',
  words: 'matches 3 columns of 2 profiled files against the data classes Egeria holds (read from Egeria) · reads the first 1 MB of 2 files Resource Explorer profiled; files Egeria profiled are not opened' };
const M = (over) => ({ file_path: 'hr/payroll.csv', column: 'email', verdict: 'matched', established: true, confidence: 90,
  matched_display_name: 'Email Address', detected_patterns: [], privacy_relevant: true, header_suspect: false,
  statement: 'hr/payroll.csv › email: conforms to \'Email Address\' with 1.00 of sampled values, from a head sample of 24 of 24 rows.', ...over });
const RUN = (over = {}) => ({ profile_run: '2026-10-28T10:00:00', ran_at: '2026-10-28T10:05:00', columns: 3, files: 2,
  words: '3 columns of 2 profiled files examined · 1 column match a data class (1 privacy-relevant) · against 1 data class Egeria holds',
  stale: false, rule: 'every match is a proposal · a person confirms it; nothing is published to Egeria',
  matches: [M(), M({ column: 'id', verdict: 'unmatched_patterned', established: false, matched_display_name: '', detected_patterns: ['uuid'],
    privacy_relevant: false, statement: 'hr/payroll.csv › id: no known match, but the column follows a recognisable pattern (uuid)' }),
  M({ file_path: 'notes.csv', column: 'colour', verdict: 'no_match', established: false, matched_display_name: '', privacy_relevant: false })],
  name_signals: [{ file_path: 'hr/payroll.csv', words: ['hr', 'payroll'] }], name_signal_files: 1, file_outcomes: { sampled: 2 }, ...over });
const CM = (price, run = null) => ({ price, run, words: run ? '' : 'not matched yet' });
const VIEW = (cm = CM(NOT_PRICED)) => ({ state: 'none', reach: REACH, leads: REACH.surveyor, surveyor: '', extensions: null,
  file_profiles: null, price: { priced: false, words: 'not priced' }, derived: { steps: [] }, class_match: cm });

function backend(views) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    if (method === 'POST' && /\/class-match$/.test(u)) return ok({ columns: 3, established: 1 });
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

test('before any profile: not priced, not matched yet, no press, never a zero', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  const sec = host.querySelector('[data-fs-class]');
  assert.equal(sec.dataset.fsClassState, 'none');
  assert.equal(host.querySelector('[data-fs-class-price]').dataset.fsClassPriced, 'no');
  assert.match(text(sec), /profile the data files first/);
  assert.match(text(sec), /not matched yet/);
  assert.doesNotMatch(text(sec), /\b0 (columns|files|matches)\b/);
  assert.equal(host.querySelector('[data-fs-act="match-classes"]'), null);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(CM(PRICED)));
  assert.ok(host.querySelector('[data-fs-act="match-classes"]'), 'profiled columns: the press is offered');
  assert.match(text(host.querySelector('[data-fs-class-price]')), /files Egeria profiled are not opened/);
});

test('a run shows its words, the proposal rule, the matches worth a look by file and column, and the names apart', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(CM(PRICED, RUN())));
  const sec = host.querySelector('[data-fs-class]');
  assert.equal(sec.dataset.fsClassState, 'matched');
  assert.match(text(host.querySelector('[data-fs-class-run]')), /matched · 2026-10-28 · 3 columns of 2 profiled files examined/);
  assert.match(text(sec), /every match is a proposal · a person confirms it; nothing is published to Egeria/);
  const table = host.querySelector('[data-fs-measure-table="class-match"]');
  const rows = [...table.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(text).join(' '));
  assert.equal(rows.length, 2, 'the established match and the proposal; "no class matched" is in the words, not the table');
  assert.match(rows[0], /hr\/payroll\.csv email matches Email Address yes .*head sample of 24 of 24 rows/);
  assert.match(rows[1], /id a pattern no class has · proposal uuid/);
  assert.match(text(host.querySelector('[data-fs-class-names]')), /1 file whose name suggests personal data · on the name only/);
  const name = host.querySelector('[data-fs-measure-table="class-names"] tbody tr');
  assert.deepEqual([...name.querySelectorAll('td')].map(text), ['hr/payroll.csv', 'hr, payroll']);
});

test('an unread catalogue is said in the words; Egeria-profiled files are said not opened; stale after newer profiles', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW(CM(PRICED, RUN({ matches: [], name_signals: [], name_signal_files: 0,
    words: "3 columns of 2 profiled files examined · nothing established · Egeria's data classes could not be read: refused",
    file_outcomes: { egeria_profiled: 1, sampled: 1 }, stale: true }))));
  const sec = host.querySelector('[data-fs-class]');
  assert.match(text(sec), /nothing established · Egeria's data classes could not be read: refused/);
  assert.doesNotMatch(text(sec), /no class matched/);
  assert.match(text(host.querySelector('[data-fs-class-egeria]')), /1 file profiled by Egeria · not opened here, so not sampled/);
  assert.match(text(host.querySelector('[data-fs-class-stale]')), /newer profiles exist/);
  assert.equal(host.querySelector('[data-fs-measure-table="class-match"]'), null);
});

test('the press posts once and repaints from the re-read', async () => {
  const { host, calls, fs } = await setUp([VIEW(CM(PRICED)), VIEW(CM(PRICED, RUN()))]);
  await fs.mountFolderSurvey(host, 'tree');
  host.querySelector('[data-fs-act="match-classes"]').click();
  await tick(80);
  assert.equal(calls.filter((c) => c.method === 'POST' && /\/class-match$/.test(c.url)).length, 1);
  assert.equal(host.querySelector('[data-fs-class]').dataset.fsClassState, 'matched');
  assert.equal(host.querySelector('[data-fs-error]'), null);
});
