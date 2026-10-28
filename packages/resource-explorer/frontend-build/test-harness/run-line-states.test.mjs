/** The run line (designer 2026-10-16 Q5, Q7, Q8; architect ruling item 1). Every word comes from the server's derived
 *  run: the plan counts, the fixable refusal, the refusal's "not run". RE stopping its following is a state of the line
 *  with a control, never "reload". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const { document } = makeDomEnvironment();
ensureLoaderRegistered();
const ns = await import('/static/next/stages/native-surveys.js');
const app = await import('/static/next/app.js');
const norm = (h) => { const d = document.createElement('div'); d.innerHTML = h; return d.textContent.replace(/\s+/g, ' ').trim(); };

const REASON = 'the stored credential has SELECT on 3 of 61 relations';
const run = (extra = {}) => ({
  state: 'partial', line: 'Database Analysis Survey · step 1 postgres_column_profile ○ not run · step 2 postgres_nested_columns ○ not run · step 3 db_derived ✓ report 4a2771da',
  summary: '2 of its 4 steps ran · made under an earlier version of this definition (4 steps, now 8)',
  plan: { recorded: true, steps: 4, definition_steps: 8, differs: true },
  submitted_at: '2026-10-16T14:42:00', read_at: '2026-10-16T14:50:00', error: '',
  steps: [{ report_guid: '' }, { report_guid: '' }, { report_guid: '4a2771da-0000' }],
  problems: [{ step: 1, label: 'postgres_column_profile', kind: 'refused', reason: REASON, credential: true },
    { step: 2, label: 'postgres_nested_columns', kind: 'refused', reason: REASON, credential: true }],
  fixable: { kind: 'credential', steps: [1, 2], reason: REASON, words: `steps 1–2 not run · ${REASON}`, action: 'set the database credential' },
  ...extra });
const row = (r, over = {}) => ({ qualified_name: 'GovActionProcess::DatabaseAnalysisSurvey', kind: 're_definition',
  display_name: 'Database Analysis Survey', runnable: true, in_flight: false, run: r, ...over });

test('the run line counts its own plan and names the version change (architect section 1)', () => {
  const t = norm(ns.nativeSurveyStatusHtml(row(run())));
  assert.match(t, /◐ 2 of its 4 steps ran · made under an earlier version of this definition \(4 steps, now 8\)/);
  const same = norm(ns.nativeSurveyStatusHtml(row(run({ summary: '6 of 8 ran', plan: { recorded: true, steps: 8, definition_steps: 8, differs: false } }))));
  assert.match(same, /◐ 6 of 8 ran/);
  const legacy = norm(ns.nativeSurveyStatusHtml(row(run({ summary: "2 ran · this run's plan wasn't recorded" }))));
  assert.match(legacy, /2 ran · this run's plan wasn't recorded/);
  assert.doesNotMatch(legacy, /2 of/);
});

test('a refused step reads "○ not run" with its reason on demand, and ⊘ is not used for it', () => {
  const h = ns.nativeSurveyStatusHtml(row(run()));
  const t = norm(h);
  assert.match(t, /step 1 postgres_column_profile ○ not run/);
  assert.doesNotMatch(h, /⊘|refused/);
  assert.match(h, /<span hidden\s+data-native-partial-why/);
  assert.match(h, /title="step 1 postgres_column_profile: the stored credential/);
});

test('a credential-gap refusal shows on the run line with the way to where the credential is set', () => {
  const h = ns.nativeSurveyStatusHtml(row(run()));
  const t = norm(h);
  assert.match(t, /steps 1–2 not run · the stored credential has SELECT on 3 of 61 relations · set the database credential ›/);
  assert.ok(/data-native-fix-credential/.test(h));
  const none = ns.nativeSurveyStatusHtml(row(run({ fixable: null })));
  assert.doesNotMatch(none, /data-native-fix-credential/);
});

test('the credential link presses the resource header\'s own "Change credentials" door', async () => {
  const door = document.createElement('button');
  door.dataset.act = 'change-credentials';
  let pressed = 0;
  door.addEventListener('click', () => { pressed += 1; });
  document.body.appendChild(door);
  const root = document.createElement('div');
  root.innerHTML = `<div id="native-surveys">${ns.nativeSurveyRowHtml(row(run()))}</div>`;
  document.body.appendChild(root);
  app.state.selectedSlug = 'coco_pharma'; app.state.subTab = 'survey'; app.state.resourceType = 'db';
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ surveys: [] }) });
  ns.bindNativeSurveys(root, 'coco_pharma', [row(run())]);
  root.querySelector('[data-native-fix-credential]').click();
  assert.equal(pressed, 1);
});

test('a run RE stopped following reads "running · as of HH:MM · stopped checking · check now"', () => {
  const inflight = row({ state: 'running', line: '', read_at: '2026-10-16T14:12:00', submitted_at: '2026-10-16T14:00:00',
    steps: [], problems: [], error: '' }, { in_flight: true });
  assert.equal(ns.nativeSurveyRowHtml(inflight).includes('data-native-stopped'), false, 'followed: no stopped line');
  const h = ns.nativeSurveyRowHtml(inflight, { stopped: true });
  const t = norm(h);
  assert.match(t, /running · as of \d{1,2}:\d{2}(\s?[AP]M)? · stopped checking after 10 minutes · check now/i);
  assert.doesNotMatch(t, /reload/i);
  assert.ok(/<button type="button" data-native-check-now/.test(h), 'check now is a button');
  const done = row({ state: 'complete', line: 'x', steps: [], read_at: '2026-10-16T14:12:00' }, { in_flight: false });
  assert.equal(ns.nativeSurveyRowHtml(done, { stopped: true }).includes('data-native-stopped'), false);
});

test('"check now" reads once, repaints from the server rows, and does not read twice on a second press', async () => {
  const inflight = row({ state: 'running', line: '', read_at: '2026-10-16T14:12:00', submitted_at: '2026-10-16T14:00:00',
    steps: [], problems: [], error: '' }, { in_flight: true, kind: 'survey_existing' });
  const finished = row({ state: 'complete', line: 'Database Analysis Survey · step 1 x ✓ report 4a2771da', steps: [],
    read_at: '2026-10-16T14:20:00', report_guid: 'r' }, { in_flight: false, kind: 'survey_existing' });
  const root = document.createElement('div');
  root.innerHTML = `<div id="native-surveys">${ns.nativeSurveyRowHtml(inflight, { stopped: true })}</div>`;
  document.body.appendChild(root);
  app.state.selectedSlug = 'coco_pharma'; app.state.subTab = 'survey'; app.state.resourceType = 'db';
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push(`${(opts.method || 'GET')} ${url}`);
    return { ok: true, status: 200, json: async () => ({ surveys: [finished] }) };
  };
  ns.bindNativeSurveys(root, 'coco_pharma', [inflight]);
  const b = root.querySelector('[data-native-check-now]');
  b.click(); b.click();
  assert.equal(b.textContent.trim(), 'checking…');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls.filter((c) => c.includes('/refresh')).length, 1, 'one read');
  assert.equal(root.querySelector('[data-native-stopped]'), null, 'the repaint is the server\'s row');
  assert.match(norm(root.innerHTML), /step 1 x ✓ report 4a2771da/);
});
