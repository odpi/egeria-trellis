/** Rehearsal walk 2026-10-12, front-end fixes (branch re/walk-fixes-ui). One test per item, exact strings.
 *  Items: 1 Contents undecided cue · 2 views_only word · 3 queue "first try" · 4 in-page confirm · 5 size not read ·
 *  6 definition row at 800 px · 7 compile panel objects · 8 stale-link banner on any resource · 9 per-member link
 *  status · 10 GITHUB_BASE_URL note · 11 evidence-moved warning once. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

ensureLoaderRegistered();
const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT = path.resolve(HERE, '../../resource_explorer/web/static/next');
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (b) => ({ ok: true, status: 200, json: async () => b });

/* ── 1 ─────────────────────────────────────────────────────────────────── */
test('item 1: an undecided count carries its own cue (◌), never the ✓ of a chosen count', async () => {
  makeDomEnvironment();
  const counts = await import('/static/next/stages/curate-counts.js');
  const tabs = await import('/static/next/stages/curate-tabs.js');
  const ctx = { kind: 'db', view: { counts: { schemas_catalogue: 1, schemas_undecided: 33 } } };
  const [chosen, undecided] = counts.tabCounts('curate_contents', ctx);
  assert.equal(chosen.cue, 'measured');
  assert.equal(undecided.cue, 'undecided_count');
  assert.equal(undecided.word, '33 undecided');
  const { document } = makeDomEnvironment();
  const box = document.createElement('div');
  box.innerHTML = tabs.tabCountsHtml('curate_contents', ctx);
  assert.equal(text(box), '✓ 1 chosen · ◌ 33 undecided');
  // a zero keeps the counted-zero cue (an empty set is a measurement, not an undecided pile)
  assert.equal(counts.tabCounts('curate_contents', { kind: 'db', view: { counts: { schemas_catalogue: 1, schemas_undecided: 0 } } })[1].cue, 'measured_zero');
});

/* ── 2 and 5 ──────────────────────────────────────────────────────────── */
test('item 2: views_only is "views only"; an unknown snake_case state is never shown raw', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const html = app.schemaTreeHtml([
    { schema: 'pr', classification: 'views_only', table_count: 25, tables: [] },
    { schema: 'zz', classification: 'some_new_state', table_count: 2, tables: [] },
  ]);
  assert.match(html, /25 view\(s\) · views only · no storage/);
  assert.doesNotMatch(html, /25 table\(s\)/);
  assert.match(html, /2 table\(s\) — some new state/);
  assert.doesNotMatch(html, /views_only|some_new_state/);
});

test('item 5: a schema row with no size read says "size not read"; a read size and the index words stay', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const row = (over) => ({ schema: 'production', classification: 'data', table_count: 28, row_total: 351658, is_estimate: true, tables: [], ...over });
  const none = app.schemaTreeHtml([row({})]);
  assert.match(none, /28 table\(s\) · 351,658 row\(s\) \(est\.\) · size not read</);
  const withSize = app.schemaTreeHtml([row({ bytes_total: 2048, index_bytes_total: null })]);
  assert.match(withSize, /\(est\.\) · 2\.0 KB · index not read</);
  assert.doesNotMatch(withSize, /size not read/);
  const withIndex = app.schemaTreeHtml([row({ bytes_total: 2048, index_bytes_total: 1024 })]);
  assert.match(withIndex, /2\.0 KB \(index 1\.0 KB\)</);
  const measured = app.schemaTreeHtml([{ schema: 'm', classification: 'measured', table_count: 3, tables: [] }]);
  assert.match(measured, /3 table\(s\) · size not read</);
});

/* ── 3 ────────────────────────────────────────────────────────────────── */
test('item 3: a done row with no retries reads "first try", never "0/8"; a retried one keeps its ratio', async () => {
  const { document } = makeDomEnvironment();
  const R = (id, status, attempts) => ({ id, entity_type: 'repo', entity_slug: 'g', element_kind: 'annotation', qualified_name: `Q${id}`, status,
    attempts, last_error: '', next_attempt_at: '', completed_at: '2026-10-08T01:30:00', created_at: '2026-10-08T01:00:00', destructive: false });
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u === '/api/egeria/admin-status') return ok({ admin: true, configured: true });
    return ok({ counts: { done: 2 }, rows: [R(1, 'done', 0), R(2, 'done', 3), R(3, 'dead', 0)], max_attempts: 8 });
  };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mod = await import(`/static/next/admin/outbox.js?t=${Math.random()}`);
  await mod.renderOutbox(host);
  assert.match(text(host.querySelector('[data-outbox-row="1"]')), /✓ done first try/);
  assert.doesNotMatch(text(host.querySelector('[data-outbox-row="1"]')), /0\/8/);
  assert.match(text(host.querySelector('[data-outbox-row="2"]')), /✓ done 3\/8/);
  assert.match(text(host.querySelector('[data-outbox-row="3"]')), /✗ dead 0\/8/, 'only a success is "first try"');
});

