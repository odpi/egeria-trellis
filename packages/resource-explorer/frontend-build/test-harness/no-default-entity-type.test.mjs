/** No resource kind is ever defaulted to 'repo'.
 *
 *  WHY THIS EXISTS: `entityType = 'repo'` defaults in re-api.js caused the same bug
 *  three times in one day (Enrichment evidence rail, native surveys, and a database
 *  work list whose Schema Inventory columns failed with "Project 'X' not found").
 *  A caller that forgets the kind now THROWS, naming the helper.
 *
 *  Three layers:
 *   1. every helper that needs a kind throws when it is omitted (one test each);
 *   2. routing-level: open a work list the way a person does and watch which
 *      endpoint the columns and the Run-across actually hit -- database list reads
 *      the database route (the Dan bug), repo list still reads the repo route;
 *   3. a work list that arrives with no entity_type fails loudly, not as 'repo'.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

// ── 1. direct throw tests, one per helper ─────────────────────────────────
const S = 'some-slug';
// [helper name, how to call it WITHOUT a kind, parameter name]
const CASES = [
  ['getQuestions', (a) => a.getQuestions(S, { phase: 'scouting' })],
  ['getAnswer', (a) => a.getAnswer(S, 'q?')],
  ['saveEnrichmentField', (a) => a.saveEnrichmentField(S, 'owner', { value: 'x' })],
  ['getJournal', (a) => a.getJournal(S)],
  ['writeJournal', (a) => a.writeJournal(S, 'body')],
  ['assignGroup', (a) => a.assignGroup(S, 'grp'), 'resourceType'],
  ['ask', (a) => a.ask('hello', { resourceSlug: S })],
  ['submitAnswerFeedback', (a) => a.submitAnswerFeedback({ slug: S, question: 'q', verdict: 'up' })],
  ['askStream', async (a) => { for await (const _e of a.askStream('hello', { resourceSlug: S })) { void _e; } }],
  ['getSurveyCandidates', (a) => a.getSurveyCandidates(S, { phase: 'scouting' })],
  ['getNativeSurveys', (a) => a.getNativeSurveys(S)],
  ['runNativeSurvey', (a) => a.runNativeSurvey(S, 'qn')],
  ['refreshNativeSurveys', (a) => a.refreshNativeSurveys(S)],
  ['getNativeSurveyReport', (a) => a.getNativeSurveyReport(S, 'guid')],
  ['runSurveyDefinition', (a) => a.runSurveyDefinition(S, 'ref')],
  ['getSurveyDashboards', (a) => a.getSurveyDashboards(S, 'scouting')],
  ['listSurveyResultBoards', (a) => a.listSurveyResultBoards(S, 'scouting')],
  ['getAnalysisTrend', (a) => a.getAnalysisTrend(S, 'aid', 'm')],
  ['getMeasurements', (a) => a.getMeasurements(S, 'aid')],
  ['getAnalysesIndex', (a) => a.getAnalysesIndex(S, '')],
  ['getMembers', (a) => a.getMembers(S, 'aid', {})],
  ['promoteMembers', (a) => a.promoteMembers(S, 'aid', { action: 'journal', members: ['m'] })],
  ['getMemberChildren', (a) => a.getMemberChildren(S, 'aid', 'k', {})],
  ['createWorkList', (a) => a.createWorkList('name', [S])],
  ['enqueueBatch', (a) => a.enqueueBatch('aid', [S], 'wl')],
  ['getBulkStates', (a) => a.getBulkStates([S], ['aid'])],
  ['getBulkFacts', (a) => a.getBulkFacts([S], ['aid'])],
  ['runAnalysis', (a) => a.runAnalysis(S, 'aid')],
  ['listRecords', (a) => a.listRecords(S)],
  ['actOnRecord', (a) => a.actOnRecord(S, 'id', { action: 'journal' })],
  ['listQuestionCatalog', (a) => a.listQuestionCatalog(), 'resourceType'],
];

for (const [name, call, param = 'entityType'] of CASES) {
  test(`${name} throws "${name}: ${param} is required" when the kind is omitted`, async () => {
    makeDomEnvironment();
    ensureLoaderRegistered();
    let fetched = false;
    globalThis.fetch = async () => { fetched = true; return { ok: true, status: 200, json: async () => ({}) }; };
    const api = await import('/static/re-api.js');
    await assert.rejects(
      async () => { await call(api); },
      (err) => { assert.equal(err.message, `${name}: ${param} is required`); return true; },
    );
    assert.equal(fetched, false, `${name} must fail before any request is sent`);
  });
}

test('every helper in the table is exported, and getResourceFacts (dead, defaulted) is gone', async () => {
  makeDomEnvironment(); ensureLoaderRegistered();
  const api = await import('/static/re-api.js');
  for (const [name] of CASES) assert.equal(typeof api[name], 'function', name);
  assert.equal(api.getResourceFacts, undefined);
});

test('a helper still works when a kind IS given (the guard is not a blanket refusal)', async () => {
  makeDomEnvironment(); ensureLoaderRegistered();
  const seen = [];
  globalThis.fetch = async (u) => { seen.push(String(u)); return { ok: true, status: 200, json: async () => ({ questions: [] }) }; };
  const api = await import('/static/re-api.js');
  await api.getQuestions('mydb', { phase: 'scouting', entityType: 'database' });
  assert.ok(seen[0].startsWith('/api/databases/mydb/questions'), seen[0]);
});

test('the four helpers that used to swallow the kind now SEND it', async () => {
  makeDomEnvironment(); ensureLoaderRegistered();
  const seen = [];
  globalThis.fetch = async (u, o) => { seen.push([String(u), o && o.method]); return { ok: true, status: 200, json: async () => ({}) }; };
  const api = await import('/static/re-api.js');
  await api.getAnalysisTrend('s', 'a', 'm', 'database');
  await api.getMembers('s', 'a', {}, 'database');
  await api.getMemberChildren('s', 'a', 'k', {}, 'database');
  await api.promoteMembers('s', 'a', { action: 'journal', members: ['x'] }, 'database');
  assert.equal(seen.length, 4);
  for (const [u] of seen) assert.match(u, /[?&]entity_type=database/, u);
});

// ── 2/3. routing level: the work-list pane ────────────────────────────────
function stubServer(calls) {
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    const ok = (body) => ({ ok: true, status: 200, statusText: 'OK', json: async () => body });
    const q = { question: 'What tables does it hold?', kind: 'direct', analysis_ids: ['a1'], stage: 'Scouting', perspectives: [] };
    // The REAL server behaviour that bit Dan: the repo route resolves the slug
    // as a repo "Project" and 404s for anything that is not one.
    if (/\/api\/projects\/[^/]+\/scouting-questions/.test(u)) {
      const slug = decodeURIComponent(/projects\/([^/]+)/.exec(u)[1]);
      if (slug.startsWith('db_')) {
        return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: `Project '${slug}' not found` }) };
      }
      return ok({ questions: [q] });
    }
    if (/\/api\/(databases|filesystems)\/[^/]+\/questions/.test(u)) return ok({ questions: [q] });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([{ id: 'a1', display_name: 'Row counts' }]);
    if (u.includes('/api/analyses/facts')) return ok({ states: {}, subjects: {} });
    if (u.includes('/api/work-lists/runs/batch')) return ok({ set_id: 'set-1', runs: [{}, {}] });
    if (u.includes('/api/work-lists/runs/sets/')) return ok({ counts: { succeeded: 2 }, finished: 2, total: 2, complete: true, unrecognised_states: [] });
    return ok({});
  };
}

async function openWorkListPane(entityType, slugs, { omitKind = false } = {}) {
  const calls = [];
  const { document, window } = makeDomEnvironment();
  stubServer(calls);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const wl = await import('/static/next/worklist.js');
  void app;
  wl.grid.workList = {
    slug: 'candidates', display_name: 'candidates', members: slugs.map((s) => ({ entity_slug: s })),
    ...(omitKind ? {} : { entity_type: entityType }),
  };
  const el = document.createElement('div'); document.body.appendChild(el);
  await wl.renderWorkListPane({ el, stage: 'Scouting', subTabs: [], perspectives: [], analyses: [], onExit() {} });
  for (let i = 0; i < 60 && !el.querySelector('#wl-grid th, #wl-grid td'); i++) await new Promise((r) => setTimeout(r, 20));
  await new Promise((r) => setTimeout(r, 80));
  return { el, document, calls, wl };
}

async function clickRunAcross(el, document) {
  el.querySelector('#wl-analysis').value = 'a1';
  el.querySelector('[data-act="run"]').click();
  for (let i = 0; i < 50 && !document.querySelector('[data-act="go"]'); i++) await new Promise((r) => setTimeout(r, 20));
  const go = document.querySelector('[data-act="go"]');
  assert.ok(go, 'the run plan dialog must offer a Run button');
  go.click();
  await new Promise((r) => setTimeout(r, 120));
}

test('a DATABASE work list reads its Schema Inventory columns from the database route (the bug Dan hit)', async () => {
  const { el, calls } = await openWorkListPane('database', ['db_one', 'db_two']);
  const text = el.querySelector('#wl-grid').textContent;
  assert.doesNotMatch(text, /could not be read/, text);
  assert.doesNotMatch(text, /not found/i, text);
  assert.ok(calls.some((c) => c.url.startsWith('/api/databases/db_one/questions')), 'columns come from the database questions route');
  assert.ok(!calls.some((c) => c.url.includes('/scouting-questions')), 'the repo route is never asked about a database');
  assert.ok(el.querySelector('#wl-grid th'), 'the grid has real columns');
});

test('a DATABASE work list "Run across" sends entity_type=database and starts the batch', async () => {
  const { el, document, calls } = await openWorkListPane('database', ['db_one', 'db_two']);
  await clickRunAcross(el, document);
  const batch = calls.find((c) => c.url.includes('/api/work-lists/runs/batch'));
  assert.ok(batch, 'the batch was enqueued');
  assert.equal(batch.body.entity_type, 'database');
  assert.equal(batch.body.work_list_slug, 'candidates');
  assert.doesNotMatch(el.querySelector('#wl-note').textContent, /refused|could not/i);
  assert.match(el.querySelector('#wl-note').textContent, /Batch/);
  // every facts read the pane made carried the work list's kind, none defaulted
  for (const c of calls.filter((x) => x.url.includes('/api/analyses/facts'))) {
    assert.match(c.url, /entity_type=database/, c.url);
  }
});

test('a REPOSITORY work list still reads its columns from the repo route and runs across as repo', async () => {
  const { el, document, calls } = await openWorkListPane('repo', ['alpha', 'beta']);
  assert.doesNotMatch(el.querySelector('#wl-grid').textContent, /could not be read/);
  assert.ok(calls.some((c) => c.url.startsWith('/api/projects/alpha/scouting-questions')));
  assert.ok(!calls.some((c) => c.url.includes('/api/databases/')));
  await clickRunAcross(el, document);
  const batch = calls.find((c) => c.url.includes('/api/work-lists/runs/batch'));
  assert.equal(batch.body.entity_type, 'repo');
  for (const c of calls.filter((x) => x.url.includes('/api/analyses/facts'))) {
    assert.match(c.url, /entity_type=repo/, c.url);
  }
});

test('a FILESYSTEM work list reads the filesystem route', async () => {
  const { calls } = await openWorkListPane('filesystem', ['fs_one']);
  assert.ok(calls.some((c) => c.url.startsWith('/api/filesystems/fs_one/questions')));
});

test('a work list that arrives with NO entity_type fails loudly -- it is never read as a repo', async () => {
  let pane; let thrown = null;
  try { pane = await openWorkListPane(undefined, ['db_one'], { omitKind: true }); } catch (e) { thrown = e; }
  const surfaced = thrown ? thrown.message : pane.el.textContent;
  assert.match(surfaced, /has no entity_type/, `the missing kind must be named, got: ${surfaced.slice(0, 200)}`);
  if (pane) assert.ok(!pane.calls.some((c) => c.url.includes('/scouting-questions')), 'no repo-route request for a kind-less work list');
});

// Mixed-kind work lists cannot exist: routes/work_lists.py WorkListCreate.entity_type is one
// value per list ("a work list is homogeneous by construction"), so there is no mixed case to test.
