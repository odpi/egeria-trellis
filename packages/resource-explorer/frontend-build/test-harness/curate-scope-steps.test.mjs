/** Panel B (the commit steps as a numbered list), the page re-reading the scope whenever a step changes
 *  state, the survey watch (a stated interval, one read in flight, stops at an end), and the commit's 401. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, flat, wait, calls } from './scope-test-kit.mjs';

const PREVIEW = {
  manifest: { lines: [{ id: 're_publishes', mechanism: 1, text: 'RE publishes.' }] },
  can_commit: true, button: 'Publish · 2 schemas', blockers: [], leave_out: [], refused: [], collisions: [], attach: ['sales', 'archive'], survey: { schemas: ['sales', 'archive'] },
};
const STEPS = (over = {}) => [
  ['publish_elements', 'done', 'published'], ['owner', 'done', 'owner set'], ['schema_targets', 'done', '1 attached · 2 already attached'],
  ['leave_outs', 'skipped', 'no schema to remove or archive'], ['survey_report', 'done', 'already in Egeria · report abcd1234 · 76 annotations in Egeria'],
  ['survey', 'submitted', "submitted · 10-06 13:25 · read back 10-06 13:27: running in Egeria · IN_PROGRESS · 3 annotations so far"],
  ['refresh', 'skipped', 'the cataloguer was already refreshing · elements arrive on its pass'],
  ['zone_membership', 'skipped', 'zones left to Egeria'], ['read_back', 'pending', ''],
].map(([name, state, detail]) => ({ name, state, detail, ...(over[name] || {}) }));
const REC = (steps, extra = {}) => ({ id: 'a3f2c001', state: 'running', author: 'me', requested_at: '2026-10-06T13:25:00', steps, ...extra });
const declared = () => baseView({ declared: { declared: true, by: 'me', at: '2026-10-04T08:00:00', kind: 'declare', baseline_survey_at: '' } });

async function open(opts = {}) {
  const ctx = await setUp(declared(), opts);
  ctx.server.preview = PREVIEW;
  Object.defineProperty(ctx.document, 'hidden', { value: false, configurable: true });   // jsdom reports a hidden page by default
  const mod = await import('/static/next/stages/curate-scope.js');
  const clock = { t: Date.parse('2026-10-06T13:41:00'), ticks: [] };
  mod.setScopeClock({ now: () => clock.t, every: (fn, ms) => { const e = { fn, ms }; clock.ticks.push(e); return () => { clock.ticks = clock.ticks.filter((x) => x !== e); }; } });
  const fold = ctx.document.querySelector('[data-scope-collapse]');
  if (fold.getAttribute('aria-expanded') === 'false') { fold.click(); await wait(); }
  return { ...ctx, clock, mod };
}
/** Ends the commit so its record poll (a 25 ms timer in these tests) stops and the process can exit. */
async function finish(ctx) {
  ctx.server.record = REC(STEPS({ survey: { state: 'done', detail: 'done' }, read_back: { state: 'done', detail: 'ok' } }).map((st) => (st.state === 'pending' || st.state === 'submitted' ? { ...st, state: 'done' } : st)), { state: 'done' });
  ctx.clock.ticks.length = 0;
  await wait(120);
  ctx.mod.setCommitPollMs(2000);
}
const tick = async (clock, ms) => { clock.ticks.filter((t) => t.ms === ms).forEach((t) => t.fn()); await wait(60); };

test('the steps are a numbered list under one header line: step n of m · running: <label> · k failed', async () => {
  const { mod } = await open();
  const host = document.createElement('div');
  host.innerHTML = mod.commitStepsHtml(REC(STEPS({ schema_targets: { state: 'failed', detail: 'TIMEOUT_ERROR_408 => Request timed out', more: '* Context: * class name' } })));
  assert.equal(flat(host.querySelector('[data-scope-steps-head]')), 'Publish · commit a3f2c001 · step 6 of 9 · running: Egeria survey · 1 failed');
  const lis = [...host.querySelectorAll('ol > li')];
  assert.equal(lis.length, 9);
  assert.match(flat(lis[0]), /^1\. ✓ Publish elements · done/);
  assert.match(flat(lis[5]), /^6\. ◔ Egeria survey · submitted/);
  assert.ok(lis[5].className.includes('text-ink') && !lis[5].className.includes('text-ink-muted'), 'the running step in normal weight');
  assert.ok(lis[0].className.includes('text-ink-muted'), 'the others muted');
  const failed = lis[2];
  assert.ok(failed.className.includes('border-l-[3px]'), 'a failed step gets a left-edge rule');
  assert.match(flat(failed.querySelector('[data-scope-step-first]')), /^TIMEOUT_ERROR_408/, 'and its first sentence at once');
  assert.ok(failed.querySelector('[data-scope-step-details]'), 'the rest behind details');
  assert.ok(lis[4].querySelector('[data-scope-details]'), 'every other sentence behind details');
  assert.equal(lis[4].querySelector('[data-scope-step-first]'), null);
});

test('a skipped refresh is not counted as failed in the header', async () => {
  const { mod } = await open();
  const host = document.createElement('div');
  host.innerHTML = mod.commitStepsHtml(REC(STEPS()));
  assert.match(flat(host.querySelector('[data-scope-steps-head]')), / · 0 failed$/);
  assert.match(flat(host.querySelector('[data-scope-commit-step="refresh"]')), /Refresh cataloger · skipped/);
});

