/** Brief S2: a database's Curate is three tabs (Describe and publish / Contents / Structure). Real app.js and router,
 *  a stub server behind fetch that records every call. What is pinned:
 *    - every database control is on EXACTLY its tab and no other (Findable and its press stay on Describe; the scope tree,
 *      manifest, commit press, steps and "Read Egeria again" are on Contents; the proposals, the data-class rules and the
 *      dependencies line are on Structure);
 *    - Describe has no commit press, and its Publish band is LAST with ONE state line;
 *    - that state line equals the scope view's commit header (its state AND its sentence), and follows it when the commit
 *      records change; a registry row flagged published with no commit proof does NOT say published;
 *    - the line's "Contents ›" takes one click to the tab where the commit button is;
 *    - a tab switch re-reads nothing; the commit preview (it reads Egeria) waits for Contents and the data-class rules (they
 *      read Egeria) wait for Structure;
 *    - the tab counts equal the scope view's own numbers; Structure reads "none yet", never 0;
 *    - a commit started on Contents shows its one-line state on Describe and Structure. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { baseView, schema, table, person, wait, flat } from './scope-test-kit.mjs';

globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };

const COMMITTED_TEXT = 'Database element d0000001 in Egeria · published 10-05 09:00 · 1 schema chosen: 1 published';
const preview = () => ({
  can_commit: true, button: 'Publish · 1 schema', blockers: [], blocked_schemas: [], blocked_notes: [],
  attach: ['sales'], refused: [], leave_out: [], collisions: [],
  survey: { schemas: ['sales'], not_scopable: [], line: "Egeria's survey is limited to your chosen schemas" },
  manifest: { lines: [], schema_targets: 1, new_targets: 1, survey_schemas: ['sales'], zones: [], owner: '', whole_schemas_line: 'whole schemas' },
});
function viewWith(over = {}) {
  const v = baseView({
    declared: { declared: true, by: 'dwolfson', at: '2026-10-04T08:00:00', kind: 'first', baseline_survey_at: '' },
    schemas: [
      schema('sales', [table('sales', 'orders')], { explicit: person('catalogue'), effective: 'catalogue', state: 'chosen' }),
      schema('archive', [table('archive', 'orders')]),
      schema('plain', [table('plain', 'p1')]),
    ],
    counts: { schemas_offered: 3, schemas_catalogue: 1, schemas_leave_out: 0, schemas_undecided: 2 },
    ...over,
  });
  return v;
}

function makeServer(view, { rules = [], record = null, scopeFail = false, latest = null } = {}) {
  const s = { calls: [], view, rules, record, polls: 0 };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    s.calls.push({ method, url: u, body: opts.body ? JSON.parse(opts.body) : undefined });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.includes('/api/egeria/rules/dataclasses')) return ok(s.rules);
    if (u.includes('/api/catalogue-scope/')) {
      if (method === 'GET' && u.endsWith('/commit-preview')) return ok(preview());
      if (method === 'POST' && u.endsWith('/commit')) {
        s.record = { id: 'cafe0123456789', author: 'me', state: 'running', requested_at: new Date().toISOString(),
          steps: [{ name: 'publish_elements', state: 'running', detail: '' }, { name: 'schema_targets', state: 'pending', detail: '' }] };
        return ok({ curation: s.record, run_id: 'run00000-1', activity_id: 'a' });
      }
      if (method === 'GET' && u.endsWith('/commits/latest')) return ok(latest || { commit: null, terminal: null, age_hours: null, stale_unfinished: false, states: {} });
      if (method === 'GET' && u.includes('/commits/')) return ok(s.record);   // stays running: the line must show while it does
      if (method === 'GET' && scopeFail) return { ok: false, status: 500, statusText: 'registry down', json: async () => ({ detail: 'registry down' }) };
      if (method === 'GET') return ok(s.view);
      return ok({ state: 'ok' });
    }
    if (u.includes('/api/journal/')) return ok({ entries: [], suggested_to: [] });
    if (u.includes('/api/curate/') || u.includes('/api/projects/groups') || u.includes('/api/projects/?')) return ok([]);
    if (u.includes('/api/databases/')) return ok([{ slug: 'adventureworks', group_slug: '' }]);
    if (u.includes('/api/filesystems/')) return ok([{ slug: 'adventureworks', group_slug: '' }]);
    return ok({});
  };
  return s;
}

async function setUp(view, { search = '', rows = {}, initialTab = '', open = true, ...serverOpts } = {}) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  // A declared scope starts folded to one line; the commit panel reads Egeria only once the section is open.
  if (open) window.localStorage.setItem('re-next.scopeSection.me.adventureworks', 'open');
  const server = makeServer(view, serverOpts);
  ensureLoaderRegistered();
  (await import('/static/next/stages/curate-scope.js')).resetScopeUi();
  globalThis.location = window.location; globalThis.history = window.history;
  window.history.replaceState(null, '', `/next${search}`);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'adventureworks', stage: 'understanding', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'me' }, curate: {},
    groups: [], databasesLoaded: true, filesystemsLoaded: true, perspectives: [], allPerspectives: [],
    databases: [{ slug: 'adventureworks', group_slug: '', ...rows }], filesystems: [{ slug: 'adventureworks', group_slug: '' }] });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  if (initialTab) await app.openCurate({ tab: initialTab });      // what a pasted URL with tab= runs
  else document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait(500);
  return { document, window, app, server };
}
const tab = async (document, id) => { document.querySelector(`[data-subtab="${id}"]`).click(); await wait(250); };
const panels = (document) => Object.fromEntries([...document.querySelectorAll('[data-curate-tab-panel]')].map((p) => [p.dataset.curateTabPanel, p]));
const where = (document, sel) => Object.entries(panels(document)).filter(([, p]) => p.querySelector(sel)).map(([n]) => n);
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const reads = (server, frag) => server.calls.filter((c) => c.method === 'GET' && c.url.includes(frag)).length;

test('the leading tabs are on for a database, and Curate opens on Describe and publish', async () => {
  const { document, app } = await setUp(viewWith());
  assert.equal(app.state.subTab, 'curate_describe');
  const rail = norm(document.querySelector('[data-subtab-rail]'));
  assert.ok(rail.indexOf('Describe and publish') < rail.indexOf('Contents') && rail.indexOf('Contents') < rail.indexOf('Structure') && rail.indexOf('Structure') < rail.indexOf('Questions'), 'the three lead the strip');
  assert.equal(document.querySelectorAll('[data-curate-tab-panel]').length, 3);
  assert.equal(document.getElementById('question-rows'), null, 'a database no longer draws Curate inside the Questions frame');
});

test('each database control is on exactly its tab and on no other', async () => {
  const { document } = await setUp(viewWith(), { rules: [] });
  await tab(document, 'curate_contents');
  await tab(document, 'curate_structure');
  await tab(document, 'curate_describe');
  const TABLE = [
    // Describe and publish
    ['describe', '[data-curate-band="findable"]'], ['describe', '[data-curate-tag-input]'], ['describe', '[data-curate-tag-add]'],
    ['describe', '[data-curate-group-select]'], ['describe', '[data-curate-band="people"]'], ['describe', '[data-curate-fb-submit]'],
    ['describe', '#journal-save'], ['describe', '[data-curate-band="publish"]'], ['describe', '[data-db-publish-state]'],
    ['describe', '[data-db-goto-contents]'], ['describe', '[data-egeria-reports-host]'],
    // Contents
    ['contents', '[data-curate-scope]'], ['contents', '[data-scope-collapse]'], ['contents', '[data-scope-depth-radio]'],
    ['contents', '[data-scope-tree]'], ['contents', '[data-scope-row="schema:sales"]'], ['contents', '[data-scope-filter]'],
    ['contents', '[data-scope-commit-panel]'], ['contents', '[data-scope-manifest]'], ['contents', '[data-scope-commit-btn]'],
    ['contents', '[data-scope-refresh-now]'], ['contents', '[data-scope-read-back]'], ['contents', '[data-rc-reset-commit]'],
    ['contents', '[data-scope-commit-header]'],
    // Structure
    ['structure', '[data-curate-work="glossary"]'], ['structure', '[data-curate-work="schema-match"]'], ['structure', '[data-curate-rules]'],
    ['structure', '[data-curate-work="dependencies"]'], ['structure', '[data-curate-dependencies-none]'],
  ];
  for (const [name, sel] of TABLE) assert.deepEqual(where(document, sel), [name], `${sel} is on ${name} and nowhere else`);
});

test('Describe has no commit press of its own, and its Publish band is last', async () => {
  const { document } = await setUp(viewWith());
  const d = panels(document).describe;
  for (const sel of ['[data-scope-commit-btn]', '[data-publish-go]', '[data-resurvey-go]', '[data-scope-read-back]', '[data-scope-commit-panel]']) {
    assert.equal(d.querySelector(sel), null, `${sel} is not on Describe`);
  }
  assert.deepEqual([...d.querySelectorAll('[data-curate-band]')].map((e) => e.dataset.curateBand), ['findable', 'people', 'publish']);
  assert.equal(d.querySelectorAll('[data-db-publish-state]').length, 1, 'ONE state line');
});

test('the state line equals the commit header, and follows it', async () => {
  const committed = viewWith();
  committed.commit = { ...committed.commit, header: { state: 'committed', text: COMMITTED_TEXT } };
  const a = await setUp(committed);
  const lineA = a.document.querySelector('[data-db-publish-state]');
  assert.equal(lineA.dataset.dbPublishState, 'committed');
  assert.equal(lineA.querySelector('[data-db-publish-state-text]').textContent, COMMITTED_TEXT, 'the sentence is the header text, verbatim');
  assert.equal(lineA.querySelector('[data-db-publish-state-text]').textContent, a.document.querySelector('[data-scope-commit-header]').textContent, 'the same text the Contents header prints');
  assert.equal(a.document.querySelector('[data-scope-commit-header]').dataset.scopeCommitHeaderState, lineA.dataset.dbPublishState);
  const b = await setUp(viewWith());   // fixture: nothing committed
  const lineB = b.document.querySelector('[data-db-publish-state]');
  assert.equal(lineB.dataset.dbPublishState, 'not_committed');
  assert.match(norm(lineB), /not committed/);
  assert.notEqual(lineB.dataset.dbPublishState, lineA.dataset.dbPublishState, 'a different header gives a different line');
});

test('a registry row flagged published, with no commit proof, does not say published', async () => {
  const { document } = await setUp(viewWith(), { rows: { is_published: true, egeria_asset_guid: 'db-asset-guid' } });
  const line = document.querySelector('[data-db-publish-state]');
  assert.equal(line.dataset.dbPublishState, 'not_committed');
  // the cue and its word (the summary) never say published; the sentence behind it is the commit header's own
  assert.doesNotMatch(norm(line.querySelector('summary')), /published|in Egeria/);
  assert.match(norm(line.querySelector('summary')), /not committed/);
  // the asset GUID row still exists, on Contents
  assert.equal(panels(document).contents.querySelector('[data-publish-asset] [data-guid]').dataset.guid, 'db-asset-guid');
  assert.equal(panels(document).describe.querySelector('[data-publish-asset]'), null);
});

test('a scope view that cannot be read is "not read", never "not committed"', async () => {
  const { document } = await setUp(viewWith(), { scopeFail: true });
  const line = document.querySelector('[data-db-publish-state]');
  assert.equal(line.dataset.dbPublishState, 'unread');
  assert.match(norm(line), /not read/);
  assert.doesNotMatch(norm(line), /not committed/);
  assert.match(norm(document.querySelector('[data-subtab-rail]')), /Contents .*not read/);
});

test('the line\'s "Contents ›" is one click from the commit button', async () => {
  const { document, app } = await setUp(viewWith());
  assert.equal(panels(document).contents.hidden, true);
  document.querySelector('[data-db-goto-contents]').click();
  await wait(300);
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(panels(document).contents.hidden, false);
  assert.equal(panels(document).describe.hidden, true);
  const btn = panels(document).contents.querySelector('[data-scope-commit-btn]');
  assert.ok(btn && !btn.disabled, 'the commit button is on screen and ready');
});

test('a tab switch re-reads nothing; what reads Egeria waits for its tab', async () => {
  const { document, server } = await setUp(viewWith());
  assert.equal(reads(server, '/commit-preview'), 0, 'the commit preview reads Egeria: not at page load, Contents is hidden');
  assert.equal(reads(server, '/api/egeria/rules/dataclasses'), 0, 'the data-class rules read Egeria: not until Structure shows');
  const scopeReads = server.calls.filter((c) => c.method === 'GET' && /catalogue-scope\/adventureworks$/.test(c.url)).length;
  assert.equal(scopeReads, 1, 'the scope view is read once at page load');
  const before = server.calls.length;
  await tab(document, 'curate_contents');
  assert.equal(reads(server, '/commit-preview'), 1, 'Contents opened: the preview is read once');
  assert.equal(reads(server, '/api/egeria/rules/dataclasses'), 0);
  await tab(document, 'curate_structure');
  assert.equal(reads(server, '/api/egeria/rules/dataclasses'), 1, 'Structure opened: the rules are read once');
  await tab(document, 'curate_describe');
  await tab(document, 'curate_contents');
  await tab(document, 'curate_structure');
  assert.equal(reads(server, '/commit-preview'), 1, 'going back reads nothing again');
  assert.equal(reads(server, '/api/egeria/rules/dataclasses'), 1);
  assert.equal(server.calls.slice(before).filter((c) => /catalogue-scope\/adventureworks$/.test(c.url)).length, 0, 'the scope is not read again');
  assert.equal(server.calls.filter((c) => c.method === 'GET' && /catalogue-scope\/adventureworks$/.test(c.url)).length - scopeReads, 0);
});

test('a pasted Structure URL reads the rules at once and not the commit preview', async () => {
  const { server, app } = await setUp(viewWith(), { initialTab: 'curate_structure' });
  assert.equal(app.state.subTab, 'curate_structure');
  assert.equal(reads(server, '/api/egeria/rules/dataclasses'), 1);
  assert.equal(reads(server, '/commit-preview'), 0);
});

test('the Contents count equals the scope view\'s own numbers; Structure says "none yet", not 0', async () => {
  const { document } = await setUp(viewWith());
  const rail = norm(document.querySelector('[data-subtab-rail]'));
  assert.match(rail, /Contents ✓ 1 chosen · ◌ 2 undecided/);
  assert.match(rail, /Structure .*none yet/);
  assert.doesNotMatch(rail, /Structure[^A-Z]*0 awaiting/);
  const { scopeHeaderText } = await import('/static/next/stages/curate-scope.js');
  const view = viewWith();
  assert.match(scopeHeaderText(view), /Your scope: 1 of 3 schemas/, 'the section header prints the same chosen number');
  await tab(document, 'curate_contents');
  assert.match(norm(document.querySelector('[data-scope-manifest-row="undecided"]')), /Undecided .* 2/, 'the manifest prints the same undecided number');
  assert.match(norm(document.querySelector('[data-scope-header]')), /1 of 3 schemas/);
});

test('before the scope is read the counts say "reading", never 0', async () => {
  const { tabCounts } = await import('/static/next/stages/curate-counts.js');
  assert.deepEqual(tabCounts('curate_contents', { kind: 'db', view: null }).map((c) => c.word), ['reading', 'reading']);
  assert.deepEqual(tabCounts('curate_contents', { kind: 'db', view: null, viewFailed: true }).map((c) => c.word), ['not read', 'not read']);
  assert.deepEqual(tabCounts('curate_structure', { kind: 'db' }).map((c) => c.word), ['none yet']);
});

test('the counts follow the view after a choice (the tab label is re-drawn from the new view)', async () => {
  const { document, server } = await setUp(viewWith());
  await tab(document, 'curate_contents');
  server.view.counts = { ...server.view.counts, schemas_catalogue: 2, schemas_undecided: 1 };
  document.querySelector('[data-scope-depth-radio]').dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await wait(400);
  assert.match(norm(document.querySelector('[data-subtab-rail]')), /Contents ✓ 2 chosen · ◌ 1 undecided/);
});

test('a commit started on Contents shows its one-line state on Describe and Structure', async () => {
  const { document, server } = await setUp(viewWith());
  await tab(document, 'curate_contents');
  document.querySelector('[data-scope-commit-btn]').click();
  await wait(500);
  assert.equal(server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/commit')).length, 1);
  for (const id of ['curate_describe', 'curate_structure']) {
    await tab(document, id);
    const line = document.querySelector('[data-curate-commit-line]');
    assert.ok(line, `the running-commit line is under the rail on ${id}`);
    assert.match(norm(line), /running/);
  }
});

test('known negative: the hidden tabs hold no Contents controls while their panel is hidden from view', async () => {
  const { document } = await setUp(viewWith());
  assert.equal(panels(document).describe.hidden, false);
  assert.equal(panels(document).contents.hidden, true);
  assert.equal(panels(document).structure.hidden, true);
  assert.equal(flat(panels(document).describe).includes('What this commit does'), false, 'the commit panel is not drawn on Describe');
});

/* ── round 2: the word follows the chosen schemas' own states ─────────── */

