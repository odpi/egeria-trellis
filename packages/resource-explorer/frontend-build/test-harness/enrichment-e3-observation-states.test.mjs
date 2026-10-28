/** ENRICHMENT-E3 regression tests, routing level: each one sets the Enrichment
 *  stage, CLICKS the Context tab in the real strip, and asserts the rendered
 *  DOM -- gate items 1-5 of BRIEF-ENRICHMENT-E3-OBSERVATION-STATES.md, plus
 *  the Scouting licence row. The stub server is STATEFUL: a PATCH /field is
 *  remembered and served back by the next GET /api/context, stamping
 *  measured_value/measured_at the way the real route does.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const T0 = new Date(Date.now() - 3 * 86400000).toISOString();
const T1 = new Date(Date.now() - 3600000).toISOString();
const REPO_EVIDENCE = ['interface_surface', 'security_scan', 'chaoss_metrics', 'cve_scan',
  'repository_health', 'license_classification', 'secret_scan', 'documentation_coverage'];

const licenceFact = (name, at = T1) => ({
  analysis_id: 'license_classification', state: 'measured', last_run_at: at, headline: `${name} — Permissive`,
  value: { findings: [{ check_name: 'license_risk_tier', label: 'permissive', summary: `${name} — Permissive` }] },
});
// The owner is its OWN fact (`database_owner`); never schema_inventory's value.
const ownerFact = (owner) => owner
  ? { analysis_id: 'database_owner', state: 'measured', last_run_at: '', headline: `database owner role: ${owner}`,
      value: { owner, measured_at: T1 } }
  : { analysis_id: 'database_owner', state: 'never_run', last_run_at: '', headline: '', value: {} };

/** The server. `facts` answers /api/analyses/facts per requested id list. */
function makeServer({ kind, factsById = {}, enrichment = {}, log }) {
  const saved = { ...enrichment };
  const catalog = kind === 'repo' ? REPO_EVIDENCE : ['schema_inventory', 'preliminary_fit', 'database_owner'];
  const measuredNow = () => {
    const f = factsById.license_classification;
    const s = f?.value?.findings?.[0]?.summary;
    return kind === 'repo' && s ? { value: s.split(' — ')[0], at: f.last_run_at } : { value: '', at: '' };
  };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    log.push(`${opts.method || 'GET'} ${u}`);
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok(catalog.map((id) => ({ id })));
    if (u.includes('/api/analyses/facts')) {
      const ids = new URL(u, 'http://x').searchParams.get('analysis_ids').split(',');
      return ok({ subjects: { [currentSlug]: ids.map((i) => factsById[i]).filter(Boolean) } });
    }
    if (opts.method === 'PATCH' && u.endsWith('/field')) {
      const body = JSON.parse(opts.body);
      const m = measuredNow();
      saved[body.key] = { value: body.value, kind: body.kind, author: 'dan', set_at: new Date().toISOString(),
        source: body.source || '', evidence: {}, interim: false,
        measured_value: body.kind === 'observation' ? m.value : '', measured_at: body.kind === 'observation' ? m.at : '' };
      log.push(`BODY ${opts.body}`);
      return ok({ key: body.key, field: saved[body.key] });
    }
    if (u.includes('/api/context/')) return ok({ enrichment: saved, question_answers: {} });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    return ok({ questions: [] });
  };
  return saved;
}
let currentSlug = '';

async function routeToContext(resourceType, slug, server) {
  currentSlug = slug;
  const { document, window } = makeDomEnvironment();
  const log = [];
  const saved = makeServer({ ...server, kind: resourceType === 'db' ? 'database' : 'repo', log });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType, selectedSlug: slug, stage: 'enrichment', subTab: 'questions',
    investigations: [], investigation: '', me: { user_id: 'dan' } });
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  content.innerHTML = app.subTabsHtml();
  app.bindSubTabs();
  content.querySelector('[data-subtab="context"]').click();
  await settle(document);
  return { document, app, log, saved };
}
async function settle(document) {
  for (let i = 0; i < 100 && !document.getElementById('context-human-questions'); i++) await new Promise((r) => setTimeout(r, 10));
  await new Promise((r) => setTimeout(r, 60));
}
const licenceRow = (document) => document.getElementById('context-observations');
const licenceText = (document) => {
  const rows = [...document.querySelectorAll('#context-observations > div')];
  const i = rows.findIndex((r) => /^\s*License/.test(r.textContent));
  // The "feeds →" line is the row's next sibling.
  return `${rows[i]?.textContent || ''} ${rows[i + 1]?.textContent || ''}`.replace(/\s+/g, ' ');
};

