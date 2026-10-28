/** Behaviour: the in-page text prompt (confirm-dialog.js inPagePrompt) and the places that used native
 *  window.prompt / confirm.
 *
 *  Owner, 2026-10-13: "I tried to select all the opea repos and save as work list but there was no ability to
 *  define a new worklist." The embedded browser pane declines native dialogs silently, so the press did nothing.
 *  These run the REAL app.js / rfa.js, click the real buttons, and read the DOM. Only fetch is stubbed; the native
 *  functions THROW if reached. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_DIR = path.resolve(HERE, '../../resource_explorer/web/static/next');
const INDEX_HTML = fs.readFileSync(path.join(NEXT_DIR, 'index.html'), 'utf8');
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));

function env() {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.CSS = globalThis.CSS || { escape: (x) => String(x).replace(/[^\w-]/g, (c) => `\\${c}`) };
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const boom = (n) => () => { throw new Error(`native ${n} reached`); };
  for (const n of ['prompt', 'confirm', 'alert']) { window[n] = boom(n); globalThis[n] = window[n]; }
  return { document, window };
}

const field = (d) => d.querySelector('[data-prompt-dialog] [data-prompt-input]');
const key = (win, el, k) => el.dispatchEvent(new win.KeyboardEvent('keydown', { key: k, bubbles: true }));

/* ── the dialog itself ──────────────────────────────────────────────────── */

test('inPagePrompt: focus lands in a labelled input; Enter resolves the typed value', async () => {
  const { document, window } = env();
  const { inPagePrompt } = await import('/static/next/confirm-dialog.js');
  const p = inPagePrompt({ title: 'Save as work list', label: 'Work list name', defaultValue: 'Candidates' });
  const box = document.querySelector('[data-prompt-dialog]');
  assert.ok(box);
  assert.equal(box.getAttribute('role'), 'dialog');
  assert.equal(box.getAttribute('aria-modal'), 'true');
  const input = field(document);
  assert.equal(input.value, 'Candidates');
  assert.equal(document.activeElement, input, 'focus moves to the input');
  assert.equal(box.querySelector(`label[for="${input.id}"]`).textContent, 'Work list name');
  input.value = 'opea repos';
  key(window, input, 'Enter');
  assert.equal(await p, 'opea repos');
  assert.equal(document.querySelector('[data-prompt-dialog]'), null, 'closed after answering');
});

test('inPagePrompt: confirm button resolves the value; Cancel, Escape and a click outside resolve null', async () => {
  const { document, window } = env();
  const { inPagePrompt } = await import('/static/next/confirm-dialog.js');
  let p = inPagePrompt({ title: 't', defaultValue: 'x' });
  field(document).value = 'typed';
  document.querySelector('[data-prompt-yes]').click();
  assert.equal(await p, 'typed');

  p = inPagePrompt({ title: 't', defaultValue: 'x' });
  document.querySelector('[data-prompt-no]').click();
  assert.equal(await p, null, 'Cancel');

  p = inPagePrompt({ title: 't' });
  key(window, document, 'Escape');
  assert.equal(await p, null, 'Escape');

  p = inPagePrompt({ title: 't' });
  document.querySelector('[data-prompt-dialog]').click();   // the backdrop itself
  assert.equal(await p, null, 'a click outside');

  p = inPagePrompt({ title: 't' });
  field(document).click();                                   // inside the card: stays open
  assert.ok(document.querySelector('[data-prompt-dialog]'));
  document.querySelector('[data-prompt-no]').click();
  await p;
});

test('inPagePrompt: with required, an empty or blank answer is refused and the dialog says so', async () => {
  const { document, window } = env();
  const { inPagePrompt } = await import('/static/next/confirm-dialog.js');
  const p = inPagePrompt({ title: 't', required: true, defaultValue: '' });
  document.querySelector('[data-prompt-yes]').click();
  assert.ok(document.querySelector('[data-prompt-dialog]'), 'still open');
  const err = document.querySelector('[data-prompt-error]');
  assert.equal(err.hidden, false);
  assert.match(err.textContent, /needs a value/);
  field(document).value = '   ';
  key(window, field(document), 'Enter');
  assert.ok(document.querySelector('[data-prompt-dialog]'), 'blank is empty too');
  field(document).value = 'ok';
  key(window, field(document), 'Enter');
  assert.equal(await p, 'ok');
});

test('inPagePrompt: not required, an empty answer is the empty string (not null)', async () => {
  const { document } = env();
  const { inPagePrompt } = await import('/static/next/confirm-dialog.js');
  const p = inPagePrompt({ title: 't' });
  document.querySelector('[data-prompt-yes]').click();
  assert.equal(await p, '');
});

/* ── save as work list, end to end ──────────────────────────────────────── */

