/** TR-106b: the LAST SURVEY tile reads the newest of the definitions' last runs and Resource Explorer's own walk.
 *  "not yet run" never shows while a walk exists. The walk arrives as `walk = { at, kind: 're_walk', capped }`; absent when none. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const OLD = '2026-11-01T10:00:00+00:00';
const NEW = '2026-11-19T07:30:00+00:00';
const def = (over = {}) => ({ qualified_name: 'GovActionProcess::Folder', display_name: 'Folder Survey', last_run_at: '', ...over });

async function tile(defs, walk) {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const host = document.createElement('div');
  host.innerHTML = app.surveyTilesHtml('lake', defs, null, walk);
  return host.querySelector('[data-tile-key="last_survey"]');
}
const words = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('a definition run newer than the walk: the tile shows the definition', async () => {
  const t = await tile([def({ last_run_at: NEW })], { at: OLD, kind: 're_walk', capped: false });
  assert.match(words(t), /Folder Survey/);
  assert.equal(t.dataset.tileOpens, 'def:GovActionProcess::Folder');
  assert.doesNotMatch(words(t), /Resource Explorer walk/);
});

test('a walk newer than the definition run: the tile shows the walk and opens the folder-survey card', async () => {
  const t = await tile([def({ last_run_at: OLD })], { at: NEW, kind: 're_walk', capped: false });
  assert.match(words(t), /Resource Explorer walk/);
  assert.equal(t.dataset.tileOpens, 'fs_survey');
  assert.equal(t.dataset.tileState, 'measured');
  assert.doesNotMatch(words(t), /stopped at its limit/);
});

test('a capped walk says it stopped at its limit', async () => {
  const t = await tile([def()], { at: NEW, kind: 're_walk', capped: true });
  assert.match(words(t), /Resource Explorer walk · stopped at its limit/);
});

test('no definitions and a walk: the tile appears, measured', async () => {
  const t = await tile([], { at: NEW, kind: 're_walk', capped: false });
  assert.ok(t, 'the tile is there');
  assert.equal(t.dataset.tileState, 'measured');
  assert.equal(t.dataset.tileOpens, 'fs_survey');
});

test('definitions never run and a walk: never "not yet run"', async () => {
  const t = await tile([def()], { at: NEW, kind: 're_walk', capped: false });
  assert.doesNotMatch(words(t), /not yet run/);
  assert.equal(t.dataset.tileState, 'measured');
});

test('neither: "not yet run" with definitions, no tile without', async () => {
  const t = await tile([def()], undefined);
  assert.match(words(t), /not yet run/);
  assert.equal(await tile([], undefined), null);
});
