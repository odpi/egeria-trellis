/** A run's publish state on screen: a cue plus a short word, from the server's proof-derived `publish_proof`
 *  (registry.publish_proofs): "queued for publish" / "published · read back" / "publish failed" with Egeria's
 *  words. The screen never says "published" before the read-back. Real modules, real jsdom; only fetch is stubbed.
 *  The Activity panel keeps re-reading while a publish is queued, so the word changes when the drain lands. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PUBLISHED_CLAIM = /(?<!not )\bpublished\b/i;
const QUEUED = { state: 'queued', word: 'queued for publish', at: '', words: '', waiting: 3 };
const READ_BACK = { state: 'read_back', word: 'published · read back', at: '2026-10-14T20:00:00', words: '' };
const FAILED = { state: 'failed', word: 'publish failed', at: '', failed: 1,
  words: 'Denied: 403 OPEN-METADATA-SECURITY-403-001 erinoverview may not create beneath the report' };

const row = (proof) => ({ id: 'a1', operation: 'analysis_run', status: 'ok', ts: '2026-10-14T19:59:00',
  entity_type: 'repo', entity_slug: 'r', summary: '2 annotation(s); 2 Egeria write(s) queued for publish',
  publish_proof: proof });

test('each state is a cue and a short word; only the read-back says published', async () => {
  makeDomEnvironment();
  const { publishProofHtml, PUBLISH_WORDS } = await import('/static/next/publish-proof.js');

  const q = publishProofHtml(QUEUED);
  assert.match(q, /data-publish-proof="queued"/);
  assert.match(q, /◔/);
  assert.match(q, />queued for publish</);
  assert.doesNotMatch(q.replace(/title="[^"]*"/g, ''), PUBLISHED_CLAIM);

  const r = publishProofHtml(READ_BACK);
  assert.match(r, /data-publish-proof="read_back"/);
  assert.match(r, /✓/);
  assert.match(r, /published · read back/);
  assert.match(r, /text-state-ok/);

  const f = publishProofHtml(FAILED);
  assert.match(f, /data-publish-proof="failed"/);
  assert.match(f, /✕/);
  assert.match(f, />publish failed</);
  assert.match(f, /OPEN-METADATA-SECURITY-403/, 'the failure carries Egeria\'s words');
  assert.match(f, /text-state-warn/);
  assert.doesNotMatch(f.replace(/title="[^"]*"/g, ''), PUBLISHED_CLAIM);

  assert.equal(publishProofHtml(null), '', 'no proof, no word');
  assert.equal(publishProofHtml({}), '');
  assert.deepEqual(Object.keys(PUBLISH_WORDS).sort(), ['failed', 'queued', 'read_back']);
});

test('a state the server could not read is never one of the three', async () => {
  makeDomEnvironment();
  const { publishProofHtml } = await import('/static/next/publish-proof.js');
  const u = publishProofHtml({ state: 'unknown', word: 'publish not established', words: 'the outbox could not be read' });
  assert.match(u, /data-publish-proof="unknown"/);
  assert.match(u, /publish not established/);
  assert.doesNotMatch(u.replace(/title="[^"]*"/g, ''), PUBLISHED_CLAIM);
});

test('the Activity row shows "queued for publish" until the read-back, then "published · read back"', async () => {
  const { document, window } = makeDomEnvironment();
  globalThis.location = window.location;
  const served = { current: [row(QUEUED)] };
  globalThis.fetch = async (u) => {
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    return ok(String(u).startsWith('/api/activity') ? served.current : {});
  };
  const api = await import('/static/re-api.js');
  api.clearCache?.();
  const activity = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  activity.refreshConfig.intervalMs = 10;

  await activity.openActivityPanel();
  const cue = () => document.querySelector('#activity-panel-body [data-publish-proof]');
  try {
    assert.equal(cue()?.dataset.publishProof, 'queued');
    assert.match(cue().textContent, /queued for publish/);
    const rowText = document.querySelector('#activity-panel-body [data-activity-id="a1"]').textContent;
    assert.doesNotMatch(rowText, PUBLISHED_CLAIM, `the row claimed published before the read-back: ${rowText}`);
    assert.ok(activity.anyRunning(served.current), 'a queued publish keeps the open list re-reading');

    // The drain lands and reads the run back: the next re-read shows the proof's word without a reload.
    served.current = [row(READ_BACK)];
    api.clearCache?.();
    for (let i = 0; i < 50 && cue()?.dataset.publishProof !== 'read_back'; i += 1) await sleep(10);
    assert.equal(cue()?.dataset.publishProof, 'read_back');
    assert.match(cue().textContent, /published · read back/);
    assert.equal(activity.anyRunning(served.current), false, 'a read-back publish ends the watch');
  } finally {
    document.querySelector('#activity-panel [data-act="close"]')?.click();
  }
});

test('a failed drain shows "publish failed" with Egeria\'s words on the Activity row', async () => {
  const { document, window } = makeDomEnvironment();
  globalThis.location = window.location;
  globalThis.fetch = async (u) => ({ ok: true, status: 200,
    json: async () => (String(u).startsWith('/api/activity') ? [row(FAILED)] : {}) });
  const api = await import('/static/re-api.js');
  api.clearCache?.();
  const activity = await import(`/static/next/stages/activity.js?t=${Math.random()}`);
  await activity.openActivityPanel();
  try {
    const cue = document.querySelector('#activity-panel-body [data-publish-proof]');
    assert.equal(cue.dataset.publishProof, 'failed');
    assert.match(cue.textContent, /publish failed/);
    assert.match(cue.textContent, /OPEN-METADATA-SECURITY-403/);
    assert.equal(activity.anyRunning([row(FAILED)]), false);
  } finally {
    document.querySelector('#activity-panel [data-act="close"]')?.click();
  }
});
