/** Cancel on a waiting or running Egeria survey row: shown only while Egeria last said waiting/running; a
 *  confirm; an acknowledgement ("◔ cancelling…"); the result validated from the server's read-back; a failure
 *  as a pop-up AND a cue that stays on the row. */
import assert from 'node:assert/strict';
import { test, afterEach } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SURVEY = 'PostgreSQLSurvey::survey-postgres-database';
const GUID = 'eeeeeeee-1111-2222-3333-444444444444';
const ago = (ms) => new Date(Date.now() - ms).toISOString();
const row = (run, extra = {}) => ({
  cancellable: ['IN_PROGRESS', 'WAITING', 'REQUESTED', 'APPROVED', 'ACTIVATING'].includes(run.egeria_status) && !run.error,
  qualified_name: SURVEY, display_name: 'Survey PostgreSQL Database', kind: 'survey_existing', description: '',
  runnable: true, cannot_run_reason: '', in_flight: ['running', 'submitted'].includes(run.state),
  run: { state: 'not_run', egeria_status: '', message: '', submitted_at: ago(3600e3), read_at: ago(60e3), report_at: '',
    report_guid: '', engine_action_guid: GUID, annotation_count: null, error: '', phase: '', stalled: false, ...run },
  ...extra,
});
const RUNNING = row({ state: 'running', egeria_status: 'IN_PROGRESS', phase: 'running' });
const CANCELLED = row({ state: 'failed', egeria_status: 'CANCELLED', message: 'cancelled in Egeria' });
const STILL = row({ state: 'running', egeria_status: 'IN_PROGRESS', phase: 'running' });

function stub(routes) {
  routes = { 'POST /api/native-surveys/database/adventureworks/refresh': () => ({ surveys: [RUNNING] }), ...routes };
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    calls.push(`${method} ${url}`);
    const key = Object.keys(routes).find((k) => { const [m, sub] = k.split(' '); return m === method && String(url).includes(sub); });
    if (!key) throw new Error(`_UNSTUBBED_FETCH ${method} ${url}`);
    const r = typeof routes[key] === 'function' ? routes[key](url, opts) : routes[key];
    const status = r && r.__status ? r.__status : 200;
    return { ok: status < 400, status, statusText: 'x', json: async () => (r && r.__status ? r.body : r) };
  };
  return calls;
}

async function setUp() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'adventureworks', stage: 'discovery', subTab: 'survey' });
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  appRef = app;
  return { document, app, ns, content };
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const wait = (ms = 10) => new Promise((r) => setTimeout(r, ms));
let appRef = null;
// leave the survey tab so the row poll stops rescheduling itself
afterEach(async () => { if (appRef) { appRef.state.subTab = 'questions'; await wait(80); } });
const CANCEL_URL = `POST /api/native-surveys/database/adventureworks/egeria-actions/${GUID}/cancel`;

function mount(ns, content, rows) {
  content.innerHTML = `<div id="native-surveys">${rows.map(ns.nativeSurveyRowHtml).join('')}</div>`;
  ns.bindNativeSurveys(content, 'adventureworks', rows, { pollMs: 25 });
}

