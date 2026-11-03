/** Behaviour: the investigation picker, and the "add to <investigation>" act.
 *
 *  W1-A of REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md (§2 picker, §4 select
 *  bar and the report / member-list acts). The owner's words: a person adds a
 *  database to an investigation from the sidebar, or from its report, without
 *  seeing the words "work list". Real app.js, real router, real index.html body;
 *  only fetch is stubbed.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_DIR = path.resolve(HERE, '../../resource_explorer/web/static/next');
const INDEX_HTML = fs.readFileSync(path.join(NEXT_DIR, 'index.html'), 'utf8');

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const $ = (d, sel) => d.querySelector(sel);

const INVESTIGATIONS = [
  { slug: 'c360', display_name: 'Customer 360', status: 'open' },
  { slug: 'old', display_name: 'Old closed one', status: 'closed' },
  { slug: 'paused', display_name: 'Paused one', status: 'suspended' },
];

const REPORT = {
  id: 'rec1', kind: 'report', name: 'High advisories', author: 'me', requested_at: '2026-10-01T10:00:00', uses: [],
  report: { header: '4 of 4 advisories', provenance: 'from cve_scan', analysis_id: 'cve_scan',
    groups: [{ name: 'g', count: 1, truncated: false, rows: [{ name: 'GHSA-1', detail: 'high' }] }] },
};

/** Recording backend. `members` is mutable: a POST to /members or to an act with
 *  action=scope adds to it, so the app's re-reads see the write. */
function makeBackend({ members = { c360: [], other: [] }, record = REPORT } = {}) {
  const calls = [];
  const created = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok([...INVESTIGATIONS, ...created]);
    if (method === 'POST' && u === '/api/investigations/') {
      const inv = { slug: 'brand-new', display_name: body.display_name, status: 'open' };
      created.push(inv); members['brand-new'] = [];
      return ok(inv);
    }
    let m = u.match(/^\/api\/investigations\/([^/?]+)\/members$/);
    if (m) {
      if (method === 'GET') return ok([...(members[m[1]] || [])]);
      if (method === 'POST') { (members[m[1]] = members[m[1]] || []).push({ entity_type: body.entity_type, entity_slug: body.entity_slug }); return ok([]); }
    }
    m = u.match(/\/records\/rec1\/act$/);
    if (m && method === 'POST') {
      if (body.action === 'scope') {
        const et = u.includes('/entity/database/') ? 'database' : 'repo';
        const slug = u.includes('/entity/database/') ? 'alpha' : 'alpha';
        const have = (members[body.investigation] = members[body.investigation] || []);
        const already = have.some((x) => x.entity_slug === slug);
        if (!already) have.push({ entity_type: et, entity_slug: slug });
        const name = [...INVESTIGATIONS, ...created].find((i) => i.slug === body.investigation).display_name;
        return ok({ action: 'scope', provenance: 'line', investigation: body.investigation, investigation_name: name, already_in_scope: already, record: record });
      }
      return ok({ action: body.action, record });
    }
    if (/\/records$/.test(u) && method === 'GET') return ok({ records: [record] });
    m = u.match(/\/members\/cve_scan\/promote/);
    if (m && method === 'POST') {
      const have = (members[body.investigation] = members[body.investigation] || []);
      const already = have.some((x) => x.entity_slug === 'alpha');
      if (!already) have.push({ entity_type: 'repo', entity_slug: 'alpha' });
      return ok({ action: body.action, investigation: body.investigation, investigation_name: 'Customer 360', already_in_scope: already, provenance: 'line' });
    }
    m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m && method === 'GET' && !['purposes', 'classifications'].includes(m[1])) {
      return ok({ slug: m[1], display_name: 'Customer 360', status: 'open', description: 'd', project_classification: 'StudyProject', purposes: [], visibility: 'public' });
    }
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (u === '/api/investigations/purposes') return ok({ purposes: ['Explore'] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [{ name: 'StudyProject', label: 'Study' }], bindings: [{ name: 'local', label: 'Local' }], default_classification: 'StudyProject', default_binding: 'local' });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u === '/api/db-servers/') return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules') || u.includes('/groups')) return ok([]);
    return ok({});
  };
  return { calls, members };
}

async function setUp(kind = 'db', { backend = {} } = {}) {
  const { document, window } = makeDomEnvironment();
  const be = makeBackend(backend);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.getElementById('rail').appendChild(rail);
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = kind;
  const rows = ['alpha', 'beta', 'gamma'].map((x) => ({
    slug: x, display_name: x, disposition: 'undecided', group_slug: '', working_set_hidden: false, credential_capability: null,
    ...(kind === 'repo' ? { github_url: `https://github.com/o/${x}` } : {}),
  }));
  s.projects = kind === 'repo' ? rows : []; s.databases = kind === 'db' ? rows : []; s.filesystems = [];
  if (kind === 'db') s.databasesLoaded = true;
  s.groups = [];
  s.investigations = INVESTIGATIONS.map((i) => ({ ...i })); s.investigation = ''; s.workingSet = new Set();
  s.selected = new Set(); s.selectMode = false; s.dispositionFacet = 'all'; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workListSlug = null; s.workListIndex = false; s.workLists = [];
  s.selectedSlug = 'alpha';
  s.stage = 'discovery'; s.subTab = 'questions';
  s.me = { user_id: 'me' };
  s.answers = new Map();
  app.renderIntentNav();
  app.renderSidebar();
  app.renderTopBar();
  document.querySelector('#intent-nav button[data-stage="discovery"]').click();
  await tick(150);
  return { document, window, app, s, ...be };
}

