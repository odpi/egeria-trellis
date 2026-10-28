/** Behaviour: PI-050/051 (scouting tiles and signal in the repository header) and PI-052 (the stale-link
 *  banner and its three repairs). Real app.js header, real repo-header.js; only fetch is stubbed.
 *
 *  What must hold: a stat GitHub was never asked for reads "not read", never 0, while a stored 0 stays 0;
 *  "no scouting results yet" and "could not read" differ; the banner offers exactly three repairs, each
 *  posting to the one resolve route; no repair reaches a delete route; a write choice asks first. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const OV = {
  slug: 'r1', display_name: 'R1', homepage: 'https://www.example.org/docs', primary_language: 'Python',
  stars: 0, forks: 12, contributors_count: 3, last_pushed_at: '', repo_size_kb: 2048,
  security_and_analysis: { secret_scanning: { status: 'enabled' }, dependabot: { status: 'disabled' } },
  deployments_count: 0, stats_unread: ['last_pushed_at'], egeria_link_stale: false,
};

function backend({ resolveFails = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.endsWith('/resolve')) {
      return resolveFails
        ? { ok: false, status: 502, statusText: 'bad', json: async () => ({ detail: 'Link cleared, but republish failed' }) }
        : ok({ status: 'ok', next_step: 'Re-created in Egeria as SurveyReport g1.' });
    }
    if (u === '/api/egeria/linkage/stale') return ok([]);
    if (u.includes('/scouting-overview')) return ok({ ...OV, egeria_link_stale: false });
    return ok({});
  };
  return calls;
}

async function setUp(opts = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = backend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const asked = [];
  autoAnswerConfirm(document, { answer: opts.confirm !== false, asked });
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'repo';
  app.state.projects = [{ slug: 'r1', display_name: 'R1', github_url: 'https://github.com/o/r1', is_published: true, disposition: 'undecided' }];
  app.state.selectedSlug = 'r1';
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  const host = document.createElement('div');
  host.id = 'resource-header';
  document.body.appendChild(host);
  const render = () => { host.innerHTML = app.resourceHeaderHtml('r1'); app.bindResourceHeader(); };
  return { document, window, app, calls, asked, host, render };
}

const tileText = (host, key) => text(host.querySelector(`[data-scouting-tile="${key}"]`));

test('a stat that was never read says "not read"; a stored zero stays 0', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV };
  t.render();
  assert.match(tileText(t.host, 'stars'), /0$/);
  assert.equal(t.host.querySelector('[data-scouting-tile="stars"] [data-unread]'), null);
  assert.match(tileText(t.host, 'last_pushed'), /not read/);
  assert.doesNotMatch(tileText(t.host, 'last_pushed'), /\b0\b/);
  assert.match(tileText(t.host, 'forks'), /12/);
  assert.match(tileText(t.host, 'size'), /2\.0 MB/);
  assert.match(tileText(t.host, 'security'), /1\/2 enabled/);
  assert.match(tileText(t.host, 'website'), /example\.org/);
  assert.equal(t.host.querySelectorAll('[data-scouting-tile]').length, 9);
});

test('no stats at all: every numeric tile reads "not read", none reads 0', async () => {
  const t = await setUp();
  t.app.state.overview = { slug: 'r1', stars: 0, forks: 0, contributors_count: 0, repo_size_kb: 0, deployments_count: 0,
    primary_language: '', last_pushed_at: '', security_and_analysis: {}, homepage: '',
    stats_unread: ['primary_language', 'stars', 'forks', 'contributors', 'last_pushed_at', 'repo_size_kb', 'security_and_analysis', 'deployments_count'] };
  t.render();
  for (const k of ['language', 'stars', 'forks', 'contributors', 'last_pushed', 'size', 'security', 'deployments']) {
    assert.match(tileText(t.host, k), /not read/, k);
  }
  assert.match(tileText(t.host, 'website'), /none found/);
});

test('an overview with no stats_unread (older server) is not read, not zero', async () => {
  const t = await setUp();
  const { stats_unread, ...old } = OV;
  t.app.state.overview = old;
  t.render();
  assert.match(tileText(t.host, 'stars'), /not read/);
});

test('the signal row tells "none yet" from "could not read"', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV };
  t.app.state.scoutingSignal = { slug: 'r1', tiles: [] };
  t.render();
  assert.match(text(t.host.querySelector('[data-scouting-signal]')), /no scouting results yet/);
  t.app.state.scoutingSignal = { slug: 'r1', error: 'HTTP 500' };
  t.render();
  assert.match(text(t.host.querySelector('[data-scouting-signal]')), /could not read/);
  t.app.state.scoutingSignal = { slug: 'r1', tiles: [{ label: '12 languages', status: 'ok', analysis_name: 'Languages' }] };
  t.render();
  assert.equal(t.host.querySelector('[data-signal-chip="ok"]').textContent, '12 languages');
});

test('a database header has no scouting tiles', async () => {
  const t = await setUp();
  t.app.state.resourceType = 'db';
  t.app.state.databases = [{ slug: 'r1', display_name: 'D', disposition: 'undecided' }];
  t.app.state.overview = { ...OV };
  t.render();
  assert.equal(t.host.querySelector('[data-scouting-tiles]'), null);
});

test('no banner while the link is fine', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV };
  t.render();
  assert.equal(t.host.querySelector('[data-stale-link-banner]'), null);
});

test('a stale link shows the banner with exactly three repairs and the post-reset word', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV, egeria_link_stale: true, egeria_link_stale_guid: 'dead-guid', published_state: 'published_earlier', last_published_at: '2026-10-01T00:00:00Z' };
  t.render();
  const banner = t.host.querySelector('[data-stale-link-banner]');
  assert.ok(banner);
  assert.match(text(banner), /link stale/);
  assert.match(text(banner), /published earlier/);
  assert.match(text(banner), /dead-guid/);
  assert.deepEqual([...banner.querySelectorAll('[data-link-repair]')].map((b) => b.dataset.linkRepair), ['republish', 'resurvey', 'discard']);
  assert.ok(!/delete|archive|remove/i.test(banner.innerHTML.replace(/title="[^"]*"/g, '')), 'no destructive word on a control');
});

test('each repair posts one action to the one resolve route and never to a delete route', async () => {
  for (const action of ['republish', 'resurvey', 'discard']) {
    const t = await setUp();
    t.app.state.overview = { ...OV, egeria_link_stale: true };
    t.render();
    t.host.querySelector(`[data-link-repair="${action}"]`).click();
    await tick();
    const posts = t.calls.filter((c) => c.method === 'POST');
    assert.equal(posts.length, 1, action);
    assert.equal(posts[0].url, '/api/egeria/linkage/repo/r1/resolve');
    assert.deepEqual(posts[0].body, { action });
    assert.ok(!t.calls.some((c) => /delete|archive/.test(c.url)), 'no delete route');
    // the header re-reads the overview afterwards, drawn from the re-read (link now fine)
    assert.equal(t.host.querySelector('[data-stale-link-banner]'), null, `${action}: banner gone after re-read`);
  }
});

test('every choice asks first, including Forget the link; declining sends nothing', async () => {
  const t = await setUp({ confirm: false });
  t.app.state.overview = { ...OV, egeria_link_stale: true };
  t.render();
  for (const a of ['republish', 'resurvey', 'discard']) { t.host.querySelector(`[data-link-repair="${a}"]`).click(); await tick(); }
  assert.equal(t.asked.length, 3);
  assert.match(t.asked[2], /Discard link/);
  assert.match(t.asked[2], /Writes nothing to Egeria/);
  assert.equal(t.calls.filter((c) => c.method === 'POST').length, 0);
});

test('confirming Forget the link sends the one discard post', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV, egeria_link_stale: true };
  t.render();
  t.host.querySelector('[data-link-repair="discard"]').click();
  await tick();
  assert.equal(t.asked.length, 1);
  assert.equal(t.calls.filter((c) => c.method === 'POST').length, 1);
});

test('a failed repair re-reads the header, so the banner shows the state that is really there (link already cleared)', async () => {
  const t = await setUp({ resolveFails: true });
  t.app.state.overview = { ...OV, egeria_link_stale: true };
  t.render();
  const before = t.calls.filter((c) => c.url.includes('/scouting-overview')).length;
  t.host.querySelector('[data-link-repair="republish"]').click();
  await tick();
  assert.equal(t.calls.filter((c) => c.url.includes('/scouting-overview')).length, before + 1, 'overview re-read after the failure');
  assert.equal(t.host.querySelector('[data-stale-link-banner]'), null, 'no banner offering a second press that would 404');
  const note = text(t.host.querySelector('[data-repair-failed]'));
  assert.match(note, /republish failed/);
  assert.match(note, /already forgotten/);
});

test('size and deployments that the server lists as unread never draw 0 or 0.0 MB', async () => {
  const t = await setUp();
  t.app.state.overview = { ...OV, repo_size_kb: 0, deployments_count: 0, stats_unread: ['repo_size_kb', 'deployments_count'] };
  t.render();
  for (const k of ['size', 'deployments']) {
    assert.match(tileText(t.host, k), /not read/, k);
    assert.doesNotMatch(tileText(t.host, k), /0\.0 MB|\b0\b/, k);
  }
});
