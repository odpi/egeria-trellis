/** Behavior: F45 + W39 + G16 (2026-11-02). A refused Survey press says so and is not wiped by the parent's reload; an
 *  Egeria-led card with no folder asset draws Survey off with the reason and an Attach press; on a file-system page the
 *  Folder Survey row's Run is off and points at the card (one enabled press per act).
 *
 *  Real fs-survey.js and native-surveys.js against a jsdom DOM; only fetch is stubbed.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const EGERIA = { state: 'expected_reachable', sentence: 'Egeria can read this path', surveyor: "Egeria's Folder Survey", decided: true };
const VIEW = (over = {}) => ({ reach: EGERIA, leads: EGERIA.surveyor, state: 'none', surveyor: '', surveyed_at: '', report_guid: '',
  published: false, results: null, limits: null, detail_lines: [], extensions: null, file_profiles: null, price: null,
  derived: { steps: [] }, egeria_asset: false, ...over });
const REFUSAL = 'Egeria has no asset for this folder yet, so its Folder Survey has nothing to act on. Attach the root folder to Egeria first (the press above), then Survey.';

async function setUp(view, { postStatus = 200 } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return { ok: true, status: 200, json: async () => view };
    if (method === 'POST' && /\/folder-survey$/.test(u) && postStatus !== 200) {
      return { ok: false, status: postStatus, statusText: 'x', json: async () => ({ detail: REFUSAL }), text: async () => JSON.stringify({ detail: REFUSAL }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.me = { user_id: 'alice' };
  app.state.resourceType = 'filesystem';
  const fs = await import('/static/next/fs-survey.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, calls, fs, ns };
}

test('F45: a refused Survey press leaves the server\'s sentence on screen and does not ask the parent to reload', async () => {
  const { host, fs } = await setUp(VIEW({ egeria_asset: true }), { postStatus: 422 });
  const seen = [];
  await fs.mountFolderSurvey(host, 'lake', { onChange: (act, info) => seen.push([act, info]) });
  host.querySelector('[data-fs-act="survey"]').click();
  await tick(80);
  assert.match(text(host.querySelector('[data-fs-error]')), /Egeria has no asset for this folder yet/);
  assert.deepEqual(seen, [['survey', { failed: true }]]);
});

test('W39: Egeria-led with no folder asset: Survey is off with the reason, Attach is beside it', async () => {
  const { host, fs } = await setUp(VIEW());
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  const survey = host.querySelector('[data-fs-act="survey"]');
  assert.ok(survey.disabled);
  assert.match(survey.title, /^Off: Egeria has no asset for this folder yet/);
  assert.ok(host.querySelector('[data-fs-act="attach"]') && !host.querySelector('[data-fs-act="attach"]').disabled);
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ egeria_asset: true }));
  assert.ok(!host.querySelector('[data-fs-act="survey"]').disabled);
  assert.equal(host.querySelector('[data-fs-act="attach"]'), null);
});

test('G16: on a file-system page the Folder Survey row\'s Run is off and points at the card', async () => {
  const { ns, host } = await setUp(VIEW());
  const row = { qualified_name: 'FileSurvey::survey-folder', display_name: 'Survey Folder', runnable: true, kind: 'egeria_survey', run: { state: 'not_run' } };
  const html = ns.nativeSurveyRowHtml(row);
  host.innerHTML = html;
  const d = host;
  assert.equal(d.querySelectorAll('[data-native-run]').length, 0, 'no enabled run button for the Folder Survey action');
  const off = d.querySelector('[data-native-run-from-card]');
  assert.ok(off.disabled);
  assert.match(off.title, /Folder survey card above \(Survey\)/);
  const other = ns.nativeSurveyRowHtml({ ...row, qualified_name: 'Other::survey' });
  assert.match(other, /data-native-run="Other::survey"/, 'other surveys keep their Run');
});