// ── gate 1 ────────────────────────────────────────────────────────────────
test('gate 1: a database\'s rail names no repository analysis and says one line; nothing is fetched for repo', async () => {
  const { document, log } = await routeToContext('db', 'coco_pharma', { factsById: {} });
  const rail = document.getElementById('rail-evidence').textContent;
  assert.match(rail, /no enrichment evidence is listed for databases yet/);
  for (const id of REPO_EVIDENCE) assert.doesNotMatch(rail, new RegExp(id), `${id} must not appear on a database`);
  assert.doesNotMatch(rail, /never.?run|has not run/i);
  assert.ok(!log.some((l) => /entity_type=repo/.test(l)), log.join('\n'));
  assert.ok(!log.some((l) => /analysis_ids=[^&]*(license_classification|repository_health)/.test(l)));
  assert.ok(log.some((l) => /GET .*\/api\/analyses\/database(\?|$)/.test(l)), 'the per-kind catalog is what decides');
});

test('gate 1: a repository\'s rail still shows its measurements, fetched with entity_type=repo', async () => {
  const { document, log } = await routeToContext('repo', 'amundsen', { factsById: { license_classification: licenceFact('Apache License 2.0') } });
  assert.match(document.getElementById('rail-evidence').textContent, /license_classification/);
  assert.ok(log.some((l) => /analyses\/facts\?.*entity_type=repo/.test(l)));
});

// ── gate 2 ────────────────────────────────────────────────────────────────
test('gate 2: amundsen licence is PROPOSED: muted, no person line, "proposed by license_classification · time"', async () => {
  const { document } = await routeToContext('repo', 'amundsen', { factsById: { license_classification: licenceFact('Apache License 2.0') } });
  const t = licenceText(document);
  assert.match(t, /proposed: Apache License 2\.0 · proposed by license_classification · \d+/);
  assert.ok(document.querySelector('[data-observation-state="proposed"]'));
  assert.ok(document.querySelector('[data-confirm="licence"]'));
  assert.doesNotMatch(t, /confirmed by|✓/);
});

