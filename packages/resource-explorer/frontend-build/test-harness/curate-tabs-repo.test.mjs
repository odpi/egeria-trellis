/** Brief S1: a repository's Curate is three tabs (Describe and publish / Contents / Structure). Real app.js and
 *  router, a stub server behind fetch that counts every call. What is pinned:
 *    - every control of the placement table is on EXACTLY its tab and no other;
 *    - a tab switch re-reads nothing (the plan is read once), and a lazy section under a hidden tab reads nothing
 *      until its tab opens;
 *    - the Project row appears once, Forget and copy GUID are not on tab 1;
 *    - the tab counts equal what the section headers print;
 *    - a jump into "what's in it" lands on Contents with the section open;
 *    - a 428 on the report press: a cue and a word on tab 1, the Project row on Contents;
 *    - a commit started on Contents shows its one-line state on Describe, Structure and a shared tab;
 *    - a reload with tab= reopens that tab; a pasted Structure URL opens Structure;
 *    - a file system draws EXACTLY what main drew (fixture generated from main's tree; a database has its tabs, S2). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';
import { snapshotNonRepoCurate } from './curate-nonrepo-snapshot.mjs';

globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const row = (o) => ({ evidence: '', source: 'manifest_parse', state: 'measured', count: null, members: null, candidate: true, detail: {}, ...o });
const planBase = () => ({
  technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: '2026-10-07T01:00:00',
  what_it_is: [row({ kind: 'Library', label: 'A library' }), row({ kind: 'Endpoint', label: 'Endpoint × 1' })],
  what_it_holds: [], relates: [], commits: [], writes: {}, keeps_current: 'x',
  made_of: [{ detail: { reviewed: 0, blueprints_reviewed: 0, blueprints_accepted: 0, blueprint_kinds: [], awaiting: { components: 3 } } }],
  awaiting: { components: 3 },
  survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] },
  project: { status: 'linked', word: 'project', name: 'P' },
});
const BRANCH = { path: 'packages/x', name: 'x', components: 3, accepted: 0, rejected: 0, undecided: 3, low_confidence: 0, types: {}, type: 'Service', ports: 0, own_ports: [], verdict: null };
const GUID = 'asset-guid-0001';

function makeServer(over = {}) {
  const s = {
    calls: [], plan: planBase(), scope: makeScopeStore(), blueprints: { blueprints: [], perspectives: [] },
    pubState: null, publishAnswers: 'ok', activity: 'success', latest: null, ...over,
  };
  s.state = () => s.pubState || { slug: 'egeria_git', in_egeria: true, asset_guid: GUID, can_publish_again: true,
    project: { status: 'linked', word: 'project', name: 'P' }, row: { word: 'published', read_at: '2026-10-07T02:00:00', surveyed_at: '2026-10-07T01:00:00' },
    survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] } };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (s.scopeFail && u.endsWith('/scope-events') && method === 'GET') return err(500, 'scope boom');
    const scoped = await s.scope.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.endsWith('/curate/commit')) return ok({ curation: { id: 'c1abcdef', state: 'running', author: 'dan', requested_at: new Date().toISOString(), steps: [{ name: 'a', state: 'running' }, { name: 'b', state: 'pending' }] }, activity_id: 'a1' });
    if (u.endsWith('/curate/commits/latest')) return ok({ curation: s.latest });
    if (u.includes('/curate/commits/')) return ok({ id: 'c1abcdef', state: s.activity === 'running' ? 'running' : 'done', author: 'dan', requested_at: new Date().toISOString(), steps: [{ name: 'a', state: s.activity === 'running' ? 'running' : 'done' }, { name: 'b', state: s.activity === 'running' ? 'pending' : 'done' }] });
    if (u.includes('/api/activity')) return ok({ id: 'a1', status: s.activity });
    if (u.endsWith('/publish-state')) return ok(s.state());
    if (u.endsWith('/publish-report')) {
      if (s.publishAnswers === '428' && !body.without_project) return err(428, 'egeria_project_context_required');
      if (s.publishAnswers === '500') return err(500, 'boom');
      if (body.without_project) s.pubState = { ...s.state(), in_egeria: true, project: { status: 'declined', word: 'no project (chosen)', name: '' }, row: { word: 'published', read_at: '2026-10-07T02:00:00', surveyed_at: '2026-10-07T01:00:00' } };
      return ok({ ok: true });
    }
    if (u.includes('/curate/plan') && s.planFail) return err(500, 'plan boom');
    if (u.includes('/curate/plan')) { if (s.holdPlan) await s.holdPlan; return ok(s.plan); }
    if (u.endsWith('/architecture/publish-plan')) return ok({ components: { to_write: [], in_egeria: 0 }, blueprints: { to_write: [], in_egeria: 0 }, label: 'Nothing to publish' });
    if (u.includes('/components/tree')) return ok({ branches: [BRANCH], total_components: 3, accepted: 0, reviewed: 0, topology: '' });
    if (u.includes('/components/blueprints')) return ok(s.blueprints);
    if (u.includes('/api/analyses/facts')) return ok({ subjects: {} });
    if (u.endsWith('/dependencies')) return ok({ heading: 'Dependencies · by kind', kinds: [], counts: {}, runtime_state: '', rows: [] });
    if (u.includes('/catalogue-depth-offer')) return ok({ layer1_done: false });
    if (u.endsWith('/file-type-measurements')) return ok({ inventoried: true, profiles: [], retired: [] });
    if (u.endsWith('/egeria-surveys')) return ok([]);
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

async function setUp(over = {}, { open = [], search = '' } = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  for (const id of open) window.localStorage.setItem(`re.curate.collapsed.${id}`, '0');
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  const server = makeServer(over);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.history.replaceState(null, '', `/next${search}`);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'egeria_git', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'dan' }, curate: {},
    curateSelection: null, groups: [], projects: [{ slug: 'egeria_git' }], perspectives: [], allPerspectives: [] });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, window, app, server };
}
async function openCurate(document) {
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(400);
}
const tab = async (document, id) => { document.querySelector(`[data-subtab="${id}"]`).click(); await wait(120); };
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const panels = (document) => Object.fromEntries([...document.querySelectorAll('[data-curate-tab-panel]')].map((p) => [p.dataset.curateTabPanel, p]));
/** The panels (by name) a selector matches in. */
const where = (document, sel) => Object.entries(panels(document)).filter(([, p]) => p.querySelector(sel)).map(([n]) => n);
const planReads = (server) => server.calls.filter((c) => c.method === 'GET' && c.url.includes('/curate/plan')).length;

