/** Behaviour: where a resource's hide, remove and disposition controls live.
 *
 *  Slice of REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md (wireframe
 *  ResourceControls.dc.html, canvas page 17). The owner looked for "remove" in
 *  the top bar (the resource's name is drawn like a control), then in the left
 *  pane, and never in the content header where it was. Source-text pins have let
 *  wrong or blank panes through many times, so every test here runs the REAL
 *  app.js and the REAL router against the REAL index.html body (the top bar is
 *  parsed from the shipped file, not re-typed), clicks real buttons, and reads
 *  the DOM. Only the network (fetch) is stubbed.
 *
 *  Covered: the menu opens from the top-bar name on EVERY stage for a repo, a
 *  database and a file system; Remove opens a panel under the bar (never in the
 *  content pane's slot) whose commit button names the object and is its only
 *  accent; the content header lost hide/remove and kept the pill; Select mode's
 *  three doors; "select these N"; the bar's grouping; one word ("Remove", never
 *  "delete") on screen and in the source; Find by kind; and that the earlier
 *  behaviours (per-kind removal routes, list refresh, stale-rail clear, open
 *  investigation refresh, hide/unhide route) did not move.
 */
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
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const STAGES = ['investigation', 'scouting', 'discovery', 'assessment', 'analysis', 'curate', 'enrichment', 'understanding', 'automate'];

const row = (slug, over = {}) => ({
  slug, display_name: slug, disposition: 'undecided', group_slug: '', working_set_hidden: false,
  credential_capability: null, ...over,
});

/** A recording backend. DELETE/POST/PUT are recorded and answered 200, except a
 *  path listed in `refuse` (answered 500). Investigation members are mutable so
 *  the open-investigation refresh can be observed. */
function makeBackend({ refuse = [], members = { c360: [] } } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (refuse.some((r) => u.includes(r))) {
      return { ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'boom' }) };
    }
    if (method === 'GET' && u.startsWith('/api/investigations/?')) return ok([{ slug: 'c360', display_name: 'Customer 360', status: 'open' }]);
    let m = u.match(/^\/api\/investigations\/([^/?]+)\/members$/);
    if (m) {
      if (method === 'GET') return ok([...(members[m[1]] || [])]);
      if (method === 'POST') { (members[m[1]] = members[m[1]] || []).push({ entity_type: body.entity_type, entity_slug: body.entity_slug }); return ok([]); }
    }
    m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m && method === 'GET' && !['purposes', 'classifications'].includes(m[1])) {
      return ok({ slug: m[1], display_name: 'Customer 360', status: 'open', description: 'd', project_classification: 'StudyProject', purposes: [], visibility: 'public' });
    }
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [], default_classification: 'StudyProject', default_binding: 'egeria' });
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u === '/api/db-servers/') return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules') || u.includes('/groups')) return ok([]);
    return ok({});
  };
  return calls;
}

const LIST_KEY = { repo: 'projects', db: 'databases', filesystem: 'filesystems' };
const LOADED_KEY = { db: 'databasesLoaded', filesystem: 'filesystemsLoaded' };

async function setUp(kind = 'db', { slugs = ['alpha', 'beta', 'gamma'], rows = null, stage = 'discovery', backend = {} } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(backend);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  autoAnswerConfirm(document, { answer: true });   // the in-page confirm (confirm-dialog.js), answered yes
  // The shipped page: its real top bar, nav, sidebar and content pane.
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.getElementById('rail').appendChild(rail);
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = kind;
  s.projects = []; s.databases = []; s.filesystems = [];
  s[LIST_KEY[kind]] = rows || slugs.map((x) => row(x, kind === 'repo' ? { github_url: `https://github.com/o/${x}` } : {}));
  if (LOADED_KEY[kind]) s[LOADED_KEY[kind]] = true;
  s.groups = [];
  s.investigations = []; s.investigation = ''; s.workingSet = new Set();
  s.selected = new Set(); s.selectMode = false; s.dispositionFacet = 'all'; s.filter = ''; s.showHidden = false; s.scope = '';
  s.workListSlug = null; s.workListIndex = false; s.workLists = [];
  s.selectedSlug = (rows ? rows[0].slug : slugs[0]);
  s.stage = stage; s.subTab = 'questions';
  s.answers = new Map();
  app.renderIntentNav();
  app.renderSidebar();
  app.renderTopBar();
  document.querySelector(`#intent-nav button[data-stage="${stage}"]`).click();
  await tick(150);
  return { document, window, app, calls, s };
}

