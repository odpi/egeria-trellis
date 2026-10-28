/** Behaviour: the Curate stage must DRAW something for every resource kind.
 *
 *  Regression: E1 (18b3bb41) deleted `<div id="enrichment-form">` from the
 *  Questions pane, but renderCurate still looked it up and did
 *  `if (!host) return;`, so Curate rendered a header and an empty body for
 *  every kind. The older tests pinned source text and stayed green.
 *
 *  These go through the REAL router: click the real Curate nav button
 *  (renderIntentNav -> loadPane -> renderCurate) against the real frame
 *  markup loadPane writes. Nothing here supplies the host element.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const PLAN = {
  technology_type: 'Git repository', disposition: 'using', in_population: true,
  last_surveyed_at: null, what_it_is: [], what_it_holds: [], relates: [], commits: [],
};

function stubServer() {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.includes('/curate/plan')) return ok(PLAN);
    if (u.includes('/components/tree')) return ok({ branches: [], topology: '' });
    if (u.includes('/blueprints')) return ok({ blueprints: [], perspectives: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
}

async function setUp(resourceType, { dropHost = false } = {}) {
  const { document, window } = makeDomEnvironment();
  stubServer();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = resourceType;
  app.state.selectedSlug = 'fixture-resource';
  app.state.stage = 'understanding';
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

async function openCurate(document) {
  const btn = document.querySelector('#intent-nav button[data-stage="curate"]');
  assert.ok(btn, 'nav must offer a Curate button');
  btn.click();
  await new Promise((r) => setTimeout(r, 250));
}

const bodyText = (document) => document.getElementById('content').textContent;

// CURATE-UI-DATABASES-IMPLEMENTED.md: the old "Curate isn't available for ..."
// body is gone; band 2 is the kind's own work (full coverage of the bands is
// in curate-bands.test.mjs). What stays pinned here is that the pane is never blank.
const KIND_TEXT = { db: /Glossary terms on tables and columns/, filesystem: /Nothing to review for file systems yet/ };
for (const kind of ['db', 'filesystem']) {
  test(`Curate on a non-repo (${kind}) draws its band-2 text, not an empty body`, async () => {
    const { document, app } = await setUp(kind);
    await openCurate(document);
    assert.equal(app.state.stage, 'curate');
    const host = document.getElementById('curate-host');
    assert.ok(host, 'Curate must have a host in the document');
    assert.match(host.textContent, KIND_TEXT[kind]);
    assert.match(bodyText(document), KIND_TEXT[kind]);
    assert.doesNotMatch(host.textContent, /Curate isn't available for/);
  });
}

test('Curate on a repo draws the plan view, not an empty body', async () => {
  const { document } = await setUp('repo');
  await openCurate(document);
  const host = document.getElementById('curate-host');
  assert.ok(host, 'Curate must have a host in the document');
  assert.match(host.textContent, /disposition/);
  assert.doesNotMatch(host.textContent, /plan loading/);
});

test('Curate host survives the Questions rows blanking (sibling, not child)', async () => {
  const { document } = await setUp('filesystem');   // a database's Curate is tabbed (Brief S2) and has no Questions frame
  await openCurate(document);
  const rows = document.getElementById('question-rows');
  assert.equal(rows.innerHTML, '');
  assert.ok(!rows.contains(document.getElementById('curate-host')));
  assert.ok(document.getElementById('curate-host').textContent.length > 20);
});

test('KNOWN-NEGATIVE: with the pane frame missing, Curate fails loudly instead of drawing nothing', async () => {
  const { document } = await setUp('db');
  const { renderCurate } = await import('/static/next/stages/curate.js');
  assert.equal(document.getElementById('question-rows'), null);
  await assert.rejects(() => renderCurate('fixture-resource'), /Curate pane host missing/);
});
