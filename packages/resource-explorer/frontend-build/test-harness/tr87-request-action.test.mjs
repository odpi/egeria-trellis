/** Behavior: TR-87 (2026-11-20), "Request action" on a file-system finding's row in Curate's publish table.
 *
 *  Real publish-table.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *  - the button is on the row the server offers it for (allowlisted, measured, count > 0), only for a signed-in person,
 *    and is ENABLED (a test presses it);
 *  - a press POSTs once to /findings/request-action with {step, finding, run_id} and to nothing else: never the
 *    row's publish-choice route; a row that reads "Keep in RE only" still shows the button beside it;
 *  - the three feedback words; an in-flight second press is ignored. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (b) => ({ ok: true, status: 200, json: async () => b });
const OFFER = { step: 'fs_permissions', finding: 'world_readable_sensitive', run_id: '2026-11-19T03:00:00', count: 3,
  unit: 'secret-looking files readable by others', key: 'fs_permissions::world_readable_sensitive::2026-11-19T03:00:00' };

const row = (over = {}) => ({ analysis_id: 'fs_permissions', analysis: 'File Permissions', measure: 'world_readable_sensitive',
  locator: 'fs_permissions/world_readable_sensitive', unit: 'files', value: 3, value_words: '3 files', as_of: '2026-11-19T01:00:00',
  confidence: { words: 'measured' }, scope: 'resource',
  writes_as: { route: 'none', type: 'RequestForActionAnnotation', words: 'RequestForActionAnnotation', writable: false },
  publishable: false, why_not: 'Keep in RE only · RequestForActionAnnotation is not written natively', choice: null,
  published: { state: 'local', words: 'local · not published' }, request_action: OFFER, ...over });

async function tableWith({ rows = [row()], me = { user_id: 'dan' }, answer, hold } = {}) {
  const calls = [];
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push({ m, u, body: o.body });
    if (u.includes('/api/publish-table/')) return ok({ entity_type: 'filesystem', slug: 'loading_bay', chosen: 0, press: 'p', rows });
    if (m === 'POST' && u.endsWith('/findings/request-action')) {
      if (hold) await hold;
      if (answer instanceof Error) return { ok: false, status: 502, json: async () => ({ detail: answer.message }), text: async () => JSON.stringify({ detail: answer.message }) };
      return ok(answer || { nothing_new: false, annotation_guid: 'abcdef12-0000', words: 'x' });
    }
    return ok({});
  };
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { selectedSlug: 'loading_bay', resourceType: 'filesystem', me, groups: [], projects: [], databases: [],
    filesystems: [{ slug: 'loading_bay', display_name: 'Loading bay' }], filesystemsLoaded: true });
  const host = document.createElement('div');
  document.body.appendChild(host);
  const { renderPublishTable } = await import('/static/next/stages/publish-table.js');
  await renderPublishTable(host, 'loading_bay', 'filesystem');
  return { host, calls };
}
const posts = (t) => t.calls.filter((c) => c.m !== 'GET');

test('a row the server offers shows an ENABLED Request action with its title and the one-line note, beside "Keep in RE only"', async () => {
  const t = await tableWith();
  const b = t.host.querySelector('[data-request-action]');
  assert.ok(b, 'the button is shown');
  assert.equal(text(b), 'Request action');
  assert.equal(b.disabled, false, 'never rendered disabled');
  assert.equal(b.getAttribute('title'), 'Files a Request For Action in Egeria for this finding, once');
  assert.match(text(t.host.querySelector('[data-publish-row]')), /A request goes to Egeria only when you press it; no finding files one by itself\./);
  // the same row still reads "Keep in RE only" and cannot publish: the request is a different write
  assert.match(text(t.host.querySelector('[data-why-not]')), /^Keep in RE only/);
  assert.equal(t.host.querySelector('[data-publish-choice="publish"]').disabled, true);
});

test('no button without an offer (not measured, zero, not allowlisted) and none when signed out', async () => {
  const none = await tableWith({ rows: [row({ request_action: null })] });
  assert.equal(none.host.querySelector('[data-request-action]'), null);
  const out = await tableWith({ me: null });
  assert.equal(out.host.querySelector('[data-request-action]'), null);
});

test('a press POSTs once to the finding route with step, finding and run, and never to the publish-choice route', async () => {
  const t = await tableWith();
  t.host.querySelector('[data-request-action]').click();
  await tick(80);
  const p = posts(t);
  assert.equal(p.length, 1);
  assert.match(p[0].u, /\/api\/filesystems\/loading_bay\/findings\/request-action$/);
  assert.deepEqual(JSON.parse(p[0].body), { step: 'fs_permissions', finding: 'world_readable_sensitive', run_id: '2026-11-19T03:00:00' });
  assert.ok(!t.calls.some((c) => /publish-table\/.*(choice|press)|\/choice/.test(c.u) && c.m !== 'GET'), 'no publish-choice write');
  assert.equal(text(t.host.querySelector('[data-request-action-feedback]')), 'requested · RFA abcdef12');
  assert.equal(t.host.querySelector('[data-choice-line]') && text(t.host.querySelector('[data-choice-line]')), 'not chosen · stays in RE');
});

test('the feedback words: already requested · nothing new, and not sent · <why>', async () => {
  const again = await tableWith({ answer: { nothing_new: true, annotation_guid: 'abcdef12', words: 'already requested · nothing new' } });
  again.host.querySelector('[data-request-action]').click();
  await tick(80);
  assert.equal(text(again.host.querySelector('[data-request-action-feedback]')), 'already requested · nothing new');
  const bad = await tableWith({ answer: new Error('not sent · Egeria went away') });
  bad.host.querySelector('[data-request-action]').click();
  await tick(80);
  assert.match(text(bad.host.querySelector('[data-request-action-feedback]')), /^not sent · /);
  assert.equal(bad.host.querySelector('[data-request-action]').disabled, false, 'it can be pressed again after a failure');
});

test('an in-flight second press is ignored', async () => {
  let release;
  const hold = new Promise((r) => { release = r; });
  const t = await tableWith({ hold });
  const b = t.host.querySelector('[data-request-action]');
  b.click(); b.click();
  await tick(40);
  assert.equal(posts(t).length, 1);
  release();
  await tick(80);
  assert.equal(posts(t).length, 1);
});
