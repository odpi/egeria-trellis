/** Behaviour: the Curate pane draws the designer's three bands on every kind,
 *  and its controls call the right API shapes.
 *  (REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §1-2;
 *  CURATE-UI-DATABASES-IMPLEMENTED.md.)
 *
 *  Real app.js, real router: the real Curate nav button is clicked, the
 *  stage draws through loadPane -> renderCurate. A stateful stub server
 *  stands behind fetch so every list the UI shows is a re-read of what the
 *  "server" holds after the write -- which is also what lets the
 *  known-negatives make the server drop a write and watch the status words
 *  follow the rows rather than the click.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const UNSIGNED = 'unsigned · from before authors were recorded';

function makeServer(over = {}) {
  const s = {
    calls: [],
    tags: [{ tag: 'sales', created_at: '2026-09-01T10:00:00', author: 'mchessell', authored: true, author_label: 'mchessell' }],
    allTags: [{ tag: 'sales', count: 3 }, { tag: 'postgres', count: 2 }],
    feedback: [
      { id: 'f1', rating: 5, category: 'usefulness', message: 'Good sample.', created_at: '2026-09-30T10:00:00', author: 'mchessell', authored: true, author_label: 'mchessell' },
      { id: 'f2', rating: 5, category: '', message: 'Loved it.', created_at: '2026-09-29T10:00:00', author: 'jdoe', authored: true, author_label: 'jdoe' },
      { id: 'f3', rating: 3, category: '', message: 'Fine.', created_at: '2026-09-28T10:00:00', author: 'jdoe', authored: true, author_label: 'jdoe' },
      { id: 'f4', rating: 2, category: 'documentation', message: 'No column comments.', created_at: '2026-08-14T10:00:00', author: null, authored: false, author_label: UNSIGNED },
    ],
    notes: [
      { id: 'n-legacy', note: 'Old unsigned remark.', created_at: '2026-08-01T10:00:00', author: null, authored: false, author_label: UNSIGNED },
      { id: 'n-signed', note: 'Signed Classic remark.', created_at: '2026-09-20T10:00:00', author: 'dwolfson', authored: true, author_label: 'dwolfson' },
    ],
    journal: [{ id: 'j1', body: 'Use the sales schema only.', author: 'dwolfson', written_at: '2026-09-29T10:00:00', suggested_to: [] }],
    groups: [{ slug: 'sales-platform', display_name: 'Sales platform' }, { slug: 'other', display_name: 'Other' }],
    groupOf: { adventureworks: 'sales-platform' },
    signedIn: true,
    // Brief T: the server's plan for this resource (GET /api/curate/egeria-state), and what a sync does to it.
    egeria: { items: [{ kind: 'tag', name: 'sales', label: 'sales', desired: true, state: 'in_egeria', word: 'in Egeria', reason: '', retry: false }], to_send: 0 },
    egeriaFail: false,
    onSync: null,
    dropWrites: false,   // known-negative: the server accepts a write and persists nothing
    status: {},          // path-substring -> status to answer instead
    ...over,
  };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b, status = 200) => ({ ok: true, status, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    for (const [frag, st] of Object.entries(s.status)) if (u.includes(frag) && method !== 'GET') return err(st, `forced ${st}`);
    if (method === 'GET' && u.includes('/api/curate/feedback-state/')) {
      return s.feedbackState ? ok(s.feedbackState) : err(500, 'feedback state unreadable');
    }
    if (method === 'GET' && u.includes('/api/curate/egeria-state/')) {
      return s.egeriaFail ? err(500, 'state unreadable') : ok(s.egeria);
    }
    if (method === 'POST' && u.includes('/api/curate/egeria-sync/')) {
      if (!s.signedIn) return err(401, 'Sign in');
      if (s.onSync) s.onSync(s, body);
      return ok({ ...s.egeria, queued: [1] });
    }
    const m = u.match(/\/api\/curate\/(tags-detail|tags|feedback|notes)\/(?:[a-z]+)\/([^/?]+)(?:\/([^/?]+))?/);
    if (method === 'GET' && u.endsWith('/api/curate/tags')) return ok(s.allTags);
    if (m) {
      const [, kind, , extra] = m;
      if (method === 'GET') return ok(kind === 'feedback' ? s.feedback : kind === 'notes' ? s.notes : s.tags);
      if (!s.signedIn) return err(401, 'Sign in to do that');
      if (kind === 'tags' && method === 'POST') {
        const t = body.tag.trim().toLowerCase();
        if (!s.dropWrites) s.tags.push({ tag: t, created_at: '2026-10-01T10:00:00', author: 'me', authored: true, author_label: 'me' });
        return ok({ status: 'success', tag: t, author: 'me' });
      }
      if (kind === 'tags' && method === 'DELETE') {
        if (!s.dropWrites) s.tags = s.tags.filter((t) => t.tag !== decodeURIComponent(extra));
        return ok({ status: 'success', removed_by: 'me' });
      }
      if (kind === 'feedback' && method === 'POST') {
        if (!s.dropWrites) s.feedback.push({ id: 'new', ...body, created_at: '2026-10-01T10:00:00', author: 'me', authored: true, author_label: 'me' });
        return ok({ id: 'new' });
      }
    }
    if (method === 'DELETE' && u.includes('/api/curate/notes/')) {
      if (!s.signedIn) return err(401, 'Sign in');
      const id = decodeURIComponent(u.split('/api/curate/notes/')[1]);
      const n = s.notes.find((x) => x.id === id);
      if (n && n.authored) return err(409, 'A signed note cannot be deleted — notes are append-only.');
      if (!s.dropWrites) s.notes = s.notes.filter((x) => x.id !== id);
      return ok({ status: 'success' });
    }
    if (u.includes('/api/journal/')) {
      if (method === 'GET') return ok({ entries: s.journal, suggested_to: [] });
      if (!s.signedIn) return err(401, 'Sign in');
      s.journal.push({ id: 'j-new', body: body.body, author: 'me', written_at: '2026-10-01T10:00:00', suggested_to: body.suggest_to });
      return ok({ id: 'j-new', work_lists: [] });
    }
    if (u.endsWith('/group') && method === 'POST') {
      s.groupOf.adventureworks = body.group_slug;
      return ok({ slug: 'adventureworks', group_slug: body.group_slug });
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
    return ok({});
  };
  return s;
}

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));

async function setUp(resourceType, serverOver = {}, { signedIn = true } = {}) {
  const { document, window } = makeDomEnvironment();
  const server = makeServer({ signedIn, ...serverOver });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = resourceType;
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = 'understanding';
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  app.state.me = signedIn ? { user_id: 'me' } : null;
  app.state.groups = server.groups;
  app.state.databasesLoaded = true;
  app.state.filesystemsLoaded = true;
  app.state.databases = [{ slug: 'adventureworks', group_slug: server.groupOf.adventureworks || '' }];
  app.state.filesystems = [{ slug: 'adventureworks', group_slug: '' }];
  app.state.perspectives = []; app.state.allPerspectives = [];
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, app, server };
}

async function openCurate(document) {
  const btn = document.querySelector('#intent-nav button[data-stage="curate"]');
  assert.ok(btn, 'nav must offer a Curate button');
  btn.click();
  await wait();
}

const host = (d) => d.getElementById('curate-host');
const band = (d, name) => host(d).querySelector(`[data-curate-band="${name}"]`);
const q = (d, sel) => host(d).querySelector(sel);
const calls = (server, method, frag) => server.calls.filter((c) => c.method === method && c.url.includes(frag));

/* ── three bands on a database ─────────────────────────────────────────── */

