/** Behaviour: Brief D 7f.8 -- the run's publish word.
 *
 *  The adaptive step records `publish_state` in its detail ("published to
 *  Egeria" / "publish failed · <why>" / "local only · publish not chosen").
 *  `answered_by` says only which scan answered, so a failure and a publish
 *  nobody chose both read "local scan". Each step row now also carries a cue
 *  (glyph) plus a short word, with the full sentence on demand.
 *
 *  Real app.js; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const dlgOf = (document) => document.getElementById('wl-detail');

async function setUp(steps, extra = {}) {
  const { document, window } = makeDomEnvironment();
  const detail = JSON.stringify({ steps, ...extra });
  const runs = [{ id: 'r1', operation: 'survey', ts: new Date().toISOString(), status: 'ok', summary: 'Done', detail }];
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity/?entity_slug=')) return ok(runs);
    return ok(u.includes('/groups') ? [] : {});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  return { document, app };
}

const stepRow = (document, name) =>
  [...dlgOf(document).querySelectorAll('li')].find((li) => li.textContent.includes(name));

test('a published step, a failed publish and a publish nobody chose each read differently', async () => {
  const { document, app } = await setUp([
    { step: 'S::pub', status: 'ok', answered_by: 'egeria-custom',
      detail: { source: 'egeria-custom', publish_state: 'published to Egeria' } },
    { step: 'S::bad', status: 'ok', answered_by: 'custom',
      detail: { source: 'custom', publish_state: 'publish failed · Egeria said no' } },
    { step: 'S::none', status: 'ok', answered_by: 'custom',
      detail: { source: 'custom', publish_state: 'local only · publish not chosen' } },
  ]);
  await app.openRunsList('coco');

  const pub = stepRow(document, 'pub').querySelector('[data-step-publish]');
  const bad = stepRow(document, 'bad').querySelector('[data-step-publish]');
  const none = stepRow(document, 'none').querySelector('[data-step-publish]');
  assert.equal(pub.dataset.stepPublish, 'measured');
  assert.equal(bad.dataset.stepPublish, 'error');
  assert.equal(none.dataset.stepPublish, 'unclassified');
  assert.match(text(pub), /^✓ published$/);
  assert.match(text(bad), /^✕ publish failed$/);
  assert.match(text(none), /^· not published$/);
  // the sentence is on demand, not on the row
  assert.equal(bad.getAttribute('title'), 'publish failed · Egeria said no');
  assert.doesNotMatch(text(stepRow(document, 'bad')), /Egeria said no/);
  // a failure and a not-chosen no longer look alike
  assert.notEqual(text(bad), text(none));
  // the answer source is still there beside it
  assert.match(text(stepRow(document, 'bad')), /answered by local scan/);
});

test('a step with no publish_state draws no publish word', async () => {
  const { document, app } = await setUp([
    { step: 'S::plain', status: 'ok', answered_by: 'local' },
    { step: 'S::str', status: 'ok', answered_by: 'local', detail: 'a plain string detail' },
  ]);
  await app.openRunsList('coco');
  assert.equal(dlgOf(document).querySelectorAll('[data-step-publish]').length, 0);
});

test('a failed publish on a step that records no answered_by still shows the word', async () => {
  const { document, app } = await setUp([
    { step: 'S::only', status: 'ok', detail: { publish_state: 'publish failed · boom' } },
  ]);
  await app.openRunsList('coco');
  assert.match(text(stepRow(document, 'only')), /✕ publish failed/);
});

// ── Brief D: index size beside the table size ───────────────────────────────

test('a table line shows its index size, and "index not read" for a NULL, never 0', async () => {
  const { setUp: _unused } = {};
  const { makeDomEnvironment: mk, ensureLoaderRegistered: ensure } = await import('./dom-harness.mjs');
  const { window } = mk();
  ensure();
  globalThis.location = window.location;
  const app = await import('/static/next/app.js');
  const read = app.tableHtml({ name: 'orders', table_type: 'BASE TABLE', row_count: 5,
    size_bytes: 8192, index_bytes: 4096, column_count: 1, columns: [] });
  const unread = app.tableHtml({ name: 'items', table_type: 'BASE TABLE', row_count: 5,
    size_bytes: 8192, index_bytes: null, column_count: 1, columns: [] });
  const oldPayload = app.tableHtml({ name: 'old', table_type: 'BASE TABLE', row_count: 5,
    size_bytes: 8192, column_count: 1, columns: [] });
  const flat = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  assert.match(flat(read), /index 4(\.0)? ?KB|index 4,096|index 4/i);
  assert.match(flat(unread), /index not read/);
  assert.doesNotMatch(flat(unread), /index 0/);
  assert.doesNotMatch(flat(oldPayload), /index/);
});

/* Backlog 7f.8, the database run: its steps record no publish word (the run
 * publishes once, after them), so the word is the run's own `publish_state`,
 * drawn beside the step count. The Database Scouting Scan's steps say
 * "answered by Prefect"; before this nothing on the run said the publish failed. */
