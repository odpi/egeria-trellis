/** Brief A follow-up 2 (owner, 2026-10-09). The "this component only / with its N children" choice was on branch
 *  rows and the bulk bar only: a leaf row that is itself a component with components beneath it still posted the
 *  plain branch meaning, so its verdict reached them. The leaf row now carries the same choice, drawn and pressed
 *  through the SAME functions as the branch row (scopeChoiceHtml, scopedVerdictOptions); the default is the
 *  component alone. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setUp, wait, norm, verdictPosts, openBranch } from './curate-part3-kit.mjs';

const SOLO = 'packages/x/solo';
const withKids = { leafRows: { [SOLO]: { children: 2, reach_low: 1, reach_accepted: 0 } } };
const leafRow = (document, p = SOLO) => document.querySelector(`[data-leaf-path="${p}"]`);
const leafToggle = (document, mode) => leafRow(document).querySelector(`[data-leaf-scope="${mode}"]`);

test('a leaf with components beneath it shows the choice, defaulting to the component alone', async () => {
  const { document } = await setUp(withKids);
  await openBranch(document);
  const row = leafRow(document);
  assert.match(norm(row.querySelector('[data-leaf-scope-choice]')), /this component only \/ with its 2 children/);
  assert.equal(leafToggle(document, 'only').getAttribute('aria-pressed'), 'true');
  assert.equal(leafToggle(document, 'with').getAttribute('aria-pressed'), 'false');
  assert.equal(norm(row.querySelector('[data-leaf-verdict="accepted"]')), 'accept');
});

test('accept on the component alone posts exactly its own scope, marked "only"', async () => {
  const { document, server } = await setUp(withKids);
  await openBranch(document);
  leafRow(document).querySelector('[data-leaf-verdict="accepted"]').click();
  await wait(250);
  const posts = verdictPosts(server);
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body.scope_locators, [SOLO]);
  assert.deepEqual(posts[0].body.only_scopes, [SOLO]);
});

test('reject on the component alone is marked "only" too', async () => {
  const { document, server } = await setUp(withKids);
  await openBranch(document);
  leafRow(document).querySelector('[data-leaf-verdict="rejected"]').click();
  await wait(250);
  assert.deepEqual(verdictPosts(server)[0].body.only_scopes, [SOLO]);
});

test('"with its 2 children" names 3 before the press, asks first, and posts the branch meaning', async () => {
  const { document, server } = await setUp(withKids);
  await openBranch(document);
  leafToggle(document, 'with').click();
  await wait(200);
  assert.equal(leafToggle(document, 'with').getAttribute('aria-pressed'), 'true', 'the choice survives the redraw');
  const btn = leafRow(document).querySelector('[data-leaf-verdict="accepted"]');
  assert.equal(norm(btn), 'accept all 3');
  assert.equal(norm(leafRow(document).querySelector('[data-leaf-verdict="rejected"]')), 'reject all');
  btn.click();
  await wait(80);
  assert.match(norm(document.querySelector('#wl-detail-body')), /^3 components, 1 of them at or below 50% confidence/);
  assert.equal(verdictPosts(server).length, 0, 'nothing is posted before the confirm');
  document.querySelector('[data-act="confirm"]').click();
  await wait(250);
  const post = verdictPosts(server)[0];
  assert.deepEqual(post.body.scope_locators, [SOLO]);
  assert.equal(post.body.only_scopes, undefined, 'no "only": the verdict reaches the components under it');
});

test('a leaf with nothing beneath it has no choice and posts as before', async () => {
  const { document, server } = await setUp({});
  await openBranch(document);
  assert.equal(leafRow(document).querySelector('[data-leaf-scope-choice]'), null);
  leafRow(document).querySelector('[data-leaf-verdict="accepted"]').click();
  await wait(250);
  const post = verdictPosts(server)[0];
  assert.deepEqual(post.body.scope_locators, [SOLO]);
  assert.equal(post.body.only_scopes, undefined);
});

test('one source: a leaf and a branch of the same shape get the same choice markup and the same press', async () => {
  await setUp({});
  const curate = await import('/static/next/stages/curate.js');
  const leaf = { path: 'a/b', children: 2, reach_low: 1, reach_accepted: 1 };
  const branch = { path: 'a/b', grouping_only: false, children: 2, components: 3, low_confidence: 1, accepted: 1 };
  assert.deepEqual(curate.leafAsBranch(leaf), branch);
  for (const mode of ['only', 'with']) {
    assert.equal(curate.scopeChoiceHtml(curate.leafAsBranch(leaf), mode, 'data-x'), curate.scopeChoiceHtml(branch, mode, 'data-x'));
  }
  assert.deepEqual(curate.scopedVerdictOptions('egeria_git', curate.leafAsBranch(leaf)), curate.scopedVerdictOptions('egeria_git', branch));
});
