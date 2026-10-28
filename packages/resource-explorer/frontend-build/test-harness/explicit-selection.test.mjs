/** Brief 2a, explicit selection for repositories: the two panes that list candidates (Curate's "what's in it"
 *  and the Analysis pane's sub-resource panel) draw the SAME state from the SAME record.
 *
 *  Real app.js and router, a stub server behind fetch that keeps the selection record the way the server
 *  does (repo-scope-kit.mjs). What is asserted: nothing pre-ticked; worthy reads proposed; accepting the
 *  proposals writes N events marked as proposals; a folder's selector is about the folder only and
 *  "include its M worthy children" writes M explicit events; a container is shown, not hidden; a press
 *  dims, says saving…, and a second press while pending sends nothing; a failed write rolls back and says
 *  why; "include all visible" acts on the filtered rows only; a published row left out says so and is not
 *  unpublished; and a reload brings every choice back because it is the record, not the DOM.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const SUBS = ['docs/a', 'docs/b', 'docs/c'];
const PLAN = {
  technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: null,
  what_it_is: [{ kind: 'Library', label: 'A library', evidence: 'x', source: 'manifest_parse', state: 'measured', count: 1, members: null, candidate: true, detail: {} }],
  what_it_holds: [
    { kind: 'DataFile', label: '0 data files', evidence: '', source: 'data_file_profiling', state: 'measured', count: 0, members: null, candidate: false, detail: {} },
    { kind: 'SubResource', label: '3 of 3 sub-resources worth cataloging', evidence: '', source: 'sub_resource_survey', state: 'measured', count: 3, members: null, candidate: true, detail: { worthy: SUBS } },
  ],
  relates: [{ kind: 'Dependency', label: '4 dependencies', evidence: '', source: 'dependency_analysis', state: 'measured', count: 4, members: null, candidate: false, detail: {} }],
  commits: [], writes: {}, keeps_current: 'x',
  survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 2, stale: ['repo_health', 'repo_language'] },
  project: { status: 'linked', word: 'project', name: 'P' },
};
const BRANCH = { path: 'packages/x', name: 'x', components: 1, accepted: 0, rejected: 0, undecided: 1, low_confidence: 0, types: {}, type: 'Service', ports: 0, own_ports: [], verdict: null };

function makeServer(over = {}) {
  const s = { calls: [], hold: {}, verdictStatus: 200, scope: makeScopeStore(), ...over };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b, status = 200) => ({ ok: true, status, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    const scoped = await s.scope.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.endsWith('/curate/commit')) {
      if (s.hold.commit) await s.hold.commit;
      if (s.commitStatus) return err(s.commitStatus, 'boom');
      return ok({ curation: { id: 'c1', state: 'queued', steps: [], author: 'dan', requested_at: '2026-10-07T00:00:00' }, activity_id: 'a1' });
    }
    if (u.endsWith('/components/verdicts')) {
      if (s.hold.verdict) await s.hold.verdict;
      if (s.verdictStatus !== 200) return err(s.verdictStatus, 'boom');
      return ok({ verdicts: body.scope_locators.map((x) => ({ scope: x })), queued: 1 });
    }
    if (u.includes('/curate/commits/')) return ok({ id: 'c1', state: 'done', steps: [], author: 'dan', requested_at: '2026-10-07T00:00:00' });
    if (u.includes('/api/activity')) return ok({ id: 'a1', status: 'success' });
    if (u.includes('/curate/plan')) return ok(PLAN);
    if (u.endsWith('/dependencies')) return ok(s.deps || { heading: 'Dependencies · by kind', kinds: ['build-time', 'runtime'], counts: { 'build-time': 1, runtime: 1 }, runtime_state: '',
      rows: [{ kind: 'build-time', name: 'fastapi', target: '0.110', source: 'pyproject.toml', state: 'measured', state_words: 'measured · from pyproject.toml', key: 'k1' },
             { kind: 'runtime', name: 'web', target: 'db', source: 'dc.yml:3', state: 'proposed', state_words: 'proposed · from dc.yml', key: 'k2' }] });
    if (u.endsWith('/dependencies/confirm')) return ok({ heading: 'Dependencies · by kind', kinds: ['build-time', 'runtime'], counts: { 'build-time': 1, runtime: 1 }, runtime_state: '', rows: [] });
    if (u.includes('/components/tree')) return ok({ branches: [BRANCH], total_components: 1, accepted: 0, reviewed: 0, topology: '' });
    if (u.includes('/components/blueprints')) return ok({ blueprints: [], perspectives: [] });
    if (u.endsWith('/publish-state')) return ok({ slug: 's', in_egeria: false, asset_guid: '', row: { word: 'none' }, project: { status: 'unset', word: 'no project' }, can_publish_again: false, survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] } });
    if (u.endsWith('/file-types')) return ok({ types: [{ label: 'Python', file_count: 12, extensions: ['.py'], cataloged: false, linked: false, dataset_guid: '' }], blocker: '' });
    if (u.endsWith('/publish-report')) { if (s.hold.publish) await s.hold.publish; return ok({ ok: true }); }
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

const deferred = () => { let resolve; const p = new Promise((r) => { resolve = r; }); return { p, resolve }; };
const commitBody = (server) => server.calls.find((c) => c.url.endsWith('/curate/commit'))?.body;


async function setUp(store = makeScopeStore(), serverOver = {}) {
  const { document, window } = makeDomEnvironment();
  const server = makeServer({ scope: store, ...serverOver });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'egeria_git';
  app.state.stage = 'understanding';
  app.state.subTab = 'questions';
  app.state.investigations = []; app.state.investigation = '';
  app.state.workListSlug = null; app.state.workListIndex = false;
  app.state.me = { user_id: 'dan' };
  app.state.curate = {}; app.state.curateSelection = null;
  app.state.groups = [];
  app.state.projects = [{ slug: 'egeria_git' }];
  app.state.perspectives = []; app.state.allPerspectives = [];
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(400);
  return { document, app, server, store };
}
const holds = (d) => d.getElementById('curate-sec-what-holds');
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const row = (d, loc) => d.querySelector(`[data-scope-row="${loc}"]`);
const seg = (d, loc, choice) => row(d, loc).querySelector(`[data-scope-act="set"][data-scope-choice="${choice}"]`);
const filled = (b) => b.getAttribute('aria-pressed') === 'true' && b.className.includes('bg-ink');
const locs = (store) => store.events.map((e) => e.locator);
const manifest = (d, id) => norm(d.querySelector(`[data-manifest-row="${id}"]`));

/* ── Curate: the rows ───────────────────────────────────────────────────────────────────────────── */