const $ = (d, sel) => d.querySelector(sel);
const slugBtn = (d) => $(d, '#scope-slug');
const menu = (d) => $(d, '#resource-menu');
const panel = (d) => $(d, '#resource-controls-panel');
const hasAccent = (el) => /(^|[\s:])(border|text|bg)-accent/.test(el.getAttribute('class') || '');
/** Elements inside `root` whose class list names the accent colour. */
const accents = (root) => [...root.querySelectorAll('*')].filter(hasAccent);

async function openMenu(d) {
  slugBtn(d).click();
  await tick(5);
  const m = menu(d);
  assert.ok(m, 'clicking the top-bar name must open the resource menu');
  return m;
}

/* ── 1. the menu on the top-bar name ─────────────────────────────────────── */

for (const kind of ['repo', 'db', 'filesystem']) {
  test(`the top-bar name is a button, "<slug> ▾", and its menu opens on EVERY stage (${kind})`, async () => {
    const ctx = await setUp(kind);
    const { document: d } = ctx;
    const b = slugBtn(d);
    assert.equal(b.tagName, 'BUTTON');
    assert.equal(text(b), 'alpha ▾');
    assert.equal(b.dataset.slug, 'alpha', 'the bare slug is still readable (feedback.js reads it)');
    const stagesSeen = [];
    for (const st of STAGES) {
      const nav = $(d, `#intent-nav button[data-stage="${st}"]`);
      if (!nav) continue;
      nav.click();
      await tick(100);
      const m = await openMenu(d);
      stagesSeen.push(st);
      const items = [...m.querySelectorAll('[role="menuitem"]')].map((x) => x.dataset.act);
      assert.deepEqual(items, ['menu-hide', 'menu-remove'], `${st}: hide first, remove last`);
      assert.match(text(m.querySelector('[data-act="menu-hide"]')), /^Hide from my list\b.*view preference/);
      assert.match(text(m.querySelector('[data-act="menu-remove"]')), /^Remove from Resource Explorer…$/);
      // A rule sits between them, and Remove is last, in ink (not accent).
      const kids = [...m.children];
      assert.equal(kids[1].getAttribute('role'), 'separator');
      assert.equal(kids[kids.length - 1].dataset.act, 'menu-remove');
      assert.equal(accents(m).length, 0, `${st}: the menu carries no accent`);
      d.dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      assert.equal(menu(d), null, `${st}: Escape closes it`);
    }
    assert.ok(stagesSeen.length >= 8, `walked ${stagesSeen.length} stages: ${stagesSeen}`);
  });
}

test('a hidden resource\'s menu reads "Unhide"', async () => {
  const ctx = await setUp('db', { rows: [row('alpha', { working_set_hidden: true })] });
  const m = await openMenu(ctx.document);
  assert.match(text(m.querySelector('[data-act="menu-hide"]')), /^Unhide\b/);
});

test('clicking elsewhere closes the menu; clicking the name again toggles it', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  await openMenu(d);
  slugBtn(d).click(); await tick(5);
  assert.equal(menu(d), null, 'the name toggles');
  await openMenu(d);
  $(d, '#content').click(); await tick(5);
  assert.equal(menu(d), null, 'an outside click closes');
});

test('no resource selected: the name is a disabled button with no menu', async () => {
  const ctx = await setUp('db');
  ctx.s.selectedSlug = null;
  ctx.app.renderTopBar();
  assert.equal(slugBtn(ctx.document).disabled, true);
  assert.equal(text(slugBtn(ctx.document)), 'no resource selected');
  slugBtn(ctx.document).click(); await tick(5);
  assert.equal(menu(ctx.document), null);
});

/* ── 2. Remove: the panel under the bar ──────────────────────────────────── */

const ROUTE = { repo: ['DELETE', '/api/projects/alpha'], db: ['DELETE', '/api/databases/alpha'], filesystem: ['DELETE', '/api/filesystems/alpha/'] };
const NOUN = { repo: 'repo', db: 'database', filesystem: 'file system' };

