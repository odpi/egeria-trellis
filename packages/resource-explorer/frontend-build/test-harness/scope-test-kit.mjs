/** Shared fixtures for the Curate scope harness tests (layout, selection, writes and errors). */
import assert from 'node:assert/strict';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

export const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
export const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();

export const node = (kind, name, over = {}) => ({
  kind, name, key: name, state: 'undecided', proposal: null, live_proposal: null, overridden: null,
  disagrees: null, notes: [], marks: [], explicit: null, effective: null, effective_from: null,
  new_since: false, access: 'established', provenance: '',
  last_write: { state: 'not_established', from: '', to: '', text: 'not established' },
  source: { kind: 'egeria', as_of: '2026-10-04T06:00:00', text: 'from Egeria survey 10-04' }, facts_from: {},
  data_classes: { state: 'not_established', classes: [], pii_columns: 0 }, ...over,
});
export const table = (schema, name, over = {}) => node('table', name, {
  schema, key: `${schema}.${name}`, table_type: 'BASE TABLE', row_count: 10, row_count_state: 'measured',
  inherited: null, differs_from_schema: false, column_count: 1, ...over,
});
export const schema = (name, tables = [], over = {}) => node('schema', name, {
  classification: 'data', table_count: tables.length, row_total: 20, bytes_total: null, is_estimate: false,
  undecided_words: 'keeps what’s in Egeria now', tables, ...over,
});
export const person = (choice, by = 'dwolfson', at = '2026-10-04T10:00:00', extra = {}) => ({
  choice, action: 'set', source: 'person', by, at, reason: '', proposal_rule: '', proposal_choice: '',
  measured_at: '', measured: {}, ...extra,
});

export function baseView(over = {}) {
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

export function makeServer(view, { signedIn = true, dropWrites = false } = {}) {
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
      if (method === 'POST' && u.endsWith('/commit')) {
        if (!s.signedIn) return err(401, 'Sign in to publish');
        if (s.holdCommit) await s.holdCommit;
        return ok({ curation: s.record || { id: 'cafe0123', state: 'queued', author: 'me', steps: [] }, run_id: 'r0000001' });
      }
      if (method === 'GET' && u.endsWith('/commits/latest')) {
        if (s.latestFails) return err(500, 'registry unreadable');
        return ok(s.latest || { commit: null, terminal: null, age_hours: null, stale_unfinished: false, states: {} });
      }
      if (method === 'GET' && u.includes('/commits/')) return ok(s.record || { id: 'cafe0123', state: 'running', author: 'me', steps: [] });
      if (method === 'POST' && u.endsWith('/read-back')) { s.readBacks = (s.readBacks || 0) + 1; if (s.holdReadBack) await s.holdReadBack; return ok({ catalogued: 0, attached_waiting: 0 }); }
      if (method === 'PUT' && s.holdPut) await s.holdPut;
      if (method === 'POST' && u.endsWith('/nodes') && s.holdPut) await s.holdPut;
      if (method === 'PUT' && s.failPut) return err(500, s.failPut);
      if (method === 'GET' && s.holdGet && !u.endsWith('/commit-preview')) await s.holdGet;
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
          const targets = body.all_schemas ? s.view.schemas.map((x) => ({ schema_name: x.name, table_name: '' })) : body.nodes;
          for (const tg of targets) {
            const n = find(tg.schema_name, tg.table_name || '');
            if (body.choice) { n.explicit = person(body.choice, 'me'); n.effective = body.choice; n.state = 'chosen'; n.proposal = null; }
            else { n.explicit = null; n.effective = null; n.state = 'undecided'; }
          }
        }
        if (u.endsWith('/node/clear')) {
          const n = find(body.schema_name, body.table_name);
          n.explicit = null; n.effective = null; n.state = 'undecided';
        }
        if (u.endsWith('/redeclare')) { s.view.new_since = { declared: true, schemas: 0, tables: 0, tables_in_known_schemas: 0, schema_names: [], text: '' }; for (const sc of s.view.schemas) { sc.new_since = false; sc.tables.forEach((t) => { t.new_since = false; }); } }
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

export async function setUp(view, opts = {}) {
  const { document, window } = makeDomEnvironment();
  const signedIn = opts.signedIn !== false;
  const server = makeServer(view, { signedIn, dropWrites: !!opts.dropWrites });
  ensureLoaderRegistered();
  const scopeMod = await import('/static/next/stages/curate-scope.js');
  scopeMod.resetScopeUi();
  if (opts.beforeOpen) opts.beforeOpen({ server, mod: scopeMod, document, window });   // a test's chance to set the server's rows and the clock before the page loads
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
  // A database's Curate is three tabs (Brief S2): the scope tree and the commit panel are on Contents, and the commit
  // preview (which reads Egeria) is read when that tab is shown. Tests of the scope open it, as a person does.
  if (opts.tab !== 'describe') {
    const t = document.querySelector('[data-subtab="curate_contents"]');
    assert.ok(t, 'a database offers the Contents tab');
    t.click(); await wait();
  }
  return { document, window, app, server };
}

export const scopeEl = (d) => d.querySelector('[data-curate-scope]');
export const row = (d, key) => d.querySelector(`[data-scope-row="${key}"]`);
export const calls = (server, method, frag) => server.calls.filter((c) => c.method === method && c.url.includes(frag));