test('Curate on a database draws the three bands with the real controls', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  assert.ok(host(document), 'Curate must have a host');
  // band 1: Findable
  const f = band(document, 'findable');
  assert.match(f.textContent, /Findable/);
  assert.match(f.textContent, /tags are public in Egeria · the group is a Folio/, 'Brief T: the head says where they go');
  assert.doesNotMatch(f.textContent, /kept in RE \(not published\)/);
  // The head names where tags and the group go; it is not itself a status (the cues are on the items, Brief T).
  const head = f.firstElementChild;
  assert.doesNotMatch(head.textContent, /not in Egeria/, 'the head is not a status');
  assert.equal(head.querySelector('[data-cue]'), null, 'no status cue on the Findable head');
  assert.match(f.textContent, /RE group/, 'RE\'s local group is not mistaken for the Egeria Project binding');
  assert.match(f.textContent, /Sales platform/, 'current group is named');
  assert.ok(f.querySelector('[data-curate-group-select]') && f.querySelector('[data-curate-group-save]'), 'inline group change control');
  assert.ok(f.querySelector('[data-curate-tag="sales"]'), 'tag chip');
  assert.ok(f.querySelector('[data-curate-tag-input]') && f.querySelector('[data-curate-tag-add]'));
  const opts = [...f.querySelectorAll('#curate-tag-datalist option')].map((o) => o.value);
  assert.deepEqual(opts, ['sales', 'postgres'], 'autocomplete is GET /api/curate/tags');
  // the kind's own work (Brief S2): the proposals and rules are on Structure, with the sentence naming what they wait for
  const k = host(document).querySelector('[data-curate-tab-panel="structure"]').textContent;
  assert.match(k, /Glossary terms on tables and columns/);
  assert.match(k, /needs data classes per column \(data_class_match\) and a glossary to match against/);
  assert.match(k, /Logical schema match/);
  assert.match(k, /needs the logical schemas Egeria knows to be read/);
  assert.match(k, /none detected for databases yet/, 'dependencies: present and explained');
  assert.equal(host(document).querySelectorAll('table').length, 0, 'never an empty table');
  // What people say
  const p = band(document, 'people');
  assert.match(p.textContent, /What people say/);
  assert.ok(p.querySelector('[data-curate-fb-rating]') && p.querySelector('[data-curate-fb-submit]'));
  assert.ok(p.querySelector('#journal-save') && p.querySelector('#journal-body'), 'notes band is the journal');
  // order on Describe: findable, people, the declared measures' publish table (declared extensibility slice 1, §5),
  // then Publish to Egeria LAST (Brief S2, Brief F); the scope is on Contents
  const order = [...host(document).querySelector('[data-curate-tab-panel="describe"]').querySelectorAll('[data-curate-band]')].map((e) => e.dataset.curateBand);
  assert.deepEqual(order, ['findable', 'people', 'measures', 'publish']);
  assert.ok(host(document).querySelector('[data-curate-tab-panel="contents"] [data-curate-scope]'), 'the scope tree is on Contents');
  // the false sentences are gone and no repo-only call fired
  assert.doesNotMatch(host(document).textContent, /reachable from the resource header|offers none of them|Curate isn't available/);
  assert.equal(server.calls.filter((c) => c.url.includes('/curate/plan')).length, 0);
});

