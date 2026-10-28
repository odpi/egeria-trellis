/** Behaviour (slice A2.1): the Schema Inventory pane reads the SAME resolved node set as the
 *  Curate tree, names which survey it read (and both, when they disagree), carries each row's
 *  source and as-of, and the page-level credential line says it is about RE's own survey.
 *
 *  Real app.js and the real pane, with a stub behind fetch. jsdom does no layout.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const EG = { kind: 'egeria', as_of: '2026-10-04T06:00:00', text: 'from Egeria survey 10-04' };
const LO = { kind: 'local', as_of: '2026-10-03T09:00:00', text: 'from RE local survey 10-03' };

function tree(n = 29, over = {}) {
  const schemas = Array.from({ length: n }, (_, i) => ({
    schema: `s${String(i).padStart(2, '0')}`, classification: 'measured', table_count: 1, row_total: null,
    bytes_total: 8192, is_estimate: false, reason: '', source: EG, facts_from: { size: EG },
    tables: [{ name: 't00', table_type: 'BASE TABLE', row_count: i === 0 ? 40 : null, row_count_state: 'measured',
      size_bytes: i === 1 ? null : 8192, column_count: i === 1 ? null : 2, source: EG,
      facts_from: i === 0 ? { size: EG, rows: LO } : { size: EG },
      columns: [{ name: 'id', type: 'integer', nullable: false, key_role: 'PK', comment: '' }] }],
  }));
  return {
    schemas,
    sources: {
      chosen: { kind: 'egeria', as_of: EG.as_of, schemas: n, tables: n, merged: true },
      egeria: { state: 'measured', schema_count: n, table_count: n, surveyed_at: EG.as_of },
      local: { state: 'measured', schema_count: 8, table_count: 61, surveyed_at: LO.as_of, sees: 'sees 6 of 8' },
      disagree: true, unreadable: 0,
    },
    ...over,
  };
}

async function setUp(payload, cap = null) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.includes('/schema-inventory-tree')) return ok(payload);
    if (u.includes('/api/databases/')) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = 'db'; s.selectedSlug = 'coco';
  s.databases = [{ slug: 'coco', display_name: 'coco', group_slug: '', credential_capability: cap,
    credential_capability_at: cap ? '2026-10-03T09:00:00' : '' }];
  s.databasesLoaded = true; s.filesystemsLoaded = true; s.filesystems = []; s.projects = [];
  s.groups = []; s.investigations = []; s.investigation = ''; s.workListSlug = null; s.workListIndex = false;
  s.perspectives = []; s.allPerspectives = []; s.workingSet = new Set(); s.me = { user_id: 'me' };
  s.stage = 'discovery'; s.subTab = 'schema_inventory';
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="discovery"]').click();
  await wait();
  return { document, app };
}

test('the pane lists the 29 schemas of the Egeria survey, not the 8 of RE\'s stale local one', async () => {
  const { document } = await setUp(tree(29));
  assert.equal(document.querySelectorAll('#schema-tree > details').length, 29);
  assert.match(flat(document.querySelector('[data-schema-tree-sources]')),
    /^29 schemas · Egeria survey 10-04 · RE's own survey saw 8 schemas, 61 tables, 10-03$/);
});

test('every schema and table row carries its source and as-of, and a filled fact says where it came from', async () => {
  const { document } = await setUp(tree(3));
  const [s0, s1] = document.querySelectorAll('#schema-tree > details');
  assert.match(flat(s0.querySelector('summary')), /from Egeria survey 10-04/);
  const t0 = s0.querySelector(':scope > div details > summary');
  assert.match(flat(t0), /from Egeria survey 10-04 · rows from RE local survey 10-03/);
  assert.equal(document.querySelectorAll('[data-tree-source]').length, 6, '3 schemas + 3 tables');
  // a null size and a null column count are "not measured", never a zero or "null column(s)"
  assert.match(flat(s1.querySelector(':scope > div details > summary')), /not measured · not measured · columns not measured/);
  assert.doesNotMatch(flat(document.querySelector('#schema-tree')), /undefined|NaN|null column/);
  // a native schema carries no row total: the stamp says tables and size, not a made-up "0 row(s)"
  assert.match(flat(s0.querySelector('summary')), /1 table\(s\) · 8\.0 KB|1 table\(s\) · 8 KB/);
  assert.doesNotMatch(flat(s0.querySelector('summary')), /0 row\(s\)/);
});

test('one survey agreeing with the other reads as one source, with no second clause', async () => {
  const p = tree(8);
  p.sources.disagree = false;
  const { document } = await setUp(p);
  assert.equal(flat(document.querySelector('[data-schema-tree-sources]')), '8 schemas · Egeria survey 10-04');
});

test('the page-level credential line names RE\'s own survey, its date, and what it is scoped to', async () => {
  const cap = { connected_as: 'surveyor', schema_total: 8, schema_visible: 6, relation_total: 61, relation_select: 3 };
  const { document } = await setUp(tree(29), cap);
  const line = flat(document.querySelector('[data-credential-scope-line]'));
  assert.equal(line, "RE's own survey 10-03: connected as surveyor, sees 6 of 8 schemas, SELECT on 3 of 61 relations; counts from that survey are scoped to this credential");
  assert.doesNotMatch(line, /every count on this page/);
});

test('a credential that sees everything gets the survey-and-date prefix but no scoped warning', async () => {
  const cap = { connected_as: 'admin', schema_total: 8, schema_visible: 8, relation_total: 61, relation_select: 61 };
  const { document } = await setUp(tree(8), cap);
  const line = flat(document.querySelector('[data-credential-scope-line]'));
  assert.match(line, /^RE's own survey 10-03: connected as admin/);
  assert.doesNotMatch(line, /scoped to this credential/);
});
