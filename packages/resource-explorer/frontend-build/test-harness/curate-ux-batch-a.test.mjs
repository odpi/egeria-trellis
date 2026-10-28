/** Curate UX batch A (owner feedback on egeria_git, 2026-10-07): items 1, 3, 4, 9, 10.
 *
 *  Real app.js, real router (the Curate nav button is clicked), a stub server behind fetch whose
 *  write answers can be held open, so the PENDING state of a pressed control is observable.
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
    if (u.endsWith('/file-type-measurements')) return ok({ inventoried: true, profiles: [], retired: [] });
    if (u.endsWith('/publish-report')) { if (s.hold.publish) await s.hold.publish; return ok({ ok: true }); }
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

async function setUp(serverOver = {}) {
  const { document, window } = makeDomEnvironment();
  // Sections start collapsed and read their data only when opened (2026-10-08): these tests exercise the
  // tree, the dependencies and the commit panel, so those sections are left open for them.
  for (const id of ['made-of', 'blueprints', 'relates', 'writes']) window.localStorage.setItem(`re.curate.collapsed.curate-sec-${id}`, '0');
  const server = makeServer(serverOver);
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
  window.HTMLElement.prototype.scrollIntoView = window.HTMLElement.prototype.scrollIntoView || function () {};   // jsdom has none
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(400);
  // The sections live on the Contents and Structure tabs; an open section reads when its tab first shows.
  for (const tab of ['curate_contents', 'curate_structure']) { document.querySelector(`[data-subtab="${tab}"]`).click(); await wait(60); }
  return { document, app, server };
}
const holds = (d) => d.getElementById('curate-sec-what-holds');
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const deferred = () => { let resolve; const p = new Promise((r) => { resolve = r; }); return { p, resolve }; };
const commitBody = (server) => server.calls.find((c) => c.url.endsWith('/curate/commit'))?.body;

/* ── item 1 (replaced by brief 2a): nothing is ticked; the press sends the RECORD, not a list ──────── */

const cellOf = (d, loc) => d.querySelector(`[data-scope-row="${loc}"]`);
const segOf = (d, loc, choice) => cellOf(d, loc).querySelector(`[data-scope-act="set"][data-scope-choice="${choice}"]`);

test('what is in it: no checkbox, nothing ticked, worthy rows read proposed, the table says 0 items', async () => {
  const { document } = await setUp();
  const sec = holds(document);
  assert.equal(sec.querySelectorAll('input[type=checkbox][data-curate-sub]').length, 0, 'the checkbox is gone');
  assert.equal(sec.querySelector('[data-curate-subs]'), null, 'so is "include the contained set whole"');
  assert.equal(norm(sec.querySelector('[data-scope-choice-head]')), 'Publish to Egeria?');
  assert.equal(sec.querySelectorAll('[data-scope-selector] [aria-pressed="true"]').length, 0, 'no segment is filled');
  assert.match(norm(cellOf(document, 'docs')), /proposed · worthy · top_level_structural_folder/);
  assert.match(norm(document.querySelector('[data-manifest-row="files"]')), /0 DataFiles/);
  assert.equal(norm(document.querySelector('[data-curate-go]')), 'Publish →', 'only the confirmed line goes: no items');
});

test('including one file under a folder nobody included: the folder is a container and the table counts 1 file, 0 folders, 1 container', async () => {
  const { document, server } = await setUp();
  segOf(document, 'docs/a.md', 'include').click();
  await wait(150);
  assert.ok(segOf(document, 'docs/a.md', 'include').getAttribute('aria-pressed') === 'true', 'filled from the record\'s answer');
  assert.match(norm(cellOf(document, 'docs')), /needed as a container · not an asset of its own/);
  const row = (id) => norm(document.querySelector(`[data-manifest-row="${id}"]`));
  assert.match(row('files'), /1 DataFile/);
  assert.match(row('folders'), /0 FileFolders/);
  assert.match(row('containers'), /1 FileFolder/);
  assert.equal(norm(document.querySelector('[data-curate-go]')), 'Publish 2 items →');
  assert.deepEqual(server.scope.posts.map((p) => p.events.map((e) => e.locator)), [['docs/a.md']]);
});

test('the press sends no sub-resource list: the server reads the record', async () => {
  const { document, server } = await setUp();
  segOf(document, 'docs/a.md', 'include').click();
  await wait(150);
  document.querySelector('[data-curate-go]').click();
  await wait(150);
  assert.equal('sub_resources' in commitBody(server), false);
});

