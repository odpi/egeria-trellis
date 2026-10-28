/** Real-DOM regression tests for Egeria-native survey launch on Survey &
 *  analyses (BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md, the owner's
 *  gate points 1, 2, 3 and 5).
 *
 *  Per the project's harness rule ("every /next fix from here on adds its
 *  regression to the harness, not only a source-text test"), these render the
 *  real pane through the real module graph:
 *
 *    - a survey RE cannot run says WHY and is not drawn as "○ not run";
 *    - Run submits, and the row then says what the SERVER's proof says
 *      ("submitted to Egeria · <time>"), not what the click implies;
 *    - the row moves to complete on its own (the poll), showing when Egeria was
 *      read, and the report's time and annotation count;
 *    - a failed survey shows Egeria's own status word and message, never
 *      "complete", never blank;
 *    - the status survives a whole-pane re-render, because it comes from data
 *      (the engine-note bug was a status written to a transient node).
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SURVEY = 'PostgreSQLSurvey::survey-postgres-database';
const CATALOG = 'PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess';

const isoAgo = (ms) => new Date(Date.now() - ms).toISOString();

function row(over = {}) {
  return {
    qualified_name: SURVEY, display_name: 'Survey PostgreSQL Database',
    kind: 'survey_existing', description: 'Egeria native survey.',
    runnable: true, cannot_run_reason: '', in_flight: false,
    run: { state: 'not_run', egeria_status: '', message: '', submitted_at: '', read_at: '',
           report_at: '', report_guid: '', engine_action_guid: '', annotation_count: null, error: '' },
    ...over,
  };
}
const withRun = (run, over = {}) => row({ run: { ...row().run, ...run }, ...over });

const catalogRow = () => row({
  qualified_name: CATALOG, display_name: 'Catalog and Survey', kind: 'catalog_and_survey',
  runnable: false, cannot_run_reason: 'this kind is not wired yet (SQLite Embedded)',
});

/** fetch stub keyed by `METHOD path-substring`, first match wins; records calls. */
function stubFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const key = Object.keys(routes).find((k) => {
      const [m, sub] = k.split(' ');
      return m === method && String(url).includes(sub);
    });
    calls.push(`${method} ${url}`);
    if (!key) throw new Error(`_UNSTUBBED_FETCH in native-surveys test: ${method} ${url}`);
    const r = typeof routes[key] === 'function' ? routes[key](url, opts) : routes[key];
    const status = r && r.__status ? r.__status : 200;
    const body = r && r.__status ? r.body : r;
    return { ok: status < 400, status, statusText: 'x', json: async () => body };
  };
  return calls;
}

async function setUp() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  // The plain specifier: native-surveys.js imports `state` from the plain
  // app.js instance, so that is the one whose state must be set (see
  // doc-sources-enrichment.test.mjs's setUpEnrichmentDom for the why).
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  const re = await import('/static/re-api.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'discovery';
  app.state.subTab = 'survey';
  return { document, app, ns, re };
}

const paneRoutes = (surveys) => ({
  'GET /api/survey-definitions/database/adventureworks/candidates': {
    technology_type: 'PostgreSQL Relational Database', candidates: [], scoping: 'questions',
    phase: 'discovery', egeria_native_processes: [] },
  'GET /api/survey-definitions/definitions': [],
  'GET /api/analyses/database/adventureworks': { analyses: [] },
  'GET /api/native-surveys/database/adventureworks': { technology_type: 'x', surveys },
});

// ── row anatomy, one state at a time ────────────────────────────────────────

test('a survey RE cannot run says why, has no Run, and is not drawn as "not run"', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(catalogRow());
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /can't be run from here — this kind is not wired yet/);
  assert.equal(host.querySelector('[data-native-run]'), null);
  // ("cannot run" contains the letters "not run"; match the words, not the letters)
  assert.doesNotMatch(text, /(^|\W)not run/);
  assert.ok(host.textContent.includes('◌'), 'the "cannot be answered here" glyph');
  assert.ok(!host.textContent.includes('○'), 'the "not run, and can be" glyph would be a lie here');
});

test('a runnable survey that has not run reads "not run" with a Run control', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(row());
  assert.match(host.textContent, /not run/);
  assert.match(host.querySelector('[data-native-run]').textContent, /^Run in Egeria\s*→$/);
});

