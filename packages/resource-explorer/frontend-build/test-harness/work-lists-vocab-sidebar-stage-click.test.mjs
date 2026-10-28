/** Behaviour: slice W1-C of REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md.
 *
 *  §1 vocabulary and the sidebar: the investigation page says "Scope · N", the
 *  list of work lists opens with the one-sentence definition, "working set" is
 *  never a label, and the sidebar splits scope / lists for this investigation /
 *  other lists / suggestions with counts read from the real rows.
 *  §5 a stage click while a work list is open: a run stage keeps the list open
 *  and re-scopes it ("Assessment’s questions"); Investigation, Understanding
 *  and Automate close it into the "↩ <list>" link; Investigation on a list
 *  linked to an investigation opens THAT investigation with its Scope in view.
 *
 *  Real app.js, real router, the shipped index.html body; only fetch is stubbed.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_DIR = path.resolve(HERE, '../../resource_explorer/web/static/next');
const INDEX_HTML = fs.readFileSync(path.join(NEXT_DIR, 'index.html'), 'utf8');

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const INVESTIGATIONS = [
  { slug: 'c360', display_name: 'Customer 360', status: 'open' },
  { slug: 'hr', display_name: 'Retire legacy HR', status: 'open' },
];
const MEMBERS = {
  c360: [{ entity_type: 'database', entity_slug: 'crm_prod' }, { entity_type: 'database', entity_slug: 'hr_payroll' },
         { entity_type: 'repo', entity_slug: 'amundsen' }],
  hr: [{ entity_type: 'database', entity_slug: 'hr_payroll' }],
};
const wlRow = (slug, over = {}) => ({
  slug, display_name: slug, entity_type: 'database', investigation: '', member_count: 2, egeria_guid: '',
  derived_from: '', ...over,
});
// 2 linked to c360, 3 unlinked/other, 1 suggestion inbox.
const WORK_LISTS = [
  wlRow('sales-databases', { display_name: 'Sales databases', investigation: 'c360', member_count: 9 }),
  wlRow('sales-shortlist', { display_name: 'Sales shortlist', investigation: 'c360', member_count: 4 }),
  wlRow('candidate-repos', { display_name: 'Candidate repos', entity_type: 'repo', member_count: 12 }),
  wlRow('old-postgres', { display_name: 'Old Postgres', member_count: 5 }),
  wlRow('hr-benches', { display_name: 'HR bench', investigation: 'hr' }),
  wlRow('suggested-to-data-expert', { display_name: 'suggested to data-expert', member_count: 1 }),
];

function stubBackend() {
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok(INVESTIGATIONS);
    let m = u.match(/^\/api\/investigations\/([^/?]+)\/members$/);
    if (m) return ok(MEMBERS[m[1]] || []);
    m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m && method === 'GET' && !['purposes', 'classifications'].includes(m[1])) {
      const inv = INVESTIGATIONS.find((i) => i.slug === m[1]);
      return ok({ ...inv, description: 'd', project_classification: 'StudyProject', purposes: [], visibility: 'public' });
    }
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' });
    if (u.startsWith('/api/work-lists/') && !u.startsWith('/api/work-lists/runs')) {
      const slug = decodeURIComponent(u.replace('/api/work-lists/', '').split('?')[0]);
      if (!slug) return ok(WORK_LISTS);
      const row = WORK_LISTS.find((w) => w.slug === slug);
      return ok({ ...row, members: Array.from({ length: row.member_count }, (_, i) => ({ entity_slug: `${slug}-${i}`, entity_type: row.entity_type, rationale: '' })) });
    }
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/analyses/facts')) return ok({ states: {}, subjects: {} });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u === '/api/db-servers/') return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules') || u.includes('/groups')) return ok([]);
    return ok({});
  };
}

async function setUp({ investigation = '', stage = 'scouting', open = null } = {}) {
  const { document, window } = makeDomEnvironment();
  stubBackend();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.getElementById('rail').appendChild(rail);
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = 'db';
  s.databases = [{ slug: 'crm_prod', display_name: 'crm_prod', disposition: 'undecided', group_slug: '', working_set_hidden: false }];
  s.databasesLoaded = true; s.projects = []; s.filesystems = [];
  s.groups = [];
  s.investigations = INVESTIGATIONS.map((i) => ({ ...i }));
  s.investigation = ''; s.workingSet = new Set(); s.scopeCount = null;
  s.selected = new Set(); s.selectMode = false; s.dispositionFacet = 'all'; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workLists = WORK_LISTS.map((w) => ({ ...w }));
  s.workListSlug = open; s.lastWorkListSlug = null; s.workListIndex = false;
  s.selectedSlug = 'crm_prod';
  s.stage = stage; s.subTab = 'questions'; s.lastRunStage = 'scouting';
  s.answers = new Map();
  app.renderIntentNav();
  if (investigation) await app.setInvestigation(investigation); else app.renderSidebar();
  app.renderTopBar();
  const inv = await import('/static/next/stages/investigation.js');
  return { document, window, app, s, inv };
}

const clickStage = async (d, stage) => {
  const b = d.querySelector(`#intent-nav button[data-stage="${stage}"]`);
  assert.ok(b, `nav offers ${stage}`);
  b.click();
  await tick(200);
};

/* ── §1 vocabulary ──────────────────────────────────────────────────────── */

