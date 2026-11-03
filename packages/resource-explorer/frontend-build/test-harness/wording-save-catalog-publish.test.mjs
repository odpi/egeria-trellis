/** Behaviour: the wording slice (REPLY-DESIGNER-SAVE-AND-PUBLISH-VERBS.md, owner decision 2026-10-06).
 *
 *  Two families of verb, never mixed. Saved in RE's record: **Save** on a control that ends a form, and the line after
 *  the click says "saved · who · when" (from the re-read row, never the click). Sent to Egeria: **Catalog** (Curate's
 *  commit, every kind) or **Publish** (any other send). The reserved verb is spelled the US way, everywhere on the page.
 *
 *  Real app.js, real router, the real Curate nav button, a stateful stub behind fetch.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const NEXT_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'resource_explorer', 'web', 'static', 'next');
const INDEX_HTML = fs.readFileSync(path.join(NEXT_DIR, 'index.html'), 'utf8');
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SAVED = /^saved · me · 10-01 10:00/;

function makeServer({ signedIn = true } = {}) {
  const s = {
    calls: [], signedIn,
    tags: [], feedback: [], journal: [],
    groups: [{ slug: 'sales-platform', display_name: 'Sales platform' }, { slug: 'other', display_name: 'Other' }],
    groupOf: { adventureworks: 'sales-platform' },
  };
  const row = (extra) => ({ created_at: '2026-10-01T10:00:00', author: 'me', authored: true, author_label: 'me', ...extra });
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    const m = u.match(/\/api\/curate\/(tags-detail|tags|feedback|notes)\/(?:[a-z]+)\/([^/?]+)/);
    if (method === 'GET' && u.endsWith('/api/curate/tags')) return ok([]);
    if (m) {
      const kind = m[1];
      if (method === 'GET') return ok(kind === 'feedback' ? s.feedback : kind === 'notes' ? [] : s.tags);
      if (!s.signedIn) return err(401, 'Sign in to do that');
      if (kind === 'tags') { s.tags.push(row({ tag: body.tag.trim().toLowerCase() })); return ok({ status: 'success' }); }
      if (kind === 'feedback') { s.feedback.push(row({ id: 'f', ...body })); return ok({ id: 'f' }); }
    }
    if (u.includes('/api/journal/')) {
      if (method === 'GET') return ok({ entries: s.journal, suggested_to: [] });
      if (!s.signedIn) return err(401, 'Sign in');
      s.journal.push({ id: 'j-new', body: body.body, author: 'me', written_at: '2026-10-01T10:00:00', suggested_to: [] });
      return ok({ id: 'j-new', work_lists: [] });
    }
    if (u.endsWith('/group') && method === 'POST') {
      if (!s.signedIn) return err(401, 'Sign in to change the group');
      s.groupOf.adventureworks = body.group_slug;
      return ok({ slug: 'adventureworks', resource_type: body.resource_type, group_slug: body.group_slug,
        saved_by: 'me', saved_at: '2026-10-01T10:00:00' });
    }
    if (u.includes('/api/projects/groups')) return ok(s.groups);
    if (u.includes('/api/databases/')) return ok([{ slug: 'adventureworks', group_slug: s.groupOf.adventureworks || '' }]);
    if (u.includes('/api/filesystems/')) return ok([{ slug: 'adventureworks', group_slug: '' }]);
    if (u.includes('/api/projects/?')) return ok([]);
    if (u.includes('/curate/plan')) return ok({
      technology_type: 'Git repository', disposition: 'using', in_population: true,
      last_surveyed_at: null, what_it_is: [], what_it_holds: [], relates: [], commits: [],
    });
    if (u.includes('/components/tree')) return ok({ branches: [], topology: '' });
    if (u.includes('/blueprints')) return ok({ blueprints: [], perspectives: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    if (u.includes('/api/catalogue-scope/')) return ok({});
    return ok({});
  };
  return s;
}

async function setUp(resourceType, { signedIn = true } = {}) {
  const { document, window } = makeDomEnvironment();
  const server = makeServer({ signedIn });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, {
    resourceType, selectedSlug: 'adventureworks', stage: 'understanding', subTab: 'questions', investigations: [],
    investigation: '', workListSlug: null, workListIndex: false, me: signedIn ? { user_id: 'me' } : null,
    groups: server.groups, databasesLoaded: true, filesystemsLoaded: true, perspectives: [], allPerspectives: [],
    databases: [{ slug: 'adventureworks', group_slug: server.groupOf.adventureworks }],
    filesystems: [{ slug: 'adventureworks', group_slug: '' }],
  });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  const btn = document.querySelector('#intent-nav button[data-stage="curate"]');
  btn.click();
  await wait();
  return { document, app, server };
}

const host = (d) => d.getElementById('curate-host');
const q = (d, sel) => host(d).querySelector(sel);
const label = (el) => el.textContent.replace(/\s+/g, ' ').trim();

/* ── Saved in RE's record: Save, and the line after says who and when ───────────────────────────── */

