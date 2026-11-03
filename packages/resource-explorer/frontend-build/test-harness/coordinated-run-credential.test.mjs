/** One-run credential for an Egeria-coordinated run (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.1, §2.3, §2.4).
 *  "with options…" beside "Run · coordinated by Egeria →" opens the EXISTING credential dialog, and Go sends the
 *  credential in the body of the coordinated start call only. The run line draws "ran as <user> · this run", the
 *  per-step stored-credential words for steps Egeria runs itself, and the lost-after-restart line, from the server's
 *  derived row only. Every value here is invented. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
makeDomEnvironment();
const ns = await import('/static/next/stages/native-surveys.js');
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const QN = 'GovActionProcess::DatabaseAnalysisSurvey';
const cand = (ctl) => ({ qualified_name: QN, display_name: 'Database Analysis Survey',
  coordinated_by_egeria: { label: 'coordinated by Egeria', available: true, why_not: '', state: '', line: '', ...ctl } });

test('"with options…" sits beside the coordinated control, and not while a run is in flight', () => {
  const h = ns.coordinateControlHtml(cand({}));
  assert.match(h, /data-coordinate-egeria-options="GovActionProcess::DatabaseAnalysisSurvey"/);
  assert.match(text(h), /coordinated by Egeria → with options…$/);
  assert.doesNotMatch(ns.coordinateControlHtml(cand({ state: 'running' })), /data-coordinate-egeria-options/);
  assert.doesNotMatch(ns.coordinateControlHtml(cand({ available: false, why_not: 'engine host off' })), /data-coordinate-egeria-options/);
});

test('Go in the existing dialog posts {db_user, db_pwd} in the start call body only; a plain Run sends none', async () => {
  const { state } = await import('/static/next/app.js');
  state.resourceType = 'db';
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
    return { ok: true, status: 200, json: async () => ({ run: { state: 'submitted', engine_action_guid: 'pi-1' }, surveys: [] }) };
  };
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({}));
  document.body.appendChild(host);
  ns.bindCoordinateControls(host, 'coco_pharma');
  host.querySelector('[data-coordinate-egeria-options]').click();
  const body = document.querySelector('#wl-detail-body');
  assert.ok(body.querySelector('[data-run-override-user]'), 'the existing credential dialog opened');
  assert.equal(calls.length, 0, 'nothing is sent until Go');
  body.querySelector('[data-run-override-user]').value = 'one_off_user';
  body.querySelector('[data-run-override-password]').value = 'invented-pw';
  body.querySelector('[data-act="go"]').click();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/api\/native-surveys\/database\/coco_pharma\/run$/);
  assert.doesNotMatch(calls[0].url, /invented-pw|one_off_user/, 'never in the URL');
  assert.deepEqual(calls[0].body, { process_qualified_name: QN, db_user: 'one_off_user', db_pwd: 'invented-pw' });
  assert.doesNotMatch(document.body.innerHTML, /invented-pw/, 'never in the markup');
  assert.match(host.querySelector('[data-coordinate-egeria]').textContent, /● started/);

  const plain = document.createElement('div');
  plain.innerHTML = ns.coordinateControlHtml(cand({}));
  ns.bindCoordinateControls(plain, 'coco_pharma');
  plain.querySelector('[data-coordinate-egeria]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls[1].body, { process_qualified_name: QN });
});

const line = (over = {}) => ({ state: 'running', line: 'Database Analysis Survey · step 1 postgres_column_profile running',
  steps: [], run_credential: { user: 'elevated_reader', scope: 'this run' }, ran_as: null, native_steps: [],
  credential_lost: false, ...over });

test('the run line says "ran as <user> (scope)" only once a step ran as it, in the override runs\' own form', async () => {
  const { analysisRanAsHtml } = await import('/static/next/app.js');
  assert.doesNotMatch(text(ns.processLineHtml(line())), /ran as/);
  const h = ns.processLineHtml(line({ ran_as: { user: 'elevated_reader', scope: 'this run' } }));
  assert.match(text(h), /ran as elevated_reader \(this run\)/);
  assert.match(h, /ran as <span class="font-mono">elevated_reader<\/span> \(this run\)/);
  assert.match(h, /data-run-credential-ran-as/);
  // the same words RE's own override rows draw (app.js analysisRanAsHtml)
  const own = analysisRanAsHtml({ last_run_ran_as: { user: 'elevated_reader', scope: 'this run' } });
  assert.ok(own.includes(ns.ranAsHtml({ user: 'elevated_reader', scope: 'this run' })));
  assert.doesNotMatch(text(ns.processLineHtml(line({ run_credential: null, ran_as: null }))), /ran as|stored credential|longer holds/);
});

test('a held-credential run line says its capability was not assessed, never the stored login\'s', () => {
  const h = ns.processLineHtml(line({ ran_as: { user: 'elevated_reader', scope: 'this run',
    capability: 'not assessed for this run\'s credential' } }));
  assert.match(text(h), /ran as elevated_reader \(this run\) · capability not assessed for this run(&apos;|')s credential/);
  assert.match(h, /data-run-credential-capability/);
});

test('a step Egeria runs itself says it uses the stored credential; a lost credential says so in the proven words', () => {
  const native = ns.processLineHtml(line({ native_steps: [1] }));
  assert.match(text(native), /step 1 runs in Egeria with the stored credential/);
  const lost = ns.processLineHtml(line({ state: 'partial', credential_lost: true }));
  assert.match(text(lost), /this Resource Explorer process no longer holds the one-run credential \(restarted, timed out, or the step ran in another process\)/);
  assert.match(lost, /data-run-credential-lost/);
  const restarted = ns.processLineHtml(line({ state: 'partial', credential_lost: true,
    credential_lost_words: 'this Resource Explorer process no longer holds the one-run credential (Resource Explorer restarted)' }));
  assert.match(text(restarted), /no longer holds the one-run credential \(Resource Explorer restarted\)/);
  assert.doesNotMatch(text(restarted), /timed out/);
});

test('"with options…" opens the existing dialog with the coordinated subtitle and the native-steps note', () => {
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({ native_steps: [1, 3] }));
  document.body.appendChild(host);
  ns.bindCoordinateControls(host, 'coco_pharma');
  host.querySelector('[data-coordinate-egeria-options]').click();
  const body = document.querySelector('#wl-detail-body');
  assert.equal(body.querySelector('[data-run-override-subtitle]').textContent,
    "Egeria runs this definition; RE's own steps use this credential once; it is not stored");
  assert.deepEqual([...body.querySelectorAll('[data-run-override-note]')].map((n) => n.textContent),
    ['step 1 runs in Egeria with the stored credential', 'step 3 runs in Egeria with the stored credential']);
  assert.doesNotMatch(body.textContent, /once, here/, 'not the definition card\'s copy');
  assert.ok(body.querySelector('[data-run-override-user]'), 'the existing inputs');
});

test('the definition card\'s dialog copy is unchanged; no native step, no note', async () => {
  const { openAnalysisOverrideDialog } = await import('/static/next/app.js');
  openAnalysisOverrideDialog({ name: 'privilege_audit', analysis_id: 'privilege_audit' }, 'coco_pharma', () => {});
  const body = document.querySelector('#wl-detail-body');
  assert.match(body.textContent, /Runs privilege_audit once, here, with the credential below/);
  assert.equal(body.querySelector('[data-run-override-subtitle]'), null);
  assert.equal(body.querySelector('[data-run-override-note]'), null);
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({ native_steps: [] }));
  document.body.appendChild(host);
  ns.bindCoordinateControls(host, 'coco_pharma');
  host.querySelector('[data-coordinate-egeria-options]').click();
  assert.equal(document.querySelector('#wl-detail-body [data-run-override-note]'), null);
});

test('every word is in one block (designer placeholders, Q3 open)', () => {
  const w = ns.RUN_CREDENTIAL_WORDS;
  assert.equal(w.optionsLabel, 'with options…');
  assert.equal(w.nativeStep(2), 'step 2 runs in Egeria with the stored credential');
  assert.equal(w.lost, 'this Resource Explorer process no longer holds the one-run credential (restarted, timed out, or the step ran in another process)');
  assert.equal(w.dialogSubtitle, "Egeria runs this definition; RE's own steps use this credential once; it is not stored");
  assert.equal(w.capabilityNotAssessed, "not assessed for this run's credential");
});

test('a start whose marker was not recorded shows a warn cue with its words, never the plain "started" tick', async () => {
  const { state } = await import('/static/next/app.js');
  state.resourceType = 'db';
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({
    run: { state: 'submitted', engine_action_guid: 'pi-1',
      run_credential_marker_error: 'started; one-run credential marker not recorded (registry gone)' },
    surveys: null }) });
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({}));
  document.body.appendChild(host);
  ns.bindCoordinateControls(host, 'coco_pharma');
  host.querySelector('[data-coordinate-egeria]').click();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const b = host.querySelector('[data-coordinate-egeria]');
  const cue = b.querySelector('[data-coordinate-marker-warn]');
  assert.ok(cue, 'the warn cue is drawn');
  assert.match(cue.className, /text-state-warn/);
  assert.equal(cue.textContent, ns.RUN_CREDENTIAL_WORDS.markerNotRecorded);
  assert.match(b.textContent, /◐/);
  assert.doesNotMatch(b.innerHTML, /text-state-ok/);
  assert.match(b.title, /marker not recorded/);
});