test('the control shows while Egeria says waiting or running, and for nothing else', async () => {
  const { ns } = await setUp();
  for (const [state, st] of [['running', 'IN_PROGRESS'], ['running', 'WAITING'], ['running', 'REQUESTED']]) {
    assert.match(ns.cancelControlHtml(row({ state, egeria_status: st })), /data-native-cancel="/, st);
  }
  const hidden = [['complete', 'COMPLETED'], ['failed', 'FAILED'], ['failed', 'CANCELLED'], ['submitted', ''], ['not_run', ''], ['unreadable', '']];
  for (const [state, st] of hidden) assert.equal(ns.cancelControlHtml(row({ state, egeria_status: st })), '', `${state} ${st}`);
  assert.equal(ns.cancelControlHtml({ ...RUNNING, run: { ...RUNNING.run, engine_action_guid: '' } }), '');
});

test('Cancel is never offered when the server says the row is not cancellable, or the last read failed', async () => {
  const { ns } = await setUp();
  // a process run or the registration row: live status, but the server's allowlist says no
  assert.equal(ns.cancelControlHtml(row({ state: 'running', egeria_status: 'IN_PROGRESS' }, { cancellable: false })), '');
  // the last read failed: not offered even with a live stored status
  assert.equal(ns.cancelControlHtml(row({ state: 'running', egeria_status: 'IN_PROGRESS', error: 'connection refused' })), '');
  // and the control follows the server flag only
  assert.equal(ns.cancelControlHtml({ ...RUNNING, cancellable: undefined }), '');
  const reg = ns.nativeSurveyRowHtml({ ...RUNNING, kind: 'catalog_and_survey', cancellable: false });
  assert.doesNotMatch(reg, /data-native-cancel/);
});

test('a cancel that lands on an action with a report tells the person Egeria may have finished', async () => {
  const { document, ns, content } = await setUp();
  stub({ [CANCEL_URL]: { cancelled: true, egeria_status: 'CANCELLED', note: 'Egeria may already have finished · the report may exist', surveys: [CANCELLED] } });
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait(40);
  assert.match(text(document.getElementById('wl-detail')), /Egeria may already have finished · the report may exist/);
});

test('"cancelling…" survives a poll repaint while the request is in flight', async () => {
  const { ns, content } = await setUp();
  let release;
  const gate = new Promise((r) => { release = r; });
  stub({ [CANCEL_URL]: async () => { await gate; return { cancelled: true, egeria_status: 'CANCELLED', surveys: [CANCELLED] }; } });
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait(60);                                   // a poll repaint (25ms) happens while the cancel is pending
  assert.match(text(content.querySelector('[data-native-cancel-ack]')), /◔ cancelling…/);
  assert.equal(content.querySelector('[data-native-cancel-ack]').hidden, false);
  release(); await wait(40);
});

test('Cancel asks first ("cancel this survey in Egeria?") and "keep it" backs out with nothing sent', async () => {
  const { ns, content } = await setUp();
  const calls = stub({});
  mount(ns, content, [RUNNING]);
  const box = content.querySelector('[data-native-cancel-box]');
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, true);
  content.querySelector('[data-native-cancel]').click();
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, false);
  assert.match(text(box), /cancel this survey in Egeria\?/);
  box.querySelector('[data-native-cancel-no]').click();
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, true);
  assert.equal(calls.length, 0);
});

test('Yes: acknowledged at once ("◔ cancelling…"), then the row shows what the read-back said', async () => {
  const { ns, content } = await setUp();
  let release;
  const gate = new Promise((r) => { release = r; });
  const calls = stub({ [CANCEL_URL]: async () => { await gate; return { cancelled: true, egeria_status: 'CANCELLED', surveys: [CANCELLED] }; } });
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait();
  assert.match(text(content.querySelector('[data-native-cancel-ack]')), /◔ cancelling…/, 'acknowledged before any result');
  assert.equal(content.querySelector('[data-native-cancel-ack]').hidden, false);
  release(); await wait(30);
  assert.equal(calls.filter((c) => c.startsWith('POST')).length, 1);
  assert.match(text(content.querySelector('[data-native-status]')), /^cancelled in Egeria/);
  assert.doesNotMatch(text(content.querySelector('[data-native-status]')), /[✓✗✕]/, 'a cancel is neutral: no tick, no cross');
  assert.equal(content.querySelector('[data-native-cancel]'), null, 'no cancel control on a cancelled action');
});

test('a cancel Egeria does not confirm is not "cancelled": a pop-up with the reason, and a cue that stays', async () => {
  const { document, ns, content } = await setUp();
  stub({ [CANCEL_URL]: { cancelled: false, egeria_status: 'IN_PROGRESS', surveys: [STILL] } });
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait(30);
  assert.match(text(document.getElementById('wl-detail')), /asked Egeria to cancel; it still says IN_PROGRESS/);
  assert.doesNotMatch(text(content.querySelector('[data-native-status]')), /cancelled/);
  // Pending, not refused: the definition card's pending cue ◔, never ◐ ("measured, within a limit").
  const cue = content.querySelector('[data-native-cancel-cue]');
  assert.match(text(cue), /^◔ asked Egeria to cancel; it still says IN_PROGRESS/);
  assert.ok(!cue.textContent.includes('◐'));
});

