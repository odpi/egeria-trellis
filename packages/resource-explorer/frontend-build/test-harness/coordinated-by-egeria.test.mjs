/** Engine host case 3: the "coordinated by Egeria" control beside a Survey Definition's Run (owner, 2026-10-15).
 *  Never labelled "run in Egeria". Hidden, with a short reason, when the server says it cannot be offered. Its state
 *  is on the control: pressed -> starting -> started, or not started with the sentence on demand; "started" comes
 *  from the server's answer (derived from the persisted row), never from the click. */
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

test('available: the lower card button reads "Run · coordinated by Egeria →" (Re-run once it has run) and is a bordered button', () => {
  const h = ns.coordinateControlHtml(cand({}));
  // The one-run credential's "with options…" sits beside it (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.1).
  assert.equal(text(ns.coordinateControlHtml({ ...cand({}), last_run_at: '2026-10-15T09:00:00' })), 'Re-run · coordinated by Egeria → with options…');
  assert.match(h, /data-coordinate-egeria="GovActionProcess::DatabaseAnalysisSurvey"/);
  assert.equal(text(h), 'Run · coordinated by Egeria → with options…');
  assert.match(h, /border-accent/);
});

const reRow = (ctl, run) => ({ ...cand(ctl), kind: 're_definition', runnable: true, run: run || { state: 'complete' } });

test('the top card (re_definition row) carries the same control, labelled like its neighbours', () => {
  const h = ns.nativeSurveyRowHtml(reRow({}));
  assert.match(h, /data-coordinate-egeria="GovActionProcess::DatabaseAnalysisSurvey"/);
  assert.match(text(h), /Re-run · coordinated by Egeria →/);
  assert.doesNotMatch(h, /data-native-run=/);
  const fresh = ns.nativeSurveyRowHtml(reRow({}, { state: 'not_run' }));
  assert.match(text(fresh), /Run · coordinated by Egeria →/);
  assert.doesNotMatch(text(fresh), /Re-run|run in egeria/i);
});

test('the top card shows the in-flight state, and a DISABLED button with the reason beside it when not dispatchable', () => {
  const live = ns.nativeSurveyRowHtml(reRow({ state: 'running', line: 'step 1' }));
  assert.match(live, /data-coordinate-egeria="[^"]*"[^>]*disabled/);
  assert.match(text(live), /◔ Re-run · coordinated by Egeria → · running/);
  const why = "Egeria can't start this · step 1 has no executor in Egeria";
  const off = ns.nativeSurveyRowHtml(reRow({ available: false, why_not: why }));
  assert.match(off, /data-coordinate-egeria="[^"]*"[^>]*disabled/);
  assert.match(text(off), /Re-run · coordinated by Egeria → Egeria can.{1,6}t start this · step 1 has no executor in Egeria/);
  assert.match(off, /title="Egeria can.{1,6}t start this · step 1 has no executor in Egeria"/);
  const lower = ns.coordinateControlHtml(cand({ available: false, why_not: 'could not check with Egeria' }));
  assert.match(lower, /disabled/);
  assert.match(text(lower), /could not check with Egeria/);
});

test('engine host off: no button on any row, the short reason once for the pane', () => {
  const off = cand({ available: false, why_not: 'engine host off' });
  assert.match(ns.coordinateControlHtml(off), /disabled/);      // disabled on the card, never hidden
  const note = ns.coordinateOffNoteHtml([off, { ...off, qualified_name: 'GovActionProcess::DatabaseScoutingSurvey' }]);
  assert.equal((note.match(/data-coordinate-off/g) || []).length, 1);
  assert.match(text(note), /^◌ coordinated by Egeria · engine host off$/);
  // rows that offer it, or that it does not belong to (repository definitions), add nothing
  assert.equal(ns.coordinateOffNoteHtml([cand({})]), '');
  assert.equal(ns.coordinateOffNoteHtml([{ qualified_name: 'GovActionProcess::RepoScoutingSurvey' }]), '');
});

test('a row that cannot offer it is explained even when another row can; each distinct reason once', () => {
  const unpublished = cand({ available: false, why_not: 'not registered with Egeria · publish it first' });
  const one = ns.coordinateOffNoteHtml([cand({}), unpublished]);
  assert.equal((one.match(/data-coordinate-off/g) || []).length, 1);
  assert.match(text(one), /coordinated by Egeria · not registered with Egeria/);
  const two = ns.coordinateOffNoteHtml([unpublished, cand({ available: false, why_not: 'engine host off' }), unpublished]);
  assert.equal((two.match(/data-coordinate-off/g) || []).length, 2);
});