const picker = (d) => $(d, '[data-investigation-picker]');
const pickerChoices = (d) => [...picker(d).querySelectorAll('[data-pick-investigation]')].map((b) => b.dataset.pickInvestigation);

async function selectTwo(ctx) {
  const d = ctx.document;
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar input[data-sel="alpha"]').click();
  $(d, '#sidebar input[data-sel="beta"]').click();
}

/* ── The picker and the Select bar ───────────────────────────────────────── */

test('the picker lists the open investigations and not the closed or suspended ones, plus "start a new one"', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  await selectTwo(ctx);
  $(d, '[data-act="sel-scope-add"]').click();
  await tick(10);
  assert.ok(picker(d), 'the picker opens');
  assert.deepEqual(pickerChoices(d), ['c360']);
  assert.match(text(picker(d)), /start a new one…/);
  assert.doesNotMatch(text(picker(d)), /Old closed one|Paused one/);
  assert.doesNotMatch(text(picker(d)), /work list|working set/i);
});

test('add with none current: enabled, opens the picker, adds, makes it current, shows the note', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  await selectTwo(ctx);
  const add = $(d, '[data-act="sel-scope-add"]');
  assert.equal(add.disabled, false, 'enabled with none current');
  assert.equal(text(add), '＋ add to an investigation…');
  const rem = $(d, '[data-act="sel-scope-remove"]');
  assert.equal(rem.disabled, true);
  assert.match(text($(d, '[data-scope-reason]')), /no investigation selected/);
  add.click();
  await tick(10);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST').length, 0, 'nothing is written until one is chosen');
  $(d, '[data-pick-investigation="c360"]').click();
  await tick(150);
  assert.equal(picker(d), null, 'the picker closes');
  const posts = ctx.calls.filter((c) => c.method === 'POST' && /\/api\/investigations\/c360\/members$/.test(c.url));
  assert.deepEqual(posts.map((c) => c.body.entity_slug).sort(), ['alpha', 'beta']);
  assert.equal(ctx.s.investigation, 'c360', 'the chosen investigation is now current');
  assert.match(text($(d, '#sidebar-action')), /^Added 2 to Customer 360, now your current investigation\.$/);
  assert.equal(text($(d, '[data-act="sel-scope-add"]')), '＋ add to Customer 360');
});

test('"start a new one" reuses the New investigation dialog, then adds to the new one and makes it current', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  await selectTwo(ctx);
  $(d, '[data-act="sel-scope-add"]').click();
  await tick(10);
  $(d, '[data-pick-new]').click();
  await tick(60);
  assert.ok($(d, '#inv-new-name'), 'the existing creation form is shown');
  $(d, '#inv-new-name').value = 'Fresh start';
  $(d, '#inv-new-submit').click();
  await tick(200);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST' && c.url === '/api/investigations/').length, 1);
  assert.deepEqual(ctx.calls.filter((c) => c.method === 'POST' && /brand-new\/members$/.test(c.url)).map((c) => c.body.entity_slug).sort(), ['alpha', 'beta']);
  assert.equal(ctx.s.investigation, 'brand-new');
  assert.match(text($(d, '#sidebar-action')), /^Added 2 to Fresh start, now your current investigation\.$/);
});

test('add with one current adds directly, no picker', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  ctx.s.investigation = 'c360';
  await selectTwo(ctx);
  $(d, '[data-act="sel-scope-add"]').click();
  await tick(150);
  assert.equal(picker(d), null);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST' && /c360\/members$/.test(c.url)).length, 2);
  assert.match(text($(d, '#sidebar-action')), /^2 added to scope\.$/);
});

/* ── The report act ──────────────────────────────────────────────────────── */

async function openRecords(ctx, entityType = 'database') {
  const d = ctx.document;
  const host = d.createElement('div');
  host.id = 'records';
  d.getElementById('content').appendChild(host);
  await ctx.app.renderRecords('alpha', entityType);
  return host;
}
const actsText = (host) => text(host.querySelector('[data-record-acts]'));

