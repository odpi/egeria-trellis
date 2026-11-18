/** Behaviour: rebuilding a repository's chat index (BRIEF-REINDEX-FROM-SCRATCH.md, R2). The cue is drawn only for an
 *  admin; the ask shows the measured figures and queues only on "Rebuild"; a held first rebuild offers "Accept and
 *  swap"; undo and discard ask first, and discard names the tables it drops. Real module; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const ASK = { slug: 'rr', name: 'RR', refused: '', first: true, keep_days: 7,
  needs_words: 'needs about 12 MB more in the database (the current index\'s size, measured) until its previous index is dropped, 7 days after the swap',
  survey_words: 'survey results updated (not reverted by undo)',
  collections: [{ collection: 'rr_markdown_docs', chunks: 1200 }, { collection: 'rr_java_code', chunks: null }] };

async function setUp({ progress = { run_id: null }, ask = ASK, refuse = 0 } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null, headers: options.headers || {} });
    if (refuse) return { ok: false, status: refuse, json: async () => ({ detail: 'Admin credential required' }) };
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/reindex/progress')) return ok(progress);
    if (u.startsWith('/api/reindex/ask')) return ok(ask);
    if (u === '/api/reindex/queue') return ok({ run_id: 'run-1', held: true });
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.sessionStorage = window.sessionStorage;
  window.sessionStorage.setItem('re_admin_token', 'tok');
  const mod = await import(`/static/next/reindex.js?t=${Math.random()}`);
  return { document, mod, calls };
}

test('nothing is drawn for a person who is not an admin', async () => {
  const { document, mod } = await setUp({ refuse: 403 });
  const host = document.createElement('span');
  host.setAttribute('data-reindex-host', '');
  document.body.appendChild(host);
  assert.equal(await mod.refreshReindexCue('rr'), null);
  assert.equal(host.innerHTML, '');
});

test('an admin with no rebuild sees the door, and the read carries the admin token', async () => {
  const { document, mod, calls } = await setUp();
  const host = document.createElement('span');
  host.setAttribute('data-reindex-host', '');
  document.body.appendChild(host);
  await mod.refreshReindexCue('rr');
  assert.match(text(host), /rebuild from scratch ›/);
  assert.equal(calls[0].headers['X-Admin-Token'], 'tok');
});

test('the cue says where a rebuild has got to', async () => {
  const { mod } = await setUp();
  const tmp = (h) => { const d = globalThis.document.createElement('div'); d.innerHTML = h; return text(d); };
  assert.match(tmp(mod.reindexCueHtml({ run_id: 'r', phase: 'building' })), /rebuild building aside/);
  assert.match(tmp(mod.reindexCueHtml({ run_id: 'r', phase: 'awaiting_accept' })), /rebuild built · accept ›/);
  assert.match(tmp(mod.reindexCueHtml({ run_id: 'r', phase: 'done', undo_available: true, previous_until: '2026-11-17T10:00:00+00:00' })),
    /rebuilt · undo until 2026-11-17/);
  assert.match(tmp(mod.reindexCueHtml({ run_id: 'r', phase: 'failed', outcomes: [{ words: 'disk' }] })), /rebuild failed/);
});

test('the ask shows the measured figures and queues only on Rebuild', async () => {
  const { document, mod, calls } = await setUp();
  const el = await mod.openReindexDialog('rr');
  await tick();
  const body = el.querySelector('#wl-detail-body');
  assert.match(text(body), /rr_markdown_docs · 1,200 chunk\(s\)/);
  assert.match(text(body), /rr_java_code · chunk count not read/);           // unread is said, never 0
  assert.match(text(body), /needs about 12 MB more/);
  assert.match(text(body), /first rebuild: it is held after building/);
  assert.match(text(body), /not reverted by undo/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  body.querySelector('[data-reindex-yes]').click();
  await tick();
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual([post.url, post.body], ['/api/reindex/queue', { slug: 'rr' }]);
  assert.match(text(body.querySelector('[data-reindex-note]')), /wait for you to accept/);
  assert.ok(document);
});

test('a refused ask says why and offers nothing', async () => {
  const { mod } = await setUp({ ask: { slug: 'rr', refused: 'RR already has a rebuild queued, running, or waiting to be accepted.' } });
  const el = await mod.openReindexDialog('rr');
  await tick();
  const body = el.querySelector('#wl-detail-body');
  assert.match(text(body), /already has a rebuild queued/);
  assert.equal(body.querySelector('[data-reindex-yes]'), null);
});

test('a held first rebuild offers Accept and swap', async () => {
  const { mod, calls } = await setUp({ progress: { run_id: 'run-9', phase: 'awaiting_accept',
    outcomes: [{ phase: 'built', words: 'built: rr_markdown_docs: 2 → 3' }] } });
  const el = await mod.openReindexDialog('rr');
  await tick();
  const body = el.querySelector('#wl-detail-body');
  assert.match(text(body), /built: rr_markdown_docs: 2 → 3/);
  body.querySelector('[data-reindex-accept]').click();
  await tick();
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/reindex/run-9/accept'));
});

test('a held first rebuild can be cancelled, after asking first', async () => {
  const { mod, calls } = await setUp({ progress: { run_id: 'run-9', phase: 'awaiting_accept',
    outcomes: [{ phase: 'built', words: 'built: rr_markdown_docs: 2 → 3' }] } });
  const el = await mod.openReindexDialog('rr');
  await tick();
  const body = el.querySelector('#wl-detail-body');
  body.querySelector('[data-reindex-cancel]').click();
  assert.match(text(body.querySelector('[data-reindex-ask-first]')), /Chat keeps the index it has now, which was never changed/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0, 'nothing until it is confirmed');
  body.querySelector('[data-reindex-confirm-yes]').click();
  await tick();
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/reindex/run-9/cancel'));
});

test('undo and discard ask first, and discard names the tables it drops', async () => {
  const { mod, calls } = await setUp({ progress: { run_id: 'run-9', phase: 'done', undo_available: true,
    previous_until: '2026-11-17T10:00:00+00:00', previous_tables: ['rr_markdown_docs__previous', 'rr_java_code__previous'],
    outcomes: [{ phase: 'done', words: 'rebuilt; undo available until 2026-11-17' }] } });
  const el = await mod.openReindexDialog('rr');
  await tick();
  const body = el.querySelector('#wl-detail-body');
  body.querySelector('[data-reindex-discard]').click();
  assert.match(text(body.querySelector('[data-reindex-ask-first]')),
    /Discard drops 2 tables \(rr_markdown_docs__previous, rr_java_code__previous\); undo will no longer be possible/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0, 'nothing until it is confirmed');
  body.querySelector('[data-reindex-confirm-yes]').click();
  await tick();
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/reindex/rr/discard-previous'));
  body.querySelector('[data-reindex-undo]').click();
  assert.match(text(body.querySelector('[data-reindex-ask-first]')), /survey results stay as the rebuild left them/);
  body.querySelector('[data-reindex-confirm-yes]').click();
  await tick();
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/reindex/rr/undo'));
});

test('the dialog is resizable, as every pane is', async () => {
  const { mod } = await setUp();
  const el = await mod.openReindexDialog('rr');
  await tick();
  assert.match(el.querySelector('[role="dialog"]').getAttribute('style') || '', /resize:horizontal/);
});

test('a 403 in the dialog says it is for admins', async () => {
  const { mod } = await setUp({ refuse: 403 });
  const el = await mod.openReindexDialog('rr');
  await tick();
  assert.match(text(el.querySelector('#wl-detail-body')), /Rebuilding is for admins/);
});
