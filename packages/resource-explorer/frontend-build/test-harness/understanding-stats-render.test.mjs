/** S4b: the pure render functions of stages/understanding-stats.js. They only word what the server derived. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const m = await import('/static/next/stages/understanding-stats.js');
const text = (h) => h.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

test('table cue: one visual cue and a short word per state', () => {
  assert.equal(text(m.tableCueHtml({ state: 'current', age_days: 3 })), '✓ analyzed 3d ago');
  assert.equal(text(m.tableCueHtml({ state: 'stale', age_days: 41 })), '◐ stale 41d');
  assert.equal(text(m.tableCueHtml({ state: 'never' })), '○ never analyzed');
  assert.equal(text(m.tableCueHtml({ state: 'reset', stats_reset: '2026-10-02T08:00:00' })), '? counters reset since 10-02');
  assert.equal(text(m.tableCueHtml({ state: 'never_reset_unknown' })), '○ not analyzed · reset time not read');
  assert.equal(text(m.tableCueHtml({ state: 'not_collected' })), '? not collected');
  assert.equal(text(m.tableCueHtml(null)), '? not collected');
  assert.doesNotMatch(m.tableCueHtml({ state: 'reset' }), /never analyzed/, 'a reset never claims "never"');
});

test('database line: never says "0 behind" for a database that was not measured', () => {
  const unread = m.statsLineHtml({ state: 'not_measured', reasons: ['no_table_activity'], currency: null });
  assert.match(text(unread), /not measured · no table activity/);
  assert.doesNotMatch(unread, /0 of/);
  assert.match(text(m.statsLineHtml({ __error: 'boom' })), /could not be read: boom/);
  const none = m.statsLineHtml({ currency: { as_of: 'x', stale_days: 30, database: { total: 5, behind: 0, never: 0, stale: 0, reset_tables: 0, not_collected: 0, counters_reset: { state: 'unknown' } } } });
  assert.match(text(none), /counters reset time not read · 0 of 5 tables not analyzed in 30 days ✓/);
});

test('schema rollup words, including a schema where nothing was analyzed', () => {
  assert.equal(m.schemaRollupWords({ total: 40, never: 3, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: 41 }), '3 of 40 never · oldest 41d');
  assert.equal(m.schemaRollupWords({ total: 4, never: 4, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: null }), '4 of 4 never · none analyzed');
  assert.equal(m.schemaRollupWords({ total: 0, empty: true }), 'no tables');
});

test('over time: a single measured survey says so; no survey says so', () => {
  const one = m.statsOverTimeHtml({ currency: {}, history: [{ measured: true, run: { surveyed_at: 'a' } }] });
  assert.match(text(one), /Only one survey stored table activity/);
  const none = m.statsOverTimeHtml({ currency: {}, history: [{ measured: false, run: { surveyed_at: 'a' } }] });
  assert.match(text(none), /No survey stored table activity/);
});

const D = (o) => ({ total: 5, behind: 0, never: 0, stale: 0, reset_tables: 0, not_collected: 0, counters_reset: { state: 'never_reset' }, ...o });
const R = (d, schemas = []) => ({ currency: { as_of: '2026-10-09T10:00:00', stale_days: 30, database: d, schemas } });

test('no tick on something unmeasured or unknowable; a tick only when fully clear', () => {
  assert.match(m.statsLineHtml(R(D({}))), /✓/);
  for (const o of [{ behind: 1 }, { reset_tables: 1 }, { not_collected: 2 }]) {
    const h = m.statsLineHtml(R(D(o)));
    assert.doesNotMatch(h, /✓/, JSON.stringify(o));
  }
  assert.match(m.statsLineHtml(R(D({ reset_tables: 1 }))), /text-ink-muted font-glyph[^>]*>\?</);
  const empty = m.statsLineHtml(R(D({ total: 0 })));
  assert.match(text(empty), /no tables ∅/); assert.doesNotMatch(empty, /✓/);
  const sch = (o) => m.schemaRollupsHtml(R(D({}), [{ schema: 's', total: 3, never: 0, stale: 0, reset_tables: 0, not_collected: 0, oldest_days: 2, empty: false, ...o }]));
  assert.match(sch({}), /✓/);
  assert.doesNotMatch(sch({ reset_tables: 1 }), /✓/);
  assert.doesNotMatch(sch({ not_collected: 1 }), /✓/);
  const e = sch({ total: 0, empty: true, oldest_days: null });
  assert.match(text(e), /∅ no tables/); assert.doesNotMatch(e, /✓/);
});

test('the survey time is on the line and in the cue title; the report cell keeps the date', () => {
  assert.match(text(m.statsLineHtml(R(D({})))), /statistics · at survey 10-09 · /);
  const st = { state: 'stale', age_days: 31, last_analyzed: '2026-09-08T10:00:00', as_of: '2026-10-09T10:00:00' };
  assert.match(m.tableCueHtml(st), /title="Last analyzed 2026-09-08\. Measured at the survey of 2026-10-09, not today\."/);
  assert.equal(text(m.tableCueHtml({ ...st, state: 'current', age_days: 3, last_analyzed: '2026-10-06T10:00:00' }, { withDate: true })), '✓ analyzed 3d ago 10-06 (3d before survey)');
});

test('a partitioned parent is labelled, not "never"', () => {
  const h = m.tableCueHtml({ state: 'partitioned' });
  assert.equal(text(h), '· partitioned · see partitions');
  assert.doesNotMatch(h, /never/);
  assert.match(text(m.statsLineHtml(R(D({ partitioned: 2 })))), /2 partitioned not counted/);
});