/* ── item 3: the mark is not a check ───────────────────────────────────── */

test('plan rows carry a short word, never a check mark that reads as accepted or published', async () => {
  const { document } = await setUp();
  const host = document.getElementById('curate-host');
  for (const id of ['curate-sec-what-holds', 'curate-sec-relates']) {
    assert.doesNotMatch(norm(document.getElementById(id)), /✓/, `${id}: no green check on a survey row`);
  }
  const chips = [...document.getElementById('curate-sec-what-holds').querySelectorAll('[data-row-found]')];
  assert.deepEqual(chips.map((c) => norm(c)), ['none found', 'found']);
  assert.match(chips[1].title, /nothing here has been accepted or published/);
  assert.equal(norm(host.querySelector('#curate-sec-relates [data-row-found]')), 'info only');
  const cue = holds(document).querySelector('[data-cue="measured"]');
  assert.match(norm(cue), /● surveyed/);
  assert.doesNotMatch(norm(cue), /✓/);
  assert.match(cue.className, /text-ink-muted/);
  assert.doesNotMatch(cue.outerHTML, /text-state-ok/, 'green is reserved for in Egeria');
  assert.match(cue.title, /nothing here is accepted or published yet/);
});

/* ── item 4: immediate feedback on accept ──────────────────────────────── */

test('accept all: the pressed control shows pending the instant it is pressed, then settled', async () => {
  const gate = deferred();
  const { document, server } = await setUp({ hold: { verdict: null } });
  server.hold.verdict = gate.p;
  const b = document.querySelector('[data-branch-verdict="accepted"]');
  b.click();                                   // components === 1: no dialog, goes straight out
  assert.equal(b.dataset.phase, 'pending', 'changed synchronously on press');
  assert.match(norm(b), /accepting…/);
  assert.ok(b.disabled, 'a second press is ignored');
  assert.match(norm(document.getElementById('component-tree-status')), /recording…/);
  gate.resolve();
  await wait(100);
  assert.match(norm(document.getElementById('component-tree-status')), /1 verdict recorded/);
});

test('reject all: pending cue on press as well', async () => {
  const gate = deferred();
  const { document, server } = await setUp();
  server.hold.verdict = gate.p;
  const b = document.querySelector('[data-branch-verdict="rejected"]');
  b.click();
  assert.equal(b.dataset.phase, 'pending');
  assert.match(norm(b), /rejecting…/);
  gate.resolve();
  await wait(100);
});

test('a failed accept shows an error state on the control, which can be pressed again', async () => {
  const { document, server } = await setUp({ verdictStatus: 500 });
  const b = document.querySelector('[data-branch-verdict="accepted"]');
  b.click();
  await wait(100);
  assert.equal(b.dataset.phase, 'error');
  assert.match(norm(b), /failed · press to retry/);
  assert.equal(b.disabled, false);
  assert.match(norm(document.getElementById('component-tree-status')), /not recorded — boom/);
  server.verdictStatus = 200;
  b.click();
  assert.equal(b.dataset.phase, 'pending');
  await wait(100);
});

/* ── item 10: Catalog says what it does ────────────────────────────────── */

test('Catalog: the press is acknowledged with a cue and a short word; the tooltip says it publishes the survey already kept (no survey is run)', async () => {
  const gate = deferred();
  const { document, server } = await setUp();
  document.querySelector('[data-curate-pick]').click();   // nothing is pre-ticked any more: the owner ticks a line
  await wait(150);
  server.hold.commit = gate.p;
  const go = document.querySelector('[data-curate-go]');
  assert.match(go.title, /Publishes the survey already kept/);
  assert.match(go.title, /does not run a survey/);
  go.click();
  assert.match(norm(go), /^◔ publishing…$/);
  assert.ok(go.disabled);
  const hint = go.nextElementSibling;
  assert.equal(norm(hint), '', 'no sentence narrating the steps (owner 2026-10-11: acknowledge, then validate)');
  gate.resolve();
  await wait(100);
});

