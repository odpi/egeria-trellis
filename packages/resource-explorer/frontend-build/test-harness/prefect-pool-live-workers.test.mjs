/** 2026-11-13 (BRIEF-RE-PREFECT-POOL-LIVE-WORKERS): the shell banner's Prefect pool line. The server counts LIVE
 *  workers, not registrations; with none live the banner says so in the server's own words, and says what runs in
 *  Resource Explorer's process instead (nothing waits). An unread or unreachable pool claims nothing.
 *  Real module; nothing stubbed but the status object. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();

const RUNS_LOCALLY = 'Prefect-dispatched steps (Soda data-quality, Great Expectations validation, any step marked '
  + "executes_at=prefect) run in Resource Explorer's process instead: no Prefect run history, retries or cancel.";
const POOL = (over = {}) => ({ reachable: true, live_count: 0, registered_count: 3, never_heard_from: 0, worker_count: 0,
  summary: 'no live worker: 3 registered, last heartbeat 32 days ago (2026-10-12T12:17:00Z)',
  runs_locally_instead: ['Soda data-quality'], runs_locally_words: RUNS_LOCALLY, ...over });
const B = { display_name: 'Foundations', state: 'present', present: true, last_healed_at: '', last_heal_result: '' };
const STATUS = (pool, over = {}) => ({ reinitializing: false, healing: '', egeria_reachable: true,
  batches: { foundations: B }, prefect_pool: pool, ...over });

async function loadShell() {
  const { document } = makeDomEnvironment();
  const mod = await import(`/static/next/shell-status.js?t=${Math.random()}`);
  return { document, mod };
}

test('no live worker: one warning line in the server words, with what runs locally instead, and no run button', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(POOL()));
  const bar = document.getElementById('bootstrap-banner');
  assert.ok(bar, 'a pool with no live worker is never only a log line');
  const lines = bar.querySelectorAll('[data-prefect-pool-line]');
  assert.equal(lines.length, 1);
  assert.equal(text(bar.querySelector('[data-prefect-pool-word]')),
    'Prefect: no live worker: 3 registered, last heartbeat 32 days ago (2026-10-12T12:17:00Z)');
  assert.equal(text(bar.querySelector('[data-prefect-pool-runs-locally]')), RUNS_LOCALLY);
  assert.doesNotMatch(text(bar), /will wait|cannot run|not run/, 'nothing waits: the steps run locally');
  assert.equal(bar.querySelector('[data-bootstrap-run]'), null, 'a bootstrap run does not start a worker');
  assert.equal(bar.dataset.bootstrapKind, 'prefect-pool');
});

const UNREACHABLE = (over = {}) => POOL({ reachable: false, live_count: null, registered_count: null,
  never_heard_from: null, worker_count: null, summary: 'server not reachable; the pool could not be checked',
  runs_locally_words: 'Runs are not recorded in Prefect; dispatched steps (Soda data-quality) run in Resource '
    + "Explorer's process instead.", last_check_error: 'connection refused', ...over });

test('an unreachable Prefect server is a warning of its own, never silence and never "no live worker"', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(UNREACHABLE()));
  const bar = document.getElementById('bootstrap-banner');
  assert.ok(bar, '"Prefect not running" must not be silent');
  const line = bar.querySelector('[data-prefect-pool-line]');
  assert.equal(line.dataset.prefectPoolLine, 'unreachable');
  assert.equal(text(bar.querySelector('[data-prefect-pool-word]')), 'Prefect: server not reachable; the pool could not be checked');
  assert.match(text(bar.querySelector('[data-prefect-pool-runs-locally]')), /^Runs are not recorded in Prefect; dispatched steps/);
  assert.doesNotMatch(text(line), /no live worker/);
  assert.match(line.className, /text-state-warn/);
});

test('a live pool, an unread pool, and Prefect switched off draw no pool line', async () => {
  const { document, mod } = await loadShell();
  for (const pool of [POOL({ live_count: 1, summary: '1 live worker(s) of 3 registered; last heartbeat 20 seconds ago' }),
    undefined, POOL({ reachable: null, live_count: null }),
    UNREACHABLE({ enabled: false }), POOL({ enabled: false })]) {
    mod.renderBootstrapBanner(document, STATUS(pool));
    assert.equal(document.getElementById('bootstrap-banner'), null, JSON.stringify(pool));
  }
});

test('the pool line rides along with a missing batch, and clears when a worker is live again', async () => {
  const { document, mod } = await loadShell();
  const missing = { foundations: { ...B, state: 'missing', present: false } };
  mod.renderBootstrapBanner(document, STATUS(POOL(), { batches: missing }));
  const bar = document.getElementById('bootstrap-banner');
  assert.ok(bar.querySelector('[data-prefect-pool-line]') && bar.querySelector('[data-bootstrap-run]'));
  mod.renderBootstrapBanner(document, STATUS(POOL({ live_count: 1 })));
  assert.equal(document.getElementById('bootstrap-banner'), null);
});

test('server text reaches the page as text, never markup', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(POOL({ summary: '<img src=x onerror=alert(1)>', runs_locally_words: '<b>x</b>' })));
  const bar = document.getElementById('bootstrap-banner');
  assert.equal(bar.querySelector('img'), null);
  assert.equal(bar.querySelector('b'), null);
});

test('a live worker that advertises no commit is a warning line in the server words', async () => {
  const { document, mod } = await loadShell();
  const summary = '1 live worker(s) of 1 registered; last heartbeat 20 seconds ago; commit parity not checked: '
    + 'worker re-host-worker-laz advertises no commit, so a step dispatched to it may run on other code than this '
    + "Resource Explorer's";
  mod.renderBootstrapBanner(document, STATUS(POOL({ live_count: 1, summary, parity_unchecked: ['re-host-worker-laz'],
    runs_locally_instead: [], runs_locally_words: '' })));
  const line = document.querySelector('[data-prefect-pool-line]');
  assert.equal(line.dataset.prefectPoolLine, 'parity_unchecked');
  assert.equal(text(document.querySelector('[data-prefect-pool-word]')), `Prefect: ${summary}`);
  assert.equal(document.querySelector('[data-prefect-pool-runs-locally]'), null);
  mod.renderBootstrapBanner(document, STATUS(POOL({ live_count: 1, parity_unchecked: [] })));
  assert.equal(document.getElementById('bootstrap-banner'), null, 'a worker on a known commit: no line');
});

test('a pool the server says does not exist is its own warning, not "server not reachable"', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(POOL({ reachable: true, pool_exists: false, live_count: 0, registered_count: 0,
    summary: "pool resource-explorer-pool doesn't exist" })));
  const line = document.querySelector('[data-prefect-pool-line]');
  assert.equal(line.dataset.prefectPoolLine, 'pool_missing');
  assert.equal(text(document.querySelector('[data-prefect-pool-word]')), "Prefect: pool resource-explorer-pool doesn't exist");
  assert.doesNotMatch(text(line), /not reachable/);
});

test('a live worker a step would not be sent to is a warning line with the refusal and what runs locally', async () => {
  // Architect ruling 2026-11-14: the banner reads the same decision dispatch makes (hedwig: the default deployment
  // on default-agent-pool while RE is configured for resource-explorer-pool, every step ran locally, no warning).
  const { document, mod } = await loadShell();
  const refused = "default deployment 'RE Survey Flow/re-survey-step-deployment' is on pool default-agent-pool but "
    + 'Resource Explorer is configured for pool resource-explorer-pool';
  const summary = `1 live worker(s) of 1 registered; last heartbeat 20 seconds ago; but a dispatched step runs locally: ${refused}`;
  mod.renderBootstrapBanner(document, STATUS(POOL({ live_count: 1, registered_count: 1, summary,
    dispatch_refused: refused, parity_unchecked: ['re-host-worker-hedwig'] })));
  const line = document.querySelector('[data-prefect-pool-line]');
  assert.equal(line.dataset.prefectPoolLine, 'dispatch_refused', 'the refusal wins over parity-unchecked');
  assert.equal(text(document.querySelector('[data-prefect-pool-word]')), `Prefect: ${summary}`);
  assert.equal(text(document.querySelector('[data-prefect-pool-runs-locally]')), RUNS_LOCALLY);
});

test('RE worker live on another pool than RE dispatches to: a warning line in the server words', async () => {
  const { document, mod } = await loadShell();
  const summary = '1 live worker(s) of 1 registered; last heartbeat 20 seconds ago; Resource Explorer\'s worker(s) '
    + 're-worker-0123456789ab-hedwig on pool default-agent-pool are live on another pool than resource-explorer-pool';
  mod.renderBootstrapBanner(document, STATUS(POOL({ live_count: 1, registered_count: 1, summary, parity_unchecked: [],
    workers_elsewhere: ['re-worker-0123456789ab-hedwig on pool default-agent-pool'] })));
  const line = document.querySelector('[data-prefect-pool-line]');
  assert.equal(line.dataset.prefectPoolLine, 'workers_elsewhere');
  assert.equal(text(document.querySelector('[data-prefect-pool-word]')), `Prefect: ${summary}`);
});

/* ── The banner's close box (2026-11-17) ───────────────────────────────── */

const PARITY = { enabled: true, reachable: true, pool_exists: true, live_count: 1, parity_unchecked: ['w1'],
  summary: 'commit parity not checked: worker w1 advertises no commit' };

test('banner close box: closing hides the banner, and the next poll does not bring it back', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(PARITY));
  assert.ok(document.getElementById('bootstrap-banner'), 'shown first');
  document.querySelector('[data-banner-close]').click();
  assert.equal(document.getElementById('bootstrap-banner'), null, 'closed');
  mod.renderBootstrapBanner(document, STATUS(PARITY));
  assert.equal(document.getElementById('bootstrap-banner'), null, 'the same condition stays closed on the next poll');
});

test('banner close box: a different condition draws the banner again', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS(PARITY));
  document.querySelector('[data-banner-close]').click();
  mod.renderBootstrapBanner(document, STATUS({ ...PARITY, live_count: 0, parity_unchecked: [], summary: 'no live worker' }));
  assert.ok(document.getElementById('bootstrap-banner'), 'a changed condition is shown');
});
