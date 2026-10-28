/** Behaviour: Curate for a DATABASE draws the catalogue COMMIT (slice B,
 *  BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md): the manifest of the three mechanisms, the
 *  Catalogue button, the per-node "In Egeria" states, and a header marker that comes from state.
 *
 *  Real app.js, real router, the real Curate nav button, a stateful stub behind fetch. Every
 *  state word on screen must be one the stub's `view.commit` (the server's rows) sent: the
 *  known negatives drop the commit data and the words go with it, and a server that sends a
 *  different header gets a different header (never a constant).
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const node = (kind, name, over = {}) => ({
  kind, name, key: name, state: 'undecided', proposal: null, live_proposal: null, overridden: null,
  disagrees: null, notes: [], marks: [], explicit: null, effective: null, effective_from: null,
  new_since: false, access: 'established', provenance: '',
  last_write: { state: 'not_established', from: '', to: '', text: 'not established' },
  source: { kind: 'egeria', as_of: '2026-10-04T06:00:00', text: 'from Egeria survey 10-04' }, facts_from: {},
  data_classes: { state: 'not_established', classes: [], pii_columns: 0 }, ...over,
});
const table = (schema, name, over = {}) => node('table', name, {
  schema, key: `${schema}.${name}`, table_type: 'BASE TABLE', row_count: 10, row_count_state: 'measured',
  inherited: null, differs_from_schema: false, column_count: 1, ...over,
});
const schema = (name, tables = [], over = {}) => node('schema', name, {
  classification: 'data', table_count: tables.length, row_total: 20, bytes_total: null, is_estimate: false,
  undecided_words: 'keeps what’s in Egeria now', tables, ...over,
});
const chose = (choice) => ({
  choice, action: 'set', source: 'person', by: 'dwolfson', at: '2026-10-04T10:00:00', reason: '',
  proposal_rule: '', proposal_choice: '', measured_at: '', measured: {},
});
const NOT_COMMITTED = 'Saved in Resource Explorer · not yet published to Egeria';

function baseView(over = {}) {
  return {
    database: 'adventureworks',
    egeria_element: { guid: 'abcdef12-0', short: 'abcdef12', text: 'abcdef12' },
    survey: { state: 'measured', schema_count: 6, table_count: 9, surveyed_at: '2026-10-04T06:00:00', report_guid: 'g' },
    sources: {
      chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 6, tables: 9, merged: false },
      egeria: { state: 'measured', schema_count: 6, table_count: 9, surveyed_at: '2026-10-04T06:00:00', report_guid: 'g' },
      local: { state: 'not_measured', schema_count: 0, table_count: 0, surveyed_at: '', surveyed_as: '', sees: '' },
      disagree: false, unreadable: 0,
    },
    declared: { declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', kind: 'first', baseline_survey_at: '' },
    depth: { value: 'tables_and_columns', declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', help: 'h', commit_note: '',
      options: [{ id: 'tables_and_columns', label: 'tables and columns', how: 'everything' }] },
    system: null,
    counts: { schemas_offered: 6, schemas_catalogue: 2, schemas_leave_out: 1, schemas_undecided: 3 },
    new_since: { declared: true, schemas: 0, tables: 0, tables_in_known_schemas: 0, schema_names: [], text: '' },
    schemas: [
      schema('sales', [table('sales', 'orders'), table('sales', 'customers', { explicit: chose('leave_out'), effective: 'leave_out' })],
        { explicit: chose('catalogue'), effective: 'catalogue', state: 'chosen' }),
      schema('archive', [table('archive', 'orders')], { explicit: chose('catalogue'), effective: 'catalogue', state: 'chosen' }),
      schema('old', [table('old', 'things')], { explicit: chose('leave_out'), effective: 'leave_out', state: 'chosen' }),
      schema('plain', [table('plain', 'p1')]),
    ],
    commit: {
      header: { state: 'not_committed', text: NOT_COMMITTED },
      database: null, collisions: [],
      schemas: {
        sales: { state: 'uncommitted', words: 'not committed yet', second: 'chosen in the scope · nothing read back from Egeria' },
        archive: { state: 'uncommitted', words: 'not committed yet', second: 'chosen in the scope · nothing read back from Egeria' },
        old: { state: 'left_out', words: 'left out', second: 'scope record · never published' },
        plain: { state: 'none', words: '', second: '' },
      },
      tables: {},
    },
    ...over,
  };
}

const stateOf = (document, key) => document.querySelector(`[data-scope-row="${key}"] [data-scope-egeria-state]`);
const secondOf = (document, key) => document.querySelector(`[data-scope-row="${key}"] [data-scope-egeria-second]`);

function previewFor(over = {}) {
  return {
    can_commit: true, button: 'Publish · 2 schemas', blockers: [], blocked_schemas: [], blocked_notes: [],
    attach: ['sales', 'archive'], refused: [], leave_out: [], collisions: [],
    survey: { schemas: ['sales', 'archive'], not_scopable: [], line: "Egeria's survey is limited to your chosen schemas" },
    manifest: {
      lines: [
        { id: 're_publishes', mechanism: 1, text: "RE publishes the server and database assets and RE's own survey report, supplying the database description and version, and joins the deployment's publish zones: zone-a, zone-b. That is written before any target is attached. Not carried: owner, not declared on Context." },
        { id: 'cataloguer_creates', mechanism: 2, text: "Egeria's cataloguer creates tables and columns for 2 schema targets (2 to attach now). Each is a schema-kind target, never the database or the server. The elements arrive on the daemon's next refresh, not now." },
        { id: 'survey_measures', mechanism: 3, text: "Egeria's survey is limited to your chosen schemas: sales, archive" },
        { id: 'whole_schemas', mechanism: 0, text: 'Egeria adds whole schemas · table choices are kept for when it can' },
      ],
      schema_targets: 2, new_targets: 2, survey_schemas: ['sales', 'archive'], zones: ['zone-a', 'zone-b'], owner: '',
      whole_schemas_line: 'Egeria adds whole schemas · table choices are kept for when it can',
    },
    ...over,
  };
}

function makeServer(view, preview, { signedIn = true, advance = null, finalSteps = null } = {}) {
  const s = { calls: [], view, preview, signedIn, record: null, polls: 0 };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (u.includes('/api/catalogue-scope/')) {
      if (method === 'GET' && u.endsWith('/commit-preview')) {
        if (s.preview === 'fail') return err(500, 'Egeria unreachable');
        return ok(s.preview);
      }
      if (method === 'POST' && u.endsWith('/commit')) {
        if (!s.signedIn) return err(401, 'Sign in to publish');
        s.record = { id: 'cafe0123456789', author: 'me', state: 'queued', steps: [
          { name: 'publish_elements', state: 'pending', detail: '' }, { name: 'schema_targets', state: 'pending', detail: '' },
          { name: 'read_back', state: 'pending', detail: '' }] };
        return ok({ curation: s.record, run_id: 'run00000-1', activity_id: 'a' });
      }
      if (method === 'GET' && u.endsWith('/commits/latest')) return ok({ commit: null, terminal: null, age_hours: null, stale_unfinished: false, states: {} });   // nothing committed before this tab
      if (method === 'GET' && u.includes('/commits/')) {
        s.polls += 1;
        if (s.polls >= 2) {
          s.record = { ...s.record, state: 'done', steps: finalSteps || [
            { name: 'publish_elements', state: 'done', detail: 'server s · database d' },
            { name: 'schema_targets', state: 'done', detail: '2 of 2 attached, each with its proof row' },
            { name: 'read_back', state: 'done', detail: '0 published · 2 attached, waiting' }] };
          if (advance) advance(s);
        } else s.record = { ...s.record, state: 'running' };
        return ok(s.record);
      }
      if (method === 'POST' && u.endsWith('/read-back')) return ok({ catalogued: 1, attached_waiting: 1, read_failed: 0 });
      if (method === 'GET') return ok(s.view);
      return ok({ state: 'ok' });
    }
    if (u.includes('/api/journal/')) return ok({ entries: [], suggested_to: [] });
    if (u.includes('/api/curate/') || u.includes('/api/projects/groups') || u.includes('/api/projects/?')) return ok([]);
    if (u.includes('/api/databases/')) return ok([{ slug: 'adventureworks', group_slug: '' }]);
    if (u.includes('/api/filesystems/')) return ok([{ slug: 'adventureworks', group_slug: '' }]);
    return ok({});
  };
  return s;
}

async function setUp(view, preview, opts = {}) {
  const { document, window } = makeDomEnvironment();
  const signedIn = opts.signedIn !== false;
  const server = makeServer(view, preview, { signedIn, advance: opts.advance, finalSteps: opts.finalSteps });
  ensureLoaderRegistered();
  const scope = await import('/static/next/stages/curate-scope.js');
  scope.resetScopeUi();
  scope.setCommitPollMs(10);
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'understanding';
  app.state.subTab = 'questions';
  app.state.investigations = []; app.state.investigation = '';
  app.state.workListSlug = null; app.state.workListIndex = false;
  app.state.me = signedIn ? { user_id: 'me' } : null;
  app.state.groups = [];
  app.state.databasesLoaded = true; app.state.filesystemsLoaded = true;
  app.state.databases = [{ slug: 'adventureworks', group_slug: '' }];
  app.state.filesystems = [{ slug: 'adventureworks', group_slug: '' }];
  app.state.perspectives = []; app.state.allPerspectives = [];
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  const btn = document.querySelector('#intent-nav button[data-stage="curate"]');
  assert.ok(btn, 'nav must offer a Curate button');
  btn.click();
  await wait();
  // A database's Curate is three tabs (Brief S2): the scope tree and the commit panel are on Contents.
  const contentsTab = document.querySelector('[data-subtab="curate_contents"]');
  assert.ok(contentsTab, 'a database offers the Contents tab');
  contentsTab.click(); await wait();
  // a declared scope starts collapsed to one line, and the commit's preview is read only once it is open
  const fold = document.querySelector('[data-scope-collapse]');
  if (opts.leaveCollapsed) return { document, window, app, server };
  if (fold && fold.getAttribute('aria-expanded') === 'false') { fold.click(); await wait(); }
  return { document, window, app, server };
}

const commitEl = (d) => d.querySelector('[data-scope-commit]');

/* ── the manifest ──────────────────────────────────────────────────────── */

