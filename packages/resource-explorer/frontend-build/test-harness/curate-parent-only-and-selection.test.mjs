/** Brief A sections 4 and 5 (owner, 2026-10-09).
 *  4. Ticking a branch meant "this branch and every component under it"; the owner wanted
 *     open-metadata-distribution/omag-server-platform alone and accepted two children by accident. The row now
 *     carries the choice "this component only" / "with its N children", the count is shown before the press, and
 *     the default is the component alone when the branch is itself a component, the children when it is grouping only.
 *  5. The checkboxes were live but ticking changed nothing near the box and the bulk bar was out of view. A selected
 *     row is marked (a cue and a short word) and the bar stays in view while anything is selected. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setUp, wait, norm, PARENT, GROUPING, verdictPosts } from './curate-part3-kit.mjs';

const both = { branches: [PARENT, GROUPING] };
const toggle = (document, path, mode) => document.querySelector(`[data-branch="${path}"] [data-branch-scope="${mode}"]`);

test('a branch that is itself a component defaults to "this component only", with the count of its children shown before the press', async () => {
  const { document } = await setUp(both);
  const row = document.querySelector('[data-branch="packages/x"]');
  assert.equal(toggle(document, 'packages/x', 'only').getAttribute('aria-pressed'), 'true');
  assert.equal(toggle(document, 'packages/x', 'with').getAttribute('aria-pressed'), 'false');
  assert.match(norm(row.querySelector('[data-branch-scope-choice]')), /this component only \/ with its 2 children/);
  assert.equal(norm(row.querySelector('[data-branch-verdict="accepted"]')), 'accept this component');
});

test('pressing accept on the component alone posts exactly one scope marked "only"', async () => {
  const { document, server } = await setUp(both);
  document.querySelector('[data-branch="packages/x"] [data-branch-verdict="accepted"]').click();
  await wait(250);
  const posts = verdictPosts(server);
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body.scope_locators, ['packages/x']);
  assert.deepEqual(posts[0].body.only_scopes, ['packages/x']);
});

test('choosing "with its 2 children" changes the button, names 3 before the press, and posts the branch meaning', async () => {
  const { document, server } = await setUp(both);
  toggle(document, 'packages/x', 'with').click();
  await wait(80);
  assert.equal(toggle(document, 'packages/x', 'with').getAttribute('aria-pressed'), 'true');
  const btn = document.querySelector('[data-branch="packages/x"] [data-branch-verdict="accepted"]');
  assert.equal(norm(btn), 'accept all 3');
  btn.click();
  await wait(80);
  assert.match(norm(document.querySelector('#wl-detail-body')), /3 components/);
  assert.equal(verdictPosts(server).length, 0, 'nothing is posted before the confirm');
  document.querySelector('[data-act="confirm"]').click();
  await wait(250);
  const post = verdictPosts(server)[0];
  assert.deepEqual(post.body.scope_locators, ['packages/x']);
  assert.equal(post.body.only_scopes, undefined, 'no "only": the verdict reaches the components under it');
});

test('a grouping-only branch has no choice to make: it means everything under it', async () => {
  const { document, server } = await setUp(both);
  const row = document.querySelector('[data-branch="packages/g"]');
  assert.equal(row.querySelector('[data-branch-scope-choice]'), null);
  assert.equal(norm(row.querySelector('[data-branch-verdict="accepted"]')), 'accept all 4');
  row.querySelector('[data-branch-verdict="accepted"]').click();
  await wait(80);
  document.querySelector('[data-act="confirm"]').click();
  await wait(250);
  assert.equal(verdictPosts(server)[0].body.only_scopes, undefined);
});

test('the choice survives a redraw', async () => {
  const { document } = await setUp(both);
  toggle(document, 'packages/x', 'with').click();
  await wait(60);
  document.querySelector('[data-select-all-shown]').click();           // forces another render
  await wait(60);
  assert.equal(toggle(document, 'packages/x', 'with').getAttribute('aria-pressed'), 'true');
});

test('the bulk accept posts "only" for exactly the ticked rows set to the component alone, and counts that reach before the press', async () => {
  const { document, server } = await setUp(both);
  for (const p of ['packages/x', 'packages/g']) {
    const c = document.querySelector(`[data-branch-select="${p}"]`); c.checked = true;
    c.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
    await wait(40);
  }
  document.querySelector('[data-selection-verdict="accepted"]').click();
  await wait(80);
  assert.match(norm(document.querySelector('#wl-detail-body')), /^5 components\. 5 will be recorded as accepted/);
  document.querySelector('[data-act="confirm"]').click();
  await wait(250);
  const post = verdictPosts(server)[0];
  assert.deepEqual(post.body.scope_locators.sort(), ['packages/g', 'packages/x']);
  assert.deepEqual(post.body.only_scopes, ['packages/x']);
});

test('a ticked row is marked where the box is: a cue and the word "selected"; unticking removes it', async () => {
  const { document } = await setUp(both);
  const row = () => document.querySelector('[data-branch="packages/x"]');
  assert.equal(row().dataset.selected, '0');
  assert.equal(row().querySelector('[data-selected-cue]'), null);
  const c = document.querySelector('[data-branch-select="packages/x"]'); c.checked = true;
  c.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await wait(60);
  assert.equal(row().dataset.selected, '1');
  assert.match(norm(row().querySelector('[data-selected-cue]')), /● selected/);
  assert.match(row().className, /bg-accent-tint/, 'the row carries the selected-row tint');
  assert.equal(document.querySelector('[data-branch="packages/g"]').dataset.selected, '0', 'only the ticked row');
  document.querySelector('[data-branch-select="packages/x"]').click();
  await wait(60);
  assert.equal(row().dataset.selected, '0');
});

test('the bulk bar stays in view while anything is selected, and is a plain bar when nothing is', async () => {
  const { document } = await setUp(both);
  const bar = () => document.querySelector('[data-selection-bar]');
  assert.equal(bar().dataset.sticky, '0');
  assert.doesNotMatch(bar().className, /sticky/);
  const c = document.querySelector('[data-branch-select="packages/g"]'); c.checked = true;
  c.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await wait(60);
  assert.equal(bar().dataset.sticky, '1');
  assert.match(bar().className, /sticky/);
  assert.match(bar().className, /top-0/);
  assert.ok(bar().querySelector('[data-selection-verdict="accepted"]'), 'the actions are on the bar that stays in view');
  assert.match(norm(bar().querySelector('[data-selected-count]')), /● 1 of 2 shown selected/);
});
