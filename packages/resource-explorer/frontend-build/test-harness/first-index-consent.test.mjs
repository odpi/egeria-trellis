/** Behaviour: first index, asked first, and the per-repository choice (REPLY-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20).
 *
 *  What must hold: nothing is queued without a POST that names the repository; "Index all" and "Ask me for each" are
 *  both offered; in the stepping path each yes queues AT ONCE (the first starts before the second is answered); the
 *  list is the record and an answer can be changed until the repository starts; the summary counts and resumes; an
 *  estimate that cannot be made says "unknown until the first index" and an unmeasured repository never reads 0 files;
 *  the cue on the repository changes in place; the remembered path only focuses a button, it never answers. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const REPOS = [
  { slug: 'r1', name: 'Alpha', measured: true, files: 1240, text_bytes: 38 * 1048576, source: 'from the survey of 10-17', repo_kb: 245760 },
  { slug: 'r2', name: 'Beta', measured: true, files: 300, text_bytes: 5 * 1048576, source: 'from the survey of 10-17', repo_kb: 20480 },
  { slug: 'r3', name: 'Gamma', measured: false, files: null, text_bytes: null, source: 'no file inventory yet', repo_kb: null },
].map((r) => ({ index_state: 'not_yet_indexed', seconds: null, chunks: null, seconds_source: '', chunks_source: '',
  fetch: `clones the repository${r.repo_kb ? ` (≈ ${Math.round(r.repo_kb / 1024)} MB)` : ''}`, ...r }));

function backend() {
  const calls = [];
  const lines = [];          // progress lines, in the order of the yeses
  let n = 0;
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/first-index/ask')) {
      const slugs = new URL(u, 'http://x').searchParams.getAll('slug');
      return ok({ repositories: REPOS.filter((r) => slugs.includes(r.slug)), missing: [], basis: {}, totals: {} });
    }
    if (u === '/api/first-index/queue') {
      const queued = body.slugs.map((slug) => {
        n += 1;
        const l = { slug, name: REPOS.find((r) => r.slug === slug).name, run_id: `run-${n}`, state: 'waiting',
          reason: '', position: lines.filter((x) => x.batch_id === body.batch_id).length + 1,
          batch_id: body.batch_id, batch_total: body.batch_total, finished_at: '', index_state: 'not_yet_indexed' };
        lines.push(l);
        return { slug, run_id: l.run_id, activity_id: `act-${n}`, position: l.position, batch_id: body.batch_id };
      });
      return ok({ queued, refused: [], batch_id: body.batch_id });
    }
    if (u.startsWith('/api/first-index/withdraw/')) {
      const id = u.split('/').pop();
      const l = lines.find((x) => x.run_id === id);
      if (!l || l.state !== 'waiting') return { ok: false, status: 409, statusText: 'x', json: async () => ({ detail: 'started' }) };
      lines.splice(lines.indexOf(l), 1);
      return ok({ run_id: id, state: 'withdrawn' });
    }
    if (u === '/api/first-index/progress') {
      return ok({ lines: lines.map((l) => ({ ...l })), done: lines.filter((l) => l.state === 'indexed').length,
        of: lines.length, active: lines.some((l) => l.state === 'waiting' || l.state === 'indexing') });
    }
    if (u === '/api/egeria/linkage/stale' || u.startsWith('/api/automate/subscriptions')) return ok([]);
    return ok({});
  };
  return { calls, lines, queueCalls: () => calls.filter((c) => c.url === '/api/first-index/queue') };
}

async function setUp({ slugs = ['r1'], current = 'r1' } = {}) {
  const { document, window } = makeDomEnvironment();
  const be = backend();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const fi = await import('/static/next/first-index.js');
  fi._setPollMs(5);
  app.state.resourceType = 'repo';
  app.state.projects = REPOS.map((r) => ({ slug: r.slug, display_name: r.name, github_url: `https://github.com/o/${r.slug}`,
    index_state: 'not_yet_indexed', disposition: 'undecided' }));
  app.state.firstIndex = undefined;
  app.state.selectedSlug = current;
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  window.localStorage.clear();
  const host = document.createElement('div');
  host.id = 'resource-header';
  document.body.appendChild(host);
  const render = () => { host.innerHTML = app.resourceHeaderHtml(current); app.bindResourceHeader(); };
  render();
  return { document, window, app, fi, be, host, render, dialog: () => document.getElementById('wl-detail') };
}

const click = (el) => el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));

test('the cue offers "index it ›" and it opens the single ask with measured and unknown figures', async () => {
  const t = await setUp();
  const cue = t.host.querySelector('[data-index-cue]');
  assert.match(text(cue), /not yet indexed/);
  click(cue.querySelector('[data-first-index-open]'));
  await tick();
  const d = t.dialog();
  assert.match(text(d), /Index Alpha for chat\?/);
  assert.match(text(d.querySelector('[data-figure="measured"]')), /1,240 files · 38 MB of text · from the survey of 10-17/);
  assert.match(text(d.querySelector('[data-figure="fetch"]')), /clones the repository \(≈ 240 MB\)/);
  assert.match(text(d.querySelector('[data-figure="estimate"]')), /time: unknown until the first index/);
  assert.match(text(d.querySelector('[data-figure="estimate"]')), /index size: unknown until the first index/);
  assert.equal(t.be.queueCalls().length, 0, 'opening the ask queues nothing');
});

test('Index it queues one named repository, and the cue changes in place as the run rows change', async () => {
  const t = await setUp();
  click(t.host.querySelector('[data-first-index-open]'));
  await tick();
  click(t.dialog().querySelector('[data-ask-yes]'));
  await tick(60);
  const q = t.be.queueCalls();
  assert.equal(q.length, 1);
  assert.deepEqual(q[0].body.slugs, ['r1']);
  assert.equal(q[0].body.path, 'single');
  const cue = () => text(t.host.querySelector('[data-index-cue]'));
  assert.match(cue(), /queued/);
  const before = t.host.querySelector('[data-index-cue]');
  t.be.lines[0].state = 'indexing';
  await t.fi.refreshFirstIndex();
  assert.match(cue(), /indexing/);
  assert.notEqual(t.host.querySelector('[data-index-cue]'), null);
  // The run says it succeeded and the project now has its own index: the cue reads indexed with the date.
  t.be.lines[0].state = 'indexed'; t.be.lines[0].finished_at = '2026-10-20T14:20:00'; t.be.lines[0].index_state = 'indexed';
  await t.fi.refreshFirstIndex();
  assert.match(cue(), /indexed 10-20/);
  void before;
});

test('a run that says indexed while the project has no index of its own does not draw "indexed"', async () => {
  const t = await setUp();
  t.app.state.firstIndex = { lines: { r1: { slug: 'r1', state: 'indexed', finished_at: '2026-10-20T14:20:00' } } };
  t.render();
  assert.doesNotMatch(text(t.host.querySelector('[data-index-cue]')), /indexed 10-20/);
  assert.match(text(t.host.querySelector('[data-index-cue]')), /not yet indexed/);
});

async function openBatch(t, { listOpen = false } = {}) {
  void t.fi.openFirstIndexAsk({ slugs: ['r1', 'r2', 'r3'], listOpen });
  await tick(60);
  return t.dialog();
}

test('the batch ask offers both paths, names the totals, and never reads an unmeasured repository as 0 files', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  assert.match(text(d.querySelector('[data-path="all"]')), /^Index all 3$/);
  assert.match(text(d.querySelector('[data-path="each"]')), /^Ask me for each ›$/);
  const totals = text(d.querySelector('[data-totals]'));
  assert.match(totals, /1,540 files/);
  assert.match(totals, /measured for 2 of 3/);
  assert.match(totals, /time: unknown until the first index/);
  assert.equal(t.be.queueCalls().length, 0);
});

test('Index all queues the three named repositories in one batch and remembers the path, not the consent', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  click(d.querySelector('[data-path="all"]'));
  await tick(80);
  const q = t.be.queueCalls();
  assert.equal(q.length, 1);
  assert.deepEqual(q[0].body.slugs, ['r1', 'r2', 'r3']);
  assert.equal(q[0].body.path, 'all');
  assert.equal(q[0].body.batch_total, 3);
  assert.equal(t.window.localStorage.getItem('re_first_index_path'), 'all');
  // next batch: the ask is shown again, both buttons offered, the remembered one focused; nothing queued by opening it
  t.dialog().querySelector('[data-act="close"]').click();
  const d2 = await openBatch(t);
  assert.ok(d2.querySelector('[data-path="all"][data-default]'));
  assert.ok(d2.querySelector('[data-path="each"]'));
  assert.equal(t.be.queueCalls().length, 1, 'the remembered path queued nothing');
});

test('Ask me for each: the first yes is queued before the second is answered; skip; the remaining shortcut; the summary', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  click(d.querySelector('[data-path="each"]'));
  await tick();
  assert.match(text(d.querySelector('[data-step-title]')), /^1 of 3 · Alpha$/);
  assert.equal(d.querySelector('[data-step-remaining]'), null, 'no shortcut on the first step');
  click(d.querySelector('[data-step-yes]'));
  await tick(60);
  // the first is already queued, with a position, while the second is still being asked
  assert.equal(t.be.queueCalls().length, 1);
  assert.deepEqual(t.be.queueCalls()[0].body.slugs, ['r1']);
  assert.equal(t.be.queueCalls()[0].body.path, 'each');
  assert.match(text(d.querySelector('[data-step-title]')), /^2 of 3 · Beta$/);
  assert.match(text(d.querySelector('[data-row="r1"]')), /yes · queued 1st/);
  assert.match(text(d.querySelector('[data-row="r2"]')), /asking now/);
  assert.match(text(d.querySelector('[data-row="r3"]')), /not asked yet/);
  // the shortcut is there from step 2 on and carries its totals on hover
  const rem = d.querySelector('[data-step-remaining]');
  assert.match(rem.textContent, /Index the remaining 2/);
  assert.match(rem.getAttribute('title'), /files/);
  click(d.querySelector('[data-step-skip]'));
  await tick();
  assert.match(text(d.querySelector('[data-step-title]')), /^3 of 3 · Gamma$/);
  assert.match(text(d.querySelector('[data-row="r2"]')), /skipped/);
  click(d.querySelector('[data-step-remaining]'));
  await tick(60);
  assert.deepEqual(t.be.queueCalls().map((c) => c.body.slugs), [['r1'], ['r3']]);
  assert.match(text(d.querySelector('[data-summary-line]')), /^2 queued · 1 skipped · 0 not asked$/);
});

test('Skip the rest ends the stepping, leaves the rest unqueued, and "Ask about the 2 ›" resumes', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  click(d.querySelector('[data-path="each"]'));
  await tick();
  click(d.querySelector('[data-step-yes]'));
  await tick(60);
  click(d.querySelector('[data-step-skip-rest]'));
  await tick();
  assert.match(text(d.querySelector('[data-summary-line]')), /^1 queued · 0 skipped · 2 not asked/);
  assert.equal(t.be.queueCalls().length, 1, 'nothing unanswered was queued');
  click(d.querySelector('[data-summary="notasked"]'));
  await tick();
  assert.deepEqual([...d.querySelectorAll('[data-ask-list] [data-row]')].map((r) => r.dataset.row), ['r2', 'r3']);
  click(d.querySelector('[data-resume]'));
  await tick();
  assert.match(text(d.querySelector('[data-step-title]')), /Beta$/);
});

test('an answer can be changed by pressing its row until the repository starts; a started row offers nothing', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  click(d.querySelector('[data-path="each"]'));
  await tick();
  click(d.querySelector('[data-step-yes]'));
  await tick(60);
  click(d.querySelector('[data-step-skip]'));
  await tick();
  // r2 was skipped: pressing it changes the answer to a yes that names it
  click(d.querySelector('[data-row="r2"] [data-row-press]'));
  await tick(60);
  assert.deepEqual(t.be.queueCalls().map((c) => c.body.slugs), [['r1'], ['r2']]);
  assert.match(text(d.querySelector('[data-row="r2"]')), /yes · queued 2nd/);
  // r1 is still waiting: pressing it takes the yes back
  click(d.querySelector('[data-row="r1"] [data-row-press]'));
  await tick(60);
  assert.ok(t.be.calls.some((c) => c.url.startsWith('/api/first-index/withdraw/')));
  assert.match(text(d.querySelector('[data-row="r1"]')), /skipped/);
  // r2 starts indexing: its row is no longer a control
  t.be.lines.find((l) => l.slug === 'r2').state = 'indexing';
  await t.fi.refreshFirstIndex();
  await tick();
  assert.match(text(d.querySelector('[data-row="r2"]')), /indexing/);
  assert.equal(d.querySelector('[data-row="r2"] [data-row-press]'), null);
});

test('closing mid-way queues nothing more', async () => {
  const t = await setUp();
  const d = await openBatch(t);
  click(d.querySelector('[data-path="each"]'));
  await tick();
  click(d.querySelector('[data-step-yes]'));
  await tick(60);
  d.querySelector('[data-act="close"]').click();
  await tick(30);
  assert.equal(t.dialog(), null);
  assert.equal(t.be.queueCalls().length, 1);
});

test('Automate lists First indexing from run rows with "N of M done"', async () => {
  const t = await setUp();
  const content = t.document.createElement('div');
  content.id = 'content';
  t.document.body.appendChild(content);
  const d = await openBatch(t);
  click(d.querySelector('[data-path="all"]'));
  await tick(80);
  t.be.lines[0].state = 'indexed'; t.be.lines[0].index_state = 'indexed'; t.be.lines[0].finished_at = '2026-10-20T14:20:00';
  t.be.lines[1].state = 'indexing';
  const automate = await import('/static/next/stages/automate.js');
  t.app.state.stage = 'automate';
  await automate.renderAutomate();
  // the first-indexing sub-tab
  click(content.querySelector('[data-automate-tab="first-index"]'));
  await tick(80);
  assert.match(text(content.querySelector('[data-fi-count]')), /^1 of 3 done$/);
  const rows = [...content.querySelectorAll('[data-fi-row]')].map((r) => [r.dataset.fiRow, r.dataset.fiState]);
  assert.deepEqual(rows, [['r1', 'indexed'], ['r2', 'indexing'], ['r3', 'waiting']]);
});
