/** DESIGN-VALUE-PROFILE-SCREEN-2026-10-30 §5: a question with several of RE's own analyses offers "the numbers behind
 *  this" per analysis when one has numbers at the question's level. Q-0079 (field level) is answered first by
 *  data_class_match, whose numbers are four scalars; the value profile's are one row per table. The row's own button
 *  keeps opening the first analysis; value_profile gets a line of its own, and its panel's rows and footer open the
 *  Value profile tab. Real app.js and envelope.js; fetch answers the measurements route with the container reader's
 *  rows, written by the real Python builder (value-profile.fixture.json). */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FIX = JSON.parse(readFileSync(new URL('./value-profile.fixture.json', import.meta.url), 'utf-8'));
const SLUG = 'coco_ods';
const tick = (ms = 80) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const fact = (id, over = {}) => ({ analysis_id: id, state: 'measured', is_known: true, headline: '', value: {},
  last_run_at: '2026-10-30T11:12:58', as_of: '', can_run: [], note: '', runnable: true, runnable_reason: '',
  library: false, numbers_at_level: false, ...over });
const Q0079 = { question: 'What do the columns actually contain?', id: 'Q-0079', kind: 'partial', stage: 'Analysis',
  levels: ['field'], analysis_ids: ['data_class_match', 'value_profile', 'reference_data_match'],
  answering_mechanism: 'Database Catalog Query + Value Profiling' };
const env = (facts) => ({ answerable: true, facts, caveat: '', sources: [] });
const FACTS = [
  fact('data_class_match', { headline: '12 of 456 columns matched a data class.' }),
  fact('value_profile', { headline: '7 of 10 columns profiled.', numbers_at_level: true }),
  fact('reference_data_match', { headline: 'No column matched reference data.' }),
];

async function setUp() {
  const { document, window } = makeDomEnvironment();
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.includes('/analyses/value_profile/measurements')) {
      return ok({ analysis_id: 'value_profile', run_at: '2026-10-30T11:12:58', source: 'value_profile',
        measurements: FIX.container, not_applicable: false, reason: '', footer: '' });
    }
    if (u.includes('/analyses/data_class_match/measurements')) {
      return ok({ analysis_id: 'data_class_match', measurements: [{ name: 'column_count', value: 456, opens: null, note: '' }],
        not_applicable: false, reason: '', footer: '' });
    }
    if (u.startsWith(`/api/databases/${SLUG}/value-profile`)) return ok(FIX.summary);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: SLUG, stage: 'analysis', subTab: 'questions', profileTable: '',
    databasesLoaded: true, databases: [{ slug: SLUG, display_name: 'Coco ODS', disposition: 'undecided' }],
    investigations: [], investigation: '', workingSet: new Set(), me: { user_id: 'me' } });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  }
  const row = document.createElement('div');
  row.id = 'qrow-0';
  document.getElementById('content').appendChild(row);
  row.innerHTML = app.rowInner(Q0079, 0, env(FACTS));
  app.bindRowActions(row, Q0079, 0);
  return { document, window, app, row, calls };
}

test('envelope: the other analysis with numbers at the level gets a numbers line; the first and the rest do not', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const { readEnvelope } = await import('/static/next/envelope.js');
  const lines = readEnvelope(Q0079, env(FACTS), (s) => s, (s) => s);
  assert.deepEqual(lines.numbersLines, [{ id: 'value_profile', sentence: '7 of 10 columns profiled.' }]);
  const firstHasIt = readEnvelope(Q0079, env([fact('data_class_match', { numbers_at_level: true, headline: 'x.' })]), (s) => s, (s) => s);
  assert.deepEqual(firstHasIt.numbersLines, [], "the row's own button already opens the first analysis");
  const notRun = readEnvelope(Q0079, env([fact('data_class_match', { headline: 'x.' }),
    fact('value_profile', { numbers_at_level: true, state: 'never_run', is_known: false })]), (s) => s, (s) => s);
  assert.deepEqual(notRun.numbersLines, [], 'nothing to show the numbers of');
});

test("Q-0079's row keeps its own button on data_class_match and adds a value_profile line with its own", async () => {
  const { row } = await setUp();
  const own = row.querySelector('[data-numbers="0"]');
  assert.equal(own.dataset.numbersFor, 'data_class_match');
  const line = row.querySelector('[data-numbers-line="value_profile"]');
  assert.ok(line, 'value_profile has a numbers line');
  assert.match(text(line), /^value_profile · 7 of 10 columns profiled\. · the numbers behind this ›$/);
  const btn = line.querySelector('[data-numbers-step]');
  assert.equal(btn.dataset.numbersFor, 'value_profile');
  assert.equal(btn.dataset.numbersLevel, 'field');
  assert.equal(row.querySelector('[data-numbers-line="reference_data_match"]'), null);
});

test('pressing it reads the field-level rows: one per table, each opening that table, and the footer opens the tab', async () => {
  const ctx = await setUp();
  const btn = ctx.row.querySelector('[data-numbers-line="value_profile"] [data-numbers-step]');
  btn.click();
  await tick();
  assert.ok(ctx.calls.some((u) => u.includes('/analyses/value_profile/measurements') && u.includes('level=field')));
  const slot = ctx.document.getElementById('qm-0');
  assert.equal(slot.hidden, false);
  assert.equal(text(btn), 'the numbers behind this 4 ›');
  const links = [...slot.querySelectorAll('[data-numbers-tab]')];
  assert.deepEqual(links.map((a) => a.dataset.numbersTab), ['sales.empty_t', 'sales.orders', 'sales.big', 'sales.v_orders', '']);
  assert.match(text(slot), /sales\.orders 4 rows · 5 of 5 columns measured/);
  assert.match(text(slot), /sales\.v_orders not applicable a view, not a table: only tables are read/);
  assert.equal(text(links.at(-1)), 'open the value profile ›');
  assert.ok(!/sales orders|v orders/.test(text(slot)), 'a table name is shown as stored, never respaced');
  // The row's own button switches the same slot to data_class_match rather than closing it.
  ctx.row.querySelector('[data-numbers="0"]').click();
  await tick();
  assert.equal(slot.hidden, false);
  assert.equal(slot.dataset.numbersShown, 'data_class_match');
  assert.equal(slot.querySelector('[data-numbers-tab]'), null);
  // A table's link opens the Value profile tab on that table.
  btn.click();
  await tick();
  slot.querySelector('[data-numbers-tab="sales.orders"]').click();
  await tick(150);
  assert.equal(ctx.app.state.subTab, 'value_profile');
  assert.equal(ctx.app.state.profileTable, 'sales.orders');
});
