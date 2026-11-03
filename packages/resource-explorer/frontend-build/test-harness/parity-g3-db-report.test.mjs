/** Behaviour (parity slice G3, PI-030/031/032/036/037/040/041/049): the database report's tabular
 *  side on Understanding, the schema inventory and Curate, and the repository's changes banner.
 *
 *  Real app.js and the real router; only fetch (a recording stub) and Plotly (a stub) are
 *  replaced. READ ONLY: every test asserts that nothing but GET (and the diagram render POST) went
 *  out. No Egeria, no registry, no real database.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { openAllUnderstandingTabs } from './understanding-tabs-kit.mjs';

const SLUG = 'coco_pharma';
const wait = (ms = 400) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
/** A grid row as its cells, one space between cells (jsdom does no layout, so divs run together). */
const cells = (row) => [...row.querySelectorAll('[role="cell"]')].map(text).join(' ');

const RUN = { run_id: 3, surveyed_at: '2026-10-06T14:02:00', source: 'local', surveyed_as: 'survey_user' };
const OLD = { run_id: 2, surveyed_at: '2026-10-03T09:00:00', source: 'local', surveyed_as: 'survey_user' };
const env = (extra = {}) => ({ database: SLUG, run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...extra });
const SIZES = env({
  measure: 'rows', tables: ['sales.orders', 'sales.items'], row_counts: [500, 20], sizes_mb: [2, 1],
  ranked_count: 2, truncated_by_limit: 0, table_count_total: 2, views_excluded: 0, not_established_count: 0,
  not_established_tables: [], provenance: 'rows',
  ranked: [
    { name: 'sales.orders', row_count: 500, size_bytes: 2097152, last_analyzed: '2026-10-02T01:00:00', pending_changes: 42, activity_state: 'measured' },
    { name: 'sales.items', row_count: 20, size_bytes: 1048576, last_analyzed: null, pending_changes: null, activity_state: 'not_collected' },
  ],
  figure: { data: [{ type: 'bar', orientation: 'h', y: ['sales.orders', 'sales.items'], x: [500, 20] }], layout: {} },
});
const OTHER = env({ figure: { data: [{ type: 'bar', x: ['a'], y: [1] }], layout: {} } });
const HISTORY_ROWS = [
  { surveyed_at: '2026-10-06T14:02:00', schema_count: 0, table_count: 0, column_count: 0, source: 'egeria-published',
    invalid_at: '2026-10-06T15:30:00.5', invalid_reason: 'false-zero: publish row with empty schema_info' },
  { surveyed_at: '2026-10-03T09:00:00', schema_count: 29, table_count: 412, column_count: 3140, source: 'local' },
];
const DIFF = { prev_date: '2026-10-03T09:00:00', curr_date: '2026-10-06T14:02:00', deltas: { schemas: 0, tables: 1, columns: 3 },
  new_tables: ['sales.items'], removed_tables: [], table_diff_state: 'measured' };
const VIEW = { schema: 'sales', name: 'v_orders', definition: 'SELECT o.total FROM sales.orders o', dependencies: ['sales.orders'],
  complexity: { complexity_score: 12, portability_rating: 100, node_count: 9, max_depth: 3, join_count: 1, cte_count: 0 },
  lineage: { total: { name: 'total', source: 'sales.orders', downstream: [] } } };

