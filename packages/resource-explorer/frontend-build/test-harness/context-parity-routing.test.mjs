/** Routing-level reachability tests for the Enrichment -> Context tab
 *  (ENRICHMENT-E1 parity with the pre-E1 Enrichment form).
 *
 *  WHY THESE EXIST: E1 replaced a render path and silently dropped three
 *  things the old path did (the evidence rail, the license row's survey
 *  proposal, and the Documentation sources block). PR #348's own tests
 *  called `renderDocSources` directly, so they stayed green while the block
 *  was unreachable from any tab. These tests go the way a person does: the
 *  Enrichment stage is set, the Context tab's strip button is CLICKED
 *  (`bindSubTabs` -> `loadPane` -> `loadContextPane` -> `renderContext`),
 *  and only then is the rendered DOM asserted.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const NOW = '2026-09-29T10:00:00Z';

const REPO_EVIDENCE_IDS = ['interface_surface', 'security_scan', 'chaoss_metrics', 'cve_scan',
  'repository_health', 'license_classification', 'secret_scan', 'documentation_coverage'];
/** GET /api/analyses/<kind>: the catalog's per-kind analyses (E3 fetches by kind). */
export function catalogFor(url) {
  const m = /\/api\/analyses\/(repo|database|filesystem)(\?|$)/.exec(url);
  if (!m) return null;
  return m[1] === 'repo' ? REPO_EVIDENCE_IDS.map((id) => ({ id })) : [{ id: 'schema_inventory' }, { id: 'preliminary_fit' }];
}

function stubServer({ facts = [], doc = { sources: [], published: false, publish_note: '' } } = {}) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (catalogFor(u)) return ok(catalogFor(u));
    if (u.includes('/api/analyses/facts')) return ok({ subjects: { amundsen: facts } });
    if (u.includes('/api/doc-sources/')) return ok(doc);
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    return ok({});
  };
}

const LICENSE_FACT = {
  analysis_id: 'license_classification', state: 'measured', last_run_at: NOW,
  headline: 'Apache License 2.0 — Permissive',
  value: { findings: [{ check_name: 'license_risk_tier', label: 'permissive', summary: 'Apache License 2.0 — Permissive' }] },
};
const SCAN_FACT = { analysis_id: 'security_scan', state: 'measured', last_run_at: NOW, headline: '0 high findings', value: {} };

/** Set the Enrichment stage and click the Context tab in the real strip. */
async function routeToContext(resourceType, slug, server) {
  const { document, window } = makeDomEnvironment();
  stubServer(server);   // after makeDomEnvironment(), which resets fetch
  ensureLoaderRegistered();
  // writeUrl() (called by the tab click) uses bare `location`/`history`.
  globalThis.location = window.location;
  globalThis.history = window.history;
  // The rail's ensureRailShowing() asks for a media query jsdom lacks.
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = resourceType;
  app.state.selectedSlug = slug;
  app.state.stage = 'enrichment';
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  // The page-shell hosts loadPane()/the rail expect (index.html).
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  content.innerHTML = app.subTabsHtml();
  app.bindSubTabs();
  const btn = content.querySelector('[data-subtab="context"]');
  assert.ok(btn, 'the Enrichment stage strip must offer a Context tab');
  btn.click();
  // loadPane is async: wait for renderContext to finish.
  for (let i = 0; i < 100 && !document.getElementById('context-human-questions'); i++) {
    await new Promise((r) => setTimeout(r, 10));
  }
  await new Promise((r) => setTimeout(r, 50));
  return { document, app };
}

test('routing: the Context tab shows the Evidence rail with the old form\'s measurements', async () => {
  const { document } = await routeToContext('repo', 'amundsen', { facts: [LICENSE_FACT, SCAN_FACT] });
  const rail = document.getElementById('rail-evidence').textContent;
  assert.match(rail, /Evidence · enrichment/);
  assert.match(rail, /0 high findings/);
  assert.match(rail, /security_scan/);
});

test('routing: the license row offers the survey-measured license to confirm', async () => {
  const { document } = await routeToContext('repo', 'amundsen', { facts: [LICENSE_FACT] });
  const html = document.getElementById('context-observations').innerHTML;
  assert.match(html, /proposed by/);
  assert.match(html, /license_classification/);
  assert.match(html, /Apache License 2\.0/);
  assert.match(html, /data-confirm="licence"/);
});

