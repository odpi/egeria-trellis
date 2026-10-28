/** Behaviour (parity P3: PI-071, PI-072): jumping between a blueprint and its components in both directions,
 *  and finding a component / reading the tree by reading.
 *
 *  Real app.js, router and curate.js. ONLY `fetch` is replaced, by a stub shaped like the component routes
 *  (`/components/tree`, `/components/leaves` incl. the empty branch, `/components/blueprints`). Navigation and
 *  search only: every test asserts that nothing but GET went out, and that a verdict was never recorded.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';

globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const prop = (perspective) => ({ perspective, run_label: 'detect', type: 'Service', confidence: 90 });
const comp = (path, perspective) => ({ path, name: path.split('/').pop(), type: 'Service', confidence: 90,
  verdict: null, proposals: perspective ? [prop(perspective)] : [], ports: [], low_confidence: false });
const COMPS = [
  comp('packages/x/compose/a', 'physical'), comp('packages/x/compose/b', 'physical'),
  comp('packages/x/solo', 'logical'), comp('docs/site', 'logical'), comp('tools/lint', ''),
];
const branch = (path, n) => ({ path, name: path, components: n, children: n - 1, accepted: 0, rejected: 0, undecided: n,
  low_confidence: 0, types: {}, type: 'Service', ports: 0, own_ports: [], verdict: null });
const BRANCHES = [branch('packages', 3), branch('docs', 1), branch('tools', 1)];
const member = (path) => ({ slug: path.split('/').pop(), scope_locator: path, verdict: null, materialized: null });
const BP_LOGICAL = { perspective: 'logical', cluster_name: 'x-stack', members: ['a', 'solo'], verdict: null,
  member_status: [member('packages/x/compose/a'), member('packages/x/solo'), member('gone/away')], child_status: [] };
const BP_PHYSICAL = { perspective: 'physical', cluster_name: 'docs-bundle', members: ['site'], verdict: null,
  member_status: [member('docs/site')], child_status: [] };

function makeServer(over = {}) {
  const s = { calls: [], rootFails: false, blueprintsFail: false, blueprints: [BP_LOGICAL, BP_PHYSICAL], scope: makeScopeStore(), ...over };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    const scoped = await s.scope.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.includes('/curate/plan')) return ok({ technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: null,
      what_it_is: [], what_it_holds: [], relates: [], commits: [], writes: {}, keeps_current: 'x', made_of: [{ detail: {} }],
      survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 1, steps: 1, stale_steps: 0, stale: [] },
      project: { status: 'linked', word: 'project', name: 'P' } });
    if (u.includes('/components/leaves')) {
      const b = decodeURIComponent((u.split('branch=')[1] || '').split('&')[0]);
      if (b === '' && s.rootFails) return err(500, 'root read failed');
      if (b === '' && u.includes('groups=false')) {
        const slim = COMPS.map((c) => ({ path: c.path, name: c.name, type: c.type, readings: c.proposals.length ? [...new Set(c.proposals.map((p) => p.perspective))] : [''] }));
        const shown = slim.slice(0, s.cap || slim.length);
        return ok({ branch: '', leaves: shown, total: s.finderTotal ?? slim.length, shown: shown.length, truncated: shown.length < slim.length });
      }
      const leaves = COMPS.filter((c) => !b || c.path === b || c.path.startsWith(`${b}/`));
      const groupMembers = leaves.filter((l) => l.path.startsWith('packages/x/compose/'));
      return ok({ leaves, groups: groupMembers.length ? [{ name: 'compose', accepted: 0, rejected: 0, undecided: groupMembers.length, members: groupMembers }] : [],
        ungrouped: leaves.filter((l) => !groupMembers.includes(l)) });
    }
    if (u.includes('/components/tree')) return ok({ branches: BRANCHES, total_components: s.treeTotal ?? COMPS.length, accepted: 0, reviewed: 0, topology: '' });
    if (u.includes('/components/blueprints')) {
      if (s.blueprintsFail) return err(500, 'blueprint read failed');
      return ok({ blueprints: s.blueprints, perspectives: [...new Set(s.blueprints.map((b) => b.perspective))].sort(), kinds: [] });
    }
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

async function setUp(over = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  for (const id of ['made-of', 'blueprints']) window.localStorage.setItem(`re.curate.collapsed.curate-sec-${id}`, '0');
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  const server = makeServer(over);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'egeria_git', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'dan' }, curate: {},
    curateSelection: null, groups: [], projects: [{ slug: 'egeria_git' }], perspectives: [], allPerspectives: [],
    componentSearch: '', componentReading: '', componentReadingsOpen: false, componentShowAll: false, blueprintShowAll: false, blueprintReading: null });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  window.HTMLElement.prototype.scrollIntoView = window.HTMLElement.prototype.scrollIntoView || function () {};   // jsdom has none
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(500);
  // The tree and the blueprints are on the Structure tab; an open section reads when its tab first shows.
  document.querySelector('[data-subtab="curate_structure"]').click();
  await wait(100);
  return { document, window, app, server };
}
const writesOf = (server) => server.calls.filter((c) => c.method !== 'GET');
const typeInto = async (document, el, v) => { el.value = v; el.dispatchEvent(new document.defaultView.Event('input', { bubbles: true })); await wait(450); };
const branchEl = (document, p) => [...document.querySelectorAll('[data-branch]')].find((b) => b.dataset.branch === p);
const openBranch = async (document, p) => { branchEl(document, p).querySelector('[data-branch-open]').click(); await wait(250); };

/* ── PI-071: component -> blueprints ───────────────────────────────────── */

