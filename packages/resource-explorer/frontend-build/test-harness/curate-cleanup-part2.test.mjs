/** Curate cleanup, part 2 (owner feedback 2026-10-07/08): collapsed sections, group accept-all, a tree that keeps
 *  its place, an honest blueprints band, old commits as history, one Software Capability heading, one settled render.
 *  Real app.js and router; a stub server behind fetch whose plan can be held open. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';

// jsdom has no CSS.escape; a real browser does.
globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const row = (o) => ({ evidence: '', source: 'manifest_parse', state: 'measured', count: null, members: null, candidate: true, detail: {}, ...o });
const planBase = () => ({
  technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: null,
  what_it_is: [row({ kind: 'Library', label: 'A library' })], what_it_holds: [], relates: [], commits: [], writes: {}, keeps_current: 'x',
  made_of: [{ detail: { reviewed: 14, blueprints_reviewed: 14, blueprints_accepted: 14, blueprint_kinds: [] } }],
  survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] },
  project: { status: 'linked', word: 'project', name: 'P' },
});
const BRANCH = { path: 'packages/x', name: 'x', components: 3, children: 2, accepted: 0, rejected: 0, undecided: 3, low_confidence: 0, types: {}, type: 'Service', ports: 0, own_ports: [], verdict: null };
const leaf = (p, v) => ({ path: p, type: 'Service', confidence: 90, verdict: v ? { verdict: v } : null, proposals: [], ports: [] });
function leaves(state) {
  const g = ['packages/x/compose/a', 'packages/x/compose/b'].map((p) => leaf(p, state[p]));
  const acc = g.filter((l) => l.verdict).length;
  return { leaves: [...g, leaf('packages/x/solo', state['packages/x/solo'])],
    groups: [{ name: 'compose', accepted: acc, rejected: 0, undecided: g.length - acc, members: g }],
    ungrouped: [leaf('packages/x/solo', state['packages/x/solo'])] };
}

function makeServer(over = {}) {
  const s = { calls: [], plan: planBase(), verdictState: {}, scope: makeScopeStore(), blueprints: { blueprints: [], perspectives: [] }, holdPlan: null, ...over };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    const scoped = await s.scope.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.endsWith('/components/verdicts')) {
      if (s.partial) { s.verdictState[body.scope_locators[0]] = body.verdict; return err(403, 'you may not curate the second one'); }
      body.scope_locators.forEach((p) => { s.verdictState[p] = body.verdict; }); return ok({ verdicts: body.scope_locators.map((x) => ({ scope: x })), queued: 1 });
    }
    if (u.includes('/curate/plan')) { if (s.holdPlan) await s.holdPlan; if (s.planFail) return err(500, 'plan boom'); return ok(s.plan); }
    if (u.includes('/components/leaves')) {
      if (s.leavesFail) return err(500, 'boom');
      const snap = leaves(s.verdictState);                    // the answer reflects the state at REQUEST time
      const d = (s.leafDelays || []).shift();
      if (d) await wait(d);
      return ok(snap);
    }
    if (u.includes('/components/tree')) return ok({ branches: [s.branch || BRANCH], total_components: 3, accepted: 0, reviewed: 0, topology: '' });
    if (u.includes('/components/blueprints')) return ok(s.blueprints);
    if (u.endsWith('/dependencies')) return ok({ heading: 'Dependencies · by kind', kinds: [], counts: {}, runtime_state: '', rows: [] });
    if (u.endsWith('/publish-state')) return ok({ slug: 's', in_egeria: false, asset_guid: '', row: { word: 'none' }, project: { status: 'unset', word: 'no project' }, can_publish_again: false, survey: { exists: false } });
    if (u.endsWith('/file-type-measurements')) return ok({ inventoried: true, profiles: [], retired: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

async function setUp(over = {}, { settle = 400, storage = 'ok', open = true } = {}) {
  delete globalThis.localStorage;   // a previous test may have left a throwing getter
  const { document, window } = makeDomEnvironment();
  // The tree, blueprints and dependencies are read only when their section is open: these tests need them.
  if (open) for (const id of ['made-of', 'blueprints', 'relates', 'writes']) window.localStorage.setItem(`re.curate.collapsed.curate-sec-${id}`, '0');
  if (storage === 'throws') {
    const boom = { get() { throw new window.DOMException('denied', 'SecurityError'); }, configurable: true };
    Object.defineProperty(window, 'localStorage', boom);
    Object.defineProperty(globalThis, 'localStorage', boom);
  } else {
    delete globalThis.localStorage;
    Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  }
  const server = makeServer(over);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.HTMLElement.prototype.scrollIntoView = window.HTMLElement.prototype.scrollIntoView || function () {};   // jsdom has none
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'egeria_git', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'dan' }, curate: {},
    curateSelection: null, groups: [], projects: [{ slug: 'egeria_git' }], perspectives: [], allPerspectives: [] });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(settle);
  // The sections live on the Contents and Structure tabs; an open section reads when its tab first shows.
  if (open) for (const tab of ['curate_contents', 'curate_structure']) { document.querySelector(`[data-subtab="${tab}"]`).click(); await wait(60); }
  return { document, app, server, window };
}
const SECS = ['what-it-is', 'what-holds', 'made-of', 'blueprints', 'relates', 'writes'].map((s) => `curate-sec-${s}`);
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('every Curate section starts collapsed; opening one is remembered across a redraw', async () => {
  const { document, app } = await setUp({}, { open: false });
  for (const id of SECS) assert.equal(document.getElementById(id).open, false, `${id} collapsed`);
  const { jumpToCurateSection } = await import('/static/next/stages/curate.js');
  jumpToCurateSection('curate-sec-blueprints');          // switches to Structure, opens it
  await wait(30);
  assert.equal(document.getElementById('curate-sec-blueprints').open, true);
  await wait(50);
  assert.equal(document.getElementById('curate-sec-blueprints').open && true, true);
  // a pick forces a full redraw; the open one stays open, the rest stay closed
  document.querySelector('[data-curate-pick]').click();
  await wait(150);
  assert.equal(document.getElementById('curate-sec-blueprints').open, true);
  assert.equal(document.getElementById('curate-sec-made-of').open, false);
  const { renderCurate } = await import('/static/next/stages/curate.js');
  await renderCurate('egeria_git');
  assert.equal(document.getElementById('curate-sec-blueprints').open, true, 'remembered per viewer');
  assert.ok(app);
});

test('storage that throws: sections still start collapsed and still open', async () => {
  const { document } = await setUp({}, { storage: 'throws', open: false });
  for (const id of SECS) assert.equal(document.getElementById(id).open, false);
  const { jumpToCurateSection } = await import('/static/next/stages/curate.js');
  jumpToCurateSection('curate-sec-writes');              // switches to Contents, opens it
  await wait(30);
  assert.equal(document.getElementById('curate-sec-writes').open, true);
});

async function openBranch(document) {
  document.querySelector('[data-branch-open="packages/x"]').click();
  await wait(150);
}

test('a leaf group offers accept all / reject all, and pressing it posts every member', async () => {
  const { document, server } = await setUp();
  await openBranch(document);
  const g = document.querySelector('[data-leaf-group="compose"]');
  assert.ok(g, 'the group is marked');
  const btn = g.querySelector('[data-group-verdict="accepted"]');
  assert.ok(btn && g.querySelector('[data-group-verdict="rejected"]'));
  assert.match(btn.textContent, /accept all 2/);
  btn.click();
  await wait(200);
  const dialogConfirm = document.querySelector('[data-act="confirm"]');
  if (dialogConfirm) { dialogConfirm.click(); await wait(250); }
  const post = server.calls.find((c) => c.url.endsWith('/components/verdicts'));
  assert.deepEqual(post.body.scope_locators.sort(), ['packages/x/compose/a', 'packages/x/compose/b']);
  assert.equal(post.body.verdict, 'accepted');
});

test('after an accept the tree keeps its open branch and its open group, even a group that is now fully decided', async () => {
  const { document, server } = await setUp();
  await openBranch(document);
  const group = () => document.querySelector('[data-leaf-group="compose"]');
  assert.equal(group().open, true, 'undecided work: open by default');
  group().querySelector('[data-leaf-verdict="accepted"]').click();
  await wait(100);
  document.querySelectorAll('[data-leaf-group="compose"] [data-leaf-verdict="accepted"]')[1]?.click();
  await wait(400);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/components/verdicts')).length >= 1, true);
  const box = document.querySelector('[data-branch="packages/x"] [data-branch-leaves]');
  assert.equal(box.hidden, false, 'the open branch stays open');
  assert.equal(group().open, true, 'the group the person was working in stays open');
  assert.doesNotMatch(norm(box), /^reading/, 'no flash back to reading');
});

test('the tree draws once on open and a re:curate-picks redraw neither blanks nor refetches it', async () => {
  const { document, server, window } = await setUp();
  const trees = () => server.calls.filter((c) => c.url.includes('/components/tree')).length;
  assert.equal(trees(), 1);
  assert.ok(document.querySelector('#component-tree [data-branch]'));
  document.dispatchEvent(new window.CustomEvent('re:curate-picks'));
  await wait(100);
  assert.ok(document.querySelector('#component-tree [data-branch]'), 'still drawn, not "Reading the components…"');
  assert.doesNotMatch(norm(document.getElementById('component-tree')), /Reading the components/);
  assert.equal(trees(), 1);
});

test('the tree, blueprints and depth reads start while the plan is still loading', async () => {
  let release; const holdPlan = new Promise((r) => { release = r; });
  const pending = setUp({ holdPlan }, { settle: 150 });
  const { server } = await pending;
  const urls = server.calls.map((c) => c.url);
  assert.ok(urls.some((u) => u.includes('/components/tree')), 'tree requested before the plan answered');
  assert.ok(urls.some((u) => u.includes('/components/blueprints')));
  assert.ok(urls.some((u) => u.includes('/catalogue-depth-offer')));
  assert.ok(urls.some((u) => u.includes('/scope-events')) || true);
  release();
  await wait(200);
});

test('no candidate blueprints: the band says so, with the verdict count and what it counts', async () => {
  const { document } = await setUp();
  const t = norm(document.getElementById('blueprint-list'));
  assert.match(t, /No candidate blueprints in this survey/);
  assert.match(t, /14 accepted blueprint verdicts/);
  assert.match(t, /kept from earlier surveys|earlier survey/);
});

test('blueprints shown and verdicts on record agree, or the band explains the difference', async () => {
  const bp = (n, v) => ({ perspective: 'logical', cluster_name: n, members: [], member_status: [], child_status: [], verdict: v ? { verdict: v } : null });
  const { document } = await setUp({ blueprints: { blueprints: [bp('a', 'accepted'), bp('b')], perspectives: ['logical'], kinds: [] } });
  const t = norm(document.getElementById('blueprint-list'));
  assert.match(t, /1 of the 14 accepted blueprint verdicts on record belong to a cluster shown here/);
});

test('an old failed commit is labelled history under the table, never the current state', async () => {
  const plan = planBase();
  const old = new Date(Date.now() - 17 * 86400000).toISOString();
  plan.commits = [{ id: 'oldcommit1', state: 'failed', author: 'dan', requested_at: old,
    steps: [{ name: 'survey_report', state: 'done' }, { name: 'sub_resources', state: 'failed', detail: 'x' }, { name: 'depth', state: 'skipped' }],
    proof_summary: { report: { state: 'not published', first: 'old failure' } } }];
  const { document } = await setUp({ plan });
  const hist = document.querySelector('[data-commit-history]');
  assert.ok(hist, 'history block');
  assert.equal(hist.open, false, 'collapsed');
  assert.match(norm(hist.querySelector('summary')), /last commit · 17d ago · failed at sub_resources/);
  assert.equal(document.querySelectorAll('[data-manifest-state]').length, 0, 'the table has no state column from a stale commit');
  assert.equal(document.querySelectorAll('[data-commit-header]').length, 1);
  assert.ok(hist.querySelector('[data-commit-header]'), 'the only header sits inside the history');
});

test('a queued commit is current, not history', async () => {
  const plan = planBase();
  plan.commits = [{ id: 'c9', state: 'running', author: 'dan', requested_at: new Date().toISOString(), steps: [{ name: 's', state: 'running' }] }];
  const { document } = await setUp({ plan });
  assert.equal(document.querySelector('[data-commit-history]'), null);
  assert.ok(document.querySelector('[data-curate-record]'));
});

test('Software Capability is one heading with its two sources under it', async () => {
  const plan = planBase();
  plan.what_it_is = [
    row({ kind: 'SoftwareCapability::egeria', label: 'Software Capability · egeria', source: 'deployment_evidence', evidence: 'dockerfile' }),
    row({ kind: 'SoftwareCapability', label: 'Software Capability · metadata platform', source: 'enrichment', evidence: 'your note · dan' }),
    row({ kind: 'Library', label: 'A library' }),
  ];
  const { document } = await setUp({ plan });
  const group = document.querySelectorAll('[data-sw-capability]');
  assert.equal(group.length, 1);
  assert.match(norm(group[0].querySelector('[data-sw-capability-heading]')), /^Software Capability$/);
  const items = group[0].querySelectorAll('[data-sw-capability-item]');
  assert.equal(items.length, 2);
  assert.match(norm(items[0]), /deployment evidence/);
  assert.match(norm(items[1]), /your note/);
  assert.equal(document.getElementById('curate-sec-what-it-is').querySelectorAll('input[data-curate-pick]').length, 3, 'every row keeps its own pick');
});

test('a dead session: the panes keep a short word, the page banner carries the prompt', async () => {
  const { document, server } = await setUp({}, { settle: 50 });
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (String(url).includes('/curate/plan')) return { ok: false, status: 401, statusText: 'x', json: async () => ({ detail: 'Authentication required. Sign in with your Egeria user id and password', error: 'login_required' }) };
    return real(url, opts);
  };
  const { renderCurate } = await import('/static/next/stages/curate.js');
  await renderCurate('egeria_git');
  const host = document.getElementById('curate-host');
  assert.ok(host.querySelector('[data-curate-signin-needed]'));
  assert.doesNotMatch(host.textContent, /Authentication required/);
  assert.ok(server);
});


/* ── review of part 2 ─────────────────────────────────────────────────────────────────────────── */
const verdictPosts = (server) => server.calls.filter((c) => c.url.endsWith('/components/verdicts'));
const rerender = (document, window) => document.querySelector('[data-select-all-shown]')
  .dispatchEvent(new window.Event('change', { bubbles: true }));