function makeStub(over = {}) {
  const calls = [];
  const r = { surveys: HISTORY_ROWS, views: { state: 'measured', run: RUN, reason: '', views: [VIEW] }, diff: DIFF,
    rules: [], repoDiff: {}, mermaid: '<svg data-fake-svg></svg>', ...over };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ method: opts.method || 'GET', url: u, body: opts.body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b, text: async () => (typeof b === 'string' ? b : JSON.stringify(b)) });
    if (u.includes('/api/diagrams/mermaid')) return { ok: true, status: 200, text: async () => r.mermaid, json: async () => ({}) };
    if (/\/api\/databases\/[^/]+\/surveys/.test(u)) return ok(r.surveys);
    if (/\/api\/databases\/[^/]+\/views/.test(u)) return ok(r.views);
    if (/\/api\/databases\/[^/]+\/diff/.test(u)) return ok(r.diff);
    if (/\/api\/egeria\/rules\/dataclasses/.test(u)) return r.rules.__http ? { ok: false, status: r.rules.__http, statusText: 'x', json: async () => ({ detail: 'x' }) } : ok(r.rules);
    if (/\/api\/egeria\/[^/]+\/diff/.test(u)) return ok(r.repoDiff);
    const m = u.match(/\/api\/stats\/databases\/[^/]+\/([a-z_]+)/);
    if (m) return ok(m[1] === 'table_sizes' ? SIZES : OTHER);
    if (/\/api\/stats\/[^/]+\/charts\//.test(u)) return ok({ data: [] });
    if (u.includes('/schema-inventory-tree')) return ok(r.tree);
    return ok(r.__default ?? {});
  };
  return { calls, r };
}

async function setUp({ resourceType = 'db', stage = 'understanding', over = {}, subTab = 'questions', summary = {} } = {}) {
  const { document, window } = makeDomEnvironment();
  const stub = makeStub(over);
  window.Plotly = { async newPlot(el) { el.drawn = true; }, purge() {}, async toImage() { return 'data:image/png;base64,AA'; } };
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => { const x = append(el); if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0); return x; };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = resourceType; s.selectedSlug = SLUG;
  const row = { slug: SLUG, display_name: SLUG, group_slug: '', last_surveyed_at: '2026-10-03T09:00:00', ...summary };
  s.databases = resourceType === 'db' ? [row] : []; s.projects = resourceType === 'repo' ? [row] : [];
  s.databasesLoaded = true; s.filesystemsLoaded = true; s.filesystems = [];
  s.groups = []; s.investigations = []; s.investigation = ''; s.workListSlug = null; s.workListIndex = false;
  s.perspectives = []; s.allPerspectives = []; s.workingSet = new Set(); s.me = { user_id: 'me' };
  s.stage = stage; s.subTab = subTab;
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid', 'question-rows']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector(`#intent-nav button[data-stage="${stage}"]`).click();
  await wait();
  await openAllUnderstandingTabs(document, 50);
  // A database's data-class rules (Curate, Structure tab) are read when that tab is shown (Brief S2).
  const structureTab = stage === 'curate' ? document.querySelector('[data-subtab="curate_structure"]') : null;
  if (structureTab) { structureTab.click(); await wait(50); }
  return { document, window, app, ...stub };
}

const writes = (calls) => calls.filter((c) => c.method !== 'GET' && !c.url.includes('/api/diagrams/mermaid'));

/* ── PI-037 ──────────────────────────────────────────────────────────────── */

test('PI-037: the history table reads include_invalid and shows the valid survey; the invalid one is counted, not shown', async () => {
  const { document, calls } = await setUp();
  assert.ok(calls.some((c) => c.url.includes(`/api/databases/${SLUG}/surveys?include_invalid=true`)));
  const sec = document.querySelector('[data-survey-history]');
  assert.ok(sec, 'a survey history section sits on Understanding');
  const rows = [...sec.querySelectorAll('[data-survey-row]')];
  assert.equal(rows.length, 1);
  assert.match(cells(rows[0]), /^2026-10-03 09:00 29 412 3,140 local survey$/);
  assert.match(text(sec.querySelector('[data-invalid-hidden]')), /1 invalid survey not shown/);
  assert.equal(sec.querySelector('[data-invalid="1"]'), null);
  assert.deepEqual(writes(calls), [], 'nothing but reads went out');
});

test('PI-037: show invalid lowers the invalid row and gives its reason and when it was marked', async () => {
  const { document } = await setUp();
  const box = document.querySelector('[data-show-invalid]');
  box.checked = true; box.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await wait(50);
  const bad = document.querySelector('[data-survey-row][data-invalid="1"]');
  assert.ok(bad, 'the invalid row is drawn once the toggle is on');
  assert.match(bad.className, /opacity-60/, 'lowered, not hidden');
  assert.match(text(bad), /invalid · false-zero: publish row with empty schema_info/);
  assert.match(text(bad.querySelector('[data-invalid-at]')), /^2026-10-06 15:30$/);
  assert.match(text(bad), /Egeria/);
  assert.equal(document.querySelectorAll('[data-survey-row]').length, 2);
});

