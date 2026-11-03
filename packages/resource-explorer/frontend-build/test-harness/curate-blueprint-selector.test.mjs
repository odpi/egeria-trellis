/** Brief section 4 (owner, 2026-10-07): the blueprint selector at the top of "what it's made of". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function curate() {
  ensureLoaderRegistered();
  const { document } = makeDomEnvironment();
  const mod = await import(`/static/next/stages/curate.js?t=${Math.random()}`);
  return { mod, document };
}

const KINDS = [
  { kind: 'deployment', name: 'Egeria Deployment Blueprint', drawn: true, state: 'proposed', perspective: 'deployment',
    source: 'recovered from 4 artifacts · 3 units' },
  { kind: 'build', name: 'Egeria Build Blueprint', drawn: false, state: 'not yet drawn', perspective: 'dev',
    source: 'from build.gradle · 12 modules' },
  { kind: 'logical', name: 'Egeria Logical Blueprint', drawn: false, state: 'not yet drawn', perspective: 'logical',
    source: 'needs your confirmation of 7 components' },
];

test('the selector names the Deployment Blueprint and lists the other two as not yet drawn', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.blueprintSelectorHtml(KINDS, 'deployment');
  const rows = [...box.querySelectorAll('[data-blueprint-kind]')];
  assert.deepEqual(rows.map((r) => r.dataset.blueprintKind), ['deployment', 'build', 'logical']);
  const text = (r) => r.textContent.replace(/\s+/g, ' ');
  assert.match(text(rows[0]), /Egeria Deployment Blueprint · recovered from 4 artifacts · 3 units · .*proposed/);
  assert.match(text(rows[1]), /Egeria Build Blueprint · from build.gradle · 12 modules · .*not yet drawn/);
  assert.match(text(rows[2]), /Egeria Logical Blueprint · needs your confirmation of 7 components · .*not yet drawn/);
});

test('only a drawn kind offers a view; the pressed one reads "viewing" (an immediate visible change)', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.blueprintSelectorHtml(KINDS, 'deployment');
  const views = box.querySelectorAll('[data-blueprint-view]');
  assert.equal(views.length, 1);
  assert.equal(views[0].textContent.trim(), '● viewing');
  assert.equal(views[0].getAttribute('aria-pressed'), 'true');
  box.innerHTML = mod.blueprintSelectorHtml(KINDS, 'logical');
  assert.equal(box.querySelector('[data-blueprint-view]').textContent.trim(), 'view');
});

test('state is a cue plus a short word, and nothing is said when the server sent no kinds', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.blueprintSelectorHtml(KINDS, 'deployment');
  assert.equal(box.querySelector('[data-blueprint-kind="deployment"] [data-cue]').dataset.cue, 'proposal');
  assert.equal(box.querySelector('[data-blueprint-kind="build"] [data-cue]').dataset.cue, 'unrun');
  assert.equal(mod.blueprintSelectorHtml([], ''), '');
  assert.equal(mod.blueprintSelectorHtml(undefined, ''), '');
});

test('accepting a blueprint is offered only for a blueprint with an accepted node', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  const base = { perspective: 'deployment', cluster_name: 'core', members: ['a'], child_status: [], verdict: null };
  box.innerHTML = mod.blueprintRowHtml({ ...base, member_status: [{ slug: 'a', verdict: null }] });
  assert.equal(box.querySelector('[data-blueprint-verdict="accepted"]'), null);
  assert.match(box.querySelector('[data-blueprint-write-blocked]').textContent, /no accepted component · nothing to publish/);
  assert.ok(box.querySelector('[data-blueprint-verdict="rejected"]'), 'reject is always available');
  box.innerHTML = mod.blueprintRowHtml({ ...base, member_status: [{ slug: 'a', verdict: { verdict: 'accepted' } }] });
  assert.equal(box.querySelector('[data-blueprint-verdict="accepted"]').textContent.trim(), 'accept');
  assert.equal(box.querySelector('[data-blueprint-write-blocked]'), null);
});