test('the report act with one current reads "add to <name>", puts the resource in SCOPE with the line as the reason, mints no work list', async () => {
  const ctx = await setUp('db');
  ctx.s.investigation = 'c360';
  const host = await openRecords(ctx);
  const btn = host.querySelector('[data-record-act="scope"]');
  assert.equal(text(btn), 'add to Customer 360');
  assert.doesNotMatch(actsText(host), /work list|working set/i);
  assert.equal(host.querySelector('[data-record-act="work_list"]'), null);
  btn.click();
  await tick(200);
  const act = ctx.calls.find((c) => c.method === 'POST' && /\/records\/rec1\/act$/.test(c.url));
  assert.ok(act, 'the act was posted');
  assert.deepEqual([act.body.action, act.body.investigation], ['scope', 'c360']);
  assert.equal(ctx.calls.filter((c) => /work-lists/.test(c.url) && c.method !== 'GET').length, 0, 'no work list is created');
  assert.match(actsText(host), /added to Customer 360’s scope/);
  assert.match(actsText(host), /already in Customer 360’s scope/, 'and the act now says so');
});

test('the report act with none current reads "add to an investigation…" and opens the picker', async () => {
  const ctx = await setUp('db');
  const host = await openRecords(ctx);
  const btn = host.querySelector('[data-record-act="scope"]');
  assert.equal(text(btn), 'add to an investigation…');
  btn.click();
  await tick(10);
  assert.deepEqual(pickerChoices(ctx.document), ['c360']);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST').length, 0);
  $(ctx.document, '[data-pick-investigation="c360"]').click();
  await tick(250);
  const act = ctx.calls.find((c) => c.method === 'POST' && /\/records\/rec1\/act$/.test(c.url));
  assert.equal(act.body.investigation, 'c360');
  assert.equal(ctx.s.investigation, 'c360');
  assert.match(actsText(host), /added to Customer 360’s scope, now your current investigation/);
});

test('the report act on a resource already in scope says so as plain text and writes nothing', async () => {
  const ctx = await setUp('db', { backend: { members: { c360: [{ entity_type: 'database', entity_slug: 'alpha' }] } } });
  ctx.s.investigation = 'c360';
  const host = await openRecords(ctx);
  assert.equal(host.querySelector('[data-record-act="scope"]'), null, 'no button');
  const t = host.querySelector('[data-scope-act-text]');
  assert.equal(t.tagName, 'SPAN');
  assert.equal(text(t), 'already in Customer 360’s scope');
  t.click();
  await tick(30);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST').length, 0);
  assert.match(actsText(host), /note in journal/, '"note in journal" stays');
});

test('a long investigation name is cut at 24 characters on the act, with the full name in the title', async () => {
  const ctx = await setUp('db');
  const longName = 'Customer 360 quarterly governance review programme';
  ctx.s.investigations = [{ slug: 'long', display_name: longName, status: 'open' }];
  ctx.s.investigation = 'long';
  const host = await openRecords(ctx);
  const btn = host.querySelector('[data-record-act="scope"]');
  assert.equal(text(btn), `add to ${longName.slice(0, 23)}…`);
  assert.ok(btn.title.includes(longName));
});

/* ── The member-list act ─────────────────────────────────────────────────── */

function memberFooter(ctx) {
  const d = ctx.document;
  const out = d.createElement('div');
  out.innerHTML = '<div id="member-selection"></div><input type="checkbox" data-pick="GHSA-1" checked><input type="checkbox" data-pick="GHSA-2" checked>';
  d.body.appendChild(out);
  ctx.s.projects = [{ slug: 'alpha', display_name: 'alpha' }];
  ctx.app.wireSelection(out, { slug: 'alpha', analysisId: 'cve_scan', metric: 'advisories', data: { total: 4, metric: 'advisories' } });
  return out;
}

test('the member-list act: one current → "add to <name>", scope with provenance, no work list', async () => {
  const ctx = await setUp('repo');
  ctx.s.investigation = 'c360';
  const out = memberFooter(ctx);
  const btn = out.querySelector('[data-promote="scope"]');
  assert.equal(text(btn), 'add to Customer 360');
  assert.equal(out.querySelector('[data-promote="work_list"]'), null);
  assert.doesNotMatch(text(out), /work list/i);
  btn.click();
  await tick(200);
  const call = ctx.calls.find((c) => c.method === 'POST' && /promote/.test(c.url));
  assert.deepEqual([call.body.action, call.body.investigation], ['scope', 'c360']);
  assert.match(text(out.querySelector('#promote-status')), /added to Customer 360’s scope/);
  assert.match(text(out), /already in Customer 360’s scope/);
  assert.equal(out.querySelector('[data-promote="scope"]'), null);
});

test('the member-list act: none current → "add to an investigation…" opens the picker', async () => {
  const ctx = await setUp('repo');
  const out = memberFooter(ctx);
  const btn = out.querySelector('[data-promote="scope"]');
  assert.equal(text(btn), 'add to an investigation…');
  btn.click();
  await tick(10);
  assert.deepEqual(pickerChoices(ctx.document), ['c360']);
});

/* ── Source scan: the words ──────────────────────────────────────────────── */

test('no "work list" label on the new paths', () => {
  const pickerSrc = fs.readFileSync(path.join(NEXT_DIR, 'investigation-picker.js'), 'utf8');
  assert.doesNotMatch(pickerSrc, /work list|working set/i);
  const app = fs.readFileSync(path.join(NEXT_DIR, 'app.js'), 'utf8');
  assert.doesNotMatch(app, />add to work list</);
});