const scoutingSteps = ['postgres_schema_and_stats', 'postgres_operations', 'credential_capability']
  .map((k) => ({ step: `GovActionProcessStep::DatabaseScoutingSurvey::${k}`, status: 'ok', answered_by: 'prefect' }));
const runWord = (document) => dlgOf(document).querySelector('[data-run-publish] [data-step-publish]');

test('a database run draws its own publish word: failed, not chosen, nothing to publish, published', async () => {
  const cases = [
    [{ publish_state: 'publish failed · EGERIA_PLATFORM_URL is not set', published: false }, 'error', /^✕ publish failed$/],
    [{ publish_state: 'local only · publish not chosen', published: false }, 'unclassified', /^· not published$/],
    [{ publish_state: 'nothing to publish · no step produced results', published: false }, 'nothing', /^∅ nothing to publish$/],
    [{ publish_state: 'published to Egeria', published: true, egeria_report_guid: 'r-1' }, 'measured', /^✓ published · creator not read$/],
  ];
  for (const [extra, kind, shown] of cases) {
    const { document, app } = await setUp(scoutingSteps, extra);
    await app.openRunsList('advisor');
    const w = runWord(document);
    assert.ok(w, `no run publish word for ${extra.publish_state}`);
    assert.equal(w.dataset.stepPublish, kind);
    assert.match(text(w), shown);
    assert.equal(w.getAttribute('title'), extra.publish_state);
    // the run word is the run's, not repeated on each step
    assert.equal(dlgOf(document).querySelectorAll('[data-step-publish]').length, 1);
  }
});

test('nothing to publish and publish not chosen no longer look alike', async () => {
  const a = await setUp(scoutingSteps, { publish_state: 'nothing to publish · no step produced results' });
  await a.app.openRunsList('advisor');
  const nothing = text(runWord(a.document));
  const b = await setUp(scoutingSteps, { publish_state: 'local only · publish not chosen' });
  await b.app.openRunsList('advisor');
  assert.notEqual(nothing, text(runWord(b.document)));
});

test('"published" without a report guid is not drawn as published', async () => {
  const { document, app } = await setUp(scoutingSteps, { publish_state: 'published to Egeria', published: true });
  await app.openRunsList('advisor');
  const w = runWord(document);
  assert.notEqual(w.dataset.stepPublish, 'measured');
  assert.doesNotMatch(text(w), /✓/);
  assert.match(text(w), /not established/);
});

test('a run with no step list still shows its publish word', async () => {
  const { document, app } = await setUp([], { publish_state: 'publish failed · boom' });
  await app.openRunsList('advisor');
  const w = runWord(document);
  assert.ok(w, 'the run word was hidden with the step list');
  assert.match(text(w), /✕ publish failed/);
});

