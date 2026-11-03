/** A reload must not forget the commit the page was watching (status from proof rows, not from the tab).
 *
 *  On load the Curate pane READS the newest commit for the database from the registry (`GET .../commits/latest`, the
 *  same rows a second tab reads) and resumes the stepper from it:
 *   - not finished: Panel B (the steps) is drawn and the 60 s survey watch resumes;
 *   - finished within 24 h: Panel B is drawn;
 *   - older: ONE line, `last commit <id> · <when> · <outcome>`, with a "show steps" disclosure, never the list by default;
 *   - none: nothing is drawn.
 *  Never a write on load, never more than one watch in flight on the page, and the watch stops at a terminal state and
 *  while the page is hidden. Real app.js, real router, a fake fetch and a fake clock. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, flat, wait } from './scope-test-kit.mjs';

const PREVIEW = {
  manifest: { lines: [{ id: 're_publishes', mechanism: 1, text: 'RE publishes.' }] },
  can_commit: true, button: 'Publish · 2 schemas', blockers: [], leave_out: [], refused: [], collisions: [], attach: ['sales', 'archive'], survey: { schemas: ['sales', 'archive'] },
};
const STEPS = (over = {}) => [
  ['publish_elements', 'done', 'published'], ['owner', 'done', 'owner set'], ['schema_targets', 'done', '2 attached'],
  ['leave_outs', 'skipped', 'no schema to delete or archive'], ['survey_report', 'done', 'report abcd1234 · 76 annotations'],
  ['survey', 'submitted', 'submitted · 10-06 13:25 · read back 10-06 13:27: running in Egeria · IN_PROGRESS · 3 annotations so far'],
  ['refresh', 'skipped', 'not asked'], ['zone_membership', 'skipped', 'zones left to Egeria'], ['read_back', 'pending', ''],
].map(([name, state, detail]) => ({ name, state, detail, ...(over[name] || {}) }));
const REC = (steps, extra = {}) => ({ id: 'a3f2c001', state: 'running', author: 'me', requested_at: '2026-10-06T13:25:00', steps, ...extra });
const LATEST = (rec, over = {}) => ({ commit: rec, terminal: rec.state === 'done' || rec.state === 'failed', age_hours: 0.3, stale_unfinished: false, states: {}, ...over });
const declared = () => baseView({ declared: { declared: true, by: 'me', at: '2026-10-04T08:00:00', kind: 'declare', baseline_survey_at: '' } });

async function open(latest, opts = {}) {
  const clock = { t: Date.parse('2026-10-06T13:41:00'), ticks: [] };
  let theMod = null;
  const ctx = await setUp(declared(), {
    beforeOpen: ({ server, mod, document }) => {
      server.preview = PREVIEW;
      server.latest = latest;
      if (opts.latestFails) server.latestFails = true;
      if (latest && latest.commit && Array.isArray(latest.commit.steps)) server.record = latest.commit;
      Object.defineProperty(document, 'hidden', { value: false, configurable: true });   // jsdom reports a hidden page by default
      mod.setScopeClock({ now: () => clock.t, every: (fn, ms) => { const e = { fn, ms }; clock.ticks.push(e); return () => { clock.ticks = clock.ticks.filter((x) => x !== e); }; } });
      mod.setCommitPollMs(25);
      theMod = mod;
    },
  });
  await wait(150);
  // A declared scope starts collapsed, and a collapsed section reads nothing; a live commit opens it on its own. For the
  // cases that do not, the person opens it (the one line and the panel live inside it).
  const fold = ctx.document.querySelector('[data-scope-collapse]');
  if (opts.openSection && fold && fold.getAttribute('aria-expanded') === 'false') { fold.click(); await wait(150); }
  return { ...ctx, clock, mod: theMod };
}
/** Ends the commit so the resumed record poll (a timer) stops and the process can exit. */
async function finish(ctx, ...others) {
  ctx.server.record = REC(STEPS({ survey: { state: 'done', detail: 'done' }, read_back: { state: 'done', detail: 'ok' } }).map((st) => (st.state === 'pending' || st.state === 'submitted' ? { ...st, state: 'done' } : st)), { state: 'done' });
  ctx.clock.ticks.length = 0;
  await wait(150);
  ctx.mod.setCommitPollMs(2000);
  for (const m of others) m.setCommitPollMs(2000);
}
const stepsEl = (d) => d.querySelector('[data-scope-commit-panel] > [data-scope-commit-steps], [data-scope-commit-panel] [data-scope-commit-steps]');
const posts = (server) => server.calls.filter((c) => c.method === 'POST');
const tick = async (clock, ms) => { clock.ticks.filter((t) => t.ms === ms).forEach((t) => t.fn()); await wait(60); };