for (const kind of ['repo', 'db', 'filesystem']) {
  test(`Remove (${kind}): a panel directly under the top bar, the commit button names the object and is the only accent`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    const m = await openMenu(d);
    m.querySelector('[data-act="menu-remove"]').click();
    await tick(5);
    const p = panel(d);
    assert.ok(p, 'a panel');
    assert.equal(menu(d), null, 'the menu closes when Remove is chosen');
    // Under the bar: it is the header's next sibling, and nowhere in the content pane.
    assert.equal(p.previousElementSibling, $(d, 'header'), 'directly under the top bar');
    assert.ok(!$(d, '#content').contains(p), 'not inside the content pane');
    assert.equal(text($(d, '#resource-action') || d.createElement('i')), '', 'the content pane\'s slot is untouched');
    // The commit button names the kind and the resource.
    const commit = p.querySelector('[data-act="remove-confirm"]');
    assert.equal(text(commit), `Remove the ${NOUN[kind]} alpha`);
    // The only accent in the panel is that button.
    assert.deepEqual(accents(p), [commit]);
    // The warning sentence is ink, not accent, and says it in words.
    const sentence = p.querySelector('[data-remove-sentence]');
    assert.ok(sentence && /(^|\s)text-ink(\s|$)/.test(sentence.className), 'ink');
    assert.equal(accents(sentence).length, 0);
    assert.match(text(sentence), /This cannot be undone/);
    assert.match(text(sentence), /Not touched:/);
    assert.doesNotMatch(text(p), /file share/i);
    assert.equal(ctx.calls.filter((c) => c.method === 'DELETE').length, 0, 'nothing is sent until the commit');
  });
}

for (const kind of ['repo', 'db', 'filesystem']) {
  test(`Remove (${kind}) commits to the kind's own route, prunes that list, moves the selection and shows the outcome`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
    panel(d).querySelector('[data-act="remove-confirm"]').click();
    await tick(200);
    const [method, url] = ROUTE[kind];
    assert.deepEqual(ctx.calls.filter((c) => c.method === 'DELETE').map((c) => `${c.method} ${c.url}`), [`${method} ${url}`]);
    assert.deepEqual(ctx.s[LIST_KEY[kind]].map((r) => r.slug), ['beta', 'gamma']);
    assert.equal(ctx.s.selectedSlug, 'beta');
    assert.equal(text(slugBtn(d)), 'beta ▾', 'the top bar follows the selection');
    assert.equal($(d, '#sidebar button[data-slug="alpha"]'), null, 'gone from the sidebar');
    const p = panel(d);
    assert.match(text(p), new RegExp(`^Removed the ${NOUN[kind]} alpha from Resource Explorer\\.`));
    assert.equal(p.dataset.slug, 'beta', 'stamped with the resource now on screen');
  });
}

test('Remove: a refused removal is said, and nothing is pruned', async () => {
  const ctx = await setUp('db', { backend: { refuse: ['/api/databases/alpha'] } });
  const d = ctx.document;
  (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
  panel(d).querySelector('[data-act="remove-confirm"]').click();
  await tick(100);
  assert.match(text(panel(d)), /Not removed: /);
  assert.equal(ctx.s.databases.length, 3);
  assert.equal(ctx.s.selectedSlug, 'alpha');
});

test('Remove: Cancel closes the panel and sends nothing; moving to another resource drops a confirmation naming the first', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
  panel(d).querySelector('[data-act="remove-cancel"]').click();
  assert.equal(panel(d), null);
  (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
  assert.match(text(panel(d)), /alpha/);
  $(d, '#sidebar button[data-slug="beta"]').click();
  await tick(100);
  assert.equal(panel(d), null, 'a confirmation about alpha must not outlive the switch to beta');
  assert.equal(ctx.calls.filter((c) => c.method === 'DELETE').length, 0);
});

test('Remove: the stale-rail clear still fires (the rail does not keep evidence about the removed resource)', async () => {
  const ctx = await setUp('db', { stage: 'discovery' });
  const d = ctx.document;
  await ctx.app.openMembers({ slug: 'alpha', analysisId: 'a1', title: 'A1' }).catch(() => {});
  const rail = $(d, '#rail-evidence');
  rail.innerHTML = '<p>evidence about alpha</p>';
  rail.dataset.railFor = 'alpha';
  rail.dataset.railKey = 'db|alpha|discovery|questions|';
  (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
  panel(d).querySelector('[data-act="remove-confirm"]').click();
  await tick(200);
  assert.ok(!rail.textContent.includes('alpha'), `rail still names alpha: ${rail.textContent}`);
});

/* ── 3. hide / unhide still hit the same route ──────────────────────────── */

for (const kind of ['repo', 'db', 'filesystem']) {
  test(`Hide then Unhide (${kind}) from the menu call the same working-set route with the kind's entity type`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    (await openMenu(d)).querySelector('[data-act="menu-hide"]').click();
    await tick(200);
    const posts = () => ctx.calls.filter((c) => c.method === 'POST' && c.url === '/api/discovery/working-set');
    const et = { repo: 'repo', db: 'database', filesystem: 'filesystem' }[kind];
    assert.deepEqual(posts().map((c) => c.body), [{ entity_type: et, entity_slug: 'alpha', hidden: true }]);
    assert.equal(ctx.s[LIST_KEY[kind]][0].working_set_hidden, true);
    assert.match(text(panel(d)), /^Hidden from your list\. Still registered/);
    assert.ok(!$(d, '#content').contains(panel(d)));
    // Hidden rows leave the list; the menu now offers the way back.
    (await openMenu(d)).querySelector('[data-act="menu-hide"]').click();
    await tick(200);
    assert.deepEqual(posts().map((c) => c.body.hidden), [true, false]);
    assert.match(text(panel(d)), /^Back in your list\./);
  });
}

/* ── 4. the content header ───────────────────────────────────────────────── */

test('the content header lost hide and remove and kept name, provenance and the disposition pill', async () => {
  for (const kind of ['repo', 'db', 'filesystem']) {
    const ctx = await setUp(kind);
    const html = ctx.app.resourceHeaderHtml('alpha');
    const host = ctx.document.createElement('div');
    host.innerHTML = html;
    assert.equal(host.querySelector('[data-act="hide"]'), null, `${kind}: no hide`);
    assert.equal(host.querySelector('[data-act="remove"]'), null, `${kind}: no remove`);
    assert.equal(host.querySelector('[data-act="remove-confirm"]'), null);
    const pill = host.querySelector('[data-act="disposition"]');
    assert.ok(pill, `${kind}: the pill stays`);
    assert.match(text(pill), /^undecided ▾$/);
    assert.match(text(host), /alpha/);
    assert.match(text(host), /never surveyed · not published to Egeria/);
    assert.doesNotMatch(text(host), /\b(hide|unhide|remove)\b/i, 'no hide/remove words in the header');
  }
});

test('on every stage the content pane shows no hide or remove control, and the pill (where a header is drawn) is still there', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  let withHeader = 0;
  for (const st of STAGES) {
    const nav = $(d, `#intent-nav button[data-stage="${st}"]`);
    if (!nav) continue;
    nav.click();
    await tick(120);
    const c = $(d, '#content');
    assert.equal(c.querySelector('[data-act="hide"], [data-act="remove"], [data-act="remove-confirm"]'), null, `${st}: no hide/remove in the pane`);
    if (c.querySelector('#resource-header')) {
      withHeader += 1;
      assert.ok(c.querySelector('[data-act="disposition"]'), `${st}: header keeps the pill`);
    }
  }
  assert.ok(withHeader >= 1, 'at least one stage draws the header');
});

