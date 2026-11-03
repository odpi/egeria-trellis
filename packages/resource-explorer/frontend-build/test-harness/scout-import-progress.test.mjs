/** Scout import (2026-10-14): the press shows state at once, the rows follow the persisted batch, a second press
 *  cannot double-start, the sidebar list is re-read as repos finish, and a never-fetched repo reads "not yet surveyed".
 *  Real discovery-import.js / repo-header.js; only `fetch` is replaced.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const URLS = ['a', 'b', 'c'].map((n) => `https://github.com/DK/${n}`);
const repo = (u, reg = false) => ({ full_name: u.slice(19), html_url: u, description: '', stars: 5, language: 'Go', license: 'mit', already_registered: reg, disposition: 'undecided' });

async function open({ statusSeq, importBody }) {
  const { document, window } = makeDomEnvironment();
  const sb = document.createElement('div'); sb.id = 'sidebar'; document.body.appendChild(sb);
  const calls = [];
  let polls = 0;
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    calls.push({ method, url: u, body: opts.body ? JSON.parse(opts.body) : undefined });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u === '/api/discovery/search') return ok([repo(URLS[0]), repo(URLS[1]), repo(URLS[2], true)]);
    if (u === '/api/discovery/import') return ok(importBody);
    if (u.startsWith('/api/discovery/import/')) return ok(statusSeq[Math.min(polls++, statusSeq.length - 1)]);
    if (u === '/api/projects/groups' || u.startsWith('/api/projects')) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js'); api.clearCache();
  await import('/static/next/app.js');
  const mod = await import(`/static/next/discovery-import.js?t=${Math.random()}`);
  mod.importPollConfig.intervalMs = 20;
  await mod.openFindReposDialog(); await wait();
  document.querySelector('[data-f="keyword"]').value = 'DataKitchen';
  document.querySelector('[data-act="search"]').click(); await wait(150);
  return { document, calls };
}
const imports = (calls) => calls.filter((c) => c.method === 'POST' && c.url === '/api/discovery/import');
const sidebarReads = (calls) => calls.filter((c) => c.url.startsWith('/api/projects') && !c.url.includes('groups')).length;

test('search marks a registered repo with the registered cue', async () => {
  const { document } = await open({ statusSeq: [{}], importBody: {} });
  assert.ok(document.querySelector('[data-registered]'));
  assert.match(text(document.querySelector('[data-registered]')), /registered/);
});

test('press: button says Importing… and is disabled at once, rows say importing, then settle; sidebar re-read as repos finish', async () => {
  const running = (s) => ({ status: 'running', done: false, summary: '', repos: s });
  const seq = [
    running({ [URLS[0]]: { state: 'importing' }, [URLS[1]]: { state: 'importing' } }),
    running({ [URLS[0]]: { state: 'imported' }, [URLS[1]]: { state: 'importing' } }),
    { status: 'ok', done: true, summary: 'Import batch complete (x): 1 imported, 1 already registered, 0 failed, out of 2 requested',
      repos: { [URLS[0]]: { state: 'imported' }, [URLS[1]]: { state: 'already_registered' } } },
  ];
  const { document, calls } = await open({ statusSeq: seq, importBody: { queued: 2, skipped: [], in_flight: [], batch_id: 'B1' } });
  document.querySelectorAll('[data-row]')[0].click(); document.querySelectorAll('[data-row]')[1].click();
  const before = sidebarReads(calls);
  document.querySelector('[data-act="import-selected"]').click();
  await wait(5);
  const btn = document.querySelector('[data-act="import-selected"]');
  assert.equal(text(btn), 'Importing…'); assert.ok(btn.disabled);
  assert.deepEqual([...document.querySelectorAll('[data-import-state]')].map((e) => e.dataset.importState), ['importing', 'importing']);
  btn.click(); await wait(5);
  assert.equal(imports(calls).length, 1, 'a second press while in flight sends nothing');
  await wait(300);
  assert.deepEqual([...document.querySelectorAll('[data-import-state]')].map((e) => e.dataset.importState), ['imported', 'already_registered']);
  assert.equal(text(document.querySelector('[data-act="import-selected"]')), 'Import Selected');
  assert.ok(sidebarReads(calls) - before >= 2, 'sidebar list re-read when a repo finished and when the batch ended');
  assert.match(text(document.getElementById('wl-detail-body')), /1 imported, 1 already registered, 0 failed/);
});

test('repo with no GitHub figures reads "not yet surveyed", never zero', async () => {
  const { scoutingTilesHtml } = await import('/static/next/stages/repo-header.js');
  const html = scoutingTilesHtml({ stats_fetched: false, stats_unread: ['stars', 'forks', 'contributors', 'primary_language', 'last_pushed_at', 'repo_size_kb', 'security_and_analysis', 'deployments_count'], stars: 0, forks: 0, contributors_count: 0, repo_size_kb: 0 });
  assert.equal((html.match(/not yet surveyed/g) || []).length >= 6, true);
  assert.ok(!/>\s*0\s*</.test(html.replace(/<[^>]+>/g, (m) => m)), 'no bare zero tile');
});
