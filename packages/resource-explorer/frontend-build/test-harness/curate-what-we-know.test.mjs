/** Brief S1b: tab 1 of a repository's Curate opens with a read-only "What we know" block (the confirmed Context
 *  facts, each with who declared it and when, "edit on Context ›"), and the page is ordered header, What we know,
 *  Findable, What people say, then Publish to Egeria LAST. Real app.js and router over a stub server. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { makeScopeStore } from './repo-scope-kit.mjs';

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


const fact = (key, label, shown, o = {}) => ({ key, label, shown, declared: !!shown, skipped: false, author: shown ? 'dwolfson' : '',
  set_at: shown ? '2026-10-02T09:00:00' : '', interim: false, review: false, unsigned: false, ...o });
const KNOW = [
  fact('owner', 'Owner', 'dwolfson'),
  fact('sensitivity', 'Confidentiality', 'Internal', { author: 'mandy', set_at: '2026-10-03T09:00:00' }),
  fact('criticality', 'Criticality', ''),
  fact('retention', 'Retention', 'Keep while in use', { author: '', unsigned: true, set_at: '' }),
  fact('licence', 'License', 'Apache-2.0', { interim: true }),
  { ...fact('documentation', 'Documentation sources', 'Runbook', { author: 'dwolfson', set_at: '2026-10-04T09:00:00' }),
    sources: [{ label: 'Runbook', url: 'https://x/y', source_type: 'runbook', author: 'dwolfson', set_at: '2026-10-04T09:00:00' }] },
];
const withKnow = (know = KNOW) => { const plan = planBase(); plan.know = know; return { plan }; };
const block = (document) => document.querySelector('[data-curate-band="know"]');

test('the block renders each fact with who declared it and when', async () => {
  const { document } = await setUp(withKnow());
  await openCurate(document);
  const owner = block(document).querySelector('[data-know-fact="owner"]');
  assert.match(norm(owner), /Owner\s*dwolfson · declared by dwolfson 10-02 edit on Context ›/);
  assert.match(norm(block(document).querySelector('[data-know-fact="sensitivity"]')), /Internal · declared by mandy 10-03/);
  const docs = block(document).querySelector('[data-know-fact="documentation"]');
  assert.match(norm(docs), /Documentation sources\s*1/);
  assert.match(norm(docs.querySelector('[data-know-source]')), /Runbook · declared by dwolfson 10-04/);
});

test('a missing fact is a cue and a short word, an unsigned or interim one shows its state', async () => {
  const { document } = await setUp(withKnow());
  await openCurate(document);
  const missing = block(document).querySelector('[data-know-fact="criticality"]');
  assert.equal(missing.dataset.declared, 'no');
  assert.equal(missing.querySelector('[data-cue="unrun"]').textContent.trim().endsWith('not declared'), true);
  const unsigned = block(document).querySelector('[data-know-fact="retention"]');
  assert.match(norm(unsigned.querySelector('[data-cue="unknown"]')), /unsigned/);
  const interim = block(document).querySelector('[data-know-fact="licence"]');
  assert.match(norm(interim.querySelector('[data-cue="proposal"]')), /interim/);
  // read-only: nothing to type into or press here except the Context link
  assert.equal(block(document).querySelectorAll('input,textarea,select').length, 0);
  assert.equal([...block(document).querySelectorAll('button')].every((b) => b.hasAttribute('data-goto-context')), true);
});

test('"edit on Context ›" goes to Enrichment, Context', async () => {
  const { document, app } = await setUp(withKnow());
  await openCurate(document);
  block(document).querySelector('[data-know-fact="owner"] [data-goto-context]').click();
  await wait(200);
  assert.equal(app.state.stage, 'enrichment');
  assert.equal(app.state.subTab, 'context');
});

test('tab 1 is ordered: header, What we know, Findable, What people say, Publish to Egeria last', async () => {
  const { document } = await setUp(withKnow());
  await openCurate(document);
  const p = document.querySelector('[data-curate-tab-panel="describe"]');
  const order = [...p.children].map((c) => c.dataset.curateDescribeHead !== undefined ? 'head' : (c.dataset.curateBand || '?'));
  assert.deepEqual(order, ['head', 'know', 'findable', 'people', 'publish']);
  // the Publish band holds the state, the Publish and Re-survey presses and the reports list, and nothing after it
  const pub = p.querySelector('[data-curate-band="publish"]');
  for (const sel of ['[data-publish-go]', '[data-resurvey-go]', '[data-egeria-reports]']) assert.ok(pub.querySelector(sel), sel);
  assert.equal(pub.nextElementSibling, null);
});

test('with the plan unreadable the block says so instead of drawing empty', async () => {
  const { document } = await setUp({ planFail: true });
  await openCurate(document);
  assert.ok(block(document).querySelector('[data-curate-plan-error]'));
});

test('Brief F §10: each documentation source says whether the page\'s Publish has sent it (from its own row)', async () => {
  const know = KNOW.map((f) => (f.key === 'documentation'
    ? { ...f, sources: [{ ...f.sources[0], published: true }, { label: 'Wiki', url: 'https://w', source_type: 'wiki', author: 'dan', set_at: '2026-10-05T09:00:00', published: false }] }
    : f));
  const { document } = await setUp(withKnow(know));
  await openCurate(document);
  const rows = [...block(document).querySelectorAll('[data-know-source]')];
  assert.match(norm(rows[0]), /Runbook .* published/);
  assert.equal(rows[0].querySelector('[data-know-source-state] [data-cue]').dataset.cue, 'catalogued');
  assert.match(norm(rows[1]), /Wiki .* saved · will publish/);
  assert.equal(rows[1].querySelector('[data-know-source-state] [data-cue]').dataset.cue, 'stored');
});