/* ── 4 ────────────────────────────────────────────────────────────────── */
test('item 4: Admin → Egeria Links never calls the native window.confirm; its modal names the action', async () => {
  const { document, window } = makeDomEnvironment();
  window.confirm = () => { throw new Error('native confirm used'); };
  globalThis.confirm = window.confirm;
  const asked = [];
  autoAnswerConfirm(document, { answer: true, asked });
  const rows = [{ entity_type: 'database', entity_slug: 'd1', stale_guid: 'g', detected_at: '', detail: '' }];
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u === '/api/egeria/admin-status') return ok({ admin: true, configured: true });
    if (u === '/api/egeria/linkage/stale') return ok(rows);
    if (u.endsWith('/resolve')) return ok({ status: 'ok', next_step: 'cleared' });
    throw new Error(`unstubbed ${u}`);
  };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mod = await import(`/static/next/admin/egeria_links.js?t=${Math.random()}`);
  await mod.renderEgeriaLinks(host);
  host.querySelector('[data-link-resolve="discard"]').click();
  await tick();
  assert.equal(asked.length, 1);
  assert.match(asked[0], /^Discard link database\/d1\?/);
  const src = fs.readFileSync(path.join(NEXT, 'admin/egeria_links.js'), 'utf8');
  assert.doesNotMatch(src, /window\.confirm/);
});

/* ── 6 ────────────────────────────────────────────────────────────────── */
test('item 6: the definition row text column has a real basis, so it wraps below 24rem instead of being squeezed', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const box = document.createElement('div');
  box.innerHTML = app.surveyRowHtml({ display_name: 'Survey & analyses', qualified_name: 'GovActionProcess::X',
    description: 'A long description that must not wrap one word per line.', steps: [1, 2, 3, 4, 5, 6] });
  const row = box.querySelector('[data-def-row]');
  assert.ok(row.classList.contains('flex-wrap'), 'the row wraps its columns');
  const col = box.querySelector('[data-def-text]');
  assert.equal(col.getAttribute('style'), 'flex:1 1 24rem', 'basis 24rem (not 0): below that the column takes its own line');
  assert.ok(col.classList.contains('min-w-0'));
  assert.match(text(box), /6 steps/);
  // the steps / last run / button columns do not shrink into the text
  assert.equal([...row.children].filter((c) => c.classList.contains('shrink-0')).length >= 2, true);
});

/* ── 7 ───────────────────────────────────────────────────────────────── */
test('item 7: compile manifest objects read as a short summary with the detail on demand, never [object Object]', async () => {
  const { document } = makeDomEnvironment();
  await loadAppModule();
  const chat = await import('/static/next/chat.js');
  const lists = { dependencies: { by_ecosystem: { total: 70, shown: { FULL: 70 } } }, files: { total: 5 } };
  assert.equal(chat.manifestValueSummary(lists), '2 entries: dependencies, files');
  assert.equal(chat.manifestValueSummary({}), '0 entries');
  assert.equal(chat.manifestValueSummary({ a: 1 }), '1 entry: a');
  assert.equal(chat.manifestValueSummary({ a: 1, b: 2, c: 3, d: 4 }), '4 entries: a, b, c, …');
  assert.equal(chat.manifestValueSummary([1, 2]), '2 item(s)');
  assert.equal(chat.manifestValueSummary(8000), '8000');
  const box = document.createElement('div');
  box.innerHTML = chat.manifestRowHtml('lists', lists) + chat.manifestRowHtml('coverage', { stored: 13, missing: 0 });
  assert.doesNotMatch(box.innerHTML, /\[object Object\]/);
  assert.ok(text(box.querySelector('[data-manifest-row="lists"]')).startsWith('lists · 2 entries: dependencies, files details'));
  const detail = box.querySelector('[data-manifest-row="coverage"] [data-manifest-detail] pre');
  assert.deepEqual(JSON.parse(detail.textContent), { stored: 13, missing: 0 });
});

