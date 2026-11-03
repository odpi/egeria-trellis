/** Behavior: winnowing a work list (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §2, §6.2, §7.1).
 *
 *  Every column with an analysis is a filter; a filter splits the rows into kept, dropped and could-not-check,
 *  and a could-not-check row says why on the row itself. "Save as a shortlist" sends the criteria with all three
 *  buckets; a filtered shortlist says "kept N of M on <criteria> as of <date>". It re-applies itself when an answer
 *  behind its filter is newer than the cut (owner, 2026-10-28: automatic), records it and says "re-applied
 *  automatically <time>: +N / −M"; "Re-apply now" shows the diff first. A member that can no longer be checked is
 *  held, never silently dropped.
 *
 *  Pure winnow.js first, then the real app.js and worklist.js over the shipped index.html; only fetch is stubbed.
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

const NOT_MEASURED = "Not measured: no survey has read this database's schema yet.";
const TYPES = { entity_type: 'database', unknown: [], analyses: { schema_inventory: { declared: true, result_kind: 'list',
  measures: [{ name: 'relations', unit: 'tables, views and foreign tables', value_field: 'relation_count', as_of: 'surveyed_at' }],
  absence: { not_measured: NOT_MEASURED, not_permitted: 'np', not_applicable: 'na', measured_nothing: 'none' } } } };
const Q = { id: 'Q-0010', question: 'How big is it?', kind: 'analysis', analysis_ids: ['schema_inventory'] };

const fact = (n, at = '2026-10-20T00:00:00Z') => [{ analysis_id: 'schema_inventory', state: 'measured',
  headline: `${n} relations`, value: { relation_count: n }, as_of: at, last_run_at: at }];

let LISTS; let FACTS; let CALLS;

function reset() {
  CALLS = [];
  LISTS = {
    sales: { slug: 'sales', display_name: 'Sales databases', entity_type: 'database', investigation: '', derived_from: '',
      egeria_guid: '', filter: null, members: ['a', 'b', 'c', 'd'].map((s) => ({ entity_slug: s, entity_type: 'database', rationale: '' })) },
  };
  FACTS = {
    a: fact(20), b: fact(5),
    c: [{ analysis_id: 'schema_inventory', state: 'never_run', headline: '' }],
  };
}

function stubBackend() {
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method !== 'GET') CALLS.push({ method, url: u, body });
    if (u.startsWith('/api/work-lists/column-types')) return ok(TYPES);
    let m = u.match(/^\/api\/work-lists\/([^/?]+)\/promote$/);
    if (m) {
      const parent = LISTS[m[1]];
      LISTS.short = { slug: 'short', display_name: body.display_name, entity_type: 'database', investigation: '',
        derived_from: m[1], egeria_guid: '', filter: { ...body.filter, parent_slug: m[1] },
        members: parent.members.filter((x) => body.survivors.includes(x.entity_slug)) };
      return ok(LISTS.short);
    }
    m = u.match(/^\/api\/work-lists\/([^/?]+)\/reapply$/);
    if (m) {
      // Plays WorkLists.reapply: kept become members, a member now could-not-check is held, the re-apply recorded.
      const wl = LISTS[m[1]];
      const before = wl.members.map((x) => x.entity_slug);
      const unchecked = Object.fromEntries(body.filter.could_not_check.map((x) => [x.entity_slug, x.words]));
      const held = before.filter((s) => !body.filter.kept.includes(s) && unchecked[s])
        .map((s) => ({ entity_slug: s, entity_type: 'database', words: unchecked[s] }));
      const joined = body.filter.kept.filter((s) => !before.includes(s));
      const dropped = before.filter((s) => !body.filter.kept.includes(s) && !unchecked[s]);
      const entry = { at: new Date().toISOString(), automatic: body.automatic, applied_at: body.filter.applied_at,
        answers_as_of: body.filter.newest_answer, joined, dropped, held, could_not_check: body.filter.could_not_check.length };
      wl.filter = { ...body.filter, reapplies: [...(wl.filter.reapplies || []), entry] };
      wl.members = [...body.filter.kept, ...held.map((h) => h.entity_slug)]
        .map((s) => ({ entity_slug: s, entity_type: 'database', rationale: '' }));
      return ok({ automatic: body.automatic, joined, dropped, held, reapply: entry, work_list: wl });
    }
    if (u.startsWith('/api/work-lists/') && !u.startsWith('/api/work-lists/runs')) {
      const slug = decodeURIComponent(u.replace('/api/work-lists/', '').split('?')[0]);
      if (!slug) return ok(Object.values(LISTS).map((w) => ({ ...w, member_count: w.members.length })));
      return ok(JSON.parse(JSON.stringify(LISTS[slug])));
    }
    if (u.includes('/api/analyses/facts')) {
      const qs = new URLSearchParams(u.split('?')[1]);
      const slugs = qs.get('slugs').split(',');
      if (qs.get('states_only')) {
        return ok({ states: Object.fromEntries(slugs.map((s) => [s, { schema_inventory: FACTS[s]?.[0]?.state === 'measured'
          ? { has_results: true, measured_at: FACTS[s][0].as_of } : { has_results: false, certain_never_run: s === 'c' } }])) });
      }
      return ok({ subjects: Object.fromEntries(slugs.filter((s) => FACTS[s]).map((s) => [s, FACTS[s]])),
        unreadable: slugs.includes('d') ? { d: 'the registry read timed out' } : {} });
    }
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/questions')) return ok({ questions: [Q] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/api/investigations/')) return ok([]);
    if (u === '/api/db-servers/') return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules') || u.includes('/groups')) return ok([]);
    return ok({});
  };
}

async function setUp(open = 'sales') {
  reset();
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
  const wl = await import('/static/next/worklist.js');
  const s = app.state;
  s.resourceType = 'db';
  s.databases = ['a', 'b', 'c', 'd'].map((slug) => ({ slug, display_name: slug, disposition: 'undecided', group_slug: '', working_set_hidden: false }));
  s.databasesLoaded = true; s.projects = []; s.filesystems = []; s.groups = []; s.investigations = [];
  s.investigation = ''; s.workingSet = new Set(); s.scopeCount = null;
  s.selected = new Set(); s.selectMode = false; s.dispositionFacet = 'all'; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workLists = Object.values(LISTS).map((w) => ({ ...w, member_count: w.members.length }));
  s.workListSlug = open; s.lastWorkListSlug = null; s.workListIndex = false; s.workListIndexNote = '';
  s.selectedSlug = 'a';
  s.stage = 'scouting'; s.subTab = 'questions'; s.lastRunStage = 'scouting';
  s.answers = new Map();
  app.renderIntentNav();
  app.renderSidebar();
  app.renderTopBar();
  document.querySelector('#intent-nav button[data-stage="scouting"]').click();
  await tick(250);
  return { document, window, app, s, wl };
}

async function addMeasureFilter(document, value = '10') {
  document.querySelector('[data-act="winnow-open"]').click();
  await tick(80);
  const field = document.querySelector('[data-winnow="field"]');
  field.value = 'measure:schema_inventory:relation_count';
  field.dispatchEvent(new document.defaultView.Event('change'));
  await tick(20);
  const op = document.querySelector('[data-winnow="op"]');
  op.value = '>=';
  op.dispatchEvent(new document.defaultView.Event('change'));
  await tick(20);
  document.querySelector('[data-winnow="value"]').value = value;
  document.querySelector('[data-act="winnow-add"]').click();
  await tick(40);
}

const bucketOf = (document, slug) => document.querySelector(`button[data-res="${slug}"]`)?.parentElement
  ?.querySelector('[data-bucket]')?.dataset.bucket || '';

/* ── winnow.js, pure ────────────────────────────────────────────────────── */

