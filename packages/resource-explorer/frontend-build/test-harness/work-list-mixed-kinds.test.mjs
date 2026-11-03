/** Behavior: a work list of more than one kind (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §5, §6.3).
 *
 *  One column set per view: with no chip pressed the bench shows the questions every kind present carries, each
 *  row answered by its own kind's analyses (facts read once per kind); a chip per kind ("2 repos · 1 database")
 *  narrows the rows to that kind and widens the columns to its full stage questions. Runs stay per kind: "Run per
 *  kind…" asks one analysis per kind and says, per kind, what ran and what did not.
 *
 *  Real app.js and worklist.js over the shipped index.html; only fetch is stubbed.
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
const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

// Q-0011 is asked of every kind, answered by each kind's own analysis; Q-0001 only of repos, Q-0050 only of databases.
const QUESTIONS = {
  repo: [{ id: 'Q-0011', question: 'Which resources overlap with this one?', kind: 'analysis', analysis_ids: ['repo_overlap'] },
    { id: 'Q-0001', question: 'Is it maintained?', kind: 'analysis', analysis_ids: ['repository_health'] }],
  database: [{ id: 'Q-0011', question: 'Which resources overlap with this one?', kind: 'analysis', analysis_ids: ['db_overlap'] },
    { id: 'Q-0050', question: 'How many tables?', kind: 'analysis', analysis_ids: ['schema_inventory'] }],
};
const MEMBERS = [{ entity_slug: 'r1', entity_type: 'repo' }, { entity_slug: 'r2', entity_type: 'repo' },
  { entity_slug: 'd1', entity_type: 'database' }];

let CALLS; let GETS;

function stubBackend() {
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method !== 'GET') CALLS.push({ method, url: u, body }); else GETS.push(u);
    if (u === '/api/work-lists/mix/runs/per-kind') {
      return ok({ runs: [{ entity_type: 'repo', analysis_id: body.analyses.repo, set_id: 'set-r', runs: [{}, {}], not_permitted: [] }],
        not_run: [{ entity_type: 'database', count: 1, words: 'no analysis chosen for the 1 database member(s)' }] });
    }
    if (u.includes('/api/work-lists/runs/sets/')) {
      return ok({ counts: { succeeded: 2 }, finished: 2, total: 2, complete: true, unrecognised_states: [], runs: [] });
    }
    if (u.startsWith('/api/work-lists/') && !u.startsWith('/api/work-lists/runs')) {
      const slug = decodeURIComponent(u.replace('/api/work-lists/', '').split('?')[0]);
      const lists = {
        mix: { slug: 'mix', display_name: 'Order system', entity_type: 'mixed', stored_entity_type: 'mixed',
          kinds: { repo: 2, database: 1 }, investigation: '', derived_from: '', egeria_guid: '', filter: null,
          members: MEMBERS.map((m) => ({ ...m, rationale: '' })) },
        dbs: { slug: 'dbs', display_name: 'Databases', entity_type: 'database', stored_entity_type: 'database',
          kinds: { database: 1 }, investigation: '', derived_from: '', egeria_guid: '', filter: null,
          members: [{ entity_slug: 'd1', entity_type: 'database', rationale: '' }] },
      };
      if (!slug) return ok(Object.values(lists).map((w) => ({ ...w, member_count: w.members.length })));
      return ok(lists[slug]);
    }
    if (u.includes('/api/analyses/facts')) {
      const qs = new URLSearchParams(u.split('?')[1]);
      const slugs = qs.get('slugs').split(',');
      const ids = (qs.get('analysis_ids') || '').split(',');
      if (qs.get('states_only')) {
        return ok({ states: Object.fromEntries(slugs.map((s) => [s, Object.fromEntries(ids.map((a) => [a,
          { has_results: true, measured_at: '2026-10-20T00:00:00Z' }]))])) });
      }
      return ok({ subjects: Object.fromEntries(slugs.map((s) => [s, ids.map((a) => ({ analysis_id: a, state: 'measured',
        headline: `${a} for ${s}`, value: {}, as_of: '2026-10-20T00:00:00Z' }))])) });
    }
    let m = u.match(/^\/api\/analyses\/(repo|database|filesystem)(\?|$)/);
    if (m) return ok(m[1] === 'repo' ? [{ id: 'repository_health', name: 'Repository health' }] : [{ id: 'schema_inventory', name: 'Schema inventory' }]);
    if (/\/api\/projects\/[^/]+\/scouting-questions/.test(u)) return ok({ questions: QUESTIONS.repo });
    if (/\/api\/databases\/[^/]+\/questions/.test(u)) return ok({ questions: QUESTIONS.database });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/api/investigations/')) return ok([]);
    if (u === '/api/db-servers/') return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules') || u.includes('/groups')) return ok([]);
    return ok({});
  };
}

async function setUp(open = 'mix') {
  CALLS = []; GETS = [];
  const { document, window } = makeDomEnvironment();
  stubBackend();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.getElementById('rail').appendChild(rail);
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = 'repo';
  s.projects = [{ slug: 'r1', display_name: 'r1' }, { slug: 'r2', display_name: 'r2' }];
  s.databases = [{ slug: 'd1', display_name: 'd1', disposition: 'undecided', group_slug: '', working_set_hidden: false }];
  s.databasesLoaded = true; s.filesystems = []; s.groups = []; s.investigations = [];
  s.investigation = ''; s.workingSet = new Set(); s.scopeCount = null;
  s.selected = new Set(); s.selectMode = false; s.dispositionFacet = 'all'; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workLists = [{ slug: open, display_name: open, entity_type: open === 'mix' ? 'mixed' : 'database', member_count: 3 }];
  s.workListSlug = open; s.lastWorkListSlug = null; s.workListIndex = false; s.workListIndexNote = '';
  s.selectedSlug = 'r1';
  s.stage = 'scouting'; s.subTab = 'questions'; s.lastRunStage = 'scouting';
  s.answers = new Map();
  app.renderIntentNav();
  app.renderSidebar();
  app.renderTopBar();
  document.querySelector('#intent-nav button[data-stage="scouting"]').click();
  await tick(300);
  return { document, app, s };
}

const columns = (d) => [...d.querySelectorAll('#wl-grid th[data-col]')].map((th) => th.getAttribute('title'));
const rows = (d) => [...d.querySelectorAll('#wl-grid button[data-res]')].map((b) => b.dataset.res).sort();

test('winnow.js sharedColumns keeps the questions every kind carries, each kind answering with its own analyses', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const w = await import('/static/next/winnow.js');
  const shared = w.sharedColumns(new Map(Object.entries(QUESTIONS)));
  assert.deepEqual(shared.map((q) => q.id), ['Q-0011']);
  assert.deepEqual(shared[0].byKind.database.analysis_ids, ['db_overlap']);
  assert.deepEqual(shared[0].analysis_ids, ['repo_overlap', 'db_overlap']);
  assert.deepEqual(w.criterionFor({ analysis_ids: ['repo_overlap', 'db_overlap'],
    analysis_ids_by_kind: { database: ['db_overlap'] } }, 'database').analysis_ids, ['db_overlap']);
});

test('a list of more than one kind shows a chip per kind and only the shared columns', async () => {
  const { document } = await setUp();
  const chips = [...document.querySelectorAll('[data-kind-chip]')].map(text);
  assert.deepEqual(chips, ['2 repos', '1 database']);
  assert.deepEqual(columns(document), ['Which resources overlap with this one?']);
  assert.deepEqual(rows(document), ['d1', 'r1', 'r2']);
  assert.ok(text(document.querySelector('[data-columns-note]')).startsWith('1 question(s) every kind here shares; 2 for repos, 2 for databases'));
});

test('facts are read once per kind, each for that kind\'s own analyses', async () => {
  const { document } = await setUp();
  const facts = GETS.filter((u) => u.includes('/api/analyses/facts') && !u.includes('states_only'));
  const repo = facts.find((u) => u.includes('entity_type=repo'));
  const db = facts.find((u) => u.includes('entity_type=database'));
  assert.ok(repo && repo.includes('slugs=r1%2Cr2') && repo.includes('analysis_ids=repo_overlap'), repo);
  assert.ok(db && db.includes('slugs=d1') && db.includes('analysis_ids=db_overlap'), db);
  assert.ok(!facts.some((u) => u.includes('entity_type=mixed')), 'never asks the server about a kind called mixed');
  // Each row's shared cell is answered from its own kind's fact.
  const cell = document.querySelector('#wl-grid button[data-cell="d1"]');
  assert.ok(cell && /answered/i.test(cell.getAttribute('title')), cell?.getAttribute('title'));
});

test('pressing a kind narrows the rows to it and widens the columns to all its questions; pressing again goes back', async () => {
  const { document } = await setUp();
  document.querySelector('[data-kind-chip="database"]').click();
  await tick(250);
  assert.deepEqual(rows(document), ['d1']);
  assert.deepEqual(columns(document), ['Which resources overlap with this one?', 'How many tables?']);
  assert.equal(document.querySelector('[data-kind-chip="database"]').getAttribute('aria-pressed'), 'true');
  assert.ok(document.querySelector('#wl-actions #wl-analysis'), 'one kind in view: the one-kind run menu');
  document.querySelector('[data-kind-chip="database"]').click();
  await tick(250);
  assert.deepEqual(rows(document), ['d1', 'r1', 'r2']);
  assert.deepEqual(columns(document), ['Which resources overlap with this one?']);
});

test('runs stay per kind: one analysis per kind, and a kind left out is said, not skipped', async () => {
  const { document } = await setUp();
  assert.ok(!document.querySelector('#wl-actions #wl-analysis'), 'no single-kind menu in the shared view');
  document.querySelector('[data-act="run-per-kind"]').click();
  await tick(60);
  const plan = text(document.querySelector('[data-per-kind-plan]'));
  assert.ok(plan.includes('2 repos') && plan.includes('1 database'), plan);
  const sel = document.querySelector('[data-per-kind="repo"]');
  sel.value = 'repository_health';
  assert.equal(CALLS.filter((c) => c.url.includes('/runs/')).length, 0, 'nothing runs until confirmed');
  document.querySelector('#wl-detail-body [data-act="go"]').click();
  await tick(150);
  const call = CALLS.find((c) => c.url === '/api/work-lists/mix/runs/per-kind');
  assert.deepEqual(call.body, { analyses: { repo: 'repository_health' } });
  const runs = document.querySelector('[data-per-kind-runs]');
  assert.ok(text(runs.querySelector('[data-run-kind="repo"]')).startsWith('repos: repository_health · 2 run(s) · batch set-r'));
  assert.equal(text(runs.querySelector('[data-run-kind="database"]')),
    'databases: not run · no analysis chosen for the 1 database member(s)');
});

test('a list of one kind has no chips and reads exactly as before', async () => {
  const { document } = await setUp('dbs');
  assert.equal(document.querySelectorAll('[data-kind-chip]').length, 0);
  assert.deepEqual(columns(document), ['Which resources overlap with this one?', 'How many tables?']);
  assert.ok(document.querySelector('#wl-actions #wl-analysis'));
});
