/** One card per definition (designer 2026-10-16, Q1-Q2). The definition card keeps its place and takes the run line and
 *  the run history from the top card; the `re_definition` row leaves "Egeria's own surveys"; the card keeps the one run
 *  control "Re-run · coordinated by Egeria →" beside "Re-run…". Every word is the server's (its derived run). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const QN = 'GovActionProcess::DatabaseAnalysisSurvey';
const blank = { state: 'not_run', egeria_status: '', message: '', submitted_at: '', read_at: '', report_guid: '', error: '' };
const partial = {
  state: 'partial', line: 'Database Analysis Survey · step 1 postgres_column_profile ○ not run · step 3 db_derived ✓ report 4a2771da · coordinated by Egeria · started by peterprofile',
  summary: '2 of its 4 steps ran · made under an earlier version of this definition (4 steps, now 8)',
  plan: { recorded: true, steps: 4, definition_steps: 8, differs: true },
  submitted_at: '2026-10-15T19:34:00', read_at: '2026-10-15T19:40:00', error: '',
  steps: [{ report_guid: '' }, { report_guid: '' }, { report_guid: '4a2771da-0000' }],
  problems: [{ step: 1, label: 'postgres_column_profile', kind: 'refused', reason: 'the stored credential has SELECT on 3 of 61 relations', credential: true }],
  fixable: null };
const defRow = (run = partial, over = {}) => ({ qualified_name: QN, display_name: 'Database Analysis Survey', kind: 're_definition',
  runnable: true, in_flight: false, run, coordinated_by_egeria: { available: true, why_not: '', state: run.state, line: run.line }, ...over });
const ownRow = { qualified_name: 'PostgreSQLSurvey::survey-postgres-database', display_name: 'Survey PostgreSQL Database', kind: 'survey_existing',
  target: 'asset', runnable: true, in_flight: false, run: { ...blank }, cannot_run_reason: '', neutral: false, stale: false };
const runs = [
  { when: '2026-10-15T19:34:00', coordinated_by: 'egeria', by: 'peterprofile', state: 'partial', ran: 2, of: 4, version_note: '(4-step version)',
    reports: [{ step: 3, guid: '4a2771da-0000' }] },
  { when: '2026-10-14T10:00:00', coordinated_by: 're', by: 'dan', state: 'complete', ran: null, of: null, version_note: '', reports: [] },
  { when: '2026-10-13T10:00:00', coordinated_by: 'egeria', by: 'dan', state: 'complete', ran: 8, of: 8, version_note: '', reports: [] }];
const cand = (over = {}) => ({ qualified_name: QN, display_name: 'Database Analysis Survey', steps: new Array(8).fill({}), step_count: 8,
  last_run_at: '2026-10-15T19:34:00Z', last_run_status: 'partial', last_run_coordinated_by: 'egeria', runs,
  coordinated_by_egeria: { label: 'coordinated by Egeria', available: true, why_not: '', state: 'partial', line: '' },
  coordinated_row: defRow(), ...over });
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  return { document, app, ns };
}

test('"Egeria\'s own surveys" lists only Egeria\'s own: a definition row is not in it', async () => {
  const { ns, document } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = ns.nativeSurveysSectionHtml([ownRow, defRow()]);
  assert.match(norm(box), /Egeria's own surveys/);
  assert.match(norm(box), /Survey PostgreSQL Database/);
  assert.doesNotMatch(norm(box), /Database Analysis Survey|Survey Definition/);
  const only = document.createElement('div');
  only.innerHTML = ns.nativeSurveysSectionHtml([defRow()]);
  assert.doesNotMatch(norm(only), /Egeria's own surveys/, 'no heading over an empty list');
  assert.ok(only.querySelector('#native-surveys'), 'the host stays: it drives the follow of the definition run');
  assert.equal(ns.nativeSurveysSectionHtml([]), '');
});

test('the definition card carries the run line under its header: the same words whoever coordinated', async () => {
  const { app, document } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = app.surveyRowHtml(cand());
  const line = box.querySelector('[data-def-run-line]');
  assert.ok(line, 'the run line is on the card');
  assert.match(norm(line), /◐ 2 of its 4 steps ran · made under an earlier version of this definition \(4 steps, now 8\)/);
  assert.match(norm(line), /coordinated by Egeria · started by peterprofile/);
  assert.match(norm(box), /8 steps/, 'the header counts the definition');
  assert.equal(box.querySelectorAll('[data-def-row]').length, 1, 'one card');
  assert.doesNotMatch(norm(box), /ran \d+[hd] ago/, 'the short last-run cell is replaced, not repeated');
});

test('a latest run RE coordinated keeps the short last-run cell (no coordinated run line)', async () => {
  const { app, document } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = app.surveyRowHtml(cand({ last_run_coordinated_by: '', coordinated_row: null }));
  assert.equal(box.querySelector('[data-def-run-line]'), null);
  assert.match(norm(box), /ran /);
});

test('earlier runs sit behind "runs · 3 ›" with the coordinator on each line and the version note on an older plan', async () => {
  const { app, document } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = app.surveyRowHtml(cand());
  const details = box.querySelector('[data-def-runs]');
  assert.match(norm(details.querySelector('summary')), /runs · 3 ›/);
  const items = [...details.querySelectorAll('[data-def-run]')].map(norm);
  assert.equal(items.length, 3);
  assert.match(items[0], /coordinated by Egeria · started by peterprofile · partial · 2 of 4 ran \(4-step version\)/);
  assert.match(items[1], /by RE · started by dan · complete/);
  assert.doesNotMatch(items[1], /\d of \d ran/, 'a count RE did not record is not shown as one');
  assert.match(items[2], /coordinated by Egeria · started by dan · complete · 8 of 8 ran/);
  assert.doesNotMatch(items[2], /-step version/);
  assert.ok(items[0].includes('step 3 report ›'));
  const none = document.createElement('div');
  none.innerHTML = app.surveyRowHtml(cand({ runs: [] }));
  assert.equal(none.querySelector('[data-def-runs]'), null);
});

test('the card keeps the one run control beside Re-run…: "Re-run · coordinated by Egeria →"', async () => {
  const { app, document } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = app.surveyRowHtml(cand());
  assert.equal(box.querySelector('[data-run-survey]').textContent.trim(), 'Re-run…');
  assert.equal(box.querySelector('[data-coordinate-egeria]').textContent.trim(), 'Re-run · coordinated by Egeria →');
  assert.doesNotMatch(norm(box), /Run again in Egeria/);
  // a definition Egeria can't start: shown off on the card, with its reason
  const off = document.createElement('div');
  off.innerHTML = app.surveyRowHtml(cand({ coordinated_by_egeria: { available: false, why_not: "Egeria can't start this · step 1 has no executor in Egeria", state: '' } }));
  const b = off.querySelector('[data-coordinate-egeria]');
  assert.ok(b.disabled);
  assert.match(norm(off), /Egeria can't start this · step 1 has no executor in Egeria/);
});

test('the pane: the definition row is not listed, the card has its run line; the page carries one card per definition', async () => {
  const { app, document } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  app.state.resourceType = 'db'; app.state.selectedSlug = 'coco_pharma'; app.state.stage = 'analysis'; app.state.subTab = 'survey';
  const routes = {
    'GET /api/survey-definitions/database/coco_pharma/candidates': { technology_type: 'PostgreSQL Relational Database', scoping: 'questions',
      phase: 'analysis', egeria_native_processes: [], candidates: [{ qualified_name: QN, display_name: 'Database Analysis Survey',
        survey_kind: 'analysis', description: 'Runs analyses.', last_run_at: '2026-10-15T19:34:00Z', last_run_status: 'partial',
        last_run_coordinated_by: 'egeria', runs, coordinated_by_egeria: { label: 'coordinated by Egeria', available: true, why_not: '', state: 'partial', line: '' } }] },
    'GET /api/survey-definitions/definitions': [],
    'GET /api/analyses/database/coco_pharma': { analyses: [] },
    'GET /api/native-surveys/database/coco_pharma': { technology_type: 'x', surveys: [ownRow, defRow()] },
  };
  globalThis.fetch = async (url, opts = {}) => {
    const key = Object.keys(routes).find((k) => k.split(' ')[0] === (opts.method || 'GET') && String(url).includes(k.split(' ')[1]));
    if (!key) throw new Error(`_UNSTUBBED_FETCH: ${url}`);
    return { ok: true, status: 200, statusText: 'x', json: async () => routes[key] };
  };
  await app.loadSurveyPane();
  const native = content.querySelector('#native-surveys');
  assert.doesNotMatch(norm(native), /Database Analysis Survey/);
  const cards = [...content.querySelectorAll('[data-def-row]')].filter((c) => c.dataset.defRow === QN);
  assert.equal(cards.length, 1);
  assert.match(norm(cards[0].querySelector('[data-def-run-line]')), /2 of its 4 steps ran/);
  assert.equal(content.querySelectorAll('[data-def-row]').length, 1, 'one card for the definition, none in the list');
});
