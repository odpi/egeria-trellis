/** Routing-level test: "Egeria's own surveys" is on Survey & analyses at EVERY
 *  stage, Enrichment included.
 *
 *  WHY THIS EXISTS: Enrichment routes its Survey & analyses tab to
 *  loadEnrichmentAnalysesMapPane instead of loadSurveyPane, and the native-survey
 *  slice only touched the latter -- so the section was invisible on Enrichment
 *  (zero /api/native-surveys requests fired). Third feature hidden by a stage
 *  special case. Design's ruling: the section renders on that tab on every stage,
 *  beneath the unlock map on Enrichment.
 *
 *  These go the way a person does: set the stage, CLICK the Survey & analyses
 *  strip button (bindSubTabs -> loadPane -> stage router), then assert the DOM
 *  and that the native-surveys endpoint was actually requested.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const NATIVE_ROW = {
  qualified_name: 'PostgreSQLSurvey::survey-postgres-database',
  display_name: 'Survey PostgreSQL Database', kind: 'survey_existing',
  description: 'Egeria native survey.', runnable: true, cannot_run_reason: '', in_flight: false,
  run: { state: 'not_run', egeria_status: '', message: '', submitted_at: '', read_at: '',
         report_at: '', report_guid: '', engine_action_guid: '', annotation_count: null, error: '' },
};

function stubServer(calls, { failNative = false } = {}) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (body) => ({ ok: true, status: 200, statusText: 'OK', json: async () => body });
    if (u.includes('/api/native-surveys')) {
      if (failNative) return { ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'boom' }) };
      return ok({ surveys: [NATIVE_ROW] });
    }
    if (u.includes('/candidates')) return ok({ candidates: [{ qualified_name: 'Fixture::candidate', display_name: 'Fixture candidate survey', survey_kind: 'survey_existing', tier: 'discovery' }], egeria_native_processes: [] });
    if (u.includes('enrichment-analyses') || u.includes('analyses-map')) return ok({ analyses: [] });
    return ok({});
  };
}

async function routeToSurvey(stage, opts = {}) {
  const calls = [];
  const { document, window } = makeDomEnvironment();
  stubServer(calls, opts);   // after makeDomEnvironment(), which resets fetch
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.stage = stage;
  app.state.subTab = 'questions';
  app.state.investigations = [];
  app.state.investigation = '';
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  content.innerHTML = app.subTabsHtml();
  app.bindSubTabs();
  const btn = content.querySelector('[data-subtab="survey"]');
  assert.ok(btn, `the ${stage} stage strip must offer a Survey & analyses tab`);
  btn.click();
  for (let i = 0; i < 100 && !document.getElementById('native-surveys') && !document.getElementById('native-surveys-unreadable'); i++) {
    await new Promise((r) => setTimeout(r, 10));
  }
  await new Promise((r) => setTimeout(r, 50));
  return { document, calls };
}

for (const stage of ['discovery', 'assessment', 'analysis', 'enrichment']) {
  test(`routing: Survey & analyses shows Egeria's own surveys on the ${stage} stage`, async () => {
    const { document, calls } = await routeToSurvey(stage);
    assert.ok(calls.some((u) => u.includes('/api/native-surveys')),
      `the ${stage} Survey & analyses tab must request /api/native-surveys (saw: ${calls.join(', ')})`);
    const host = document.getElementById('native-surveys');
    assert.ok(host, `#native-surveys must be present on ${stage}`);
    assert.match(host.textContent, /Survey PostgreSQL Database/);
    assert.match(document.getElementById('content').textContent, /Egeria's own surveys/);
  });
}

test('routing: on Enrichment the unlock map stays first and native surveys sit beneath it', async () => {
  const { document } = await routeToSurvey('enrichment');
  const map = document.getElementById('enrichment-analyses-map');
  const native = document.getElementById('native-surveys');
  assert.ok(map && native);
  assert.ok(map.compareDocumentPosition(native) & 4 /* DOCUMENT_POSITION_FOLLOWING */,
    'native surveys must come after the map');
  assert.match(map.textContent, /unlocks/);
});

for (const stage of ['discovery', 'assessment', 'analysis', 'enrichment']) {
  test(`routing: a FAILED native-survey read on ${stage} is drawn, and leaves the rest of the pane intact`, async () => {
    const { document } = await routeToSurvey(stage, { failNative: true });
    const text = document.getElementById('content').textContent.replace(/\s+/g, ' ');
    assert.ok(document.getElementById('native-surveys-unreadable'), 'the unreadable section must be present');
    assert.match(text, /Egeria's own surveys/);
    assert.match(text, /\? couldn't read Egeria's surveys · re-check/);
    assert.equal(document.getElementById('native-surveys'), null, 'no rows are invented on failure');
    if (stage === 'enrichment') {
      assert.match(document.getElementById('enrichment-analyses-map').textContent, /unlocks/);
    } else {
      assert.match(text, /Fixture candidate survey/, 'the candidates list is untouched');
    }
  });
}