test('the investigation page headings the scope "Scope · N", never "Members (N)"', async () => {
  const { document, s, inv } = await setUp({ stage: 'investigation' });
  inv.openInvestigationDetail('c360');
  await clickStage(document, 'investigation');
  const h = document.querySelector('#inv-scope h4');
  assert.ok(h, 'the Scope section exists');
  assert.equal(text(h), 'Scope · 3');
  assert.doesNotMatch(text(document.getElementById('content')), /Members \(/);
  assert.equal(s.stage, 'investigation');
});

test('the list of work lists opens with the one-sentence definition, and never says "working set"', async () => {
  const { document, s } = await setUp();
  s.workListIndex = true;
  document.querySelector('[data-act="worklists"]').click();
  await tick(100);
  const c = text(document.getElementById('content'));
  assert.ok(c.includes('An investigation says why, and its scope says which resources, of any kind; a work list is a bench: one kind of resource, laid out against one stage’s questions, so you can decide which belong in a scope.'), c.slice(0, 300));
  assert.doesNotMatch(document.body.textContent, /working set/i);
});

test('the empty list of work lists carries the definition too', async () => {
  const { document, s } = await setUp();
  s.workLists = [];
  document.querySelector('[data-act="worklists"]').click();
  await tick(100);
  assert.ok(document.querySelector('[data-wl-definition]'));
});

/* ── §1 the sidebar ─────────────────────────────────────────────────────── */

const sidebar = (d) => d.getElementById('sidebar');

test('sidebar, an investigation current: scope line, its lists, other lists folded, suggestions apart', async () => {
  const { document } = await setUp({ investigation: 'c360' });
  const sb = sidebar(document);
  assert.equal(text(sb.querySelector('[data-act="sidebar-scope"]')), 'Scope · 3', 'every kind in the scope is counted');
  assert.equal(text(sb.querySelector('[data-linked-lists-head]')), 'Work lists for Customer 360 · 2');
  const linkedRows = [...sb.querySelectorAll('button[data-worklist]')].filter((b) => !b.closest('details'));
  assert.deepEqual(linkedRows.map((b) => b.dataset.worklist), ['sales-databases', 'sales-shortlist']);
  const other = sb.querySelector('details[data-other-lists]');
  assert.equal(text(other.querySelector('summary')), 'Other work lists · 3');
  assert.equal(other.hasAttribute('open'), false, 'folded to one line');
  assert.deepEqual([...other.querySelectorAll('button[data-worklist]')].map((b) => b.dataset.worklist),
    ['candidate-repos', 'old-postgres', 'hr-benches']);
  const sug = sb.querySelector('details[data-suggestions]');
  assert.equal(text(sug.querySelector('summary')), 'Suggestions · 1');
  assert.match(text(sug), /inboxes, not work lists/);
  assert.ok(!other.contains(sug) && !sug.contains(other));
  assert.equal(sb.querySelectorAll('button[data-worklist="suggested-to-data-expert"]').length, 1);
  assert.ok(sug.querySelector('button[data-worklist="suggested-to-data-expert"]'), 'the inbox is in Suggestions only');
  assert.ok(!other.querySelector('button[data-worklist="suggested-to-data-expert"]'));
});

test('the Scope line opens the "In scope" chip', async () => {
  const { document, s } = await setUp({ investigation: 'c360' });
  sidebar(document).querySelector('[data-act="sidebar-scope"]').click();
  assert.equal(s.scope, 'working-set');
});

test('sidebar, none current: "Work lists · N" lists every bench; suggestions still a separate line', async () => {
  const { document } = await setUp();
  const sb = sidebar(document);
  assert.equal(text(sb.querySelector('[data-all-lists-head]')), 'Work lists · 5');
  assert.equal(sb.querySelector('[data-act="sidebar-scope"]'), null, 'no scope line without an investigation');
  assert.equal(sb.querySelector('details[data-other-lists]'), null);
  const benches = [...sb.querySelectorAll('button[data-worklist]')].filter((b) => !b.closest('details'));
  assert.equal(benches.length, 5);
  assert.ok(!benches.some((b) => b.dataset.worklist.startsWith('suggested-to-')));
  assert.equal(text(sb.querySelector('details[data-suggestions] summary')), 'Suggestions · 1');
});

test('counts follow the rows: another investigation current gives its own numbers', async () => {
  const { document } = await setUp({ investigation: 'hr' });
  const sb = sidebar(document);
  assert.equal(text(sb.querySelector('[data-act="sidebar-scope"]')), 'Scope · 1');
  assert.equal(text(sb.querySelector('[data-linked-lists-head]')), 'Work lists for Retire legacy HR · 1');
  assert.equal(text(sb.querySelector('details[data-other-lists] summary')), 'Other work lists · 4');
});

/* ── §5 a stage click while a work list is open ─────────────────────────── */

const RUN = [['discovery', 'Discovery'], ['assessment', 'Assessment'], ['analysis', 'Analysis'],
             ['curate', 'Curate'], ['enrichment', 'Enrichment'], ['scouting', 'Scouting']];

for (const [id, label] of RUN) {
  test(`run stage ${id}: the open list stays open and its title line says "${label}’s questions"`, async () => {
    const { document, s } = await setUp({ stage: id === 'scouting' ? 'discovery' : 'scouting', open: 'sales-databases' });
    document.querySelector('[data-act="worklists"]')?.focus?.();
    await clickStage(document, id);
    assert.equal(s.workListSlug, 'sales-databases', 'still open');
    const c = document.getElementById('content');
    assert.ok(c.querySelector('#wl-grid'), 'the bench is on screen');
    assert.equal(text(c.querySelector('[data-wl-stage-line]')), `${label}’s questions`);
    assert.match(text(c), /Sales databases 9 databases · /);
  });
}

for (const frame of ['investigation', 'understanding', 'automate']) {
  test(`${frame} (unlinked list): the click goes to the stage and the list closes into the back link`, async () => {
    const { document, s } = await setUp({ stage: 'assessment', open: 'old-postgres' });
    await clickStage(document, frame);
    assert.equal(s.stage, frame);
    assert.equal(s.workListSlug, null, 'the list closed');
    assert.equal(s.lastWorkListSlug, 'old-postgres');
    assert.equal(document.getElementById('content').querySelector('#wl-grid'), null, 'no bench under a frame stage');
    const back = document.querySelector('[data-act="back-to-matrix"]');
    assert.ok(back, 'the back link is offered');
    assert.match(text(back), /Old Postgres/);
    if (frame === 'investigation') assert.match(text(document.getElementById('content')), /Investigation/);
  });
}

test('the back link reopens the list on the last run stage, not on the frame stage', async () => {
  const { document, s } = await setUp({ stage: 'assessment', open: 'old-postgres' });
  await clickStage(document, 'assessment');
  await clickStage(document, 'understanding');
  document.querySelector('[data-act="back-to-matrix"]').click();
  await tick(200);
  assert.equal(s.workListSlug, 'old-postgres');
  assert.equal(s.stage, 'assessment');
  assert.ok(document.getElementById('content').querySelector('#wl-grid'));
});

test('opening a list from the sidebar while on a frame stage lands on a run stage, not an instant close', async () => {
  const { document, s } = await setUp({ stage: 'automate' });
  sidebar(document).querySelector('button[data-worklist="old-postgres"]').click();
  await tick(200);
  assert.equal(s.workListSlug, 'old-postgres');
  assert.equal(s.stage, 'scouting');
});

test('Investigation on a list linked to an investigation opens THAT investigation, Scope in view, though not current', async () => {
  const { document, window, s } = await setUp({ investigation: 'hr', stage: 'assessment', open: 'sales-databases' });
  let scrolled = 0;
  window.Element.prototype.scrollIntoView = function () { if (this.id === 'inv-scope') scrolled += 1; };
  await clickStage(document, 'investigation');
  assert.equal(s.investigation, 'hr', 'the current investigation is left alone');
  assert.equal(s.workListSlug, null);
  assert.equal(s.lastWorkListSlug, 'sales-databases');
  assert.equal(text(document.querySelector('#inv-scope h4')), 'Scope · 3', 'c360’s own scope, not hr’s');
  assert.match(text(document.getElementById('content')), /Customer 360/);
  assert.equal(scrolled, 1, 'the Scope section was scrolled into view');
  assert.ok(document.querySelector('[data-act="back-to-matrix"]'), 'one click back to the list');
});