test('PI-037: the toggle is remembered in this browser and a throwing storage still draws the table', async () => {
  const first = await setUp();
  const box = first.document.querySelector('[data-show-invalid]');
  box.checked = true; box.dispatchEvent(new first.document.defaultView.Event('change', { bubbles: true }));
  assert.equal(first.window.localStorage.getItem('re-next.showInvalidSurveys'), '1');
  // a fresh draw starts with the remembered state
  first.document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await wait();
  await openAllUnderstandingTabs(first.document, 50);
  assert.equal(first.document.querySelector('[data-show-invalid]').checked, true);
  assert.equal(first.document.querySelectorAll('[data-survey-row]').length, 2);

  // storage that throws: reads say off, writes say not stored, nothing throws
  const mod = await import('/static/next/stages/db-report.js');
  assert.equal(mod.readShowInvalid(undefined), false);
  assert.equal(mod.writeShowInvalid({ setItem() { throw new Error('denied'); } }, true), false);
  assert.equal(mod.readShowInvalid({ getItem() { throw new Error('denied'); } }), false);
});

test('PI-037: with nothing invalid the toggle says "none" and is disabled; an unreadable list says so', async () => {
  const { document } = await setUp({ over: { surveys: [HISTORY_ROWS[1]] } });
  const t = document.querySelector('[data-show-invalid]');
  assert.equal(t.disabled, true);
  assert.match(text(document.querySelector('[data-survey-history]')), /show invalid \(none\)/);
  const bad = await setUp({ over: { surveys: { not: 'a list' } } });
  assert.match(text(bad.document.querySelector('[data-survey-history]')), /could not be read: the server did not send a list/);
});

/* ── PI-030 ──────────────────────────────────────────────────────────────── */

test('PI-030: the header says surveyed N ago and which source the latest valid survey came from', async () => {
  for (const [src, word] of [['local', 'local survey'], ['egeria-published', 'Egeria'], ['egeria', 'Egeria'], ['egeria-custom', 'hybrid'], ['hybrid', 'hybrid']]) {
    const { document } = await setUp({ summary: { last_survey_source: src } });
    const badge = document.querySelector('[data-survey-source]');
    assert.ok(badge, `badge for ${src}`);
    assert.equal(text(badge), word);
    assert.match(text(badge.parentElement), /^surveyed .* · /);
  }
  const none = await setUp({ summary: { last_survey_source: '' } });
  assert.equal(none.document.querySelector('[data-survey-source]'), null, 'no source recorded: no badge, no invented word');
});

/* ── PI-036 ──────────────────────────────────────────────────────────────── */

test('PI-036: the ranked table gives rows, size, last analyzed, pending changes, and the diff word', async () => {
  const { document } = await setUp();
  const t = document.querySelector('[data-ranked-tables]');
  assert.ok(t);
  const orders = t.querySelector('[data-ranked-row="sales.orders"]');
  const items = t.querySelector('[data-ranked-row="sales.items"]');
  assert.match(cells(orders), /^sales\.orders 500 2\.0 MB 2026-10-02 01:00 42 in both runs$/);
  assert.match(cells(items), /^sales\.items 20 1\.0 MB not collected not collected new$/);
});

test('PI-036: with no diff the column says "no diff yet", never "in both runs"', async () => {
  const { document } = await setUp({ over: { diff: {} } });
  assert.match(text(document.querySelector('[data-ranked-row="sales.orders"]')), /no diff yet$/);
  assert.doesNotMatch(text(document.querySelector('[data-ranked-tables]')), /in both runs/);
});

/* ── PI-040 ──────────────────────────────────────────────────────────────── */