/* ── placement ───────────────────────────────────────────────────────── */

// Tab 3's Architecture Publish mounts when Structure first opens, so every tab is visited before the table is read.
const PLACEMENT = [
  // Describe and publish
  ['describe', '[data-curate-describe-head]'], ['describe', '[data-curate-band="findable"]'], ['describe', '[data-curate-band="people"]'],
  ['describe', '[data-publish-status]'], ['describe', '[data-publish-row]'], ['describe', '[data-publish-project]'],
  ['describe', '[data-publish-survey]'], ['describe', '[data-publish-go]'], ['describe', '[data-resurvey-go]'],
  ['describe', '[data-file-measurements-section]'], ['describe', '[data-egeria-reports]'],
  // Contents
  ['contents', '[data-forget-open]'], ['contents', '[data-forget-confirm]'], ['contents', '[data-curate-project-row]'],
  ['contents', '[data-publish-asset] [data-copy-guid]'], ['contents', '#curate-sec-what-holds'], ['contents', '#curate-sec-writes'],
  ['contents', '[data-repo-commit-panel]'], ['contents', '[data-curate-go]'], ['contents', '[data-commit-resurvey]'],
  ['contents', '[data-repo-manifest]'],
  // Structure
  ['structure', '[data-architecture-publish]'], ['structure', '[data-architecture-go]'], ['structure', '#curate-sec-what-it-is'],
  ['structure', '#curate-sec-made-of'], ['structure', '#curate-sec-blueprints'], ['structure', '#curate-sec-relates'],
];

test('every control of the placement table is on exactly its tab and no other', async () => {
  const { document } = await setUp({ publishAnswers: 'ok' });
  await openCurate(document);
  await tab(document, 'curate_structure');
  await tab(document, 'curate_contents');
  await tab(document, 'curate_describe');
  for (const [want, sel] of PLACEMENT) assert.deepEqual(where(document, sel), [want], `${sel} belongs on ${want} only (found on: ${where(document, sel).join(',') || 'nowhere'})`);
  // the retired jump line is nowhere
  assert.equal(document.querySelector('[data-curate-nav]'), null);
});