test('nothing is pre-ticked; a worthy row reads proposed in muted ink with its reason; the table says 0 items', async () => {
  const { document } = await setUp();
  assert.equal(norm(holds(document).querySelector('[data-scope-choice-head]')), 'Publish to Egeria?');
  assert.equal(holds(document).querySelectorAll('[data-scope-selector] [aria-pressed="true"]').length, 0);
  const w = row(document, 'docs').querySelector('[data-scope-state-word="proposed"]');
  assert.equal(norm(w), 'proposed · worthy · top_level_structural_folder');
  assert.ok(w.className.includes('text-ink-muted'));
  assert.equal(row(document, 'src/x.py'), null, 'a not-worthy row is hidden until "show the rest"');
  assert.match(manifest(document, 'files'), /0 DataFiles/);
  assert.match(manifest(document, 'left_out'), /1 not selected · 4 proposals not accepted · 0 left out/);
});

test('accepting the proposals writes N events marked as proposals, and each becomes a filled segment', async () => {
  const { document, store } = await setUp();
  const accept = holds(document).querySelector('[data-scope-act="accept"]');
  assert.equal(norm(accept), 'accept the 4 proposals');
  accept.click();
  await wait(150);
  assert.equal(store.posts.length, 1);
  assert.equal(store.posts[0].events.length, 4);
  assert.ok(store.posts[0].events.every((e) => e.source === 'proposal' && e.proposal_rule === 'worthy' && e.choice === 'include'));
  for (const l of ['docs', 'docs/a.md', 'docs/b.md', 'src']) assert.ok(filled(seg(document, l, 'include')), `${l} is filled`);
  assert.match(norm(row(document, 'docs')), /saved · you · just now/);
  const again = await setUp(store);                     // after a reload the record says where each came from
  assert.match(norm(row(again.document, 'docs')), /accepted proposal/);
  assert.equal(holds(document).querySelector('[data-scope-act="accept"]'), null, 'nothing left to accept');
  assert.match(manifest(document, 'files'), /2 DataFiles/);
  assert.match(manifest(document, 'folders'), /2 FileFolders/);
});