test('the disposition pill still opens the picker in the header\'s own slot', async () => {
  const ctx = await setUp('db', { stage: 'discovery' });
  const d = ctx.document;
  ctx.app.state.subTab = 'questions';
  $(d, '#intent-nav button[data-stage="discovery"]').click();
  await tick(150);
  const pill = $(d, '#content [data-act="disposition"]');
  assert.ok(pill, 'the discovery pane draws the header with its pill');
  pill.click();
  await tick(20);
  assert.ok($(d, '#content #resource-action [data-disp]'), 'the picker opens beside the content');
});

/* ── 5. Select mode ──────────────────────────────────────────────────────── */

test('Select mode, door 1: the bordered "Select several…" button, then "Done selecting" with the first-line hint', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  const btn = () => $(d, '#sidebar [data-act="select-mode"]');
  assert.equal(text(btn()), 'Select several…');
  assert.match(btn().className, /(^|\s)border(\s|$)/, 'bordered: it is a control');
  assert.equal($(d, '[data-select-bar]'), null);
  btn().click();
  assert.equal(text(btn()), 'Done selecting');
  assert.equal(text($(d, '[data-select-hint]')), 'Tick resources, then act on all of them.');
  assert.equal(ctx.s.selectMode, true);
  btn().click();
  assert.equal(ctx.s.selectMode, false);
  assert.equal(ctx.s.selected.size, 0, 'leaving clears the ticks');
});

for (const [name, mods] of [['shift', { shiftKey: true }], ['⌘ (meta)', { metaKey: true }], ['Ctrl', { ctrlKey: true }]]) {
  test(`Select mode, door 2/3: ${name}-click on a row enters it with that row ticked and does not open the row`, async () => {
    const ctx = await setUp('db');
    const d = ctx.window.document;
    assert.equal(ctx.s.selectMode, false);
    $(d, '#sidebar button[data-slug="beta"]').dispatchEvent(new ctx.window.MouseEvent('click', { bubbles: true, cancelable: true, ...mods }));
    await tick(20);
    assert.equal(ctx.s.selectMode, true);
    assert.deepEqual([...ctx.s.selected], ['beta']);
    assert.equal(ctx.s.selectedSlug, 'alpha', 'the pane did not move');
    assert.equal($(d, '#sidebar input[data-sel="beta"]').checked, true);
    assert.equal($(d, '#sidebar input[data-sel="alpha"]').checked, false);
    // Once on, the same chord toggles the row off.
    $(d, '#sidebar button[data-slug="beta"]').dispatchEvent(new ctx.window.MouseEvent('click', { bubbles: true, cancelable: true, ...mods }));
    assert.deepEqual([...ctx.s.selected], []);
    assert.equal(ctx.s.selectMode, true, 'still in select mode');
  });
}

