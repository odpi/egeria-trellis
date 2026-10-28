/** Behaviour: Curate for a DATABASE draws "What gets catalogued" as the first
 *  section of band 2 -- the scope tree, the depth line, proposals, the four
 *  observation states, inheritance, name conflicts and "new since" rows --
 *  and its controls call the right API shapes.
 *  (the designer reply on catalogue scope (page 18 of the CatalogueScope wireframe); slice A, the scope tree.
 *  Editing the scope sends nothing to Egeria. The commit and its per-node states are
 *  curate-catalogue-commit.test.mjs.)
 *
 *  Real app.js, real router, the real Curate nav button. A stateful stub
 *  stands behind fetch so every row shown is a re-read of what the "server"
 *  holds after a write; the known-negative makes it drop a write.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();
/** The choice cell's words without its state mark and its transient 'saved'. */
const words = (el) => { const c = el.cloneNode(true); c.querySelectorAll('[data-scope-selector], [data-scope-saved-note]').forEach((n) => n.remove()); return flat(c); };

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
const person = (choice, by = 'dwolfson', at = '2026-10-04T10:00:00', extra = {}) => ({
  choice, action: 'set', source: 'person', by, at, reason: '', proposal_rule: '', proposal_choice: '',
  measured_at: '', measured: {}, ...extra,
});

function baseView(over = {}) {
  return {
    database: 'adventureworks',
    egeria_element: { guid: 'abcdef12-0', short: 'abcdef12', text: 'abcdef12' },
    survey: { state: 'measured', schema_count: 29, table_count: 266, surveyed_at: '2026-10-04T06:00:00', report_guid: 'g' },
    sources: {
      chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 29, tables: 266, merged: false },
      egeria: { state: 'measured', schema_count: 29, table_count: 266, surveyed_at: '2026-10-04T06:00:00', report_guid: 'g' },
      local: { state: 'not_measured', schema_count: 0, table_count: 0, surveyed_at: '', surveyed_as: '', sees: '' },
      disagree: false, unreadable: 0,
    },
    declared: { declared: false, by: '', at: '', kind: '', baseline_survey_at: '' },
    depth: {
      value: 'schemas_and_tables', declared: false, by: '', at: '',
      help: "Egeria adds whole schemas with their tables and columns, and a shallower depth can't be asked for until Egeria has a depth option (S2), so a depth here changes this tree only.",
      commit_note: 'The commit publishes tables and columns for every chosen schema whatever depth is chosen: Egeria has no depth option yet (S2).',
      options: [
        { id: 'database_only', label: 'the database only', how: 'tree view only: shows the database alone · the commit still publishes whole schemas' },
        { id: 'schemas', label: 'schemas', how: 'tree view only: shows schemas · the commit still publishes their tables and columns' },
        { id: 'schemas_and_tables', label: 'schemas and tables', how: 'tree view only: shows schemas and tables · the commit still publishes their columns' },
        { id: 'tables_and_columns', label: 'tables and columns', how: 'everything: this is what the commit publishes for each chosen schema' },
      ],
    },
    system: { folded: 3, text: 'not published: system schemas are never offered' },
    counts: { schemas_offered: 3, schemas_catalogue: 0, schemas_leave_out: 0, schemas_undecided: 3 },
    new_since: { declared: false, schemas: 0, tables: 0, tables_in_known_schemas: 0, schema_names: [], text: '' },
    // what the server derives from proof rows when nothing has been committed
    commit: { header: { state: 'not_committed', text: 'Saved in Resource Explorer · not yet published to Egeria' },
      database: null, collisions: [], schemas: {}, tables: {} },
    schemas: [
      schema('sales', [table('sales', 'orders'), table('sales', 'customers')]),
      schema('archive', [table('archive', 'orders')]),
      schema('empty_one', [], { classification: 'no_tables', row_total: null }),
    ],
    ...over,
  };
}

const proposalFor = (choice, reason, rule = 'empty_schema') => ({
  rule, choice, reason, measured: { table_count: 0 }, measured_at: '2026-10-02T09:00:00',
});

