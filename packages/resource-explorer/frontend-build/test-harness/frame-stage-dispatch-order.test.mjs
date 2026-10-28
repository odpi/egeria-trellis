/** Routing-level regression: frame-class stages (Investigation, Understanding,
 *  Automate) must render when state.subTab is left over from another stage.
 *
 *  A top-nav stage click deliberately does NOT touch state.subTab. loadPane()
 *  used to test the subTab branches (context, survey, disposition, ...) BEFORE
 *  the frame-stage checks, so Enrichment (default subTab 'context') -> click
 *  Investigation rendered Context again. These tests click the REAL nav
 *  buttons (renderIntentNav -> data-stage handler -> loadPane) and assert the
 *  frame's own DOM landed.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

function stubServer() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/analyses/facts')) return ok({ subjects: { amundsen: [] } });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/investigations')) return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
}

async function setUp() {
  const { document, window } = makeDomEnvironment();
  stubServer();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'amundsen';
  app.state.stage = 'enrichment';
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
  return { document, app };
}

async function clickStage(document, stage) {
  const btn = document.querySelector(`#intent-nav button[data-stage="${stage}"]`);
  assert.ok(btn, `nav must offer a ${stage} button`);
  btn.click();
  await new Promise((r) => setTimeout(r, 150));
}

const text = (document) => document.getElementById('content').textContent;
const isContext = (document) => !!document.getElementById('context-form');

const FRAMES = {
  investigation: (d) => /Investigation/.test(text(d)) && !isContext(d),
  // A repository's Understanding now draws its leading tabs (stage-tabs.js) instead of the one-pane header.
  understanding: (d) => /Understanding has one pane/.test(text(d)) || !!d.getElementById('content').querySelector('[data-understanding-tab-panel]'),
  automate: (d) => !!d.getElementById('content').querySelector('[data-automate-tab]'),
};

test('Enrichment lands on Context by default (sanity: the stale subTab the bug needs)', async () => {
  const { document, app } = await setUp();
  await clickStage(document, 'enrichment');
  assert.equal(app.state.subTab, 'context');
  assert.ok(isContext(document), 'Enrichment must render the Context pane');
});

for (const frame of ['investigation', 'understanding', 'automate']) {
  test(`Enrichment (Context) -> ${frame}: the frame renders, Context does not linger`, async () => {
    const { document, app } = await setUp();
    await clickStage(document, 'enrichment');
    assert.ok(isContext(document));
    await clickStage(document, frame);
    assert.equal(app.state.stage, frame);
    // Understanding on a repository has leading tabs (stage-tabs.js): entering it always opens tab 1, never the
    // sub-tab carried over. The routing order (the frame before any sub-tab branch) is what this test pins.
    assert.equal(app.state.subTab, frame === 'understanding' ? 'understanding_glance' : 'context',
      frame === 'understanding' ? 'a stage with leading tabs opens on its first' : 'subTab is deliberately left untouched');
    assert.ok(!isContext(document), `Context must not render under ${frame}`);
    assert.ok(FRAMES[frame](document), `${frame} frame must render; got: ${text(document).slice(0, 160)}`);
  });
}

for (const sub of ['schema_inventory', 'survey', 'by_analysis', 'disposition']) {
  test(`stale subTab '${sub}' -> Investigation still renders the frame`, async () => {
    const { document, app } = await setUp();
    app.state.stage = 'discovery';
    app.state.subTab = sub;
    await clickStage(document, 'investigation');
    assert.equal(app.state.subTab, sub);
    assert.ok(FRAMES.investigation(document), `got: ${text(document).slice(0, 160)}`);
  });
}

test('REVERSE: Investigation -> Enrichment lands on Context, not stale frame content', async () => {
  const { document, app } = await setUp();
  await clickStage(document, 'investigation');
  assert.ok(FRAMES.investigation(document));
  assert.equal(app.state.subTab, 'questions');
  await clickStage(document, 'enrichment');
  assert.equal(app.state.subTab, 'context');
  assert.ok(isContext(document), 'Enrichment must render Context');
  assert.doesNotMatch(text(document), /No investigations yet/);
});
