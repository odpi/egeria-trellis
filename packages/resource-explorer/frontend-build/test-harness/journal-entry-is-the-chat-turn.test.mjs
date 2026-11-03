/** The journal entry IS the chat turn (owner 2026-10-22). Two ways in, one row out:
 *    - the chat answers "save this to the journal" with the journal tool's question and draws [Add] [Not now];
 *    - the pane's per-turn control shows the entry DERIVED from the turn, read-only, with [Add] [Not now] and one
 *      optional note kept apart from it.
 *  Real app.js, real chat.js. ONLY `fetch` is replaced. Writes are asserted from the requests the page sent
 *  and from the state on the element, not from the button press.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SCOPED = 'docling_core';
const QUESTION_WORDS = 'Add this turn to the investigation journal? [Add] [Not now]';
const DERIVED = 'Per chat\n\nQuestion: Who are the main contributors?\n\nAnswer:\n\nalice 120\n\n- Scope: investigation study - resource docling_core\n- Evidence: contributor_stats';

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
        frames = [{ t: 'chunk', v: 'alice 120' },
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
const writes = (server) => server.calls.filter((c) => c.url === '/api/journal/investigation/study/turn' && c.body.confirm);
const previews = (server) => server.calls.filter((c) => c.url === '/api/journal/investigation/study/turn' && !c.body.confirm);

test('"save this to the journal": the chat asks in its words, sends the open investigation, and draws Add / Not now', async () => {
  const t = await setUp();
  await ask(t, 'save this to the journal');
  const stream = t.server.calls.find((c) => c.url === '/api/query/stream');
  assert.equal(stream.body.investigation, 'study', 'the tool is told which investigation is open');
  const box = t.document.querySelector('[data-journal-proposal-box]');
  assert.ok(box, 'the question is on the turn');
  assert.equal(text(box.querySelector('[data-jp-sentence]')), 'Add this turn to the investigation journal?');
  assert.equal(text(box.querySelector('[data-jp-add]')), 'Add');
  assert.equal(text(box.querySelector('[data-jp-no]')), 'Not now');
  assert.ok(box.querySelector('[data-cue="human"]'), 'a visual cue, not only a sentence');
  assert.doesNotMatch(text(t.document.getElementById('promoted-body')), /\[Add\]|\[Not now\]/, 'the bracketed words become controls');
  assert.equal(writes(t.server).length, 0, 'asking writes nothing');
  assert.ok(!/\$\{|undefined/.test(box.outerHTML));
});

test('Add on the chat question writes once, with the turn the tool named, and shows the saved cue', async () => {
  const t = await setUp();
  await ask(t, 'save this to the journal');
  t.document.querySelector('[data-jp-add]').click();
  await wait(200);
  const w = writes(t.server);
  assert.equal(w.length, 1);
  const stream = t.server.calls.find((c) => c.url === '/api/query/stream');
  assert.deepEqual(w[0].body, { session_id: stream.body.session_id, exchange_id: 41, note: '', confirm: true },
    'only the session and the stored exchange travel; no question or answer text');
  const box = t.document.querySelector('[data-journal-proposal-box]');
  assert.ok(box.querySelector('[data-cue="measured"]'));
  assert.match(text(box), /added to the journal/);
  assert.equal(box.querySelector('[data-jp-add]'), null, 'no second press');
});

test('Not now writes nothing and says so on the element', async () => {
  const t = await setUp();
  await ask(t, 'save this to the journal');
  t.document.querySelector('[data-jp-no]').click();
  await wait(100);
  assert.equal(writes(t.server).length, 0);
  assert.ok(t.document.querySelector('[data-journal-proposal-box] [data-cue="optional"]'));
});

test('a failed write is a cue and a reason, and Add stays usable', async () => {
  const t = await setUp({ writeFails: true });
  await ask(t, 'save this to the journal');
  t.document.querySelector('[data-jp-add]').click();
  await wait(200);
  const box = t.document.querySelector('[data-journal-proposal-box]');
  assert.ok(box.querySelector('[data-cue="error"]'));
  assert.match(text(box), /registry down/);
  assert.ok(box.querySelector('[data-jp-add]'));
});

test('the pane control shows the DERIVED entry read-only with Add / Not now and an optional note; Add writes it', async () => {
  const t = await setUp();
  await ask(t, 'Who are the main contributors?');
  t.document.querySelector('[data-act="journal-turn"]').click();
  await wait(200);
  assert.equal(previews(t.server).length, 1);
  assert.deepEqual(Object.keys(previews(t.server)[0].body).sort(), ['confirm', 'exchange_id', 'note', 'session_id']);
  assert.equal(previews(t.server)[0].body.exchange_id, 41);
  const form = t.document.querySelector('[data-journal-form]');
  assert.equal(form.querySelector('textarea'), null, 'no editable text: the entry is the turn');
  assert.equal(form.querySelector('[data-journal-entry]').dataset.raw, DERIVED);
  assert.equal(text(form.querySelector('[data-journal-add]')), 'Add');
  assert.equal(text(form.querySelector('[data-journal-cancel]')), 'Not now');
  assert.equal(writes(t.server).length, 0, 'showing it writes nothing');
  form.querySelector('[data-journal-note-input]').value = 'check the bot account';
  form.querySelector('[data-journal-add]').click();
  await wait(200);
  const w = writes(t.server);
  assert.equal(w.length, 1);
  assert.equal(w[0].body.note, 'check the bot account');
  assert.equal(w[0].body.answer, undefined, 'the client sends no answer text');
  // state on the element: the cue replaces the form, and the trigger is no longer a button
  assert.ok(t.document.querySelector('[data-journal-saved] [data-cue="measured"]'));
  const trigger = t.document.querySelector('[data-act="journal-turn"]');
  assert.equal(trigger.dataset.journalState, 'saved');
  assert.equal(trigger.tagName, 'SPAN');
});

test('the pane control: Not now closes the entry and writes nothing', async () => {
  const t = await setUp();
  await ask(t, 'Who are the main contributors?');
  t.document.querySelector('[data-act="journal-turn"]').click();
  await wait(200);
  t.document.querySelector('[data-journal-cancel]').click();
  assert.equal(t.document.querySelector('[data-journal-entry]'), null);
  assert.equal(writes(t.server).length, 0);
});

test('no investigation open: the control is disabled with the words, and nothing is written', async () => {
  const t = await setUp({ investigation: '' });
  await ask(t, 'Who are the main contributors?');
  const btn = t.document.querySelector('[data-act="journal-turn"]');
  assert.equal(btn.disabled, true);
  assert.equal(btn.title, 'no investigation is open · choose one on the rail');
  btn.click();
  await wait(100);
  assert.equal(t.server.calls.filter((c) => c.url.includes('/api/journal/')).length, 0);
});

test('a turn already in the journal says so when the control is opened, with the saved cue and no Add', async () => {
  const t = await setUp({ already: true });
  await ask(t, 'Who are the main contributors?');
  t.document.querySelector('[data-act="journal-turn"]').click();
  await wait(200);
  assert.match(text(t.document.querySelector('[data-journal-saved]')), /already in the journal/);
  assert.equal(t.document.querySelector('[data-journal-add]'), null);
  assert.equal(t.document.querySelector('[data-act="journal-turn"]').dataset.journalState, 'saved');
  assert.equal(writes(t.server).length, 0);
});

test('the chat question turn has no per-turn journal control (it is not itself a turn to add)', async () => {
  const t = await setUp();
  await ask(t, 'save this to the journal');
  assert.equal(t.document.querySelector('[data-act="journal-turn"]'), null);
});

test('Add on the chat question marks the EARLIER turn it named as in the journal', async () => {
  const t = await setUp();
  await ask(t, 'Who are the main contributors?');
  await ask(t, 'save this to the journal');
  t.document.querySelector('[data-jp-add]').click();
  await wait(200);
  const earlier = t.app.state.chat.find((x) => x.exchangeId === 41);
  assert.ok(earlier && earlier.journalSaved, 'the answered turn is marked saved');
});