test('PI-071: a component row names the candidate blueprints it belongs to, and pressing one shows that blueprint', async () => {
  const { document, server } = await setUp();
  await openBranch(document, 'packages');
  const leaf = document.querySelector('[data-leaf-path="packages/x/solo"]');
  assert.ok(leaf.querySelector('[data-leaf-blueprints]'), 'the control is on the component row');
  leaf.querySelector('[data-leaf-blueprints]').click();
  await wait(150);
  const line = text(leaf.querySelector('[data-leaf-bp-line]'));
  assert.match(line, /member of x-stack \(logical reading\)/);
  leaf.querySelector('[data-leaf-bp]').click();
  await wait(300);
  const row = [...document.querySelectorAll('[data-blueprint]')].find((b) => b.dataset.blueprint === 'logical::x-stack');
  assert.ok(row, 'the blueprint row is drawn');
  assert.equal(row.dataset.jumped, '1', 'and marked as where the jump landed');
  assert.deepEqual(writesOf(server), [], 'navigation only: nothing but reads');
});

test('PI-071: a jump to a blueprint in ANOTHER reading switches the list to that reading', async () => {
  const { document } = await setUp();
  await openBranch(document, 'docs');
  const leaf = document.querySelector('[data-leaf-path="docs/site"]');
  leaf.querySelector('[data-leaf-blueprints]').click();
  await wait(150);
  leaf.querySelector('[data-leaf-bp]').click();
  await wait(300);
  const row = [...document.querySelectorAll('[data-blueprint]')].find((b) => b.dataset.blueprint === 'physical::docs-bundle');
  assert.ok(row && row.dataset.jumped === '1');
  assert.match(text(document.querySelector('[data-blueprint-reading-line]')), /physical reading/);
});

test('PI-071: a component in no blueprint says "in no candidate blueprint"; a failed read says it was not read', async () => {
  const a = await setUp();
  await openBranch(a.document, 'tools');
  const l1 = a.document.querySelector('[data-leaf-path="tools/lint"]');
  l1.querySelector('[data-leaf-blueprints]').click();
  await wait(150);
  assert.match(text(l1.querySelector('[data-leaf-bp-line]')), /in no candidate blueprint/);
  const b = await setUp({ blueprintsFail: true });
  await openBranch(b.document, 'tools');
  const l2 = b.document.querySelector('[data-leaf-path="tools/lint"]');
  l2.querySelector('[data-leaf-blueprints]').click();
  await wait(150);
  const t = text(l2.querySelector('[data-leaf-bp-line]'));
  assert.match(t, /blueprints not read/);
  assert.doesNotMatch(t, /in no candidate blueprint/);
});