test('winnow.js: three buckets, and one known failure drops a row even when another criterion cannot be checked', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const w = await import('/static/next/winnow.js');
  const crit = [{ question_id: 'Q-1', field: 'measure:x:n', op: '>=', value: 10, analysis_ids: ['x'] },
    { question_id: 'Q-2', field: 'state', op: 'is', value: 'answered', analysis_ids: ['y'] }];
  const cells = { a: [{ present: true, value: 12, asOf: '2026-10-01' }, { present: true, value: 'answered', asOf: '2026-09-01' }],
    b: [{ present: true, value: 3 }, { present: false, reason: 'unrun', words: 'never measured' }],
    c: [{ present: true, value: 30 }, { present: false, reason: 'unrun', words: 'never measured' }] };
  const read = (c, m) => cells[m.entity_slug][crit.indexOf(c)];
  const out = w.applyFilter(crit, ['a', 'b', 'c'].map((entity_slug) => ({ entity_slug })), read);
  assert.deepEqual(out.kept, ['a']);
  assert.deepEqual(out.dropped.map((d) => d.entity_slug), ['b']);
  assert.deepEqual(out.couldNotCheck.map((u) => [u.entity_slug, u.words]), [['c', 'never measured']]);
  assert.equal(out.oldestAnswer, '2026-09-01');
});

