/** The repository commit on Contents: acknowledge the press, validate its success (owner, 2026-10-11:
 *  "we need to acknowledge that the user asked for a publish and validate its success - if there is a failure
 *  that might be a pop-up"). Pinned: the cue and short word on the press; "confirmed" only from the record's own
 *  read-back; the failure pop-up plus the persistent cue; the steps behind "details" collapsed; the
 *  running-commit line under the rail unchanged. */
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
    if (u.endsWith('/curate/commit') && s.commitFail) return err(s.commitFail === true ? 500 : s.commitFail, 'the registry is read-only');
    if (u.endsWith('/curate/commit')) return ok({ curation: { id: 'c1abcdef', state: 'running', author: 'dan', requested_at: new Date().toISOString(), steps: [{ name: 'a', state: 'running' }, { name: 'b', state: 'pending' }] }, activity_id: 'a1' });
    if (u.endsWith('/curate/commits/latest')) return ok({ curation: s.latest });
    if (u.includes('/curate/commits/') && s.record) return ok(typeof s.record === 'function' ? s.record() : s.record);
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


const STEPS_OK = (assetDetail) => [
  { name: 'publish_asset', state: 'done', detail: assetDetail },
  { name: 'classifications', state: 'skipped', detail: 'none set' },
  { name: 'sub_resources', state: 'done', detail: '2 of 2 published · 2 read back' },
  { name: 'components', state: 'skipped', detail: 'nothing to do' },
];
const REC = (over = {}) => ({ id: 'c1abcdef', state: 'done', author: 'dan', requested_at: '2026-10-11T08:00:00', finished_at: '2026-10-11T08:01:00',
  steps: STEPS_OK('published · from the survey of 2026-10-07 · asset g · survey report r · 42 annotations linked'),
  proof_summary: { report: { state: 'published', read_at: '2026-10-11T08:01:00' }, confirm: { state: 'confirmed', word: 'published 10-11 · confirmed' }, sub_resources: { read_back: 2, sent: 0, failed: 0 } }, ...over });
const press = async (document, server) => {
  server.scope.events.push({ locator: 'docs/a.md', choice: 'include', action: 'set', author: 'dan' });
  await openCurate(document);
  await tab(document, 'curate_contents');
  const go = document.querySelector('[data-curate-go]');
  go.click();
  return go;
};

test('the press is acknowledged at once: a running cue and a short word, no narrated steps', async () => {
  const { document, server } = await setUp({ activity: 'running' });
  const go = await press(document, server);
  assert.equal(go.disabled, true);
  assert.ok(go.querySelector('[data-cue="running"]'), 'a cue on the button');
  assert.equal(norm(go), '◔ publishing…');
  await wait(300);
  const res = document.querySelector('[data-curate-record] [data-commit-result]');
  assert.equal(res.dataset.commitResult, 'running');
  assert.equal(norm(res), '◔ publishing…');
  assert.ok(!/re-surveying|publishing the survey already kept/.test(norm(document.querySelector('[data-repo-commit-panel]'))), 'no sentence narrating the steps');
});

test('"confirmed" only when the record\'s own read-back verified it', async () => {
  const a = await setUp({ record: REC() });
  await press(a.document, a.server);
  await wait(2400);
  const ok = a.document.querySelector('[data-curate-record] [data-commit-result]');
  assert.equal(ok.dataset.commitResult, 'confirmed');
  assert.match(norm(ok), /^✓ published 10-11 · confirmed$/);
  // sent but not read back: the same press, a different record, a different word
  const b = await setUp({ record: REC({ steps: STEPS_OK('sent, not yet read back · from the survey of 2026-10-07'), proof_summary: { report: { state: 'sent' }, sub_resources: { sent: 0, failed: 0 } } }) });
  await press(b.document, b.server);
  await wait(2400);
  const un = b.document.querySelector('[data-curate-record] [data-commit-result]');
  assert.equal(un.dataset.commitResult, 'unconfirmed');
  assert.match(norm(un), /published 10-11 · not yet confirmed/);
  assert.doesNotMatch(norm(un), /· confirmed/);
  // the step says read back but the proof rows do not hold the report: not confirmed either
  const c = await setUp({ record: REC({ proof_summary: { report: { state: 'none' }, sub_resources: { sent: 0, failed: 0 } } }) });
  await press(c.document, c.server);
  await wait(2400);
  assert.equal(c.document.querySelector('[data-curate-record] [data-commit-result]').dataset.commitResult, 'unconfirmed');
});

