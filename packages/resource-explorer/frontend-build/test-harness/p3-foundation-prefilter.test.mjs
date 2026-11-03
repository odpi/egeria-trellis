/** Behaviour (parity P3: PI-083): the repo search can be pre-filtered by foundation. A chip fills the Org and
 *  Topic the foundation is known by and runs the search; pressing it again clears what it filled. The list
 *  comes from `GET /api/discovery/foundations` (what the server holds). Real discovery-import.js; only `fetch`
 *  is replaced. Nothing is imported: a search is a read.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 80) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const FOUNDATIONS = {
  cncf: { label: 'CNCF', org: 'cncf' },
  apache: { label: 'Apache Software Foundation', org: 'apache' },
  lfai: { label: 'LF AI & Data', topic: 'lfai' },
};

async function open({ foundations = FOUNDATIONS, foundationsFail = false, searchFails = false } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    calls.push({ method, url: u, body: opts.body ? JSON.parse(opts.body) : undefined });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u === '/api/discovery/foundations') return foundationsFail ? { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'config unreadable' }) } : ok(foundations);
    if (u === '/api/projects/groups') return ok([]);
    if (u === '/api/discovery/search') {
      if (searchFails) return { ok: false, status: 502, statusText: 'x', json: async () => ({ detail: 'github is down' }) };
      return ok([{ full_name: 'apache/superset', html_url: 'https://github.com/apache/superset', description: 'bi', stars: 60000, language: 'Python', license: 'apache-2.0', already_registered: false, disposition: 'undecided' }]);
    }
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  await import('/static/next/app.js');
  const mod = await import(`/static/next/discovery-import.js?t=${Math.random()}`);
  await mod.openFindReposDialog();
  await wait();
  return { document, calls, window };
}
const searches = (calls) => calls.filter((c) => c.url === '/api/discovery/search');

test('PI-083: the search form lists the foundations the server holds, none pressed', async () => {
  const { document, calls } = await open();
  const chips = [...document.querySelectorAll('[data-foundation]')];
  assert.deepEqual(chips.map(text), ['CNCF', 'Apache Software Foundation', 'LF AI & Data']);
  assert.ok(chips.every((c) => c.getAttribute('aria-pressed') === 'false'));
  assert.ok(calls.some((c) => c.url === '/api/discovery/foundations'));
  assert.deepEqual(searches(calls), [], 'nothing is searched until one is pressed');
});

test('PI-083: pressing a foundation fills Org and Topic and runs the search with exactly those filters', async () => {
  const { document, calls } = await open();
  document.querySelector('[data-foundation="apache"]').click();
  await wait();
  assert.equal(document.querySelector('[data-f="org"]').value, 'apache');
  assert.equal(document.querySelector('[data-f="topic"]').value, '');
  const s = searches(calls);
  assert.equal(s.length, 1);
  assert.equal(s[0].body.org, 'apache');
  assert.equal(s[0].body.topic, '');
  const on = document.querySelector('[data-foundation="apache"]');
  assert.equal(on.getAttribute('aria-pressed'), 'true');
  assert.match(text(on), /^● Apache Software Foundation$/);
  assert.match(text(document.getElementById('wl-detail-body')), /Found 1 repo\(s\)\./);
  assert.ok(document.querySelector('[data-row]'), 'the result is in the review table');
  assert.deepEqual(calls.filter((c) => c.method !== 'GET' && c.url !== '/api/discovery/search'), [], 'nothing was imported');
});

test('PI-083: other typed filters are kept; a foundation with only a topic fills the topic', async () => {
  const { document, calls } = await open();
  const kw = document.querySelector('[data-f="keyword"]');
  kw.value = 'catalog';
  document.querySelector('[data-foundation="lfai"]').click();
  await wait();
  const s = searches(calls)[0];
  assert.equal(s.body.keyword, 'catalog');
  assert.equal(s.body.topic, 'lfai');
  assert.equal(s.body.org, '');
});

test('PI-083: pressing it again clears what it filled and searches nothing', async () => {
  const { document, calls } = await open();
  document.querySelector('[data-foundation="cncf"]').click();
  await wait();
  document.querySelector('[data-foundation="cncf"]').click();
  await wait();
  assert.equal(document.querySelector('[data-f="org"]').value, '');
  assert.equal(document.querySelector('[data-foundation="cncf"]').getAttribute('aria-pressed'), 'false');
  assert.equal(searches(calls).length, 1, 'the second press did not search');
});

test('PI-083: a failed search says so, and the foundation list that could not be read is "not read", not "none"', async () => {
  const a = await open({ searchFails: true });
  a.document.querySelector('[data-foundation="cncf"]').click();
  await wait();
  assert.match(text(a.document.getElementById('wl-detail-body')), /Search failed: github is down/);
  const b = await open({ foundationsFail: true });
  const note = b.document.querySelector('[data-foundations]');
  assert.match(text(note), /not read/);
  assert.match(text(note), /config unreadable/);
  assert.doesNotMatch(text(note), /none configured/);
  const c = await open({ foundations: {} });
  assert.match(text(c.document.querySelector('[data-foundations]')), /none configured/);
});