test('Catalog: a failed commit restores the button and says why', async () => {
  const { document } = await setUp({ commitStatus: 500 });
  document.querySelector('[data-curate-pick]').click();   // nothing is pre-ticked any more: the owner ticks a line
  await wait(150);
  const go = document.querySelector('[data-curate-go]');
  go.click();
  await wait(150);
  assert.equal(go.disabled, false);
  assert.equal(norm(go), 'Publish →');
  assert.match(norm(document.getElementById('curate-host')), /not published: boom/);
});

/* ── items 9 + 10: Publish band ────────────────────────────────────────── */

test('Publish band: the file types section comes before the Publish controls and has no press of its own', async () => {
  const { document } = await setUp();
  const band = document.querySelector('[data-curate-band="publish"]');
  const ft = band.querySelector('[data-file-measurements-section]');
  const pub = band.querySelector('[data-publish-go]');
  assert.ok(ft && pub);
  assert.equal(band.querySelector('[data-file-types-section]'), null);
  assert.ok(ft.compareDocumentPosition(pub) & 4 /* FOLLOWING */, 'file types precede Publish');
  ft.open = true;
  ft.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait();
  assert.equal(ft.querySelector('[data-file-types-go]'), null);
  assert.match(norm(ft), /Files are published by selection on Curate/);
});

test('Publish: idle words say it publishes the kept survey (it never surveys); the press is acknowledged at once', async () => {
  const gate = deferred();
  const { document, server } = await setUp();
  server.hold.publish = gate.p;
  const band = document.querySelector('[data-curate-band="publish"]');
  const fb = band.querySelector('[data-publish-feedback]');
  assert.match(norm(fb), /publishes the kept survey/);
  assert.match(band.querySelector('[data-publish-go]').title, /does not run a survey/);
  band.querySelector('[data-publish-go]').click();
  assert.match(norm(band.querySelector('[data-publish-feedback]')), /◔ publishing…/);
  assert.doesNotMatch(norm(band.querySelector('[data-publish-feedback]')), /takes a while/);
  gate.resolve();
  await wait(100);
});


/* ── brief sections 1 and 2: the commit sends the box, and the table sits with the button ─────────── */

test('Catalog: the re-survey box is off by default, the press sends resurvey_stale false; ticking it sends true', async () => {
  const { document, server } = await setUp();
  document.querySelector('[data-curate-pick]').click();   // nothing is pre-ticked any more: the owner ticks a line
  await wait(150);
  const box = () => document.querySelector('[data-commit-resurvey]');
  assert.equal(box().checked, false);
  document.querySelector('[data-curate-go]').click();
  await wait(150);
  const first = server.calls.find((c) => c.method === 'POST' && c.url.endsWith('/curate/commit'));
  assert.equal(first.body.resurvey_stale, false);
  box().click();
  assert.equal(box().checked, true, 'the press shows at once');
  document.querySelector('[data-curate-go]').click();
  await wait(150);
  const posts = server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/curate/commit'));
  assert.equal(posts[posts.length - 1].body.resurvey_stale, true);
});

test('Catalog: the table with its button is in "what gets written", and the old hint is gone', async () => {
  const { document } = await setUp();
  const panel = document.querySelector('[data-repo-commit-panel]');
  assert.ok(panel && panel.querySelector('[data-repo-manifest]') && panel.querySelector('[data-curate-go]'));
  assert.equal(panel.querySelector('[data-curate-go]').textContent.trim(), 'Publish →');
  assert.doesNotMatch(document.getElementById('curate-host').textContent, /refreshing stale surveys only/);
  assert.match(panel.textContent.replace(/\s+/g, ' '), /1 report · 42 annotations/);
});


test('how it relates: the dependencies are ONE table with a kind column, and a runtime row can be confirmed there', async () => {
  const { document, server } = await setUp();
  await wait(100);
  const host = document.getElementById('curate-sec-relates').querySelector('[data-dependency-table-host]');
  assert.equal(host.querySelector('[data-dep-heading]').textContent.trim(), 'Dependencies · by kind');
  assert.equal(host.querySelectorAll('[role=table]').length, 1);
  assert.match(host.querySelector('[data-dep-counts]').textContent.replace(/\s+/g, ' '), /1 build-time · 1 runtime/);
  const b = host.querySelector('[data-dep-confirm]');
  assert.ok(b, 'a proposed runtime row offers confirm');
  b.click();
  await wait(80);
  const posts = server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/dependencies/confirm'));
  assert.deepEqual(posts.map((p) => p.body), [{ keys: ['k2'], verdict: 'confirmed' }]);
});