test('the manifest lists the three mechanisms in order, the target count, the survey schemas and the whole-schemas line', async () => {
  const { document } = await setUp(baseView(), previewFor());
  const lines = [...document.querySelectorAll('[data-scope-manifest-line]')];
  assert.deepEqual(lines.map((l) => l.dataset.scopeManifestLine), ['re_publishes', 'cataloguer_creates', 'whole_schemas', 'survey_measures']);
  // the table: one row per mechanism, the long sentences behind "details" on the row they qualify
  const rows = [...document.querySelectorAll('[data-scope-manifest-row]')].map((r) => r.dataset.scopeManifestRow);
  assert.deepEqual(rows, ['publishes', 'catalogs', 'surveys', 'left_out', 'undecided']);
  assert.match(flat(document.querySelector('[data-scope-manifest-row="catalogs"]')), /Egeria adds your included schemas.*2 · sales, archive.*next refresh/);
  assert.match(flat(lines[0]), /^1\. RE publishes .* before any target is attached/);
  assert.match(flat(lines[1]), /^2\. Egeria's cataloguer creates tables and columns for 2 schema targets .* never the database or the server/);
  assert.match(flat(lines[3]), /^3\. Egeria's survey is limited to your chosen schemas: sales, archive$/);
  assert.match(flat(lines[2]), /^Egeria adds whole schemas · table choices are kept for when it can$/);
  const btnBox = document.querySelector('[data-scope-commit-row]');
  assert.ok(btnBox.querySelector('[data-scope-commit-btn]') && btnBox.querySelector('[data-scope-refresh-now]'), 'button, then the refresh box, in the same right-hand column');
  assert.equal(flat(document.querySelector('[data-scope-commit-btn]')), 'Publish · 2 schemas');
  assert.equal(document.querySelector('[data-scope-commit-btn]').disabled, false);
});

test('leave-out rows name their form before the press: soft delete, archive, and "couldn\'t check"', async () => {
  const preview = previewFor({
    button: 'Publish · 2 schemas · removes 1 from Egeria · archives 1',
    leave_out: [
      { schema: 'old', form: 'soft_delete', blocked: false, text: 'old: nothing hangs off it · will delete in Egeria with its 1 tables' },
      { schema: 'ledger', form: 'archive', blocked: false, text: "ledger: 2 term assignments hang off it · will archive in Egeria, not delete · re-including it later creates a new element; the archived record stays" },
      { schema: 'tmp', form: 'cannot_check', blocked: true, text: "tmp: couldn't check what hangs off it" },
    ],
  });
  const { document } = await setUp(baseView(), preview);
  const rows = Object.fromEntries([...document.querySelectorAll('[data-scope-leave-out]')].map((r) => [r.dataset.scopeLeaveOut, r]));
  assert.equal(rows.old.dataset.form, 'soft_delete');
  assert.match(flat(rows.old), /nothing hangs off it · will delete in Egeria/
  );
  assert.doesNotMatch(flat(rows.old), /removed|soft-deleted/);
  assert.equal(rows.ledger.dataset.form, 'archive');
  assert.match(flat(rows.ledger), /2 term assignments hang off it · will archive in Egeria, not delete/);
  assert.match(flat(rows.ledger), /re-including it later creates a new element; the archived record stays/);
  assert.match(flat(rows.tmp), /^⚠ tmp: couldn't check what hangs off it$/);
  assert.doesNotMatch(flat(rows.tmp), /nothing hangs off it/);
  assert.match(flat(document.querySelector('[data-scope-commit-btn]')), /removes 1 from Egeria · archives 1/);
});

test('a name collision is flagged on the row and in the panel, and the commit button is disabled with the reason', async () => {
  const view = baseView();
  view.schemas.push(schema('a_b', [table('a_b', 't_ab')], { explicit: chose('catalogue'), effective: 'catalogue' }));
  view.commit.collisions = [{ kind: 'schema', schema: 'a_b', name: 'a_b', other: 'aXb', text: "⚠ Egeria's listing for a_b would also return aXb" }];
  view.commit.schemas.a_b = { state: 'uncommitted', words: 'not committed yet', second: '' };
  const preview = previewFor({
    can_commit: false, blockers: ["1 name collision Egeria's listing can't tell apart: leave the schema out, or rename it in the database"],
    collisions: view.commit.collisions,
  });
  const { document } = await setUp(view, preview);
  assert.match(flat(document.querySelector('[data-scope-row="schema:a_b"] [data-scope-collision]')), /Egeria's listing for a_b would also return aXb/);
  assert.match(flat(document.querySelector('[data-scope-collision-line]')), /^⚠ Egeria's listing for a_b would also return aXb$/);
  assert.ok(document.querySelector('[data-scope-commit-row]').contains(document.querySelector('[data-scope-collision-line]')), 'listed directly under the button');
  const btn = document.querySelector('[data-scope-commit-btn]');
  assert.equal(btn.disabled, true);
  assert.match(btn.title, /^name collisions: Egeria's listing for a_b would also return aXb$/);
  assert.match(flat(document.querySelector('[data-scope-commit-why]')), /^⚠ name collisions: /);
});

test('a schema whose archived copy could not be checked is held with that sentence', async () => {
  const preview = previewFor({ refused: [{ schema: 'ledger', text: 'ledger: could not check whether an archived copy exists' }] });
  const { document } = await setUp(baseView(), preview);
  assert.match(flat(document.querySelector('[data-scope-refused="ledger"]')), /could not check whether an archived copy exists/);
});

test('a preview that cannot be read says so and leaves no Catalog button', async () => {
  const { document } = await setUp(baseView(), 'fail');
  assert.match(flat(document.querySelector('[data-scope-commit-error]')), /The commit preview could not be read: Egeria unreachable/);
  assert.equal(document.querySelector('[data-scope-commit-btn]'), null);
});

test('signed out: the manifest is readable, the commit and the read are disabled with the sign-in reason', async () => {
  const { document } = await setUp(baseView(), previewFor(), { signedIn: false });
  assert.ok(document.querySelector('[data-scope-manifest-line]'));
  const btn = document.querySelector('[data-scope-commit-btn]');
  assert.equal(btn.disabled, true);
  assert.match(btn.title, /sign in to change what gets published/);
  assert.equal(document.querySelector('[data-scope-read-back]').disabled, true);
  assert.equal(document.querySelector('[data-scope-refresh-now]').disabled, true);
});

/* ── per-node states, from the server's proof rows ─────────────────────── */

function committedView() {
  const v = baseView();
  v.commit = {
    header: { state: 'committed', text: 'Database element d0000001 in Egeria · published 10-05 09:00 · 2 schemas chosen: 1 published, 1 attached, waiting · cataloguer connector\'s last refresh 10-05 09:05 (the connector\'s, not a schema\'s)' },
    database: { guid: 'd0000001-0', short: 'd0000001', at: '2026-10-05T09:00:00' },
    collisions: [],
    schemas: {
      sales: { state: 'catalogued', words: 'published · 2 tables · read back 10-05 09:12', second: 'element · read back from Egeria' },
      archive: { state: 'attached_waiting', words: "attached · waiting for Egeria's next refresh", second: "connector's last refresh 09:05 (the connector's, not this schema's)" },
      old: { state: 'deleted', words: 'deleted in Egeria · 10-05 09:30', second: "was published · Egeria's cataloguer still lists this schema until its connector restarts · nothing is recreated" },
      plain: { state: 'failed', words: 'failed · 500 Egeria says no', second: 'step: attach · outbox #7 · will retry' },
    },
    tables: {
      'sales.orders': { state: 'catalogued', words: 'published · read back 10-05 09:12', second: 'element · read back from Egeria' },
      'sales.customers': { state: 'catalogued', words: 'published · read back 10-05 09:12', second: 'Egeria adds whole schemas · table choices are kept for when it can' },
      'archive.orders': { state: 'follows_schema', words: 'as its schema: attached waiting', second: '' },
      'old.things': { state: 'follows_schema', words: 'as its schema: deleted in Egeria', second: '' },
      'plain.p1': { state: 'none', words: '', second: '' },
    },
  };
  v.schemas.push(schema('ledger', [table('ledger', 'l1')], { explicit: chose('catalogue'), effective: 'catalogue' }));
  v.schemas.push(schema('q', [table('q', 'q1')], { explicit: chose('catalogue'), effective: 'catalogue' }));
  v.commit.schemas.ledger = { state: 'archived', words: 'archived in Egeria · 10-05 09:31', second: 're-including creates a new element; the archived record stays' };
  v.commit.schemas.q = { state: 'queued', words: 'queued · outbox #4182', second: 'step: attach' };
  return v;
}

test('every state word on a schema row is the server\'s, with its glyph and second line', async () => {
  const { document } = await setUp(committedView(), previewFor());
  const word = (k) => stateOf(document, `schema:${k}`);
  assert.equal(word('sales').dataset.scopeEgeriaWord, 'catalogued');
  assert.match(flat(word('sales')), /^✓ published · 2 tables · read back 10-05 09:12$/);
  assert.match(flat(secondOf(document, 'schema:sales')), /element · read back from Egeria/);
  assert.match(flat(word('archive')), /^◔ attached · waiting for Egeria's next refresh$/);
  assert.match(flat(secondOf(document, 'schema:archive')), /the connector's, not this schema's/);
  assert.match(flat(word('q')), /^◔ queued · outbox #4182$/);
  assert.match(flat(word('plain')), /^✕ failed · 500 Egeria says no$/);
  assert.match(flat(secondOf(document, 'schema:plain')), /step: attach · outbox #7 · will retry/);
  assert.match(flat(word('old')), /^deleted in Egeria · 10-05 09:30$/);
  assert.match(flat(secondOf(document, 'schema:old')), /still lists this schema until its connector restarts · nothing is recreated/);
  assert.match(flat(word('ledger')), /^□ archived in Egeria · 10-05 09:31$/);
  assert.match(flat(secondOf(document, 'schema:ledger')), /re-including creates a new element; the archived record stays/);
});

test('a schema whose attach action was sent reads "sent", between queued and attached (D-C ladder)', async () => {
  const v = committedView();
  v.commit.schemas.q = { state: 'sent', words: 'sent to Egeria · attach action a0008 · waiting for the target',
    second: "Egeria's attach action was started; its target is not in the cataloguer's list yet" };
  const { document } = await setUp(v, previewFor());
  const w = stateOf(document, 'schema:q');
  assert.equal(w.dataset.scopeEgeriaWord, 'sent');
  assert.match(flat(w), /^◔ sent to Egeria · attach action a0008 · waiting for the target$/);
});

test('table rows show their own read-back, follow their schema, and a left-out table says whole schemas', async () => {
  const { document } = await setUp(committedView(), previewFor());
  document.querySelector('[data-scope-toggle="sales"]').click();
  document.querySelector('[data-scope-toggle="archive"]').click();
  assert.match(flat(stateOf(document, 'table:sales.orders')), /^✓ published · read back 10-05 09:12$/);
  assert.match(flat(secondOf(document, 'table:sales.customers')), /^Egeria adds whole schemas · table choices are kept for when it can$/);
  assert.match(flat(stateOf(document, 'table:archive.orders')), /^as its schema: attached waiting$/);
});

test('a row with no state says nothing about Egeria, and a left-out schema says it is the scope record', async () => {
  const { document } = await setUp(baseView(), previewFor());
  assert.equal(stateOf(document, 'schema:plain').dataset.scopeEgeriaWord, 'none');
  assert.equal(flat(stateOf(document, 'schema:plain')), '—');
  assert.equal(stateOf(document, 'schema:old').dataset.scopeEgeriaWord, 'left_out');
  assert.equal(flat(stateOf(document, 'schema:old')), 'left out');
  assert.match(flat(secondOf(document, 'schema:old')), /scope record · never published/);
  assert.equal(stateOf(document, 'schema:old').querySelector('.font-glyph'), null, 'left out has no glyph: it is not a state of Egeria');
});

test('known negative: a server that sends no proof rows gets no state words at all, not remembered ones', async () => {
  const v = committedView();
  delete v.commit;
  const { document } = await setUp(v, previewFor());
  assert.equal(stateOf(document, 'schema:sales').textContent, 'not read yet');
  assert.doesNotMatch(flat(document.querySelector('[data-scope-state-cell]')), /catalogued|attached|queued|failed|removed|archived/);
  assert.match(flat(document.querySelector('[data-scope-commit-header]')), /^Egeria state not read: this server sent no proof rows$/);
});

/* ── the header marker comes from state ────────────────────────────────── */

test('the header marker is whatever the server derived, and changes when the state does', async () => {
  const a = await setUp(baseView(), previewFor());
  assert.equal(flat(a.document.querySelector('[data-scope-commit-header]')), NOT_COMMITTED);
  assert.equal(a.document.querySelector('[data-scope-commit-header]').dataset.scopeCommitHeaderState, 'not_committed');
  const b = await setUp(committedView(), previewFor());
  const h = flat(b.document.querySelector('[data-scope-commit-header]'));
  assert.match(h, /^Database element d0000001 in Egeria · published 10-05 09:00 · 2 schemas chosen: 1 published, 1 attached, waiting/);
  assert.notEqual(h, NOT_COMMITTED);
  assert.equal(b.document.querySelector('[data-scope-commit-header]').dataset.scopeCommitHeaderState, 'committed');
});

test('the old constant markers are gone from the source', () => {
  const src = readFileSync(new URL('../../resource_explorer/web/static/next/stages/curate-scope.js', import.meta.url), 'utf8');
  assert.ok(!src.includes('nothing here is sent to Egeria'), 'the tree header no longer claims nothing is sent');
  assert.ok(!src.includes('Saved in Resource Explorer'), 'the "saved, not cataloged" line is the server\'s, derived, never typed here');
  assert.ok(!/>not read yet</.test(src.replace("'<div data-scope-egeria-state class=\"text-ink-muted\">not read yet</div>'", '')), 'no per-row constant');
});

/* ── pressing Catalogue ────────────────────────────────────────────────── */

test('pressing Catalog posts the refresh choice, shows the record\'s steps as they land, then re-reads the tree from the rows', async () => {
  const advance = (s) => {
    s.view = committedView();      // the server's rows now hold the proof
  };
  const { document, server } = await setUp(baseView(), previewFor(), { advance });
  assert.equal(stateOf(document, 'schema:sales').textContent, 'not committed yet');
  document.querySelector('[data-scope-commit-btn]').click();
  await wait(40);
  const [post] = server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/commit'));
  assert.deepEqual(post.body, { refresh_now: true });
  assert.ok(document.querySelector('[data-scope-commit-step]'), 'the record\'s steps are drawn');
  await wait(300);
  const steps = [...document.querySelectorAll('[data-scope-commit-step]')];
  assert.deepEqual(steps.map((e) => [e.dataset.scopeCommitStep, e.dataset.state]),
    [['publish_elements', 'done'], ['schema_targets', 'done'], ['read_back', 'done']]);
  assert.match(flat(steps[1]), /2 of 2 attached, each with its proof row/);
  // the tree and header were re-read after the run: the words are now the server's rows
  assert.match(flat(stateOf(document, 'schema:sales')), /^✓ published · 2 tables/);
  assert.match(flat(document.querySelector('[data-scope-commit-header]')), /^Database element d0000001 in Egeria/);
  assert.match(flat(document.querySelector('[data-scope-status]')), /^commit cafe0123 done: 3 done$/);
});

test('a failed row reads Egeria\'s first sentence and folds the rest under details (D4)', async () => {
  const v = committedView();
  v.commit.schemas.plain = {
    state: 'failed', words: 'failed · AUTHORIZATION_ERROR_401 => User not authorized received for user - ``.',
    second: 'step: attach · outbox #7 · will retry', details: '* Context: * class name=`BaseServerClient` * caller method=`_async_create_element_from_template`',
  };
  const { document } = await setUp(v, previewFor());
  const row = stateOf(document, 'schema:plain');
  assert.match(flat(row), /^✕ failed · AUTHORIZATION_ERROR_401 => User not authorized received for user - ``\. details/);
  const d = row.querySelector('[data-scope-egeria-details]');
  assert.ok(d, 'the long tail is under a details element');
  assert.equal(d.hasAttribute('open'), false, 'collapsed until asked');
  assert.match(d.textContent, /Context:.*BaseServerClient/);
  // a row with no tail draws no details element
  assert.equal(stateOf(document, 'schema:sales').querySelector('[data-scope-egeria-details]'), null);
});

test('a survey that was only submitted reads submitted, and a step\'s long tail is folded (D3, D4)', async () => {
  const finalSteps = [
    { name: 'schema_targets', state: 'failed', detail: '0 of 1 attached, each with its proof row · plain: AUTHORIZATION_ERROR_401 => not authorized.',
      more: 'plain: * Context: * class name=`BaseServerClient`' },
    { name: 'survey', state: 'submitted', detail: 'submitted · 10-05 12:53 · Egeria\'s survey is limited to your chosen schemas: plain · engine action 6c5100b1' },
    { name: 'refresh', state: 'requested', detail: 'refresh requested · the connector\'s last refresh time did not move on the status read' },
    { name: 'zone_membership', state: 'skipped', detail: 'zones left to Egeria · RE writes no ZoneMembership (EXPLORER_PUBLISH_ZONES is not configured)' },
  ];
  const { document } = await setUp(baseView(), previewFor(), { finalSteps });
  document.querySelector('[data-scope-commit-btn]').click();
  await wait(300);
  const steps = Object.fromEntries([...document.querySelectorAll('[data-scope-commit-step]')].map((e) => [e.dataset.scopeCommitStep, e]));
  assert.equal(steps.survey.dataset.state, 'submitted');
  assert.match(flat(steps.survey), /Egeria survey · submitted/);
  assert.match(flat(steps.survey), /10-05 12:53/);
  assert.match(flat(steps.survey.querySelector('[data-scope-step-hint]')), /^running in Egeria · started 12:53/);
  assert.ok(!/survey · done/.test(flat(steps.survey)), 'initiation is never "done"');
  assert.equal(steps.refresh.dataset.state, 'requested');
  assert.match(flat(steps.zone_membership), /zones left to Egeria/);
  const d = steps.schema_targets.querySelector('[data-scope-step-details]');
  assert.ok(d && !d.hasAttribute('open'));
  assert.ok(!/Context/.test(steps.schema_targets.textContent.replace(d.textContent, '')), 'the row itself carries no Context block');
  assert.match(flat(document.querySelector('[data-scope-status]')), /1 submitted · 1 requested · 1 failed · 1 skipped/);
});

test('a refused commit says why and draws no steps', async () => {
  const { document, server } = await setUp(baseView(), previewFor(), { signedIn: true });
  server.signedIn = false;                          // the session lapsed between the draw and the press
  document.querySelector('[data-scope-commit-btn]').click();
  await wait(60);
  assert.match(flat(document.querySelector('[data-scope-commit-status]')), /^your session expired · sign in again/);
  assert.ok(document.querySelector('[data-scope-commit-status] [data-scope-sign-in]'), 'and offers the sign-in');
  assert.ok(document.querySelector('[data-scope-commit-why]'), 'the button shows the reason');
  assert.equal(document.querySelector('[data-scope-commit-step]'), null);
});

test('Read Egeria again posts the read-back and the status comes from its answer', async () => {
  const { document, server } = await setUp(baseView(), previewFor());
  document.querySelector('[data-scope-read-back]').click();
  await wait(120);
  assert.equal(server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/read-back')).length, 1);
  assert.match(flat(document.querySelector('[data-scope-status]')), /^read back: 1 published · 1 attached, waiting$/);
});

test('a collapsed section reads nothing from Egeria: the preview is read when it is opened', async () => {
  const { document, server } = await setUp(baseView(), previewFor(), { leaveCollapsed: true });
  assert.equal(server.calls.filter((c) => c.url.endsWith('/commit-preview')).length, 0);
  // the marker is still there on the collapsed line, from the server's state
  assert.equal(flat(document.querySelector('[data-scope-saved-marker]')), NOT_COMMITTED);
  document.querySelector('[data-scope-collapse]').click();
  await wait();
  assert.equal(server.calls.filter((c) => c.url.endsWith('/commit-preview')).length, 1);
  assert.ok(document.querySelector('[data-scope-commit-btn]'));
});

/* ── the wording slice: the reserved verb is Catalog; Egeria words only after the read-back ───────── */

test('the commit control is "Catalog", the button without a server label falls back to it, and no UK spelling is on the panel', async () => {
  const { document } = await setUp(baseView(), previewFor({ button: '' }));
  const btn = document.querySelector('[data-scope-commit-btn]');
  assert.equal(flat(btn), 'Publish');
  const panel = flat(document.querySelector('[data-scope-commit-panel]'));
  assert.doesNotMatch(panel.replace(/Egeria's cataloguer/g, '').replace(/refresh Egeria's cataloguer/g, ''), /[Cc]atalogu(e|ed|es|ing)\b/);
});

test('the choice selector says Include | Leave out; "catalog" is on the commit button alone', async () => {
  const { document } = await setUp(baseView());
  const cell = document.querySelector('[data-scope-row="schema:sales"] [data-scope-choice-cell]') || document.querySelector('[data-scope-row="schema:sales"]');
  const words = flat(cell);
  assert.match(words, /Include.*Leave out/);
  assert.doesNotMatch(words, /[Cc]atalog/, 'the word catalog is on the commit button alone');
  const choiceValue = document.querySelector('[data-scope-row="schema:sales"] [data-scope-act="set"][data-scope-choice="catalogue"]');
  assert.ok(choiceValue, 'the API value stays "catalogue": an identifier, not a word on the page');
});

test('"Read Egeria again" reports in the Egeria family: "cataloged" only from the read-back', async () => {
  const { document } = await setUp(committedView(), previewFor());
  document.querySelector('[data-scope-read-back]').click();
  await wait(120);
  assert.match(flat(document.querySelector('[data-scope-status]')), /^read back: 1 published · 1 attached, waiting$/);
});

test('a schema row sent to Egeria says so before the read-back, and cataloged only after it', async () => {
  const v = committedView();
  v.commit.schemas.q = { state: 'sent', words: 'sent to Egeria · attach action a0008 · waiting for the target', second: '' };
  const { document } = await setUp(v, previewFor());
  assert.match(flat(stateOf(document, 'schema:q')), /^◔ sent to Egeria/);
  assert.match(flat(stateOf(document, 'schema:sales')), /^✓ published · 2 tables · read back 10-05 09:12$/);
});
