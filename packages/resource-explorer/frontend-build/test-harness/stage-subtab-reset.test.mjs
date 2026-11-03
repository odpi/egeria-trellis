/** Behaviour test: a stage switch must not leave a sub-tab that does not exist on
 *  the new stage driving the pane (Enrichment's Context pane under Discovery).
 *  Clicks the REAL nav buttons and the real sub-tab buttons through loadPane().
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

function stubServer() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/analyses/facts')) return ok({ subjects: { adventureworks: [] } });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/investigations')) return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
}

async function setUp(search = '') {
  const { document, window } = makeDomEnvironment();
  stubServer();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.history.replaceState(null, '', '/next' + search);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'scouting';
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, window, app };
}

const wait = () => new Promise((r) => setTimeout(r, 150));
async function clickStage(document, stage) {
  const btn = document.querySelector(`#intent-nav button[data-stage="${stage}"]`);
  assert.ok(btn, `nav must offer a ${stage} button`);
  btn.click();
  await wait();
}
const content = (d) => d.getElementById('content');
const text = (d) => content(d).textContent;
const hasContext = (d) => !!d.getElementById('context-form') || /What we judge/.test(text(d));
const urlTab = (w) => new URLSearchParams(w.location.search).get('tab');
const highlighted = (d) => [...content(d).querySelectorAll('span.border-accent')].map((s) => s.textContent.trim());

function assertConsistent(d, w, app, stage) {
  assert.ok(!hasContext(d), `${stage}: Context pane must be gone; got ${text(d).slice(0, 120)}`);
  assert.notEqual(app.state.subTab, 'context', `${stage}: state.subTab must not be context`);
  const t = urlTab(w);
  assert.ok(t === null || t === app.state.subTab, `${stage}: URL tab=${t} vs state ${app.state.subTab}`);
  assert.notEqual(t, 'context', `${stage}: URL must not carry tab=context`);
  const valid = app.visibleSubTabs().map((x) => x.label);
  assert.ok(!valid.includes('Context'), `${stage}: Context must not be a visible tab`);
  for (const h of highlighted(d)) assert.ok(valid.includes(h), `${stage}: highlighted '${h}' not in ${valid}`);
}

for (const stage of ['scouting', 'discovery', 'assessment', 'analysis', 'curate']) {
  test(`Enrichment/Context -> ${stage}: Context gone, tab bar valid, URL consistent`, async () => {
    const { document, window, app } = await setUp();
    await clickStage(document, 'enrichment');
    assert.ok(hasContext(document), 'Enrichment must show Context');
    await clickStage(document, stage);
    assert.equal(app.state.stage, stage);
    assertConsistent(document, window, app, stage);
    // Curate on a database (Brief S2) opens on its first leading tab; every other stage opens on Questions.
    const landing = stage === 'curate' && ['db', 'repo'].includes(app.state.resourceType) ? 'Describe and publish' : 'Questions';
    assert.ok(highlighted(document).includes(landing), `${stage}: ${landing} should be highlighted; got ${highlighted(document)}`);
  });
}

for (const frame of ['understanding', 'automate', 'investigation']) {
  test(`Enrichment/Context -> ${frame}: Context gone`, async () => {
    const { document, app } = await setUp();
    await clickStage(document, 'enrichment');
    await clickStage(document, frame);
    assert.equal(app.state.stage, frame);
    assert.ok(!hasContext(document));
    assert.ok(text(document).length > 0);
  });
  test(`Enrichment/Context -> ${frame} -> discovery: no stale Context after the frame`, async () => {
    const { document, window, app } = await setUp();
    await clickStage(document, 'enrichment');
    await clickStage(document, frame);
    await clickStage(document, 'discovery');
    assertConsistent(document, window, app, `${frame}->discovery`);
  });
}

test('deep link ?stage=discovery&tab=context lands on a valid pane', async () => {
  const { document, window, app } = await setUp('?type=db&resource=adventureworks&stage=discovery&tab=context');
  // readUrl() (module-private) leaves exactly this state; loadPane() is not
  // exported either, so re-enter it through the real Discovery nav button.
  app.state.stage = 'discovery'; app.state.subTab = 'context';
  await clickStage(document, 'discovery');
  assertConsistent(document, window, app, 'deep link');
});

test('Enrichment -> Context -> Enrichment still shows Context', async () => {
  const { document, app } = await setUp();
  await clickStage(document, 'enrichment');
  await clickStage(document, 'discovery');
  await clickStage(document, 'enrichment');
  assert.equal(app.state.subTab, 'context');
  assert.ok(hasContext(document));
});

for (const sub of ['survey', 'by_analysis', 'disposition', 'schema_inventory']) {
  test(`sub-tab ${sub} chosen on Discovery survives a switch to Assessment`, async () => {
    const { document, app } = await setUp();
    await clickStage(document, 'discovery');
    const b = content(document).querySelector(`button[data-subtab="${sub}"]`);
    assert.ok(b, `discovery must offer ${sub}`);
    b.click(); await wait();
    assert.equal(app.state.subTab, sub);
    await clickStage(document, 'assessment');
    assert.equal(app.state.subTab, sub, `${sub} exists on Assessment, so it must survive`);
  });
}

test('schema_inventory (database-only) reverts when the resource is a repo', async () => {
  const { document, app } = await setUp();
  app.state.subTab = 'schema_inventory';
  app.state.resourceType = 'repo';
  await clickStage(document, 'assessment');
  assert.equal(app.state.subTab, 'questions');
});

test('KNOWN-NEGATIVE: reconcile leaves a valid sub-tab alone and changes nothing', async () => {
  const { app } = await setUp();
  app.state.stage = 'discovery'; app.state.subTab = 'survey';
  assert.equal(app.reconcileSubTabForStage(), false);
  assert.equal(app.state.subTab, 'survey');
  app.state.subTab = 'context';
  assert.equal(app.reconcileSubTabForStage(), true);
  assert.equal(app.state.subTab, 'questions');
});