/* ── tags ──────────────────────────────────────────────────────────────── */

test('adding a tag POSTs {tag} and the chip comes from the re-read list', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  const input = q(document, '[data-curate-tag-input]');
  input.value = 'Sample-Data';
  q(document, '[data-curate-tag-add]').click();
  await wait();
  const [c] = calls(server, 'POST', '/api/curate/tags/database/adventureworks');
  assert.ok(c, 'tag add must POST /api/curate/tags/database/adventureworks');
  assert.deepEqual(c.body, { tag: 'Sample-Data' });
  assert.ok(q(document, '[data-curate-tag="sample-data"]'), 'chip drawn from the re-read (server lower-cases)');
  assert.match(q(document, '[data-curate-findable-status]').textContent, /“sample-data” is on the list/);
});

test('removing a tag DELETEs /tags/{type}/{slug}/{tag}', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  q(document, '[data-curate-tag-remove="sales"]').click();
  await wait();
  assert.equal(calls(server, 'DELETE', '/api/curate/tags/database/adventureworks/sales').length, 1);
  assert.equal(q(document, '[data-curate-tag="sales"]'), null);
  assert.match(q(document, '[data-curate-findable-status]').textContent, /off the list/);
});

test('KNOWN-NEGATIVE: a server that drops the write does not get an "added" status', async () => {
  const { document } = await setUp('db', { dropWrites: true });
  await openCurate(document);
  q(document, '[data-curate-tag-input]').value = 'ghost';
  q(document, '[data-curate-tag-add]').click();
  await wait();
  assert.equal(q(document, '[data-curate-tag="ghost"]'), null);
  const s = q(document, '[data-curate-findable-status]').textContent;
  assert.match(s, /not in the re-read list/);
  assert.doesNotMatch(s, /is on the list/);
});

