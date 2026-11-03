/** The investigation page's Journal section: entries newest first, an honest empty state, and a failed read
 *  (an error status, or a reply with no entries list) that is said as a failure and never drawn as an empty journal. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML = fs.readFileSync(path.resolve(HERE, '../../resource_explorer/web/static/next/index.html'), 'utf8');
// The page loads marked with a <script> tag, which jsdom never runs: hand it the same vendored file.
globalThis.marked = createRequire(import.meta.url)('../../resource_explorer/web/static/vendor/marked.min.js');
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp({ journal, promoted = false, state, publish }) {
  const calls = [];
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u === '/api/journal/investigation/c360') {
      if (journal === 'error') return { ok: false, status: 500, json: async () => ({ detail: 'registry down' }), text: async () => 'registry down' };
      return ok(journal);
    }
    if (u === '/api/curate/feedback-state/investigation/c360') {
      if (state === 'error') return { ok: false, status: 500, json: async () => ({ detail: 'down' }), text: async () => 'down' };
      return ok(state || { notes: [], to_send: 0 });
    }
    if (u === '/api/investigations/c360/journal/publish') {
      calls.push(u);
      if (publish && publish.status) return { ok: false, status: publish.status, json: async () => ({ detail: publish.detail }), text: async () => publish.detail };
      return ok(publish || { sent: 1 });
    }
    if (/\/members$/.test(u)) return ok([]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/link-status$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    if (/^\/api\/investigations\/([^/?]+)$/.test(u)) {
      return ok({ slug: 'c360', display_name: 'C', status: 'open', description: 'd', project_classification: 'StudyProject',
        purposes: [], visibility: 'public', egeria_project_guid: promoted ? 'proj-guid' : null });
    }
    if (/^\/api\/investigations\/\?/.test(u)) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const api = await import('/static/re-api.js'); api.clearCache();
  const app = await import('/static/next/app.js');
  const inv = await import('/static/next/stages/investigation.js');
  app.state.stage = 'investigation'; app.state.me = { user_id: 'me' }; app.state.investigations = [];
  inv.openInvestigationDetail('c360');
  await inv.renderInvestigation();
  await tick();
  return { document, calls };
}

test('the journal lists its entries with author and date, and says they wait for promotion', async () => {
  const t = await setUp({ journal: { entries: [
    { id: 'e2', author: 'alice', written_at: '2026-10-19T10:00:00+00:00', body: 'Per chat: marquez emits OpenLineage.', suggested_to: [] },
    { id: 'e1', author: 'bob', written_at: '2026-10-18T09:00:00+00:00', body: 'First note.', suggested_to: [] },
  ], suggested_to: [] } });
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /Journal · 2/);
  assert.match(section, /alice · 2026-10-19/);
  assert.match(section, /Per chat: marquez emits OpenLineage\./);
  assert.match(section, /bob · 2026-10-18/);
  assert.match(section, /once this investigation is promoted/);
});

test('an empty journal says so and points at the chat action', async () => {
  const t = await setUp({ journal: { entries: [], suggested_to: [] } });
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /Journal · 0/);
  assert.match(section, /No entries yet/);
  assert.doesNotMatch(section, /could not be read/);
});

test('a failed read is said as a failure, never drawn as an empty journal', async () => {
  const t = await setUp({ journal: 'error' });
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /The journal could not be read/);
  assert.doesNotMatch(section, /No entries yet|Journal · 0/);
});

test('a reply with no entries list is a failed read too', async () => {
  const t = await setUp({ journal: {} });
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /The journal could not be read/);
  assert.doesNotMatch(section, /No entries yet|Journal · 0/);
});

const ENTRIES = { entries: [
  { id: 'e2', author: 'me', written_at: '2026-10-19T10:00:00+00:00', body: 'Mine.', suggested_to: [] },
  { id: 'e1', author: 'bob', written_at: '2026-10-18T09:00:00+00:00', body: 'Bob.', suggested_to: [] },
], suggested_to: [] };

test('an unpromoted investigation has no Publish button', async () => {
  const t = await setUp({ journal: ENTRIES });
  assert.equal(t.document.querySelector('[data-act="inv-journal-publish"]'), null);
});

test('a promoted investigation shows the Publish button with the count to send and each entry\'s state', async () => {
  const t = await setUp({ journal: ENTRIES, promoted: true,
    state: { to_send: 1, notes: [{ id: 'e2', word: 'not yet sent' }, { id: 'e1', word: 'not yours to send' }] } });
  const btn = t.document.querySelector('[data-act="inv-journal-publish"]');
  assert.ok(btn);
  assert.equal(btn.disabled, false);
  assert.match(text(btn), /Publish my entries · 1/);
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /me · 2026-10-19 · not yet sent/);
  assert.match(section, /bob · 2026-10-18 · not yours to send/);
});

test('the Publish button is disabled when nothing is left to send', async () => {
  const t = await setUp({ journal: ENTRIES, promoted: true, state: { to_send: 0, notes: [] } });
  assert.equal(t.document.querySelector('[data-act="inv-journal-publish"]').disabled, true);
});

test('a failed state read leaves the words off and never claims published', async () => {
  const t = await setUp({ journal: ENTRIES, promoted: true, state: 'error' });
  const section = text(t.document.getElementById('inv-journal'));
  assert.match(section, /me · 2026-10-19(?! ·)/);
  assert.doesNotMatch(section, /published/i);
});

test('pressing Publish posts once and reports entries that were not sent', async () => {
  const t = await setUp({ journal: ENTRIES, promoted: true, state: { to_send: 1, notes: [] },
    publish: { sent: 0, not_permitted: ['e2'], unread: [] } });
  t.document.querySelector('[data-act="inv-journal-publish"]').click();
  await tick(150);
  assert.equal(t.calls.length, 1);
  assert.match(text(t.document.getElementById('inv-journal')), /1 not sent/);
});

test('a refused publish says why and the button stays usable', async () => {
  const t = await setUp({ journal: ENTRIES, promoted: true, state: { to_send: 1, notes: [] },
    publish: { status: 409, detail: 'Promote the investigation first.' } });
  t.document.querySelector('[data-act="inv-journal-publish"]').click();
  await tick(150);
  assert.match(text(t.document.getElementById('inv-journal')), /Not published/);
  assert.equal(t.document.querySelector('[data-act="inv-journal-publish"]').disabled, false);
});

test('an entry that carries a compile shows it in its header', async () => {
  const t = await setUp({ journal: { entries: [
    { id: 'e1', author: 'me', written_at: '2026-10-19T10:00:00+00:00', body: 'x', compile_id: 'ab12cd34ef56', suggested_to: [] }], suggested_to: [] } });
  assert.match(text(t.document.getElementById('inv-journal')), /me · 2026-10-19 · compile ab12cd34/);
});

const MD = '**bold words**\n\n- first item\n- second item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n';

test('an entry written in markdown shows bold, a list and a table as elements, inside a scroller', async () => {
  const t = await setUp({ journal: { entries: [
    { id: 'e1', author: 'alice', written_at: '2026-10-19T10:00:00+00:00', body: MD, compile_id: 'abcdef123456', suggested_to: [] },
  ], suggested_to: [] }, state: { notes: [{ id: 'e1', word: 'published' }], to_send: 0 } });
  const body = t.document.querySelector('[data-journal-body]');
  assert.equal(body.querySelector('strong').textContent, 'bold words');
  assert.equal(body.querySelectorAll('ul li').length, 2);
  assert.equal(body.querySelectorAll('table td').length, 2);
  assert.match(body.querySelector('table').parentElement.className, /overflow-x-auto/);
  assert.doesNotMatch(body.textContent, /\*\*|\|---/);
  // the metadata line is unchanged
  const entry = body.parentElement;
  assert.equal(text(entry.firstElementChild), 'alice · 2026-10-19 · compile abcdef12 · published');
});

test('hostile markup in an entry is inert: no script, no handler, no javascript: link, no image', async () => {
  const evil = '<script>window.__x=1</script>\n\n<img src=x onerror="window.__x=2">\n\n[click](javascript:alert(1))\n\n'
    + '[ok](https://example.com/p)\n\n<a href="javascript:alert(2)" onclick="alert(3)">raw</a>\n\n![pic](https://example.com/p.png)';
  const t = await setUp({ journal: { entries: [
    { id: 'e1', author: 'mallory', written_at: '2026-10-19T10:00:00+00:00', body: evil, suggested_to: [] },
  ], suggested_to: [] } });
  const body = t.document.querySelector('[data-journal-body]');
  assert.equal(body.querySelectorAll('script, img, iframe').length, 0);
  for (const el of body.querySelectorAll('*')) {
    for (const a of el.attributes) assert.doesNotMatch(a.name, /^on/i);
  }
  for (const a of body.querySelectorAll('a')) assert.doesNotMatch(a.getAttribute('href') || '', /^javascript:/i);
  assert.equal([...body.querySelectorAll('a')].find((a) => a.textContent === 'ok').getAttribute('href'), 'https://example.com/p');
  assert.equal(globalThis.window.__x, undefined);
});

test('an entry made from a chat turn shows the person\'s note apart, labelled "your note", and says it goes in the same Egeria Note', async () => {
  const t = await setUp({ journal: { entries: [
    { id: 'e1', author: 'me', written_at: '2026-10-22T08:00:00+00:00', body: 'Per chat\n\nQuestion: q', compile_id: 'abcdef12', note: 'check bob' },
    { id: 'e2', author: 'me', written_at: '2026-10-21T08:00:00+00:00', body: 'plain entry' }], suggested_to: [] } });
  const entries = [...t.document.querySelectorAll('#inv-journal [data-journal-body]')];
  assert.equal(entries.length, 2);
  const note = t.document.querySelector('#inv-journal [data-journal-note]');
  assert.equal(text(note), 'your note: check bob');
  assert.ok(!text(entries[0]).includes('check bob'), 'the note is not merged into the entry text');
  assert.equal(t.document.querySelectorAll('#inv-journal [data-journal-note]').length, 1, 'an entry without a note has no note line');
  assert.match(text(t.document.getElementById('inv-journal')), /note goes in the same Egeria Note, after the entry/);
});