const withCommit = (header, states) => {
  const v = viewWith();
  v.commit = { ...v.commit, header, schemas: { sales: { state: 'none' }, ...states } };
  return v;
};
const HDR = (text) => ({ state: 'committed', text });
const wordOf = (document) => {
  const l = document.querySelector('[data-db-publish-state]');
  return { how: l.dataset.dbPublishHow, word: norm(l.querySelector('summary')), line: norm(l) };
};

test('"committed" only when every chosen schema is done', async () => {
  const { document } = await setUp(withCommit(HDR(COMMITTED_TEXT), { sales: { state: 'catalogued' } }));
  const w = wordOf(document);
  assert.equal(w.how, 'done');
  assert.match(w.word, /✓ committed/);
  assert.match(w.line, /Database element d0000001 in Egeria/, 'the first clause is inline');
  assert.equal(document.querySelector('[data-db-publish-state-text]').textContent, COMMITTED_TEXT, 'the full sentence is behind the disclosure');
});

test('every schema failed: "partly committed", never "committed"', async () => {
  const { document } = await setUp(withCommit(HDR('Database element d1 in Egeria · published 10-05 · 1 schema chosen: 1 failed'), { sales: { state: 'failed' } }));
  const w = wordOf(document);
  assert.equal(w.how, 'partial');
  assert.match(w.word, /◐ partly committed · 1 failed$/);
  assert.doesNotMatch(w.word, /^✓/);
});

