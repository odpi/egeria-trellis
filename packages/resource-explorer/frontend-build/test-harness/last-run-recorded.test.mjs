/** A run credited only from step_runs has status "recorded": it proves the
 *  step ran, not that it succeeded (step_runs rows are written for a step that
 *  raised too). The card must show "ran ..." with no success check and no
 *  warning, and the row glyph must be the neutral one, not "measured".
 *  `lastRunHtml` and `analysisRowGlyph` gained `export`, no other change.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const WHEN = new Date(Date.now() - 2 * 86400000).toISOString();

async function html(status) {
  makeDomEnvironment();
  const app = await loadAppModule();
  return { app, out: app.lastRunHtml({ last_run_at: WHEN, last_run_status: status }) };
}

test('recorded: says it ran, no check, no warning', async () => {
  const { out } = await html('recorded');
  assert.match(out, /ran /);
  assert.ok(!out.includes('✓'), out);
  assert.ok(!out.includes('⚠'), out);
});

test('ok keeps its check; error keeps its warning', async () => {
  assert.ok((await html('ok')).out.includes('✓'));
  const err = (await html('error')).out;
  assert.ok(err.includes('⚠') && !err.includes('✓'));
});

test('analysisRowGlyph: recorded is neutral, ok measured, error error', async () => {
  const { app } = await html('ok');
  const g = (s) => app.analysisRowGlyph({ last_run_at: WHEN, last_run_status: s });
  assert.deepEqual(g('recorded'), app.factGlyph('unclassified'));
  assert.notDeepEqual(g('recorded'), app.factGlyph('measured'));
  assert.deepEqual(g('ok'), app.factGlyph('measured'));
  assert.deepEqual(g('error'), app.factGlyph('error'));
});
