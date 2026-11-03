/** Brief S0 / 5b: a count on a tab label and the same count in its section header come from ONE function.
 *  Pure functions in, answers out; plus the header functions the sections really use. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const load = async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const counts = await import('/static/next/stages/curate-counts.js');
  const curate = await import('/static/next/stages/curate.js');
  const scope = await import('/static/next/stages/curate-scope.js');
  return { counts, curate, scope };
};

const REPO_VIEW = {
  manifest: { files: 20, folders: 3 },
  rows: [
    { locator: 'a', candidate: true },
    { locator: 'b', candidate: true, choice: 'include' },
    { locator: 'c', role: 'container' },
    { locator: 'd', candidate: false },
    { locator: 'e', candidate: true, choice: 'leave_out' },
  ],
};
const DB_VIEW = {
  declared: { declared: true, by: 'dan', at: '2026-10-11T00:00:00Z' },
  counts: { schemas_offered: 29, schemas_catalogue: 3, schemas_leave_out: 0, schemas_undecided: 26 },
};

test('repo chosen = the number the section header prints (savedCountHtml), from the same record', async () => {
  const { counts, curate } = await load();
  const c = counts.contentsChosen('repo', REPO_VIEW);
  assert.deepEqual(c, { state: 'count', n: 23 });
  const header = curate.savedCountHtml(REPO_VIEW).replace(/<[^>]+>/g, '');
  assert.match(header, new RegExp(`^\\s*${c.n} included`));
  // the header changes when the record changes, and so does the tab
  const v2 = { ...REPO_VIEW, manifest: { files: 1, folders: 0 } };
  assert.match(curate.savedCountHtml(v2).replace(/<[^>]+>/g, ''), /^\s*1 included/);
  assert.equal(counts.contentsChosen('repo', v2).n, 1);
});

test('db chosen = the "N of M schemas" the scope header prints; undecided is the server\'s own number', async () => {
  const { counts, scope } = await load();
  const c = counts.contentsChosen('db', DB_VIEW);
  assert.equal(c.n, 3);
  assert.match(scope.scopeHeaderText(DB_VIEW), /^Your scope: 3 of 29 schemas/);
  assert.deepEqual(counts.contentsUndecided('db', DB_VIEW), { state: 'count', n: 26 });
});

test('repo undecided counts candidate/container rows with no choice of their own', async () => {
  const { counts } = await load();
  assert.deepEqual(counts.contentsUndecided('repo', REPO_VIEW), { state: 'count', n: 2 });
});

test('absence is not zero: no view/plan reads "reading"; a read zero is a count of 0; no such parts is "none yet"', async () => {
  const { counts } = await load();
  for (const kind of ['repo', 'db']) {
    assert.equal(counts.contentsChosen(kind, null).state, 'reading');
    assert.equal(counts.contentsUndecided(kind, undefined).state, 'reading');
    assert.equal(counts.contentsChosen(kind, {}).state, 'reading');
  }
  assert.equal(counts.structureAwaiting('repo', null).state, 'reading');
  assert.deepEqual(counts.structureAwaiting('repo', { awaiting: 0 }), { state: 'count', n: 0 });
  assert.equal(counts.structureAwaiting('repo', { what_it_is: [] }).state, 'unknown', 'a plan that cannot say is not 0');
  assert.deepEqual(counts.structureAwaiting('repo', { awaiting: { components: 2, blueprints: 1, deps: 3 } }), { state: 'count', n: 6 });
  assert.equal(counts.structureAwaiting('db', { awaiting: 5 }).state, 'none_yet');
  assert.equal(counts.contentsChosen('filesystem', REPO_VIEW).state, 'none_yet');
  assert.equal(counts.contentsChosen('repo', { manifest: { files: 0, folders: 0 } }).n, 0);
});

test('countWords: a cue key and a short word for each of the four answers', async () => {
  const { counts } = await load();
  assert.deepEqual(counts.countWords({ state: 'count', n: 4 }, 'awaiting you'), { cue: 'measured', word: '4 awaiting you' });
  assert.equal(counts.countWords({ state: 'count', n: 0 }, 'chosen').cue, 'measured_zero');
  assert.deepEqual(counts.countWords({ state: 'reading' }, 'chosen'), { cue: 'running', word: 'reading' });
  assert.equal(counts.countWords({ state: 'unknown' }, 'x').word, 'not counted');
  assert.equal(counts.countWords({ state: 'none_yet' }, 'x').word, 'none yet');
});

test('tabCountsText: Contents carries chosen and undecided, Structure awaiting, Describe nothing', async () => {
  const { counts } = await load();
  const ctx = { kind: 'repo', view: REPO_VIEW, plan: { awaiting: 4 } };
  assert.equal(counts.tabCountsText('curate_contents', ctx), '23 chosen · 2 undecided');
  assert.equal(counts.tabCountsText('curate_structure', ctx), '4 awaiting you');
  assert.equal(counts.tabCountsText('curate_describe', ctx), '');
  assert.equal(counts.tabCountsText('curate_contents', { kind: 'repo' }), 'reading · reading');
});