test('a survey that has run says "Run again in Egeria →" (the destination is named whenever it is Egeria)', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(withRun({ state: 'complete', read_at: isoAgo(5_000), report_at: isoAgo(9_000),
    annotation_count: 4, report_guid: 'r-1', engine_action_guid: 'ea-1' }));
  assert.match(host.querySelector('[data-native-run]').textContent, /^Run again in Egeria\s*→$/);
  assert.doesNotMatch(host.textContent, /\bre-run\b/);
});

test('submitted: says "submitted to Egeria" with the time and the engine-action GUID, not "running"', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  const guid = 'eeeeeeee-1111-2222-3333-444444444444';
  host.innerHTML = ns.nativeSurveyRowHtml(withRun({
    state: 'submitted', submitted_at: isoAgo(60_000), engine_action_guid: guid }, { in_flight: true }));
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /submitted to Egeria · .*ago|submitted to Egeria · just now/);
  assert.ok(text.includes(guid));
  assert.doesNotMatch(text, /running|complete/);
  assert.ok(host.querySelector('[data-native-run]').disabled, 'no second submit while one is in flight');
});

test('running: Egeria\'s own status word and the time it was read', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(withRun({
    state: 'running', egeria_status: 'IN_PROGRESS', read_at: isoAgo(5_000),
    engine_action_guid: 'ea-1' }, { in_flight: true }));
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /running · Egeria says IN_PROGRESS · read just now/);
  assert.doesNotMatch(text, /complete/);
});

test('complete: when it was read, the report\'s time, the annotation count, and a way to open them', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(withRun({
    state: 'complete', egeria_status: 'COMPLETED', read_at: isoAgo(3 * 60_000),
    report_at: isoAgo(3 * 3600_000), report_guid: 'rep-1', annotation_count: 638,
    engine_action_guid: 'ea-1' }));
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /complete · read 3m ago · report from 3h ago · 638 annotations/);
  assert.ok(host.querySelector('[data-native-report="rep-1"]'));
  assert.match(host.querySelector('[data-native-run]').textContent, /^Run again in Egeria\s*→$/);
});

test('failed: Egeria\'s status word and message on the row; never "complete", never blank', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(withRun({
    state: 'failed', egeria_status: 'FAILED',
    message: 'OMES-SURVEY-ACTION-0007 could not connect to host.docker.internal',
    read_at: isoAgo(10_000), engine_action_guid: 'ea-1' }));
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /FAILED · OMES-SURVEY-ACTION-0007 could not connect to host\.docker\.internal/);
  assert.doesNotMatch(text, /complete/);
  assert.ok(host.textContent.includes('✕'));
});

test('a submission Egeria refused says so; an unreadable one says the read failed', async () => {
  const { document, ns } = await setUp();
  const a = document.createElement('div');
  a.innerHTML = ns.nativeSurveyRowHtml(withRun({ state: 'submit_failed', error: 'OMAG-400 not recognized' }));
  assert.match(a.textContent, /not submitted — Egeria did not accept it: OMAG-400 not recognized/);
  const b = document.createElement('div');
  b.innerHTML = ns.nativeSurveyRowHtml(withRun({
    state: 'unreadable', submitted_at: isoAgo(1000), engine_action_guid: 'ea-9',
    error: 'view server unreachable' }, { in_flight: true }));
  assert.match(b.textContent.replace(/\s+/g, ' '), /submitted to Egeria · .* last read failed: view server unreachable/);
});

test('every state ever emitted by the server has a row that renders something specific', async () => {
  const { document, ns } = await setUp();
  for (const state of ['not_run', 'submit_failed', 'submitted', 'unreadable', 'running',
                       'awaiting_report', 'report_incomplete', 'complete', 'failed']) {
    const host = document.createElement('div');
    host.innerHTML = ns.nativeSurveyStatusHtml(withRun({
      state, egeria_status: 'X', message: 'm', error: 'e', annotation_count: 1,
      report_guid: 'r', engine_action_guid: 'g' }));
    assert.ok(host.textContent.trim().length > 4, `state ${state} rendered blank`);
  }
});

// ── the pane: run, poll, re-render ──────────────────────────────────────────