test('Forget Egeria links and the asset\'s copy GUID are not on tab 1; the Project row appears once', async () => {
  const { document } = await setUp({ pubState: { slug: 'egeria_git', in_egeria: true, asset_guid: GUID, can_publish_again: true,
    project: { status: 'unset', word: 'no project', name: '' }, row: { word: 'none' }, survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 1, annotations: 1, steps: 1, stale_steps: 0, stale: [] } },
  plan: { ...planBase(), project: { status: 'unset', word: 'no project', name: '' } } });
  await openCurate(document);
  const describe = panels(document).describe;
  assert.equal(describe.querySelector('[data-forget-open]'), null);
  assert.equal(describe.querySelector('[data-publish-status] [data-copy-guid]'), null);
  assert.equal(describe.querySelector('[data-publish-asset]'), null);
  assert.equal(document.querySelectorAll('[data-curate-project-row]').length, 1, 'one Project row');
  for (const sel of ['[data-gate-bind]', '[data-commit-decline]', '[data-gate-without]']) {
    assert.equal(document.querySelectorAll(sel).length, 1, `${sel} appears once on the whole screen`);
    assert.deepEqual(where(document, sel), ['contents']);
  }
  assert.equal(document.querySelector('[data-commit-bind]'), null, 'the commit blocker holds no second copy');
  // the blocker still names the block, as a line (no buttons in it)
  const blocker = document.querySelector('[data-commit-blocker="no_project"]');
  assert.ok(blocker && !blocker.querySelector('button'));
});

/* ── one read, lazy and per tab ──────────────────────────────────────── */

test('switching tabs re-reads nothing: the plan is read once, and the publish state once', async () => {
  const { document, server } = await setUp();
  await openCurate(document);
  const before = server.calls.length;
  for (const id of ['curate_contents', 'curate_structure', 'curate_describe', 'curate_structure', 'curate_contents']) await tab(document, id);
  assert.equal(planReads(server), 1);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/publish-state')).length, 1);
  const added = server.calls.slice(before).map((c) => c.url);
  assert.equal(added.filter((u) => u.includes('/curate/plan') || u.endsWith('/publish-state') || u.includes('/scope-events')).length, 0,
    `a switch added: ${added.join(' ')}`);
});

test('a lazy section under a hidden tab reads nothing until its tab opens; Architecture Publish waits for Structure', async () => {
  const { document, server } = await setUp({}, { open: ['curate-sec-made-of', 'curate-sec-blueprints', 'curate-sec-relates', 'curate-sec-writes'] });
  await openCurate(document);
  const has = (frag) => server.calls.some((c) => c.url.includes(frag));
  for (const f of ['/components/tree', '/components/blueprints', '/catalogue-depth-offer', '/architecture/publish-plan']) assert.equal(has(f), false, `${f} not read on Describe`);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/dependencies')).length, 0);
  await tab(document, 'curate_contents');
  assert.equal(has('/catalogue-depth-offer'), true, 'Contents opened: its open section reads');
  assert.equal(has('/components/tree'), false, 'and Structure\'s still does not');
  assert.equal(has('/architecture/publish-plan'), false);
  await tab(document, 'curate_structure');
  assert.equal(has('/components/tree'), true);
  assert.equal(has('/components/blueprints'), true);
  assert.equal(server.calls.some((c) => c.url.endsWith('/dependencies')), true);
  assert.equal(has('/architecture/publish-plan'), true, 'Architecture Publish mounts on Structure');
  assert.equal(server.calls.filter((c) => c.url.includes('/components/tree')).length, 1);
});

/* ── counts ──────────────────────────────────────────────────────────── */

test('the tab counts equal what the section headers print, and a count not yet read says so (never 0)', async () => {
  let release; const holdPlan = new Promise((r) => { release = r; });
  const { document, server } = await setUp({ holdPlan });
  server.scope.events.push({ locator: 'docs/a.md', choice: 'include', action: 'set', author: 'dan' }, { locator: 'docs/b.md', choice: 'include', action: 'set', author: 'dan' });
  await openCurate(document);
  // the rail's text, cut at each tab's label: "Contents <count> Structure <count> Questions"
  const seg = (from, to) => (norm(document.querySelector('[data-subtab-rail]')).match(new RegExp(`${from}(.*?)${to}`)) || [, ''])[1];
  assert.match(seg('Structure', 'Questions'), /reading/, 'the plan is still being read: the Structure count is a cue and "reading"');
  assert.doesNotMatch(seg('Structure', 'Questions'), /\b0\b/);
  release();
  await wait(400);
  const chosenN = Number(document.querySelector('[data-scope-saved-count]').textContent.match(/(\d+) included/)[1]);
  assert.match(seg('Contents', 'Structure'), new RegExp(`${chosenN} chosen`), 'Contents N chosen = the section header\'s N included');
  assert.match(seg('Structure', 'Questions'), /3 awaiting you/, 'Structure = the plan\'s own awaiting');
});