test('a 401 from the tag route says to sign in, in words', async () => {
  const { document } = await setUp('db', { status: { '/api/curate/tags/': 401 } });
  await openCurate(document);
  q(document, '[data-curate-tag-input]').value = 'x';
  q(document, '[data-curate-tag-add]').click();
  await wait();
  assert.match(q(document, '[data-curate-findable-status]').textContent, /sign in to add the tag/);
});

/* ── group ─────────────────────────────────────────────────────────────── */

test('changing the group POSTs resource_type database and the new group', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  const sel = q(document, '[data-curate-group-select]');
  sel.value = 'other';
  q(document, '[data-curate-group-save]').click();
  await wait();
  const [c] = calls(server, 'POST', '/api/projects/adventureworks/group');
  assert.ok(c);
  assert.deepEqual(c.body, { resource_type: 'database', group_slug: 'other' });
  assert.match(q(document, '[data-curate-group-now]').textContent, /Other/);
  assert.match(q(document, '[data-curate-findable-status]').textContent, /group is now Other/);
});

/* ── ratings ───────────────────────────────────────────────────────────── */

test('ratings are counts with no average, each entry signed and dated; unsigned shows the label', async () => {
  const { document } = await setUp('db');
  await openCurate(document);
  const h = q(document, '[data-curate-ratings-headline]').textContent;
  // Addendum C: one rating per person counts (jdoe's older ★★★ is behind "earlier ratings ▸")
  assert.match(h, /3 · ★★★★★ 2 · ★★ 1/);
  const all = band(document, 'people').textContent;
  assert.doesNotMatch(all, /average|avg|mean|\d\.\d\s*(\/|out of)\s*5/i, 'never an average or a score');
  const entries = [...host(document).querySelectorAll('[data-curate-feedback]')].map((e) => e.textContent);
  assert.equal(entries.length, 4, 'every row is still drawn: three current, one under earlier ratings');
  assert.equal(q(document, '[data-curate-earlier-ratings]').querySelectorAll('[data-curate-feedback]').length, 1);
  assert.equal(q(document, '[data-curate-earlier-ratings]').open, false, 'earlier ratings are closed by default');
  assert.ok(entries.some((t) => /mchessell · 2026-09-30/.test(t)), 'signed and dated');
  assert.ok(entries.some((t) => t.includes(UNSIGNED)), 'legacy entry says it is unsigned');
});

test('KNOWN-NEGATIVE: a legacy row with no author_label from the server still shows the unsigned words', async () => {
  const { document } = await setUp('db', {
    feedback: [{ id: 'x', rating: 4, category: '', message: 'old', created_at: '2026-01-01T00:00:00', author: null }],
  });
  await openCurate(document);
  assert.match(q(document, '[data-curate-feedback]').textContent, /unsigned · from before authors were recorded/);
});

test('submitting a rating POSTs {rating, category, message}; list comes from the re-read', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  q(document, '[data-curate-fb-rating]').value = '4';
  q(document, '[data-curate-fb-category]').value = 'quality';
  q(document, '[data-curate-fb-message]').value = 'Solid.';
  q(document, '[data-curate-fb-submit]').click();
  await wait();
  const [c] = calls(server, 'POST', '/api/curate/feedback/database/adventureworks');
  assert.ok(c);
  assert.deepEqual(c.body, { rating: 4, category: 'quality', message: 'Solid.' });
  assert.match(q(document, '[data-curate-ratings-headline]').textContent, /4 · ★★★★★ 2 · ★★★★ 1 · ★★ 1/);
  assert.match(q(document, '[data-curate-ratings-status]').textContent, /saved · will publish/);
});

test('a message is required: no POST without one', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  q(document, '[data-curate-fb-submit]').click();
  await wait(50);
  assert.equal(calls(server, 'POST', '/api/curate/feedback').length, 0);
  assert.match(q(document, '[data-curate-ratings-status]').textContent, /feedback needs one/);
});

