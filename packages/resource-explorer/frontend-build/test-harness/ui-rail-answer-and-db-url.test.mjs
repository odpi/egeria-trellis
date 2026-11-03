/** Two UI fixes (2026-10-21 rehearsal): a finished chat turn shows its answer in the rail, and a URL naming a
 *  resource without `type=` resolves the slug's kind from the loaded lists. Real app.js and chat.js; only
 *  `fetch` is replaced. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 50) => new Promise((r) => setTimeout(r, ms));

function stubFetch({ repos = [], dbs = [], fss = [], failDbs = false } = {}) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b, text: async () => JSON.stringify(b) });
    if (u === '/api/databases/') { if (failDbs) throw new Error('down'); return ok(dbs.map((slug) => ({ slug }))); }
    if (u === '/api/filesystems/') return ok(fss.map((slug) => ({ slug })));
    return ok({});
  };
}

async function load(search, lists) {
  delete globalThis.localStorage;
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(globalThis, 'localStorage', { value: window.localStorage, configurable: true, writable: true });
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', `/next${search}`);
  stubFetch(lists);
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: null, stage: 'scouting', subTab: 'questions', workListSlug: null,
    activePerspectives: new Set(), databases: [], databasesLoaded: false, filesystems: [], filesystemsLoaded: false,
    projects: (lists.repos || []).map((slug) => ({ slug })), unknownResource: '', otherKinds: [], chat: [], promoted: null });
  app.readUrl();
  return { app, window, document };
}
const qs = (window) => new URLSearchParams(window.location.search);

test('missing type resolves to db, and the URL is rewritten with type=db', async () => {
  const { app, window } = await load('?resource=laz_local_adventureworks', { dbs: ['laz_local_adventureworks'], repos: ['egeria_git'] });
  assert.equal(app.state.resourceType, 'repo', 'before resolving it reads as a repo');
  await app.resolveUrlKind();
  assert.equal(app.state.resourceType, 'db');
  assert.equal(qs(window).get('type'), 'db');
  assert.equal(qs(window).get('resource'), 'laz_local_adventureworks');
  assert.equal(app.state.unknownResource, '');
});

test('missing type resolves to repo and writes no type', async () => {
  const { app, window } = await load('?resource=egeria_git', { dbs: ['other_db'], repos: ['egeria_git'] });
  await app.resolveUrlKind();
  assert.equal(app.state.resourceType, 'repo');
  assert.equal(qs(window).get('type'), null);
  assert.deepEqual(app.state.otherKinds, []);
});

test('a slug found nowhere is "no resource called <slug>", not a repo', async () => {
  const { app, document } = await load('?resource=ghost', { dbs: ['a'], repos: ['b'] });
  await app.resolveUrlKind();
  assert.equal(app.state.unknownResource, 'ghost');
  assert.match(app.unknownResourcePaneHtml(), /no resource called ghost/);
  assert.ok(!/repo|checklist/i.test(app.unknownResourcePaneHtml().replace(/<[^>]*>/g, '')));
});

test('a slug in two kinds opens the repo and names the other kind', async () => {
  const { app, document } = await load('?resource=shared', { dbs: ['shared'], repos: ['shared'] });
  const r = await app.resolveUrlKind();
  assert.equal(app.state.resourceType, 'repo');
  assert.deepEqual(app.state.otherKinds, ['db']);
  assert.deepEqual(r.kinds, ['repo', 'db']);
  for (const id of ['content', 'sidebar']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  app.renderKindChooser();
  const chooser = document.querySelector('[data-kind-chooser]');
  assert.ok(chooser);
  assert.match(chooser.textContent, /shared is also a database/);
  assert.equal(chooser.querySelector('[data-open-kind="db"]')?.textContent, 'database');
});

test('a failed list is not evidence of absence', async () => {
  const { app } = await load('?resource=maybe_db', { failDbs: true, repos: ['b'] });
  await app.resolveUrlKind();
  assert.equal(app.state.unknownResource, '');
});

test('an explicit type is left alone', async () => {
  const { app } = await load('?type=db&resource=x', { dbs: [] });
  assert.equal(await app.resolveUrlKind(), null);
  assert.equal(app.state.resourceType, 'db');
});

// ── the rail turn ─────────────────────────────────────────────────────────────
async function railWith(turns) {
  const t = await load('', {});
  const chat = await import('/static/next/chat.js');
  const { document } = t;
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail', 'sidebar', 'rail-scope', 'scope-slug', 'investigation-name', 'whoami', 'activity-count', 'answered-count']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  const re = document.createElement('div'); re.id = 'rail-evidence'; document.getElementById('rail').appendChild(re);
  t.app.state.chat = turns; t.app.state.promoted = null; t.app.state.selectedSlug = null;
  chat.renderRail();
  return { ...t, chat };
}
const rail = (d) => d.getElementById('chat-log');

test('a finished turn shows its answer text in the rail', async () => {
  const { document } = await railWith([{ question: 'q?', answer: 'The answer is 42.', source: 'compiled', pending: false }]);
  const a = rail(document).querySelector('[data-turn-answer]');
  assert.ok(a);
  assert.equal(a.textContent.trim(), 'The answer is 42.');
  assert.equal(rail(document).querySelector('[data-turn-more]'), null);
  assert.ok(!rail(document).querySelector('[data-turn-clamped]'));
});

test('a long answer is clamped with "more ›"', async () => {
  const { document } = await railWith([{ question: 'q?', answer: 'word '.repeat(200), source: 's', pending: false }]);
  assert.ok(rail(document).querySelector('[data-turn-clamped]'));
  assert.match(rail(document).querySelector('[data-turn-more]').textContent, /more ›/);
  assert.match(rail(document).querySelector('[data-turn-answer]').getAttribute('style'), /line-clamp:4/);
  assert.match(rail(document).querySelector('[data-turn-answer]').className, /break-words/);
});

test('a pending turn shows the cue and no answer block', async () => {
  const { document } = await railWith([{ question: 'q?', answer: 'partial', pending: true, streaming: true }]);
  const p = rail(document).querySelector('[data-turn-pending]');
  assert.ok(p);
  assert.ok(p.querySelector('[data-cue="running"]'), 'a visual cue, not only a sentence');
  assert.equal(rail(document).querySelector('[data-turn-answer]'), null);
});

test('answer text is escaped, never rendered as HTML', async () => {
  const { document } = await railWith([{ question: 'q?', answer: '<img src=x onerror=alert(1)><b>hi</b>', source: 's', pending: false }]);
  const a = rail(document).querySelector('[data-turn-answer]');
  assert.equal(a.querySelector('img'), null);
  assert.equal(a.querySelector('b'), null);
  assert.match(a.textContent, /<img src=x/);
});