test('tags: the control beside the input is "Save", and the line after reads "saved · who · when"', async () => {
  const { document } = await setUp('db');
  assert.equal(label(q(document, '[data-curate-tag-add]')), 'Save');
  q(document, '[data-curate-tag-input]').value = 'Sample-Data';
  q(document, '[data-curate-tag-add]').click();
  await wait();
  const line = label(q(document, '[data-curate-findable-status]'));
  assert.match(line, SAVED);
  assert.match(line, /“sample-data” is on the list/);
});

test('ratings: the control is "Save rating", and the line after reads "saved · who · when"', async () => {
  const { document } = await setUp('db');
  assert.equal(label(q(document, '[data-curate-fb-submit]')), 'Save rating');
  q(document, '[data-curate-fb-rating]').value = '4';
  q(document, '[data-curate-fb-message]').value = 'Solid.';
  q(document, '[data-curate-fb-submit]').click();
  await wait();
  assert.match(label(q(document, '[data-curate-ratings-status]')), SAVED);
});

test('journal: "Save entry" with the permanence sentence, and the line after reads "saved · who · when"', async () => {
  const { document } = await setUp('db');
  const b = q(document, '#journal-save');
  assert.equal(label(b), 'Save entry');
  assert.match(label(b.parentElement), /saved entries are permanent: no edit, no delete/);
  assert.doesNotMatch(label(b.parentElement), /\bWrite\b|written|writing/i);
  q(document, '#journal-body').value = 'Use the sales schema only.';
  b.click();
  await wait();
  assert.equal(label(q(document, '#journal-save')), 'Save entry', 'the control goes back to its own word');
  assert.match(label(q(document, '[data-journal-note]')), SAVED);
});

test('journal, signed out: the disabled control still says "Save entry" and says why', async () => {
  const { document } = await setUp('db', { signedIn: false });
  const b = q(document, '#journal-save');
  assert.equal(label(b), 'Save entry');
  assert.ok(b.disabled);
  assert.match(label(b.parentElement), /sign in to save — an entry needs an author/);
});

test('group: a choice (the select) stays; the line after reads "saved · who · when" from the route\'s own answer', async () => {
  const { document, server } = await setUp('db');
  q(document, '[data-curate-group-select]').value = 'other';
  q(document, '[data-curate-group-save]').click();
  await wait();
  const [c] = server.calls.filter((x) => x.method === 'POST' && x.url.endsWith('/group'));
  assert.deepEqual(c.body, { resource_type: 'database', group_slug: 'other' });
  assert.match(label(q(document, '[data-curate-findable-status]')), SAVED);
});

test('group, signed out: a 401 says to sign in, never "saved"', async () => {
  const { document, server } = await setUp('db');
  server.signedIn = false;
  q(document, '[data-curate-group-select]').value = 'other';
  q(document, '[data-curate-group-save]').click();
  await wait();
  const line = label(q(document, '[data-curate-findable-status]'));
  assert.match(line, /sign in to change the group/);
  assert.doesNotMatch(line, /^saved/);
});

/* ── Sent to Egeria: Catalog, on a repository's Curate too ───────────────────────────────────── */