function makeServer(view, { signedIn = true, dropWrites = false } = {}) {
  const s = { calls: [], view, signedIn, dropWrites };
  const find = (sn, tn) => {
    const sc = s.view.schemas.find((x) => x.name === sn);
    return tn ? sc.tables.find((t) => t.name === tn) : sc;
  };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (u.includes('/api/catalogue-scope/')) {
      if (method === 'GET' && u.endsWith('/commits/latest')) return ok({ commit: null, terminal: null, age_hours: null, stale_unfinished: false, states: {} });
      if (method === 'GET' && u.endsWith('/commit-preview')) return ok(s.preview || { manifest: { lines: [] }, can_commit: false, blockers: ['nothing to commit: choose at least one schema to publish'], button: 'Publish · 0 schemas', leave_out: [], refused: [], collisions: [] });
      if (method === 'GET') return ok(s.view);
      if (!s.signedIn) return err(401, 'Sign in to change the scope');
      if (!s.dropWrites) {
        if (u.endsWith('/depth')) { s.view.depth.value = body.depth; s.view.depth.declared = true; s.view.depth.by = 'me'; s.view.depth.at = '2026-10-04T11:00:00'; }
        if (u.endsWith('/node')) {
          const n = find(body.schema_name, body.table_name);
          n.explicit = person(body.choice, 'me'); n.effective = body.choice; n.state = 'chosen'; n.proposal = null;
        }
        if (u.endsWith('/node/confirm')) {
          const n = find(body.schema_name, body.table_name);
          n.explicit = person(n.proposal.choice, 'me', '2026-10-04T11:00:00', { action: 'confirm', source: n.proposal.rule, reason: n.proposal.reason });
          n.effective = n.proposal.choice; n.state = 'confirmed'; n.proposal = null;
        }
        if (u.endsWith('/node/override')) {
          const n = find(body.schema_name, body.table_name);
          const o = n.proposal; const choice = o.choice === 'leave_out' ? 'catalogue' : 'leave_out';
          n.explicit = person(choice, 'me', '2026-10-04T11:00:00', { action: 'override', proposal_rule: o.rule, proposal_choice: o.choice, reason: o.reason });
          n.effective = choice; n.state = 'overridden'; n.overridden = { rule: o.rule, choice: o.choice, reason: o.reason }; n.proposal = null;
        }
        if (u.endsWith('/nodes')) {
          const names = body.all_schemas ? s.view.schemas.map((x) => x.name) : body.nodes.map((n) => n.schema_name);
          for (const nm of names) {
            const n = find(nm, '');
            if (body.choice) { n.explicit = person(body.choice, 'me'); n.effective = body.choice; n.state = 'chosen'; n.proposal = null; }
            else { n.explicit = null; n.effective = null; n.state = 'undecided'; }
          }
        }
        if (u.endsWith('/node/clear')) {
          const n = find(body.schema_name, body.table_name);
          n.explicit = null; n.effective = null; n.state = 'undecided';
        }
        if (u.endsWith('/redeclare')) { s.view.declared = { declared: true, by: 'me', at: '2026-10-05T09:30:00', kind: 'redeclare', baseline_survey_at: '' }; s.view.new_since = { declared: true, schemas: 0, tables: 0, tables_in_known_schemas: 0, schema_names: [], text: '' }; for (const sc of s.view.schemas) { sc.new_since = false; sc.tables.forEach((t) => { t.new_since = false; }); } }
      }
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

async function setUp(view, opts = {}) {
  const { document, window } = makeDomEnvironment();
  const signedIn = opts.signedIn !== false;
  const server = makeServer(view, { signedIn, dropWrites: !!opts.dropWrites });
  ensureLoaderRegistered();
  (await import('/static/next/stages/curate-scope.js')).resetScopeUi();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'understanding';
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  app.state.me = signedIn ? { user_id: 'me' } : null;
  app.state.groups = [];
  app.state.databasesLoaded = true;
  app.state.filesystemsLoaded = true;
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
  return { document, window, app, server };
}

const scopeEl = (d) => d.querySelector('[data-curate-scope]');
const row = (d, key) => d.querySelector(`[data-scope-row="${key}"]`);
const calls = (server, method, frag) => server.calls.filter((c) => c.method === method && c.url.includes(frag));

/* ── the section, its header, the element ──────────────────────────────── */

test('the scope is on Contents; the two waiting sections and the dependencies line are on Structure', async () => {
  const { document } = await setUp(baseView());
  const contents = document.querySelector('[data-curate-tab-panel="contents"]');
  const structure = document.querySelector('[data-curate-tab-panel="structure"]');
  assert.deepEqual([...contents.querySelectorAll('[data-curate-work]')].map((e) => e.dataset.curateWork), ['scope']);
  assert.deepEqual([...structure.querySelectorAll('[data-curate-work]')].map((e) => e.dataset.curateWork), ['glossary', 'schema-match', 'dependencies']);
  assert.match(flat(contents.querySelector('[data-curate-work="scope"]')), /^What gets published/);
  assert.match(flat(structure.querySelector('[data-curate-work="glossary"]')), /and the tables to be published first \(on Contents\)/);
  assert.match(flat(structure.querySelector('[data-curate-work="schema-match"]')), /and the tables to be published first \(on Contents\)/);
  assert.equal(document.querySelectorAll('#curate-host table').length, 0, 'never a <table>');
});

test('header, undeclared, Egeria the source: "29 schemas · Egeria survey 10-04"', async () => {
  const { document } = await setUp(baseView());
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    'Your scope: none declared yet · 29 schemas · Egeria survey 10-04');
  assert.match(flat(document.querySelector('[data-scope-tree-header]')), /reads Egeria element abcdef12/);
  assert.match(flat(document.querySelector('[data-scope-tree-header]')), /tree read from the Egeria survey 10-04: 29 schemas, 266 tables/);
});

test('header, RE local survey the only source: names the source, its date and what the credential sees', async () => {
  const { document } = await setUp(baseView({ sources: {
    chosen: { kind: 'local', as_of: '2026-10-03T09:00:00', schemas: 8, tables: 61, merged: false },
    egeria: { state: 'not_measured', schema_count: null, table_count: null, surveyed_at: '', report_guid: '' },
    local: { state: 'measured', schema_count: 8, table_count: 61, surveyed_at: '2026-10-03T09:00:00', surveyed_as: 'surveyor', sees: 'sees 6' },
    disagree: false, unreadable: 0 } }));
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    'Your scope: none declared yet · 8 schemas · RE local survey 10-03 · sees 6 of 8');
});

test('header, the two sources DISAGREE: it names both, with their counts and dates', async () => {
  const { document } = await setUp(baseView({ sources: {
    chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 29, tables: 266, merged: true },
    egeria: { state: 'measured', schema_count: 29, table_count: 266, surveyed_at: '2026-10-04T06:00:00', report_guid: 'g' },
    local: { state: 'measured', schema_count: 8, table_count: 61, surveyed_at: '2026-10-03T09:00:00', surveyed_as: 'surveyor', sees: 'sees 6' },
    disagree: true, unreadable: 0 } }));
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    "Your scope: none declared yet · Egeria's latest survey covers 29 schemas, 266 tables · RE's own survey saw 8 schemas, 61 tables, 10-03");
  assert.match(flat(document.querySelector('[data-scope-tree-header]')), /facts it lacked filled from the other survey/);
});

test('header, undeclared and never measured: says not measured yet, and an uncatalogued database says so', async () => {
  const { document } = await setUp(baseView({
    survey: { state: 'not_measured', schema_count: null, table_count: null, surveyed_at: '', report_guid: '' },
    sources: { chosen: { kind: 'local', as_of: '', schemas: 0, tables: 0, merged: false },
      egeria: { state: 'not_measured' }, local: { state: 'not_measured' }, disagree: false, unreadable: 0 },
    egeria_element: { guid: '', short: '', text: 'not published to Egeria' },
  }));
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    "Your scope: none declared yet · Egeria's latest survey: not measured yet");
  assert.match(flat(document.querySelector('[data-scope-tree-header]')), /not published to Egeria/);
});

const declaredView = () => baseView({
  declared: { declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', kind: 'first', baseline_survey_at: '' },
  counts: { schemas_offered: 29, schemas_catalogue: 7, schemas_leave_out: 0, schemas_undecided: 22 },
});

test('header, declared: collapsed by default to one line of essentials, a real button; the marker stays', async () => {
  const { document } = await setUp(declaredView());
  const btn = document.querySelector('[data-scope-collapse]');
  assert.equal(btn.tagName, 'BUTTON');
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    "Your scope: 7 of 29 schemas known to RE · declared by dwolfson 10-04 · Egeria's latest survey covers 29 schemas, 266 tables");
  assert.equal(document.querySelector('[data-scope-body]').hidden, true);
  // the marker is the server's derived state (curate-catalogue-commit.test.mjs covers its changes)
  assert.equal(flat(document.querySelector('[data-scope-saved-marker]')),
    'Saved in Resource Explorer · not yet published to Egeria');
});

