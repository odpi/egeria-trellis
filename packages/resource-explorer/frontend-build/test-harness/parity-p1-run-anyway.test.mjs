/** Parity P1, PI-063: "Already up to date" offers Run anyway, which forces the run. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = () => new Promise((r) => setTimeout(r, 10));

async function load() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const api = await import('/static/re-api.js');
  const mod = await import(`/static/next/run-anyway.js?t=${Math.random()}`);
  return { document, api, mod };
}

const SKIPPED = {
  status: 'skipped', reason: 'already-fresh', activity_id: null, run_id: null,
  detail: 'secret_scan ran 3m ago and nothing changed. A re-run costs about 40s.',
};

test('only a skipped answer is a freshness skip', async () => {
  const { mod } = await load();
  assert.equal(mod.isFreshnessSkip(SKIPPED), true);
  assert.equal(mod.isFreshnessSkip({ status: 'queued', activity_id: 'a1' }), false);
  assert.equal(mod.isFreshnessSkip(null), false);
  assert.equal(mod.isFreshnessSkip(undefined), false);
});

test('runAnalysis adds force=true only when asked, for repo and database paths', async () => {
  const { api } = await load();
  const urls = [];
  globalThis.fetch = async (u, o) => { urls.push([String(u), o.method]); return { ok: true, status: 200, json: async () => ({}) }; };
  await api.runAnalysis('egeria_git', 'secret_scan', 'repo');
  await api.runAnalysis('egeria_git', 'secret_scan', 'repo', { force: true });
  await api.runAnalysis('aw', 'x', 'database', { force: true });
  assert.deepEqual(urls, [
    ['/api/projects/egeria_git/analyses/secret_scan/run', 'POST'],
    ['/api/projects/egeria_git/analyses/secret_scan/run?force=true', 'POST'],
    ['/api/databases/aw/analyses/x/run?force=true', 'POST'],
  ]);
});

test('the dialog shows the server sentence with a cue, and Run anyway forces exactly once', async () => {
  const { document, mod } = await load();
  let forced = 0;
  mod.offerRunAnyway(SKIPPED, { slug: 'egeria_git', analysisId: 'secret_scan', onForce: async () => { forced += 1; } });
  const detail = document.querySelector('[data-run-anyway-detail]');
  assert.match(detail.textContent, /ran 3m ago and nothing changed/);
  assert.match(document.querySelector('[role="dialog"]').textContent, /Already up to date/);
  const btn = document.querySelector('[data-run-anyway]');
  btn.click();
  assert.equal(btn.disabled, true, 'pressing shows an immediate pressed state');
  assert.match(btn.textContent, /Starting/);
  btn.click(); // a second press while starting does nothing
  await tick();
  assert.equal(forced, 1);
  assert.equal(document.querySelector('[data-run-anyway]'), null, 'the dialog closes once the forced run is started');
});

test('Leave it closes without forcing', async () => {
  const { document, mod } = await load();
  let forced = 0;
  mod.offerRunAnyway(SKIPPED, { slug: 's', analysisId: 'a', onForce: async () => { forced += 1; } });
  document.querySelector('[data-act="close"]').click();
  assert.equal(document.querySelector('[data-run-anyway]'), null);
  assert.equal(forced, 0);
});

test('a forced run that fails stays open, re-arms, and says why', async () => {
  const { document, mod } = await load();
  mod.offerRunAnyway(SKIPPED, { slug: 's', analysisId: 'a', onForce: async () => { throw new Error('queue is down'); } });
  const btn = document.querySelector('[data-run-anyway]');
  btn.click();
  await tick();
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Run anyway');
  assert.match(document.querySelector('[data-run-anyway-state]').textContent, /Not started: queue is down/);
});

test('the server sentence and the slug are escaped', async () => {
  const { document, mod } = await load();
  mod.offerRunAnyway({ status: 'skipped', detail: '<img src=x onerror=alert(1)>' },
    { slug: '<b id="pwn">s</b>', analysisId: 'a', onForce: async () => {} });
  assert.equal(document.querySelector('img'), null);
  assert.equal(document.querySelector('#pwn'), null);
  assert.match(document.querySelector('[data-run-anyway-detail]').textContent, /<img src=x/);
});

test('a skipped answer without a detail still gets a sentence, not a blank', async () => {
  const { document, mod } = await load();
  mod.offerRunAnyway({ status: 'skipped' }, { slug: 's', analysisId: 'a', onForce: async () => {} });
  assert.ok(document.querySelector('[data-run-anyway-detail]').textContent.trim().length > 10);
});