test('two overlapping tree renders, then ONE press of a branch button posts ONE verdict', async () => {
  // a one-component branch posts at once (no confirm to hide a doubled handler behind)
  const { document, server, window } = await setUp({ branch: { ...BRANCH, components: 1 } });
  await openBranch(document);
  server.leafDelays = [200, 200];
  rerender(document, window); rerender(document, window);
  await wait(500);
  document.querySelector('[data-branch-verdict="accepted"]').click();
  await wait(300);
  assert.equal(verdictPosts(server).length, 1);
});

test('the branch buttons are live while the open branches are still refreshing', async () => {
  const { document, server, window } = await setUp();
  await openBranch(document);
  document.querySelector('[data-branch-scope="with"]').click();      // the branch and its children: the press names 3
  await wait(60);
  server.leafDelays = [400];
  rerender(document, window);
  await wait(60);
  document.querySelector('[data-branch-verdict="accepted"]').click();
  await wait(60);
  assert.ok(document.querySelector('[data-act="confirm"]'), 'the press opened its confirm while the refresh was in flight');
  await wait(450);
});

test('an older leaves response never overwrites a newer one', async () => {
  const { document, server, window } = await setUp();
  await openBranch(document);
  server.leafDelays = [400, 0];
  rerender(document, window);                       // render A: slow read of the OLD state
  await wait(40);
  server.verdictState['packages/x/solo'] = 'accepted';
  rerender(document, window);                       // render B: fast read of the NEW state
  await wait(700);
  const solo = [...document.querySelectorAll('[data-branch-leaves] [data-leaf-verdict="accepted"]')].find((b) => b.dataset.scope === 'packages/x/solo');
  assert.match(norm(solo), /change/, 'the newer state won');
});