test('gate 2: accepting the proposal makes the row CONFIRMED and signed, with the feeds line', async () => {
  const { document, log } = await routeToContext('repo', 'amundsen', { factsById: { license_classification: licenceFact('Apache License 2.0') } });
  document.querySelector('[data-confirm="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  const t = licenceText(document);
  assert.ok(document.querySelector('[data-observation-state="confirmed"]'));
  assert.match(t, /from license_classification · confirmed by dan · /);
  assert.match(t, /feeds →/);
  assert.doesNotMatch(t, /proposed/);
  assert.ok(log.some((l) => l.startsWith('BODY') && /"source":"license_classification"/.test(l)));
});

test('gate 2: typing a DIFFERENT value makes it OVERRIDDEN with the measured value still visible', async () => {
  const { document, app } = await routeToContext('repo', 'amundsen', { factsById: { license_classification: licenceFact('Apache License 2.0') } });
  const input = document.querySelector('[data-field="licence"]');
  input.value = 'BSD-3-Clause';
  document.querySelector('[data-save="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  const t = licenceText(document);
  assert.ok(document.querySelector('[data-observation-state="overridden"]'));
  assert.match(t, /set by dan/);
  assert.match(t, /measured: Apache License 2\.0 \(license_classification\)/);
  assert.equal(app.state.enrichment.licence.value, 'BSD-3-Clause');
});

// ── gate 3 ────────────────────────────────────────────────────────────────
test('gate 3: a SAME re-run leaves a confirmed row confirmed; a DIFFERENT measurement raises "⚠ review", cleared only by a person', async () => {
  const facts = { license_classification: licenceFact('Apache License 2.0', T0) };
  const { document, app, saved } = await routeToContext('repo', 'amundsen', { factsById: facts });
  document.querySelector('[data-confirm="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  assert.ok(document.querySelector('[data-observation-state="confirmed"]'));

  // Survey re-runs with the SAME result: nothing changes, no false disagreement.
  facts.license_classification = licenceFact('Apache License 2.0', new Date().toISOString());
  const reload = async () => { app.state.subTab = 'questions'; document.getElementById('content').innerHTML = app.subTabsHtml(); app.bindSubTabs();
    document.querySelector('[data-subtab="context"]').click(); await settle(document); };
  await reload();
  assert.ok(document.querySelector('[data-observation-state="confirmed"]'), 'same measurement must not flip to disagree');
  assert.doesNotMatch(licenceText(document), /review/);

  // Survey now measures something DIFFERENT.
  facts.license_classification = licenceFact('GPL-3.0', new Date().toISOString());
  await reload();
  assert.ok(document.querySelector('[data-observation-state="disagrees"]'));
  assert.match(licenceText(document), /⚠ review — survey now measures GPL-3\.0 \(was Apache License 2\.0\)/);
  // It does not clear by itself: reload again, still flagged.
  await reload();
  assert.ok(document.querySelector('[data-observation-state="disagrees"]'));
  assert.equal(saved.licence.value, 'Apache License 2.0', 'the person\'s value is never rewritten by a survey');

  // A person chooses: "keep mine" re-stamps, flag clears.
  document.querySelector('[data-keep="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  assert.ok(!document.querySelector('[data-observation-state="disagrees"]'));
  assert.ok(document.querySelector('[data-observation-state="overridden"]'));
  assert.match(licenceText(document), /measured: GPL-3\.0/);
});

test('gate 3: an override stays overridden when the survey re-measures the ORIGINAL value again', async () => {
  const facts = { license_classification: licenceFact('MIT License', T0) };
  const { document, app } = await routeToContext('repo', 'amundsen', { factsById: facts });
  document.querySelector('[data-field="licence"]').value = 'Apache-2.0';
  document.querySelector('[data-save="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  facts.license_classification = licenceFact('MIT License', new Date().toISOString());
  app.state.subTab = 'questions'; document.getElementById('content').innerHTML = app.subTabsHtml(); app.bindSubTabs();
  document.querySelector('[data-subtab="context"]').click(); await settle(document);
  assert.ok(document.querySelector('[data-observation-state="overridden"]'));
  assert.ok(!document.querySelector('[data-observation-state="disagrees"]'));
});

// ── gate 4 ────────────────────────────────────────────────────────────────
test('gate 4: coco_pharma licence reads "no survey measures this for databases", accepts a typed value, signed', async () => {
  const { document, log } = await routeToContext('db', 'coco_pharma', { factsById: {} });
  assert.match(licenceText(document), /no survey measures this for databases/);
  assert.ok(!document.querySelector('[data-confirm="licence"]'), 'nothing can propose a licence for a database');
  document.querySelector('[data-field="licence"]').value = 'internal use only';
  document.querySelector('[data-save="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  const t = licenceText(document);
  assert.match(t, /no survey measures this for databases/);
  assert.match(t, /confirmed by dan · /);
  assert.match(t, /feeds →/);
  assert.ok(log.some((l) => l.startsWith('BODY') && /"kind":"observation"/.test(l)));
});

// ── gate 5 ────────────────────────────────────────────────────────────────
test('gate 5: the owner row says "not measured yet · run a survey" before a survey, then the measured role as material', async () => {
  const before = await routeToContext('db', 'coco_pharma', { factsById: { database_owner: ownerFact(null) } });
  const ownerRowText = (document) => {
    const rows = [...document.querySelectorAll('#context-judgements > div')];
    return rows.find((r) => /^\s*Owner/.test(r.textContent))?.textContent.replace(/\s+/g, ' ') || '';
  };
  assert.match(ownerRowText(before.document), /database owner role: not measured yet · run a survey/);

  const after = await routeToContext('db', 'coco_pharma', { factsById: { database_owner: ownerFact('pharma_owner') } });
  const t = ownerRowText(after.document);
  assert.match(t, /database owner role: pharma_owner \(measured\)/);
  const row = [...after.document.querySelectorAll('#context-judgements > div')].find((r) => /^\s*Owner/.test(r.textContent));
  assert.ok(!row.querySelector('[data-confirm]'), 'never offered as a proposal');
  assert.ok(!row.querySelector('[data-value="pharma_owner"]'));
  assert.match(row.querySelector('[data-field="owner"]').value, /^$/, 'the owner input is not pre-filled from the measurement');
});

test('gate 5: a repository has no database-owner line at all', async () => {
  const { document } = await routeToContext('repo', 'amundsen', { factsById: {} });
  assert.doesNotMatch(document.getElementById('context-judgements').textContent, /database owner role/);
});

// ── Scouting's licence row (brief §4) ─────────────────────────────────────
test('scouting: a database\'s licence question is never ◌; it points to Context, and the link routes there', async () => {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  currentSlug = 'coco_pharma';
  const log = [];
  const saved = makeServer({ kind: 'database', factsById: {}, log });
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'coco_pharma', stage: 'scouting', subTab: 'questions',
    enrichment: {}, contextAnswers: {}, questions: [], investigations: [], investigation: '' });
  const entry = { question: 'Under what license or agreement may this resource be used?', kind: 'direct', stage: 'Scouting', perspectives: ['Security'] };
  const env = { answerable: false, facts: [], blocked_reason: 'no reader' };
  const host = document.createElement('div'); host.id = 'content'; document.body.appendChild(host);
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid', 'intent-nav']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  host.innerHTML = `<div>${app.rowInner(entry, 0, env)}</div>`;
  assert.doesNotMatch(host.textContent, /◌|no reader yet/);
  assert.match(host.textContent, /no survey measures this for databases · record it on Context ›/);

  app.state.enrichment = { licence: { value: 'internal use only', author: 'dan', set_at: T0 } };
  host.innerHTML = `<div>${app.rowInner(entry, 0, env)}</div>`;
  assert.doesNotMatch(host.textContent, /◌|no reader yet/);
  assert.match(host.textContent, /recorded on Enrichment › Context ›/);

  app.wireHumanAnswers(host, 'coco_pharma');
  host.querySelector('[data-goto-context]').click();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(app.state.stage, 'enrichment');
  assert.equal(app.state.subTab, 'context');
  void saved;
});

test('scouting: a repository\'s licence row is untouched (its reader is a survey, not Context)', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', enrichment: {}, contextAnswers: {}, questions: [] });
  const entry = { question: 'Under what license or agreement may this resource be used?', kind: 'direct', perspectives: [] };
  const html = app.rowInner(entry, 0, { answerable: false, facts: [], blocked_reason: 'x' });
  assert.doesNotMatch(html, /data-goto-context/);
});

// ── follow-up: the owner is its own fact ──────────────────────────────────
test('gate 5: the owner line reads ONLY the database_owner fact -- a schema_inventory fact carrying a tables list changes nothing', async () => {
  const ownerRow = (document) => [...document.querySelectorAll('#context-judgements > div')]
    .find((r) => /^\s*Owner/.test(r.textContent))?.textContent.replace(/\s+/g, ' ') || '';
  const empty = { analysis_id: 'schema_inventory', state: 'measured', last_run_at: T1, headline: '0 schema(s)',
    value: { relation_count: 0, tables: [] } };
  const a = await routeToContext('db', 'coco_pharma', { factsById: { schema_inventory: empty, database_owner: ownerFact('pharma_owner') } });
  assert.match(ownerRow(a.document), /database owner role: pharma_owner \(measured\)/, 'an empty-tables schema must not hide a real owner read');
  const b = await routeToContext('db', 'coco_pharma', { factsById: { schema_inventory: empty } });
  assert.match(ownerRow(b.document), /not measured yet · run a survey/);
  assert.ok(b.log.some((l) => /analysis_ids=[^&]*database_owner/.test(l)));
  assert.ok(!b.log.some((l) => /analysis_ids=[^&]*schema_inventory/.test(l)), 'schema_inventory is no longer fetched for the owner line');
});

// ── follow-up: "survey now agrees" ────────────────────────────────────────
test('equal-value case: confirmed, and the material line says "survey now agrees · time"', async () => {
  const facts = { license_classification: licenceFact('MIT License', T0) };
  const { document, app } = await routeToContext('repo', 'amundsen', { factsById: facts });
  document.querySelector('[data-field="licence"]').value = 'GPL-3.0';     // override MIT
  document.querySelector('[data-save="licence"]').click();
  await new Promise((r) => setTimeout(r, 80));
  assert.ok(document.querySelector('[data-observation-state="overridden"]'));
  // The survey later measures exactly what the person typed.
  facts.license_classification = licenceFact('GPL-3.0', new Date().toISOString());
  app.state.subTab = 'questions'; document.getElementById('content').innerHTML = app.subTabsHtml(); app.bindSubTabs();
  document.querySelector('[data-subtab="context"]').click(); await settle(document);
  assert.ok(document.querySelector('[data-observation-state="confirmed"]'));
  assert.ok(!document.querySelector('[data-observation-state="disagrees"]'));
  assert.match(document.querySelector('[data-observation-agrees]').textContent, /survey now agrees · /);
});

// ── follow-up: one state, one word, everywhere ────────────────────────────
const LICENCE_Q = 'Under what license or agreement may this resource be used?';
test('one state everywhere: Context, the Questions KEY and the work list (cells, narrow list, digest) say the same thing for the db licence row', async () => {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const entry = { question: LICENCE_Q, kind: 'direct', analysis_ids: [], stage: 'Scouting', perspectives: ['Security'] };
  const recorded = { licence: { value: 'internal use only', author: 'dan', set_at: T0, kind: 'observation', source: 'user' } };
  const ctxBySlug = { rec_db: recorded, blank_db: {} };
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    const m = /\/api\/context\/database\/([^/?]+)/.exec(u);
    if (m) return ok({ enrichment: ctxBySlug[m[1]] || {}, question_answers: {} });
    if (/questions/.test(u)) return ok({ questions: [entry] });
    if (u.includes('/api/analyses/facts')) return ok({ states: {}, subjects: {} });
    return ok({});
  };
  const app = await import('/static/next/app.js');
  const wl = await import('/static/next/worklist.js');
  const g = await import('/static/next/glyphs.js');
  const word = (k) => g.wordOf(k);

  // -- Questions KEY, for each resource ------------------------------------
  const keyFor = (enrichment) => {
    const host = document.createElement('div'); host.id = 'state-legend'; document.body.appendChild(host);
    Object.assign(app.state, { resourceType: 'db', enrichment, questions: [entry],
      answers: new Map([[LICENCE_Q, { answerable: false, facts: [], blocked_reason: 'no reader' }]]), runsInFlight: new Map() });
    app.renderLegend();
    const t = host.textContent.replace(/\s+/g, ' ');
    host.remove();
    return t;
  };
  const keyRec = keyFor(recorded);
  const keyBlank = keyFor({});
  assert.match(keyRec, new RegExp(`${word('answered')} 1`));
  assert.match(keyBlank, new RegExp(`${word('human')} 1`));
  for (const k of [keyRec, keyBlank]) assert.doesNotMatch(k, /no reader yet/, 'the KEY must not count it as ◌');

  // -- the work list over the same two resources ----------------------------
  wl.grid.workList = { display_name: 'dbs', entity_type: 'database', members: [{ entity_slug: 'rec_db' }, { entity_slug: 'blank_db' }] };
  const el = document.createElement('div'); document.body.appendChild(el);
  for (const id of ['wl-actions', 'wl-note', 'wl-progress']) { /* created by renderWorkListPane */ }
  await wl.renderWorkListPane({ el, stage: 'Scouting', subTabs: [], perspectives: [], analyses: [], onExit() {} });
  for (let i = 0; i < 50 && !el.querySelector('#wl-grid td'); i++) await new Promise((r) => setTimeout(r, 20));
  await new Promise((r) => setTimeout(r, 100));
  const cellGlyph = (slug) => el.querySelector(`[data-wlrow="${slug}"] td.wl-cell, tr[data-slug="${slug}"] td.wl-cell`)?.textContent.trim();
  const grid = el.querySelector('#wl-grid').innerHTML;
  assert.doesNotMatch(grid, /◌/, 'no ◌ anywhere in the work list for this question');
  assert.ok(grid.includes(wl.CELL.answered.glyph) && grid.includes(wl.CELL.human.glyph),
    'recorded member reads answered, blank member reads human, in the same vocabulary as the KEY');
  void cellGlyph;
  // (The digest's own static key line names the aggregate "not run · no reader
  // yet · unread" bucket; the assertion is about THIS question's cells.)
  const cellText = [...el.querySelectorAll('#wl-grid td.wl-cell')].map((c) => `${c.getAttribute('title') || ''} ${c.textContent}`).join(' | ');
  assert.doesNotMatch(cellText, /no reader yet/);
  assert.match(cellText, new RegExp(word('answered')));
  assert.match(cellText, new RegExp(word('human')));
});