test('including a folder writes one event for the folder; its cell says what inside is not selected; "include its worthy children" writes one per child', async () => {
  const { document, store } = await setUp();
  seg(document, 'docs', 'include').click();
  await wait(150);
  assert.deepEqual(locs(store), ['docs'], 'one event for the folder and none for its children');
  const cell = norm(row(document, 'docs'));
  assert.match(cell, /folder only · 2 inside not selected/);
  assert.match(manifest(document, 'folders'), /1 FileFolder/);
  assert.match(manifest(document, 'files'), /0 DataFiles/);
  const btn = row(document, 'docs').querySelector('[data-scope-act="children"]');
  assert.equal(norm(btn), 'include its 2 worthy children');
  btn.click();
  await wait(150);
  assert.deepEqual(locs(store), ['docs', 'docs/a.md', 'docs/b.md']);
  assert.ok(filled(seg(document, 'docs/a.md', 'include')) && filled(seg(document, 'docs/b.md', 'include')));
  assert.match(manifest(document, 'files'), /2 DataFiles/);
  assert.match(manifest(document, 'containers'), /0 FileFolders/, 'docs is chosen itself, so it is no container');
  assert.equal(row(document, 'docs').querySelector('[data-scope-act="children"]'), null);
});

test('a left-out folder says its children keep their own choice, and does not touch them', async () => {
  const { document, store } = await setUp();
  seg(document, 'docs/a.md', 'include').click();
  await wait(150);
  seg(document, 'docs', 'leave_out').click();
  await wait(150);
  assert.match(norm(row(document, 'docs')), /left out · children keep their own choice/);
  assert.ok(filled(seg(document, 'docs/a.md', 'include')), 'the child keeps its own choice');
  assert.deepEqual(locs(store), ['docs/a.md', 'docs']);
});

test('a container is shown, not hidden: hollow mark, its words, counted separately and never as a chosen folder', async () => {
  const { document } = await setUp();
  seg(document, 'docs/a.md', 'include').click();
  await wait(150);
  const c = row(document, 'docs').querySelector('[data-scope-container]');
  assert.equal(norm(c), '○ needed as a container · not an asset of its own');
  assert.equal(row(document, 'docs').querySelector('[aria-pressed="true"]'), null, 'its own selector is not filled');
  assert.match(manifest(document, 'files'), /1 DataFile/);
  assert.match(manifest(document, 'folders'), /0 FileFolders/);
  assert.match(manifest(document, 'containers'), /1 FileFolder/);
  assert.equal(norm(document.querySelector('[data-curate-go]')), 'Publish 2 items →');
});

test('a press dims the selector, says saving… in the same cell, and a second press sends no second POST', async () => {
  const gate = deferred();
  const { document, store } = await setUp();
  store.hold = gate.p;
  seg(document, 'src', 'include').click();
  const cell = row(document, 'src').querySelector('[data-scope-choice-cell]');
  assert.match(norm(cell), /saving…/);
  assert.ok(seg(document, 'src', 'include').disabled && seg(document, 'src', 'include').className.includes('opacity-60'), 'dimmed and disabled');
  assert.ok(!filled(seg(document, 'src', 'include')), 'not filled until the record answers');
  seg(document, 'src', 'include').click();
  seg(document, 'src', 'leave_out').click();
  assert.equal(store.posts.length, 1, 'no second POST');
  gate.resolve();
  await wait(150);
  assert.ok(filled(seg(document, 'src', 'include')));
  assert.match(norm(row(document, 'src')), /saved · you · just now/);
  assert.equal(store.posts.length, 1);
});

test('a failed write rolls the cell back and says why', async () => {
  const { document, store } = await setUp();
  store.failWith = { status: 500, detail: 'registry down' };
  seg(document, 'src', 'include').click();
  await wait(150);
  assert.ok(!filled(seg(document, 'src', 'include')), 'rolled back');
  assert.match(norm(row(document, 'src')), /✕ not saved · registry down/);
  assert.equal(seg(document, 'src', 'include').disabled, false, 'it can be pressed again');
  store.failWith = null;
  seg(document, 'src', 'include').click();
  await wait(150);
  assert.ok(filled(seg(document, 'src', 'include')));
});

