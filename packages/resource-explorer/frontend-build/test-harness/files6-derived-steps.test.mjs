/** Behavior: Files build step 6 (2026-10-28) on the file system's folder survey card: the price line before the Survey
 *  press (F12) and "Derived from the walk" (fs_change_rates, fs_duplicates, fs_activity, fs_permissions).
 *
 *  Real fs-survey.js, colresize.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - the price line is the server's words, "not priced" when no walk has measured the tree, never a number of its own;
 *  - a step never run says "not run yet", an uninstalled one names its package, never zeros;
 *  - a run step shows each measure's stored state words and summary, and the reason when it could not measure;
 *  - Derive is offered only once a walk kept rows and a step is installed; it posts one run per installed step,
 *    polls each activity, and repaints from the re-read; a failed run is said with its own reason.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REACH = { state: 're_only', sentence: 'only Resource Explorer can read this path · checked 2026-10-28', surveyor: 'Resource Explorer', decided: true };
const STEP = (step, over = {}) => ({ analysis_id: step, step, name: step.replace(/_/g, ' '), description: '', intent: 'analysis',
  installed: true, missing_reason: '', run: '', results: [], ...over });
const RAN = (step, results) => STEP(step, { run: '2026-10-28T09:00:00', results });
const RES = (measure, state, summary, reason = '') => ({ measure, scope: 'tree', state, summary, reason,
  state_words: { measured: 'measured', nothing_found: 'nothing found', not_established: 'not established' }[state], values: {} });
const EXT = { rows: { kept: true, files: 4 }, led_by: 're' };
const VIEW = ({ steps = [STEP('fs_duplicates')], price = { priced: false, words: 'not priced · no walk has measured this file system yet' }, extensions = EXT } = {}) =>
  ({ state: 'none', reach: REACH, leads: REACH.surveyor, surveyor: '', extensions, file_profiles: null, price, derived: { steps } });

function backend(views, { fail = '' } = {}) {
  const calls = [];
  let v = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && /\/folder-survey$/.test(u)) return ok(views[Math.min(v++, views.length - 1)]);
    const run = /\/analyses\/([^/]+)\/run$/.exec(u);
    if (method === 'POST' && run) return ok({ status: 'started', activity_id: `act-${run[1]}`, run_id: `r-${run[1]}` });
    const act = /\/api\/activity\/act-([^/?]+)/.exec(u);
    if (method === 'GET' && act) {
      return ok(act[1] === fail ? { id: `act-${act[1]}`, status: 'error', detail: JSON.stringify({ error: 'the registry did not answer' }) }
        : { id: `act-${act[1]}`, status: 'ok', summary: '2 annotation(s).' });
    }
    return ok({});
  };
  return calls;
}

async function setUp(views = [VIEW()], opts = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = backend(views, opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const fs = await import('/static/next/fs-survey.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, calls, fs };
}

test('the price line is the stored words: "not priced" before a walk, the walk\'s own numbers after', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  const line = host.querySelector('[data-fs-price]');
  assert.equal(line.dataset.fsPriced, 'no');
  assert.equal(text(line), 'not priced · no walk has measured this file system yet');
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ price: { priced: true, words: 'last walk (2026-10-28): 4,210 files, 1.2 GB · about 3 s to walk again on this machine' } }));
  assert.equal(host.querySelector('[data-fs-price]').dataset.fsPriced, 'yes');
  assert.match(text(host.querySelector('[data-fs-price]')), /4,210 files, 1\.2 GB · about 3 s/);
});

test('never run says "not run yet"; not installed names the package; neither shows a number', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [STEP('fs_duplicates'),
    STEP('fs_activity', { installed: false, missing_reason: "step not installed here: 'fs_activity' comes from package trellis-step-fs-activity 0.1.0" })] }));
  const dup = host.querySelector('[data-fs-step="fs_duplicates"]');
  assert.equal(dup.dataset.fsStepState, 'not_run');
  assert.match(text(dup), /not yet run/);
  const act = host.querySelector('[data-fs-step="fs_activity"]');
  assert.equal(act.dataset.fsStepState, 'not_installed');
  assert.match(text(act), /trellis-step-fs-activity/);
  assert.doesNotMatch(text(host.querySelector('[data-fs-derived]')), /\b0 (files|sets|copies|owners)\b/);
  assert.match(text(host.querySelector('[data-fs-derived]')), /kept here · sent to Egeria in the walk's own survey report when that walk is published/);
});

test('a run step shows each measure in its own words, with the reason when it could not measure', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [RAN('fs_activity', [
    RES('fs_activity', 'measured', '12 files modified within 7 days; 300 not modified for over 365 days (9,000 bytes)'),
    RES('fs_access_activity', 'not_established', 'not measured · access times not recorded on this mount',
      "access times not recorded on this mount: no file's access time is later than its modification time")])] }));
  const step = host.querySelector('[data-fs-step="fs_activity"]');
  assert.equal(step.dataset.fsStepState, 'run');
  assert.match(text(step), /derived · 2026-10-28/);
  const mod = step.querySelector('[data-fs-result="fs_activity"]');
  assert.equal(mod.dataset.fsResultState, 'measured');
  assert.match(text(mod), /measured · 12 files modified within 7 days/);
  const acc = step.querySelector('[data-fs-result="fs_access_activity"]');
  assert.equal(acc.dataset.fsResultState, 'not_established');
  assert.match(text(acc), /not established · not measured · access times not recorded on this mount/);
  assert.match(text(acc), /no file's access time is later than its modification time/);
});

test('Derive is offered only once a walk kept rows and a step is installed', async () => {
  const { host, fs } = await setUp();
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ extensions: null }));
  assert.equal(host.querySelector('[data-fs-act="derive"]'), null, 'no walk rows: nothing to derive from');
  host.innerHTML = fs.folderSurveyCardHtml(VIEW({ steps: [STEP('fs_duplicates', { installed: false, missing_reason: 'x' })] }));
  assert.equal(host.querySelector('[data-fs-act="derive"]'), null, 'nothing installed: nothing to run');
  host.innerHTML = fs.folderSurveyCardHtml(VIEW());
  assert.ok(host.querySelector('[data-fs-act="derive"]'));
});

test('the press runs each installed step, polls each, and repaints from the re-read', async () => {
  const before = VIEW({ steps: [STEP('fs_duplicates'), STEP('fs_permissions'), STEP('fs_activity', { installed: false, missing_reason: 'm' })] });
  const after = VIEW({ steps: [RAN('fs_duplicates', [RES('fs_duplicates', 'nothing_found', 'no two of the 4 hashed files have the same content')]),
    RAN('fs_permissions', [RES('fs_permissions', 'measured', '1 owners, 1 groups')]), STEP('fs_activity', { installed: false, missing_reason: 'm' })] });
  const { host, calls, fs } = await setUp([before, after]);
  await fs.mountFolderSurvey(host, 'tree');
  host.querySelector('[data-fs-act="derive"]').click();
  await tick(150);
  const runs = calls.filter((c) => c.method === 'POST' && /\/analyses\/[^/]+\/run$/.test(c.url)).map((c) => c.url.split('/').slice(-2)[0]);
  assert.deepEqual(runs, ['fs_duplicates', 'fs_permissions'], 'one run per installed step, the uninstalled one not posted');
  assert.ok(calls.some((c) => /\/api\/activity\/act-fs_duplicates/.test(c.url)), 'each run is polled to its end');
  assert.equal(host.querySelector('[data-fs-step="fs_duplicates"]').dataset.fsStepState, 'run');
  assert.equal(host.querySelector('[data-fs-error]'), null);
});

test('a failed run is said with its own reason; the other steps still run', async () => {
  const views = [VIEW({ steps: [STEP('fs_duplicates'), STEP('fs_permissions')] })];
  const { host, calls, fs } = await setUp(views, { fail: 'fs_duplicates' });
  await fs.mountFolderSurvey(host, 'tree');
  host.querySelector('[data-fs-act="derive"]').click();
  await tick(150);
  assert.ok(calls.some((c) => c.method === 'POST' && /fs_permissions\/run$/.test(c.url)));
  assert.match(text(host.querySelector('[data-fs-error]')), /fs_duplicates: Failed — the registry did not answer/);
});

test('a failed press of any kind keeps its words after the re-read (the re-read used to clear them)', async () => {
  const { host, fs } = await setUp([VIEW()]);
  globalThis.fetch = ((orig) => async (url, options = {}) => {
    if ((options.method || 'GET').toUpperCase() === 'POST' && /\/folder-survey$/.test(String(url))) {
      return { ok: false, status: 409, json: async () => ({ detail: 'nobody can be shown to read this path' }) };
    }
    return orig(url, options);
  })(globalThis.fetch);
  await fs.mountFolderSurvey(host, 'tree');
  host.querySelector('[data-fs-act="survey"]').click();
  await tick(80);
  assert.match(text(host.querySelector('[data-fs-error]')), /nobody can be shown to read this path/);
});
