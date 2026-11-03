/** A text answer promoted to the pane (owner screenshot, 2026-10-25): its markdown is rendered, as the chat rail
 *  renders it, and the pane is as tall as the words. The 320px floor is a chart's and a diagram's, never a text
 *  answer's; under a short answer it read as a blank block before "Analyses that would answer this".
 *  Real app.js. ONLY `fetch` is replaced.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

// The page loads marked with a <script> tag, which jsdom never runs: hand it the same vendored file.
globalThis.marked = createRequire(import.meta.url)('../../resource_explorer/web/static/vendor/marked.min.js');

const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const ANSWER = 'Yes. The `cve_scan` analysis lists **`python:docling-core`** as a dependency.';

async function setUp() {
  const { document, window } = makeDomEnvironment();
  window.localStorage.clear();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '{}' });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'data_prep_kit', stage: 'analysis', subTab: 'questions',
    chat: [], promoted: null, projects: [{ slug: 'data_prep_kit', display_name: 'data_prep_kit' }], databases: [],
    databasesLoaded: false, groups: [], investigations: [], investigation: null, workListSlug: null, workListIndex: false,
    perspectives: [], allPerspectives: [], workingSet: new Set(), me: { user_id: 'me' }, activePerspectives: new Set(),
    whyOpen: new Set() });
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail', 'sidebar']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  return { document, app };
}

test('a promoted text answer renders its markdown and drops the chart-sized floor', async () => {
  const { document, app } = await setUp();
  await app.promoteToPane({ question: 'Is Data-Prep-Kit integrated with Docling?', answer: ANSWER, source: 'From 13 stored results' });
  await wait(300);
  const body = document.getElementById('promoted-body');
  assert.ok(body, 'the promoted body is drawn');
  assert.ok(!body.classList.contains('min-h-[320px]'), 'a text answer is as tall as its words');
  assert.ok(body.querySelector('strong'), 'bold markdown is rendered, not shown as **');
  assert.ok(body.querySelector('code'), 'inline code is rendered');
  assert.doesNotMatch(body.textContent, /\*\*/, 'no raw markdown markers remain');
  assert.match(body.textContent, /python:docling-core/, 'the words themselves are kept');
  assert.equal(body.firstElementChild.dataset.raw, ANSWER, 'the stored text is kept on the element, unchanged');
});