/* ── 8 ───────────────────────────────────────────────────────────────── */
async function headerSetUp({ stale, resolveFails = false, answer = true }) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  let rows = stale;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    if (u === '/api/egeria/linkage/stale') return ok(rows);
    if (u.endsWith('/resolve')) {
      if (resolveFails) return { ok: false, status: 502, statusText: 'bad', json: async () => ({ detail: 'publish failed' }) };
      rows = [];
      return ok({ status: 'ok', next_step: 'Link cleared.' });
    }
    return ok({});
  };
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const asked = [];
  autoAnswerConfirm(document, { answer, asked });
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.databases = [{ slug: 'd1', display_name: 'D1', disposition: 'undecided', is_published: true,
    egeria_publish_note: 'published to Egeria · link stale since 2026-10-12 — element not found' }];
  app.state.selectedSlug = 'd1';
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  app.state.staleLink = null;
  const host = document.createElement('div');
  host.id = 'resource-header';
  document.body.appendChild(host);
  const render = () => { host.innerHTML = app.resourceHeaderHtml('d1'); app.bindResourceHeader(); };
  return { document, app, calls, asked, host, render };
}

test('item 8: a stale database shows the banner with Admin\'s three choices and calls the same route with the same words', async () => {
  const { app, calls, asked, host, render } = await headerSetUp({ stale: [] });
  render();
  assert.equal(host.querySelector('[data-stale-link-banner]'), null, 'not on the stale list: no banner');
  app.state.staleLink = { slug: 'd1', row: { entity_type: 'database', entity_slug: 'd1', stale_guid: 'dead-guid' } };
  render();
  const banner = host.querySelector('[data-stale-link-banner]');
  assert.ok(banner);
  assert.deepEqual([...banner.querySelectorAll('[data-link-repair]')].map((b) => text(b)), ['Republish', 'Re-survey', 'Discard link']);
  assert.match(text(banner), /dead-guid/);
  const links = await import('/static/next/admin/egeria_links.js');
  assert.deepEqual([...banner.querySelectorAll('[data-link-repair]')].map((b) => b.getAttribute('title')), links.ACTIONS.map((a) => a.does));
  banner.querySelector('[data-link-repair="discard"]').click();
  await tick();
  assert.match(asked[0], /^Discard link d1\?\n\nRemove RE's local publish records for this resource and stop\. Writes nothing to Egeria\./);
  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/egeria/linkage/database/d1/resolve');
  assert.deepEqual(post.body, { action: 'discard' });
  assert.equal(host.querySelector('[data-stale-link-banner]'), null, 'after the stale list is re-read, the banner leaves');
});

test('item 8: a failed repair pops up the reason; declining sends nothing', async () => {
  const f = await headerSetUp({ stale: [{ entity_type: 'database', entity_slug: 'd1', stale_guid: 'g' }], resolveFails: true });
  f.app.state.staleLink = { slug: 'd1', row: { entity_type: 'database', entity_slug: 'd1', stale_guid: 'g' } };
  f.render();
  f.host.querySelector('[data-link-repair="republish"]').click();
  await tick(80);
  const pop = f.document.querySelector('[data-notice-dialog]');
  assert.ok(pop, 'a pop-up');
  assert.match(text(pop), /The repair did not finish.*publish failed/);
  const d = await headerSetUp({ stale: [], answer: false });
  d.app.state.staleLink = { slug: 'd1', row: { entity_type: 'database', entity_slug: 'd1', stale_guid: 'g' } };
  d.render();
  d.host.querySelector('[data-link-repair="resurvey"]').click();
  await tick();
  assert.equal(d.calls.filter((c) => c.method === 'POST').length, 0);
});

/* ── 9 ──────────────────────────────────────────────────────────────── */
test('item 9: each member row shows linked / not linked / could not tell from link-status, reason on hover; unread is "not read"', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const inv = await import('/static/next/stages/investigation.js');
  const bound = { slug: 'c', egeria_project_guid: 'g' };
  const rows = [
    { entity_type: 'repo', entity_slug: 'a', state: 'linked', reason: '' },
    { entity_type: 'repo', entity_slug: 'b', state: 'not_linked', reason: 'not in Egeria yet — no asset GUID' },
    { entity_type: 'database', entity_slug: 'c', state: 'could_not_tell', reason: 'no working set yet' },
  ];
  const cell = (m, r = rows) => {
    const d = document.createElement('div');
    d.innerHTML = inv.memberLinkCellHtml(bound, m, r);
    return d;
  };
  let c = cell({ entity_type: 'repo', entity_slug: 'a' });
  assert.equal(text(c), '✓ linked');
  assert.ok(c.querySelector('[data-inv-member-link="linked"]'));
  c = cell({ entity_type: 'repo', entity_slug: 'b' });
  assert.equal(text(c), '○ not linked');
  assert.equal(c.querySelector('[data-cue]').getAttribute('title'), 'not in Egeria yet — no asset GUID');
  c = cell({ entity_type: 'database', entity_slug: 'c' });
  assert.equal(text(c), '? could not tell');
  assert.equal(c.querySelector('[data-cue]').getAttribute('title'), 'no working set yet');
  assert.equal(text(cell({ entity_type: 'repo', entity_slug: 'zz' })), '? not read', 'a member the response omitted is not read, not "not linked"');
  assert.equal(text(cell({ entity_type: 'repo', entity_slug: 'a' }, null)), '? not read');
});

