/** A Curate pane against a stub server, for the Brief A tests (Accept is a decision, Publish writes; the row
 *  choice for a parent; the visible selection; the five Curate part 2 follow-ups). Real app.js and router; the
 *  stub answers fetch and records every call. Same shape as curate-cleanup-part2.test.mjs's, with the
 *  architecture Publish endpoints added. */
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';

globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };
export const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
export const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const row = (o) => ({ evidence: '', source: 'manifest_parse', state: 'measured', count: null, members: null, candidate: true, detail: {}, ...o });
export const planBase = () => ({
  technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: null,
  what_it_is: [row({ kind: 'Library', label: 'A library' })], what_it_holds: [], relates: [], commits: [], writes: {}, keeps_current: 'x',
  made_of: [{ detail: { reviewed: 14, blueprints_reviewed: 14, blueprints_accepted: 14, blueprint_kinds: [] } }],
  survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] },
  project: { status: 'linked', word: 'project', name: 'P' },
});
/** A parent that is itself a component with two children; and a grouping-only branch. */
export const PARENT = { path: 'packages/x', name: 'x', components: 3, children: 2, grouping_only: false, accepted: 0, rejected: 0,
  undecided: 3, low_confidence: 0, types: {}, type: 'Service', ports: 0, own_ports: [], verdict: null };
export const GROUPING = { ...PARENT, path: 'packages/g', name: 'g', components: 4, children: 4, grouping_only: true, type: '' };
export const emptyPlan = () => ({
  slug: 'egeria_git', components: { to_write: [], in_egeria: 0, rejected_in_egeria: 0 },
  blueprints: { to_write: [], in_egeria: 0, rejected_in_egeria: 0 }, label: 'Nothing to publish', nothing: true,
  last: { run: '', at: '', items: [] },
});
const leaf = (p, v, extra = {}) => ({ path: p, type: 'Service', confidence: 90, verdict: v ? { verdict: v } : null, proposals: [], ports: [], ...extra });

export function makeServer(over = {}) {
  const s = {
    calls: [], plan: planBase(), verdictState: {}, scope: makeScopeStore(), blueprints: { blueprints: [], perspectives: [] },
    branches: [PARENT], publishPlan: emptyPlan(), leafRows: null, holdPlan: null, holdBlueprints: null, holdVerdicts: null,
    ...over,
  };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    const scoped = await s.scope.handle(u, method, body, ok, err);
    if (scoped) return scoped;
    if (u.endsWith('/components/verdicts')) {
      if (s.holdVerdicts) await s.holdVerdicts(body);
      body.scope_locators.forEach((p) => { s.verdictState[p] = body.verdict; });
      return ok({ verdicts: body.scope_locators.map((x) => ({ scope: x })), queued: 0, run_id: null });
    }
    if (u.endsWith('/architecture/publish-plan')) return ok(s.publishPlan);
    if (u.endsWith('/architecture/publish')) {
      if (s.publishFail) return err(s.publishFail, s.publishFailDetail || 'a publish is already running for this repository');
      s.publishPlan = s.afterPublish || s.publishPlan;
      return ok({ run_id: 'r1', activity_id: 'a1', queued: 1 });
    }
    if (u.includes('/api/activity/a1')) return ok({ id: 'a1', status: 'ok' });
    if (u.includes('/curate/plan')) { if (s.holdPlan) await s.holdPlan; if (s.planFail) return err(500, 'plan boom'); return ok(s.plan); }
    if (u.includes('/components/leaves')) {
      if (s.leavesFail) return err(500, 'boom');
      const g = ['packages/x/compose/a', 'packages/x/compose/b'].map((p) => leaf(p, s.verdictState[p]));
      const g2 = ['packages/x/other/c', 'packages/x/other/d'].map((p) => leaf(p, s.verdictState[p]));
      const solo = leaf('packages/x/solo', s.verdictState['packages/x/solo'], (s.leafRows || {})['packages/x/solo'] || {});
      const cnt = (m) => ({ accepted: m.filter((l) => l.verdict).length, rejected: 0, undecided: m.filter((l) => !l.verdict).length });
      return ok({ leaves: [...g, ...g2, solo],
        groups: [{ name: 'compose', members: g, ...cnt(g) }, { name: 'other', members: g2, ...cnt(g2) }], ungrouped: [solo] });
    }
    if (u.includes('/components/tree')) {
      s.treeCalls = (s.treeCalls || 0) + 1;
      if (s.holdTree) await s.holdTree(s.treeCalls);
      if (s.treeFail) return err(500, 'tree boom');
    }
    if (u.includes('/components/tree')) return ok({ branches: s.branches, total_components: 3, accepted: 0, reviewed: 0, topology: '' });
    if (u.includes('/components/blueprints')) {
      const n = s.bpCalls = (s.bpCalls || 0) + 1;
      if (s.holdBlueprints) await s.holdBlueprints(n);
      return ok(s.blueprintsFn ? s.blueprintsFn(n) : s.blueprints);
    }
    if (u.endsWith('/dependencies')) {
      if (s.depsReject) return ok({ kinds: null, counts: null, rows: null });      // a body the table cannot draw
      if (s.depsFail) return err(500, 'deps boom');
      return ok({ heading: 'Dependencies · by kind', kinds: [], counts: {}, runtime_state: '', rows: [] });
    }
    if (u.endsWith('/publish-state')) return ok({ slug: 's', in_egeria: false, asset_guid: '', row: { word: 'none' }, project: { status: 'unset', word: 'no project' }, can_publish_again: false, survey: { exists: false } });
    if (u.endsWith('/file-type-measurements')) return ok({ inventoried: true, profiles: [], retired: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

export async function setUp(over = {}, { settle = 400, open = true, resourceType = 'repo' } = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  if (open) for (const id of ['made-of', 'blueprints', 'relates', 'writes']) window.localStorage.setItem(`re.curate.collapsed.curate-sec-${id}`, '0');
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  const server = makeServer(over);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.HTMLElement.prototype.scrollIntoView = window.HTMLElement.prototype.scrollIntoView || function () {};   // jsdom has none
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType, selectedSlug: 'egeria_git', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'dan' }, curate: {},
    curateSelection: null, groups: [], projects: [{ slug: 'egeria_git' }], perspectives: [], allPerspectives: [] });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(settle);
  // A repository's sections live on the Contents and Structure tabs; an open section reads when its tab first shows.
  if (open && resourceType === 'repo') for (const tab of ['curate_contents', 'curate_structure']) { document.querySelector(`[data-subtab="${tab}"]`).click(); await wait(60); }
  return { document, app, server, window };
}
export const verdictPosts = (server) => server.calls.filter((c) => c.url.endsWith('/components/verdicts'));
export const openBranch = async (document, path = 'packages/x') => {
  document.querySelector(`[data-branch-open="${path}"]`).click();
  await wait(150);
};
