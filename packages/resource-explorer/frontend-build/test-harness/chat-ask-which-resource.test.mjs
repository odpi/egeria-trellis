/** Behaviour (PI-119, owner ruling 2026-10-13): a question asked on one resource that names ANOTHER is held
 *  by the server and the person is asked. "Ask about <other>" switches the selection and asks again;
 *  "Keep <this>" asks again as written with the check skipped; the fuzzy "did you mean" box offers
 *  Yes-remember / Ask-once / No.
 *
 *  Real app.js, real chat.js, real router. ONLY `fetch` is replaced (a stateful stub of the routes). The
 *  switch is asserted from the app state and from the request the re-ask sent, not from the button press.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SCOPED = 'alpha_repo';

function makeServer() {
  const s = { calls: [], aliases: [] };
  const enc = new TextEncoder();
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b, text: async () => JSON.stringify(b) });
    if (u === '/api/databases/') return ok([{ slug: 'orders_warehouse', display_name: 'Orders Warehouse' }]);
    if (u === '/api/filesystems/') return ok([]);
    if (u === '/api/aliases/' && method === 'POST') {
      s.aliases.push({ alias: String(body.alias).toLowerCase().replace(/[ -]/g, '_'), project_slug: body.project_slug });
      return ok({ saved: true });
    }
    if (u.startsWith('/api/aliases/') && method === 'GET') return ok({ project_slug: u.split('/').pop(), aliases: s.aliases });
    if (u === '/api/query/stream') {
      let frames;
      if (body.skip_resource_check || /^what is alpha/.test(body.query)) {
        frames = [{ t: 'chunk', v: `Answer on ${body.project_slug}.` },
          { t: 'done', intent: 'lookup', hash: 'h', cached: false, compiled: { text: 't', manifest: { packed: [], gaps: [] } } }];
      } else if (/orders/.test(body.query)) {
        frames = [{ t: 'chunk', v: 'held' }, { t: 'done', held: true, resource_question: {
          kind: 'other_resource', named_slug: 'orders_warehouse', named_name: 'Orders Warehouse', named_type: 'database', scoped_slug: SCOPED } }];
      } else if (/misspelt/.test(body.query)) {
        frames = [{ t: 'chunk', v: 'held' }, { t: 'done', held: true, alias_suggestion: {
          term: 'bravo servise', candidate_slug: 'bravo_service', candidate_name: 'Bravo Service', candidate_type: 'repo', scoped_slug: SCOPED } }];
      } else {
        frames = [{ t: 'chunk', v: 'held' }, { t: 'done', held: true, resource_question: {
          kind: 'other_resource', named_slug: 'bravo_service', named_name: 'Bravo Service', named_type: 'repo', scoped_slug: SCOPED } }];
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

async function setUp() {
  const { document, window } = makeDomEnvironment();
  window.localStorage.clear();
  const server = makeServer();
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const chat = await import('/static/next/chat.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: SCOPED, stage: 'scouting', subTab: 'questions', chat: [], promoted: null,
    projects: [{ slug: SCOPED, display_name: 'Alpha Repo' }, { slug: 'bravo_service', display_name: 'Bravo Service' }],
    databases: [], databasesLoaded: false, groups: [], investigations: [], investigation: '', workListSlug: null,
    workListIndex: false, perspectives: [], allPerspectives: [], workingSet: new Set(), me: { user_id: 'me' },
    activePerspectives: new Set(), whyOpen: new Set() });
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail', 'sidebar', 'rail-scope', 'scope-slug', 'investigation-name', 'whoami', 'activity-count', 'answered-count']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  const sw = document.createElement('a'); sw.id = 'switch-ui'; document.body.appendChild(sw);
  const re = document.createElement('div'); re.id = 'rail-evidence'; document.getElementById('rail').appendChild(re);
  chat.renderRail();
  return { document, app, server };
}

async function ask(t, q) {
  t.document.getElementById('ask-input').value = q;
  t.document.getElementById('ask-submit').click();
  await wait(350);
}
const streams = (server) => server.calls.filter((c) => c.url === '/api/query/stream');

test('other resource: the turn is held and offers Ask about <named> / Keep <scoped>', async () => {
  const t = await setUp();
  await ask(t, 'how does bravo service compare');
  const box = t.document.querySelector('[data-resource-question-box]');
  assert.ok(box, 'the question to the person is on the turn');
  assert.match(text(box.querySelector('[data-rq-sentence]')), /You asked about Bravo Service; this chat is on alpha_repo\./);
  assert.match(text(box.querySelector('[data-rq-switch]')), /^Ask about Bravo Service$/);
  assert.match(text(box.querySelector('[data-rq-keep]')), /^Keep alpha_repo$/);
  assert.ok(box.querySelector('[data-cue="human"]'), 'a visual cue, not only a sentence');
  assert.equal(streams(t.server).length, 1);
  assert.equal(streams(t.server)[0].body.skip_resource_check, false);
  // literal classes only: no template-literal residue made it into the markup
  assert.ok(!/\$\{|undefined/.test(box.outerHTML));
});

test('Ask about <named>: the selection changes (read back from state) and the question is asked again there', async () => {
  const t = await setUp();
  await ask(t, 'how does bravo service compare');
  t.document.querySelector('[data-rq-switch]').click();
  await wait(400);
  assert.equal(t.app.state.selectedSlug, 'bravo_service');
  const second = streams(t.server)[1];
  assert.ok(second, 're-asked');
  assert.equal(second.body.project_slug, 'bravo_service');
  assert.equal(second.body.skip_resource_check, true, 'no second question about the same name');
  assert.equal(second.body.query, 'how does bravo service compare');
});

test('Ask about a database: the resource TYPE switches too, not only the slug', async () => {
  const t = await setUp();
  await ask(t, 'which tables does orders have');
  t.document.querySelector('[data-rq-switch]').click();
  await wait(500);
  assert.equal(t.app.state.resourceType, 'db');
  assert.equal(t.app.state.selectedSlug, 'orders_warehouse');
  const second = streams(t.server)[1];
  assert.equal(second.body.project_slug, 'orders_warehouse');
  assert.equal(second.body.entity_type, 'database');
});

test('Keep <scoped>: selection unchanged, asked again as written with the check skipped', async () => {
  const t = await setUp();
  await ask(t, 'how does bravo service compare');
  t.document.querySelector('[data-rq-keep]').click();
  await wait(400);
  assert.equal(t.app.state.selectedSlug, SCOPED);
  const second = streams(t.server)[1];
  assert.equal(second.body.project_slug, SCOPED);
  assert.equal(second.body.skip_resource_check, true);
});

test('fuzzy name: did-you-mean box has Yes-remember / Ask-once / No', async () => {
  const t = await setUp();
  await ask(t, 'misspelt bravo servise');
  const box = t.document.querySelector('[data-alias-box]');
  assert.match(text(box.querySelector('[data-alias-sentence]')), /did you mean Bravo Service \(bravo_service\)\?/);
  assert.ok(box.querySelector('[data-alias-yes]') && box.querySelector('[data-alias-once]') && box.querySelector('[data-alias-no]'));
  assert.match(text(box.querySelector('[data-alias-once]')), /^Ask about Bravo Service once$/);
});

test('fuzzy name, Ask once: switches and re-asks, saving NO alias', async () => {
  const t = await setUp();
  await ask(t, 'misspelt bravo servise');
  t.document.querySelector('[data-alias-once]').click();
  await wait(400);
  assert.equal(t.app.state.selectedSlug, 'bravo_service');
  assert.deepEqual(t.server.aliases, []);
  assert.equal(streams(t.server)[1].body.project_slug, 'bravo_service');
});

test('fuzzy name, Yes remember it: saved, READ BACK from the alias list, then asked on the candidate', async () => {
  const t = await setUp();
  await ask(t, 'misspelt bravo servise');
  t.document.querySelector('[data-alias-yes]').click();
  await wait(500);
  assert.deepEqual(t.server.aliases, [{ alias: 'bravo_servise', project_slug: 'bravo_service' }]);
  assert.ok(t.server.calls.some((c) => c.method === 'GET' && c.url === '/api/aliases/bravo_service'), 'read back');
  assert.equal(t.app.state.selectedSlug, 'bravo_service');
  assert.equal(streams(t.server)[1].body.project_slug, 'bravo_service');
});

test('fuzzy name, No: nothing saved, asked on the current resource with the check skipped', async () => {
  const t = await setUp();
  await ask(t, 'misspelt bravo servise');
  t.document.querySelector('[data-alias-no]').click();
  await wait(400);
  assert.deepEqual(t.server.aliases, []);
  assert.equal(t.app.state.selectedSlug, SCOPED);
  assert.equal(streams(t.server)[1].body.skip_resource_check, true);
});

test('a question that names nothing else is answered with no box', async () => {
  const t = await setUp();
  await ask(t, 'what is alpha doing');
  assert.equal(t.document.querySelector('[data-resource-question-box]'), null);
  assert.equal(t.document.querySelector('[data-alias-box]'), null);
});

test('chat.js builds no class names from interpolation in the new boxes', () => {
  const src = readFileSync(new URL('../../resource_explorer/web/static/next/chat.js', import.meta.url), 'utf8');
  const block = src.slice(src.indexOf('function otherResourceHtml'), src.indexOf('function redrawResourceQuestion'));
  assert.ok(block.length > 500, 'the block under test was found');
  for (const m of block.matchAll(/class="([^"]*)"/g)) assert.ok(!m[1].includes('${'), `interpolated class: ${m[1]}`);
});