test('a run that recorded no publish_state draws no run word', async () => {
  const { document, app } = await setUp(scoutingSteps, { published: false, errors: ['Failed to publish'] });
  await app.openRunsList('advisor');
  assert.equal(dlgOf(document).querySelectorAll('[data-run-publish]').length, 0);
});

/* A queued run publishes AS the person who pressed it (2026-10-21). The card says who Egeria states created the report
 * (`published_as`, read back by GUID), and a run whose presser's sign-in was gone says so in its own words. */
test('a published run names who Egeria says created the report; with no creator read back, it says so', async () => {
  const withWho = await setUp(scoutingSteps, { publish_state: 'published to Egeria', published: true,
    egeria_report_guid: 'r-1', published_as: 'peterprofile' });
  await withWho.app.openRunsList('advisor');
  assert.match(text(runWord(withWho.document)), /^✓ published as peterprofile$/);

  const without = await setUp(scoutingSteps, { publish_state: 'published to Egeria', published: true,
    egeria_report_guid: 'r-1' });
  await without.app.openRunsList('advisor');
  assert.match(text(runWord(without.document)), /^✓ published · creator not read$/);
});

test('an expired presser sign-in is drawn as a warning with its words, never as published', async () => {
  const words = 'published: no · your sign-in expired · press Publish on the card';
  const { document, app } = await setUp(scoutingSteps, { publish_state: words, published: false });
  await app.openRunsList('advisor');
  const w = runWord(document);
  assert.equal(w.dataset.stepPublish, 'expired');
  assert.equal(text(w).replace(/^\S+ /, ''), words);
  assert.doesNotMatch(text(w), /^✓/);
  assert.doesNotMatch(text(w), /not established/);
});

test('a sign-in that expired mid-publish shows its words and the partial-write note', async () => {
  const words = 'published: no · your sign-in expired · press Publish on the card';
  const note = 'The sign-in expired while publishing: some writes may already have been made as the person who pressed; press Publish on the card to finish.';
  const { document, app } = await setUp(scoutingSteps, { publish_state: words, published: false, publish_note: note });
  await app.openRunsList('advisor');
  const w = runWord(document);
  assert.match(text(w), /your sign-in expired/);
  const n = dlgOf(document).querySelector('[data-run-publish-note]');
  assert.ok(n && text(n).includes('some writes may already have been made'), 'the partial-write note is missing');
});

/* Ruling 5 (architect, 2026-10-24): when counts moved on more than the configured share of the tables a publish
 * measured, the run's detail carries `written_live` (from the proof rows) and the card adds a visible link to the
 * database's Analysis stage, where Change Rates (db_change_rates, Q-0086) is. Drawn from the flag, never the sentence. */
test('a written-live run links to change rates; a run without the flag draws no link', async () => {
  const published = { publish_state: 'published to Egeria · 5 measures published', published: true,
    egeria_report_guid: 'r-1', published_as: 'peterprofile' };
  const live = await setUp(scoutingSteps, { ...published,
    written_live: { analysis_id: 'db_change_rates', question_id: 'Q-0086' } });
  await live.app.openRunsList('coco_pharma');
  const link = dlgOf(live.document).querySelector('[data-run-publish] [data-run-written-live]');
  assert.ok(link, 'no written-live link');
  assert.equal(text(link), 'this database is written live · see change rates');
  assert.equal(link.getAttribute('href'), '?type=db&resource=coco_pharma&stage=analysis');
  assert.match(link.getAttribute('title'), /db_change_rates \(Q-0086\)/);
  assert.match(text(runWord(live.document)), /^✓ published as peterprofile$/);       // the word is unchanged

  // the sentence alone (no flag) never draws the link
  const plain = await setUp(scoutingSteps, { ...published,
    publish_state: 'published to Egeria · this database is written live · see change rates' });
  await plain.app.openRunsList('coco_pharma');
  assert.equal(dlgOf(plain.document).querySelectorAll('[data-run-written-live]').length, 0);
});