test('include all visible and clear all visible act on the filtered rows only, one event per row', async () => {
  const { document, store } = await setUp();
  const f = holds(document).querySelector('[data-scope-filter]');
  f.value = 'docs'; f.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  assert.equal(document.querySelectorAll('[data-scope-row]').length, 3);
  const inc = holds(document).querySelector('[data-scope-act="include-visible"]');
  assert.equal(norm(inc), 'include all visible (3)');
  inc.click();
  await wait(150);
  assert.deepEqual(locs(store).sort(), ['docs', 'docs/a.md', 'docs/b.md']);
  assert.equal(store.posts[0].events.length, 3);
  assert.equal(row(document, 'src'), null, 'src was not shown, so not written');
  holds(document).querySelector('[data-scope-act="clear-visible"]').click();
  await wait(150);
  assert.equal(store.posts[1].events.length, 3);
  assert.ok(store.posts[1].events.every((e) => e.action === 'clear'));
  assert.equal(holds(document).querySelectorAll('[aria-pressed="true"]').length, 0);
});

test('a published row that is left out says so, stays in the Egeria lane, and nothing is deleted', async () => {
  const store = makeScopeStore({ published: { docs: { guid: 'g-docs', when: new Date().toISOString() } } });
  const { document, server } = await setUp(store);
  assert.match(norm(row(document, 'docs').querySelector('[data-scope-egeria]')), /published · just now/);
  seg(document, 'docs', 'leave_out').click();
  await wait(150);
  assert.match(norm(row(document, 'docs')), /left out for future publishes · published earlier · kept in Egeria/);
  assert.match(norm(row(document, 'docs').querySelector('[data-scope-egeria]')), /published/);
  assert.match(manifest(document, 'published_earlier'), /1/);
  assert.equal(server.calls.filter((c) => c.method === 'DELETE').length, 0);
  assert.equal(server.calls.some((c) => /delete|archive|unpublish/i.test(c.url)), false);
});

test('reload: every choice survives, because it is the record and not the DOM', async () => {
  const store = makeScopeStore();
  const first = await setUp(store);
  seg(first.document, 'docs/a.md', 'include').click();
  await wait(150);
  seg(first.document, 'src', 'leave_out').click();
  await wait(150);
  const second = await setUp(store);                    // a brand-new page and app state, the same record
  assert.ok(filled(seg(second.document, 'docs/a.md', 'include')));
  assert.ok(filled(seg(second.document, 'src', 'leave_out')));
  assert.match(manifest(second.document, 'containers'), /1 FileFolder/);
  assert.equal(norm(second.document.querySelector('[data-curate-go]')), 'Publish 2 items →');
});

test('signed out: the selectors are disabled with the reason, and nothing can be written', async () => {
  const { document, app, store } = await setUp();
  app.state.me = null;
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(400);
  const b = seg(document, 'src', 'include');
  assert.ok(b.disabled && /sign in/.test(b.title));
  b.click();
  assert.equal(store.posts.length, 0);
});

/* ── the Analysis pane: the same record ─────────────────────────────────────────────────────────── */