test('header, declared: a click expands to the whole scope and the header is the full sentence', async () => {
  const { document } = await setUp(declaredView());
  document.querySelector('[data-scope-collapse]').click();
  assert.equal(document.querySelector('[data-scope-collapse]').getAttribute('aria-expanded'), 'true');
  assert.equal(document.querySelector('[data-scope-body]').hidden, false);
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    'Your scope: 7 of 29 schemas · declared by dwolfson 10-04 · 29 schemas · Egeria survey 10-04');
});

test('header, undeclared: open, and the disclosure glyph is outside the header sentence', async () => {
  const { document } = await setUp(baseView());
  assert.equal(document.querySelector('[data-scope-collapse]').getAttribute('aria-expanded'), 'true');
  assert.equal(document.querySelector('[data-scope-body]').hidden, false);
  assert.equal(document.querySelector('[data-scope-collapse] [aria-hidden="true"]').textContent, '▾');
});

/* ── depth ─────────────────────────────────────────────────────────────── */

test('depth line: four depths, each honest that a depth is a tree view and the commit does whole schemas', async () => {
  const { document, window, server } = await setUp(baseView());
  const radios = [...document.querySelectorAll('[data-scope-depth-radio]')];
  assert.deepEqual(radios.map((r) => r.dataset.scopeDepthRadio), ['database_only', 'schemas', 'schemas_and_tables', 'tables_and_columns']);
  assert.equal(radios.find((r) => r.checked).dataset.scopeDepthRadio, 'schemas_and_tables');
  assert.match(flat(document.querySelector('[data-scope-depth]')), /the database only.*schemas.*schemas and tables.*tables and columns/);
  assert.match(flat(document.querySelector('[data-scope-depth-how]')), /the commit still publishes their columns/);
  assert.match(flat(document.querySelector('[data-scope-depth-help]')), /changes this tree only/);
  assert.match(flat(document.querySelector('[data-scope-depth-commit-note]')), /whatever depth is chosen: Egeria has no depth option yet \(S2\)/);
  // the Catalogue button lives in its own panel under the tree, never inside it
  assert.equal(document.querySelector('[data-scope-tree] [data-scope-commit-btn]'), null);
  const r = radios.find((x) => x.dataset.scopeDepthRadio === 'schemas');
  r.checked = true;
  r.dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait();
  const [c] = calls(server, 'PUT', '/depth');
  assert.deepEqual(c.body, { depth: 'schemas' });
  assert.match(flat(document.querySelector('[data-scope-status]')), /depth is now schemas/);
  assert.match(flat(document.querySelector('[data-scope-depth-how]')), /chosen by me 10-04/);
});

test('column depth: column rows appear under an opened table', async () => {
  const withCols = baseView();
  withCols.depth.value = 'tables_and_columns';
  withCols.schemas[0].tables[0].columns = [{ name: 'order_id', type: 'integer', key_role: 'PK' }];
  const { document } = await setUp(withCols);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.match(flat(document.querySelector('[data-scope-column]')), /order_id integer PK/);
});

test('shallower depth: no column rows, and a table says which level the depth excludes', async () => {
  const shallow = baseView();
  shallow.schemas[0].tables.forEach((t) => { t.provenance = 'columns hidden in this view · the commit still publishes them'; });
  const { document } = await setUp(shallow);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(document.querySelector('[data-scope-column]'), null);
  assert.match(flat(row(document, 'table:sales.orders')), /columns hidden in this view · the commit still publishes them/);
});

/* ── proposals ─────────────────────────────────────────────────────────── */

function withProposal() {
  const v = baseView();
  const e = v.schemas[2];
  e.state = 'proposed';
  e.proposal = proposalFor('leave_out', '0 tables, measured 10-02');
  e.live_proposal = e.proposal;
  return v;
}

test('a proposal row: the proposal glyph and word, its reason, confirm and the other choice; effective stays undecided', async () => {
  const { document } = await setUp(withProposal());
  const r = row(document, 'schema:empty_one');
  assert.equal(words(r.querySelector('[data-scope-choice-cell]')),
    '⏵ proposed: leave out · 0 tables, measured 10-02 confirm · include instead');
  assert.equal(r.dataset.scopeEffective, '', 'an unconfirmed proposal is still undecided');
  assert.ok(r.querySelector('[aria-label="proposal"]'), 'the glyph carries the existing word');
});

test('confirm POSTs the node and the row reads confirmed from the re-read', async () => {
  const { document, server } = await setUp(withProposal());
  row(document, 'schema:empty_one').querySelector('[data-scope-act="confirm"]').click();
  await wait();
  const [c] = calls(server, 'POST', '/node/confirm');
  assert.deepEqual(c.body, { schema_name: 'empty_one', table_name: '' });
  assert.match(words(row(document, 'schema:empty_one').querySelector('[data-scope-choice-cell]')), /leave out · confirmed by me 10-04/);
  assert.match(flat(document.querySelector('[data-scope-status]')), /empty_one: leave out is in the re-read scope/);
});

test('override POSTs the node; the row shows the other choice and the proposal reason struck through', async () => {
  const { document, server } = await setUp(withProposal());
  row(document, 'schema:empty_one').querySelector('[data-scope-act="override"]').click();
  await wait();
  assert.equal(calls(server, 'POST', '/node/override').length, 1);
  const cell = row(document, 'schema:empty_one').querySelector('[data-scope-choice-cell]');
  assert.match(words(cell), /include · overridden by me 10-04/);
  const struck = cell.querySelector('[data-scope-struck]');
  assert.ok(struck && struck.className.includes('line-through'));
  assert.match(flat(struck), /proposed: leave out · 0 tables, measured 10-02/);
});

test('survey now disagrees: both values and the survey date, and the choice does not move', async () => {
  const v = baseView();
  const e = v.schemas[2];
  e.explicit = person('leave_out', 'dwolfson', '2026-10-04T10:00:00', { action: 'confirm', source: 'empty_schema', proposal_rule: 'empty_schema' });
  e.effective = 'leave_out'; e.state = 'disagrees';
  e.disagrees = { was: { table_count: 0 }, now: { table_count: 4 }, survey_at: '2026-10-09T00:00:00', words: 'it had 0 tables; it now has 4' };
  const { document } = await setUp(v);
  const cell = row(document, 'schema:empty_one').querySelector('[data-scope-choice-cell]');
  assert.match(flat(cell.querySelector('[data-scope-disagrees]')),
    /survey now disagrees: Confirmed leave out on 10-04, when it had 0 tables; it now has 4 \(survey of 10-09\)\. The choice has not changed\./);
  assert.match(words(cell), /leave out · set by dwolfson 10-04/);
  assert.ok(cell.querySelector('[data-scope-act="clear"]'), 'offers to reconsider');
});

