/** E2 (ENRICHMENT-E2-DOC-SOURCES-RESEAT) regression tests, real jsdom
 *  renders, one group per gate item:
 *   1. repo Context mounts the doc-sources block; signed row w/ facts
 *   2. timed_out / unreachable / blocked wording
 *   3. ingest fact reads "ingestion not built yet" (== Survey & analyses)
 *   4. Egeria fact wording per state, never blank
 *   5. remove: ordinary control, confirms once, row gone; glyphs via glyphs.js
 *   6. E1 not regressed on a repo (signed rows, feeds, headings)
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

function src(over = {}) {
  return {
    id: 's1', entity_type: 'repo', entity_slug: 'amundsen', url: 'https://docs.example/a',
    label: 'Amundsen docs', source_type: 'wiki', added_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    added_by: 'dan', origin: 'local', probe_state: 'reachable', probe_status_code: 200,
    probe_ms: 180, probe_error: '', probed_at: new Date().toISOString(),
    egeria_external_ref_guid: '', egeria_state: 'local_only', ...over,
  };
}
function payload(sources) { return { sources, published: false, publish_note: '' }; }

async function setUp(kind = 'repo', slug = 'amundsen') {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const app = await import('/static/next/app.js');
  const enrichment = await import(`/static/next/stages/enrichment.js?t=${Date.now()}_${Math.random()}`);
  app.state.resourceType = kind;
  app.state.selectedSlug = slug;
  const host = document.createElement('div');
  host.id = 'doc-sources-block';
  document.body.appendChild(host);
  return { document, app, enrichment, host };
}
function stub(bodyOrFn) {
  globalThis.fetch = async (url, opts) => {
    const body = typeof bodyOrFn === 'function' ? bodyOrFn(String(url), opts) : bodyOrFn;
    return { ok: true, status: 200, json: async () => body };
  };
}

// ── gate 1: repo Context mounts the block, signed row with all facts ──────
test('gate 1: a repo resource\'s Context mounts "Where it\'s documented" with a signed row, facts and feeds line', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'amundsen';
  app.state.investigation = ''; app.state.investigations = []; app.state.questions = [];
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';
  globalThis.fetch = async (url) => {
    const u = String(url);
    let body;
    if (u.includes('/api/doc-sources/repo/amundsen')) body = payload([src()]);
    else if (u.includes('/api/context/')) body = { enrichment: {}, question_answers: {} };
    else throw new Error(`unstubbed ${u}`);
    return { ok: true, status: 200, json: async () => body };
  };
  await renderContext('amundsen');
  await new Promise((r) => setTimeout(r, 20));
  const html = document.getElementById('context-form').textContent;
  assert.match(html, /Where it's documented/);
  assert.match(html, /sources, not answers/);
  const row = document.querySelector('[data-source-row="s1"]');
  assert.ok(row, 'source row must render on a repo');
  assert.match(row.textContent, /added by dan · /);
  assert.match(row.textContent, /reachable/);
  assert.match(row.textContent, /ingestion not built yet/);
  assert.match(row.textContent, /local only/);
  assert.match(row.textContent, /feeds →/);
});

test('gate 1: the signature is its own line (personRowLineHtml), not on the probe line; unsigned rows say "unknown"', async () => {
  const { enrichment, host } = await setUp();
  stub(payload([src(), src({ id: 's2', added_by: '' })]));
  await enrichment.renderDocSources('amundsen');
  const sig = host.querySelector('[data-doc-signature="s1"]');
  assert.match(sig.textContent, /added by dan · \d+d ago/);
  assert.doesNotMatch(host.querySelector('[data-doc-probe-line="s1"]').textContent, /added by/);
  assert.match(host.querySelector('[data-doc-signature="s2"]').textContent, /added by unknown · /);
});

// ── gate 2: probe wording ─────────────────────────────────────────────────
test('gate 2: timed_out reads "timed out · <seconds> · re-check", never "blocked"', async () => {
  const { enrichment, host } = await setUp();
  stub(payload([src({ probe_state: 'timed_out', probe_status_code: null, probe_ms: 4012, probe_error: 'timed out: x' })]));
  await enrichment.renderDocSources('amundsen');
  const line = host.querySelector('[data-doc-probe-line="s1"]').textContent;
  assert.match(line, /timed out · 4\.0s · re-check/);
  assert.doesNotMatch(line, /blocked/);
});

test('gate 2: unreachable reads "unreachable · <reason> · re-check"; blocked says "by the site" and carries the status', async () => {
  const { enrichment, host } = await setUp();
  stub(payload([
    src({ id: 'u', probe_state: 'unreachable', probe_status_code: null, probe_error: 'connection refused' }),
    src({ id: 'b', probe_state: 'blocked', probe_status_code: 503, probe_error: 'HTTP 503' }),
  ]));
  await enrichment.renderDocSources('amundsen');
  assert.match(host.querySelector('[data-doc-probe-line="u"]').textContent, /unreachable · connection refused · re-check/);
  assert.doesNotMatch(host.querySelector('[data-doc-probe-line="u"]').textContent, /blocked/);
  assert.match(host.querySelector('[data-doc-probe-line="b"]').textContent, /blocked by the site · HTTP 503/);
});

test('gate 2: all three ? states share the not_established glyph; sign-in/not-found/reachable/unprobed use glyphs.js states', async () => {
  const { enrichment, host } = await setUp();
  const states = ['reachable', 'needs_sign_in', 'not_found', 'blocked', 'timed_out', 'unreachable', ''];
  stub(payload(states.map((st, i) => src({ id: `g${i}`, probe_state: st, probed_at: st ? '2026-09-28T00:00:00Z' : '' }))));
  await enrichment.renderDocSources('amundsen');
  const glyphOf = (i) => host.querySelector(`[data-doc-probe-glyph="g${i}"] span`);
  const expected = ['✓', '◐', '✕', '?', '?', '?', '○'];
  expected.forEach((g, i) => {
    assert.equal(glyphOf(i).textContent, g, `state '${states[i]}'`);
    assert.ok(glyphOf(i).classList.contains('font-glyph'), 'rendered by glyphs.js glyphSpan (pinned font)');
    assert.ok(glyphOf(i).getAttribute('aria-label'), 'glyphSpan gives an aria-label');
  });
  assert.doesNotMatch(host.innerHTML, /●/, 'the off-vocabulary ● is gone');
  assert.match(host.querySelector('[data-doc-probe-line="g6"]').textContent, /not probed yet/);
});

// ── gate 3: ingest fact wording matches Survey & analyses ─────────────────
test('gate 3: every row\'s ingest fact reads "ingestion not built yet", the same words as Survey & analyses', async () => {
  const { enrichment, host } = await setUp();
  stub(payload([src(), src({ id: 's2', probe_state: 'timed_out' })]));
  await enrichment.renderDocSources('amundsen');
  for (const id of ['s1', 's2']) {
    assert.equal(host.querySelector(`[data-doc-ingest="${id}"]`).textContent.trim(), 'ingestion not built yet');
  }
  assert.equal(host.querySelectorAll('button:disabled').length, 0, 'no disabled "coming soon" button any more');
  // Same words as the analyses map (app.js: "ingestion not built yet")
  const fs = await import('node:fs');
  const appSrc = fs.readFileSync(new URL('../../resource_explorer/web/static/next/app.js', import.meta.url), 'utf8');
  assert.match(appSrc, /'ingestion'[^\n]*not built yet/);
});

// ── gate 4: Egeria fact wording, never blank ──────────────────────────────
test('gate 4: the Egeria fact is never blank and each state has its own words', async () => {
  const { enrichment, host } = await setUp();
  stub(payload([
    src({ id: 'a', egeria_state: 'local_only' }),
    src({ id: 'b', egeria_state: 'not_catalogued' }),
    src({ id: 'c', egeria_state: 'publishing' }),
    src({ id: 'd', egeria_state: 'catalogued', egeria_state_detail: 'ref-1' }),
    src({ id: 'e' , egeria_state: undefined }),
  ]));
  await enrichment.renderDocSources('amundsen');
  const t = (id) => host.querySelector(`[data-doc-egeria-state="${id}"]`).textContent.trim();
  assert.match(t('a'), /^local only/);
  assert.equal(t('b'), 'local — not published (publish needed)');
  assert.equal(t('c'), 'local — publishing…');
  assert.equal(t('d'), 'published in Egeria');
  assert.ok(t('e').length > 0, 'a row with no egeria_state still reads "local", never blank');
});

// ── gate 5: remove ────────────────────────────────────────────────────────
test('gate 5: remove is an ordinary control (no state colour), asks once, and the row is gone after', async () => {
  const { enrichment, host } = await setUp();
  let removed = false; let deletes = 0;
  stub((url, opts) => {
    if (opts && opts.method === 'DELETE') { removed = true; deletes += 1; return { ok: true }; }
    return payload(removed ? [] : [src()]);
  });
  const kit = autoAnswerConfirm(document, { answer: false });
  await enrichment.renderDocSources('amundsen');
  const btn = () => host.querySelector('[data-doc-remove="s1"]');
  assert.ok(btn().className.includes('text-accent-ink'));
  assert.ok(!btn().className.includes('text-state-warn'), 'remove must not carry a state colour');
  assert.equal(btn().className, host.querySelector('[data-doc-recheck="s1"]').className, 'same styling as re-check');

  btn().click();                                   // decline
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(kit.asked.length, 1); assert.equal(deletes, 0, 'declined confirm must not delete');
  assert.ok(host.querySelector('[data-source-row="s1"]'));

  kit.stop();
  const yes = autoAnswerConfirm(document, { answer: true, asked: kit.asked });
  btn().click();                                   // accept
  await new Promise((r) => setTimeout(r, 30));
  yes.stop();
  assert.equal(kit.asked.length, 2); assert.equal(deletes, 1, 'exactly one delete request');
  assert.equal(host.querySelector('[data-source-row="s1"]'), null, 'row must be gone after removal');
});

// ── gate 6: repo backend entity type is what the block calls ──────────────
test('gate 6: on a repo the block calls /api/doc-sources/repo/<slug> (not database)', async () => {
  const { enrichment } = await setUp();
  const urls = [];
  stub((url) => { urls.push(url); return payload([]); });
  await enrichment.renderDocSources('amundsen');
  assert.ok(urls.some((u) => u.includes('/api/doc-sources/repo/amundsen')), urls.join(','));
});
