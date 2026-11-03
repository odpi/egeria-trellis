/** Brief S0: the stage-specific leading tabs, the plumbing. DORMANT on main (STAGE_TABS_KINDS is empty), so the
 *  first block pins that the rail is byte-identical to before for every stage and kind with the gate off; the rest
 *  turn the gate on and drive the real nav buttons, the real tab buttons, openCurate and jumpToCurateSection. */
import assert from 'node:assert/strict';
import { test, afterEach } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

function stubServer() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/analyses/facts')) return ok({ subjects: { adventureworks: [] } });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/curate/plan')) return ok({ technology_type: 'Git repository', disposition: 'using', in_population: true, last_surveyed_at: null,
      what_it_is: [], what_it_holds: [], made_of: [], relates: [], writes: {}, commits: [], survey: { exists: false }, project: { status: 'linked', word: 'project', name: 'P' } });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/investigations')) return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
}

async function setUp(kind = 'db') {
  const { document, window } = makeDomEnvironment();
  stubServer();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const tabs = await import('/static/next/stages/stage-tabs.js');
  const ct = await import('/static/next/stages/curate-tabs.js');
  const counts = await import('/static/next/stages/curate-counts.js');
  tabs.setStageTabsKinds('curate', []);
  tabs.setStageTabsKinds('understanding', []);
  app.state.resourceType = kind;
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'scouting';
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  app.state.curate = {};
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, window, app, tabs, ct, counts };
}

const wait = () => new Promise((r) => setTimeout(r, 150));
async function clickStage(document, stage) {
  const btn = document.querySelector(`#intent-nav button[data-stage="${stage}"]`);
  assert.ok(btn, `nav must offer a ${stage} button`);
  btn.click();
  await wait();
}
const urlTab = (w) => new URLSearchParams(w.location.search).get('tab');
const ids = (app) => app.visibleSubTabs().map((t) => t.id);

afterEach(async () => {
  const tabs = await import('/static/next/stages/stage-tabs.js');
  tabs.setStageTabsKinds('curate', []);
  tabs.setStageTabsKinds('understanding', []);
});

/* ── gate OFF: byte-identical to main ────────────────────────────────── */

// What main shows: Context on Enrichment only, Schema Inventory and Table preview on a database only.
const MAIN_IDS = (stage, kind) => [
  ...(stage === 'enrichment' ? ['context'] : []),
  'questions', 'survey', 'by_analysis', 'disposition',
  ...(kind === 'db' ? ['schema_inventory', 'value_profile', 'table_preview'] : []),
];
const MAIN_LABEL = { context: 'Context', questions: 'Questions', survey: 'Survey & analyses', by_analysis: 'By analysis', disposition: 'Disposition', schema_inventory: 'Schema Inventory', value_profile: 'Value profile', table_preview: 'Table preview' };

for (const kind of ['repo', 'db', 'filesystem']) {
  for (const stage of ['scouting', 'discovery', 'assessment', 'analysis', 'curate', 'enrichment', 'understanding']) {
    test(`gate off: ${stage}/${kind} rail is exactly main's (same tabs, same markup, no extras)`, async () => {
      const { app } = await setUp(kind);
      app.state.stage = stage;
      app.state.subTab = stage === 'enrichment' ? 'context' : 'questions';
      assert.deepEqual(ids(app), MAIN_IDS(stage, kind));
      assert.equal(app.stageDefaultSubTab(), stage === 'enrichment' ? 'context' : 'questions');
      const html = app.subTabsHtml();
      assert.ok(!/data-subtab-rail|data-curate-commit-line|data-tab-counts|curate_|understanding_/.test(html), html);
      assert.ok(html.startsWith('<div class="mb-s4 flex flex-wrap items-baseline gap-s3 font-heading text-subtab">'), 'rail wrapper unchanged');
      assert.ok(html.endsWith('</div>'), 'nothing after the rail');
      const labels = [...html.matchAll(/>([^<>]+)<\/(?:span|button)>/g)].map((m) => m[1]);
      assert.deepEqual(labels, MAIN_IDS(stage, kind).map((id) => MAIN_LABEL[id]));
    });
  }
}

test('gate off (a database): Curate opens on Questions, the URL carries no tab=, openCurate still lands on the Questions frame', async () => {
  const { document, window, app, ct } = await setUp('db');
  await clickStage(document, 'curate');
  assert.equal(app.state.stage, 'curate');
  assert.equal(app.state.subTab, 'questions');
  assert.equal(urlTab(window), null);
  await app.openCurate({ tab: 'curate_contents', section: 'curate-sec-what-holds' });
  assert.equal(app.state.subTab, 'questions', 'a tab is ignored while the gate is off');
  assert.equal(urlTab(window), null);
  // the commit line is a no-op while the gate is off
  assert.equal(ct.setCommitLine({ kind: 'repo', id: 'abcd1234', state: 'running', step: 1, steps: 3 }), false);
  assert.equal(document.querySelector('[data-curate-commit-line]'), null);
});

