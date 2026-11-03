/** Parity P1, Automate: schedule one resource (PI-073, PI-099), pause and resume (PI-100), why a run
 *  failed (PI-103). Real jsdom render of stages/automate.js; fake fetch records every call. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = () => new Promise((r) => setTimeout(r, 15));

const SCHEDULES = [
  { entity_type: 'repo', entity_slug: 'egeria_git', analysis_id: 'repo_secret_scan', schedule: 'daily',
    enabled: 1, target_kind: 'analysis', last_run_status: 'error', last_run_activity_id: 'act-9',
    last_run: '2026-10-08T01:00:00', next_run: '2026-10-09T01:00:00' },
  { entity_type: 'repo', entity_slug: 'egeria_git', analysis_id: 'GovActionProcess::RepoSurvey', schedule: 'weekly',
    enabled: 0, target_kind: 'survey', last_run_status: '', last_run_activity_id: '',
    last_run: '', next_run: '' },
  { entity_type: 'repo', entity_slug: 'other', analysis_id: 'lic', schedule: 'daily',
    enabled: 1, target_kind: 'analysis', last_run_status: 'error', last_run_activity_id: '',
    last_run: '2026-10-08T01:00:00', next_run: '' },
];

async function setUp({ activity } = {}) {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ url: String(url), method, body });
    const ok = (json) => ({ ok: true, status: 200, json: async () => json });
    const u = String(url);
    if (u === '/api/schedules/') return ok(SCHEDULES);
    if (u.startsWith('/api/analyses/repo')) return ok([{ id: 'repo_secret_scan', name: 'Secret scan' }, { id: 'lic', name: 'Licence' }]);
    if (u === '/api/survey-definitions/definitions') {
      return ok([{ name: 'RepoSurvey', qualified_name: 'GovActionProcess::RepoSurvey', resource_type: 'repo' },
        { name: 'DbSurvey', qualified_name: 'GovActionProcess::DbSurvey', resource_type: 'database' }]);
    }
    if (u === '/api/activity/act-9') return ok(activity || { summary: 'Secret scan failed', detail: JSON.stringify({ error: 'rate limited' }) });
    if (u.startsWith('/api/schedules/repo/') && method === 'POST') return ok({ status: 'ok' });
    throw new Error(`_UNSTUBBED_FETCH ${method} ${u}`);
  };
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'egeria_git';
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  const automate = await import(`/static/next/stages/automate.js?t=${Math.random()}`);
  await automate.renderAutomate();
  host.querySelector('[data-automate-tab="schedules"]').click();
  await tick(); await tick();
  return { document, host, calls, app, automate };
}

test('a paused schedule says so with a cue and a short word; an enabled one says on', async () => {
  const { host } = await setUp();
  const rows = [...host.querySelectorAll('[data-sched-toggle]')];
  assert.equal(rows.length, 3);
  assert.match(rows[0].textContent, /● on/);
  assert.equal(rows[0].getAttribute('aria-pressed'), 'true');
  assert.match(rows[1].textContent, /○ paused/);
  assert.equal(rows[1].getAttribute('aria-pressed'), 'false');
});

test('pressing the toggle posts the opposite enabled flag, keeping cadence and target kind', async () => {
  const { host, calls } = await setUp();
  host.querySelectorAll('[data-sched-toggle]')[1].click();
  await tick();
  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/schedules/repo/egeria_git');
  assert.deepEqual(post.body, { analysis_id: 'GovActionProcess::RepoSurvey', schedule: 'weekly', enabled: true, target_kind: 'survey' });
  host.querySelectorAll('[data-sched-toggle]')[0].click();
  await tick();
  const second = calls.filter((c) => c.method === 'POST')[1];
  assert.equal(second.body.enabled, false);
  assert.equal(second.body.target_kind, 'analysis');
});

test('the form offers all four cadences', async () => {
  const { host } = await setUp();
  const opts = [...host.querySelectorAll('[data-sched-cadence] option')].map((o) => o.value);
  assert.deepEqual(opts, ['manual', 'daily', 'weekly', 'monthly']);
});

test('scheduling an analysis for the selected resource posts target_kind analysis', async () => {
  const { host, calls } = await setUp();
  const form = host.querySelector('[data-sched-form]');
  assert.deepEqual([...form.querySelectorAll('[data-sched-target] option')].map((o) => o.value), ['repo_secret_scan', 'lic']);
  form.querySelector('[data-sched-cadence]').value = 'monthly';
  form.dispatchEvent(new globalThis.window.Event('submit', { cancelable: true }));
  await tick();
  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/schedules/repo/egeria_git');
  assert.deepEqual(post.body, { analysis_id: 'repo_secret_scan', schedule: 'monthly', enabled: true, target_kind: 'analysis' });
});

test('scheduling a survey lists only definitions that suit the resource kind and posts target_kind survey', async () => {
  const { host, calls } = await setUp();
  const form = host.querySelector('[data-sched-form]');
  const kind = form.querySelector('[data-sched-kind]');
  kind.value = 'survey';
  kind.dispatchEvent(new globalThis.window.Event('change'));
  assert.deepEqual([...form.querySelectorAll('[data-sched-target] option')].map((o) => o.value), ['GovActionProcess::RepoSurvey']);
  form.querySelector('[data-sched-cadence]').value = 'manual';
  form.dispatchEvent(new globalThis.window.Event('submit', { cancelable: true }));
  await tick();
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual(post.body, { analysis_id: 'GovActionProcess::RepoSurvey', schedule: 'manual', enabled: true, target_kind: 'survey' });
});

test('with no resource selected the form says to pick one, and posts nothing', async () => {
  const { document, calls } = await setUp();
  const app = await import('/static/next/app.js');
  app.state.selectedSlug = '';
  const automate = await import(`/static/next/stages/automate.js?t=${Math.random()}`);
  document.getElementById('content').innerHTML = '';
  await automate.renderAutomate();
  document.querySelector('[data-automate-tab="schedules"]').click();
  await tick(); await tick();
  assert.ok(document.querySelector('[data-sched-form-empty]'));
  assert.equal(document.querySelector('[data-sched-form]'), null);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
});

test('a failed run: pressing error reads the activity row and shows its summary and detail', async () => {
  const { host, calls } = await setUp();
  const btn = host.querySelector('[data-sched-error]');
  assert.match(btn.textContent, /error/);
  btn.click();
  await tick(); await tick();
  assert.ok(calls.some((c) => c.url === '/api/activity/act-9'));
  const detail = host.querySelector('[data-sched-error-detail]');
  assert.match(detail.textContent, /Secret scan failed/);
  assert.match(detail.textContent, /rate limited/);
  assert.equal(btn.getAttribute('aria-expanded'), 'true');
});

test('a failed run with no activity row says there is no reason to show, and does not fetch', async () => {
  const { host, calls } = await setUp();
  const buttons = host.querySelectorAll('[data-sched-error]');
  assert.equal(buttons.length, 2);
  buttons[1].click();
  await tick();
  assert.match(host.querySelectorAll('[data-sched-error-detail]')[0].textContent, /no\s+activity entry was recorded/);
  assert.equal(calls.filter((c) => c.url.startsWith('/api/activity/')).length, 0);
});

test('dynamic strings are escaped in the schedule rows', async () => {
  SCHEDULES.push({ entity_type: 'repo', entity_slug: '<img src=x onerror=alert(1)>', analysis_id: '"><b id="pwn">x</b>',
    schedule: 'daily', enabled: 1, target_kind: 'analysis', last_run_status: '', last_run_activity_id: '' });
  try {
    const { host } = await setUp();
    assert.equal(host.querySelector('img'), null);
    assert.equal(host.querySelector('#pwn'), null);
    assert.match(host.textContent, /<img src=x onerror=alert\(1\)>/);
  } finally { SCHEDULES.pop(); }
});