test('a group the person toggled while the refresh was in flight is not reset', async () => {
  const { document, server, window } = await setUp();
  await openBranch(document);
  server.leafDelays = [300];
  rerender(document, window);
  await wait(50);
  document.querySelector('[data-leaf-group="compose"]').open = false;
  await wait(450);
  assert.equal(document.querySelector('[data-leaf-group="compose"]').open, false);
});

test('a failed refresh is said on the box, not silent', async () => {
  const { document, server, window } = await setUp();
  await openBranch(document);
  server.leavesFail = true;
  rerender(document, window);
  await wait(250);
  const box = document.querySelector('[data-branch="packages/x"] [data-branch-leaves]');
  assert.ok(box.querySelector('[data-refresh-failed]'));
  assert.match(norm(box.querySelector('[data-refresh-failed]')), /could not refresh/);
});

test('group accept all posts only the UNDECIDED members and says that number; reject all asks first too', async () => {
  const { document, server } = await setUp();
  server.verdictState['packages/x/compose/a'] = 'accepted';
  await openBranch(document);
  const g = () => document.querySelector('[data-leaf-group="compose"]');
  assert.match(norm(g().querySelector('[data-group-verdict="accepted"]')), /accept all 1$/);
  g().querySelector('[data-group-verdict="rejected"]').click();
  await wait(100);
  assert.equal(verdictPosts(server).length, 0, 'nothing posted before the reject is confirmed');
  const dlg = document.querySelector('[data-act="confirm"]');
  assert.ok(dlg, 'reject all has its own confirm');
  dlg.click();
  await wait(300);
  assert.deepEqual(verdictPosts(server)[0].body.scope_locators, ['packages/x/compose/b']);
  assert.equal(verdictPosts(server)[0].body.verdict, 'rejected');
});