test('known-negative: a plain click on a row still opens it and does not enter Select mode', async () => {
  const ctx = await setUp('db');
  $(ctx.document, '#sidebar button[data-slug="beta"]').click();
  await tick(100);
  assert.equal(ctx.s.selectMode, false);
  assert.equal(ctx.s.selectedSlug, 'beta');
});

test('a facet-filtered list offers "select these N"; an unfiltered one does not; clicking ticks exactly those', async () => {
  const rows = [row('a', { disposition: 'ignored' }), row('b', { disposition: 'ignored' }), row('c'), row('d')];
  const ctx = await setUp('db', { rows });
  const d = ctx.document;
  assert.equal($(d, '[data-act="select-these"]'), null, 'nothing narrows the list: no offer');
  $(d, '#sidebar button[data-facet="ignored"]').click();
  const offer = $(d, '[data-act="select-these"]');
  assert.ok(offer);
  assert.equal(text(offer), 'select these 2');
  assert.match(text(offer.parentElement), /^2 shown · select these 2$/);
  offer.click();
  assert.equal(ctx.s.selectMode, true);
  assert.deepEqual([...ctx.s.selected].sort(), ['a', 'b']);
  assert.equal(ctx.calls.filter((c) => c.method !== 'GET').length, 0, 'selecting writes nothing');
});

/* ── 6. the Select bar: groups by weight, remove last and quiet ──────────── */

test('the Select bar groups scope; judgement and lists; view; then a rule and "remove…", each on its own line', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  ctx.s.investigation = 'c360';
  $(d, '#sidebar [data-act="select-mode"]').click();
  const groups = [...d.querySelectorAll('[data-bar-group]')];
  assert.deepEqual(groups.map((g) => g.dataset.barGroup), ['scope', 'judgement', 'view', 'remove']);
  const acts = (g) => [...g.querySelectorAll('[data-act]')].map((x) => x.dataset.act);
  assert.deepEqual(acts(groups[0]), ['sel-scope-add', 'sel-scope-remove']);
  assert.deepEqual(acts(groups[1]), ['sel-disposition', 'sel-worklist']);
  assert.deepEqual(acts(groups[2]), ['sel-hide']);
  assert.deepEqual(acts(groups[3]), ['sel-remove']);
  // Separate lines: no group nests in another, and they are siblings in order.
  for (let i = 1; i < groups.length; i += 1) assert.equal(groups[i - 1].nextElementSibling, groups[i]);
  assert.match(groups[3].className, /(^|\s)border-t(\s|$)/, 'remove sits after a rule');
  // Remove is quiet: bordered, never the accent.
  const rm = groups[3].querySelector('[data-act="sel-remove"]');
  assert.match(rm.className, /(^|\s)border(\s|$)/);
  assert.equal(hasAccent(rm), false);
  assert.equal(text(rm), 'remove…', 'nothing ticked: the bare verb');
  assert.equal(rm.disabled, true);
  assert.match(text(groups[3]), /from Resource Explorer/);
});

test('the bar\'s remove button counts what is ticked: "Remove 1 database…" with one, never changing its wording to look like the single control', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar input[data-sel="alpha"]').click();
  assert.equal(text($(d, '[data-act="sel-remove"]')), 'Remove 1 database…');
  $(d, '#sidebar input[data-sel="beta"]').click();
  assert.equal(text($(d, '[data-act="sel-remove"]')), 'Remove 2 databases…');
  assert.equal(hasAccent($(d, '[data-act="sel-remove"]')), false, 'not primary');
});