test('winnow.js: an incomparable value is could-not-check, never a silent drop', async () => {
  makeDomEnvironment();
  ensureLoaderRegistered();
  const w = await import('/static/next/winnow.js');
  assert.deepEqual(w.passes({ field: 'measure:x:v', op: 'older_than_days', value: 30 }, 'soon'),
    { present: false, reason: 'not-a-date', words: 'soon is not a date, so its age cannot be compared' });
  assert.equal(w.passes({ field: 'measure:x:v', op: 'older_than_days', value: 30 }, '2026-01-01', Date.parse('2026-10-28')), true);
  assert.equal(w.passes({ field: 'headline', op: 'lacks', value: 'Orders' }, '3 orders tables'), false);
  assert.equal(w.resolvePath({ value: { roles: [1, 2, 3] } }, 'roles[]'), 3);
  assert.equal(w.resolvePath({ value: { wal: { failed: 0 } } }, 'wal.failed'), 0);
  assert.deepEqual(w.reapplyDiff(['a', 'b', 'd'], ['b', 'c'], ['d']), { wouldDrop: ['a'], wouldJoin: ['c'], wouldHold: ['d'] });
});

/* ── The bench ──────────────────────────────────────────────────────────── */

test('a measure filter splits the rows into kept, dropped and could-not-check, each saying why', async () => {
  const { document } = await setUp();
  await addMeasureFilter(document);
  assert.equal(bucketOf(document, 'a'), 'kept');
  assert.equal(bucketOf(document, 'b'), 'dropped');
  assert.equal(bucketOf(document, 'c'), 'could-not-check');
  assert.equal(bucketOf(document, 'd'), 'could-not-check');
  const unchecked = text(document.querySelector('[data-winnow-unchecked]'));
  assert.ok(unchecked.includes(`c · ${NOT_MEASURED}`), unchecked);          // the declared absence sentence
  assert.ok(unchecked.includes('d · could not read · the registry read timed out'), unchecked);
  const summary = text(document.querySelector('[data-winnow-summary]'));
  assert.ok(summary.startsWith('1 kept · 1 dropped · 2 could not check · 4 all rows'), summary);
  assert.equal(text(document.querySelector('[data-criterion="0"]')).replace('×', '').trim(),
    'Q-0010 · relation_count at least 10');
});

test('a filter on an analysis never run is a run first, offered for exactly those members', async () => {
  const { document } = await setUp();
  await addMeasureFilter(document);
  const cost = document.querySelector('[data-winnow-cost]');
  assert.ok(text(cost).startsWith('1 member has no answer to Q-0010 · a filter on it is a run first'), text(cost));
  const run = cost.querySelector('[data-act="winnow-run"]');
  assert.equal(run.dataset.slugs, 'c');
  run.click();
  await tick(40);
  assert.ok(text(document.getElementById('wl-detail-body')).includes('runs schema_inventory on 1 resource(s)'));
  assert.equal(CALLS.filter((c) => c.url.includes('/runs/batch')).length, 0, 'nothing runs until it is confirmed');
});