/* ── notes: the journal, and Classic's notes read-only beneath ─────────── */

test('a journal entry POSTs {body, suggest_to} to /api/journal/database/{slug} and appears', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  assert.match(q(document, '#journal-entries').textContent, /Use the sales schema only/);
  q(document, '#journal-body').value = 'Archive-shaped production.* tables.';
  q(document, '#journal-save').click();
  await wait();
  const [c] = calls(server, 'POST', '/api/journal/database/adventureworks');
  assert.ok(c);
  assert.deepEqual(c.body, { body: 'Archive-shaped production.* tables.', suggest_to: [] });
  assert.match(q(document, '#journal-entries').textContent, /Archive-shaped production/);
});

test('Classic notes: legacy unsigned note has the label and a delete; a signed note has no delete', async () => {
  const { document, server } = await setUp('db');
  await openCurate(document);
  const legacy = q(document, '[data-curate-classic-note="n-legacy"]');
  const signed = q(document, '[data-curate-classic-note="n-signed"]');
  assert.match(legacy.textContent, /unsigned · from before authors were recorded/);
  assert.ok(legacy.querySelector('[data-curate-note-delete]'), 'unsigned note is deletable');
  assert.match(signed.textContent, /dwolfson/);
  assert.equal(signed.querySelector('[data-curate-note-delete]'), null, 'a signed note has no delete control');
  assert.match(band(document, 'people').textContent, /Curator notes from Classic · 2 · 1 unsigned/);
  // newest first
  const ids = [...host(document).querySelectorAll('[data-curate-classic-note]')].map((e) => e.dataset.curateClassicNote);
  assert.deepEqual(ids, ['n-signed', 'n-legacy']);
  // delete asks first, then DELETEs, then re-reads
  legacy.querySelector('[data-curate-note-delete]').click();
  assert.equal(calls(server, 'DELETE', '/api/curate/notes/').length, 0, 'asks before deleting');
  legacy.querySelector('[data-yes]').click();
  await wait();
  assert.equal(calls(server, 'DELETE', '/api/curate/notes/n-legacy').length, 1);
  assert.equal(q(document, '[data-curate-classic-note="n-legacy"]'), null);
  assert.match(q(document, '[data-curate-notes-status]').textContent, /note deleted/);
});

test('there is no edit control for a journal entry or any note', async () => {
  const { document } = await setUp('db');
  await openCurate(document);
  const txt = band(document, 'people').innerHTML;
  assert.doesNotMatch(txt, /data-[a-z-]*(edit|amend)|>\s*edit\s*</i);
});

/* ── signed out ────────────────────────────────────────────────────────── */

test('signed out: every write control is disabled and says why', async () => {
  const { document, server } = await setUp('db', {}, { signedIn: false });
  await openCurate(document);
  const disabled = (sel) => {
    const els = [...host(document).querySelectorAll(sel)];
    assert.ok(els.length, `control ${sel} must be present`);
    for (const e of els) assert.ok(e.disabled, `${sel} must be disabled when signed out`);
  };
  disabled('[data-curate-tag-input]');
  disabled('[data-curate-tag-add]');
  disabled('[data-curate-tag-remove]');
  disabled('[data-curate-fb-rating]');
  disabled('[data-curate-fb-category]');
  disabled('[data-curate-fb-message]');
  disabled('[data-curate-fb-submit]');
  disabled('[data-curate-note-delete]');
  disabled('#journal-save');
  const text = host(document).textContent;
  assert.match(q(document, '[data-curate-findable-status]').textContent, /sign in to add or remove a tag — it needs an author/);
  assert.match(q(document, '[data-curate-ratings-status]').textContent, /sign in to rate or comment — it needs an author/);
  assert.match(text, /sign in to save — an entry needs an author/);
  q(document, '[data-curate-tag-add]').click();
  q(document, '#journal-save').click();
  await wait(50);
  assert.equal(server.calls.filter((c) => c.method !== 'GET').length, 0, 'no write is sent while signed out');
});