for (const [kind, three, one] of [['db', 'Remove 3 databases', 'Remove 1 database'], ['repo', 'Remove 3 repos', 'Remove 1 repo'], ['filesystem', 'Remove 3 file systems', 'Remove 1 file system']]) {
  test(`the bulk confirmation (${kind}) uses the per-kind sentence and reads "${three}"; commit is the only accent`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    $(d, '#sidebar [data-act="select-mode"]').click();
    $(d, '#sidebar [data-act="sel-all"]').click();
    $(d, '[data-act="sel-remove"]').click();
    const box = $(d, '#sidebar-action');
    const commit = box.querySelector('[data-act="sel-remove-confirm"]');
    assert.equal(text(commit), three);
    assert.deepEqual(accents(box), [commit], 'the commit button is the only accent');
    assert.match(text(box.querySelector('[data-remove-sentence]')), /This cannot be undone/);
    assert.match(text(box.querySelector('[data-remove-sentence]')), /Not touched:/);
    assert.match(text(box), /alpha, beta, gamma/, 'names what goes');
    assert.doesNotMatch(text(box), /file share/i);
    assert.equal(ctx.calls.filter((c) => c.method === 'DELETE').length, 0, 'nothing until the commit');
    commit.click();
    await tick(150);
    const base = { db: '/api/databases/', repo: '/api/projects/', filesystem: '/api/filesystems/' }[kind];
    const tail = kind === 'filesystem' ? '/' : '';
    assert.deepEqual(ctx.calls.filter((c) => c.method === 'DELETE').map((c) => c.url), ['alpha', 'beta', 'gamma'].map((x) => `${base}${x}${tail}`));
    assert.equal(ctx.s[LIST_KEY[kind]].length, 0, 'lists refresh');
    assert.match(text($(d, '#sidebar-action')), /^3 removed\./);
  });

  test(`the bulk confirmation (${kind}) with one ticked still says "${one}"`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    $(d, '#sidebar [data-act="select-mode"]').click();
    $(d, '#sidebar input[data-sel="beta"]').click();
    $(d, '[data-act="sel-remove"]').click();
    assert.equal(text($(d, '[data-act="sel-remove-confirm"]')), one);
    $(d, '[data-act="sel-remove-cancel"]').click();
    assert.equal(text($(d, '#sidebar-action')), '');
  });
}

test('the bulk commit with a refusal reports it per resource and keeps that resource listed', async () => {
  const ctx = await setUp('db', { backend: { refuse: ['/api/databases/beta'] } });
  const d = ctx.document;
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar [data-act="sel-all"]').click();
  $(d, '[data-act="sel-remove"]').click();
  $(d, '[data-act="sel-remove-confirm"]').click();
  await tick(150);
  assert.match(text($(d, '#sidebar-action')), /beta: boom/);
  assert.deepEqual(ctx.s.databases.map((r) => r.slug), ['beta']);
});

test('the bulk hide still calls the same route; "− scope"/"＋ scope" still refresh the OPEN investigation pane', async () => {
  const members = { c360: [{ entity_type: 'database', entity_slug: 'old-db' }] };
  const ctx = await setUp('db', { stage: 'investigation', backend: { members } });
  const d = ctx.document;
  ctx.s.investigations = [{ slug: 'c360', display_name: 'Customer 360', status: 'open' }];
  ctx.s.investigation = 'c360';
  const inv = await import('/static/next/stages/investigation.js');
  inv.openInvestigationDetail('c360');
  await inv.renderInvestigation();
  const listed = () => [...$(d, '#content').querySelectorAll('[data-remove-member]')].map((b) => b.dataset.removeMember.split('|')[1]);
  assert.deepEqual(listed(), ['old-db']);
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar input[data-sel="alpha"]').click();
  $(d, '[data-act="sel-scope-add"]').click();
  await tick(100);
  assert.deepEqual(listed().sort(), ['alpha', 'old-db'], 'the open investigation page lists the new member');
  $(d, '[data-act="sel-hide"]').click();
  await tick(100);
  assert.deepEqual(ctx.calls.filter((c) => c.url === '/api/discovery/working-set').map((c) => c.body),
    [{ entity_type: 'database', entity_slug: 'alpha', hidden: true }]);
});

test('the Select bar scope wording: three states, and "save as work list…" is quiet', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  const longName = 'Customer 360 quarterly governance review programme';
  $(d, '#sidebar [data-act="select-mode"]').click();
  const add = () => $(d, '[data-act="sel-scope-add"]');
  const rem = () => $(d, '[data-act="sel-scope-remove"]');
  // None current.
  assert.equal(text(add()), '＋ add to an investigation…');
  assert.equal(text(rem()), '− remove from investigation');
  assert.equal(rem().disabled, true);
  assert.match(text($(d, '[data-scope-reason]')), /no investigation selected/);
  assert.equal(rem().title, 'Choose an investigation first');
  // One current.
  ctx.s.investigations = [{ slug: 'c360', display_name: 'Customer 360', status: 'open' },
    { slug: 'long', display_name: longName, status: 'open' }];
  ctx.s.investigation = 'c360';
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar [data-act="select-mode"]').click();
  assert.equal(text(add()), '＋ add to Customer 360');
  assert.equal(text(rem()), '− remove from Customer 360');
  assert.equal(d.querySelector('[data-scope-reason]'), null);
  // Long name: cut at 24 chars, full name in the title.
  ctx.s.investigation = 'long';
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar [data-act="select-mode"]').click();
  assert.equal(text(add()), `＋ add to ${longName.slice(0, 23)}…`);
  assert.ok(add().title.includes(longName));
  assert.ok(rem().title.includes(longName));
  // Work list: ellipsis, no accent border.
  const wl = $(d, '[data-act="sel-worklist"]');
  assert.equal(text(wl), 'save as work list…');
  assert.doesNotMatch(wl.className, /border-accent/);
  assert.doesNotMatch(text($(d, '[data-select-bar]')), /delete/i);
});

