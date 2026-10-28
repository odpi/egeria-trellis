/** The report viewer for a report RE's own step wrote (Database Staleness, Writes Since Statistics Reset): the group
 *  header counts results by state, each row is subject, state cue, sentence and key values, and the rows that say
 *  "not measured" / "nothing found" sit behind a count. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { document } = makeDomEnvironment();
const m = await import('/static/next/stages/native-report.js');
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

const result = (type, scope, state, summary, resourceProperties = {}, explanation = '') => ({
  annotation_type: type, summary, explanation,
  detail: { additional_properties: { scope, resultState: state }, resource_properties: resourceProperties },
});
const STALE = 'Database Staleness';
const measured = (n) => result(STALE, `public.t${n}`, 'measured', `+${n * 10} rows since 2026-10-01 (100 → ${100 + n * 10})`,
  { rowsBefore: '100', rowsAfter: String(100 + n * 10), rowsChanged: String(n * 10) });
const unmeasured = (n) => result(STALE, `public.u${n}`, 'not_established', 'not measured · one snapshot only', {},
  'only one survey has measured row counts');
const REP = { report_at: '2026-10-15T10:56:00', engine_action_guid: 'ea-1', process_qualified_name: 're-database-staleness',
  annotations: [
    ...[1, 2, 3].map(measured), ...[1, 2, 3, 4].map(unmeasured),
    result(STALE, 'public.same', 'nothing_found', 'no rows changed since 2026-10-01 (5 rows)', { rowsChanged: '0' }),
    result('Writes Since Statistics Reset', 'shop', 'not_established', '', {}, 'no survey report in this run')] };

const render = () => { const root = document.createElement('div'); root.innerHTML = m.reportViewerHtml(REP); return root; };

test('a group header shows the counts by state', () => {
  const root = render();
  const g = root.querySelector('[data-report-group="Database Staleness"]');
  assert.equal(text(g.querySelector('[data-report-header]').innerHTML), 'Database Staleness · 3 measured · 1 nothing found · 4 not measured');
  assert.equal(m.stepGroupHeader('X', REP.annotations.slice(0, 3)), 'X · 3 measured');
});

test('a measured row shows the subject, a state cue and word, the sentence and the key values', () => {
  const root = render();
  const rows = [...root.querySelectorAll('[data-report-group="Database Staleness"] > table [data-report-row]')];
  assert.equal(rows.length, 3);
  const t = text(rows[0].innerHTML);
  assert.match(t, /^public\.t1 ✓ measured \+10 rows since 2026-10-01 \(100 → 110\) rows before 100 · rows after 110 · rows changed 10$/);
  assert.equal(rows[0].dataset.resultState, 'measured');
  assert.ok(rows[0].querySelector('[aria-hidden="true"]'), 'the cue glyph is there');
});

test('not-measured and nothing-found rows are collapsed behind a count, not shown', () => {
  const root = render();
  const g = root.querySelector('[data-report-group="Database Staleness"]');
  const collapsed = [...g.querySelectorAll('details[data-report-collapsed]')];
  assert.deepEqual(collapsed.map((d) => d.dataset.reportCollapsed), ['nothing_found', 'not_established']);
  assert.ok(collapsed.every((d) => !d.hasAttribute('open')));
  assert.deepEqual(collapsed.map((d) => text(d.querySelector('summary').innerHTML)), ['∅ 1 nothing found', '? 4 not measured']);
  // the visible table holds only measured rows
  assert.equal(g.querySelectorAll(':scope > table [data-result-state]:not([data-result-state="measured"])').length, 0);
  const inside = [...collapsed[1].querySelectorAll('[data-report-row]')].map((r) => text(r.innerHTML));
  assert.equal(inside[0], 'public.u1 ? not measured not measured · one snapshot only');
});

test('a stored empty summary still reads as words from the data: state, then the stored reason', () => {
  const root = render();
  const w = root.querySelector('[data-report-group="Writes Since Statistics Reset"]');
  assert.equal(text(w.querySelector('[data-report-header]').innerHTML), 'Writes Since Statistics Reset · 1 not measured');
  assert.match(text(w.querySelector('[data-report-collapsed="not_established"] [data-report-row]').innerHTML),
    /^shop \? not measured not measured · no survey report in this run$/);
});

test('a native survey report (no result state) still draws the element tables', () => {
  const native = { annotations: [{ annotation_type: 'Capture Database Table Measurements', summary: 'x',
    detail: { json_properties: JSON.stringify({ qualifiedTableName: 'db.s.t', tableSize: 2048, columnCount: 3 }) } }] };
  const root = document.createElement('div'); root.innerHTML = m.reportViewerHtml(native);
  assert.equal(root.querySelectorAll('[data-report-collapsed]').length, 0);
  assert.match(text(root.querySelector('[data-report-row]').innerHTML), /^db\.s\.t /);
});