test('an Egeria error is the pop-up and the cue; the cue survives a repaint; a new attempt clears it', async () => {
  const { document, ns, content } = await setUp();
  let fail = true;
  stub({ [CANCEL_URL]: () => (fail ? { __status: 502, body: { detail: 'Egeria did not cancel the action: engine host unreachable', unsent: true } }
    : { cancelled: true, egeria_status: 'CANCELLED', surveys: [CANCELLED] }) });
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait(30);
  // Designer (cancel words §6): a cancel that was not sent says so in the definition card's words, with no glyph.
  assert.match(text(document.getElementById('wl-detail')), /cancel not sent · couldn't reach Egeria · try again/);
  assert.equal(text(content.querySelector('[data-native-cancel-cue]')), "cancel not sent · couldn't reach Egeria · try again");
  // a repaint from the server's rows keeps the cue while the action is still live
  content.querySelector('#native-surveys').innerHTML = ns.nativeSurveyRowHtml(RUNNING);
  assert.match(text(content.querySelector('[data-native-cancel-cue]')), /cancel not sent/);
  // trying again, and succeeding, leaves no stale cue on the cancelled row
  fail = false;
  document.getElementById('wl-detail').remove();
  mount(ns, content, [RUNNING]);
  content.querySelector('[data-native-cancel]').click();
  content.querySelector('[data-native-cancel-yes]').click();
  await wait(30);
  assert.equal(content.querySelector('[data-native-cancel-cue]'), null);
});


// Designer REPLY-DESIGNER-ENGINE-HOST-CANCEL-WORDS §6: the three outcome words, no glyph on any of them (◐ means
// "measured, within a limit" in glyphs.js, not "your request didn't go through"). Same block as the definition card.
for (const [name, status, detail, words, extra = { by: 'egeria' }] of [
  ['too late', 409, 'Only a waiting or running action can be cancelled; Egeria now says COMPLETED.',
    'too late to cancel · the run had already finished'],
  ['not permitted', 403, 'user dana is not authorized to update',
    "Egeria won't let you cancel this run · user dana is not authorized to update"],
  // A real Egeria security refusal: the server marks it `refused_by: egeria` and re-api.js prefixes "refused by
  // Egeria: ". The words must carry the unprefixed sentence once, never the doubled line.
  ['not permitted, a real Egeria 403', 403, 'user dana is not authorized to update',
    "Egeria won't let you cancel this run · user dana is not authorized to update", { refused_by: 'egeria' }],
  ['refused with a reason', 422, 'the action is not in a state that can be cancelled',
    'Egeria refused the cancel: the action is not in a state that can be cancelled'],
  ['not sent', 502, 'Egeria did not cancel the action: engine host unreachable',
    "cancel not sent · couldn't reach Egeria · try again", { unsent: true }],
]) {
  test(`a refused native cancel (${name}) uses the designer's words and draws no glyph`, async () => {
    const { ns, content } = await setUp();
    stub({ [CANCEL_URL]: { __status: status, body: { detail, ...extra } } });
    mount(ns, content, [RUNNING]);
    content.querySelector('[data-native-cancel]').click();
    content.querySelector('[data-native-cancel-yes]').click();
    await wait(30);
    const cue = content.querySelector('[data-native-cancel-cue]');
    assert.equal(text(cue), words);
    assert.ok(!/[◐◔◌⊘✕?]/.test(cue.textContent), 'no glyph on a refused or unsent cancel');
    assert.equal(cue.querySelector('.font-glyph'), null);
    assert.ok(!/refused by Egeria/i.test(cue.textContent), 'the prefix is not doubled onto the designer words');
    // the run's own cue is unchanged by the refusal: still the running row, Cancel still offered
    assert.ok(content.querySelector('[data-native-cancel]'));
  });
}

// RE's own gate refusals (no `by: egeria`) are shown verbatim, never as Egeria's answer.
for (const [name, status, detail] of [
  ['the access rule', 403, 'zone sales-zone does not grant dana update'],
  ['the single-action gate', 409, 'Only a single-action survey can be cancelled from here; this one was started as a process or a registration, whose identifier is not an engine action.'],
  ['the unreadable fresh read', 502, "could not read Egeria's current status · not cancelled"],
  ['the sign-in gate', 403, 'sign in to curate'],
]) {
  test(`an RE-gate refusal (${name}) is shown verbatim, not as an Egeria answer`, async () => {
    const { ns, content } = await setUp();
    stub({ [CANCEL_URL]: { __status: status, body: { detail } } });
    mount(ns, content, [RUNNING]);
    content.querySelector('[data-native-cancel]').click();
    content.querySelector('[data-native-cancel-yes]').click();
    await wait(30);
    const cue = content.querySelector('[data-native-cancel-cue]');
    assert.equal(text(cue), detail);
    assert.ok(!/too late|won't let you|Egeria refused|cancel not sent/.test(cue.textContent));
    assert.equal(cue.querySelector('.font-glyph'), null);
  });
}
