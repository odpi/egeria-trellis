/** Real-DOM regression tests for the Documentation sources block on the
 *  Enrichment stage (BRIEF-DATABASE-DOCUMENTATION-SOURCES.md slice 1,
 *  "Declare and probe" -- resource_explorer/web/static/next/stages/
 *  enrichment.js's `renderDocSources`).
 *
 *  Per the project's 2026-09 harness-regression rule ("every /next fix
 *  from here on adds its regression to the harness, not only a source-text
 *  test" -- also applies to new features), this is a real jsdom render,
 *  not a string match against enrichment.js's source.
 *
 *  `renderDocSources` fetches from `/api/doc-sources/...` via re-api.js's
 *  `getDocSources`, so `globalThis.fetch` is stubbed per test to return
 *  fixture JSON keyed by URL -- the harness's documented pattern (dom-
 *  harness.mjs's header comment: "if a test needs a network response, stub
 *  globalThis.fetch before calling into app.js").
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

function stubFetchJson(responsesByUrlSubstring) {
  globalThis.fetch = async (url) => {
    const key = Object.keys(responsesByUrlSubstring).find((k) => String(url).includes(k));
    if (!key) {
      throw new Error(`_UNSTUBBED_FETCH in doc-sources test: no fixture for ${url}`);
    }
    const body = responsesByUrlSubstring[key];
    return {
      ok: true,
      status: 200,
      json: async () => body,
    };
  };
}

function docSourcesFixture(overrides = {}) {
  return {
    sources: [
      {
        id: 'src1', entity_type: 'database', entity_slug: 'adventureworks',
        url: 'https://docs.example/dictionary', label: 'Data dictionary',
        source_type: 'data_dictionary', added_at: '2026-09-28T00:00:00Z', added_by: 'dan',
        origin: 'local', probe_state: 'reachable', probe_status_code: 200,
        probe_ms: 180, probe_title: 'AdventureWorks Data Dictionary', probe_byte_count: 4096,
        probe_error: '', probed_at: '2026-09-28T00:00:05Z', egeria_external_ref_guid: '',
      },
    ],
    published: false,
    publish_note: '',
    ...overrides,
  };
}

async function setUpEnrichmentDom() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  // enrichment.js imports `state` from the PLAIN '/static/next/app.js'
  // specifier (no cache-bust query) -- dom-harness's own loadAppModule()
  // always cache-busts (`?t=...`), which is right for isolating app.js's
  // *own* tests from each other, but it means the module it returns is a
  // DIFFERENT instance than the one enrichment.js's static import resolves
  // to, so mutating ITS `state` would never be seen by renderDocSources.
  // Importing the plain specifier here gets the exact instance
  // enrichment.js itself imports.
  const app = await import('/static/next/app.js');
  const enrichment = await import(`/static/next/stages/enrichment.js?t=${Date.now()}_${Math.random()}`);

  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';

  const host = document.createElement('div');
  host.id = 'doc-sources-block';
  document.body.appendChild(host);

  return { document, app, enrichment, host };
}

test('a declared source renders its reachable state, HTTP status and fetch time', async () => {
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': docSourcesFixture() });

  await enrichment.renderDocSources('adventureworks');

  assert.match(host.textContent, /Data dictionary/);
  assert.match(host.textContent, /reachable/);
  assert.match(host.textContent, /HTTP 200/);
  assert.match(host.textContent, /180ms/);
  assert.match(host.textContent, /local only/, 'unpublished resource must say "local only"');
});

test('needs_sign_in and not_found states render their own glyph/label, not "reachable"', async () => {
  const fixture = docSourcesFixture({
    sources: [
      { id: 's1', url: 'https://a', label: 'Needs sign-in', source_type: 'wiki', added_at: '',
        origin: 'local', probe_state: 'needs_sign_in', probe_status_code: 401, probe_ms: 50,
        probe_title: '', probe_byte_count: 0, probe_error: '', probed_at: '2026-09-28T00:00:00Z',
        egeria_external_ref_guid: '' },
      { id: 's2', url: 'https://b', label: 'Gone', source_type: 'runbook', added_at: '',
        origin: 'local', probe_state: 'not_found', probe_status_code: 404, probe_ms: 30,
        probe_title: '', probe_byte_count: 0, probe_error: '', probed_at: '2026-09-28T00:00:00Z',
        egeria_external_ref_guid: '' },
    ],
  });
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': fixture });

  await enrichment.renderDocSources('adventureworks');

  assert.match(host.textContent, /reachable behind a sign-in/);
  assert.match(host.textContent, /HTTP 401/);
  assert.match(host.textContent, /not found/);
  assert.match(host.textContent, /HTTP 404/);
});

test('a published, cataloged source shows no "local only" note, and an unpublished one always does', async () => {
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({
    '/api/doc-sources/database/adventureworks': docSourcesFixture({
      published: true,
      sources: [{
        ...docSourcesFixture().sources[0],
        egeria_external_ref_guid: 'ref-guid-1', egeria_state: 'catalogued',
        egeria_state_detail: 'ref-guid-1',
      }],
    }),
  });

  await enrichment.renderDocSources('adventureworks');

  assert.doesNotMatch(host.textContent, /local only/);
  assert.match(host.textContent, /published in Egeria/);
});

test('a stale-linkage publish note is shown verbatim instead of the generic "local only" copy', async () => {
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({
    '/api/doc-sources/database/adventureworks': docSourcesFixture({
      published: false,
      publish_note: 'published to Egeria · link stale since 2026-09-22 — element not found',
    }),
  });

  await enrichment.renderDocSources('adventureworks');

  assert.match(host.textContent, /link stale since 2026-09-22/);
});

test('the re-check button re-fetches and the row reflects the NEW probe state after a second render', async () => {
  // This is the harness's whole reason for existing (see its header
  // comment): assert what survives a SECOND render, not just the first.
  // NOTE: fetch is stubbed AFTER setUpEnrichmentDom(), not before --
  // makeDomEnvironment() (called inside it) installs its own loudly-failing
  // fetch stub, which would otherwise clobber a stub set earlier.
  const { enrichment, host, document } = await setUpEnrichmentDom();

  let call = 0;
  globalThis.fetch = async () => {
    call += 1;
    const body = call === 1
      ? docSourcesFixture()
      : docSourcesFixture({
          sources: [{
            ...docSourcesFixture().sources[0],
            probe_state: 'blocked', probe_status_code: 503, probe_error: 'HTTP 503',
          }],
        });
    return { ok: true, status: 200, json: async () => body };
  };

  await enrichment.renderDocSources('adventureworks');
  assert.match(host.textContent, /reachable/);

  const recheckBtn = host.querySelector('[data-doc-recheck]');
  assert.ok(recheckBtn, 'expected a re-check button on the declared source row');
  // The click handler calls recheckDocSource() (a POST) then
  // renderDocSources() again -- stub the POST leg too.
  globalThis.fetch = async (url, opts) => {
    if (opts && opts.method === 'POST') return { ok: true, status: 200, json: async () => ({}) };
    call += 1;
    return {
      ok: true, status: 200,
      json: async () => docSourcesFixture({
        sources: [{
          ...docSourcesFixture().sources[0],
          probe_state: 'blocked', probe_status_code: 503, probe_error: 'HTTP 503',
        }],
      }),
    };
  };
  recheckBtn.dispatchEvent(new document.defaultView.Event('click', { bubbles: true }));
  // Allow the async click handler's microtasks (recheck POST + re-render) to settle.
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.match(host.textContent, /blocked/);
  assert.match(host.textContent, /HTTP 503/);
});

test('the source-kind dropdown offers all nine declared kinds, including the 2026-09-29 additions', async () => {
  // Owner gate feedback on 8813 (2026-09-29) added Installation Guide and
  // User Manual; design added API Reference and Release Notes while
  // touching the list. This pins the full <select> option set so it can't
  // silently regress to the original five.
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': docSourcesFixture() });

  await enrichment.renderDocSources('adventureworks');

  const select = host.querySelector('#doc-source-type');
  assert.ok(select, 'expected the source-kind <select>');
  const options = [...select.querySelectorAll('option')].map((o) => o.value);
  assert.deepEqual(options, [
    'data_dictionary', 'design_notes', 'runbook', 'wiki',
    'installation_guide', 'user_manual', 'api_reference', 'release_notes',
    'other',
  ]);

  const labels = [...select.querySelectorAll('option')].map((o) => o.textContent);
  assert.deepEqual(labels, [
    'data dictionary', 'design notes', 'runbook', 'wiki',
    'installation guide', 'user manual', 'api reference', 'release notes',
    'other',
  ]);
});

test('each of the four Egeria publish-state rows renders its own required wording (2026-09-29 fix)', async () => {
  // The bug this pins against: previously EVERY published resource rendered
  // an empty publishNote regardless of whether a given source had actually
  // reached Egeria -- "1 declared ·" followed by nothing, with no way to
  // tell a catalogued source from a stuck one. Each state below must render
  // distinguishable, non-empty text.
  const base = docSourcesFixture().sources[0];
  const fixture = docSourcesFixture({
    published: true,
    in_egeria_count: 1,
    local_count: 3,
    sources: [
      { ...base, id: 'cataloged-1', label: 'Cataloged source',
        egeria_external_ref_guid: 'ref-guid-42', egeria_state: 'catalogued',
        egeria_state_detail: 'ref-guid-42' },
      { ...base, id: 'publishing-1', label: 'Publishing source',
        egeria_external_ref_guid: '', egeria_state: 'publishing', egeria_state_detail: '' },
      { ...base, id: 'failed-1', label: 'Failed source',
        egeria_external_ref_guid: '', egeria_state: 'publish_failed',
        egeria_state_detail: 'Egeria unreachable: connection refused' },
      { ...base, id: 'localonly-1', label: 'Local-only source',
        egeria_external_ref_guid: '', egeria_state: 'local_only', egeria_state_detail: '' },
    ],
  });
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': fixture });

  await enrichment.renderDocSources('adventureworks');

  assert.match(host.textContent, /published in Egeria/, 'state 1: cataloged');
  assert.match(host.textContent, /local — publishing…/, 'state 2: publishing');
  assert.match(
    host.textContent,
    /local — publish failed: Egeria unreachable: connection refused, retrying/,
    'state 3: publish_failed must surface the REAL reason, not a generic message',
  );
  assert.match(host.textContent, /local only — resource not published/, 'state 4: local_only');

  // The ref GUID is surfaced but not cluttering the row's own text -- the
  // catalogued row carries it in a title attribute instead.
  const catalogRow = host.querySelector('[data-source-row="cataloged-1"]');
  assert.ok(catalogRow, 'expected the cataloged row in the DOM');
  const titled = catalogRow.querySelector('[title*="ref-guid-42"]');
  assert.ok(titled, 'expected the ref guid surfaced via a title attribute near the cataloged row');

  // Header counts the real per-row states, not a single published boolean.
  assert.match(host.textContent, /4.*declared/s);
  assert.match(host.textContent, /1.*in Egeria/s);
  assert.match(host.textContent, /3.*local/s);
});

test('the not_catalogued state (round 4, 2026-09-29) renders its own honest wording, never "publishing"', async () => {
  // Adoption-race fix: a row with a ref guid but no link guid and nothing
  // pending/running must say it is NOT catalogued, not "local —
  // publishing…" with nothing actually in flight backing that claim.
  const base = docSourcesFixture().sources[0];
  const fixture = docSourcesFixture({
    published: true,
    sources: [{
      ...base, id: 'stuck-1', label: 'Stuck source',
      egeria_external_ref_guid: 'ref-guid-stuck', egeria_state: 'not_catalogued',
      egeria_state_detail: '',
    }],
  });
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': fixture });

  await enrichment.renderDocSources('adventureworks');

  assert.match(host.textContent, /local — not published \(publish needed\)/);
  assert.doesNotMatch(host.textContent, /local — publishing…/);
  assert.doesNotMatch(host.textContent, /published in Egeria/);
});

test('a "publishing" row polls the GET endpoint and updates to cataloged without any user action (round 4, 2026-09-29)', async () => {
  // The bug this pins against: the add response reflects the PRE-drain
  // state and nothing ever re-fetched once the background immediate-attempt
  // drain finished -- live-verified 2026-09-29 (source "pdr"): the server
  // completed in ~4s, the page stayed stuck on "local — publishing…"
  // forever. `renderDocSourcesFromData` must schedule a re-fetch while any
  // row is `publishing` and re-render from it, with no click/reload.
  const { enrichment, host } = await setUpEnrichmentDom();
  const base = docSourcesFixture().sources[0];
  const publishingFixture = docSourcesFixture({
    published: true, in_egeria_count: 0, local_count: 1,
    sources: [{ ...base, id: 'poll-1', egeria_external_ref_guid: '',
      egeria_state: 'publishing', egeria_state_detail: '' }],
  });
  const cataloguedFixture = docSourcesFixture({
    published: true, in_egeria_count: 1, local_count: 0,
    sources: [{ ...base, id: 'poll-1', egeria_external_ref_guid: 'ref-guid-99',
      egeria_state: 'catalogued', egeria_state_detail: 'ref-guid-99' }],
  });

  let call = 0;
  globalThis.fetch = async () => {
    call += 1;
    const body = call === 1 ? publishingFixture : cataloguedFixture;
    return { ok: true, status: 200, json: async () => body };
  };

  // The real poll interval is 2s (capped at 30s) -- too slow for a unit
  // test. Speed up ONLY the wait, not the scheduling logic itself: replace
  // setTimeout with an immediate-ish version for this test, restored after.
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn) => realSetTimeout(fn, 0);
  try {
    await enrichment.renderDocSources('adventureworks');
    assert.match(host.textContent, /local — publishing…/);
    assert.equal(call, 1);

    // Let the scheduled poll tick (and its own re-render) settle -- no
    // click, no reload, nothing but time passing.
    await new Promise((resolve) => realSetTimeout(resolve, 10));
    await new Promise((resolve) => realSetTimeout(resolve, 10));

    assert.match(host.textContent, /published in Egeria/, 'row must update on its own');
    assert.ok(call >= 2, 'expected the poll to have re-fetched at least once');
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test('the doc-source add control says what it does, in order: "Save and probe" (a probe is a network read of the URL, not an Egeria send)', async () => {
  const { enrichment, host } = await setUpEnrichmentDom();
  stubFetchJson({ '/api/doc-sources/database/adventureworks': docSourcesFixture({ sources: [] }) });
  await enrichment.renderDocSources('adventureworks');
  const btn = host.querySelector('#doc-source-add');
  assert.equal(btn.textContent.trim(), 'Save and probe');
  assert.doesNotMatch(host.textContent, /add \+ probe/);
});