test('load with a commit still running: the stepper is drawn from the registry and the 60 s watch resumes, with no write on load', async () => {
  const ctx = await open(LATEST(REC(STEPS())));
  const { document, server, clock } = ctx;
  assert.ok(stepsEl(document), 'Panel B is drawn for the running commit');
  assert.match(flat(document.querySelector('[data-scope-steps-head]')), /^Publish · commit a3f2c001 · step 6 of 9 · running: Egeria survey/);
  assert.equal(document.querySelector('[data-scope-last-commit]'), null);
  assert.ok(server.calls.some((c) => c.method === 'GET' && c.url.endsWith('/commits/latest')), 'it read the newest commit from the registry');
  assert.deepEqual(posts(server), [], 'no write on load');
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 1, 'the survey watch resumed (one)');
  await tick(clock, 60000);
  assert.equal(posts(server).filter((c) => c.url.endsWith('/read-back')).length, 1, 'the watch reads back: a read, on the 60 s tick, not on load');
  await finish(ctx);
});

test('load with a commit that finished within 24 h: the steps are drawn, and no watch starts when its survey is over', async () => {
  const rec = REC(STEPS({ survey: { state: 'done', detail: 'done · report abcd1234 · 23 annotations' }, read_back: { state: 'done', detail: 'ok' } }), { state: 'done', finished_at: '2026-10-06T13:30:00' });
  const { document, clock, server } = await open(LATEST(rec, { age_hours: 20 }));
  assert.ok(stepsEl(document));
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 0);
  assert.deepEqual(posts(server), []);
});

test('load with a commit that finished yesterday or earlier: ONE line, steps behind "show steps", never the list by default', async () => {
  const rec = REC(STEPS({ survey: { state: 'done', detail: 'done' }, read_back: { state: 'done', detail: 'ok' } }), { state: 'done', requested_at: '2026-10-04T09:00:00', finished_at: '2026-10-04T09:20:00' });
  const { document, server } = await open(LATEST(rec, { age_hours: 52 }), { openSection: true });
  const line = document.querySelector('[data-scope-last-commit]');
  assert.ok(line, 'the one-line summary');
  assert.match(flat(line.querySelector('[data-scope-last-commit-line]')), /^last commit a3f2c001 · 10-04 09:20 · done$/);
  const disclosure = line.querySelector('details');
  assert.ok(disclosure && !disclosure.hasAttribute('open'));
  assert.match(flat(disclosure.querySelector('summary')), /show steps/);
  assert.equal(document.querySelector('[data-scope-commit-panel] > [data-scope-commit-steps]'), null, 'the whole list is not drawn by default');
  assert.deepEqual(posts(server), []);
});

test('an old commit that failed says so in its one line', async () => {
  const rec = REC(STEPS({ schema_targets: { state: 'failed', detail: 'Egeria refused' } }), { state: 'failed', finished_at: '2026-10-03T08:00:00' });
  const { document } = await open(LATEST(rec, { age_hours: 80 }), { openSection: true });
  assert.match(flat(document.querySelector('[data-scope-last-commit-line]')), /^last commit a3f2c001 · 10-03 08:00 · failed · 1 step failed$/);
});

test('a commit stuck "running" for hours is not watched: one line says it did not finish', async () => {
  const rec = REC(STEPS(), { state: 'running', requested_at: '2026-10-06T01:00:00' });
  const { document, clock } = await open(LATEST(rec, { age_hours: 12.7, stale_unfinished: true }), { openSection: true });
  assert.match(flat(document.querySelector('[data-scope-last-commit-line]')), /^last commit a3f2c001 · 10-06 01:00 · did not finish/);
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 0, 'no watch on a commit that never finished');
});