/* ── PI-071: blueprint -> components ───────────────────────────────────── */

test('PI-071: a blueprint\'s member opens the tree on its branch and shows the component row', async () => {
  const { document, server } = await setUp();
  document.querySelector('[data-blueprint-members="logical::x-stack"]').click();
  await wait(100);
  const rail = document.getElementById('rail-evidence');
  const rows = [...rail.querySelectorAll('[data-member-row]')];
  assert.equal(rows.length, 3);
  assert.match(text(rows[0]), /a component .* show in tree/);
  rows[0].querySelector('[data-jump-member]').click();
  await wait(500);
  const leaf = document.querySelector('[data-leaf-path="packages/x/compose/a"]');
  assert.ok(leaf, 'the branch opened and the row is drawn');
  assert.equal(leaf.dataset.jumped, '1');
  assert.equal(leaf.closest('details[data-leaf-group]').open, true, 'its group is open');
  assert.deepEqual(writesOf(server), []);
});

test('PI-071: a member the survey no longer proposes says so next to the control, and a filter that hid the row is cleared', async () => {
  const { document, app } = await setUp();
  app.state.componentSearch = 'lint';
  document.querySelector('[data-blueprint-members="logical::x-stack"]').click();
  await wait(100);
  const rows = [...document.getElementById('rail-evidence').querySelectorAll('[data-member-row]')];
  rows[2].querySelector('[data-jump-member]').click();     // gone/away
  await wait(500);
  assert.match(text(rows[2]), /not in the component tree: this survey no longer proposes it/);
  assert.equal(app.state.componentSearch, '', 'the filter that could hide the target was cleared');
});

/* ── PI-072: search ────────────────────────────────────────────────────── */

test('PI-072: searching narrows the branches to those holding a match, counts them, and narrows an opened branch', async () => {
  const { document, server } = await setUp();
  const input = document.querySelector('[data-tree-search]');
  assert.ok(input, 'a search box sits above the tree');
  await typeInto(document, input, 'solo');
  assert.ok(server.calls.some((c) => c.url.includes('/components/leaves?branch=&groups=false')), 'every component was read once');
  const status = text(document.querySelector('[data-find-status]'));
  assert.match(status, /1 of 5 components match · in 1 of 3 branches/);
  assert.deepEqual([...document.querySelectorAll('[data-branch]')].map((b) => b.dataset.branch), ['packages']);
  assert.match(text(branchEl(document, 'packages').querySelector('[data-branch-matches]')), /1 match/);
  assert.equal(document.activeElement, document.querySelector('[data-tree-search]'), 'typing is not interrupted');
  await openBranch(document, 'packages');
  assert.match(text(document.querySelector('[data-leaf-narrowed]')), /narrowed 1 of 3 components under this branch/);
  assert.deepEqual([...document.querySelectorAll('[data-leaf-path]')].map((l) => l.dataset.leafPath), ['packages/x/solo']);
  document.querySelector('[data-tree-find-clear]').click();
  await wait(300);
  assert.equal(document.querySelectorAll('[data-branch]').length, 3);
  assert.equal(document.querySelector('[data-find-status]'), null);
  assert.deepEqual(writesOf(server), []);
});

test('PI-072: a search that matches nothing says "no component matches" (measured), not a blank list', async () => {
  const { document } = await setUp();
  await typeInto(document, document.querySelector('[data-tree-search]'), 'zzz-nothing');
  const status = text(document.querySelector('[data-find-status]'));
  assert.match(status, /no component matches/);
  assert.match(status, /0 of 5 components match · in 0 of 3 branches/);
  assert.equal(document.querySelectorAll('[data-branch]').length, 0);
});

