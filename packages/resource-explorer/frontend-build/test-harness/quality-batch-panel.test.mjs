/** Behaviour: Quality Q3's batch panel on a database work list (stages/quality-batch.js).
 *  Real module in a real DOM; the API is a stub shaped like /api/quality-batch/price and /run and the schedules route.
 *  Pinned: two priced rows shown before anything runs, each figure saying what it is (measured or not, size unknown
 *  never 0); a row that cannot run says why and is not counted in "Run on N"; the press's outcome member by member;
 *  the skip rule said before scheduling, and the schedule cue from the server's read-back; escaping. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function row(slug, over = {}) {
  return {
    entity_slug: slug, display_name: slug, can_run: true, blocked: '',
    tables: { count: 2, words: '2 tables · as of 2026-10-20 09:00Z' },
    size: { bytes: 98304, unknown_tables: 0, words: '96.0 KB' },
    time: { seconds: null, basis: 'not measured', runs: 0, words: 'time not measured yet · the first run sets it' },
    budget_words: "each table's profile stops at 5 min",
    change: { unchanged: false, words: 'never had a quality run' },
    words: '2 tables · as of 2026-10-20 09:00Z · 96.0 KB · time not measured yet · the first run sets it',
    ...over,
  };
}

const PRICE = {
  definition: 'DatabaseQualitySurvey', runnable: 2,
  total: { seconds: null, words: 'not measured yet · the first batch sets the price' },
  rows: [
    row('coco_pharma', { display_name: 'Coco Pharma' }),
    row('adventureworks', {
      display_name: 'AdventureWorks',
      size: { bytes: 4194304, unknown_tables: 1, words: '4.0 MB · 1 table(s) size unknown · never analyzed' },
      time: { seconds: 90, basis: 'measured', runs: 1, words: 'about 1m 30s · measured over 1 run(s)' },
      change: { unchanged: true, words: 'unchanged since its last quality run on 2026-10-20 10:00Z' },
    }),
  ],
};

function fakeApi({ price = PRICE, run = null, schedule = null } = {}) {
  const calls = { price: [], run: [], schedule: [] };
  return {
    calls,
    async priceQualityBatch(body) { calls.price.push(body); return price; },
    async runQualityBatch(body) {
      calls.run.push(body);
      if (run) return run(body);
      return {
        set_id: 'set-abc', summary: '2 queued',
        members: price.rows.map((r) => ({ entity_slug: r.entity_slug, display_name: r.display_name, outcome: 'queued',
          words: `queued · ${r.words}` })),
      };
    },
    async saveSchedule(...args) {
      calls.schedule.push(args);
      if (schedule) return schedule(...args);
      return { status: 'ok', schedule: args[3], entity_type: args[0], entity_slug: args[1], analysis_id: args[2] };
    },
  };
}

async function setUp(opts = {}, hooks = {}) {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const mod = await import('/static/next/stages/quality-batch.js');
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  const api = fakeApi(opts);
  await mod.mountQualityBatch(slot, { slug: 'quality-targets', display_name: 'Quality targets' }, api, hooks);
  return { slot, api, mod };
}

test('two priced rows are shown before anything is queued, each figure saying what it is', async () => {
  const { slot, api } = await setUp();
  assert.deepEqual(api.calls.price, [{ work_list_slug: 'quality-targets' }]);
  assert.equal(api.calls.run.length, 0);
  const rows = slot.querySelectorAll('[data-price-rows] tbody tr');
  assert.equal(rows.length, 2);
  const works = slot.querySelector('tr[data-slug="adventureworks"]');
  assert.equal(text(works.querySelector('[data-size]')), '4.0 MB · 1 table(s) size unknown · never analyzed');
  assert.equal(works.querySelector('[data-time]').dataset.basis, 'measured');
  assert.equal(works.querySelector('[data-change]').dataset.unchanged, 'true');
  const pharma = slot.querySelector('tr[data-slug="coco_pharma"]');
  assert.equal(text(pharma.querySelector('[data-time]')), 'time not measured yet · the first run sets it');
  assert.equal(text(slot.querySelector('[data-total]')), 'not measured yet · the first batch sets the price');
  assert.equal(text(slot.querySelector('[data-budget]')), "each table's profile stops at 5 min.");
  assert.match(text(slot), /Nothing runs until you confirm\./);
  assert.equal(text(slot.querySelector('[data-act="run"]')), 'Run on 2');
});

test('a row that cannot run says why on its own row and is not counted', async () => {
  const blocked = row('adventureworks', { can_run: false,
    words: 'cannot run · no stored credential · register one before a batch can run it' });
  const { slot } = await setUp({ price: { ...PRICE, runnable: 1, rows: [PRICE.rows[0], blocked] } });
  const tr = slot.querySelector('tr[data-slug="adventureworks"]');
  assert.equal(tr.dataset.canRun, 'false');
  assert.equal(text(tr.querySelector('[data-blocked]')),
    'cannot run · no stored credential · register one before a batch can run it');
  assert.equal(text(slot.querySelector('[data-act="run"]')), 'Run on 1');
});

test('nothing runnable disables Run', async () => {
  const { slot } = await setUp({ price: { ...PRICE, runnable: 0, rows: [row('x', { can_run: false, words: 'cannot run · y' })] } });
  assert.equal(slot.querySelector('[data-act="run"]').disabled, true);
});

test('the press shows every member with its outcome and hands the run set on', async () => {
  const started = [];
  const { slot, api } = await setUp({
    run: () => ({ set_id: 'set-1', summary: '1 queued · 1 not permitted: adventureworks not permitted · zone',
      members: [
        { entity_slug: 'coco_pharma', display_name: 'Coco Pharma', outcome: 'queued', words: 'queued · 2 tables' },
        { entity_slug: 'adventureworks', display_name: 'AdventureWorks', outcome: 'not permitted', words: 'not permitted · zone' },
      ] }),
  }, { onStarted: (id) => started.push(id) });
  slot.querySelector('[data-act="run"]').click();
  await tick(5);
  assert.deepEqual(api.calls.run, [{ work_list_slug: 'quality-targets' }]);
  assert.equal(slot.querySelector('[data-member="adventureworks"]').dataset.outcome, 'not permitted');
  assert.match(text(slot.querySelector('[data-member="adventureworks"]')), /not permitted · zone/);
  assert.equal(text(slot.querySelector('[data-set]')), 'set-1');
  assert.deepEqual(started, ['set-1']);
  assert.equal(slot.querySelector('[data-act="run"]').disabled, true);    // one press, one batch
});

test('a refused press says it was not queued and lets the person try again', async () => {
  const { slot } = await setUp({ run: () => { throw new Error('server unreachable'); } });
  slot.querySelector('[data-act="run"]').click();
  await tick(5);
  assert.match(text(slot.querySelector('[data-run-failed]')), /The batch was not queued: server unreachable/);
  assert.equal(slot.querySelector('[data-act="run"]').disabled, false);
});

test('the skip rule is said before scheduling, and the cue is the server read-back', async () => {
  const { slot, api, mod } = await setUp();
  assert.match(text(slot.querySelector('[data-schedule]')), new RegExp(mod.SKIP_RULE_WORDS.replace(/\./g, '\\.')));
  slot.querySelector('[data-cadence]').value = 'daily';
  slot.querySelector('[data-act="schedule"]').click();
  await tick(5);
  assert.deepEqual(api.calls.schedule, [['work_list', 'quality-targets', 'DatabaseQualitySurvey', 'daily', true, 'survey']]);
  const cue = slot.querySelector('[data-schedule-cue]');
  assert.equal(cue.dataset.saved, 'true');
  assert.equal(text(cue), 'scheduled daily · DatabaseQualitySurvey across this list · unchanged databases are skipped');
});

test('a refused schedule says so on the cue', async () => {
  const { slot } = await setUp({ schedule: () => { throw new Error('No work list'); } });
  slot.querySelector('[data-act="schedule"]').click();
  await tick(5);
  assert.equal(text(slot.querySelector('[data-schedule-cue]')), 'not scheduled · No work list');
});

test('a price that cannot be read offers nothing to run', async () => {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const mod = await import('/static/next/stages/quality-batch.js');
  const slot = document.createElement('div');
  await mod.mountQualityBatch(slot, { slug: 'l' }, { async priceQualityBatch() { throw new Error('registry down'); } });
  assert.match(text(slot.querySelector('[data-price-failed]')), /registry down/);
  assert.equal(slot.querySelector('[data-act="run"]'), null);
});

test('names from the registry are escaped', async () => {
  const { slot } = await setUp({ price: { ...PRICE, rows: [row('<b>x</b>', { display_name: '<img src=x onerror=1>' })] } });
  assert.equal(slot.querySelector('img'), null);
  assert.equal(slot.querySelector('b'), null);
});