test('load with no commit: nothing is drawn', async () => {
  const { document, server, clock } = await open(null, { openSection: true });
  assert.equal(document.querySelector('[data-scope-commit-steps]'), null);
  assert.equal(document.querySelector('[data-scope-last-commit]'), null);
  assert.ok(server.calls.some((c) => c.url.endsWith('/commits/latest')));
  assert.equal(clock.ticks.length, 0);
});

test('the latest-commit read failing leaves the page working and says nothing false', async () => {
  const { document } = await open(null, { latestFails: true, openSection: true });
  assert.ok(document.querySelector('[data-scope-commit-btn]'), 'the commit panel still draws');
  assert.equal(document.querySelector('[data-scope-commit-steps]'), null);
});

test('a reload mid-watch resumes: a fresh page (second tab) reads the same registry row and draws the same steps', async () => {
  const ctx = await open(LATEST(REC(STEPS())));
  const { document, server } = ctx;
  const first = flat(document.querySelector('[data-scope-steps-head]'));
  // "reload" / second tab: a fresh module instance has no tab-local state at all
  const fresh = await import(`/static/next/stages/curate-scope.js?tab2=${Date.now()}`);
  const clock2 = { t: Date.parse('2026-10-06T13:41:00'), ticks: [] };
  fresh.setScopeClock({ now: () => clock2.t, every: (fn, ms) => { const e = { fn, ms }; clock2.ticks.push(e); return () => { clock2.ticks = clock2.ticks.filter((x) => x !== e); }; } });
  const host2 = document.createElement('div');
  document.body.appendChild(host2);
  await fresh.renderCatalogueScope(host2, 'adventureworks');
  const fold = host2.querySelector('[data-scope-collapse]');
  if (fold && fold.getAttribute('aria-expanded') === 'false') fold.click();
  await wait(150);
  assert.equal(flat(host2.querySelector('[data-scope-steps-head]')), first, 'both tabs read the same state');
  assert.equal(clock2.ticks.filter((t) => t.ms === 60000).length, 1, 'the second page resumed its own single watch');
  assert.deepEqual(posts(server), [], 'neither load wrote anything');
  clock2.ticks.length = 0;
  host2.remove();                                   // the second page goes away: its record poll stops (it is stale)
  await finish(ctx, fresh);
});

test('never more than one watch on a page: drawing the panel again does not start a second', async () => {
  const ctx = await open(LATEST(REC(STEPS())));
  const { document, clock } = ctx;
  document.querySelector('[data-scope-read-back]').click();      // redraws the panel from the registry
  await wait(150);
  assert.ok(clock.ticks.filter((t) => t.ms === 60000).length <= 1, `watches: ${clock.ticks.filter((t) => t.ms === 60000).length}`);
  await finish(ctx);
});

test('the resumed watch stops at a terminal state and does not read while the page is hidden', async () => {
  const ctx = await open(LATEST(REC(STEPS())));
  const { server, clock } = ctx;
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 1, 'precondition: the resumed watch is running');
  Object.defineProperty(ctx.document, 'hidden', { value: true, configurable: true });
  server.readBacks = 0;
  await tick(clock, 60000);
  assert.equal(server.readBacks || 0, 0, 'hidden: no read');
  Object.defineProperty(ctx.document, 'hidden', { value: false, configurable: true });
  server.record = REC(STEPS({ survey: { state: 'done', detail: 'done · report abcd1234 · 9 annotations' }, read_back: { state: 'done', detail: 'ok' } }), { state: 'done' });
  await tick(clock, 60000);
  assert.equal(clock.ticks.filter((t) => t.ms === 60000).length, 0, 'stopped at the terminal state');
});

test('an answer that is not a commit record is no commit: the page never follows a shape it does not know', async () => {
  const { document, clock } = await open({ commit: { header: { state: 'committed' }, schemas: {} }, terminal: false }, { openSection: true });
  assert.equal(document.querySelector('[data-scope-commit-steps]'), null);
  assert.equal(document.querySelector('[data-scope-last-commit]'), null);
  assert.equal(clock.ticks.length, 0);
});