test('the survey step shows elapsed time, annotations so far, the usual duration, check again and the stated interval', async () => {
  const { mod } = await open();
  const host = document.createElement('div');
  host.innerHTML = mod.commitStepsHtml(REC(STEPS()));
  assert.equal(flat(host.querySelector('[data-scope-step-hint]')),
    'running in Egeria · started 13:25 · 16 min · 3 annotations so far · usually takes about 15-25 minutes: come back and press Read Egeria again · check again · checking every 60 s');
  assert.equal(host.querySelector('[class*="animate-spin"], [role="progressbar"]'), null, 'no spinner');
});

test('a step changing state makes the page read the scope again, so a row is never older than a step on the page', async () => {
  const ctx = await open();
  const { document, server } = ctx;
  server.record = REC(STEPS({ schema_targets: { state: 'running', detail: '' }, survey: { state: 'pending', detail: '' } }), { state: 'running' });
  server.record.steps.forEach((st) => { if (['survey_report', 'leave_outs', 'refresh', 'zone_membership', 'read_back'].includes(st.name)) st.state = 'pending'; });
  const mod = ctx.mod; mod.setCommitPollMs(25);
  document.querySelector('[data-scope-commit-btn]').click();
  await wait(60);
  assert.equal(document.querySelector('[data-scope-commit-step="schema_targets"]').dataset.state, 'running');
  assert.match(flat(document.querySelector('[data-scope-row="schema:sales"]')), /undecided/);
  // the attach lands: the server's row now says so, and the step changes state
  server.view.commit.schemas.sales = { state: 'catalogued', words: 'published · 2 tables', second: 'read back 13:26' };
  server.record = REC(STEPS({ schema_targets: { state: 'done', detail: '2 of 2 attached' } }));
  const before = calls(server, 'GET', '/api/catalogue-scope/adventureworks').filter((c) => !c.url.includes('/commit')).length;
  await wait(120);
  assert.equal(document.querySelector('[data-scope-commit-step="schema_targets"]').dataset.state, 'done');
  assert.match(flat(row(document, 'schema:sales').querySelector('[data-scope-state-cell]')), /published · 2 tables/, 'the row is as new as the step');
  const after = calls(server, 'GET', '/api/catalogue-scope/adventureworks').filter((c) => !c.url.includes('/commit')).length;
  assert.ok(after - before >= 1 && after - before <= 3, `one read per change, not per tick (${after - before})`);
  await finish(ctx);
});

async function runningSurvey() {
  const ctx = await open();
  ctx.server.record = REC(STEPS());
  ctx.mod.setCommitPollMs(25);
  ctx.document.querySelector('[data-scope-commit-btn]').click();
  await wait(80);
  return ctx;
}

test('while the survey is open the page reads it every 60 s: a read-back, never a write, one at a time', async () => {
  const ctx = await runningSurvey();
  const { server, clock, document } = ctx;
  const posts = () => server.calls.filter((c) => c.method === 'POST');
  server.calls.length = 0; server.readBacks = 0;
  const g = (() => { let r; const p = new Promise((x) => { r = x; }); return [p, r]; })();
  server.holdReadBack = g[0];
  await tick(clock, 60000);
  await tick(clock, 60000);                         // the first read is still out: no second one starts
  assert.equal(server.readBacks, 1, 'at most one read in flight');
  assert.deepEqual(posts().map((c) => c.url.split('/').pop()), ['read-back'], 'only the read-back route is ever posted');
  g[1]();
  await wait(80);
  assert.ok(!posts().some((c) => /\/node|\/nodes|\/commit$/.test(c.url)), 'never a write');
  server.holdReadBack = null;
  await tick(clock, 60000);
  assert.equal(server.readBacks, 2);
  assert.ok(document.querySelector('[data-scope-checking-every]'));
  await finish(ctx);
});

test('polling stops when the survey reaches an end, and does not run while the page is hidden', async () => {
  const ctx = await runningSurvey();
  const { server, clock } = ctx;
  Object.defineProperty(ctx.document, 'hidden', { value: true, configurable: true });
  server.readBacks = 0;
  await tick(clock, 60000);
  assert.equal(server.readBacks || 0, 0, 'hidden: no read');
  Object.defineProperty(ctx.document, 'hidden', { value: false, configurable: true });
  server.record = REC(STEPS({ survey: { state: 'done', detail: 'done · report abcd1234 · 9 annotations' }, read_back: { state: 'done', detail: 'ok' } }), { state: 'done' });
  await tick(clock, 60000);
  assert.equal(server.readBacks, 1);
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 0, 'the interval is stopped once the survey is over');
  await finish(ctx);
});

test('"check again" reads the survey now', async () => {
  const ctx = await runningSurvey();
  ctx.server.readBacks = 0;
  ctx.document.querySelector('[data-scope-check-again]').click();
  await wait(80);
  assert.equal(ctx.server.readBacks, 1);
  await finish(ctx);
});

test('a 401 on the commit press: the button returns, says the session expired and offers sign in', async () => {
  const ctx = await open();
  ctx.server.signedIn = false;
  ctx.document.querySelector('[data-scope-commit-btn]').click();
  await wait(60);
  const b = ctx.document.querySelector('[data-scope-commit-btn]');
  assert.equal(flat(b), 'Publish · 2 schemas');
  assert.equal(b.disabled, false, 'it can be pressed again after signing in');
  assert.match(flat(ctx.document.querySelector('[data-scope-commit-why]')), /^⚠ your session expired · sign in again/);
  assert.ok(ctx.document.querySelector('[data-scope-commit-status] [data-scope-sign-in]'));
});
