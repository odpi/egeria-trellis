/** The repository Curate plan (GET /api/projects/<slug>/curate/plan) took 25 s on egeria_git. The
 *  stage draws at once with 'plan loading · n s' (the seconds tick), the rest of the page stays
 *  usable, the plan fills in when it arrives, and a failure shows its cause. The clock is fake. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const PLAN = {
  technology_type: 'Git repository', disposition: 'using', in_population: true,
  last_surveyed_at: null, what_it_is: [], what_it_holds: [], relates: [], commits: [],
};
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp({ fail = null } = {}) {
  const { document, window } = makeDomEnvironment();
  let release; const gate = new Promise((r) => { release = r; });
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url); calls.push(u);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.includes('/curate/plan')) {
      await gate;
      if (fail) return { ok: false, status: 500, statusText: fail, json: async () => ({ detail: fail }) };
      return ok(PLAN);
    }
    if (u.includes('/components/tree')) return ok({ branches: [], topology: '' });
    if (u.includes('/blueprints')) return ok({ blueprints: [], perspectives: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const curate = await import('/static/next/stages/curate.js');
  const clock = { t: 1_000_000, ticks: [] };
  curate.setCurateClock({ now: () => clock.t, every: (fn) => { clock.ticks.push(fn); return () => { clock.ticks = clock.ticks.filter((f) => f !== fn); }; } });
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'fixture-resource', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait();
  return { document, app, release, clock, calls };
}
const loading = (d) => d.querySelector('[data-curate-plan-loading]');

test('the stage draws at once with "plan loading · 0 s" while the plan request is pending', async () => {
  const { document } = await setUp();
  assert.ok(loading(document), 'a loading line, not a blank pane');
  assert.equal(flat(loading(document)), 'plan loading · 0 s');
  assert.ok(document.querySelector('[data-curate-band="findable"]') && document.querySelector('[data-curate-band="people"]'),
    'the other bands are drawn without waiting for the plan');
});

test('the seconds tick from the clock, not from a guess', async () => {
  const { document, clock } = await setUp();
  clock.t += 7_400; clock.ticks.forEach((f) => f());
  assert.equal(flat(loading(document)), 'plan loading · 7 s');
  clock.t += 18_000; clock.ticks.forEach((f) => f());
  assert.equal(flat(loading(document)), 'plan loading · 25 s');
});

test('the rest of the page stays usable while the plan loads, and the plan fills in when it arrives', async () => {
  const { document, app, release, clock } = await setUp();
  document.querySelector('#intent-nav button[data-stage="discovery"]').click();   // a stage control still answers
  await wait(20);
  assert.equal(app.state.stage, 'discovery');
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(20);
  assert.ok(loading(document), 'back on Curate the plan is still loading');
  release();
  await wait(150);
  assert.equal(loading(document), null, 'the loading line is gone');
  assert.match(document.getElementById('curate-host').textContent, /disposition/);
  assert.equal(clock.ticks.length, 0, 'the ticker is stopped');
});

test('a failed plan shows its cause, not a dead page, and the other bands stay', async () => {
  const { document, release, clock } = await setUp({ fail: 'database is locked' });
  release();
  await wait(150);
  const host = document.getElementById('curate-host');
  assert.match(flat(host), /The plan could not be read/);
  assert.match(flat(host), /database is locked/);
  assert.equal(loading(document), null);
  assert.equal(clock.ticks.length, 0);
  assert.ok(document.querySelector('[data-curate-band="findable"]'));
});