test('PI-040: Views lists the view, its complexity and dependencies, opens its SQL, and draws a flowchart on open', async () => {
  const { document, calls } = await setUp();
  const row = document.querySelector('[data-view="sales.v_orders"]');
  assert.ok(row);
  assert.match(cells(row.querySelector('[role="row"]')), /^sales\.v_orders 12 of 100 100% 1 \/ 0 sales\.orders$/);
  assert.match(text(row.querySelector('[data-view-deps]')), /sales\.orders/);
  assert.equal(calls.filter((c) => c.url.includes('/api/diagrams/mermaid')).length, 0, 'nothing is drawn until asked');
  const sql = row.querySelector('[data-view-sql]');
  assert.match(text(sql), /SELECT o\.total FROM sales\.orders o/);
  const flow = row.querySelector('[data-view-flow]');
  flow.open = true; flow.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait(100);
  const posts = calls.filter((c) => c.url.includes('/api/diagrams/mermaid'));
  assert.equal(posts.length, 1);
  const src = JSON.parse(posts[0].body).source;
  assert.match(src, /flowchart LR/);
  assert.match(src, /sales\.orders\.total/);
  assert.match(src, /depends on/);
  assert.doesNotMatch(src, /lineage/i, 'the diagram never says the other word');
  assert.ok(flow.querySelector('[data-fake-svg]'), 'the answer is drawn where it was asked');
  // opening again does not ask again
  flow.open = false; flow.dispatchEvent(new document.defaultView.Event('toggle'));
  flow.open = true; flow.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait(50);
  assert.equal(calls.filter((c) => c.url.includes('/api/diagrams/mermaid')).length, 1);
});

test('PI-040: no word "lineage" anywhere on screen', async () => {
  const { document } = await setUp();
  assert.ok(document.querySelector('[data-section-views] [data-view]'), 'the Views section is there to be checked');
  assert.doesNotMatch(document.getElementById('content').textContent, /lineage/i);
  assert.doesNotMatch(document.getElementById('content').innerHTML, /lineage/i);
});

test('PI-040: a survey with no views key says "not measured" with the reason, never "no views"', async () => {
  const { document } = await setUp({ over: { views: { state: 'not_measured', run: RUN, views: [], reason: 'this survey did not run the sql_analysis step, so views were not analyzed' } } });
  const s = document.querySelector('[data-views-state="not_measured"]');
  assert.match(text(s), /^○ not measured — this survey did not run the sql_analysis step/);
  assert.doesNotMatch(text(document.querySelector('[data-section-views]')), /found none/);
  const none = await setUp({ over: { views: { state: 'measured', run: RUN, views: [], reason: '' } } });
  assert.match(text(none.document.querySelector('[data-views-state="measured"]')), /looked for views and found none/);
  const never = await setUp({ over: { views: { state: 'never_surveyed', run: null, views: [], reason: 'x' } } });
  assert.match(text(never.document.querySelector('[data-views-state="never_surveyed"]')), /not run: needs a database survey/);
});

test('PI-040: a flowchart that cannot be drawn says why, in its own slot', async () => {
  const { document, calls } = await setUp();
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, opts) => (String(url).includes('/api/diagrams/mermaid')
    ? { ok: false, status: 502, statusText: 'bad', json: async () => ({ detail: 'Kroki is not reachable' }) } : orig(url, opts));
  const flow = document.querySelector('[data-view-flow]');
  flow.open = true; flow.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait(100);
  assert.match(text(flow), /could not be drawn: Kroki is not reachable/);
  void calls;
});

/* ── PI-031 / PI-032 ─────────────────────────────────────────────────────── */

const TREE = {
  schemas: [{ schema: 'sales', classification: 'measured', table_count: 2, row_total: null, bytes_total: 1, is_estimate: false, reason: '',
    tables: [
      { name: 'orders', table_type: 'BASE TABLE', row_count: 5, row_count_state: 'measured', size_bytes: 8192, column_count: 2,
        columns: [
          { name: 'id', type: 'integer', nullable: false, key_role: 'PK', comment: '', default: "nextval('orders_id_seq')", foreign_key: null },
          { name: 'cust', type: 'integer', nullable: true, key_role: 'FK', comment: '', default: '', foreign_key: { foreign_schema: 'sales', foreign_table: 'customer', foreign_column: 'id' } }] },
      { name: 'items', table_type: 'BASE TABLE', row_count: 5, row_count_state: 'measured', size_bytes: 8192, column_count: null, columns: [] }] }],
  sources: { chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 29, tables: 412, merged: false },
    egeria: { state: 'measured', schema_count: 29, table_count: 412, surveyed_at: '2026-10-04T06:00:00' },
    local: { state: 'measured', schema_count: 29, table_count: 412, surveyed_at: '2026-10-03T09:00:00' }, disagree: false, unreadable: 0 },
};