async function sidebarWithTwoTicked() {
  const { document, window } = env();
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    if (method !== 'GET') posts.push({ method, u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'POST' && u === '/api/work-lists/') return ok({ slug: 'opea', display_name: 'x', members: [] });
    if (u.startsWith('/api/work-lists')) return ok([]);
    if (u.startsWith('/api/investigations/?')) return ok([]);
    return ok(u.match(/(\/projects|\/groups|\/schedules|\/subscriptions)/) ? [] : {});
  };
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div'); rail.id = 'rail-evidence'; document.getElementById('rail').appendChild(rail);
  const api = await import('/static/re-api.js'); api.clearCache();
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = 'repo';
  s.projects = ['opea-a', 'opea-b', 'other'].map((slug) => ({ slug, display_name: slug, disposition: 'undecided', group_slug: '', working_set_hidden: false, github_url: `https://github.com/o/${slug}` }));
  s.databases = []; s.filesystems = []; s.groups = [];
  s.investigations = []; s.investigation = ''; s.workingSet = new Set();
  s.selected = new Set(); s.selectMode = false; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workListSlug = null; s.workListIndex = false; s.workLists = [];
  s.selectedSlug = 'other'; s.stage = 'discovery'; s.subTab = 'questions'; s.answers = new Map();
  app.renderIntentNav(); app.renderSidebar(); app.renderTopBar();
  document.querySelector('#sidebar [data-act="select-mode"]').click();
  document.querySelector('#sidebar input[data-sel="opea-a"]').click();
  document.querySelector('#sidebar input[data-sel="opea-b"]').click();
  assert.deepEqual([...s.selected].sort(), ['opea-a', 'opea-b']);
  return { document, window, s, posts };
}

test('save as work list: tick 2, press, a name box opens, type a name, Save -> createWorkList with that name and the slugs', async () => {
  const { document, window, posts } = await sidebarWithTwoTicked();
  document.querySelector('[data-act="sel-worklist"]').click();
  await tick(5);
  const input = field(document);
  assert.ok(input, 'pressing "save as work list…" opens an in-page name box (native prompt would have thrown)');
  assert.match(document.querySelector('[data-prompt-dialog]').textContent, /2 resource\(s\)/);
  assert.equal(input.value, 'Candidates', 'the default is as it was');
  input.value = 'OPEA repos';
  key(window, input, 'Enter');
  await tick(60);
  const created = posts.filter((p) => p.u === '/api/work-lists/');
  assert.equal(created.length, 1);
  assert.equal(created[0].body.display_name, 'OPEA repos');
  assert.deepEqual([...created[0].body.entity_slugs].sort(), ['opea-a', 'opea-b']);
});

test('save as work list: Cancel creates nothing and leaves the ticks', async () => {
  const { document, s, posts } = await sidebarWithTwoTicked();
  document.querySelector('[data-act="sel-worklist"]').click();
  await tick(5);
  document.querySelector('[data-prompt-no]').click();
  await tick(60);
  assert.deepEqual(posts, []);
  assert.equal(s.selected.size, 2);
});

/* ── an RFA prompt site ────────────────────────────────────────────────── */

async function rfaDrawer() {
  const { document, window } = env();
  const patches = [];
  globalThis.fetch = async (url, options = {}) => {
    const m = (options.method || 'GET').toUpperCase();
    if (m === 'PATCH') patches.push({ url: String(url), body: JSON.parse(options.body) });
    const rows = [{ id: 'r1', entity_slug: 'alpha', entity_name: 'alpha', summary: 'fix it', rfa_status: 'open',
      assignee: 'ann', ts: '2026-10-01T00:00:00Z', analysis_name: 'a' }];
    return { ok: true, status: 200, json: async () => (String(url) === '/api/activity/rfas' ? rows : {}) };
  };
  const api = await import('/static/re-api.js'); api.clearCache();
  const rfa = await import('/static/next/rfa.js');
  rfa.openRfaDrawer('');
  await tick(60);
  return { document, window, patches };
}

test('RFA Reassign: a name box in the page, prefilled; Cancel records nothing, Assign sends the typed name', async () => {
  const { document, window, patches } = await rfaDrawer();
  document.querySelector('[data-rfa-act="reassigned"]').click();
  await tick(5);
  assert.ok(field(document), 'the native prompt would have thrown; an in-page box opened');
  document.querySelector('[data-prompt-no]').click();
  await tick(40);
  assert.deepEqual(patches, [], 'Cancel records nothing');
  document.querySelector('[data-rfa-act="reassigned"]').click();
  await tick(5);
  const input = field(document);
  assert.equal(input.value, 'ann');
  assert.match(document.querySelector('[data-prompt-dialog]').textContent, /Assign to \(name or email\)/);
  input.value = ' bob ';
  key(window, input, 'Enter');
  await tick(60);
  assert.equal(patches.length, 1);
  assert.equal(patches[0].body.status, 'reassigned');
  assert.equal(patches[0].body.assignee, 'bob');
});

/* ── a confirm site ─────────────────────────────────────────────────────── */

test('a converted confirm site (Automate schedule remove) asks in the page; declining sends nothing, accepting does', async () => {
  const { document } = env();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const m = (options.method || 'GET').toUpperCase();
    if (m !== 'GET') calls.push(`${m} ${url}`);
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const api = await import('/static/re-api.js'); api.clearCache();
  const { inPageConfirm } = await import('/static/next/confirm-dialog.js');
  // The site's own source holds the call; pin that it is the in-page one and that the dialog answers both ways.
  const src = fs.readFileSync(path.join(NEXT_DIR, 'stages/automate.js'), 'utf8');
  assert.match(src, /await inPageConfirm\(\{ title: 'Remove schedule\?'/);
  const no = autoAnswerConfirm(document, { answer: false });
  assert.equal(await inPageConfirm({ title: 'Remove schedule?', message: 'm', confirmLabel: 'Remove' }), false);
  no.stop();
  const yes = autoAnswerConfirm(document, { answer: true });
  assert.equal(await inPageConfirm({ title: 'Remove schedule?', message: 'm', confirmLabel: 'Remove' }), true);
  yes.stop();
  assert.deepEqual(calls, []);
});
