/** Measures the Curate scope pane on a coco_pharma-sized fixture (29 schemas, 266 tables, a few
 *  inherited choices): render time, DOM node count, and the requests one write makes.
 *  Run:  node measure-scope-render.mjs     (not a test; prints JSON). Reads whichever static/ tree
 *  the loader resolves, so the same file measures an unchanged checkout too. */
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const node = (kind, name, over = {}) => ({
  kind, name, key: name, state: 'undecided', proposal: null, live_proposal: null, overridden: null,
  disagrees: null, notes: [], marks: [], explicit: null, effective: null, effective_from: null,
  new_since: false, access: 'established', provenance: '',
  last_write: { state: 'not_established', text: 'not established' },
  source: { kind: 'egeria', as_of: '2026-10-04T06:00:00' }, facts_from: {},
  data_classes: { state: 'not_established', classes: [], pii_columns: 0 }, ...over,
});
const person = (choice) => ({ choice, action: 'set', source: 'person', by: 'me', at: '2026-10-04T10:00:00', reason: '' });
function fixture() {
  const schemas = [];
  for (let i = 0; i < 29; i++) {
    const name = `coco_s${i}`;
    const nt = i < 26 ? 9 : 16;   // 26*9 + 3*16 = 282 -> trimmed below
    const tables = [];
    for (let j = 0; j < nt; j++) {
      tables.push(node('table', `t${j}`, { schema: name, key: `${name}.t${j}`, table_type: 'BASE TABLE', row_count: 10,
        inherited: null, differs_from_schema: false, column_count: 1 }));
    }
    schemas.push(node('schema', name, { classification: 'data', table_count: tables.length, row_total: 20, tables,
      undecided_words: 'keeps what is in Egeria now' }));
  }
  let extra = schemas.reduce((a, s) => a + s.tables.length, 0) - 266;
  for (let i = 28; extra > 0 && i >= 0; i--) { const s = schemas[i]; while (extra > 0 && s.tables.length > 1) { s.tables.pop(); s.table_count--; extra--; } }
  for (const i of [0, 1, 2]) {
    const s = schemas[i]; s.explicit = person('catalogue'); s.effective = 'catalogue'; s.effective_from = 'self'; s.state = 'chosen';
    s.tables.forEach((t) => { t.effective = 'catalogue'; t.effective_from = 'schema'; t.inherited = 'catalogue'; });
  }
  return {
    database: 'db', egeria_element: { short: 'abcdef12' },
    survey: { state: 'measured', schema_count: 29, table_count: 266 },
    sources: { chosen: { kind: 'egeria', as_of: '2026-10-04T06:00:00', schemas: 29, tables: 266 }, egeria: { state: 'measured', schema_count: 29, table_count: 266 }, local: { state: 'not_measured' } },
    declared: { declared: true, by: 'me', at: '2026-10-04T10:00:00', kind: 'first' },
    depth: { value: 'schemas_and_tables', options: [{ id: 'schemas_and_tables', label: 'schemas and tables', how: '' }], help: '' },
    system: null, counts: { schemas_offered: 29, schemas_catalogue: 3 }, new_since: { declared: true, text: '' },
    commit: { header: { state: 'not_committed', text: 'x' }, collisions: [], schemas: {}, tables: {} },
    schemas,
  };
}

const { document, window } = makeDomEnvironment();
ensureLoaderRegistered();
globalThis.location = window.location;
const view = fixture();
const reqs = [];
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET'; reqs.push(`${method} ${String(url).replace(/^.*\/api\//, '')}`);
  const body = opts.body ? JSON.parse(opts.body) : null;
  if (method === 'PUT' && body) { const s = view.schemas.find((x) => x.name === body.schema_name); if (s && !body.table_name) { s.explicit = person(body.choice); s.effective = body.choice; s.state = 'chosen'; } }
  const out = String(url).endsWith('/commit-preview') ? { manifest: { lines: [] }, can_commit: false, blockers: [], button: 'x', leave_out: [], refused: [], collisions: [] } : view;
  return { ok: true, status: 200, json: async () => out };
};
const app = await import('/static/next/app.js');
const scope = await import('/static/next/stages/curate-scope.js');
app.state.selectedSlug = 'db'; app.state.me = { user_id: 'me' };
const host = document.createElement('div'); document.body.appendChild(host);
const count = () => host.querySelectorAll('*').length;
const t0 = performance.now();
await scope.renderCatalogueScope(host, 'db');
const initial = performance.now() - t0; const nodesInitial = count();
await wait(100);
async function oneWrite(schemaName, choice) {
  reqs.length = 0;
  host.querySelector('[data-scope-status]').textContent = '';
  const b = host.querySelector(`[data-scope-act="set"][data-scope-choice="${choice}"][data-scope-schema="${schemaName}"]`);
  const t = performance.now(); b.click();
  for (let i = 0; i < 400 && !/re-read scope/.test(host.querySelector('[data-scope-status]')?.textContent || ''); i++) await wait(1);
  return { ms: Math.round(performance.now() - t), reqs: reqs.slice() };
}
const collapsedWrite = await oneWrite('coco_s7', 'catalogue');
await wait(100);
host.querySelectorAll('[data-scope-toggle]').forEach((b) => b.click());
const nodesExpanded = count();
const expandedWrite = await oneWrite('coco_s5', 'leave_out'); await wait(150);
console.log(JSON.stringify({ initialRenderMs: Math.round(initial), nodesCollapsed: nodesInitial, nodesAllExpanded: nodesExpanded,
  oneWriteCollapsedMs: collapsedWrite.ms, oneWriteAllExpandedMs: expandedWrite.ms, requestsAfterWrite: collapsedWrite.reqs }, null, 1));
process.exit(0);