test('a repository\'s Curate commit control reads "Publish N items →" (brief 2a) and no page shows both spellings', async () => {
  const { document } = await setUp('repo');
  const btn = host(document).querySelector('[data-curate-go]');
  assert.ok(btn, 'the repository commit control is present');
  assert.match(label(btn), /^Publish( \d+ items?)? →$/);
  assert.doesNotMatch(host(document).textContent, /[Cc]atalogu/);
});

test('no Curate page on a database shows the UK spelling or a verb outside Save / Catalog / Publish on a save or send control', async () => {
  const { document } = await setUp('db');
  assert.doesNotMatch(host(document).textContent, /[Cc]atalogu(e|ed|ing)\b/);
  const controls = [...host(document).querySelectorAll('button')].map(label).filter(Boolean);
  for (const w of controls) assert.doesNotMatch(w, /^(Submit|Write|add|Create|Sync|Send)\b/, `control "${w}"`);
});

/* ── Sent to Egeria: Publish, on the Investigation page ──────────────────────────────────────────── */

async function investigationPage({ bound }) {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  const calls = [];
  const inv = { slug: 'inv1', display_name: 'Inv One', status: 'open', description: 'd', project_classification: 'StudyProject',
    purposes: [], visibility: 'public', ...(bound ? { egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::inv1' } : {}) };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    calls.push(`${method} ${u}`);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.includes('/promote')) return ok({ ok: true, classification_requested: 'StudyProject', classification_confirmed: 'StudyProject',
      members_linked: [], members_unlinkable: [], errors: [] });
    if (u.includes('/sync-egeria')) return ok({});
    if (u.endsWith('/members') || u.includes('/members?')) return ok([]);
    if (u.includes('/dispositions')) return ok({});
    if (u.includes('/next-steps')) return ok({ steps: [], complete: true });
    if (u.endsWith('/purposes')) return ok({ purposes: [] });
    if (u.endsWith('/classifications')) return ok({ classifications: [{ name: 'StudyProject', label: 'Study' }],
      bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' });
    if (u.includes('/api/investigations/inv1')) return ok(inv);
    if (u.includes('/api/investigations/')) return ok([inv]);
    return ok({});
  };
  const app = await import('/static/next/app.js');
  const page = await import('/static/next/stages/investigation.js');
  Object.assign(app.state, { stage: 'investigation', resourceType: 'db', selectedSlug: 'adventureworks', investigation: '', investigations: [inv] });
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div'); rail.id = 'rail-evidence'; document.getElementById('rail').appendChild(rail);
  page.openInvestigationDetail('inv1');
  await page.renderInvestigation();
  await wait(100);
  return { document, calls };
}

test('investigation, not yet in Egeria: the send reads "Publish to Egeria →", and the line after reads published once read back', async () => {
  const { document } = await investigationPage({ bound: false });
  const btn = document.querySelector('[data-act="inv-promote"]');
  assert.equal(label(btn), 'Publish to Egeria →');
  assert.doesNotMatch(document.getElementById('content').textContent, /Create in Egeria/);
  btn.click();
  await wait();
  assert.match(label(document.getElementById('inv-egeria-form')), /^published · read back \d\d:\d\d/);
});

test('investigation, in Egeria: the send reads "Publish again" (RE\'s publish is one-way; "Sync now" promised a two-way reconcile)', async () => {
  const { document } = await investigationPage({ bound: true });
  const btn = document.querySelector('[data-act="inv-sync"]');
  assert.equal(label(btn), 'Publish again');
  assert.doesNotMatch(document.getElementById('content').textContent, /Sync now/);
});

/* ── the source scan: no lower-case or UK stragglers on a save or send control ───────────────────── */

test('source scan: every <button> whose text is a bare "save"/"add"/"submit" in static/next reads Save (or names its object)', () => {
  const root = path.join(NEXT_DIR);
  const offenders = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) { if (ent.name !== 'fonts') walk(full); continue; }
      if (!/\.js$/.test(ent.name)) continue;
      fs.readFileSync(full, 'utf8').split('\n').forEach((line, i) => {
        if (/>(save|Submit|Write|add \+ probe|Sync now|Create in Egeria →)<\/button>/.test(line)) offenders.push(`${path.relative(root, full)}:${i + 1}`);
      });
    }
  };
  walk(root);
  assert.deepEqual(offenders, []);
});