/* ── file system, repository ────────────────────────────────────────────── */

test('a file system gets the one honest sentence and the shared bands, no kind sections', async () => {
  const { document } = await setUp('filesystem');
  await openCurate(document);
  assert.match(band(document, 'kind').textContent,
    /Nothing to review for file systems yet\. Group, tags, ratings and notes above and below apply\./);
  assert.doesNotMatch(band(document, 'kind').textContent, /Glossary terms|Logical schema match/);
  assert.ok(band(document, 'findable').querySelector('[data-curate-tag-input]'));
  assert.ok(band(document, 'people').querySelector('[data-curate-fb-submit]'));
});

test('a database does not get the file-share sentence', async () => {
  const { document } = await setUp('db');
  await openCurate(document);
  assert.doesNotMatch(host(document).textContent, /Nothing to review for file systems/);
});

test('a repository keeps its plan view across its tabs, with the bands around it', async () => {
  const { document, server } = await setUp('repo');
  await openCurate(document);
  // The plan's pieces are on three tabs now: the header line on Describe, the sections on Contents and Structure.
  const panel = (n) => document.querySelector(`[data-curate-tab-panel="${n}"]`);
  assert.match(panel('describe').textContent, /disposition/, 'the plan header line renders on Describe and publish');
  assert.ok(panel('contents').querySelector('#curate-sec-what-holds') && panel('structure').querySelector('#curate-sec-made-of'));
  assert.doesNotMatch(document.getElementById('curate-host').textContent, /plan loading/);
  assert.ok(server.calls.some((c) => c.url.includes('/curate/plan')));
  assert.ok(band(document, 'findable').querySelector('[data-curate-tag-input]'));
  assert.ok(band(document, 'people').querySelector('[data-curate-fb-submit]'));
  assert.ok(calls(server, 'GET', '/api/curate/tags-detail/repo/adventureworks').length >= 1, 'repo tags use entity type repo');
  assert.equal(document.querySelector('[data-curate-work]'), null, 'no database sections on a repo');
});

/* ── a band that cannot read says so ───────────────────────────────────── */

test('a band whose read fails shows a visible message, not nothing', async () => {
  const { document, server } = await setUp('db');
  const real = globalThis.fetch;
  globalThis.fetch = async (url, o) => {
    if (String(url).includes('/api/curate/feedback/')) return { ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'boom' }) };
    return real(url, o);
  };
  await openCurate(document);
  assert.match(band(document, 'people').textContent, /Ratings could not be read: boom/);
  assert.ok(band(document, 'findable').querySelector('[data-curate-tag-input]'), 'other bands still draw');
  void server;
});

/* ── Brief T: each tag and the group carry their Egeria state, from the server's plan ───────────────── */

const egeriaItem = (kind, name, st, extra = {}) => ({
  kind, name, label: extra.label || name, desired: extra.desired !== false, state: st,
  // Brief F: the server's words for the single Publish
  word: st === 'failed' ? (extra.reason ? `not sent · ${extra.reason}` : 'not sent')
    : { in_egeria: 'published 10-11', not_yet: 'saved · will publish', not_sent: 'saved · will publish', pending: 'sending',
      retrying: 'sending · retrying', unlinked: 'unlinked' }[st],
  reason: extra.reason || '', retry: !!extra.retry, action: extra.action || '',
});

test('Brief T: a tag chip carries its Egeria state as a glyph cue plus the word, from the plan', async () => {
  const { document } = await setUp('db', { egeria: { items: [egeriaItem('tag', 'sales', 'in_egeria')], to_send: 0 } });
  await openCurate(document);
  const cue = q(document, '[data-curate-tag-state="sales"]');
  assert.ok(cue, 'the chip has a state cue');
  assert.equal(cue.dataset.egeriaState, 'in_egeria');
  assert.equal(cue.dataset.cue, 'catalogued');
  assert.match(cue.className, /text-state-ok/);
  assert.ok(cue.querySelector('.font-glyph'), 'a glyph, not only a word');
  assert.match(cue.textContent, /published 10-11/);
});

