/** Quality-screens read item 8 (2026-10-30): the numbers behind an answer showed stored times raw,
 *  "proposals surveyed at 2026-10-30T11:12:58.965452", where Curate shows the same instant as "2026-10-30 11:12". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

async function load() {
  makeDomEnvironment();
  await loadAppModule();
  return import('/static/next/app.js');
}

test('a stored time reads in the house format, with no T and no seconds or microseconds', async () => {
  const { fmtScalar } = await load();
  assert.equal(fmtScalar('2026-10-30T11:12:58.965452', 'proposals_surveyed_at'), '2026-10-30 11:12');
  assert.equal(fmtScalar('2026-10-30T11:12:58+00:00', 'surveyed_at'), '2026-10-30 11:12');
});

test('everything else reads as before', async () => {
  const { fmtScalar } = await load();
  assert.equal(fmtScalar(1236, 'column_count'), '1,236');
  assert.equal(fmtScalar('postgres', 'ran_as'), 'postgres');
  assert.equal(fmtScalar('2026-10-30', 'date'), '2026-10-30');
  assert.equal(fmtScalar(true), 'yes');
});

test("a raw-measures answer line shows a stored time in the house format (data_class_match, change_rates)", async () => {
  await load();
  const { scalarMeasures } = await import('/static/next/envelope.js');
  const line = scalarMeasures({ from_surveyed_at: '2026-10-13T13:59:00.394550', to_surveyed_at: '2026-10-20T21:07:32.063533',
    column_count: 1236 });
  assert.equal(line, 'from surveyed at 2026-10-13 13:59 · to surveyed at 2026-10-20 21:07 · column count 1236');
  assert.ok(!/T\d\d:|\.\d{6}/.test(line), line);
});

test('QS-4: a number of days reads as days to one place, not "interval days 7.2976"', async () => {
  await load();
  const { scalarMeasures } = await import('/static/next/envelope.js');
  const line = scalarMeasures({ interval_days: 7.2976, table_count: 68 });
  assert.equal(line, 'interval 7.3 days · table count 68');
});
