/** Behaviour: Files step 1 (2026-10-20), PI-022.
 *
 *  Register a file system on /next with a real form (no link to Classic), and the file system's one Publish,
 *  which at this step attaches the root folder only. Real app.js, router and stage modules; only fetch is stubbed.
 *
 *  - the form has the five fields the route's model takes, and NO Egeria field;
 *  - the POST body is exactly the route's own model keys (checked against the Python model by a pytest);
 *  - on success the file system is in the sidebar under FS and selected, and the dialog closes;
 *  - a duplicate slug is an ERROR cue on the field and holds Register; an unreadable mount is a WARNING cue and
 *    does not;
 *  - Publish: the state on screen is the re-read proof row; "confirmed" only when that re-read says attached;
 *    a failure is a pop-up AND a persistent cue.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const ROUTE_MODEL_KEYS = ['canonical_mount_point', 'display_name', 'group_slug', 'local_mount_point', 'slug'];

function makeBackend({
  existing = [], mountState = 'readable', registerError = null, rootStates = [{ state: 'not_attached' }],
  attach = null,
} = {}) {
  const calls = [];
  const rows = [...existing];
  const roots = [...rootStates];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (method === 'GET' && u === '/api/filesystems/') return ok(rows);
    if (method === 'POST' && u === '/api/filesystems/check-mount') return ok({ state: mountState });
    if (method === 'POST' && u === '/api/filesystems/') {
      if (registerError) return err(400, registerError);
      const stored = String(body.slug).replace(/-/g, '_').toLowerCase();
      rows.push({
        slug: stored, display_name: body.display_name, local_mount_point: body.local_mount_point,
        canonical_mount_point: body.canonical_mount_point, description: '', status: 'active',
        last_surveyed_at: '', egeria_asset_guid: '', file_count: 0, data_file_count: 0, group_slug: body.group_slug,
        disposition: 'undecided', is_published: false, egeria_publish_note: '', working_set_hidden: false,
      });
      return ok({ ...rows[rows.length - 1] });
    }
    if (method === 'GET' && /^\/api\/filesystems\/[^/]+\/root-folder$/.test(u)) {
      return ok(roots.length > 1 ? roots.shift() : roots[0]);
    }
    if (method === 'POST' && /^\/api\/filesystems\/[^/]+\/attach-root$/.test(u)) {
      if (attach) return attach(err, ok);
      return ok({ state: 'attached' });
    }
    if (u.startsWith('/api/projects/groups')) return ok([{ slug: 'g1', display_name: 'Group one' }]);
    if (u.includes('/egeria-surveys')) return ok([]);
    return ok(u.includes('/projects/') || u.includes('/databases/') || u.includes('/filesystems/') ? [] : {});
  };
  return calls;
}

async function setUp(opts = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'filesystem';
  app.state.filesystems = opts.existing || [];
  app.state.filesystemsLoaded = true;
  app.state.groups = [];
  app.state.selectedSlug = null;
  app.state.me = { user_id: 'dan' };
  const side = document.createElement('div');
  side.id = 'sidebar';
  document.body.appendChild(side);
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail', 'rail-scope', 'scope-slug',
    'investigation-name', 'whoami', 'activity-count', 'answered-count']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderSidebar();
  return { document, window, calls, app };
}

const openForm = async (document) => {
  document.querySelector('#sidebar [data-act="find-repos"]').click();
  await tick(60);
  return document.getElementById('wl-detail');
};
const fill = (dlg, key, value) => {
  const inp = dlg.querySelector(`[data-fsr="${key}"]`);
  assert.ok(inp, `the form has a ${key} field`);
  inp.value = value;
  inp.dispatchEvent(new inp.ownerDocument.defaultView.Event('input', { bubbles: true }));
  return inp;
};
const change = (inp) => inp.dispatchEvent(new inp.ownerDocument.defaultView.Event('change', { bubbles: true }));
const cue = (dlg, field) => dlg.querySelector(`[data-fs-cue="${field}"]`);

test('the Find action on FS opens a real form, not a link to Classic, with the five route fields and no Egeria field', async () => {
  const { document } = await setUp();
  const dlg = await openForm(document);
  assert.ok(dlg, 'a dialog opened');
  assert.equal(dlg.querySelector('a[href*="classic"]'), null, 'no link to Classic');
  assert.doesNotMatch(text(dlg), /Open in the current UI/);
  const keys = [...dlg.querySelectorAll('[data-fsr]')].map((i) => i.dataset.fsr).sort();
  // plus "Where it is" (S3 reach, 2026-11-02): a local mount by default; the local POST stays the five fields (next test)
  assert.deepEqual(keys, [...ROUTE_MODEL_KEYS, 'protocol'].sort());
  assert.equal(dlg.querySelector('[data-fsr="protocol"]').value, 'local');
  // the Egeria connection is the platform configuration's: no field, no label, no toggle for it
  assert.doesNotMatch(dlg.innerHTML.toLowerCase(), /egeria[ _-]?(url|server|user|password|host)|platform url|view server/);
  assert.match(text(dlg), /Nothing is sent to Egeria/);
});

test('Register posts exactly the route model, then the new file system is in the sidebar and selected', async () => {
  const { document, calls, app } = await setUp();
  const dlg = await openForm(document);
  fill(dlg, 'slug', 'My-Data');
  fill(dlg, 'display_name', 'My data');
  fill(dlg, 'local_mount_point', '/mnt/mydata');
  fill(dlg, 'canonical_mount_point', 'file://nas/mydata');
  dlg.querySelector('[data-fsr="group_slug"]').value = 'g1';
  dlg.querySelector('[data-fsr="group_slug"]').dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  dlg.querySelector('[data-act="fsr-register"]').click();
  await tick(120);
  const post = calls.find((c) => c.method === 'POST' && c.url === '/api/filesystems/');
  assert.ok(post, 'the existing POST /api/filesystems/ was used');
  assert.deepEqual(Object.keys(post.body).sort(), ROUTE_MODEL_KEYS);
  assert.equal(post.body.group_slug, 'g1');
  assert.equal(document.getElementById('wl-detail'), null, 'the dialog closed');
  // the stored slug (dashes became underscores) is what the sidebar shows and what is selected
  assert.equal(app.state.selectedSlug, 'my_data');
  const row = document.querySelector('#sidebar button[data-slug="my_data"]');
  assert.ok(row, 'the new file system is a row in the sidebar');
  assert.match(text(row), /My data/);
});

test('a slug that is already registered is an error cue on the field and holds Register', async () => {
  const existing = [{ slug: 'my_data', display_name: 'Old', local_mount_point: '/x', canonical_mount_point: '',
    status: 'active', egeria_asset_guid: '', is_published: false, working_set_hidden: false }];
  const { document, calls } = await setUp({ existing });
  const dlg = await openForm(document);
  fill(dlg, 'display_name', 'Another');
  fill(dlg, 'local_mount_point', '/mnt/other');
  fill(dlg, 'slug', 'my-data');                       // the same row once stored
  assert.equal(cue(dlg, 'slug').dataset.cueKind, 'error');
  assert.match(text(cue(dlg, 'slug')), /✕/);
  assert.match(text(cue(dlg, 'slug')), /already registered/);
  assert.match(dlg.querySelector('[data-fsr="slug"]').className, /border-state-warn/);
  assert.equal(dlg.querySelector('[data-act="fsr-register"]').disabled, true);
  dlg.querySelector('[data-act="fsr-register"]').click();
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST' && c.url === '/api/filesystems/').length, 0);
  fill(dlg, 'slug', 'other-data');
  assert.equal(cue(dlg, 'slug').dataset.cueKind, 'none');
  assert.equal(dlg.querySelector('[data-act="fsr-register"]').disabled, false);
});

test('a refusal from the server for a duplicate shows on the slug field, not as a sentence under the form', async () => {
  const { document } = await setUp({ registerError: "FileSystem 'z' already registered." });
  const dlg = await openForm(document);
  fill(dlg, 'slug', 'z'); fill(dlg, 'display_name', 'Z'); fill(dlg, 'local_mount_point', '/z');
  dlg.querySelector('[data-act="fsr-register"]').click();
  await tick(80);
  assert.equal(cue(dlg, 'slug').dataset.cueKind, 'error');
  assert.equal(dlg.querySelector('[data-act="fsr-register"]').disabled, true);
  assert.equal(text(dlg.querySelector('[data-fsr-state]')), '', 'no sentence below the form for this');
});

test('a mount the server cannot read is a warning cue, and Register stays available', async () => {
  const { document, calls } = await setUp({ mountState: 'not_readable' });
  const dlg = await openForm(document);
  fill(dlg, 'slug', 'nas'); fill(dlg, 'display_name', 'NAS');
  change(fill(dlg, 'local_mount_point', '/Volumes/elsewhere'));
  await tick(60);
  assert.ok(calls.some((c) => c.url === '/api/filesystems/check-mount' && c.body.path === '/Volumes/elsewhere'));
  assert.equal(cue(dlg, 'mount').dataset.cueKind, 'warning');
  assert.match(text(cue(dlg, 'mount')), /⚠/);
  assert.match(text(cue(dlg, 'mount')), /can't read this path/);
  assert.match(dlg.querySelector('[data-fsr="local_mount_point"]').className, /border-state-gap/);
  assert.equal(dlg.querySelector('[data-act="fsr-register"]').disabled, false, 'a warning does not block');
  // editing the path drops the stale warning: the new path has not been checked
  fill(dlg, 'local_mount_point', '/Volumes/elsewhere2');
  assert.equal(cue(dlg, 'mount').dataset.cueKind, 'none');
});

// ── the file system's Publish ───────────────────────────────────────────

async function publishBand(opts) {
  const env = await setUp(opts);
  const { document, app } = env;
  app.state.selectedSlug = 'nas';
  app.state.filesystems = [{ slug: 'nas', display_name: 'NAS', egeria_asset_guid: '', is_published: false }];
  const host = document.createElement('section');
  document.body.appendChild(host);
  const { renderPublishBand } = await import('/static/next/stages/publish.js');
  await renderPublishBand(host, 'nas', 'filesystem');
  await tick(40);
  return { ...env, host };
}

test('Publish for a file system: not attached until a read-back says so, then confirmed from the re-read row', async () => {
  const GUID = '11111111-2222-3333-4444-555555555555';
  const { host, calls } = await publishBand({
    rootStates: [{ state: 'not_attached' },
      { state: 'attached', guid: GUID, read_back_at: `${new Date().toISOString().slice(0, 10)}T14:05:09`, by: 'dan', why: '' }],
  });
  assert.match(text(host), /root folder not attached/);
  assert.equal(host.querySelectorAll('[data-fs-publish-go]').length, 1, 'ONE publish control');
  assert.doesNotMatch(text(host), /no publish control here yet/);
  host.querySelector('[data-fs-publish-go]').click();
  // the cue is on the press, before the answer
  assert.match(text(host.querySelector('[data-publish-feedback]')), /publishing/);
  await tick(80);
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/filesystems/nas/attach-root'));
  assert.match(text(host.querySelector('[data-fs-root]')), /root folder attached/);
  assert.match(text(host.querySelector('[data-fs-root]')), /read back 14:05/);
  assert.equal(host.querySelector('[data-guid]').dataset.guid, GUID);
  assert.match(text(host.querySelector('[data-publish-feedback]')), /confirmed/);
});

test('"confirmed" never comes from the press: a 200 whose re-read is still not attached says not confirmed', async () => {
  const { host } = await publishBand({
    rootStates: [{ state: 'not_attached' }, { state: 'not_confirmed', guid: 'g', read_back_at: '2026-10-20T14:05:09', why: 'Egeria answered with a different element (x)' }],
  });
  host.querySelector('[data-fs-publish-go]').click();
  await tick(80);
  assert.doesNotMatch(text(host.querySelector('[data-publish-feedback]')), /confirmed · /);
  assert.match(text(host.querySelector('[data-publish-feedback]')), /not confirmed/);
  assert.match(text(host.querySelector('[data-fs-root]')), /root folder not confirmed/);
  assert.match(text(host.querySelector('[data-fs-root]')), /different element/);
});

test('the Publish words promise the root folder only, not tags, group or feedback', async () => {
  const { host } = await publishBand({});
  const words = text(host);
  assert.match(words, /attaches the root folder only/);
  assert.doesNotMatch(words, /tags|group|feedback/i);
});

test('a GUID held without a read-back proof reads "GUID held · not read back yet", never "not attached", and reports are listed', async () => {
  const GUID = '11111111-2222-3333-4444-555555555555';
  const { host, calls } = await publishBand({ rootStates: [{ state: 'held', guid: GUID, read_back_at: '', by: '', why: '' }] });
  assert.equal(host.querySelector('[data-fs-root]').dataset.fsRoot, 'held');
  assert.match(text(host.querySelector('[data-fs-root]')), /GUID held · not read back yet/);
  assert.doesNotMatch(text(host), /not attached/);
  assert.doesNotMatch(text(host), /no asset to list reports from/);
  assert.equal(host.querySelector('[data-guid]').dataset.guid, GUID);
  assert.ok(calls.some((c) => c.method === 'GET' && c.url === '/api/filesystems/nas/egeria-surveys'),
    'reports are listed from the held GUID');
});

test('"read back" carries the date when the read-back was not today', async () => {
  const { readBackWhen } = await import('/static/next/stages/publish.js');
  const now = new Date('2026-10-20T20:00:00Z');
  assert.equal(readBackWhen('2026-10-20T14:05:09', now), '14:05');
  assert.equal(readBackWhen('2026-10-18T14:05:09', now), '2026-10-18 14:05');
  assert.equal(readBackWhen('', now), '');
});

test('a failed publish is a pop-up and a cue that stays on the band', async () => {
  const { host, document } = await publishBand({
    attach: (err) => err(500, 'Publish failed: Egeria is unreachable'),
  });
  host.querySelector('[data-fs-publish-go]').click();
  await tick(80);
  assert.ok(document.querySelector('[data-publish-failure]'), 'a pop-up');
  assert.match(text(document.querySelector('[data-publish-failure]')), /Egeria is unreachable/);
  assert.match(text(host.querySelector('[data-publish-feedback]')), /not published/);
  assert.equal(host.querySelector('[data-fs-publish-go]').disabled, false, 'the press can be retried');
  assert.match(text(host.querySelector('[data-fs-root]')), /root folder not attached/, 'nothing claims it is attached');
});