/* ── navigation ──────────────────────────────────────────────────────── */

test('a jump from the members rail to "what\'s in it" lands on Contents with the section open', async () => {
  const { document, app } = await setUp();
  await openCurate(document);
  assert.equal(app.state.subTab, 'curate_describe', 'Curate always lands on Describe and publish');
  await app.openMembers({ slug: 'egeria_git', analysisId: 'sub_resource_survey', title: 'x' });
  document.getElementById('rail-evidence').querySelector('[data-members-goto-scope]').click();
  await wait(200);
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(panels(document).contents.hidden, false);
  assert.equal(panels(document).describe.hidden, true);
  assert.equal(document.getElementById('curate-sec-what-holds').open, true);
});

test('a reload with tab= reopens that tab; a pasted Structure URL opens Structure; no tab= opens Describe', async () => {
  const a = await setUp({}, { search: '?resource=egeria_git&stage=curate&tab=curate_contents' });
  a.app.state.stage = 'curate'; a.app.state.subTab = 'curate_contents';        // readUrl is what a real load runs
  const { document } = a;
  a.app.renderIntentNav();
  await a.app.openCurate({ tab: 'curate_contents' });
  await wait(300);
  assert.equal(panels(document).contents.hidden, false);
  assert.equal(new URLSearchParams(a.window.location.search).get('tab'), 'curate_contents');
  const b = await setUp({}, { search: '?resource=egeria_git&stage=curate&tab=curate_structure' });
  b.app.state.stage = 'curate'; b.app.state.subTab = 'curate_structure';
  await b.app.openCurate({ tab: 'curate_structure' });
  await wait(300);
  assert.equal(panels(b.document).structure.hidden, false);
  assert.equal(panels(b.document).describe.hidden, true);
  const c = await setUp();
  await openCurate(c.document);
  assert.equal(panels(c.document).describe.hidden, false);
});

/* ── the 428 gate ────────────────────────────────────────────────────── */

test('a 428 on the report press: a cue and a word on Describe and publish, the Project row on Contents', async () => {
  const { document } = await setUp({
    publishAnswers: '428',
    pubState: { slug: 'egeria_git', in_egeria: false, asset_guid: '', can_publish_again: false, project: { status: 'unset', word: 'no project', name: '' }, row: { word: 'none' },
      survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 1, annotations: 1, steps: 1, stale_steps: 0, stale: [] } },
  });
  await openCurate(document);
  document.querySelector('[data-publish-go]').click();
  await wait(200);
  const gate = panels(document).describe.querySelector('[data-publish-gate-panel]');
  assert.ok(gate && gate.querySelector('[data-cue]'), 'a cue');
  assert.match(norm(gate), /^\S* ?needs a project/);
  assert.ok(norm(gate).length < 40, 'a short word, not a sentence');
  assert.equal(gate.querySelector('button[data-gate-bind], button[data-gate-without]'), null);
  assert.ok(panels(document).contents.querySelector('[data-curate-project-row] [data-gate-without]'), 'the choices are the Project row');
  gate.querySelector('[data-publish-gate-goto]').click();
  await wait(50);
  assert.equal(panels(document).contents.hidden, false, 'the link goes to Contents');
});

test('"Publish this report without one" pressed on the Project row shows its result in that row, as a cue and a short word', async () => {
  const unset = { slug: 'egeria_git', in_egeria: false, asset_guid: '', can_publish_again: false, project: { status: 'unset', word: 'no project', name: '' }, row: { word: 'none' },
    survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 1, annotations: 1, steps: 1, stale_steps: 0, stale: [] } };
  const a = await setUp({ pubState: unset, publishAnswers: '428' });
  await openCurate(a.document);
  await tab(a.document, 'curate_contents');
  a.document.querySelector('[data-curate-project-row] [data-gate-without]').click();
  await wait(300);
  const fb = () => a.document.querySelector('[data-curate-project-feedback]');
  assert.ok(fb().querySelector('[data-cue="measured"]'), 'a cue where it was pressed');
  assert.match(norm(fb()), /published/);
  assert.ok(norm(fb()).length < 25, 'a short word');
  // the result follows the re-read row: a press the server refuses says so in the same place
  const b = await setUp({ pubState: unset, publishAnswers: '500' });
  await openCurate(b.document);
  await tab(b.document, 'curate_contents');
  b.document.querySelector('[data-curate-project-row] [data-gate-without]').click();
  await wait(300);
  const fb2 = b.document.querySelector('[data-curate-project-feedback]');
  assert.ok(fb2.querySelector('[data-cue="error"]'));
  assert.match(norm(fb2), /not published/);
});