test('PI-031: one line above the schema inventory names each count and where it came from', async () => {
  const { document } = await setUp({ stage: 'discovery', subTab: 'schema_inventory', over: { tree: TREE } });
  const line = document.querySelector('[data-schema-counts]');
  assert.ok(line);
  assert.equal(text(line), '29 schemas · 412 tables · 2 columns in the tables measured (1 tables\' columns not measured) · from Egeria survey 10-04');
});

test('PI-031: when every table reports its columns the line is the plain three numbers', async () => {
  const t = JSON.parse(JSON.stringify(TREE));
  t.schemas[0].tables[1].column_count = 3;
  const { document } = await setUp({ stage: 'discovery', subTab: 'schema_inventory', over: { tree: t } });
  assert.equal(text(document.querySelector('[data-schema-counts]')), '29 schemas · 412 tables · 5 columns · from Egeria survey 10-04');
});

test('PI-032: a column row shows its default and the foreign key target, and says when none is recorded', async () => {
  const { document } = await setUp({ stage: 'discovery', subTab: 'schema_inventory', over: { tree: TREE } });
  const tbl = document.querySelector('#schema-tree details details');
  const rows = [...tbl.querySelectorAll('tr[data-tree-text]')];
  const id = rows.find((r) => r.dataset.treeText === 'id');
  const cust = rows.find((r) => r.dataset.treeText === 'cust');
  assert.match(text(id), /nextval\('orders_id_seq'\)/);
  assert.match(text(id.querySelector('[data-col-fk]')), /^—$/, 'a column with no foreign key has none');
  assert.match(text(cust.querySelector('[data-col-default]')), /none recorded/);
  assert.equal(text(cust.querySelector('[data-col-fk]')), '→ sales.customer.id');
  assert.match(text(tbl.querySelector('thead')), /Default.*References/);
});

/* ── PI-049 ──────────────────────────────────────────────────────────────── */

test('PI-049: a repository\'s Understanding opens with the changes-since-last-run banner from /api/egeria/{slug}/diff', async () => {
  const repoDiff = { prev_date: '2026-10-03T09:00:00', curr_date: '2026-10-06T14:02:00', total_prev: 100, total_curr: 112, total_delta: 12,
    changes: [{ label: 'Python', prev: 60, curr: 72, delta: 12, pct: 20 }, { label: 'Markdown', prev: 10, curr: 9, delta: -1, pct: -10 }] };
  const { document, calls } = await setUp({ resourceType: 'repo', over: { repoDiff } });
  assert.ok(calls.some((c) => c.url.endsWith(`/api/egeria/${SLUG}/diff`)));
  const b = document.querySelector('[data-since-last-run]');
  assert.ok(b);
  assert.match(text(b), /^Since the run of 2026-10-03: 112 files, 12 more · 2 file types changed\./);
  b.querySelector('[data-names-toggle]').click();
  assert.match(text(b), /Python 60 → 72 \(\+12\)/);
  assert.match(text(b), /Markdown 10 → 9 \(−1\)/);
});

test('PI-049: one run only says there is nothing to compare; a failed read says it could not be read', async () => {
  const one = await setUp({ resourceType: 'repo', over: { repoDiff: {} } });
  assert.match(text(one.document.querySelector('[data-since-last-run]')), /Only one run so far — nothing to compare\./);
  const orig = globalThis.fetch;
  const { document } = await setUp({ resourceType: 'repo' });
  globalThis.fetch = async (u, o) => (String(u).includes('/diff') ? { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'registry down' }) } : orig(u, o));
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await wait();
  assert.match(text(document.querySelector('[data-since-last-run]')), /could not be read: registry down/);
});