/* ── undecided and inheritance ─────────────────────────────────────────── */

test('an undecided schema says it keeps what is in Egeria now', async () => {
  const { document } = await setUp(baseView());
  assert.match(flat(row(document, 'schema:sales')), /undecided · keeps what’s in Egeria now/);
});

test('inherited and differing tables are drawn differently', async () => {
  const v = baseView();
  const s = v.schemas[0];
  s.explicit = person('catalogue'); s.effective = 'catalogue'; s.state = 'chosen'; s.effective_from = 'self';
  s.tables[0].effective = 'catalogue'; s.tables[0].effective_from = 'schema'; s.tables[0].inherited = 'catalogue';
  s.tables[1].explicit = person('leave_out'); s.tables[1].effective = 'leave_out'; s.tables[1].effective_from = 'self';
  s.tables[1].state = 'chosen'; s.tables[1].differs_from_schema = true;
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  const inherited = row(document, 'table:sales.orders').querySelector('[data-scope-state-word="inherited"]');
  assert.match(flat(inherited), /^\(schema\) details include · from its schema$/);
  assert.ok(inherited.className.includes('text-ink-muted'), 'muted ink');
  const differs = row(document, 'table:sales.customers').querySelector('[data-scope-choice-cell]');
  assert.match(flat(differs), /leave out · set by dwolfson 10-04 · differs from its schema/);
  assert.ok(differs.querySelector('[data-scope-tag="differs"]'), 'a bordered tag, not a sentence');
});

/* ── no conflicts: a table name chosen two ways is not something to resolve ───── */