test('showing one bucket narrows the rows to it', async () => {
  const { document } = await setUp();
  await addMeasureFilter(document);
  document.querySelector('[data-show="could-not-check"]').click();
  await tick(30);
  const shown = [...document.querySelectorAll('button[data-res]')].map((b) => b.dataset.res).sort();
  assert.deepEqual(shown, ['c', 'd']);
});

test('save sends the criteria with all three buckets, and the shortlist says what it was cut on', async () => {
  const { document } = await setUp();
  await addMeasureFilter(document);
  document.querySelector('[data-act="winnow-save"]').click();
  await tick(40);
  document.querySelector('[data-prompt-yes]').click();
  await tick(250);
  const promote = CALLS.find((c) => c.url.endsWith('/promote'));
  assert.ok(promote, 'a promote was sent');
  const f = promote.body.filter;
  assert.deepEqual(promote.body.survivors, ['a']);
  assert.deepEqual([f.kept, f.dropped, f.could_not_check.map((u) => u.entity_slug)], [['a'], ['b'], ['c', 'd']]);
  assert.equal(f.parent_slug, 'sales');
  assert.equal(f.parent_count, 4);
  assert.equal(f.oldest_answer, '2026-10-20T00:00:00Z');
  assert.ok(f.could_not_check.every((u) => u.words), 'each with its sentence');
  const line = text(document.querySelector('[data-shortlist-filter]'));
  assert.ok(line.startsWith('kept 1 of 4 on Q-0010 · relation_count at least 10 as of '), line);
  assert.ok(line.includes('2 could not be checked'), line);
});

test('re-apply shows the diff on the parent\'s current answers and changes nothing until pressed', async () => {
  reset();
  const { document } = await setUp('sales');
  LISTS.short = { slug: 'short', display_name: 'Big ones', entity_type: 'database', investigation: '', derived_from: 'sales',
    egeria_guid: '', members: [{ entity_slug: 'a', entity_type: 'database', rationale: '' }],
    filter: { criteria: [{ question_id: 'Q-0010', question: 'How big is it?', analysis_ids: ['schema_inventory'],
      field: 'measure:schema_inventory:relation_count', op: '>=', value: 10 }],
    applied_at: '2026-10-21T00:00:00Z', oldest_answer: '', parent_slug: 'sales', parent_count: 4,
    kept: ['a'], dropped: ['b'], could_not_check: [{ entity_slug: 'c', words: NOT_MEASURED }, { entity_slug: 'd', words: 'x' }] } };
  const wlmod = await import('/static/next/worklist.js');
  await wlmod.openWorkList(wlmod.grid.ctx, 'short');
  await tick(150);
  assert.ok(text(document.querySelector('[data-shortlist-filter]'))
    .startsWith('kept 1 of 4 on Q-0010 · relation_count at least 10 as of 2026-10-21 · 2 could not be checked'));
  // Fresher answers: a shrank, b grew.
  FACTS.a = fact(3, '2026-10-27T00:00:00Z');
  FACTS.b = fact(40, '2026-10-27T00:00:00Z');
  document.querySelector('[data-act="reapply"]').click();
  await tick(150);
  const diff = text(document.querySelector('[data-reapply-diff]'));
  assert.ok(diff.includes('1 would now drop: a'), diff);
  assert.ok(diff.includes('1 would now join: b'), diff);
  assert.ok(diff.includes('2 could not be checked'), diff);
  assert.equal(CALLS.filter((c) => c.url.endsWith('/reapply')).length, 0, 'nothing changed before the press');
  document.querySelector('#wl-detail-body [data-act="go"]').click();
  await tick(200);
  const call = CALLS.find((c) => c.url.endsWith('/reapply'));
  assert.deepEqual(call.body.filter.kept, ['b']);
  assert.equal(call.body.automatic, false, 'the press is recorded as a press');
  assert.ok(text(document.getElementById('wl-note')).startsWith('Re-applied: 1 joined (b), 1 dropped (a).'));
  assert.ok(text(document.querySelector('[data-shortlist-reapplied] summary')).startsWith('re-applied '));
});