/* ── PI-041 ──────────────────────────────────────────────────────────────── */

const RULE_EG = { name: 'EmailAddress', display_name: 'Email Address', description: 'Electronic mail address.', keywords: ['email', 'mail_addr'], source: 'Egeria (Active)' };
const RULE_LOCAL = { name: 'Password', display_name: 'Password', description: 'Credential.', keywords: ['password'], source: 'Local Fallback' };

test('PI-041: the term section lists the data-class rules Egeria applies, read-only, with their keywords', async () => {
  const { document, calls } = await setUp({ stage: 'curate', over: { rules: [RULE_EG, RULE_LOCAL] } });
  const b = document.querySelector('[data-curate-rules]');
  assert.ok(b);
  assert.match(text(b), /Data-class rules Egeria applies/);
  assert.ok(b.closest('[data-curate-work="glossary"]'), 'on the term section, not beside it');
  assert.match(text(b), /Email Address.*email, mail_addr/);
  assert.doesNotMatch(text(b.querySelector('[data-rules-egeria]')), /Password/, 'a local fallback rule is not claimed as Egeria\'s');
  assert.match(text(b.querySelector('[data-rules-local]')), /not read from Egeria.*Password/);
  assert.deepEqual(writes(calls), []);
  assert.equal(b.querySelectorAll('button, input, select, textarea').length, 0, 'read-only: no control in it');
});

test('PI-041: when only the built-in list answers, the block says "no reader yet" and does not claim the rules as Egeria\'s', async () => {
  const { document } = await setUp({ stage: 'curate', over: { rules: [RULE_LOCAL] } });
  const b = document.querySelector('[data-curate-rules]');
  assert.match(text(b), /no reader yet/);
  assert.match(text(b), /Egeria's own data-class rules were not read/);
  assert.equal(b.querySelector('[data-rules-egeria]'), null);
});

test('PI-041: an empty answer and a failed route both say "no reader yet" in words', async () => {
  const empty = await setUp({ stage: 'curate', over: { rules: [] } });
  assert.match(text(empty.document.querySelector('[data-curate-rules]')), /no reader yet/);
  const failed = await setUp({ stage: 'curate', over: { rules: Object.assign([], { __http: 500 }) } });
  assert.match(text(failed.document.querySelector('[data-curate-rules]')), /no reader yet.*could not be read/);
});

/* ── keyword source (re/vvd-typed-finds) ─────────────────────────────────── */

test('the rules block names the keyword source the read used', async () => {
  const eg = await setUp({ stage: 'curate', over: { rules: [{ ...RULE_EG, keyword_source: 'keywords: Egeria, 1 set' }, { ...RULE_LOCAL, keyword_source: 'keywords: Egeria, 1 set' }] } });
  assert.equal(text(eg.document.querySelector('[data-curate-rules] [data-rules-keyword-source]')), 'keywords: Egeria, 1 set');
  const none = await setUp({ stage: 'curate', over: { rules: [{ ...RULE_LOCAL, keyword_source: 'keywords: built-in list · Egeria had none' }] } });
  assert.equal(text(none.document.querySelector('[data-rules-keyword-source]')), 'keywords: built-in list · Egeria had none');
  const old = await setUp({ stage: 'curate', over: { rules: [RULE_LOCAL] } });
  assert.equal(old.document.querySelector('[data-rules-keyword-source]'), null, 'no label when the server gave none');
});

test('the Views section names the PII keyword source of the survey it came from', async () => {
  const { document } = await setUp({ over: { views: { state: 'measured', run: RUN, reason: '', views: [VIEW], keyword_source: 'keywords: built-in list · could not read Egeria' } } });
  assert.equal(text(document.querySelector('[data-section-views] [data-views-keyword-source]')), 'keywords: built-in list · could not read Egeria');
  const old = await setUp({});
  assert.equal(old.document.querySelector('[data-views-keyword-source]'), null);
});