test('Survey & analyses lists the native surveys with Run, and the unrunnable one with its reason', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  stubFetch(paneRoutes([row(), catalogRow()]));

  await app.loadSurveyPane();

  const text = content.textContent.replace(/\s+/g, ' ');
  assert.match(text, /Egeria's own surveys/);
  assert.match(text, /Survey PostgreSQL Database/);
  assert.ok(content.querySelector(`[data-native-run="${SURVEY}"]`));
  assert.equal(content.querySelector(`[data-native-run="${CATALOG}"]`), null);
  assert.match(text, /can't be run from here — this kind is not wired yet/);
});

test('Run submits, the row shows the server-proven "submitted", and the poll moves it to complete', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);

  const guid = 'eeeeeeee-1111-2222-3333-444444444444';
  let phase = 'idle';
  const calls = stubFetch({
    ...paneRoutes([row()]),
    'GET /api/native-surveys/database/adventureworks': () => ({ technology_type: 'x', surveys: [
      phase === 'idle' ? row() : phase === 'submitted'
        ? withRun({ state: 'submitted', submitted_at: isoAgo(1000), engine_action_guid: guid }, { in_flight: true })
        : withRun({ state: 'complete', egeria_status: 'COMPLETED', read_at: isoAgo(500),
                    report_at: isoAgo(60_000), report_guid: 'rep-1', annotation_count: 3,
                    engine_action_guid: guid })] }),
    'POST /api/native-surveys/database/adventureworks/run': () => {
      phase = 'submitted';
      return { run: {}, surveys: [withRun({ state: 'submitted', submitted_at: isoAgo(1000),
                                            engine_action_guid: guid }, { in_flight: true })] };
    },
    'POST /api/native-surveys/database/adventureworks/refresh': () => {
      phase = 'complete';
      return { technology_type: 'x', surveys: [withRun({
        state: 'complete', egeria_status: 'COMPLETED', read_at: isoAgo(500),
        report_at: isoAgo(60_000), report_guid: 'rep-1', annotation_count: 3,
        engine_action_guid: guid })] };
    },
  });

  await app.loadSurveyPane();
  const status = () => content.querySelector('[data-native-status]').textContent.replace(/\s+/g, ' ');
  assert.match(status(), /not run/);

  // The pane's own bind uses the default 8s poll; drive the same code with a
  // short one through the module's public binder so the test does not wait.
  const ns = await import('/static/next/stages/native-surveys.js');
  ns.bindNativeSurveys(content, 'adventureworks', [row()], { pollMs: 20 });

  content.querySelector(`[data-native-run="${SURVEY}"]`).click();
  await new Promise((r) => setTimeout(r, 5));
  assert.match(status(), /submitted to Egeria/, 'the row shows the persisted submission');
  assert.ok(calls.some((c) => c.startsWith('POST') && c.endsWith('/run')));

  await new Promise((r) => setTimeout(r, 120));      // the poll fires on its own
  assert.match(status(), /complete · read .* · report from .* · 3 annotations/);
  assert.ok(calls.some((c) => c.endsWith('/refresh')), 'it polled');
});

test('the status comes from data, so a whole-pane reload cannot wipe it', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  stubFetch(paneRoutes([withRun({
    state: 'failed', egeria_status: 'FAILED', message: 'engine host down',
    read_at: isoAgo(1000), engine_action_guid: 'ea-1' })]));

  await app.loadSurveyPane();
  assert.match(content.textContent, /FAILED · engine host down/);
  await app.loadSurveyPane();                          // second render of the whole pane
  assert.match(content.textContent, /FAILED · engine host down/);
  assert.doesNotMatch(content.textContent, /complete ·/);
});

test('a refused Run (422) shows the reason on the row and leaves the row unchanged', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  stubFetch({
    ...paneRoutes([row()]),
    'POST /api/native-surveys/database/adventureworks/run': { __status: 422,
      body: { detail: 'Egeria has no asset with the GUID RE has stored for this resource.' } },
  });
  await app.loadSurveyPane();
  content.querySelector(`[data-native-run="${SURVEY}"]`).click();
  await new Promise((r) => setTimeout(r, 5));
  const err = content.querySelector('[data-native-error]');
  assert.match(err.textContent, /no asset with the GUID/);
  assert.ok(!err.classList.contains('hidden'));
  assert.match(content.querySelector('[data-native-status]').textContent, /not run/);
});

test('secrets path not configured: a ? note, and Run stays available', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(row({
    credentials: 'not-configured',
    credentials_note: "can't confirm Egeria has credentials · secrets path not configured" }));
  assert.match(host.textContent.replace(/\s+/g, ' '),
    /\? can't confirm Egeria has credentials · secrets path not configured/);
  assert.ok(host.querySelector('[data-native-run]'));
});