test('Brief F: a resource not in Egeria says "saved · will publish" on the chip (Publish creates it, then sends)', async () => {
  const { document } = await setUp('db', { egeria: { items: [egeriaItem('tag', 'sales', 'not_yet')], to_send: 0 } });
  await openCurate(document);
  assert.match(q(document, '[data-curate-tag-state="sales"]').textContent, /saved · will publish/);
  assert.equal(q(document, '[data-curate-tag-state="sales"]').dataset.cue, 'stored');
  assert.equal(q(document, '[data-curate-egeria-send]'), null, 'nothing to send');
});

test('Brief T: a failed unlink shows "failed · reason" with retry; retry POSTs {retry:{kind,name}} and the words come from the re-read', async () => {
  const items = [egeriaItem('tag', 'old', 'failed', { desired: false, retry: true, reason: 'not retried: destructive write · OMAG-500 busy' })];
  const { document, server } = await setUp('db', {
    tags: [],
    egeria: { items, to_send: 0 },
    onSync: (srv) => { srv.egeria = { items: [egeriaItem('tag', 'old', 'unlinked', { desired: false })], to_send: 0 }; },
  });
  await openCurate(document);
  const removed = q(document, '[data-curate-removed-tag="old"]');
  assert.ok(removed, 'a removed tag Egeria still has is listed');
  assert.match(removed.textContent, /not sent · not retried: destructive write · OMAG-500 busy/);
  assert.equal(q(document, '[data-curate-tag-state="old"]').dataset.cue, 'partial');
  removed.querySelector('[data-curate-egeria-retry]').click();
  await wait();
  const [c] = calls(server, 'POST', '/api/curate/egeria-sync/database/adventureworks');
  assert.ok(c, 'retry POSTs the sync route');
  assert.deepEqual(c.body, { retry: { kind: 'tag', name: 'old' } });
  assert.match(q(document, '[data-curate-tag-state="old"]').textContent, /unlinked/);
  assert.match(q(document, '[data-curate-findable-status]').textContent, /retried tag “old” · Egeria: unlinked/);
});

test('KNOWN-NEGATIVE Brief T: a retry the server did not carry out does not say unlinked', async () => {
  const items = [egeriaItem('tag', 'old', 'failed', { desired: false, retry: true, reason: 'busy' })];
  const { document } = await setUp('db', { tags: [], egeria: { items, to_send: 0 } });   // onSync changes nothing
  await openCurate(document);
  q(document, '[data-curate-egeria-retry]').click();
  await wait();
  assert.doesNotMatch(q(document, '[data-curate-findable-status]').textContent, /unlinked/);
  assert.match(q(document, '[data-curate-tag-state="old"]').textContent, /not sent · busy/);
});

test('Brief T: the group line carries the Folio state; a group left behind shows its unlink state', async () => {
  const { document } = await setUp('db', { egeria: { items: [
    egeriaItem('tag', 'sales', 'in_egeria'),
    egeriaItem('group', 'sales-platform', 'pending', { label: 'Sales platform' }),
    egeriaItem('group', 'other', 'unlinked', { desired: false, label: 'Other' }),
  ], to_send: 0 } });
  await openCurate(document);
  const g = q(document, '[data-curate-group-state]');
  assert.ok(g && g.dataset.egeriaState === 'pending');
  assert.match(g.textContent, /sending/);
  const left = q(document, '[data-curate-left-group="other"]');
  assert.ok(left);
  assert.match(left.textContent, /Other/);
  assert.match(left.textContent, /unlinked/);
});

test('Brief F: a saved tag reads "saved · will publish" and Findable has NO send control (the page\'s Publish sends)', async () => {
  const { document, server } = await setUp('db', {
    egeria: { items: [egeriaItem('tag', 'sales', 'not_sent', { action: 'link' })], to_send: 1 },
  });
  await openCurate(document);
  assert.match(q(document, '[data-curate-tag-state="sales"]').textContent, /saved · will publish/);
  assert.equal(q(document, '[data-curate-egeria-send]'), null, 'no per-section send');
  assert.match(band(document, 'findable').textContent, /sent with Publish/);
  assert.equal(calls(server, 'POST', '/api/curate/egeria-sync/').length, 0);
});

