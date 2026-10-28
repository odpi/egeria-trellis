/** Real-DOM tests for registering a database's SERVER with Egeria (optional), on Survey & analyses.
 *
 *  - a database never given to Egeria is a NEUTRAL state (muted ○ "not registered", the word "optional",
 *    no ✕, no warn colour), with the one control "Register the server with Egeria →";
 *  - a stored pointer Egeria says is gone uses the stale wording and the SAME row offers the control;
 *  - a press shows a visible change at once ("registering…", disabled) and the result is the server's rows;
 *  - a refused connection shows Egeria's sentence verbatim plus the one RE-can-still line;
 *  - RE's own survey control (data-run-survey) is unaffected in every one of those cases;
 *  - the databases a server survey found are listed as Egeria reported them, with "found, not yet published".
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SURVEY = 'PostgreSQLSurvey::survey-postgres-database';
const SERVER = 'PostgreSQLSurvey::survey-postgres-server';
const CATALOG = 'PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess';
const REGISTER = 'Register the server with Egeria →';
const NOT_REGISTERED = "Egeria has not been given this database · register it to run Egeria's own survey · optional";
const STALE = 'the Egeria asset RE had stored no longer exists';
const GONE = 'no longer in Egeria · was registered 10-15 as dddddddd…';
const AGAIN = 'Register again →';
const REACH = 'Egeria connects from its own platform; RE can still survey this database itself';

const blankRun = { state: 'not_run', egeria_status: '', message: '', submitted_at: '', read_at: '',
  report_at: '', report_guid: '', engine_action_guid: '', annotation_count: null, error: '' };

const surveyRow = (over = {}) => ({
  qualified_name: SURVEY, display_name: 'Survey PostgreSQL Database', kind: 'survey_existing', target: 'asset',
  description: '', runnable: false, cannot_run_reason: NOT_REGISTERED, neutral: true, stale: false,
  in_flight: false, run: { ...blankRun }, reach_note: '',
  register: { available: true, label: REGISTER, why_not: '' }, ...over,
});
const catalogRow = (over = {}) => ({
  qualified_name: CATALOG, display_name: 'Catalog and Survey', kind: 'catalog_and_survey', target: 'server',
  description: '', runnable: false, cannot_run_reason: '', neutral: false, stale: false, in_flight: false,
  run: { ...blankRun, state: 'not_registered' }, reach_note: '', asset_guid: '',
  register: { available: true, label: REGISTER, why_not: '' }, ...over,
});

function stubFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const key = Object.keys(routes).find((k) => {
      const [m, sub] = k.split(' ');
      return m === method && String(url).includes(sub);
    });
    calls.push(`${method} ${url}`);
    if (!key) throw new Error(`_UNSTUBBED_FETCH: ${method} ${url}`);
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
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'discovery';
  app.state.subTab = 'survey';
  return { document, app, ns };
}

const paneRoutes = (surveys, extra = {}) => ({
  'GET /api/survey-definitions/database/adventureworks/candidates': {
    technology_type: 'PostgreSQL Relational Database', scoping: 'questions', phase: 'discovery',
    egeria_native_processes: [],
    candidates: [{ qualified_name: 'RE::survey', display_name: 'RE own survey', survey_kind: 'discovery',
      description: 'RE surveys the database itself.' }] },
  'GET /api/survey-definitions/definitions': [],
  'GET /api/analyses/database/adventureworks': { analyses: [] },
  'GET /api/native-surveys/database/adventureworks': { technology_type: 'x', surveys },
  ...extra,
});

const ownControl = (content) => content.querySelector('[data-run-survey]');

test('a never-registered database is NEUTRAL: muted cue, "optional", no error mark, one control', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  stubFetch(paneRoutes([surveyRow(), catalogRow()]));
  await app.loadSurveyPane();

  const text = content.textContent.replace(/\s+/g, ' ');
  assert.ok(text.includes(NOT_REGISTERED));
  assert.ok(text.includes('not registered with Egeria · optional'));
  const nativeHost = content.querySelector('#native-surveys');
  assert.ok(nativeHost.textContent.includes('○'), 'the neutral not-done glyph');
  assert.ok(!nativeHost.textContent.includes('✕'), 'no error mark');
  for (const el of nativeHost.querySelectorAll('[data-native-status], [data-native-survey] > span:first-child')) {
    assert.ok(!el.outerHTML.includes('text-state-warn'), 'no warn colour on a cue or a status');
  }
  assert.doesNotMatch(text, /Publish the resource|RE does not collect|cannot run this one/);
  const buttons = [...nativeHost.querySelectorAll('[data-native-register]')];
  assert.ok(buttons.length >= 1 && buttons.every((b) => b.textContent.trim() === REGISTER));
  assert.equal(nativeHost.querySelector('[data-native-run]'), null, 'nothing offers to run what Egeria was never given');
  assert.ok(ownControl(content), "RE's own survey control is unaffected");
});

test('a stored pointer Egeria says is gone: the stale words, and the SAME row offers the control', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  const gone = [surveyRow({ cannot_run_reason: STALE, neutral: false, stale: true, gone_line: GONE,
    register: { available: true, label: AGAIN, why_not: '' } }),
    catalogRow({ stale: true, gone_line: GONE, register: { available: true, label: AGAIN, why_not: '' },
      cannot_run_reason: STALE, asset_guid: 'dddddddd-0000-0000-0000-000000000001' })];
  stubFetch(paneRoutes([surveyRow({ runnable: true, cannot_run_reason: '', neutral: false,
    register: { available: false, label: REGISTER, why_not: '' } }), catalogRow({
    run: { ...blankRun, state: 'registered' }, asset_guid: 'dddddddd-0000-0000-0000-000000000001',
    register: { available: false, label: REGISTER, why_not: '' } })], {
    'POST /api/native-surveys/database/adventureworks/check': { pointers: { database: 'gone' }, surveys: gone },
  }));
  await app.loadSurveyPane();
  await new Promise((r) => setTimeout(r, 20));      // the one check read, made because a pointer is stored

  const row = content.querySelector(`[data-native-survey="${SURVEY}"]`);
  // Designer Q10: Egeria's answer in its own frame, the old GUID kept in the words, and NO glyph (not the ? family).
  assert.ok(row.textContent.replace(/\s+/g, ' ').includes(GONE));
  assert.ok(!row.textContent.includes('?'), 'no ? cue: this was established, Egeria answered');
  assert.ok(!row.textContent.includes('✕'));
  assert.ok(!/ exists/.test(row.textContent), 'not the old "no longer exists" sentence');
  const control = row.querySelector('[data-native-register]');
  assert.equal(control.textContent.trim(), AGAIN);
  assert.ok(!control.dataset.nativeStartAgain, 'one press: nothing exists to duplicate');
  assert.ok(ownControl(content), "RE's own survey control is unaffected");
});

test('a database with nothing stored makes no check read at all', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  const calls = stubFetch(paneRoutes([surveyRow(), catalogRow()]));
  await app.loadSurveyPane();
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(!calls.some((c) => c.includes('/check')));
});

test('pressing the control changes at once, and the result is the server rows, not the click', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  let release;
  const gate = new Promise((r) => { release = r; });
  const calls = stubFetch(paneRoutes([surveyRow(), catalogRow()], {
    'POST /api/native-surveys/database/adventureworks/register': async () => {
      await gate;
      return { registered: {}, surveys: [
        surveyRow({ cannot_run_reason: '', runnable: true, neutral: false, register: { available: false, label: REGISTER, why_not: '' } }),
        catalogRow({ run: { ...blankRun, state: 'registered' }, in_flight: false,
          asset_guid: 'dddddddd-0000-0000-0000-000000000001',
          register: { available: false, label: REGISTER, why_not: '' } })] };
    },
  }));
  // the stub returns a promise-valued body; unwrap it
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const res = await orig(url, opts);
    const body = await res.json();
    const real = (body && typeof body.then === 'function') ? await body : body;
    return { ...res, json: async () => real };
  };
  await app.loadSurveyPane();
  const btn = content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-register]`);
  btn.click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(btn.textContent, 'registering…');
  assert.ok(btn.disabled, 'a visible change and no second press');
  release();
  await new Promise((r) => setTimeout(r, 20));
  const status = content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-status]`).textContent;
  assert.match(status.replace(/\s+/g, ' '), /registered with Egeria · database asset dddddddd/);
  assert.ok(calls.some((c) => c.startsWith('POST') && c.endsWith('/register')));
});

test('a refused connection: Egeria\'s sentence verbatim, plus the one RE-can-still line, and RE\'s own control stays', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  const sentence = 'OMES-SURVEY-ACTION-0007 Connection to host.docker.internal:5432 refused';
  stubFetch(paneRoutes([surveyRow({ runnable: true, cannot_run_reason: '', neutral: false,
    run: { ...blankRun, state: 'failed', egeria_status: 'FAILED', message: sentence, read_at: new Date().toISOString(),
      engine_action_guid: 'ea-1' }, reach_note: REACH, register: { available: false, label: REGISTER, why_not: '' } }),
    catalogRow({ run: { ...blankRun, state: 'registered' }, register: { available: false, label: REGISTER, why_not: '' } })]));
  await app.loadSurveyPane();

  const row = content.querySelector(`[data-native-survey="${SURVEY}"]`);
  const text = row.textContent.replace(/\s+/g, ' ');
  assert.ok(text.includes(`FAILED · ${sentence}`), "Egeria's sentence, unchanged");
  assert.ok(row.querySelector('[data-native-reach]').textContent.includes(REACH));
  assert.ok(ownControl(content), "RE's own survey control is unaffected");
});

test('a failure that is not about the connection carries no RE-can-still line', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(surveyRow({ runnable: true, cannot_run_reason: '', neutral: false,
    run: { ...blankRun, state: 'failed', egeria_status: 'FAILED', message: 'duplicate annotation' } }));
  assert.equal(host.querySelector('[data-native-reach]'), null);
});

test('the databases a server survey found are listed as Egeria reported them, with the one control', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml({
    qualified_name: SERVER, display_name: 'Survey PostgreSQL Server', kind: 'survey_existing', target: 'server',
    description: '', runnable: true, cannot_run_reason: '', neutral: false, stale: false, in_flight: false,
    run: { ...blankRun, state: 'complete', read_at: new Date().toISOString(), report_at: new Date().toISOString(),
      report_guid: 'rep', annotation_count: 3 }, reach_note: '',
    discovered: [
      { name: 'adventureworks', re_slug: 'adventureworks', state: 'cataloged', control: '' },
      { name: 'sibling', re_slug: 'sibling', state: 'found', control: REGISTER },
      { name: 'unknown_db', re_slug: '', state: 'found', control: '' }] });
  const text = host.textContent.replace(/\s+/g, ' ');
  assert.match(text, /adventureworks published in Egeria/);
  assert.match(text, /sibling found, not yet published/);
  assert.match(text, /unknown_db found · not registered in RE/);
  const buttons = host.querySelectorAll('[data-native-register-slug]');
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].dataset.nativeRegisterSlug, 'sibling');
});

test('an unwired kind says "this kind is not wired yet" on its row and offers no control', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(catalogRow({
    run: { ...blankRun, state: 'not_registered' }, cannot_run_reason: 'this kind is not wired yet (SQLite Embedded)',
    register: { available: false, label: REGISTER, why_not: 'this kind is not wired yet (SQLite Embedded)' } }));
  assert.match(host.textContent.replace(/\s+/g, ' '), /can't be registered from here — this kind is not wired yet \(SQLite Embedded\)/);
  assert.equal(host.querySelector('[data-native-register]'), null);
  assert.ok(!host.textContent.includes('optional'), 'not the optional wording: this one cannot be registered at all');
});

// ── review round ────────────────────────────────────────────────────────────

test('notes from persisted facts are drawn under the status, one line each', async () => {
  const { document, ns } = await setUp();
  const host = document.createElement('div');
  host.innerHTML = ns.nativeSurveyRowHtml(catalogRow({ notes: [
    'created, not yet confirmed: press to confirm',
    "the server's connection uses adventureworks's credentials (collection adventureworks::PostgreSQL Secret), not this database's",
    "secrets path not configured: RE cannot check Egeria's credentials"] }));
  const notes = [...host.querySelectorAll('[data-native-note]')].map((n) => n.textContent);
  assert.equal(notes.length, 3);
  assert.ok(notes[1].includes("uses adventureworks's credentials"));
});

test('an unresolved earlier run offers "Start again →", and the press sends the explicit flag', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  let sent = null;
  stubFetch(paneRoutes([surveyRow(), catalogRow({
    run: { ...blankRun, state: 'awaiting_registration', egeria_status: 'COMPLETED' },
    notes: ['An earlier registration (process 99999999-0000-0000-0000-000000000001, Egeria says COMPLETED) has not produced a database RE can read in Egeria.'],
    register: { available: true, label: 'Start again →', why_not: '', start_again: true } })], {
    'POST /api/native-surveys/database/adventureworks/register': (url, opts) => {
      sent = JSON.parse(opts.body);
      return { registered: { projected: { written: true, checked: true } }, surveys: [catalogRow({ run: { ...blankRun, state: 'registered' } })] };
    },
  }));
  await app.loadSurveyPane();
  const btn = content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-register]`);
  assert.equal(btn.textContent.trim(), 'Start again →');
  assert.ok(btn.dataset.nativeStartAgain);
  assert.ok(content.textContent.includes('99999999-0000-0000-0000-000000000001'));
  btn.click();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(sent, null, 'one click only asks');
  assert.equal(btn.textContent, 'Confirm: start again');
  assert.match(content.textContent, /second database element in Egeria/);
  btn.click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(sent, { start_again: true });
  assert.match(content.textContent, /re-projected the secrets file \(a local write\)/);
});

test('the ordinary press does not send start_again', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  let sent = null;
  stubFetch(paneRoutes([surveyRow(), catalogRow()], {
    'POST /api/native-surveys/database/adventureworks/register': (url, opts) => {
      sent = JSON.parse(opts.body);
      return { registered: { projected: {} }, surveys: [catalogRow({ run: { ...blankRun, state: 'registered' } })] };
    },
  }));
  await app.loadSurveyPane();
  content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-register]`).click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(sent, { start_again: false });
});

test('the confirm is disarmed after a refused press, and after its timer, so a later click asks again', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  let posts = 0;
  const sentence = 'RE has no record of the earlier process, so it cannot check whether it is still running.';
  stubFetch(paneRoutes([surveyRow(), catalogRow({
    run: { ...blankRun, state: 'awaiting_registration' },
    register: { available: true, label: 'Start again →', why_not: '', start_again: true, confirm: sentence } })], {
    'POST /api/native-surveys/database/adventureworks/register': () => { posts += 1;
      return { __status: 409, body: { detail: 'The earlier registration is still running.' } }; },
  }));
  await app.loadSurveyPane();
  const btn = content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-register]`);
  btn.click();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(content.textContent.includes(sentence), 'the confirm says what RE can and cannot check');
  btn.click();                                           // confirmed -> sent -> refused (409)
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(posts, 1);
  assert.equal(btn.dataset.confirm, undefined, 'a refusal disarms the confirm');
  assert.equal(btn.textContent.trim(), 'Start again →');
  assert.ok(!btn.classList.contains('border-state-warn'));
  btn.click();                                           // next click only asks again
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(posts, 1, 'no send without a fresh confirm');
  assert.equal(btn.textContent, 'Confirm: start again');
});

test('the "press again to confirm" line is cleared with the disarm', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  stubFetch(paneRoutes([surveyRow(), catalogRow({
    run: { ...blankRun, state: 'awaiting_registration' },
    register: { available: true, label: 'Start again →', why_not: '', start_again: true, confirm: 'Be sure.' } })]));
  await app.loadSurveyPane();
  const row = content.querySelector(`[data-native-survey="${CATALOG}"]`);
  const btn = row.querySelector('[data-native-register]');
  btn.click();
  await new Promise((r) => setTimeout(r, 5));
  const info = row.querySelector('[data-native-info]');
  assert.ok(info.textContent.includes('Press again to confirm'));
  btn._disarm();                                         // what the timer calls
  assert.equal(btn.dataset.confirm, undefined);
  assert.ok(info.classList.contains('hidden') && info.textContent === '', 'the line goes with the disarm');
});

test('an earlier process that failed is one press: no confirm, the explicit flag still sent', async () => {
  const { document, app } = await setUp();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  let sent = null;
  stubFetch(paneRoutes([surveyRow(), catalogRow({
    run: { ...blankRun, state: 'failed', egeria_status: 'FAILED', message: 'boom' },
    register: { available: true, label: 'Start again →', why_not: '', start_again: true, one_press: true, confirm: '' } })], {
    'POST /api/native-surveys/database/adventureworks/register': (url, opts) => {
      sent = JSON.parse(opts.body);
      return { registered: { projected: {} }, surveys: [catalogRow({ run: { ...blankRun, state: 'registered' } })] };
    },
  }));
  await app.loadSurveyPane();
  content.querySelector(`[data-native-survey="${CATALOG}"] [data-native-register]`).click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(sent, { start_again: true }, 'sent at once: there is nothing to duplicate');
});
