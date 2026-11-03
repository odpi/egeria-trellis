/** The single-page Understanding (the layout without the leading tabs: gate off) must stay byte-identical to main.
 *  Its own file: the names-toggle ids are a per-process counter, so it runs db then repo in a fresh process, the
 *  order the fixture was generated in (understanding-single-snapshot.mjs, run against main 1eace306). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { snapshotUnderstanding } from './understanding-single-snapshot.mjs';

test('gate off: the single-page Understanding (db and repo) is byte-identical to main', async () => {
  const want = JSON.parse(readFileSync(new URL('./understanding-single.fixture.json', import.meta.url), 'utf8'));
  assert.equal(await snapshotUnderstanding('db'), want.db);
  assert.equal(await snapshotUnderstanding('repo'), want.repo);
});