/* ── gate ON ─────────────────────────────────────────────────────────── */

test('gate on (curate/repo): the three tabs lead the Curate rail and only there; other kinds and stages unchanged', async () => {
  const { app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  app.state.stage = 'curate';
  assert.deepEqual(ids(app), ['curate_describe', 'curate_contents', 'curate_structure', 'questions', 'survey', 'by_analysis', 'disposition']);
  for (const stage of ['scouting', 'discovery', 'assessment', 'analysis', 'understanding']) {
    app.state.stage = stage;
    assert.deepEqual(ids(app), MAIN_IDS(stage, 'repo'), stage);
  }
  app.state.stage = 'enrichment';
  assert.deepEqual(ids(app), MAIN_IDS('enrichment', 'repo'));
  app.state.resourceType = 'db';
  app.state.stage = 'curate';
  assert.deepEqual(ids(app), MAIN_IDS('curate', 'db'), 'db is not in the set');
  app.state.resourceType = 'repo';
  const html = (app.state.subTab = 'curate_describe', app.subTabsHtml());
  assert.ok(html.includes('data-subtab-rail'));
  const labels = [...html.matchAll(/>([^<>]+)<\/(?:span|button)>/g)].map((m) => m[1]);
  assert.deepEqual(labels.slice(0, 1), ['Describe and publish']);
  assert.ok(html.includes('>Contents'), 'Contents tab present');
  assert.ok(html.includes('>Structure'), 'Structure tab present');
});

test('gate on (understanding/db): At a glance / Contents / Over time lead Understanding only', async () => {
  const { app, tabs } = await setUp('db');
  tabs.setStageTabsKinds('understanding', ['db']);
  app.state.stage = 'understanding';
  assert.deepEqual(ids(app).slice(0, 3), ['understanding_glance', 'understanding_contents', 'understanding_over_time']);
  app.state.stage = 'curate';
  assert.ok(!ids(app).some((i) => i.startsWith('understanding_')));
  assert.equal(app.stageDefaultSubTab('understanding'), 'understanding_glance');
  assert.equal(app.stageDefaultSubTab('curate'), 'questions');
  assert.equal(app.stageDefaultSubTab('enrichment'), 'context');
});

test('stageDefaultSubTab: enrichment=context, gated stage=its first tab, else questions', async () => {
  const { app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  assert.equal(app.stageDefaultSubTab('curate', 'repo'), 'curate_describe');
  assert.equal(app.stageDefaultSubTab('curate', 'db'), 'questions');
  assert.equal(app.stageDefaultSubTab('discovery', 'repo'), 'questions');
  assert.equal(app.stageDefaultSubTab('enrichment', 'repo'), 'context');
});

test('gate on: Curate nav opens on tab 1 with tab= in the URL; a tab click writes tab= and swaps the panel in place', async () => {
  const { document, window, app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  await clickStage(document, 'curate');
  assert.equal(app.state.subTab, 'curate_describe');
  assert.equal(urlTab(window), 'curate_describe', 'always written on these stages');
  const shown = () => [...document.querySelectorAll('[data-curate-tab-panel]')].filter((p) => !p.hidden).map((p) => p.dataset.curateTabPanel);
  assert.deepEqual(shown(), ['describe'], 'three panels in the document, one showing');
  assert.equal(document.querySelectorAll('[data-curate-tab-panel]').length, 3);
  document.querySelector('[data-subtab="curate_contents"]').click();
  await wait();
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(urlTab(window), 'curate_contents');
  assert.deepEqual(shown(), ['contents']);
});

test('gate on: leaving Curate reverts the tab and drops tab=; coming back always opens on tab 1', async () => {
  const { document, window, app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  await clickStage(document, 'curate');
  document.querySelector('[data-subtab="curate_structure"]').click();
  await wait();
  assert.equal(app.state.subTab, 'curate_structure');
  await clickStage(document, 'discovery');
  assert.equal(app.state.subTab, 'questions');
  assert.equal(urlTab(window), null);
  await clickStage(document, 'curate');
  assert.equal(app.state.subTab, 'curate_describe', 'no landing preference: tab 1');
  // a tab carried from a non-tab sub-tab of another stage does not stick either
  app.state.stage = 'discovery'; app.state.subTab = 'survey';
  await clickStage(document, 'curate');
  assert.equal(app.state.subTab, 'curate_describe');
});

test('reconcile: a curate_* tab off Curate reverts (like context off Enrichment); with the gate off it reverts on Curate too', async () => {
  const { app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  app.state.stage = 'discovery'; app.state.subTab = 'curate_contents';
  assert.equal(app.reconcileSubTabForStage(), true);
  assert.equal(app.state.subTab, 'questions');
  app.state.stage = 'curate'; app.state.subTab = 'curate_contents';
  assert.equal(app.reconcileSubTabForStage(), false, 'valid on Curate with the gate on');
  tabs.setStageTabsKinds('curate', []);
  assert.equal(app.reconcileSubTabForStage(), true);
  assert.equal(app.state.subTab, 'questions');
  app.state.stage = 'curate'; app.state.subTab = 'understanding_glance';
  tabs.setStageTabsKinds('curate', ['repo']);
  assert.equal(app.reconcileSubTabForStage(), true);
  assert.equal(app.state.subTab, 'curate_describe', 'a wrong-stage tab falls to the stage default');
});

test('openCurate: tab honoured, unknown tab opens tab 1, section is queued for the jump', async () => {
  const { window, app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  await app.openCurate({ tab: 'curate_structure' });
  assert.equal(app.state.stage, 'curate');
  assert.equal(app.state.subTab, 'curate_structure');
  assert.equal(urlTab(window), 'curate_structure');
  await app.openCurate({ tab: 'nonsense' });
  assert.equal(app.state.subTab, 'curate_describe');
  await app.openCurate({ tab: 'curate_contents', section: 'curate-sec-what-holds' });
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(app.state.curate.pendingJump, 'curate-sec-what-holds', 'the stub has no section: the jump waits for the draw');
  await app.openCurateStage();
  assert.equal(app.state.subTab, 'curate_describe', 'openCurateStage = Describe and publish');
});

test('jumpToCurateSection switches to the section\'s tab first', async () => {
  const { app, tabs } = await setUp('repo');
  const { jumpToCurateSection } = await import('/static/next/stages/curate.js');
  tabs.setStageTabsKinds('curate', ['repo']);
  await app.openCurate({ tab: 'curate_describe' });
  assert.equal(jumpToCurateSection('curate-sec-made-of'), true);
  await wait();
  assert.equal(app.state.subTab, 'curate_structure');
  jumpToCurateSection('curate-sec-what-holds');
  await wait();
  assert.equal(app.state.subTab, 'curate_contents');
  assert.equal(tabs.tabOfSection('curate-sec-writes'), 'curate_contents');
  assert.equal(tabs.tabOfSection('curate-sec-relates'), 'curate_structure');
});

test('jumpToCurateSection with the gate off does not switch anything', async () => {
  const { app } = await setUp('repo');
  const { jumpToCurateSection } = await import('/static/next/stages/curate.js');
  app.state.stage = 'curate'; app.state.subTab = 'questions';
  assert.equal(jumpToCurateSection('curate-sec-made-of'), false, 'no element, queued as before');
  assert.equal(app.state.subTab, 'questions');
  assert.equal(app.state.curate.pendingJump, 'curate-sec-made-of');
});

test('the work-list grid does not get the leading tabs', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../../resource_explorer/web/static/next/app.js', import.meta.url), 'utf8');
  assert.match(src, /subTabs: SUB_TABS\.filter\(\(t\) => !t\.stageTab\)/);
});

/* ── the count cue ───────────────────────────────────────────────────── */

test('a tab label whose count is still loading shows a cue and "reading", never 0', async () => {
  const { app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  app.state.stage = 'curate'; app.state.subTab = 'curate_describe';
  const html = app.subTabsHtml();
  const counts = html.match(/<span data-tab-counts[\s\S]*?<\/span><\/span>/g) || [];
  assert.equal(counts.length, 2, 'Contents and Structure carry counts; Describe does not');
  for (const c of counts) {
    assert.match(c, /data-count-state="reading"/);
    assert.match(c, /reading/);
    assert.ok(!/\b0\b/.test(c.replace(/<[^>]+>/g, '')), `no 0 while unread: ${c}`);
  }
});

test('a loaded count is shown on the label; db Structure says "none yet", not 0 awaiting', async () => {
  const { app, tabs } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo', 'db']);
  app.state.stage = 'curate'; app.state.subTab = 'curate_describe';
  app.state.curate.counts = {
    view: { manifest: { files: 2, folders: 1 }, rows: [{ candidate: true }, { candidate: true, choice: 'include' }, { candidate: true }] },
    plan: { awaiting: 4 },
  };
  const text = app.subTabsHtml().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  assert.match(text, /3 chosen/); assert.match(text, /2 undecided/);
  assert.match(text, /4 awaiting you/);
  app.state.resourceType = 'db';
  app.state.curate.counts = { view: { counts: { schemas_catalogue: 3, schemas_undecided: 26 } }, plan: null };
  const dbText = app.subTabsHtml().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  assert.match(dbText, /3 chosen/); assert.match(dbText, /26 undecided/);
  assert.match(dbText, /Structure\s+\S*\s*none yet/);
  assert.ok(!/0 awaiting/.test(dbText));
});

/* ── the running-commit line ─────────────────────────────────────────── */

const REC = (steps, extra = {}) => ({ id: 'a3f2c9d0e1', steps: steps.map((state, i) => ({ name: `s${i}`, state })), requested_at: '2026-10-11T08:00:00Z', ...extra });

test('commit line: drawn by the rail on a non-Contents tab, from the record; updates in place; link goes to Contents', async () => {
  const { document, app, tabs, ct } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  await clickStage(document, 'curate');
  document.querySelector('[data-subtab="curate_structure"]').click();
  await wait();
  assert.equal(document.querySelector('[data-curate-commit-line]'), null, 'no record, no line');
  const line = ct.commitLineFromRecord('repo', REC(['done', 'done', 'running', 'queued']));
  assert.equal(line.state, 'running'); assert.equal(line.step, 3); assert.equal(line.steps, 4);
  assert.equal(ct.setCommitLine(line), true);
  const el = document.querySelector('[data-curate-commit-line]');
  assert.ok(el, 'inserted under the rail');
  assert.equal(el.previousElementSibling.hasAttribute('data-subtab-rail'), true);
  assert.match(el.textContent, /Publish · commit a3f2 · step 3 of 4 · running/);
  ct.setCommitLine(ct.commitLineFromRecord('repo', REC(['done', 'done', 'done', 'done'], { finished_at: '2026-10-11T08:05:00Z' })));
  assert.equal(document.querySelectorAll('[data-curate-commit-line]').length, 1, 'replaced, not stacked');
  assert.match(document.querySelector('[data-curate-commit-line]').textContent, /published · read back 2026-10-11/);
  // a redraw of the rail (a tab click) keeps it, from state
  document.querySelector('[data-subtab="curate_describe"]').click();
  await wait();
  assert.match(document.querySelector('[data-curate-commit-line]').textContent, /published/);
  document.querySelector('[data-curate-commit-goto]').click();
  await wait();
  assert.equal(app.state.subTab, 'curate_contents');
});

test('commit line: a failed step says failed with its reason, from the record not the click', async () => {
  const { document, tabs, ct, app } = await setUp('repo');
  tabs.setStageTabsKinds('curate', ['repo']);
  await clickStage(document, 'curate');
  const rec = REC(['done', 'failed', 'queued']);
  rec.steps[1].reason = 'not permitted';
  ct.setCommitLine(ct.commitLineFromRecord('repo', rec));
  const el = document.querySelector('[data-curate-commit-line]');
  assert.equal(el.dataset.commitLineState, 'failed');
  assert.match(el.textContent, /failed · not permitted/);
  assert.equal(ct.commitLineFromRecord('repo', { id: 'x' }), null, 'not a commit record: no line');
  assert.equal(app.state.curate.commitLine.state, 'failed');
});

test('commit line: the database poller feeds it too (a catalogue commit record with state done)', async () => {
  const { ct } = await setUp('db');
  const l = ct.commitLineFromRecord('db', { id: 'ffee0011', state: 'done', steps: [{ name: 'a', state: 'done' }], finished_at: '2026-10-11T09:00:00Z' });
  assert.equal(l.state, 'published');
  assert.equal(l.readBack, '2026-10-11T09:00:00Z');
});

test('commit line: a catalogue commit that needs read-back says so, never failed and never press again (ruling 2026-10-25)', async () => {
  const { ct } = await setUp('db');
  const l = ct.commitLineFromRecord('db', { id: 'ffee0012', state: 'needs read-back', finished_at: '2026-10-25T17:49:40Z',
    steps: [{ name: 'publish_elements', state: 'done' }, { name: 'leave_outs', state: 'needs read-back' }] });
  assert.equal(l.state, 'needs read-back');
  const host = document.createElement('div');
  host.innerHTML = ct.commitLineHtml(l);
  const el = host.querySelector('[data-curate-commit-line]');
  assert.equal(el.dataset.commitLineState, 'needs read-back');
  assert.match(el.textContent, /Publish · commit ffee · needs read-back · do not press again · Refresh re-reads/);
  assert.doesNotMatch(el.textContent, /failed|press Publish again/);
});
