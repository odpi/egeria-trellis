/** Registration states (designer 2026-10-16, Q9): every word comes from the server's persisted-proof fields.
 *  - "registered" alone says Egeria hasn't connected yet; a failed connection says so and names the address tried;
 *  - a refused survey names the address Egeria tried (the localhost clause only when the server says so);
 *  - "will change" is the Egeria lane of the row, with Register again;
 *  - an inherited Egeria-visible host is muted, one that differs is in ink. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const blankRun = { state: 'not_run', egeria_status: '', message: '', submitted_at: '', read_at: '',
  report_at: '', report_guid: '', engine_action_guid: '', annotation_count: null, error: '' };
const catalog = (over = {}) => ({
  qualified_name: 'PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess', display_name: 'Catalog and Survey',
  kind: 'catalog_and_survey', target: 'server', description: '', runnable: false, cannot_run_reason: '',
  neutral: false, stale: false, in_flight: false, run: { ...blankRun, state: 'registered' }, reach_note: '',
  asset_guid: 'dddddddd-0000-0000-0000-000000000001',
  register: { available: false, label: 'Register the server with Egeria →', why_not: '' }, ...over,
});
const survey = (over = {}) => ({
  qualified_name: 'PostgreSQLSurvey::survey-postgres-server', display_name: 'Survey PostgreSQL Server',
  kind: 'survey_existing', target: 'server', description: '', runnable: true, cannot_run_reason: '', neutral: false,
  stale: false, in_flight: false, run: { ...blankRun }, reach_note: '', ...over,
});

async function render(rows) {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const ns = await import('/static/next/stages/native-surveys.js');
  const host = document.createElement('div');
  host.innerHTML = rows.map(ns.nativeSurveyRowHtml).join('');
  document.body.appendChild(host);
  return { host, text: host.textContent.replace(/\s+/g, ' ') };
}

test('registered with no proof of reach says Egeria has not connected yet', async () => {
  const { text } = await render([catalog({ reach: { state: 'not_yet', words: "Egeria hasn't connected yet" } })]);
  assert.match(text, /registered with Egeria · Egeria hasn't connected yet/);
});

test('registered after a connection failure says Egeria could not connect and the address it tried', async () => {
  const { host, text } = await render([catalog({ reach: { state: 'failed', words: "Egeria couldn't connect", address: 'localhost:5442' } })]);
  assert.match(text, /Egeria couldn't connect \(tried localhost:5442\)/);
  assert.equal(host.querySelector('[data-native-reach-state]').dataset.nativeReachState, 'failed');
});

test('registered with a survey that connected adds nothing: registered means only that', async () => {
  const { host } = await render([catalog({ reach: { state: 'connected', words: 'Egeria has connected' } })]);
  assert.equal(host.querySelector('[data-native-reach-state]'), null);
});

test('a refused survey names the address Egeria tried; the localhost clause only when the server says so', async () => {
  const failed = { ...blankRun, state: 'failed', egeria_status: 'FAILED', message: 'Connection to localhost:5442 refused' };
  const own = await render([survey({ run: failed, tried: { address: 'localhost:5442', own_machine: true,
    words: "Egeria tried localhost:5442, and localhost is Egeria's own machine" } })]);
  assert.match(own.text, /Egeria tried localhost:5442, and localhost is Egeria's own machine · change the Egeria-visible host/);
  assert.ok(own.host.querySelector('[data-native-change-host]'));
  const other = await render([survey({ run: failed, tried: { address: 'db.internal:5432', own_machine: false, words: '' } })]);
  assert.match(other.text, /Egeria tried db\.internal:5432 ·/);
  assert.doesNotMatch(other.text, /own machine/);
  const none = await render([survey({ run: failed, tried: {} })]);
  assert.equal(none.host.querySelector('[data-native-tried]'), null);
});

test('will change: the Egeria lane names what Egeria holds and Register again sends the new address', async () => {
  const { host, text } = await render([catalog({
    address: { current: 'localhost:5432', held: 'host.docker.internal:5432', will_change: true },
    register: { available: true, label: 'Register again →', why_not: '' } })]);
  assert.match(text, /⚠ will change · Egeria has host\.docker\.internal:5432 · Register again → sends localhost:5432/);
  assert.equal(host.querySelector('[data-native-register]').textContent.trim(), 'Register again →');
  const quiet = await render([catalog({ address: { current: 'a:1', held: 'a:1', will_change: false } })]);
  assert.equal(quiet.host.querySelector('[data-native-will-change]'), null);
});

test('an inherited Egeria-visible host is muted; one that differs is in ink, and saving redraws the cue', async () => {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const mod = await import('/static/next/egeria-host-edit.js');
  const host = document.createElement('div');
  host.innerHTML = mod.egeriaHostLineHtml('database', 'd1', '', 'host.docker.internal')
    + mod.egeriaHostLineHtml('database', 'd2', 'localhost', 'host.docker.internal')
    + mod.egeriaHostLineHtml('server', 's1', 'host.docker.internal');
  const [inherits, differs, server] = [...host.querySelectorAll('[data-eh-value]')];
  assert.equal(inherits.textContent, 'host.docker.internal');
  assert.ok(inherits.classList.contains('text-ink-muted') && !inherits.classList.contains('text-ink'));
  assert.match(inherits.parentElement.textContent, /\(from its server\)/);
  assert.ok(differs.classList.contains('text-ink') && !differs.classList.contains('text-ink-muted'));
  assert.match(differs.parentElement.textContent, /differs from its server/);
  assert.equal(server.dataset.ehInherited, undefined, 'a server line has no inheritance cue');
  // same value as its server is inherited, whatever the stored string
  const same = document.createElement('div');
  same.innerHTML = mod.egeriaHostLineHtml('database', 'd3', 'host.docker.internal', 'host.docker.internal');
  assert.equal(same.querySelector('[data-eh-value]').dataset.ehInherited, '1');
});