test('when the plan read fails, the Structure count says "not read" with a cue, never 0 and never "reading"', async () => {
  const { document } = await setUp({ planFail: true });
  await openCurate(document);
  const rail = norm(document.querySelector('[data-subtab-rail]'));
  const structure = (rail.match(/Structure(.*?)Questions/) || [, ''])[1];
  assert.match(structure, /not read/);
  assert.doesNotMatch(structure, /reading|\b0\b/);
  assert.ok(document.querySelector('[data-subtab-rail] [data-count-state="failed"][data-cue="error"]'));
  assert.ok(document.querySelector('[data-curate-plan-error]'), 'the pane still says the plan failed');
});

test('when the selection-record read fails, the Contents count says "not read" with a cue, never 0 and never "reading"', async () => {
  const { document } = await setUp({ scopeFail: true });
  await openCurate(document);
  const rail = norm(document.querySelector('[data-subtab-rail]'));
  const contents = (rail.match(/Contents(.*?)Structure/) || [, ''])[1];
  assert.match(contents, /not read/);
  assert.doesNotMatch(contents, /reading|\b0\b/);
  assert.ok(document.querySelector('[data-subtab-rail] [data-count-state="failed"][data-cue="error"]'));
  assert.match(norm([...document.querySelectorAll('[data-subtab-rail] > *')].find((e) => /Structure/.test(e.textContent))), /3 awaiting you/, 'the plan count is unaffected');
});

/* ── the running-commit line ─────────────────────────────────────────── */

test('a commit started on Contents shows its one line on Describe, Structure and a shared tab; the stepper stays on Contents', async () => {
  const { document, server, app } = await setUp({ activity: 'running' });
  server.scope.events.push({ locator: 'docs/a.md', choice: 'include', action: 'set', author: 'dan' });
  await openCurate(document);
  await tab(document, 'curate_contents');
  document.querySelector('[data-curate-go]').click();
  await wait(300);
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /Publish · commit c1ab/);
  for (const id of ['curate_describe', 'curate_structure']) {
    await tab(document, id);
    const line = document.querySelectorAll('[data-curate-commit-line]');
    assert.equal(line.length, 1, `one line on ${id}`);
    assert.match(norm(line[0]), /running/);
    assert.equal(line[0].previousElementSibling.hasAttribute('data-subtab-rail'), true, 'under the rail');
  }
  assert.equal(panels(document).describe.querySelector('[data-commit-step]'), null, 'the stepper is not on Describe');
  assert.equal(panels(document).structure.querySelector('[data-commit-step]'), null, 'nor on Structure');
  assert.ok(panels(document).contents.querySelector('[data-curate-record]'), 'the full record is on Contents');
  document.querySelector('[data-subtab="questions"]').click();                 // a shared tab, still on Curate
  await wait(300);
  assert.equal(app.state.stage, 'curate');
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /Publish · commit c1ab/);
  server.activity = 'success';
  await wait(2400);                      // let the press's poll see the end, under THIS stub (a later test swaps fetch)
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /published/, 'the line follows the record to its end');
});

test('a reload while a commit is running: the line comes from the cheap latest-commit read, not the plan', async () => {
  const rec = { id: 'beef0000', state: 'running', author: 'dan', requested_at: new Date().toISOString(), steps: [{ name: 'a', state: 'done' }, { name: 'b', state: 'running' }, { name: 'c', state: 'pending' }] };
  let release; const holdPlan = new Promise((r) => { release = r; });
  const { document, server } = await setUp({ latest: rec, holdPlan });
  await openCurate(document);
  assert.equal(server.calls.some((c) => c.url.endsWith('/curate/commits/latest')), true);
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /step 2 of 3 · running/, 'on screen while the plan is still being read');
  release();
  await wait(200);
});

/* ── file system: exactly what main drew (a database has its own tabs now: curate-tabs-db.test.mjs) ── */

const FIXTURE = JSON.parse(readFileSync(new URL('./curate-nonrepo.fixture.json', import.meta.url), 'utf8'));
for (const kind of ['filesystem']) {
  test(`${kind}: Curate draws byte-for-byte what main drew (gate off for the kind)`, async () => {
    const out = await snapshotNonRepoCurate(kind);
    assert.equal(out.rail, FIXTURE[kind].rail, 'the rail');
    assert.equal(out.content, FIXTURE[kind].content, 'the pane');
    assert.ok(!/data-curate-tab-panel|data-subtab-rail|curate_describe/.test(out.content + out.rail));
  });
}