test('PI-072: a search that cannot read the components says so and leaves the tree unfiltered', async () => {
  const { document } = await setUp({ rootFails: true });
  await typeInto(document, document.querySelector('[data-tree-search]'), 'solo');
  const status = text(document.querySelector('[data-find-status]'));
  assert.match(status, /search not read/);
  assert.match(status, /root read failed/);
  assert.match(status, /the tree below is not filtered/);
  assert.equal(document.querySelectorAll('[data-branch]').length, 3, 'nothing was hidden on a guess');
});

/* ── PI-072: reading toggle ────────────────────────────────────────────── */

test('PI-072: "by reading" counts the components per reading and filters the tree to the pressed one', async () => {
  const { document, server } = await setUp();
  assert.equal(document.querySelector('[data-tree-readings]'), null, 'nothing is read until asked for');
  document.querySelector('[data-tree-readings-open]').click();
  await wait(300);
  const chips = [...document.querySelectorAll('[data-tree-reading]')];
  assert.deepEqual(chips.map((c) => text(c)), ['● all 5', 'logical 2', 'physical 2', 'no reading recorded 1']);
  assert.equal(chips[0].getAttribute('aria-pressed'), 'true');
  chips[2].click();                      // physical
  await wait(300);
  assert.deepEqual([...document.querySelectorAll('[data-branch]')].map((b) => b.dataset.branch), ['packages']);
  assert.match(text(document.querySelector('[data-find-status]')), /2 of 5 components match · in 1 of 3 branches/);
  const on = [...document.querySelectorAll('[data-tree-reading]')].find((c) => c.getAttribute('aria-pressed') === 'true');
  assert.match(text(on), /^● physical 2$/);
  [...document.querySelectorAll('[data-tree-reading]')][3].click();     // no reading recorded
  await wait(300);
  assert.deepEqual([...document.querySelectorAll('[data-branch]')].map((b) => b.dataset.branch), ['tools']);
  assert.deepEqual(writesOf(server), []);
});

test('PI-072: componentMatches and readingsOf: name, path or type; "" is no reading recorded', async () => {
  await setUp();
  const { componentMatches, readingsOf } = await import('/static/next/stages/curate.js');
  const c = comp('packages/x/solo', 'logical');
  assert.equal(componentMatches(c, 'SOLO', ''), true);
  assert.equal(componentMatches(c, 'packages/x', ''), true);
  assert.equal(componentMatches(c, 'service', ''), true);
  assert.equal(componentMatches(c, 'solo', 'physical'), false);
  assert.deepEqual(readingsOf(comp('a', '')), ['']);
  assert.deepEqual(readingsOf({ proposals: [{ }, { perspective: 'logical' }] }), ['physical', 'logical']);
});

test('PI-072: the "of M" in the search is the header\'s own total (tree.total_components), never a second count', async () => {
  const { document } = await setUp({ treeTotal: 7 });
  await typeInto(document, document.querySelector('[data-tree-search]'), 'solo');
  assert.match(text(document.querySelector('[data-find-status]')), /1 of 7 components match/);
  document.querySelector('[data-tree-find-clear]').click();
  await wait(300);
  document.querySelector('[data-tree-readings-open]').click();
  await wait(300);
  assert.match(text(document.querySelector('[data-tree-reading=""]')), /^● all 7$/);
});

test('PI-072: a list the server capped is searched as "first N of M" with M from the server, never cut silently', async () => {
  const { document, server } = await setUp({ cap: 3 });
  await typeInto(document, document.querySelector('[data-tree-search]'), 'a');
  const status = text(document.querySelector('[data-find-status]'));
  assert.match(status, /first 3 of 5 searched/);
  const read = server.calls.find((c) => c.url.includes('groups=false'));
  assert.ok(read, 'the search asks for the slim, ungrouped read');
  assert.ok(!server.calls.some((c) => c.url.includes('branch=&') && !c.url.includes('groups=false')), 'no whole-set grouped read');
});