/* ── 7. Find carries a word ──────────────────────────────────────────────── */

for (const [kind, label] of [['repo', '＋ Find repos'], ['db', '＋ Find databases'], ['filesystem', '＋ Add a file system']]) {
  test(`the sidebar's add button reads "${label}" for ${kind}, still opens the right Find, and the ? stays an icon`, async () => {
    const ctx = await setUp(kind);
    const d = ctx.document;
    const btn = $(d, '#sidebar [data-act="find-repos"]');
    assert.equal(text(btn), label);
    assert.doesNotMatch(text(btn), /share/i);
    assert.equal(btn.querySelector('svg'), null, 'a word, not an icon');
    assert.equal(text($(d, '#sidebar [data-act="mark-key"]')), '', 'the ? is still an icon');
    btn.click();
    await tick(60);
    assert.ok($(d, '#wl-detail'), 'Find still opens its dialog');
  });
}

for (const [kind, label, noun] of [['db', '＋ Find databases', 'databases'], ['filesystem', '＋ Add a file system', 'filesystems']]) {
  test(`an empty ${kind} list says so and carries the same control inline`, async () => {
    const ctx = await setUp(kind, { rows: null, slugs: ['x'] });
    ctx.s[LIST_KEY[kind]] = [];
    ctx.s.selectedSlug = null;
    ctx.app.renderSidebar();
    const d = ctx.document;
    const msg = [...d.querySelectorAll('#sidebar div')].find((x) => /registered\./.test(x.textContent) && x.children.length === 1);
    assert.ok(msg, 'the empty-state line');
    assert.match(text(msg), new RegExp(`^No ${noun} registered\\. ${label}$`));
    const inline = msg.querySelector('[data-act="find-repos"]');
    assert.ok(inline);
    inline.click();
    await tick(60);
    assert.ok($(d, '#wl-detail'), 'the inline control opens Find');
  });
}

/* ── 8. one word: no "delete" in a label or tooltip ──────────────────────── */

const DELETE_WORD = /\bdelet(e|es|ed|ing|ion)\b/i;

test('on screen: no "delete" in any visible text, title or aria-label of the sidebar, bar, menu, panels or header', async () => {
  const ctx = await setUp('db');
  const d = ctx.document;
  const surfaces = [];
  const grab = (label) => {
    for (const root of [$(d, '#sidebar'), $(d, 'header'), panel(d), menu(d), $(d, '#sidebar-action')].filter(Boolean)) {
      const clone = root.cloneNode(true);
      // The confirmation SENTENCE describes what RE's own records undergo
      // ("delete its local survey records"): it is the designer's wording and a
      // sentence, not a label or tooltip. Its commit button and everything else is checked.
      clone.querySelectorAll('[data-remove-sentence]').forEach((x) => x.remove());
      const attrs = [...clone.querySelectorAll('[title],[aria-label],[placeholder]')]
        .map((e) => `${e.getAttribute('title') || ''} ${e.getAttribute('aria-label') || ''} ${e.getAttribute('placeholder') || ''}`);
      surfaces.push([label, text(clone), ...attrs]);
    }
  };
  grab('initial');
  $(d, '#sidebar [data-act="select-mode"]').click();
  $(d, '#sidebar input[data-sel="alpha"]').click();
  grab('select mode');
  $(d, '[data-act="sel-remove"]').click();
  grab('bulk confirm');
  (await openMenu(d)).querySelector('[data-act="menu-remove"]').click();
  grab('single confirm');
  (await openMenu(d));
  grab('menu');
  const header = document_header(ctx);
  surfaces.push(['content header', text(header), ...[...header.querySelectorAll('[title]')].map((e) => e.title)]);
  for (const [label, ...strs] of surfaces) {
    for (const s of strs) assert.doesNotMatch(s, DELETE_WORD, `${label}: ${JSON.stringify(s.slice(0, 120))}`);
  }
});
function document_header(ctx) {
  const h = ctx.document.createElement('div');
  h.innerHTML = ctx.app.resourceHeaderHtml('alpha');
  return h;
}