function filteredShortlist(appliedAt) {
  LISTS.short = { slug: 'short', display_name: 'Big ones', entity_type: 'database', investigation: '', derived_from: 'sales',
    egeria_guid: '', members: [{ entity_slug: 'a', entity_type: 'database', rationale: '' }],
    filter: { criteria: [{ question_id: 'Q-0010', question: 'How big is it?', analysis_ids: ['schema_inventory'],
      field: 'measure:schema_inventory:relation_count', op: '>=', value: 10 }],
    applied_at: appliedAt, oldest_answer: '', parent_slug: 'sales', parent_count: 4,
    kept: ['a'], dropped: ['b'], could_not_check: [{ entity_slug: 'c', words: NOT_MEASURED }, { entity_slug: 'd', words: 'x' }] } };
}

test('a shortlist whose answers refreshed re-applies itself on open, records it, and shows +N / −M with the diff', async () => {
  const { document } = await setUp('sales');
  filteredShortlist('2026-10-21T00:00:00Z');
  FACTS.a = fact(3, '2026-10-27T00:00:00Z');          // a's answer refreshed after the cut, and now fails
  FACTS.b = fact(40, '2026-10-27T00:00:00Z');
  const wlmod = await import('/static/next/worklist.js');
  await wlmod.openWorkList(wlmod.grid.ctx, 'short');
  await tick(400);
  const calls = CALLS.filter((c) => c.url.endsWith('/reapply'));
  assert.equal(calls.length, 1, 'exactly one automatic re-apply');
  assert.equal(calls[0].body.automatic, true);
  assert.equal(calls[0].body.filter.newest_answer, '2026-10-27T00:00:00Z');
  const summary = text(document.querySelector('[data-shortlist-reapplied] summary'));
  assert.ok(/^re-applied automatically .*: \+1 \/ −1$/.test(summary), summary);
  const record = text(document.querySelector('[data-reapply-record]'));
  assert.ok(record.includes('answers as of 2026-10-27T00:00') && record.includes('joined: b') && record.includes('dropped: a'), record);
});

test('nothing newer than the cut: no automatic re-apply', async () => {
  const { document } = await setUp('sales');
  filteredShortlist('2026-10-25T00:00:00Z');           // the answers (2026-10-20) are older than the cut
  const wlmod = await import('/static/next/worklist.js');
  await wlmod.openWorkList(wlmod.grid.ctx, 'short');
  await tick(400);
  assert.equal(CALLS.filter((c) => c.url.endsWith('/reapply')).length, 0);
  assert.equal(document.querySelector('[data-shortlist-reapplied]'), null);
});

test('a member that can no longer be checked is held on an automatic re-apply, never silently dropped', async () => {
  const { document } = await setUp('sales');
  filteredShortlist('2026-10-21T00:00:00Z');
  FACTS.a = [{ analysis_id: 'schema_inventory', state: 'never_run', headline: '' }];   // a: no answer now
  FACTS.b = fact(40, '2026-10-27T00:00:00Z');
  const wlmod = await import('/static/next/worklist.js');
  await wlmod.openWorkList(wlmod.grid.ctx, 'short');
  await tick(400);
  const call = CALLS.find((c) => c.url.endsWith('/reapply'));
  assert.ok(call.body.filter.could_not_check.some((u) => u.entity_slug === 'a'));
  const summary = text(document.querySelector('[data-shortlist-reapplied] summary'));
  assert.ok(summary.endsWith('+1 / −0 · 1 held, could not be checked'), summary);
  assert.ok(text(document.querySelector('[data-reapply-record]')).includes(`held: a · ${NOT_MEASURED}`));
  assert.deepEqual(wlmod.grid.workList.members.map((m) => m.entity_slug).sort(), ['a', 'b'], 'a stays a member');
});
