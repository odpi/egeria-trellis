/** Brief section 5 (project owner, 2026-10-07): the verdict row says what accepting did to the
 *  element's zones, and says it from the promotion's PROOF ROW (the server read the element's zones
 *  before and after). Nothing here builds the sentence from the click.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function curate() {
  ensureLoaderRegistered();
  const { document } = makeDomEnvironment();
  const mod = await import(`/static/next/stages/curate.js?t=${Math.random()}`);
  return { mod, document };
}

const NONE = 'accepted · zones left to Egeria · everyone visible';

test('a component row shows the words the proof row carries, with a check cue', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.leafRowHtml({
    path: 'a/b', type: 'Service', confidence: 80, verdict: { verdict: 'accepted', decided_by: 'dan' },
    promotion: { status: 'promoted', words: NONE },
  });
  const el = box.querySelector('[data-promotion="promoted"]');
  assert.ok(el, 'the promotion words must be on the row');
  assert.match(el.textContent, /accepted · zones left to Egeria · everyone visible/);
  assert.equal(el.querySelector('[data-cue]').dataset.cue, 'measured');
});

test('a configured zone is named, and a refused promotion is a warning cue never a check', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.blueprintRowHtml({
    perspective: 'deployment', cluster_name: 'Egeria Deployment Blueprint', members: [], member_status: [],
    verdict: { verdict: 'accepted' }, materialized: { guid: 'g1' },
    promotion: { status: 'promoted', words: 'accepted · zone team-zone' },
  });
  assert.match(box.querySelector('[data-promotion]').textContent, /accepted · zone team-zone/);
  box.innerHTML = mod.blueprintRowHtml({
    perspective: 'deployment', cluster_name: 'x', members: [], member_status: [],
    verdict: { verdict: 'accepted' }, materialized: { guid: 'g1' },
    promotion: { status: 'error', words: 'accepted · zones not changed · Egeria did not accept clearing the draft zone' },
  });
  const cue = box.querySelector('[data-promotion="error"] [data-cue]');
  assert.equal(cue.dataset.cue, 'error');
});

test('a row with no promotion recorded says nothing about zones', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.leafRowHtml({ path: 'a/b', verdict: null, promotion: null });
  assert.equal(box.querySelector('[data-promotion]'), null);
});

test('left_as_is is a neutral "left as is" cue with the sentence on hover, never the error cue', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  const words = "zones left as they are · egeria-runtime · not RE's draft zone";
  box.innerHTML = mod.leafRowHtml({ path: 'a/b', verdict: { verdict: 'accepted' }, promotion: { status: 'left_as_is', words } });
  const cue = box.querySelector('[data-promotion="left_as_is"] [data-cue]');
  assert.equal(cue.dataset.cue, 'unrun');
  assert.equal(cue.textContent.trim().endsWith('left as is'), true);
  assert.equal(cue.getAttribute('title'), words);
  assert.equal(box.querySelector('[data-cue="error"]'), null);
});
