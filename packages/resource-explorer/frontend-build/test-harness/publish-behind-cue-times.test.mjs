/** Behaviour: the Publish band's "behind" cue compares survey times written in two forms (survey_time, 2026-11):
 *  older rows carry no zone and are UTC, newer rows carry `+00:00`. Both are read as UTC, so the cue is right
 *  whichever form each side has and whatever the browser's time zone. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  makeDomEnvironment();
  ensureLoaderRegistered();
  return import('/static/next/stages/publish.js');
}

test('an older report without a zone is behind a newer kept survey with one', async () => {
  const { behindCue } = await load();
  const out = behindCue({ surveyed_at: '2026-11-10T10:00:00.000001' },
    { exists: true, surveyed_at: '2026-11-10T11:00:00.000001+00:00' });
  assert.match(out, /data-publish-behind/);
});

test('the same instant in both forms is not behind', async () => {
  const { behindCue } = await load();
  assert.equal(behindCue({ surveyed_at: '2026-11-10T10:00:00' },
    { exists: true, surveyed_at: '2026-11-10T10:00:00+00:00' }), '');
});

test('a report newer than the kept survey is not behind', async () => {
  const { behindCue } = await load();
  assert.equal(behindCue({ surveyed_at: '2026-11-10T12:00:00+00:00' },
    { exists: true, surveyed_at: '2026-11-10T11:00:00' }), '');
});
