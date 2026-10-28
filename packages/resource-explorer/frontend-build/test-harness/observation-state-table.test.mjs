/** ENRICHMENT-E3: the four observation states, one pure function, table-tested.
 *  Every row is (persisted field, current measurement) -> state. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { observationState, sameFact } = await import('/static/next/observation-state.js');

const T0 = '2026-09-30T10:00:00Z';   // the person wrote the row against this measurement
const T1 = '2026-09-30T12:00:00Z';   // a later survey run
const m = (value, at = T1) => ({ value, at });
const f = (value, extra = {}) => ({ value, source: 'user', set_at: T0, measured_value: '', measured_at: '', ...extra });

const TABLE = [
  // name, field, measurement, expected state
  ['nothing measured, nothing said', null, null, 'none'],
  ['nothing said, survey measured -> proposed', null, m('MIT'), 'proposed'],
  ['empty value counts as nothing said', f(''), m('MIT'), 'proposed'],
  ['accepted the proposal -> confirmed', f('MIT', { source: 'license_classification', measured_value: 'MIT', measured_at: T0 }), m('MIT', T0), 'confirmed'],
  ['typed a value matching the measurement -> confirmed', f('MIT', { measured_value: 'MIT', measured_at: T0 }), m('MIT', T0), 'confirmed'],
  ['typed a different value -> overridden', f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 }), m('MIT', T0), 'overridden'],
  ['SAME re-run leaves a confirmed row confirmed (gate 3)', f('MIT', { source: 'license_classification', measured_value: 'MIT', measured_at: T0 }), m('MIT', T1), 'confirmed'],
  ['override + survey re-runs with the SAME original measured value -> still overridden, NO disagree',
    f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 }), m('MIT', T1), 'overridden'],
  ['override + survey re-runs with a DIFFERENT value -> disagrees',
    f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 }), m('GPL-3.0', T1), 'disagrees'],
  ['confirmed + survey now measures something different -> disagrees',
    f('MIT', { source: 'license_classification', measured_value: 'MIT', measured_at: T0 }), m('GPL-3.0', T1), 'disagrees'],
  ['person value equals the NEW measurement -> nothing to review', f('GPL-3.0', { measured_value: 'MIT', measured_at: T0 }), m('GPL-3.0', T1), 'confirmed'],
  ['spelling/case/whitespace is not a disagreement', f('mit', { measured_value: 'MIT', measured_at: T0 }), m('  MIT ', T1), 'confirmed'],
  ['typed with nothing measured, still nothing measured -> confirmed', f('MIT'), null, 'confirmed'],
  ['typed with nothing measured, survey later measures a different value -> disagrees', f('Apache-2.0'), m('MIT', T1), 'disagrees'],
  ['typed with nothing measured, survey later agrees -> confirmed', f('MIT'), m('MIT', T1), 'confirmed'],
  ['pre-E3 accepted row (source=analysis, no stored measurement), same re-run -> confirmed',
    f('MIT', { source: 'license_classification' }), m('MIT', T1), 'confirmed'],
  ['pre-E3 accepted row, survey now different -> disagrees',
    f('MIT', { source: 'license_classification' }), m('GPL-3.0', T1), 'disagrees'],
  ['override, survey measurement gone (nothing now) -> stays overridden, no disagree',
    f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 }), null, 'overridden'],
  ['a measurement older than the one the row was made against is not later',
    f('MIT', { measured_value: 'MIT', measured_at: T1 }), m('GPL-3.0', T0), 'confirmed'],
];

for (const [name, field, measurement, expected] of TABLE) {
  test(`state: ${name}`, () => {
    assert.equal(observationState({ field, measurement }).state, expected);
  });
}

test('disagree is cleared only by a person choosing: re-stamping the stored measurement clears it', () => {
  const before = f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 });
  assert.equal(observationState({ field: before, measurement: m('GPL-3.0') }).state, 'disagrees');
  // Polling again changes nothing on its own.
  assert.equal(observationState({ field: before, measurement: m('GPL-3.0', '2026-09-30T13:00:00Z') }).state, 'disagrees');
  // The person chooses ("keep mine"): the server re-stamps measured_value/at.
  const after = f('Apache-2.0', { measured_value: 'GPL-3.0', measured_at: T1 });
  assert.equal(observationState({ field: after, measurement: m('GPL-3.0') }).state, 'overridden');
});

test('the result names the measured and stored values the drawing needs', () => {
  const r = observationState({ field: f('Apache-2.0', { measured_value: 'MIT', measured_at: T0 }), measurement: m('MIT') });
  assert.deepEqual([r.measured, r.stored], ['MIT', 'MIT']);
  assert.ok(sameFact('A  b', 'a B'));
});
