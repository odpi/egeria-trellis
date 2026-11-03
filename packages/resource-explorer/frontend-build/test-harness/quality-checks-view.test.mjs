/** Behaviour: proposed quality checks on a database's Curate page (stages/quality-checks.js).
 *  Real module, real colresize and selector; the API is a stub that behaves like the route (summary without rows,
 *  a group's rows on request, a verdict that is read back). Pinned: counts, collapsed groups, filters, the selector,
 *  the cue (pending, then the read-back, then a persistent failure), no selector on a not-established row, escaping,
 *  resizable columns, no bulk verdict, and signed-out disabling. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function check(over) {
  return {
    id: 1, check_key: 'sales.orders.placed::Required', schema_name: 'sales', table_name: 'orders', column_name: 'placed',
    test_type: 'Required', dimension: 'completeness', origin: 'vendored', general_type: 'D', params: { threshold_value: 0, profile_surveyed_at: 'x' },
    profile_surveyed_at: '2026-10-20T10:00:00+00:00', proposal_state: 'proposed', reason: '', status: 'proposed', selectable: true,
    verdict: null, ...over,
  };
}

function fakeApi(rows, { post = null, failRead = false, rules = null, publish = null } = {}) {
  const calls = { gets: [], posts: [] };
  const counts = () => {
    const c = { total: rows.length, proposed: 0, accepted: 0, rejected: 0, retired: 0, not_established: 0 };
    rows.forEach((r) => { c[r.status] += 1; });
    return c;
  };
  const api = {
    calls,
    ...(rules ? { async getRules(slug) { calls.rulesReads = (calls.rulesReads || 0) + 1; return rules(calls.rulesReads); } } : {}),
    ...(publish ? { async postPublish(slug) { calls.publishes = (calls.publishes || 0) + 1; return publish(slug); } } : {}),
    async getProposals(slug, params = {}) {
      calls.gets.push(params);
      if (failRead) throw new Error('registry down');
      const match = (r) => (!params.test_type || r.test_type === params.test_type) && (!params.dimension || r.dimension === params.dimension)
        && (!params.state || r.status === params.state);
      const byTable = new Map();
      rows.forEach((r) => { const k = `${r.schema_name}.${r.table_name}`; (byTable.get(k) || byTable.set(k, []).get(k)).push(r); });
      let groups = [...byTable.entries()].map(([k, rs]) => {
        const f = rs.filter(match);
        const c = { total: f.length, proposed: 0, accepted: 0, rejected: 0, retired: 0, not_established: 0 };
        f.forEach((r) => { c[r.status] += 1; });
        const [schema_name, table_name] = k.split('.');
        return { schema_name, table_name, counts: c, rows: params.table ? f : null };
      }).filter((g) => g.counts.total);
      if (params.table) groups = groups.filter((g) => g.table_name === params.table);
      groups.sort((a, b) => `${a.schema_name}.${a.table_name}`.localeCompare(`${b.schema_name}.${b.table_name}`));
      const uniq = (key) => [...new Set(rows.map((r) => r[key]))].sort().map((v) => ({ value: v, count: rows.filter((r) => r[key] === v).length }));
      return {
        slug, has_proposals: rows.length > 0, tables: byTable.size, surveyed_at: '2026-10-21T08:00:00+00:00',
        profile_surveyed_at: '2026-10-20T10:00:00+00:00', counts: counts(), shown: {}, applied: { test_type: params.test_type || '', dimension: params.dimension || '', state: params.state || '' },
        filters: { test_types: uniq('test_type'), dimensions: uniq('dimension'),
          states: ['proposed', 'accepted', 'rejected', 'retired', 'not_established'].map((s) => ({ value: s, count: counts()[s] })) },
        groups,
      };
    },
    async postVerdict(slug, key, verdict) {
      calls.posts.push({ key, verdict });
      if (post) return post(key, verdict, rows);
      const r = rows.find((x) => x.check_key === key);
      r.status = verdict;
      r.verdict = { id: calls.posts.length, verdict, decided_by: 'steward', created_at: '2026-10-21T09:00:00+00:00', note: '' };
      return { recorded: true, check: { ...r } };
    },
  };
  return api;
}

async function setUp(rows, { me = 'steward', ...opts } = {}) {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.location = document.defaultView.location;
  globalThis.getComputedStyle = document.defaultView.getComputedStyle.bind(document.defaultView);
  globalThis.history = document.defaultView.history;
  document.defaultView.matchMedia = document.defaultView.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const mod = await import('/static/next/stages/quality-checks.js');
  const slot = document.createElement('div');
  document.body.appendChild(slot);
  const api = fakeApi(rows, opts);
  const view = mod.mountQualityChecks(slot, 'coco', { api, me, openContents: () => { api.calls.contents = (api.calls.contents || 0) + 1; } });
  await tick(5);
  return { document, slot, api, view, mod };
}

const ROWS = () => [
  check({ id: 1 }),
  check({ id: 2, check_key: 'sales.orders.order_id::Unique', column_name: 'order_id', test_type: 'Unique', dimension: 'uniqueness' }),
  check({ id: 3, check_key: 'sales.orders.note::Missing_Pct', column_name: 'note', test_type: 'Missing_Pct', status: 'not_established',
    proposal_state: 'not_established', selectable: false, reason: 'its selection rule reads null_value_ct, which the profile did not measure' }),
  check({ id: 4, check_key: 'sales.items.sku::Required', table_name: 'items', column_name: 'sku' }),
];

const open = async (t, name) => {
  const g = [...t.slot.querySelectorAll('[data-qc-group]')].find((e) => e.dataset.qcGroup === name);
  g.open = true;
  g.dispatchEvent(new t.document.defaultView.Event('toggle'));
  await tick(5);
  return g;
};

test('counts and groups are drawn from the summary, collapsed, with no rows read yet', async () => {
  const t = await setUp(ROWS());
  assert.match(text(t.slot.querySelector('[data-qc-counts]')), /3 proposed · 1 not established/);
  assert.match(text(t.slot.querySelector('[data-qc-counts]')), /2 tables/);
  const groups = [...t.slot.querySelectorAll('[data-qc-group]')];
  assert.deepEqual(groups.map((g) => g.dataset.qcGroup), ['sales.items', 'sales.orders']);
  assert.ok(groups.every((g) => !g.open), 'collapsed');
  assert.match(text(groups[1].querySelector('[data-qc-group-counts]')), /2 proposed · 1 not established/);
  assert.equal(t.slot.querySelectorAll('[data-qc-row]').length, 0);
  assert.ok(t.api.calls.gets.every((p) => !p.table), 'no group read until opened');
});

test('opening a group reads that table only and draws a resizable grid with a selector per decidable check', async () => {
  const t = await setUp(ROWS());
  const g = await open(t, 'sales.orders');
  assert.deepEqual(t.api.calls.gets.at(-1), { test_type: '', dimension: '', state: '', schema: 'sales', table: 'orders' });
  const rows = [...g.querySelectorAll('[data-qc-row]')];
  assert.equal(rows.length, 3);
  const req = rows.find((r) => r.dataset.qcRow === 'sales.orders.placed::Required');
  assert.equal(req.querySelectorAll('[data-qc-act="verdict"]').length, 2);
  assert.deepEqual([...req.querySelectorAll('[data-qc-act="verdict"]')].map((b) => text(b)), ['Accept', 'Reject']);
  assert.match(text(req), /completeness/);
  assert.match(text(req), /threshold value 0/);
  assert.doesNotMatch(text(req), /profile_surveyed_at/, 'the profile date has its own column');
  assert.ok(g.querySelector('[data-rc-handle], .rc-handle, [data-grid-host]'), 'colresize grid');
  assert.equal(g.querySelectorAll('[role="columnheader"]').length, 6);
});

test('a not-established row has no selector and says why on the row', async () => {
  const t = await setUp(ROWS());
  const g = await open(t, 'sales.orders');
  const ne = g.querySelector('[data-qc-row="sales.orders.note::Missing_Pct"]');
  assert.equal(ne.querySelectorAll('button').length, 0);
  assert.equal(ne.querySelector('[data-qc-cue]').dataset.qcCue, 'not_established');
  assert.match(text(ne.querySelector('[data-qc-reason]')), /null_value_ct/);
});

test('an accept the profile cannot support is disabled with the reason on the row, Reject stays, the verdict stays', async () => {
  const why = 'this profile could not evaluate the check, so there are no parameters to accept; accept after a profile that measures it';
  const t = await setUp([check({ status: 'accepted', params: {}, accept_blocked: why,
    verdict: { id: 1, verdict: 'accepted', decided_by: 'steward', created_at: '2026-10-20T09:00:00+00:00', note: '' } })]);
  const g = await open(t, 'sales.orders');
  const row = g.querySelector('[data-qc-row="sales.orders.placed::Required"]');
  const acc = row.querySelector('[data-qc-verdict="accepted"]');
  const rej = row.querySelector('[data-qc-verdict="rejected"]');
  assert.ok(acc.disabled);
  assert.equal(acc.getAttribute('title'), why);
  assert.ok(!rej.disabled, 'Reject needs no parameters');
  assert.match(acc.className, /opacity-60/);
  assert.doesNotMatch(acc.className, /cursor-pointer/);
  assert.match(rej.className, /cursor-pointer/);
  assert.doesNotMatch(rej.className, /opacity-60/);
  assert.match(text(row.querySelector('[data-qc-accept-blocked]')), /could not evaluate the check/);
  assert.match(text(row), /accepted · you · 2026-10-20/, 'the earlier verdict stays visible');
  assert.equal(acc.getAttribute('aria-pressed'), 'true');
  acc.click();
  await tick(10);
  assert.equal(t.api.calls.posts.length, 0, 'a disabled Accept sends nothing');
  rej.click();
  await tick(10);
  assert.deepEqual(t.api.calls.posts, [{ key: 'sales.orders.placed::Required', verdict: 'rejected' }]);
});

test('a press shows recording… at once, then the verdict only once it is read back', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const t = await setUp(ROWS(), { post: async (key, verdict, rows) => {
    await gate;
    const r = rows.find((x) => x.check_key === key);
    r.status = verdict;
    r.verdict = { id: 9, verdict, decided_by: 'steward', created_at: '2026-10-21T09:00:00+00:00', note: '' };
    return { recorded: true, check: { ...r } };
  } });
  const g = await open(t, 'sales.orders');
  g.querySelector('[data-qc-row="sales.orders.placed::Required"] [data-qc-verdict="accepted"]').click();
  await tick(5);
  let row = g.querySelector('[data-qc-row="sales.orders.placed::Required"]');
  assert.equal(row.querySelector('[data-qc-cue]').dataset.qcCue, 'pending');
  assert.match(text(row), /recording…/);
  assert.ok([...row.querySelectorAll('button')].every((b) => b.disabled), 'no second press while one is out');
  release();
  await tick(10);
  row = g.querySelector('[data-qc-row="sales.orders.placed::Required"]');
  assert.equal(row.querySelector('[data-qc-cue]').dataset.qcCue, 'accepted');
  assert.match(text(row), /accepted · you · 2026-10-21/);
  assert.equal(row.querySelector('[data-qc-verdict="accepted"]').getAttribute('aria-pressed'), 'true');
  assert.equal(row.querySelector('[data-qc-verdict="rejected"]').getAttribute('aria-pressed'), 'false');
  assert.deepEqual(t.api.calls.posts, [{ key: 'sales.orders.placed::Required', verdict: 'accepted' }]);
});

test('the group and page counts follow a recorded verdict', async () => {
  const t = await setUp(ROWS());
  const g = await open(t, 'sales.orders');
  g.querySelector('[data-qc-row="sales.orders.placed::Required"] [data-qc-verdict="rejected"]').click();
  await tick(15);
  assert.match(text(t.slot.querySelector('[data-qc-counts]')), /2 proposed · 1 rejected · 1 not established/);
  assert.match(text(g.querySelector('[data-qc-group-counts]')), /1 proposed · 1 rejected · 1 not established/);
  assert.equal(g.querySelector('[data-qc-row="sales.orders.placed::Required"]').dataset.qcStatus, 'rejected');
});

test('Accept then Reject on the same check is two presses; the later verdict wins on screen', async () => {
  const t = await setUp(ROWS());
  const g = await open(t, 'sales.orders');
  const sel = (v) => g.querySelector(`[data-qc-row="sales.orders.placed::Required"] [data-qc-verdict="${v}"]`);
  sel('accepted').click(); await tick(10);
  sel('rejected').click(); await tick(10);
  assert.deepEqual(t.api.calls.posts.map((p) => p.verdict), ['accepted', 'rejected']);
  assert.equal(sel('rejected').getAttribute('aria-pressed'), 'true');
});

test('a write that fails leaves a cue on the row until the next press, and the old verdict stands', async () => {
  const t = await setUp(ROWS(), { post: async () => { throw new Error('sign in to accept or reject a quality check'); } });
  const g = await open(t, 'sales.orders');
  g.querySelector('[data-qc-row="sales.orders.placed::Required"] [data-qc-verdict="accepted"]').click();
  await tick(10);
  const row = g.querySelector('[data-qc-row="sales.orders.placed::Required"]');
  assert.equal(row.querySelector('[data-qc-cue]').dataset.qcCue, 'failed');
  assert.match(text(row), /not recorded · sign in to accept or reject a quality check/);
  assert.equal(row.dataset.qcStatus, 'proposed');
  await tick(30);
  assert.equal(g.querySelector('[data-qc-row="sales.orders.placed::Required"] [data-qc-cue]').dataset.qcCue, 'failed', 'it persists');
});

test('a verdict that is not read back matching is not shown as recorded', async () => {
  const t = await setUp(ROWS(), { post: async (key) => ({ recorded: false, check: { ...check({}), check_key: key } }) });
  const g = await open(t, 'sales.orders');
  g.querySelector('[data-qc-row="sales.orders.placed::Required"] [data-qc-verdict="accepted"]').click();
  await tick(10);
  const row = g.querySelector('[data-qc-row="sales.orders.placed::Required"]');
  assert.equal(row.querySelector('[data-qc-cue]').dataset.qcCue, 'failed');
  assert.match(text(row), /could not be read back/);
});

test('filters re-read with the choice and keep every option', async () => {
  const t = await setUp(ROWS());
  const sel = t.slot.querySelector('[data-qc-filter="test_type"]');
  assert.deepEqual([...sel.options].map((o) => o.value), ['', 'Missing_Pct', 'Required', 'Unique']);
  sel.value = 'Required';
  sel.dispatchEvent(new t.document.defaultView.Event('change'));
  await tick(10);
  assert.equal(t.api.calls.gets.at(-1).test_type, 'Required');
  assert.match(text(t.slot.querySelector('[data-qc-counts]')), /2 of 2 tables match/);
  const sel2 = t.slot.querySelector('[data-qc-filter="test_type"]');
  assert.equal(sel2.value, 'Required');
  assert.equal(sel2.options.length, 4, 'the way back is still offered');
  assert.deepEqual([...t.slot.querySelectorAll('[data-qc-filter="state"] option')].map((o) => o.value),
    ['', 'proposed', 'accepted', 'rejected', 'retired', 'not_established']);
});

test('no proposals is said, not drawn as zero checks', async () => {
  const t = await setUp([]);
  assert.match(text(t.slot), /No quality checks have been proposed for this database yet/);
  assert.equal(t.slot.querySelectorAll('[data-qc-group]').length, 0);
});

test('nobody signed in: the selector is there but disabled, with the reason', async () => {
  const t = await setUp(ROWS(), { me: '' });
  const g = await open(t, 'sales.orders');
  const b = g.querySelector('[data-qc-verdict="accepted"]');
  assert.ok(b.disabled);
  assert.match(b.getAttribute('title'), /sign in/);
});

test('there is no bulk verdict anywhere on the view', async () => {
  const t = await setUp(ROWS());
  await open(t, 'sales.orders');
  assert.doesNotMatch(text(t.slot), /accept all|accept every|reject all/i);
  const verdictButtons = t.slot.querySelectorAll('[data-qc-act="verdict"]');
  assert.ok([...verdictButtons].every((b) => b.closest('[data-qc-row]')), 'every verdict button belongs to one check row');
});

test('column names, reasons and parameters are escaped', async () => {
  const evil = '"><img src=x onerror=alert(1)>';
  const t = await setUp([check({ check_key: `s.t.${evil}::Required`, schema_name: 's', table_name: 't', column_name: evil,
      params: { threshold_value: evil } }),
    check({ id: 2, check_key: 's.t::Row_Ct', schema_name: 's', table_name: 't', status: 'not_established', selectable: false,
      reason: evil })]);
  const g = await open(t, 's.t');
  assert.equal(t.slot.querySelectorAll('img').length, 0);
  assert.match(text(g), /onerror=alert/, 'shown as text');
  assert.equal(g.querySelectorAll('[data-qc-row]').length, 2);
});

test('a read that fails says so inside the block', async () => {
  const t = await setUp(ROWS(), { failRead: true });
  assert.match(text(t.slot.querySelector('[data-qc-error]')), /could not be read: registry down/);
});

test('the grid never lets a long value push the page wide: cells break and the host scrolls inside its box', async () => {
  const t = await setUp(ROWS());
  const g = await open(t, 'sales.orders');
  const host = g.querySelector('[data-grid-host]');
  assert.match(host.className, /overflow-x-auto/);
  assert.match(host.className, /max-w-full/);
});

test('a test type is named in words, with the identifier and description behind a title; an unknown type shows its identifier', async () => {
  const t = await setUp([check({ test_type: 'Alpha_Trunc', test_name: 'Alpha Truncation', test_description: 'Tests that maximum length has not dropped.' }),
    check({ id: 2, check_key: 'sales.orders.b::X', column_name: 'b', test_type: 'Mystery_Type' })]);
  const g = await open(t, 'sales.orders');
  const cells = [...g.querySelectorAll('[data-qc-row]')].map((r) => text(r));
  assert.match(cells[0], /Alpha Truncation/);
  assert.doesNotMatch(cells[0], /Alpha_Trunc/);
  assert.match(g.querySelector('[title*="Alpha_Trunc"]').getAttribute('title'), /Tests that maximum length/);
  assert.match(cells[1], /Mystery_Type/);
});

test('parameters show only what is set, in words: a null is not a parameter', async () => {
  const t = await setUp([check({ params: { baseline_ct: null, baseline_value: null, threshold_value: 0, severity: 'error' },
    param_items: [{ key: 'threshold_value', label: 'threshold missing value count', text: '0' }, { key: 'severity', label: 'severity', text: 'error' }] }),
  check({ id: 2, check_key: 'sales.orders.c::Y', column_name: 'c', params: { baseline_ct: null, threshold_value: 5, severity: 'warning' } })]);
  const g = await open(t, 'sales.orders');
  const [a, b] = [...g.querySelectorAll('[data-qc-row]')].map((r) => text(r));
  assert.match(a, /threshold missing value count 0 · severity error/);
  assert.doesNotMatch(a + b, /null|=/);
  assert.match(b, /threshold value 5 · severity warning/, 'a server without param_items still gets words');
});

test('a long list of values is one summary with the list behind a disclosure', async () => {
  const list = Array.from({ length: 60 }, (_, i) => `v${i}`);
  const t = await setUp([check({ param_items: [{ key: 'baseline_value', label: 'list of placeholder values', list,
    summary: 'standard list of placeholder values · 60 values' }] })]);
  const g = await open(t, 'sales.orders');
  const d = g.querySelector('[data-qc-param-list]');
  assert.match(text(d.querySelector('summary')), /standard list of placeholder values · 60 values/);
  assert.ok(d.querySelector('summary') && !d.open, 'closed until opened');
});

test('a whole-table check shows a muted dash with a title, not "none named"', async () => {
  const t = await setUp([check({ dimension: '', column_name: '' })]);
  const g = await open(t, 'sales.orders');
  assert.doesNotMatch(text(g), /none named/);
  const dash = g.querySelector('[data-qc-row] [title*="no data-quality dimension"]');
  assert.equal(dash.textContent, '—');
});

test('a not-established row says it in the server\'s person words and keeps the stored sentence as a title', async () => {
  const t = await setUp([check({ status: 'not_established', selectable: false, reason: 'its selection rule reads frequent_patterns, which the profile did not measure',
    reason_words: 'needs the common text patterns, which this profile did not collect' })]);
  const g = await open(t, 'sales.orders');
  const r = g.querySelector('[data-qc-reason]');
  assert.match(text(r), /needs the common text patterns/);
  assert.match(r.getAttribute('title'), /selection rule/);
});

test('the default column widths fit the 800 px pane', async () => {
  const { QC_COLS } = await import('/static/next/stages/quality-checks.js');
  assert.ok(QC_COLS.columns.reduce((n, c) => n + c.def, 0) + QC_COLS.chrome <= 800);
});

// ── quality-screen wording, round 2 (read §C, 2026-10-30). jsdom has no layout, so "fits at 1440" is pinned as the
// visible line's length: the cell must not carry a 24-name list or JSON, which is what wrapped to ~25 lines at 1440.
const DUPE_NAMES = Array.from({ length: 24 }, (_, i) => `column_${i}`);
const visible = (el) => { const c = el.cloneNode(true); c.querySelectorAll('details > :not(summary)').forEach((n) => n.remove()); return text(c); };

test('QS-1: Duplicate Rows says "24 columns compared (all but rowguid)", the names behind a disclosure, the why in a title', async () => {
  const t = await setUp([check({ column_name: '', test_type: 'Dupe_Rows', check_key: 'sales.orders::Dupe_Rows', dimension: '',
    params: { groupby_names: DUPE_NAMES, skip_errors: 0, excluded_columns: [{ column: 'rowguid', general_type: 'X' }], severity: 'error' },
    param_items: [{ key: 'groupby_names', label: 'columns compared', list: DUPE_NAMES, summary: '24 columns compared (all but rowguid)',
      title: 'rowguid is left out: its type has no equality (identifiers, binary, arrays and the like), so it cannot be compared' },
    { key: 'skip_errors', label: 'threshold failure count', text: '0' }, { key: 'severity', label: 'severity', text: 'error' }] })]);
  t.document.defaultView.innerWidth = 1440;
  const g = await open(t, 'sales.orders');
  const row = g.querySelector('[data-qc-row]');
  const d = row.querySelector('[data-qc-param-list]');
  assert.equal(text(d.querySelector('summary')), '24 columns compared (all but rowguid)');
  assert.ok(!d.open, 'the 24 names stay closed until asked for');
  assert.match(d.querySelector('summary').getAttribute('title'), /rowguid is left out: its type has no equality/);
  const params = visible(row);
  assert.ok(!params.includes('column_3,'), 'no name list on the visible line');
  assert.match(params, /24 columns compared \(all but rowguid\) · threshold failure count 0 · severity error/);
});

test('QS-2: no parameter reads as JSON, even from a server that sends no param_items', async () => {
  const t = await setUp([check({ column_name: '', test_type: 'Dupe_Rows', check_key: 'sales.orders::Dupe_Rows',
    params: { groupby_names: ['a', 'b'], excluded_columns: [{ column: 'rowguid', general_type: 'X' }], severity: 'error' } })]);
  const g = await open(t, 'sales.orders');
  const said = text(g.querySelector('[data-qc-row]'));
  assert.doesNotMatch(said, /[[\]{}"]|general_type/, said);
  assert.match(said, /excluded columns rowguid/);
});

test('QS-3: a not-established row carries no type-inference tail ("undetermined", "statements unresolved")', async () => {
  const t = await setUp([check({ status: 'not_established', selectable: false, proposal_state: 'not_established',
    reason: 'type not inferred · statements unresolved: Constant', reason_words: "the column's type could not be worked out" })]);
  const g = await open(t, 'sales.orders');
  const r = g.querySelector('[data-qc-reason]');
  assert.equal(text(r), "the column's type could not be worked out");
  assert.doesNotMatch(text(g), /undetermined|statements unresolved/);
  assert.match(r.getAttribute('title'), /statements unresolved/, 'the stored sentence stays behind the title');
});

test('where a check comes from is said in the detailed title only, never in the visible words', async () => {
  const t = await setUp([check({ test_type: 'Unique', test_name: 'Percent Unique', test_description: 'Tests for duplicates.',
    attribution: 'Implements TestGen test type 1034 (catalogue commit 3606859c)' })]);
  const g = await open(t, 'sales.orders');
  const row = g.querySelector('[data-qc-row]');
  assert.doesNotMatch(text(row), /TestGen/, 'no visible word says it');
  assert.match(row.querySelector('[title*="Implements TestGen"]').getAttribute('title'), /Tests for duplicates\..*Implements TestGen test type 1034/);
});

// ── Publish ───────────────────────────────────────────────────────────────────────────────────────────────────
const KEY_A = 'sales.orders.placed::Required';
const KEY_B = 'sales.orders.order_id::Unique';
const preview = (over = {}) => ({ accepted: 3, to_publish: 2, to_retire: 0, waiting: 0, unchanged: 1, edited: 0, retry: 0,
  words: '2 to publish · 1 unchanged', ...over });
const rulesPayload = (over = {}) => ({ rules: {}, preview: preview(), in_egeria: true, ...over });
const clickEl = (t, el) => el.dispatchEvent(new t.document.defaultView.MouseEvent('click', { bubbles: true }));
const btn = (t) => t.slot.querySelector('[data-qc-act="publish"]');

test('Publish: the before line says what a press would do, a press runs once, the after words come from the server', async () => {
  let reads = 0;
  const t = await setUp(ROWS(), {
    rules: () => { reads += 1; return reads === 1 ? rulesPayload() : rulesPayload({ preview: preview({ to_publish: 0, unchanged: 3, words: '3 unchanged' }),
      rules: { [KEY_B]: { state: 'created', words: 'in Egeria · DataQualityRule · read back' } } }); },
    publish: async () => { await tick(5); return { state: 'published', state_words: 'published', line: 'quality rules: 2 new',
      outcomes: { [KEY_A]: { state: 'created', words: 'published · created in Egeria' }, [KEY_B]: { state: 'unchanged', words: 'published · unchanged' } } }; },
  });
  assert.match(text(t.slot.querySelector('[data-qc-publish-line]')), /^2 to publish · 1 unchanged$/);
  assert.equal(btn(t).disabled, false);
  const g = await open(t, 'sales.orders');
  clickEl(t, btn(t)); clickEl(t, btn(t));                       // a double press
  assert.equal(btn(t).disabled, true, 'disabled while it runs');
  assert.match(text(t.slot.querySelector('[data-qc-publish-why]')), /a press runs once/);
  await tick(30);
  assert.equal(t.api.calls.publishes, 1, 'one press runs once');
  assert.equal(t.api.calls.rulesReads, 2, 'the preview is read again after the write');
  assert.match(text(t.slot.querySelector('[data-qc-publish-after]')), /^\S+ published$/, 'a glyph, then the state words');
  assert.match(text(t.slot.querySelector('[data-qc-publish-line]')), /3 data quality rules already in Egeria/);
  const cues = [...g.querySelectorAll('[data-qc-rule]')].map((e) => text(e));
  assert.ok(cues.some((c) => /Egeria data quality rule · published · created in Egeria/.test(c)));
  assert.ok(cues.some((c) => /Egeria data quality rule · published · unchanged/.test(c)));
});

test('Publish: the coco_pharma shape (3 waiting, nothing written) says "nothing published", not "published", and links to Contents', async () => {
  const waiting = { state: 'not_in_egeria', words: 'not in Egeria · commit its schema in Contents' };
  const t = await setUp(ROWS(), {
    rules: () => rulesPayload({ preview: preview({ accepted: 3, to_publish: 3, unchanged: 0, words: '3 to publish' }) }),
    publish: async () => ({ state: 'nothing_published', state_words: 'nothing published · 3 waiting for Contents', line: 'quality rules: nothing to write; 3 not in Egeria',
      outcomes: { [KEY_A]: waiting, [KEY_B]: waiting } }),
  });
  const g = await open(t, 'sales.orders');
  clickEl(t, btn(t));
  await tick(30);
  const after = text(t.slot.querySelector('[data-qc-publish-after]'));
  assert.match(after, /^\S+ nothing published · 3 waiting for Contents$/);
  assert.doesNotMatch(after, /^\S+ published/, 'a press that wrote nothing is not "published"');
  const row = [...g.querySelectorAll('[data-qc-rule="not_in_egeria"]')];
  assert.equal(row.length, 2);
  assert.match(text(row[0]), /not in Egeria · commit its schema in Contents/);
  clickEl(t, row[0].querySelector('[data-qc-act="contents"]'));
  assert.equal(t.api.calls.contents, 1, 'the row links to Contents');
});

test('Publish: a refused check shows Egeria\'s own sentence on its row', async () => {
  const t = await setUp(ROWS(), {
    rules: () => rulesPayload(),
    publish: async () => ({ state: 'nothing_published', state_words: 'nothing published · 1 could not be published', line: 'x',
      outcomes: { [KEY_B]: { state: 'refused', words: 'refused by Egeria · the type DataQualityRule is not known here' } } }),
  });
  const g = await open(t, 'sales.orders');
  clickEl(t, btn(t));
  await tick(30);
  assert.match(text(g.querySelector('[data-qc-rule="refused"]')), /refused by Egeria · the type DataQualityRule is not known here/);
});

test('Publish: disabled, with a sentence, only when nobody is signed in or a press is running', async () => {
  const noOne = await setUp(ROWS(), { me: '', rules: () => rulesPayload(), publish: async () => ({}) });
  assert.equal(btn(noOne).disabled, true);
  assert.match(text(noOne.slot.querySelector('[data-qc-publish-why]')), /sign in to publish/);
  clickEl(noOne, btn(noOne));
  assert.equal(noOne.api.calls.publishes, undefined, 'a disabled press does nothing');
});

test('Publish (F27): a press POSTs even when the preview says nothing would change, nothing is accepted, or the database has no asset', async () => {
  for (const over of [
    { preview: preview({ accepted: 2, to_publish: 0, unchanged: 2, words: '2 unchanged' }) },
    { preview: preview({ accepted: 0, to_publish: 0, unchanged: 0, words: 'nothing is accepted yet' }) },
    { in_egeria: false },
  ]) {
    const t = await setUp(ROWS(), { rules: () => rulesPayload(over), publish: async () => ({ state: 'published', state_words: 'published', line: 'x' }) });
    assert.equal(btn(t).disabled, false, JSON.stringify(over));
    clickEl(t, btn(t));
    await tick(30);
    assert.equal(t.api.calls.publishes, 1, `one POST for ${JSON.stringify(over)}`);
  }
});

test('Publish (W17): "2 unchanged" is said as what it means', async () => {
  const t = await setUp(ROWS(), { rules: () => rulesPayload({ preview: preview({ accepted: 2, to_publish: 0, unchanged: 2, words: '2 unchanged' }) }),
    publish: async () => ({}) });
  assert.equal(text(t.slot.querySelector('[data-qc-publish-line]')), '2 data quality rules already in Egeria · a press changes nothing');
});

test('Publish (F25): "publishing…" at once, then the state, counts and read-back time from the response; on reload "last published" from /rules', async () => {
  let release;
  const t = await setUp(ROWS(), {
    rules: () => rulesPayload({ last_published: { at: '2026-10-30T12:50:00+00:00', by: 'erinoverview' } }),
    publish: () => new Promise((res) => { release = () => res({ state: 'published', state_words: 'published', line: 'x', counts: { unchanged: 2 },
      read_at: '2026-10-30T12:51:15+00:00' }); }),
  });
  assert.match(text(t.slot.querySelector('[data-qc-publish-last]')), /^last published by erinoverview · read back 2026-10-30 12:50$/);
  clickEl(t, btn(t));
  assert.match(text(t.slot.querySelector('[data-qc-publish-after]')), /publishing…/);
  release();
  await tick(30);
  assert.match(text(t.slot.querySelector('[data-qc-publish-after]')), /published · 2 unchanged · read back 2026-10-30 12:51$/);
});

test('Quality table (F26): the grid carries data-rc-id and a resize handle per column, and a drag changes a width', async () => {
  const t = await setUp(ROWS(), {});
  const g = await open(t, 'sales.orders');
  const host = g.querySelector('[data-grid-host]');
  assert.equal(host.getAttribute('data-rc-id'), 'db-quality-checks');
  assert.equal(host.querySelectorAll('[data-rc-handle]').length, 6);
  const h = host.querySelector('[data-rc-handle="params"]');
  const win = t.document.defaultView;
  const ev = (type, x) => new win.MouseEvent(type, { bubbles: true, clientX: x });
  h.dispatchEvent(ev('pointerdown', 100)); win.document.dispatchEvent(ev('pointermove', 160)); win.document.dispatchEvent(ev('pointerup', 160));
  assert.notEqual(host.style.getPropertyValue('--rc-params'), '', 'the dragged column took a width');
});

test('Publish: a rules read that fails is said, never shown as "nothing to publish"; the line says so', async () => {
  const t = await setUp(ROWS(), { rules: () => { throw new Error('registry down'); }, publish: async () => ({}) });
  assert.match(text(t.slot.querySelector('[data-qc-publish-line]')), /could not read what a publish would do: registry down/);
  assert.doesNotMatch(text(t.slot.querySelector('[data-qc-publish]')), /nothing to publish/);
  assert.equal(btn(t).disabled, false, 'a press does not depend on the preview read');
});

test('Publish: a failed press says so on the band and publishes nothing it cannot read back', async () => {
  const t = await setUp(ROWS(), { rules: () => rulesPayload(), publish: async () => { throw new Error('Egeria could not be reached'); } });
  clickEl(t, btn(t));
  await tick(30);
  assert.match(text(t.slot.querySelector('[data-qc-publish-after]')), /not published · Egeria could not be reached/);
  assert.equal(btn(t).disabled, false, 'it can be pressed again');
});

test('a server without the publish routes draws no Publish band (the module still loads with just proposals)', async () => {
  const t = await setUp(ROWS());
  assert.equal(t.slot.querySelector('[data-qc-publish]'), null);
});
