/** The journal entry IS the chat turn (owner 2026-10-22). Two ways in, one row out:
 *    - the chat answers "save this to the journal" with the journal tool's question and draws [Add] [Not now];
 *    - the pane's per-turn control shows the entry DERIVED from the turn, read-only, with [Add] [Not now] and one
 *      optional note kept apart from it.
 *  Real app.js, real chat.js. ONLY `fetch` is replaced. Writes are asserted from the requests the page sent
 *  and from the state on the element, not from the button press.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

// The page loads marked with a <script> tag, which jsdom never runs: hand it the same vendored file.
globalThis.marked = createRequire(import.meta.url)('../../resource_explorer/web/static/vendor/marked.min.js');
const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SCOPED = 'docling_core';
const QUESTION_WORDS = 'Add this turn to the investigation journal? [Add] [Not now]';
const DERIVED_UNUSED = 'Per chat\n\nQuestion: Who are the main contributors?\n\nAnswer:\n\nalice 120\n\n- Scope: investigation study - resource docling_core\n- Evidence: contributor_stats';

let ANSWER = '';
let DERIVED = '';
function makeServer({ writeFails = false, already = false } = {}) {
  const s = { calls: [] };
  const enc = new TextEncoder();
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b, text: async () => JSON.stringify(b) });
    if (u === '/api/journal/investigation/study/turn') {
      if (writeFails && body.confirm) return { ok: false, status: 500, json: async () => ({ detail: 'registry down' }), text: async () => 'registry down' };
      if (body.confirm) return ok(already ? { written: false, already: true, id: 'e1' } : { written: true, already: false, id: 'e1' });
      return ok({ written: false, already, body: DERIVED, turn: {} });
    }
    if (u === '/api/query/stream') {
      let frames;
      if (/journal/.test(body.query)) {
        frames = [{ t: 'chunk', v: QUESTION_WORDS },
          { t: 'done', intent: '', hash: '', cached: false, compiled: null, journal_proposal: {
            investigation: 'study', exchange_id: 41, question: 'Who are the main contributors?', answer: 'alice 120', compile_id: 'abcdef1234567890', resource_slug: SCOPED }, exchange_id: 43 }];
      } else {
        frames = [{ t: 'chunk', v: ANSWER },
          { t: 'done', intent: 'lookup', hash: 'h', cached: false, exchange_id: 41, compiled: { text: 't', manifest: { compile_id: 'abcdef1234567890', packed: [{ key: 'contributor_stats' }], gaps: [] } } }];
      }
      let sent = false;
      return { ok: true, status: 200, body: { getReader: () => ({ async read() {
        if (sent) return { done: true };
        sent = true;
        return { done: false, value: enc.encode(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')) };
      } }) } };
    }
    return ok({});
  };
  return s;
}

async function setUp({ investigation = 'study', writeFails = false, already = false } = {}) {
  const { document, window } = makeDomEnvironment();
  window.localStorage.clear();
  const server = makeServer({ writeFails, already });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const chat = await import('/static/next/chat.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: SCOPED, stage: 'scouting', subTab: 'questions', chat: [], promoted: null,
    projects: [{ slug: SCOPED, display_name: 'docling_core' }],
    databases: [], databasesLoaded: false, groups: [], investigations: [], investigation, workListSlug: null,
    workListIndex: false, perspectives: [], allPerspectives: [], workingSet: new Set(), me: { user_id: 'me' },
    activePerspectives: new Set(), whyOpen: new Set() });
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail', 'sidebar', 'rail-scope', 'scope-slug', 'investigation-name', 'whoami', 'activity-count', 'answered-count']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  const re = document.createElement('div'); re.id = 'rail-evidence'; document.getElementById('rail').appendChild(re);
  chat.renderRail();
  return { document, app, server };
}

async function ask(t, q) {
  t.document.getElementById('ask-input').value = q;
  t.document.getElementById('ask-submit').click();
  await wait(350);
}

const HOSTILE = 'Yes, **data-prep-kit** is integrated with **docling**. The `cve_scan` analysis <script>window.__x=1</script> <img src=x onerror="window.__x=1"> [bad](javascript:alert(1)) [ok](https://example.org/a)';
const assertSafe = (root) => {
  assert.equal(root.querySelector('script'), null, 'no script element');
  assert.equal(root.querySelector('img'), null, 'no img element');
  assert.equal(root.querySelector('[onerror]'), null, 'no event attribute');
  assert.ok(!root.querySelector('a[href^="javascript"]'), 'no javascript: link');
  assert.ok(root.textContent.includes('<script>'), 'raw HTML shows as text');
  assert.ok(root.textContent.includes('<img'), 'raw HTML shows as text');
  const ok = root.querySelector('a[href="https://example.org/a"]');
  assert.ok(ok, 'an https link stays a link');
  assert.match(ok.getAttribute('rel'), /noopener/);
};

test('the chat rail renders the answer as markdown and renders hostile HTML as text', async () => {
  ANSWER = HOSTILE; DERIVED = 'x';
  const t = await setUp();
  await ask(t, 'is data-prep-kit integrated with docling?');
  const el = t.document.querySelector('[data-turn-answer]');
  assert.ok(el, 'the rail shows the answer');
  assert.equal(el.querySelector('strong')?.textContent, 'data-prep-kit');
  assert.equal(el.querySelector('code')?.textContent, 'cve_scan');
  assert.ok(!el.textContent.includes('**'), 'no raw asterisks');
  assert.ok(!el.textContent.includes('`'), 'no raw backticks');
  assertSafe(el);
  assert.equal(globalThis.__x, undefined);
});

test('the journal entry preview renders markdown, keeps the stored text exactly, and renders hostile HTML as text', async () => {
  ANSWER = 'alice 120'; DERIVED = `Per chat\n\nQuestion: q\n\nAnswer:\n\n${HOSTILE}`;
  const t = await setUp();
  await ask(t, 'q');
  t.document.querySelector('[data-act="journal-turn"]').click();
  await wait(300);
  const entry = t.document.querySelector('[data-journal-entry]');
  assert.equal(entry.querySelector('strong')?.textContent, 'data-prep-kit');
  assert.equal(entry.querySelector('code')?.textContent, 'cve_scan');
  assert.ok(!entry.textContent.includes('**'));
  assert.equal(entry.dataset.raw, DERIVED, 'the entry text is unchanged');
  assertSafe(entry);
  assert.equal(t.document.querySelector('[data-journal-form] textarea'), null);
});

test('showMarkdown (the saved journal list uses the same renderer) is safe on hostile text', async () => {
  const { showMarkdown } = await import('/static/next/markdown.js');
  const { document } = await setUp();
  const el = document.createElement('div'); document.body.appendChild(el);
  await showMarkdown(el, HOSTILE);
  assert.equal(el.querySelector('strong')?.textContent, 'data-prep-kit');
  assertSafe(el);
});