test('queued and failed are counted apart', async () => {
  const v = withCommit(HDR('x · y'), { sales: { state: 'queued' }, archive: { state: 'failed' } });
  v.schemas[1].effective = 'catalogue';
  const { document } = await setUp(v);
  assert.match(wordOf(document).word, /partly committed · 1 failed \/ 1 queued/);
});

test('after an Egeria reset: "not in Egeria now · reset"', async () => {
  const { document } = await setUp(withCommit(HDR('Database element d1 published earlier · Egeria was reset 10-08 · not in Egeria now'), { sales: { state: 'reset' } }));
  const w = wordOf(document);
  assert.equal(w.how, 'reset');
  assert.match(w.word, /not in Egeria now · reset/);
});

test('no proof at all: "not committed"', async () => {
  const { document } = await setUp(viewWith());
  assert.equal(wordOf(document).how, 'none');
  assert.match(wordOf(document).word, /not committed/);
});

test('an unfinished commit found on load is followed from Describe, without opening Contents', async () => {
  const { setCommitPollMs } = await import('/static/next/stages/curate-scope.js');
  setCommitPollMs(40);
  const rec = (done) => ({ id: 'beef0000aaaa', author: 'me', state: done ? 'done' : 'running', requested_at: new Date().toISOString(),
    steps: [{ name: 'a', state: done ? 'done' : 'running' }, { name: 'b', state: done ? 'done' : 'pending' }] });
  const latest = { commit: rec(false), terminal: false, age_hours: 0, stale_unfinished: false, states: {} };
  const { document, server } = await setUp(viewWith(), { latest, record: rec(false) });
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /running/, 'the line is there on load');
  assert.equal(panels(document).contents.hidden, true, 'Contents was never opened');
  server.record = rec(true);
  await wait(400);
  assert.match(norm(document.querySelector('[data-curate-commit-line]')), /published/, 'the line settled from Describe');
  assert.equal(server.calls.filter((c) => c.url.endsWith('/commit-preview')).length, 0, 'and nothing read Egeria');
  setCommitPollMs(2000);
});

test('the manifest never prints 0 for an undecided count it has not read', async () => {
  const { contentsUndecided } = await import('/static/next/stages/curate-counts.js');
  assert.equal(contentsUndecided('db', { counts: {} }).state, 'reading');
  const v = viewWith(); v.counts = { schemas_offered: 3 };
  const { document } = await setUp(v);
  await tab(document, 'curate_contents');
  assert.match(norm(document.querySelector('[data-scope-manifest-row="undecided"]')), /Undecided Egeria stays as it is —/);
  assert.doesNotMatch(norm(document.querySelector('[data-scope-manifest-row="undecided"]')), /it is 0/);
});
