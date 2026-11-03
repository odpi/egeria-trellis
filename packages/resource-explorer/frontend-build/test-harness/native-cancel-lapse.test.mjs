/** The cancel confirm is a state of the control and lapses (CONFIRM_MS, as the register confirm does). Its own
 *  file: it mocks setTimeout, which the other cancel tests' real waits cannot share. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const GUID = 'eeeeeeee-1111-2222-3333-444444444444';
const RUNNING = {
  cancellable: true, qualified_name: 'PostgreSQLSurvey::survey-postgres-database', display_name: 'Survey', kind: 'survey_existing',
  runnable: true, in_flight: true,
  run: { state: 'running', egeria_status: 'IN_PROGRESS', engine_action_guid: GUID, submitted_at: new Date().toISOString(), read_at: new Date().toISOString(), error: '' },
};

test('the confirm lapses after CONFIRM_MS and the Cancel control comes back', async (t) => {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ surveys: [RUNNING] }) });
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'adventureworks', stage: 'discovery', subTab: 'survey' });
  // A hand-rolled clock (node's mock timers trip over the DOM harness): setTimeout queues, tick() runs what is due.
  const realSet = globalThis.setTimeout; const realClear = globalThis.clearTimeout;
  let now = 0; const queue = [];
  globalThis.setTimeout = (fn, ms = 0) => { const id = queue.length + 1; queue.push({ id, at: now + ms, fn }); return id; };
  globalThis.clearTimeout = (id) => { const q = queue.find((x) => x.id === id); if (q) q.fn = null; };
  t.after(() => { globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear; });
  const tick = (ms) => { now += ms; for (const q of queue) if (q.fn && q.at <= now) { const f = q.fn; q.fn = null; f(); } };
  const content = document.createElement('div'); document.body.appendChild(content);
  content.innerHTML = `<div id="native-surveys">${ns.nativeSurveyRowHtml(RUNNING)}</div>`;
  ns.bindNativeSurveys(content, 'adventureworks', [RUNNING], { pollMs: 600000 });
  const box = content.querySelector('[data-native-cancel-box]');
  content.querySelector('[data-native-cancel]').click();
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, false);
  tick(5900);
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, false, 'still armed just before the limit');
  tick(200);
  assert.equal(box.querySelector('[data-native-cancel-confirm]').hidden, true);
  assert.equal(box.querySelector('[data-native-cancel]').hidden, false);
  app.state.subTab = 'questions';
});