test('item 9: the investigation detail table has the column, filled from the link-status response', async () => {
  const { document, window } = makeDomEnvironment();
  const INDEX_HTML = fs.readFileSync(path.join(NEXT, 'index.html'), 'utf8');
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (/\/link-status$/.test(u)) return ok({ bound: true, counts: { linked: 1, not_linked: 1, could_not_tell: 0, linkable: 1 }, members: [
      { entity_type: 'repo', entity_slug: 'a', state: 'linked', reason: '' },
      { entity_type: 'repo', entity_slug: 'b', state: 'not_linked', reason: 'not attached yet — use Link members' }] });
    if (/\/members$/.test(u)) return ok([{ entity_type: 'repo', entity_slug: 'a' }, { entity_type: 'repo', entity_slug: 'b' }]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    const m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m) return ok({ slug: m[1], display_name: 'C', status: 'open', description: 'd', project_classification: 'StudyProject', purposes: [], visibility: 'public', egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::C' });
    return ok([]);
  };
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const api = await import('/static/re-api.js'); api.clearCache();
  const app = await import('/static/next/app.js');
  const inv = await import('/static/next/stages/investigation.js');
  app.state.stage = 'investigation'; app.state.me = { user_id: 'me' }; app.state.investigations = [];
  inv.openInvestigationDetail('c360');
  await inv.renderInvestigation();
  await tick(80);
  const cells = [...document.querySelectorAll('[data-inv-member-link-cell]')].map((c) => text(c));
  assert.deepEqual(cells, ['✓ linked', '○ not linked']);
  assert.ok([...document.querySelectorAll('th')].some((th) => text(th) === 'In the working set'));
});

/* ── 10 ─────────────────────────────────────────────────────────────── */
test('item 10: Repository discovery sources says in one line that GITHUB_BASE_URL in .env sets the base URL', async () => {
  const { document } = makeDomEnvironment();
  globalThis.fetch = async () => ok([]);
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mod = await import(`/static/next/admin/discovery_sources.js?t=${Math.random()}`);
  await mod.renderDiscoverySources(host);
  const note = host.querySelector('[data-github-env-note]');
  assert.ok(note);
  assert.equal(text(note), "GitHub's base URL comes from GITHUB_BASE_URL in .env. It is not set here.");
  assert.equal(host.querySelector('[data-github-env-note] input, [data-github-env-note] button'), null, 'no control');
});

/* ── 11 ─────────────────────────────────────────────────────────────── */
test('item 11: the evidence-moved warning is said once per resource; each row carries a small cue only', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const enr = await import('/static/next/stages/enrichment.js');
  const old = '2020-01-01T00:00:00Z';
  const now = new Date().toISOString();
  const ev = { cve_scan: old, security_scan: old };
  const j = (v) => ({ value: v, kind: 'judgement', author: 'dan', set_at: now, source: '', interim: false, evidence: ev });
  app.state.enrichment = { sensitivity: j('internal'), criticality: j('high') };
  app.state.enrichmentFacts = { cve_scan: { last_run_at: now }, security_scan: { last_run_at: now } };
  const rows = enr.JUDGEMENTS.map((d) => enr.fieldRowHtml(d, 'judgement')).join('');
  assert.equal((rows.match(/evidence moved:/g) || []).length, 2, 'only the two cues\' hover titles name it');
  assert.doesNotMatch(rows.replace(/title="[^"]*"/g, ''), /evidence moved/);
  assert.equal((rows.match(/data-moved-jump/g) || []).length, 2, 'one small cue per moved judgement');
  const summary = enr.movedSummaryHtml();
  assert.equal((summary.match(/evidence moved:/g) || []).length, 1);
  const d = document.createElement('div');
  d.innerHTML = summary;
  assert.equal(text(d), '⚠ review — evidence moved: cve_scan, security_scan · 2 judgements made before they last ran');
  app.state.enrichment = {};
  assert.equal(enr.movedSummaryHtml(), '');
});