test('KNOWN-NEGATIVE Brief T: an unreadable state says so on every chip, never draws "in Egeria" or nothing', async () => {
  const { document } = await setUp('db', { egeriaFail: true });
  await openCurate(document);
  const cue = q(document, '[data-curate-tag-state="sales"]');
  assert.ok(cue);
  assert.match(cue.textContent, /could not read/);
  assert.doesNotMatch(band(document, 'findable').textContent, /in Egeria(?! ·)/);
  assert.match(q(document, '[data-curate-egeria-unread]').textContent, /Egeria state could not be read/);
});

test('Brief T: adding a tag says its Egeria state from the re-read', async () => {
  const { document } = await setUp('db', {
    egeria: { items: [egeriaItem('tag', 'sales', 'in_egeria'), egeriaItem('tag', 'pii', 'pending')], to_send: 0 },
  });
  await openCurate(document);
  q(document, '[data-curate-tag-input]').value = 'pii';
  q(document, '[data-curate-tag-add]').click();
  await wait();
  assert.match(q(document, '[data-curate-findable-status]').textContent, /“pii” is on the list · Egeria: sending/);
  assert.match(q(document, '[data-curate-tag-state="pii"]').textContent, /sending/);
});

/* ── Brief F: ratings and notes carry their Egeria state; one rating per person ──────────────────────── */

const fbItem = (kind, id, author, st, word, extra = {}) => ({ kind, id, author, mine: author === 'me', state: st, word, reason: '', action: '', ...extra });
const FEEDBACK_STATE = {
  ratings: [
    fbItem('rating', 'f1', 'mchessell', 'published', 'published 10-11'),
    fbItem('rating', 'f2', 'jdoe', 'waiting', "jdoe's · waiting for them"),
    fbItem('rating', 'f3', 'jdoe', 'replaced', 'replaced by a newer rating'),
    fbItem('rating', 'f4', '', 'kept', 'kept in RE · unsigned'),
  ],
  notes: [fbItem('note', 'j1', 'dwolfson', 'not_sent', 'not sent · OMAG-500 busy')],
  to_send: 0, waiting_for_others: 1,
};

test('Brief F: each rating shows its Egeria state from the server plan; a replaced one sits under "earlier ratings ▸"', async () => {
  const { document } = await setUp('db', { feedbackState: FEEDBACK_STATE });
  await openCurate(document);
  const cueOf = (id) => [...host(document).querySelectorAll('[data-curate-feedback]')].find((e) => e.dataset.curateFeedback === id)
    .querySelector('[data-curate-feedback-cue]');
  assert.match(cueOf('f1').textContent, /published 10-11/);
  assert.equal(cueOf('f1').dataset.cue, 'catalogued');
  assert.match(cueOf('f2').textContent, /jdoe's · waiting for them/);
  const earlier = q(document, '[data-curate-earlier-ratings]');
  assert.ok(earlier && /earlier ratings ▸/.test(earlier.querySelector('summary').textContent));
  assert.equal(earlier.querySelector('[data-curate-feedback]').dataset.curateFeedback, 'f3');
  assert.match(q(document, '[data-curate-ratings-headline]').textContent, /3 · ★★★★★ 2 · ★★ 1 · one per person/);
});

test('Brief F: each journal entry shows its Note state; a failed one says "not sent · <reason>" with the partial cue', async () => {
  const { document } = await setUp('db', { feedbackState: FEEDBACK_STATE });
  await openCurate(document);
  await wait();
  const cue = q(document, '[data-journal-cue="j1"] [data-curate-note-cue]');
  assert.ok(cue, 'the entry carries a cue');
  assert.match(cue.textContent, /not sent · OMAG-500 busy/);
  assert.equal(cue.dataset.cue, 'partial');
});

test('KNOWN-NEGATIVE Brief F: an unreadable feedback state never draws "published" on an entry', async () => {
  const { document } = await setUp('db');                  // feedback-state answers 500
  await openCurate(document);
  await wait();
  assert.doesNotMatch(band(document, 'people').textContent, /published/);
  assert.match(q(document, '[data-journal-cue="j1"]').textContent, /could not be read/);
});