test('a failure is a pop-up with the reason and the failed items, and a persistent cue on the panel', async () => {
  const steps = STEPS_OK('published').map((st) => st.name === 'sub_resources' ? { ...st, state: 'failed', detail: '1 of 2 published · not published: docs/b.md' } : st);
  const { document, server } = await setUp({ record: REC({ state: 'failed', steps }) });
  await press(document, server);
  await wait(2400);
  const pop = document.querySelector('[data-publish-failure]');
  assert.ok(pop, 'the pop-up');
  assert.match(norm(pop), /sub_resources/);
  assert.match(norm(pop), /not published: docs\/b\.md/);
  assert.equal(pop.querySelectorAll('[data-publish-failed-item]').length, 1);
  pop.querySelector('[data-publish-failure-close]').click();
  assert.equal(document.querySelector('[data-publish-failure]'), null, 'closed');
  const res = document.querySelector('[data-curate-record] [data-commit-result]');
  assert.equal(res.dataset.commitResult, 'failed');
  assert.ok(res.querySelector('[data-cue="partial"]'));
  assert.match(norm(res), /^◐ not finished · 1 of 2 published/);
  // the request itself refused: pop-up and cue too
  const b = await setUp({ commitFail: true });
  await press(b.document, b.server);
  await wait(300);
  assert.ok(b.document.querySelector('[data-publish-failure]'));
  assert.match(norm(b.document.querySelector('[data-repo-commit-panel] [data-commit-result="failed"]')), /not finished · .*read-only/);
});

test('the steps are behind "details" and collapsed by default; the data is unchanged', async () => {
  const { document, server } = await setUp({ record: REC() });
  await press(document, server);
  await wait(2400);
  const d = document.querySelector('[data-curate-record] [data-commit-details]');
  assert.ok(d && d.tagName === 'DETAILS');
  assert.equal(d.open, false);
  assert.match(norm(d.querySelector('summary')), /^details ▸$/);
  assert.equal(d.querySelectorAll('[data-commit-step]').length, 4, 'all four steps are still in the data');
  assert.ok(d.querySelector('[data-commit-header]'));
  assert.equal(document.querySelector('[data-curate-record] > [data-commit-step]'), null);
  assert.equal(document.querySelectorAll('[data-curate-record] [data-commit-step]').length, 4);
});

test('the running-commit line under the rail keeps its one line and its words', async () => {
  const { document, server, app } = await setUp({ activity: 'running' });
  await press(document, server);
  await wait(300);
  const lines = document.querySelectorAll('[data-curate-commit-line]');
  assert.equal(lines.length, 1);
  assert.equal(lines[0].previousElementSibling.hasAttribute('data-subtab-rail'), true);
  assert.match(norm(lines[0]), /^◔ Publish · commit c1ab · step 1 of 2 · running · Contents ›$/);
  assert.ok(!lines[0].querySelector('[data-commit-result]') && !lines[0].querySelector('details'));
  server.activity = 'success';
  await wait(2400);
});

test('confirmed is decided by the server\'s one function: pending or dead annotations are never "confirmed"; no proof summary is not confirmed', async () => {
  const sub = { sent: 0, failed: 0 };
  const rep = { state: 'published', read_at: '2026-10-11T08:01:00' };
  const cases = [
    [{ report: rep, confirm: { state: 'unconfirmed', word: 'published 10-11 · not yet confirmed · 3 sending' }, sub_resources: sub }, 'unconfirmed', /not yet confirmed · 3 sending/],
    [{ report: rep, confirm: { state: 'differs', word: 'published 10-11 · Egeria shows 40 · 2 not sent' }, sub_resources: sub }, 'differs', /Egeria shows 40 · 2 not sent/],
    [{ report: rep, sub_resources: sub }, 'unconfirmed', /not yet confirmed/],
  ];
  for (const [ps, state, re] of cases) {
    const { document, server } = await setUp({ record: REC({ proof_summary: ps }) });
    await press(document, server);
    await wait(2400);
    const res = document.querySelector('[data-curate-record] [data-commit-result]');
    assert.equal(res.dataset.commitResult, state);
    assert.match(norm(res), re);
    assert.doesNotMatch(norm(res), /· confirmed$/);
  }
  const { document, server } = await setUp({ record: { ...REC(), proof_summary: undefined } });
  await press(document, server);
  await wait(2400);
  assert.equal(document.querySelector('[data-curate-record] [data-commit-result]').dataset.commitResult, 'unconfirmed');
});

test('a refused request (401, 403, 409) offers no Retry; a failed step of a finished record does', async () => {
  for (const status of [401, 403, 409]) {
    const b = await setUp({ commitFail: status });
    await press(b.document, b.server);
    await wait(300);
    const pop = b.document.querySelector('[data-publish-failure]');
    assert.ok(pop, `pop-up on ${status}`);
    assert.equal(pop.querySelector('[data-publish-failure-retry]'), null, `no Retry on ${status}`);
    assert.match(norm(pop), /not permitted/);
  }
  const steps = STEPS_OK('published').map((st) => st.name === 'sub_resources' ? { ...st, state: 'failed', detail: 'boom' } : st);
  const c = await setUp({ record: REC({ state: 'failed', steps }) });
  await press(c.document, c.server);
  await wait(2400);
  assert.ok(c.document.querySelector('[data-publish-failure-retry]'), 'Retry stays for a failed step');
});

test('the idle hint under Publish does not narrate the steps', async () => {
  const { document, server } = await setUp();
  server.scope.events.push({ locator: 'docs/a.md', choice: 'include', action: 'set', author: 'dan' });
  await openCurate(document);
  await tab(document, 'curate_contents');
  assert.doesNotMatch(norm(document.querySelector('[data-repo-commit-panel]')), /each step reports|a queued run/);
});