test('same-named tables chosen differently draw no banner, no "needs a person" line, and force no tree open', async () => {
  // An older server may still send the retired fields: the pane must not draw them.
  const v = baseView();
  const text = 'orders is chosen differently in sales and archive';
  const conflict = { name: 'orders', text, catalogue_in: ['sales'], leave_out_in: ['archive'] };
  v.schemas[0].tables[0].explicit = person('catalogue'); v.schemas[0].tables[0].effective = 'catalogue'; v.schemas[0].tables[0].conflict = conflict;
  v.schemas[1].tables[0].explicit = person('leave_out'); v.schemas[1].tables[0].effective = 'leave_out'; v.schemas[1].tables[0].conflict = conflict;
  v.conflicts = { count: 1, names: ['orders'], pairs: [{ name: 'orders', catalogue_in: 'sales', leave_out_in: 'archive' }] };
  const { document } = await setUp(v);
  assert.equal(document.querySelector('[data-scope-conflict-summary]'), null);
  assert.equal(document.querySelector('[data-scope-conflict]'), null);
  assert.equal(document.querySelector('[data-scope-resolve]'), null);
  assert.ok(!/needs a person|can't express|catalogue both|leave both out/.test(flat(scopeEl(document))), 'no conflict words anywhere');
  assert.equal(row(document, 'table:sales.orders'), null, 'no tree is forced open');
  assert.equal(row(document, 'table:archive.orders'), null, 'no tree is forced open');
  assert.equal(document.querySelectorAll('[data-scope-toggle][aria-expanded="true"]').length, 0);
});

/* ── new since ─────────────────────────────────────────────────────────── */

test('new since the scope was declared: rows say so, the count line quotes the manifest, declaring again resets it', async () => {
  const v = baseView({
    declared: { declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', kind: 'first', baseline_survey_at: '' },
    new_since: { declared: true, schemas: 1, tables: 3, tables_in_known_schemas: 0, schema_names: ['coco_new'],
      text: '1 new schema (3 tables) not in your scope', since: '2026-10-04T08:00:00' },
  });
  v.schemas.push(schema('coco_new', [table('coco_new', 'a')], { new_since: true }));
  v.schemas[3].tables[0].new_since = true;
  const { document, server } = await setUp(v);
  assert.match(flat(document.querySelector('[data-scope-new-since]')), /1 new schema \(3 tables\) not in your scope/);
  assert.match(flat(row(document, 'schema:coco_new')), /new since your scope was declared · undecided/);
  assert.equal(row(document, 'schema:sales').querySelector('[data-scope-new-since-row]'), null);
  const again = document.querySelector('[data-scope-redeclare]');
  assert.equal(flat(again), 'Start a new baseline', 'it saves; it does not redeclare the choices');
  assert.doesNotMatch(flat(document.querySelector('[data-scope-new-since]')), /declare the scope again/);
  again.click();
  await wait();
  assert.equal(calls(server, 'POST', '/redeclare').length, 1);
  assert.equal(document.querySelector('[data-scope-new-since]'), null);
  assert.match(flat(document.querySelector('[data-scope-status]')), /^saved · me · 10-05 09:30 · new baseline: nothing is new since now$/);
});

/* ── marks, notes, access, system ──────────────────────────────────────── */

test('PII is a mark, staging a note, no access is "not established": none of them is a proposal', async () => {
  const v = baseView();
  v.schemas[0].marks = ['PII · 3 columns'];
  v.schemas[1].notes = ['name suggests staging'];
  v.schemas[2] = schema('locked', [], { classification: 'no_access', access: 'not_established', table_count: null, row_total: null });
  const { document } = await setUp(v);
  assert.match(flat(row(document, 'schema:sales').querySelector('[data-scope-classes-cell]')), /PII · 3 columns/);
  assert.match(flat(row(document, 'schema:archive').querySelector('[data-scope-state-cell]')), /name suggests staging/);
  assert.match(flat(row(document, 'schema:locked').querySelector('[data-scope-state-cell]')), /\? not established/);
  assert.equal(scopeEl(document).querySelectorAll('[data-scope-act="confirm"]').length, 0, 'no proposal anywhere');
  assert.match(flat(row(document, 'schema:locked').querySelector('[data-scope-rows-cell]')), /not established/);
});

test('system schemas are folded with their sentence and never offered a control', async () => {
  const { document } = await setUp(baseView());
  const sys = document.querySelector('[data-scope-system]');
  assert.match(flat(sys), /3 system schemas folded · not published: system schemas are never offered/);
  assert.equal(sys.querySelector('button'), null);
  assert.equal(document.querySelector('[data-scope-row*="pg_catalog"]'), null);
});

test('a database with no stored rows says so in words, not an empty table', async () => {
  const { document } = await setUp(baseView({ schemas: [], system: null }));
  assert.match(flat(document.querySelector('[data-scope-tree]')), /No stored schema rows yet/);
  assert.equal(scopeEl(document).querySelectorAll('table').length, 0);
});

/* ── signed out, and a server that drops the write ─────────────────────── */

test('signed out: the tree reads, every control is disabled with the reason, and no write is attempted', async () => {
  const { document, server } = await setUp(withProposal(), { signedIn: false });
  assert.match(flat(document.querySelector('[data-scope-signed-out]')), /You can read the scope as it stands\. sign in to change what gets published/);
  const controls = [...scopeEl(document).querySelectorAll('button[data-scope-act], input[data-scope-depth-radio]')];
  assert.ok(controls.length > 4);
  for (const c of controls) {
    assert.ok(c.disabled, 'control is disabled');
    assert.match(c.title, /sign in to change what gets published/);
  }
  row(document, 'schema:empty_one').querySelector('[data-scope-act="confirm"]').click();
  await wait(50);
  assert.equal(server.calls.filter((c) => c.method !== 'GET').length, 0);
  assert.match(flat(row(document, 'schema:empty_one')), /proposed: leave out/);
});

test('KNOWN-NEGATIVE: a server that drops the write does not get a "saved" status', async () => {
  const { document } = await setUp(baseView(), { dropWrites: true });
  row(document, 'schema:sales').querySelector('[data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait();
  const s = flat(document.querySelector('[data-scope-status]'));
  assert.match(s, /the write returned, but the re-read scope shows no choice for sales/);
  assert.doesNotMatch(s, /is in the re-read scope/);
  assert.match(flat(row(document, 'schema:sales')), /undecided/);
});

test('a 401 from a write says to sign in, in words', async () => {
  const { document, server } = await setUp(baseView());
  server.signedIn = false;
  row(document, 'schema:sales').querySelector('[data-scope-act="set"][data-scope-choice="leave_out"]').click();
  await wait();
  assert.match(flat(document.querySelector('[data-scope-status]')), /your session expired · sign in again/);
});

/* ── slice A2: sources on every row, activity, select-all and bulk, layout ── */

test('every row carries a muted source line, and a fact another survey supplied says where it came from', async () => {
  const v = baseView();
  v.schemas[0].tables[0].facts_from = { rows: { kind: 'local', as_of: '2026-10-03T09:00:00', text: '' } };
  v.schemas[1].source = { kind: 'local', as_of: '2026-10-03T09:00:00', text: 'from RE local survey 10-03' };
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  const src = (key) => row(document, key).querySelector('[data-scope-source]');
  assert.equal(flat(src('schema:sales')), 'from Egeria survey 10-04');
  assert.equal(flat(src('schema:archive')), 'from RE local survey 10-03');
  assert.equal(flat(src('table:sales.orders')), 'from Egeria survey 10-04 · rows from RE local survey 10-03');
  assert.equal(flat(src('table:sales.customers')), 'from Egeria survey 10-04');
  for (const key of ['schema:sales', 'schema:archive', 'table:sales.orders']) {
    assert.ok(src(key).className.includes('text-ink-muted'), `${key}: the source line is muted ink`);
  }
});

test('activity is a word with its window: active, dormant, or can\'t tell with the reason; never "none since"', async () => {
  const v = baseView();
  v.schemas[0].tables[0].last_write = { state: 'active', writes: 1204, text: 'active · 1,204 writes since counters reset 06-02' };
  v.schemas[0].tables[1].last_write = { state: 'cant_tell', reason: '2 days of evidence', text: "can't tell · counters reset 10-03 · 2 days of evidence" };
  v.schemas[1].last_write = { state: 'dormant', writes: 0, window_days: 336, text: 'dormant · 0 writes in 336 days (counters reset 2025-11-02)' };
  v.schemas[2].last_write = { state: 'cant_tell', reason: 'reset date not recorded', text: "can't tell · reset date not recorded" };
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  const act = (key) => flat(row(document, key).querySelector('[data-scope-lastwrite-cell]'));
  // The cell is short; the whole sentence rides in its title, so nothing is lost.
  assert.equal(act('table:sales.orders'), 'active · 1,204 writes');
  assert.equal(act('table:sales.customers'), "can't tell");
  assert.equal(act('schema:archive'), 'dormant · 0 writes in 336 days');
  assert.equal(act('schema:empty_one'), "can't tell");
  const tip = (key) => row(document, key).querySelector('[data-scope-lastwrite-cell] [title]').title;
  assert.equal(tip('table:sales.orders'), 'active · 1,204 writes since counters reset 06-02');
  assert.equal(tip('table:sales.customers'), "can't tell · counters reset 10-03 · 2 days of evidence");
  assert.equal(tip('schema:archive'), 'dormant · 0 writes in 336 days (counters reset 2025-11-02)');
  assert.equal(tip('schema:empty_one'), "can't tell · reset date not recorded");
  assert.ok(row(document, 'table:sales.customers').querySelector('[data-scope-activity-cant-tell]').className.includes('text-ink-muted'));
  assert.doesNotMatch(flat(scopeEl(document)), /none since|no writes since/);
  assert.equal(scopeEl(document).querySelectorAll('[data-scope-act="confirm"]').length, 0, 'a can\'t tell proposes nothing');
  assert.match(flat(document.querySelector('[data-scope-activity-head]')), /^Activity$/);
  assert.match(document.querySelector('[data-scope-activity-head]').title, /0 writes in at least 90 days/);
});

test('columns, left to right: choice, Schema / table, Rows, Size, Activity, Classification, In Egeria (full names in the titles)', async () => {
  const { document } = await setUp(baseView());
  const headEls = [...document.querySelector('[data-scope-tree-head]').children].filter((c) => flat(c));
  assert.deepEqual(headEls.map(flat), ['Include in Egeria?', 'Schema / table', 'Rows', 'Size', 'Activity', 'Classification', 'In Egeria']);
  // every shortened header keeps its meaning on hover
  const byText = Object.fromEntries(headEls.map((c) => [flat(c), c.title]));
  assert.match(byText.Rows, /Row count/);
  assert.match(byText.Size, /Size on disk/);
  assert.match(byText.Activity, /dormant means 0 writes in at least 90 days/);
  assert.match(byText.Classification, /Data classes found in the columns/);
  assert.equal(byText['In Egeria'], 'State in Egeria');
  const cells = [...row(document, 'schema:sales').children].map((c) => Object.keys(c.dataset)[0]);
  assert.deepEqual(cells, ['scopeSelectCell', 'scopeChoiceCell', 'scopeNameCell', 'scopeRowsCell', 'scopeSizeCell', 'scopeLastwriteCell', 'scopeClassesCell', 'scopeStateCell']);
  assert.match(flat(row(document, 'schema:sales').querySelector('[data-scope-name-cell]')), /sales · 2 tables/);
});

test('rows and size: a scan, an estimate (≈), not measured, and "◐ sources disagree" with both values', async () => {
  const v = baseView();
  const [orders, customers] = v.schemas[0].tables;
  orders.rows_view = { state: 'measured', value: 3412, text: '3,412', detail: 'RE local survey 10-14 · scan' };
  customers.rows_view = { state: 'estimate', value: 3400, estimate: true, text: '≈3,400', detail: 'RE local survey 10-14 · estimate' };
  v.schemas[1].tables[0].rows_view = { state: 'disagree', value: null, text: '◐ sources disagree', detail: 'RE local survey 10-02: ≈3,400 · Egeria survey 10-04: 0' };
  v.schemas[2].rows_view = { state: 'not_measured', value: null, text: 'not measured', detail: '' };
  orders.size_view = { state: 'measured', value: 1, text: '22 MB', detail: 'Egeria survey 10-04' };
  customers.size_view = { state: 'not_measured', value: null, text: 'not measured', detail: '' };
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  document.querySelector('[data-scope-toggle="archive"]').click();
  const cell = (key, which) => row(document, key).querySelector(`[data-scope-${which}-cell]`);
  assert.equal(flat(cell('table:sales.orders', 'rows')), '3,412');
  assert.equal(flat(cell('table:sales.customers', 'rows')), '≈3,400');
  assert.match(cell('table:sales.customers', 'rows').querySelector('[title]').title, /estimate/);
  assert.match(flat(cell('table:archive.orders', 'rows')), /^◐ sources disagree RE local survey 10-02: ≈3,400 · Egeria survey 10-04: 0$/);
  assert.equal(flat(cell('schema:empty_one', 'rows')), 'not measured');
  assert.equal(flat(cell('table:sales.orders', 'size')), '22 MB');
  assert.equal(flat(cell('table:sales.customers', 'size')), 'not measured');
  assert.ok(cell('table:sales.orders', 'rows').querySelector('.tnum.text-right, .tnum'), 'tabular figures');
});

test('a lens term found in table names is a muted suggestion sentence, not a proposal and not a rule control', async () => {
  const v = baseView({ suggested_rules: [{ term: 'Sales', count: 14, text: 'The lens names Sales: 14 table names contain it · make that a scope rule?' }] });
  const { document } = await setUp(v);
  const line = document.querySelector('[data-scope-suggested-rule]');
  assert.equal(flat(line), 'The lens names Sales: 14 table names contain it · make that a scope rule?');
  assert.ok(line.className.includes('text-ink-muted'));
  assert.equal(line.querySelector('button'), null, 'rules are a later slice: no control');
  assert.equal(scopeEl(document).querySelectorAll('[data-scope-act="confirm"]').length, 0);
});

test('the column title is "Schema / table", tables are indented under their schema, and each says its kind', async () => {
  const { document } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  const head = flat(document.querySelector('[data-scope-tree-head]'));
  assert.match(head, /Schema \/ table/);
  assert.doesNotMatch(head, /(^|\s)name(\s|$)/i);
  const t = row(document, 'table:sales.orders');
  assert.ok(t.parentElement.className.includes('ml-s3'), 'the table row is indented');
  assert.match(flat(t.querySelector('[data-scope-kind]')), /^· table$/);
  assert.ok(row(document, 'schema:sales').querySelector('[data-scope-toggle]'), 'a schema row has its expander');
});

test('the STATE column is fully present and the tree scrolls sideways inside its own container', async () => {
  const { document } = await setUp(baseView());
  const host = document.querySelector('[data-scope-tree]');
  assert.equal(host.style.overflowX, 'auto', 'overflow-x is set on the container');
  assert.ok(host.className.includes('overflow-x-auto'));
  assert.equal(flat(document.querySelector('[data-scope-state-head]')), 'In Egeria');
  // A2.1: max-content let one long sentence widen every row and forced a scroll at any width.
  // The floor is a fixed one, so at about 1300px all seven columns fit and only a narrower
  // pane scrolls. (jsdom does no layout: the fit itself is reasoned from these widths, not measured.)
  assert.equal(host.querySelector('.min-w-max'), null, 'no max-content floor');
  // the floor is now the sum of the column widths (+ the name floor), carried on --rc-total
  assert.match(host.querySelector('[data-rc-inner]').getAttribute('style'), /^min-width:var\(--rc-total,\d+px\);padding-right:8px$/, 'a fixed floor instead');
  assert.ok(row(document, 'schema:sales').querySelector('[data-scope-state-cell]'));
  // jsdom does no layout: whether STATE is fully visible at ~1300px is NOT measured here.
});

test('select all schemas ticks every schema row, and the bulk bar counts them', async () => {
  const { document, window } = await setUp(baseView());
  const bar = document.querySelector('[data-scope-bulk]');
  assert.match(flat(bar), /select all shown/);
  assert.match(flat(bar), /0 of 3 schemas/);
  assert.ok(document.querySelector('[data-scope-bulk-act="catalogue"]').disabled, 'nothing selected: nothing to apply');
  const all = document.querySelector('[data-scope-all-box]');
  all.checked = true;
  all.dispatchEvent(new window.Event('change', { bubbles: true }));
  const boxes = [...document.querySelectorAll('[data-scope-select]')];
  assert.equal(boxes.length, 3);
  assert.ok(boxes.every((b) => b.checked));
  assert.match(flat(document.querySelector('[data-scope-bulk]')), /3 of 3 schemas/);
  assert.equal(document.querySelector('[data-scope-bulk-act="leave_out"]').disabled, false);
  assert.equal(row(document, 'table:sales.orders'), null, 'tables are collapsed: no table row (and no table tick) is drawn');
});

test('catalog selected: one POST naming the ticked schemas, and the status is derived from the re-read', async () => {
  const { document, window, server } = await setUp(baseView());
  for (const n of ['sales', 'archive']) {
    const b = document.querySelector(`[data-scope-select="${n}"]`);
    b.checked = true;
    b.dispatchEvent(new window.Event('change', { bubbles: true }));
  }
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /2 of 3 schemas/);
  document.querySelector('[data-scope-bulk-act="catalogue"]').click();
  await wait();
  const [c] = calls(server, 'POST', '/nodes');
  assert.deepEqual(c.body, { nodes: [{ schema_name: 'sales', table_name: '' }, { schema_name: 'archive', table_name: '' }], choice: 'catalogue', all_schemas: false });
  assert.match(flat(document.querySelector('[data-scope-status]')), /^2 schemas now set to include by me$/);
  assert.match(flat(row(document, 'schema:sales').querySelector('[data-scope-choice-cell]')), /include · set by me/);
  assert.match(flat(document.querySelector('[data-scope-selected-count]')), /0 of 3 schemas/, 'the selection is spent');
});

test('leave out selected and clear choice send their own choice', async () => {
  const { document, window, server } = await setUp(baseView());
  const tick = (n) => { const b = document.querySelector(`[data-scope-select="${n}"]`); b.checked = true; b.dispatchEvent(new window.Event('change', { bubbles: true })); };
  tick('sales');
  document.querySelector('[data-scope-bulk-act="leave_out"]').click();
  await wait();
  tick('sales');
  document.querySelector('[data-scope-bulk-act="clear"]').click();
  await wait();
  const posts = calls(server, 'POST', '/nodes');
  assert.deepEqual(posts.map((p) => p.body.choice), ['leave_out', '']);
  assert.match(flat(document.querySelector('[data-scope-status]')), /^1 schema now has no choice in the re-read scope$/);
});

test('include all N schemas: one click, one POST asking for all of them, "3 schemas now set to include by me"', async () => {
  const { document, server } = await setUp(baseView());
  const btn = document.querySelector('[data-scope-catalogue-all]');
  assert.equal(flat(btn), 'include all 3 schemas');
  btn.click();
  await wait();
  const [c] = calls(server, 'POST', '/nodes');
  assert.equal(c.body.all_schemas, true);
  assert.equal(c.body.choice, 'catalogue');
  assert.match(flat(document.querySelector('[data-scope-status]')), /^3 schemas now set to include by me$/);
});

test('bulk: a table with its own differing choice is reported from the re-read, not overwritten', async () => {
  const v = baseView();
  v.schemas[0].tables[0].explicit = person('catalogue'); v.schemas[0].tables[0].effective = 'catalogue';
  v.schemas[0].tables[0].differs_from_schema = true; v.schemas[0].tables[0].state = 'chosen';
  const { document, window } = await setUp(v);
  const b = document.querySelector('[data-scope-select="sales"]');
  b.checked = true; b.dispatchEvent(new window.Event('change', { bubbles: true }));
  document.querySelector('[data-scope-bulk-act="leave_out"]').click();
  await wait();
  assert.match(flat(document.querySelector('[data-scope-status]')), /^1 schema now set to leave out by me · 1 table keeps its own choice and differs from its schema$/);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.match(flat(row(document, 'table:sales.orders').querySelector('[data-scope-choice-cell]')), /include · set by dwolfson 10-04 · differs from its schema/);
});

test('bulk, signed out: every bulk control is disabled with the reason and nothing is sent', async () => {
  const { document, server } = await setUp(baseView(), { signedIn: false });
  const controls = [...document.querySelectorAll('[data-scope-all-box], [data-scope-select], [data-scope-bulk-act], [data-scope-catalogue-all]')];
  assert.ok(controls.length >= 7);
  for (const c of controls) {
    assert.ok(c.disabled, 'disabled');
    assert.match(c.title, /sign in to change what gets published/);
  }
  document.querySelector('[data-scope-catalogue-all]').click();
  await wait(50);
  assert.equal(server.calls.filter((x) => x.method !== 'GET').length, 0);
});

test('KNOWN-NEGATIVE: a bulk write the server drops is not reported as done', async () => {
  const { document, server } = await setUp(baseView(), { dropWrites: true });
  document.querySelector('[data-scope-catalogue-all]').click();
  await wait();
  assert.equal(calls(server, 'POST', '/nodes').length, 1);
  const s = flat(document.querySelector('[data-scope-status]'));
  assert.match(s, /the write returned, but the re-read scope shows only 0 of 3 schemas set to include/);
  assert.doesNotMatch(s, /now set to/);
});

test('a failed read of RE\'s own survey says so instead of "not measured"', async () => {
  const lo = { state: 'unreadable', read_error: 'RuntimeError: x', schema_count: 0, table_count: 0, surveyed_at: '', surveyed_as: '', sees: '' };
  const { document } = await setUp(baseView({ sources: {
    chosen: { kind: 'local', as_of: '', schemas: 0, tables: 0, merged: false },
    egeria: { state: 'not_measured' }, local: lo, disagree: false, unreadable: 0 } }));
  assert.equal(flat(document.querySelector('[data-scope-header]')),
    "Your scope: none declared yet · RE's own survey could not be read");
  assert.doesNotMatch(flat(document.querySelector('[data-scope-header]')), /not measured/);
  const { document: d2 } = await setUp(baseView({ sources: {
    chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 29, tables: 266, merged: false },
    egeria: { state: 'measured', schema_count: 29, table_count: 266, surveyed_at: '2026-10-04T06:00:00' }, local: lo, disagree: false, unreadable: 0 } }));
  assert.equal(flat(d2.querySelector('[data-scope-header]')),
    "Your scope: none declared yet · 29 schemas · Egeria survey 10-04 · RE's own survey could not be read");
});

test('when the earlier surveys could not be read, new-since says can\'t tell and no row is flagged new', async () => {
  const v = baseView({
    declared: { declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', kind: 'first', baseline_survey_at: '' },
    new_since: { declared: true, can_tell: false, schemas: 0, tables: 0, tables_in_known_schemas: 0, schema_names: [],
      text: "can't tell: the earlier surveys could not be read", since: '2026-10-04T08:00:00' },
  });
  const { document } = await setUp(v);
  assert.equal(flat(document.querySelector('[data-scope-new-since]')).startsWith("can't tell: the earlier surveys could not be read"), true);
  assert.equal(document.querySelectorAll('[data-scope-new-since-row]').length, 0);
});

/* ── slice A2.1: columns indented under their table, tighter widths, one activity line ── */

function withColumns() {
  const v = baseView();
  v.depth.value = 'tables_and_columns';
  v.schemas[0].tables[0].columns = [
    { name: 'order_id', type: 'integer', key_role: 'PK' }, { name: 'total', type: 'numeric', key_role: '' }];
  v.schemas[0].tables[1].columns = [{ name: 'customer_id', type: 'integer', key_role: 'PK' }];
  return v;
}

test('columns sit directly under their own table, indented under its NAME, with a visible marker', async () => {
  const { document } = await setUp(withColumns());
  document.querySelector('[data-scope-toggle="sales"]').click();
  const block = document.querySelector('[data-scope-schema-block="sales"]');
  // document order inside the schema block: table row, ITS columns, next table row, ITS columns
  const seq = [...block.querySelectorAll('[data-scope-row], [data-scope-column]')]
    .map((e) => (e.dataset.scopeRow ? e.dataset.scopeRow : flat(e).replace(/^└ /, '')));
  assert.deepEqual(seq, ['schema:sales', 'table:sales.orders', 'order_id integer PK', 'total numeric',
    'table:sales.customers', 'customer_id integer PK']);
  // grouped: one wrapper per table, and it follows that table's row immediately
  const wrappers = [...block.querySelectorAll('[data-scope-columns]')];
  assert.equal(wrappers.length, 2);
  assert.equal(row(document, 'table:sales.orders').nextElementSibling, wrappers[0]);
  assert.equal(row(document, 'table:sales.customers').nextElementSibling, wrappers[1]);
  // indented under the name column: it starts with spacers the width of the tick and choice cells
  const spacers = [...wrappers[0].children].slice(0, 2).map((c) => c.getAttribute('style'));
  const rowWidths = [...row(document, 'table:sales.orders').children].slice(0, 2).map((c) => c.getAttribute('style'));
  assert.deepEqual(spacers, rowWidths, 'the column list starts where the table NAME starts');
  assert.ok(wrappers[0].children[2].className.includes('border-l'), 'a rule marks them as the table\'s');
  const col = block.querySelector('[data-scope-column]');
  assert.ok(col.textContent.includes('└'), 'a visible marker');
  assert.ok(col.className.includes('text-ink-muted') && col.className.includes('text-provenance'), 'muted and smaller than the rows');
  assert.ok(!col.querySelector('.text-ink'), 'no full-strength ink inside a column line');
  assert.ok(row(document, 'table:sales.orders').className.includes('text-caveat'));
});

test('a table with no columns draws no empty column wrapper', async () => {
  const { document } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(document.querySelector('[data-scope-columns]'), null);
});

test('the per-row source line sits under the name, not in the state cell', async () => {
  const { document } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  const r = row(document, 'table:sales.orders');
  assert.ok(r.querySelector('[data-scope-name-cell] [data-scope-source]'));
  assert.equal(r.querySelector('[data-scope-state-cell] [data-scope-source]'), null);
  assert.match(flat(r.querySelector('[data-scope-state-cell]')), /^—/);   // no proof row, so no state word
});

test('widths: the default columns fit a ~1300px content width, the name wraps, narrow panes still scroll', async () => {
  const { document } = await setUp(baseView());
  const inner = document.querySelector('[data-rc-inner]');
  const floor = Number(inner.getAttribute('style').match(/--rc-total,(\d+)px/)[1]);
  // 2026-10-07: 1300 -> 1420. "not measured" and "not established" broke mid-word in the 84px Rows/Size
  // columns, so the defaults were widened to keep a word whole; a narrower pane scrolls inside its box.
  assert.ok(floor <= 1420, `defaults + name floor + gaps (${floor}px) must fit about 1420px`);
  const head = document.querySelector('[data-scope-tree-head]');
  const nameHead = [...head.children].find((c) => flat(c) === 'Schema / table');
  assert.match(nameHead.getAttribute('style'), /flex:0 0 auto/, 'the name column is a fixed width too, so no drag moves the columns to its left');
  const nameCell = row(document, 'schema:sales').querySelector('[data-scope-name-cell]');
  assert.ok(nameCell.className.includes('break-words'), 'the schema / table name may wrap');
  assert.ok(document.querySelector('[data-scope-tree]').className.includes('overflow-x-auto'), 'scroll stays the fallback');
});

test('one line above the tree says why activity can\'t tell, grouped by reason, instead of repeating it per row', async () => {
  const v = baseView();
  const ct = (reason) => ({ state: 'cant_tell', reason, text: `can't tell · ${reason}` });
  v.schemas[0].tables[0].last_write = ct('reset date not recorded');
  v.schemas[0].tables[1].last_write = ct('reset date not recorded');
  v.schemas[1].tables[0].last_write = ct('counters not measured');
  v.schemas[2].last_write = ct('reset date not recorded');       // no tables: counted as its own row
  const { document } = await setUp(v);
  const line = flat(document.querySelector('[data-scope-activity-summary]'));
  assert.equal(line, "Activity: can't tell on 3 rows: counter reset date not recorded · on 1 row: counters not measured");
  // the reason is still reachable on each row
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(row(document, 'table:sales.orders').querySelector('[data-scope-lastwrite-cell] [title]').title,
    "can't tell · reset date not recorded");
});

test('no summary line when nothing is can\'t-tell', async () => {
  const v = baseView();
  v.schemas.forEach((s) => { s.last_write = { state: 'active', writes: 5, text: 'active · 5 writes' }; s.tables.forEach((t) => { t.last_write = s.last_write; }); });
  const { document } = await setUp(v);
  assert.equal(document.querySelector('[data-scope-activity-summary]'), null);
});

test('a size the server could not measure reads "not measured" on the row, never "0 B"', async () => {
  const v = baseView();
  const [orders, customers] = v.schemas[0].tables;
  orders.size_view = { state: 'not_measured', value: null, text: 'not measured', detail: '' };
  customers.size_view = { state: 'measured', value: 0, text: '0 B', detail: 'Egeria survey 10-04' };
  const { document } = await setUp(v);
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(flat(row(document, 'table:sales.orders').querySelector('[data-scope-size-cell]')), 'not measured');
  assert.equal(flat(row(document, 'table:sales.customers').querySelector('[data-scope-size-cell]')), '0 B');
});
