/** A status on screen keeps following the server until a terminal state, and a page that was open across a server
 *  restart (or a long absence) re-reads what it shows. Owner 2026-10-14: a survey row did not change until reload;
 *  "engine host off" stayed on an open page after the server restarted with the host enabled. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

/** A hand clock: setTimeout queues, advance(ms) runs what is due and lets promises settle. */
function clock(t) {
  const realSet = globalThis.setTimeout; const realClear = globalThis.clearTimeout;
  let now = 0; const queue = []; const delays = [];
  globalThis.setTimeout = (fn, ms = 0) => { const id = queue.length + 1; delays.push(ms); queue.push({ id, at: now + ms, fn }); return id; };
  globalThis.clearTimeout = (id) => { const q = queue.find((x) => x.id === id); if (q) q.fn = null; };
  t.after(() => { globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear; });
  const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
  return {
    delays,
    pending: () => queue.filter((q) => q.fn).length,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = queue.filter((q) => q.fn && q.at <= end).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        now = due.at; const f = due.fn; due.fn = null; f(); await settle();
      }
      now = end; await settle();
    },
  };
}

const row = (state, extra = {}) => ({
  qualified_name: 'PostgreSQLSurvey::survey-postgres-database', display_name: 'Survey', kind: 'survey_existing',
  runnable: true, in_flight: ['submitted', 'running'].includes(state),
  run: { state, egeria_status: state === 'running' ? 'IN_PROGRESS' : '', engine_action_guid: 'g-1',
    submitted_at: new Date().toISOString(), read_at: '', error: '', message: state === 'failed' ? 'boom' : '' },
  ...extra,
});

test('followUntil: shows each answer, backs off 2 s then 10 s, survives a failed read, stops at the end', async (t) => {
  makeDomEnvironment();
  const c = clock(t);
  const { followUntil } = await import('/static/next/live-status.js');
  const answers = ['submitted', 'ERR', ...Array(40).fill('running'), 'failed'];
  let i = 0; const shown = []; let reads = 0;
  followUntil({
    read: async () => { reads += 1; const a = answers[i++]; if (a === 'ERR') throw new Error('net'); return a; },
    onUpdate: (a) => shown.push(a),
    isDone: (a) => a === 'failed',
  });
  await c.advance(1000000);
  assert.deepEqual(shown, ['submitted', ...Array(40).fill('running'), 'failed']);
  assert.equal(reads, 43);
  assert.equal(c.pending(), 0, 'no timer left once terminal');
  assert.equal(c.delays[0], 2000);
  assert.ok(c.delays.includes(10000), 'slows to 10 s after the first minute');
  assert.ok(c.delays.every((d) => d === 2000 || d === 10000));
});

test('followUntil with maxMs: a state stuck in flight stops after the cap and says so once', async (t) => {
  makeDomEnvironment();
  const c = clock(t);
  const { followUntil } = await import('/static/next/live-status.js');
  let reads = 0; let gaveUp = 0;
  followUntil({ read: async () => { reads += 1; return 'running'; }, isDone: () => false, maxMs: 600000, onGiveUp: () => { gaveUp += 1; } });
  await c.advance(3600000);
  assert.equal(gaveUp, 1);
  assert.ok(reads < 100, `bounded reads, got ${reads}`);
  assert.equal(c.pending(), 0);
});

async function mountRows(first) {
  const mine = globalThis.fetch;
  const { document } = makeDomEnvironment();
  globalThis.fetch = mine;   // the harness installs a loudly failing stub; these tests serve their own answers
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'coco_pharma', stage: 'discovery', subTab: 'survey' });
  const content = document.createElement('div'); document.body.appendChild(content);
  content.innerHTML = `<div id="native-surveys">${first.map(ns.nativeSurveyRowHtml).join('')}</div>`;
  ns.bindNativeSurveys(content, 'coco_pharma', first);
  return { document, content, app, ns };
}

test('native-survey row follows submitted -> running -> failed with no reload, then stops reading', async (t) => {
  const c = clock(t);
  const answers = [[row('running')], [row('running')], [row('failed')]];
  let reads = 0;
  globalThis.fetch = async (u) => { return { ok: true, status: 200, json: async () => ({ surveys: answers[Math.min(reads++, 2)] }) }; };
  const m = await mountRows([row('submitted')]);
  const text = () => m.content.textContent;
  assert.match(text(), /submitted/);
  await c.advance(2000);
  assert.doesNotMatch(text(), /submitted to Egeria/);
  assert.match(text(), /Egeria says\s*IN_PROGRESS/);
  await c.advance(30000);
  assert.match(text(), /boom/);
  const n = reads;
  await c.advance(120000);
  assert.equal(reads, n, 'no reads after the terminal state');
  m.app.state.subTab = 'questions';
});

const OFF = () => row('not_run', { runnable: false, cannot_run_reason: 'engine host off', in_flight: false,
  run: { state: 'not_run', egeria_status: '', engine_action_guid: '', submitted_at: '', read_at: '', error: '', message: '' } });
const ON = () => row('not_run', { runnable: true, cannot_run_reason: '', in_flight: false,
  run: { state: 'not_run', egeria_status: '', engine_action_guid: '', submitted_at: '', read_at: '', error: '', message: '' } });

test('a server restart (boot id changes) re-reads the rows: "engine host off" becomes runnable', async () => {
  let served = [OFF()];
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ surveys: served }) });
  const m = await mountRows(served);
  const shell = await import('/static/next/shell-status.js');
  assert.match(m.content.textContent, /engine host off/);
  shell.noteBootId('boot-A');
  served = [ON()];
  shell.noteBootId('boot-A');
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.match(m.content.textContent, /engine host off/, 'same boot id: nothing re-read');
  shell.noteBootId('boot-B');
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.doesNotMatch(m.content.textContent, /engine host off/);
  assert.ok(m.content.querySelector('[data-native-run]'), 'the Run control is there');
  m.app.state.subTab = 'questions';
});

test('returning to the tab re-reads the rows', async () => {
  let served = [OFF()];
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ surveys: served }) });
  const m = await mountRows(served);
  served = [ON()];
  Object.defineProperty(m.document, 'visibilityState', { value: 'visible', configurable: true });
  m.document.dispatchEvent(new m.document.defaultView.Event('visibilitychange'));
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.doesNotMatch(m.content.textContent, /engine host off/);
  m.app.state.subTab = 'questions';
});

test('pollActivity rides over a failed read and a claimed status, and ends at the first terminal one', async () => {
  makeDomEnvironment();
  const api = await import('/static/re-api.js');
  const seq = [new Error('net'), { status: 'claimed' }, { status: 'running' }, { status: 'error' }];
  let i = 0;
  globalThis.fetch = async () => {
    const a = seq[i++];
    if (a instanceof Error) throw a;
    return { ok: true, status: 200, json: async () => a };
  };
  const done = await api.pollActivity('x', { intervalMs: 1, slowMs: 1 });
  assert.equal(done.status, 'error');
  assert.equal(i, 4);
});