test('known-negative: the confirmation sentence does say what happens to RE\'s own records (so the exemption is real)', async () => {
  const ctx = await setUp('db');
  assert.match(ctx.app.removeConfirmationHtml('database', 'alpha'), /delete its local survey records/);
  assert.match(ctx.app.removeConfirmationHtml('database', ['a', 'b']), /delete their local survey records/);
});

/** Source scan over static/next. Every remaining "delete" is one of:
 *   - a JS identifier/method (`.delete(`, `delete x.y`, `data-delete…`, `deleteX`),
 *   - a comment,
 *   - a sentence describing an effect ("nothing is deleted", "does not delete your files"),
 *   - a label on a DIFFERENT kind of object in admin and note/schedule panes
 *     (annotation types, groups, discovery sources, unsigned notes, schedules,
 *     the Egeria process kind) -- the designer retired "delete" for removing a
 *     RESOURCE from Resource Explorer, not for these.
 *  Anything else fails, which is how a new "delete…" label on a resource gets caught. */
test('source scan: no resource-removal label or tooltip in static/next says "delete" (allowlist documented)', () => {
  const ALLOW_FILES = [
    /^admin\//,                 // other objects: annotation types, groups, discovery sources, repair, resync, prefect
    /^stages\/curate-bands\.js$/, // an unsigned curation note
    /^stages\/automate\.js$/,   // a schedule
  ];
  const ALLOW_LINES = [
    /\bdelete (out|host|layout|\$\()/,         // `delete x.dataset...`
    /\bdelete [\w$]+(\.[\w$]+|\[[^\]]+\])+\s*;/,   // the JS `delete` OPERATOR on a property: `delete a.b[c];` (a label has no `.`/`[` after its first word)
    /\.delete\(/,                              // Map/Set methods
    /data-delete[\w-]*/,                       // attribute names
    /delete: 'deletes an Egeria entry'/,       // Egeria process-kind label (a different object)
    /nothing (is|was) deleted|Nothing is deleted|does not delete|is not deleted|never deletes/i,  // effect sentences
    /delete its local survey records|delete their local survey records|delete all (its|their) local survey data/, // removeConfirmationHtml
    /window\.confirm\(`Close /,                // investigation close: "Nothing is deleted"
    /Unbind this investigation/,
    /The asset can be deleted there|deleted there/, // admin/resync, covered by admin/ anyway
    /saved entries are permanent: no edit, no delete/,   // the journal's permanence sentence, not a resource-removal label
    /deleted in Egeria|state === 'deleted'|'will delete'/,   // the Curate commit's word for an Egeria soft delete (an Egeria element, not a resource in RE)
  ];
  const offenders = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) { if (ent.name !== 'fonts') walk(full); continue; }
      if (!/\.(js|html)$/.test(ent.name) || ent.name.startsWith('tailwind')) continue;
      const rel = path.relative(NEXT_DIR, full);
      if (ALLOW_FILES.some((re) => re.test(rel))) continue;
      const lines = fs.readFileSync(full, 'utf8').split('\n');
      let inBlock = false;
      lines.forEach((line, i) => {
        const t = line.trim();
        if (inBlock) { if (t.includes('*/')) inBlock = false; return; }
        if (t.startsWith('/*')) { if (!t.includes('*/')) inBlock = true; return; }
        if (t.startsWith('//') || t.startsWith('*') || t.startsWith('<!--')) return;
        const code = line.replace(/\/\/.*$/, (m) => (line.slice(0, line.indexOf(m)).includes("'") ? m : ''));
        if (!/\bdelet(e|es|ed|ing|ion)\b/i.test(code.replace(/data-delete[\w-]*/g, ''))) return;
        if (ALLOW_LINES.some((re) => re.test(code))) return;
        offenders.push(`${rel}:${i + 1}: ${t.slice(0, 140)}`);
      });
    }
  };
  walk(NEXT_DIR);
  assert.deepEqual(offenders, [], 'a "delete" label/tooltip crept back into static/next');
});

test('known-negative: the source scan\'s patterns would flag a resource "delete…" label', () => {
  const line = `<button data-act="sel-x" title="Unregister entirely and delete all local survey data">delete…</button>`;
  assert.match(line, DELETE_WORD);
  const ALLOW = [/delete its local survey records/, /\.delete\(/, /nothing is deleted/i];
  assert.equal(ALLOW.some((re) => re.test(line)), false);
});