test('a run Egeria is coordinating shows on the control, from the persisted state', () => {
  const h = ns.coordinateControlHtml(cand({ state: 'running', line: 'Database Analysis Survey · step 1 ... running' }));
  assert.match(h, /disabled/);
  assert.match(h, /aria-pressed="true"/);
  assert.match(text(h), /◔ Re-run · coordinated by Egeria → · running/);
});

test('no server answer about the control: nothing is drawn', () => {
  assert.equal(ns.coordinateControlHtml({ qualified_name: QN }), '');
});

test('pressed -> starting -> started, from the server answer; a second press sends nothing', async () => {
  const { state } = await import('/static/next/app.js');
  state.resourceType = 'db';
  let release;
  const calls = [];
  globalThis.fetch = (url, init) => {
    calls.push([String(url), init && init.method]);
    return new Promise((resolve) => { release = () => resolve({ ok: true, status: 200,
      json: async () => ({ run: { state: 'submitted', engine_action_guid: 'pi-1', line: 'Database Analysis Survey · step 1 postgres_column_profile not started yet' }, surveys: [] }) }); });
  };
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({}));
  document.body.appendChild(host);
  ns.bindCoordinateControls(host, 'coco_pharma');
  const b = host.querySelector('[data-coordinate-egeria]');
  b.click();
  assert.equal(b.disabled, true);
  assert.equal(b.getAttribute('aria-pressed'), 'true');
  assert.match(b.textContent, /starting…/);
  b.click();                                    // the second press while starting
  assert.equal(calls.length, 1);
  assert.match(calls[0][0], /\/api\/native-surveys\/database\/coco_pharma\/run$/);
  release();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.match(b.textContent, /● started/);
  assert.match(b.title, /Database Analysis Survey/);
  b.click();                                    // still pressed: nothing more is sent
  assert.equal(calls.length, 1);
});

test('a refused start says "not started" on the control, with the reason one click away', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 422, headers: { get: () => 'application/json' },
    json: async () => ({ detail: 'engine host off' }), text: async () => '{"detail":"engine host off"}' });
  const host = document.createElement('div');
  host.innerHTML = ns.coordinateControlHtml(cand({}));
  ns.bindCoordinateControls(host, 'coco_pharma');
  const b = host.querySelector('[data-coordinate-egeria]');
  b.click();
  await new Promise((r) => setTimeout(r, 10));
  assert.match(b.textContent, /✗ not started/);
  assert.equal(b.disabled, false);
  const why = host.querySelector('[data-coordinate-why]');
  assert.equal(why.classList.contains('hidden'), false);
  assert.equal(why.querySelector('[data-coordinate-why-text]').classList.contains('hidden'), true);
  why.querySelector('[data-coordinate-why-open]').click();
  assert.match(why.querySelector('[data-coordinate-why-text]').textContent, /engine host off/);
});

test('a definition RE ran says where it was started, from the run it recorded', async () => {
  const { lastRunHtml } = await import('/static/next/app.js');
  const when = new Date(Date.now() - 60000).toISOString();
  const h = lastRunHtml({ last_run_at: when, last_run_status: 'ok', last_run_started_by: 're', last_run_by: 'dan' });
  assert.match(text(h), /run by RE · started by dan/);
  assert.doesNotMatch(text(lastRunHtml({ last_run_at: when, last_run_status: 'ok' })), /started|coordinated|run by/);
});

test('each reason line uses its own row label; a row with no reason still says it is not offered', () => {
  const a = cand({ available: false, why_not: 'engine host off', label: 'coordinated by Egeria' });
  const b = cand({ available: false, why_not: '', label: 'coordinated by Egeria (beta)' });
  const h = ns.coordinateOffNoteHtml([a, b]);
  assert.equal((h.match(/data-coordinate-off/g) || []).length, 2);
  assert.match(text(h), /coordinated by Egeria · engine host off/);
  assert.match(text(h), /coordinated by Egeria \(beta\) · not offered for this definition/);
});