test('routing: the Context tab mounts the Documentation sources block, and says nothing is "not built"', async () => {
  const { document } = await routeToContext('db', 'amundsen', {
    doc: { sources: [{ id: 's1', url: 'https://docs.example/d', label: 'Data dictionary', source_type: 'data_dictionary',
      added_at: NOW, origin: 'local', probe_state: 'reachable', probe_status_code: 200, probe_ms: 10,
      probe_title: '', probe_byte_count: 0, probe_error: '', probed_at: NOW, egeria_external_ref_guid: '' }],
      published: false, publish_note: '' },
  });
  const block = document.getElementById('doc-sources-block');
  assert.ok(block, '#doc-sources-block must be present on Enrichment -> Context');
  // Context's own heading frames the block; the block's title is omitted.
  assert.match(document.getElementById('context-form').textContent, /Where it's documented/);
  assert.match(document.getElementById('context-form').textContent, /sources, not answers/);
  assert.doesNotMatch(block.textContent, /Documentation sources/);
  assert.match(block.textContent, /1<\/span> declared|1 declared/);
  assert.match(block.textContent, /Data dictionary/);
  assert.match(block.textContent, /feeds → nothing reads this yet/, 'a supplied doc-source row gets a feeds line');
  assert.doesNotMatch(document.getElementById('context-form').textContent, /Documentation sources — not built/);
});

test('renderDocSources keeps its own heading by default (only Context opts out)', async () => {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  stubServer({});
  const app = await import('/static/next/app.js');
  const enrichment = await import(`/static/next/stages/enrichment.js?t=${Date.now()}_${Math.random()}`);
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'amundsen';
  const host = document.createElement('div');
  host.id = 'doc-sources-block';
  document.body.appendChild(host);
  await enrichment.renderDocSources('amundsen');
  assert.match(host.textContent, /Documentation sources/);
});

test('routing: switching resources never leaves the previous resource\'s measurements in the rail', async () => {
  // Resource A (amundsen) renders with a distinctive measurement; then the
  // person moves to B (coco_pharma) whose fetch is SLOW. While B loads, and
  // after it lands with no measurements, nothing from A may be on screen.
  stubServer({ facts: [{ ...SCAN_FACT, headline: 'AMUNDSEN-ONLY-MEASUREMENT' }] });
  const { document, app } = await routeToContext('repo', 'amundsen', { facts: [{ ...SCAN_FACT, headline: 'AMUNDSEN-ONLY-MEASUREMENT' }] });
  assert.match(document.getElementById('rail-evidence').textContent, /AMUNDSEN-ONLY-MEASUREMENT/);

  let release;
  const gate = new Promise((r) => { release = r; });
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (catalogFor(u)) return ok(catalogFor(u));
    if (u.includes('/api/analyses/facts')) { await gate; return ok({ subjects: { coco_pharma: [] } }); }
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    return ok({ questions: [] });
  };
  app.state.selectedSlug = 'coco_pharma';
  // The person picks the other resource and lands on Context again through
  // the strip (the active tab is a span, so re-render the strip off Context).
  app.state.subTab = 'questions';
  document.getElementById('content').innerHTML = app.subTabsHtml();
  app.bindSubTabs();
  const ctxBtn = document.querySelector('[data-subtab="context"]');
  ctxBtn.click();
  for (let i = 0; i < 50 && !/coco_pharma/.test(document.getElementById('rail-evidence').textContent); i++) {
    await new Promise((r) => setTimeout(r, 10));
  }
  const loading = document.getElementById('rail-evidence').textContent;
  assert.doesNotMatch(loading, /AMUNDSEN-ONLY-MEASUREMENT/, 'previous resource\'s data must be gone while the new one loads');
  assert.match(loading, /Reading the evidence/);
  assert.equal(Object.keys(app.state.enrichmentFacts).length, 0);

  release();
  await new Promise((r) => setTimeout(r, 100));
  const landed = document.getElementById('rail-evidence').textContent;
  assert.doesNotMatch(landed, /AMUNDSEN-ONLY-MEASUREMENT/);
  assert.match(landed, /coco_pharma/);
});

test('doc-source rows say who added them, and "unknown" (not blank) for an unsigned one', async () => {
  const doc = { sources: [
    { id: 'a', url: 'https://x/a', label: 'Signed', source_type: 'wiki', added_at: NOW, added_by: 'erinoverview', origin: 'local',
      probe_state: 'reachable', probe_status_code: 200, probe_ms: 5, probed_at: NOW, egeria_external_ref_guid: '' },
    { id: 'b', url: 'https://x/b', label: 'Unsigned', source_type: 'wiki', added_at: NOW, added_by: '', origin: 'local',
      probe_state: 'reachable', probe_status_code: 200, probe_ms: 5, probed_at: NOW, egeria_external_ref_guid: '' },
  ], published: false, publish_note: '' };
  const { document } = await routeToContext('db', 'amundsen', { doc });
  const rows = document.querySelectorAll('[data-source-row]');
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /added by erinoverview/);
  assert.match(rows[1].textContent, /added by unknown/);
});

test('one word for the ◌ glyph: no-surveyor and no_reader share it, and it is "no reader yet"', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const { STATES } = await import('/static/next/glyphs.js').then((m) => ({ STATES: m.STATES || m.GLYPH_STATES || null }));
  const g = await import('/static/next/glyphs.js');
  assert.equal(g.wordOf('no-surveyor'), 'no reader yet');
  assert.equal(g.wordOf('no_reader'), 'no reader yet');
  assert.equal(g.stateEntry('no-surveyor').glyph, g.stateEntry('no_reader').glyph);
  void STATES;
});

test('a direct-field row whose field is empty is ∅ (the existing `nothing` state), never ✓, ○ or ◌', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const app = await import('/static/next/app.js');
  const g = await import('/static/next/glyphs.js');
  const entry = { kind: 'direct', question: 'What is this resource, and what is it for?' };
  const empty = { answerable: true, facts: [{ is_known: true, state: 'nothing_found',
    headline: 'No description recorded for this database yet.' }] };
  const filled = { answerable: true, facts: [{ is_known: true, state: 'measured', headline: 'A sample db' }] };
  const st = app.rowState(entry, empty);
  assert.equal(st, 'nothing');
  assert.equal(g.stateEntry(st).glyph, '∅');
  assert.equal(g.wordOf('nothing'), 'nothing found');
  // One state on ∅ for "known nothing": no second one beside it.
  assert.deepEqual(Object.entries(g.STATES).filter(([, v]) => v.glyph === '∅' && v.word.includes('nothing')).map(([k]) => k), ['nothing']);
  assert.equal(app.rowState(entry, filled), 'automatic');
  // Unanswerable stays ◌, not ∅.
  assert.equal(app.rowState(entry, { answerable: false, facts: [] }), 'no_reader');
});
