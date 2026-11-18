/** EA plan confirm gate, in the browser: runPlanConfirmed() and its dialog (plan_editor.js).
 *
 *  Every Execute / Re-run button goes through runPlanConfirmed(): the first POST runs nothing and returns the
 *  plan as it would run; the dialog shows it; Run sends {pending_id, digest} (plus ack_non_idempotent on a
 *  re-run that repeats non-idempotent commands, only once its checkbox is ticked).
 *
 *  Runs the REAL plan_editor.js as a classic <script> in jsdom. Only fetch and Auth are stubbed; the
 *  server's first answer is the shape GovernancePlanAgent.prepare_execute returns.
 *
 *  Run:  node --test advisor/web/static/test/plan_confirm.test.mjs   (jsdom: see render.test.mjs)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';

test('the harness can run (jsdom found)', { skip: !REQUIRED && SKIP }, () => {
  assert.ok(jsdom, `EA_RENDER_HARNESS_REQUIRED=1 but ${SKIP}`);
});

const EDITOR = fs.readFileSync(path.join(STATIC, 'plan_editor.js'), 'utf8');

function shown(n, extra = {}) {
  const names = Array.from({ length: n }, (_, i) => `Create Term ${i + 1}`);
  return {
    confirm_required: true, query_type: 'plan_confirm', doc_id: 'plan-1', pending_id: 'p1', plan_digest: 'd'.repeat(64),
    command_count: n, command_names: names,
    command_section: names.map(x => `## ${x}\n\n### Display Name\n${x}\n`).join('\n'),
    rerun: false, ack_required: false, non_idempotent: [], ...extra,
  };
}

function makeWindow(first) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'dangerously', url: 'http://ea.test/' });
  const w = dom.window;
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.posts = [];
  w.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    w.posts.push({ url, body, auth: init.headers.Authorization });
    const answer = w.posts.length === 1 ? first : { response: 'ran', query_type: 'plan_executed' };
    return { ok: true, status: 200, json: async () => answer };
  };
  const s = w.document.createElement('script');
  s.textContent = EDITOR;
  w.document.body.appendChild(s);
  return w;
}

const dialog = (w) => w.document.querySelector('[data-plan-confirm="dialog"]');
const part = (w, name) => w.document.querySelector(`[data-plan-confirm="${name}"]`);
const tick = () => new Promise(r => setTimeout(r, 0));

/** Start a run and wait for its dialog. Returns {pending}: an async function returning the promise itself
 *  would adopt it, and awaiting that would wait for the click this test has not made yet. */
async function open(w, url = '/api/plans/plan-1/execute', body = { draft_id: 'd1' }) {
  const pending = w.runPlanConfirmed(url, body);
  for (let i = 0; i < 20 && !dialog(w); i++) await tick();
  assert.ok(dialog(w), 'the dialog opened');
  return { pending };
}

test('the dialog lists the first 30 of 45 commands by name and keeps the whole script', { skip: SKIP }, async () => {
  const w = makeWindow(shown(45));
  const { pending } = await open(w);
  assert.equal(part(w, 'count').textContent, 'First 30 of 45 commands:');
  assert.equal(dialog(w).querySelectorAll('ol > li').length, 30);
  assert.match(dialog(w).textContent, /and 15 more, in the full Command Sequence below/);
  const script = part(w, 'script').textContent;
  assert.ok(script.includes('## Create Term 1') && script.includes('## Create Term 45'), 'the full script, never cut');
  part(w, 'run').click();
  const result = await pending;
  assert.equal(result.response, 'ran');
  assert.deepEqual(w.posts[1].body, { draft_id: 'd1', pending_id: 'p1', digest: 'd'.repeat(64) });
  assert.equal(w.posts[1].auth, 'Bearer t');
  assert.equal(dialog(w), null, 'the dialog is gone');
});

test('a short plan says all its commands', { skip: SKIP }, async () => {
  const w = makeWindow(shown(3));
  const { pending } = await open(w);
  assert.equal(part(w, 'count').textContent, 'All 3 commands:');
  part(w, 'cancel').click();
  await pending;
});

test('cancel sends nothing more', { skip: SKIP }, async () => {
  const w = makeWindow(shown(3));
  const { pending } = await open(w);
  part(w, 'cancel').click();
  const result = await pending;
  assert.equal(result.cancelled, true);
  assert.equal(w.posts.length, 1);
});

test('a re-run that repeats non-idempotent commands needs the checkbox, then sends the acknowledgement', { skip: SKIP }, async () => {
  const w = makeWindow(shown(4, {
    rerun: true, ack_required: true,
    non_idempotent: [{ command: 'Link Term to Term', count: 2, why: 'a Link command: running it again adds the relationship again' }],
  }));
  const { pending } = await open(w, '/api/plans/plan-1/rerun', {});
  assert.match(part(w, 'unsafe').textContent, /Link Term to Term \(×2\): a Link command/);
  const run = part(w, 'run');
  assert.equal(run.disabled, true);
  run.click();                                         // disabled: nothing happens
  await tick();
  assert.equal(w.posts.length, 1);
  const ack = part(w, 'ack');
  ack.checked = true;
  ack.dispatchEvent(new w.Event('change'));
  assert.equal(run.disabled, false);
  run.click();
  await pending;
  assert.equal(w.posts[1].url, '/api/plans/plan-1/rerun');
  assert.deepEqual(w.posts[1].body, { pending_id: 'p1', digest: 'd'.repeat(64), ack_non_idempotent: true });
});

test('a first run never sends the acknowledgement', { skip: SKIP }, async () => {
  const w = makeWindow(shown(2));
  const { pending } = await open(w);
  assert.equal(part(w, 'ack'), null);
  part(w, 'run').click();
  await pending;
  assert.ok(!('ack_non_idempotent' in w.posts[1].body));
});

test('a refusal or sign-in answer comes back without a dialog', { skip: SKIP }, async () => {
  const w = makeWindow({ state: 'refused', response: 'Sign in to run Dr.Egeria commands.' });
  const result = await w.runPlanConfirmed('/api/plans/plan-1/execute', {});
  assert.equal(result.state, 'refused');
  assert.equal(dialog(w), null);
  assert.equal(w.posts.length, 1);
});

test('server text reaches the page as text, never markup', { skip: SKIP }, async () => {
  const w = makeWindow(shown(1, { command_names: ['<img src=x onerror=alert(1)>'], doc_id: '<b>x</b>' }));
  const { pending } = await open(w);
  assert.equal(dialog(w).querySelector('img'), null);
  assert.equal(dialog(w).querySelector('b'), null);
  part(w, 'cancel').click();
  await pending;
});