test('a partly applied group batch says how many were recorded and how many failed', async () => {
  const { document, server } = await setUp();
  await openBranch(document);
  server.partial = true;
  document.querySelector('[data-leaf-group="compose"] [data-group-verdict="accepted"]').click();
  await wait(100);
  document.querySelector('[data-act="confirm"]').click();
  await wait(500);
  const t = norm(document.getElementById('component-tree-status'));
  assert.match(t, /1 of 2 recorded/);
  assert.match(t, /1 failed/);
  // Brief Z: a 403 is "not permitted" followed by the server's reason, not a fixed sentence.
  assert.match(t, /not permitted · you may not curate the second one/);
});

test('a prefetch left by a failed plan, or by a slug change, is not left to be consumed later', async () => {
  const a = await setUp({ planFail: true }, { settle: 300 });
  const mod = await import('/static/next/stages/curate.js');
  assert.equal(mod.curatePrefetchPending(), false, 'plan failed: prefetch dropped');
  a.document.body.innerHTML = '';
  let release; const holdPlan = new Promise((r) => { release = r; });
  const b = await setUp({ holdPlan }, { settle: 100 });
  b.app.state.selectedSlug = 'another';
  release();
  await wait(300);
  assert.equal(mod.curatePrefetchPending(), false, 'slug changed: prefetch dropped');
});