async function mountAnalysis(store) {
  const { document, window } = makeDomEnvironment();
  const server = makeServer({ scope: store });
  const prior = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    const ok = (b, status = 200) => ({ ok: true, status, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    server.calls.push({ method, url: u, body });
    const scoped = await store.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.endsWith('/analyses/sub_resource_survey/results')) {
      return ok({ findings: [
        { path: 'docs', kind: 'folder', label: 'worthy', summary: 'top_level_structural_folder', owners: ['dan'], last_updated_at: '2026-09-01T00:00:00' },
        { path: 'docs/a.md', kind: 'file', label: 'worthy', summary: 'well_known_file', owners: [] },
        { path: 'docs/b.md', kind: 'file', label: 'worthy', summary: 'well_known_file', owners: [] },
        { path: 'src', kind: 'folder', label: 'worthy', summary: 'top_level_structural_folder', owners: [] },
        { path: 'src/x.py', kind: 'file', label: 'not_worthy', summary: 'plain source', owners: [] }] });
    }
    if (u.endsWith('/sub-resources/catalog')) return ok({ cataloged: ['docs', 'docs/a.md'], published: { docs: 'g1', 'docs/a.md': 'g2' }, containers: 1, read_back: 2, sent: 0, failed: 0, manifest: {} });
    if (u.endsWith('/sub-resources')) return ok([]);
    if (u.includes('/analyses')) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.selectedSlug = 'egeria_git';
  app.state.me = { user_id: 'dan' };
  const { mountSubResourcePanel } = await import('/static/next/stages/analysis.js');
  const panel = document.createElement('div');
  document.body.appendChild(panel);
  await mountSubResourcePanel('egeria_git', panel);
  void prior;
  return { document, panel, server };
}

test('Analysis panel: no checkbox and nothing pre-ticked; rows read from the record, worthy ones proposed', async () => {
  const store = makeScopeStore();
  const { panel } = await mountAnalysis(store);
  assert.equal(panel.querySelectorAll('input[type=checkbox]').length, 0);
  assert.equal(panel.querySelector('[data-subres-publish]'), null, 'the sandbox flag is retired');
  assert.equal(norm(panel.querySelector('[data-scope-choice-head]')), 'Publish to Egeria?');
  assert.equal(panel.querySelectorAll('[aria-pressed="true"]').length, 0);
  assert.match(norm(panel.querySelector('[data-scope-row="docs"]')), /proposed · worthy · top_level_structural_folder/);
  assert.match(norm(panel.querySelector('[data-subres-manifest]')), /0 items to publish/);
  assert.ok(panel.querySelector('[data-subres-catalog]').disabled);
  assert.match(norm(panel.querySelector('[data-subres-blocker]')), /nothing selected/);
});

test('Analysis panel and Curate show the same state from the same record', async () => {
  const store = makeScopeStore();
  const a = await mountAnalysis(store);
  a.panel.querySelector('[data-scope-row="docs/a.md"] [data-scope-act="set"][data-scope-choice="include"]').click();
  await wait(150);
  assert.deepEqual(locs(store), ['docs/a.md']);
  assert.match(norm(a.panel.querySelector('[data-scope-row="docs"]')), /needed as a container/);
  assert.match(norm(a.panel.querySelector('[data-subres-manifest]')), /2 items to publish · 1 file · 0 folders you chose · 1 container/);
  const c = await setUp(store);
  assert.ok(filled(seg(c.document, 'docs/a.md', 'include')));
  assert.match(norm(row(c.document, 'docs')), /needed as a container/);
  assert.equal(norm(c.document.querySelector('[data-curate-go]')), 'Publish 2 items →');
});

test('Analysis panel: Publish sends no item list, shows the proof counts as cues, and a repository not in Egeria is a blocker with a way to it', async () => {
  const store = makeScopeStore();
  const a = await mountAnalysis(store);
  a.panel.querySelector('[data-scope-row="docs"] [data-scope-act="set"][data-scope-choice="include"]').click();
  await wait(150);
  const btn = a.panel.querySelector('[data-subres-catalog]');
  assert.equal(btn.textContent.trim(), 'Publish 1 item');
  btn.click();
  await wait(150);
  const post = a.server.calls.find((c) => c.method === 'POST' && c.url.endsWith('/sub-resources/catalog'));
  assert.deepEqual(post.body, {}, 'the request names nothing: the server reads the record');
  assert.match(norm(a.panel.querySelector('[data-subres-feedback]')), /published · 2 read back/);

  const off = makeScopeStore({ inEgeria: false });
  const b = await mountAnalysis(off);
  b.panel.querySelector('[data-scope-row="docs"] [data-scope-act="set"][data-scope-choice="include"]').click();
  await wait(150);
  assert.ok(b.panel.querySelector('[data-subres-catalog]').disabled);
  assert.match(norm(b.panel.querySelector('[data-subres-blocker]')), /the repository is not in Egeria yet · publish the repository first →/);
  assert.ok(b.panel.querySelector('[data-subres-goto-curate]'));
});

test('what a tick does is said above the rows and on Include; one chosen folder is "Publish 1 item →" and no sibling or README moves', async () => {
  const { document, store } = await setUp();
  assert.match(norm(holds(document).querySelector('[data-scope-tick-words]')), /Include publishes this row only/);
  assert.match(seg(document, 'docs', 'include').title, /README are separate rows/);
  seg(document, 'src', 'include').click();
  await wait(150);
  assert.equal(norm(document.querySelector('[data-curate-go]')), 'Publish 1 item →');
  assert.deepEqual(store.view().manifest.chosen, ['src']);
});
