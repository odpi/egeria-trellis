/** UI batch W44–W62 (2026-11-03 read-backs): the words each grade fixed, as /next draws them. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
async function load() {
  makeDomEnvironment();
  await loadAppModule();
  return import('/static/next/app.js');
}
const fact = (id, over = {}) => ({ analysis_id: id, state: 'never_run', is_known: false, headline: '', value: {}, last_run_at: '',
  as_of: '', can_run: [id], note: '', runnable: true, runnable_reason: '', library: false, ...over });

// W54: "not run" was worded two ways; one answer sentence, and the source line says "not yet run" in both cases.
for (const [label, second] of [['all never ran', 'never_run'], ['one has no reader', 'no_reader']]) {
  test(`W54 an unrun row (${label}) says one sentence and "not yet run" once on its source line`, async () => {
    const app = await load();
    const entry = { question: 'What columns does it have?', id: 'Q-0010', kind: 'analysis', stage: 'Analysis',
      analysis_ids: ['postgres_column_profile', 'postgres_nested_columns'], answering_mechanism: 'Code Analysis' };
    const env = { answerable: false, caveat: '', sources: [],
      blocked_reason: 'Nothing has been measured for this yet. Run: postgres_column_profile, postgres_nested_columns.',
      facts: [fact('postgres_column_profile'), fact('postgres_nested_columns', { state: second })] };
    const said = text(app.rowInner(entry, 0, env));
    assert.match(said, /Nothing has been measured for this yet\. Run: postgres_column_profile/);
    assert.equal((said.match(/not yet run/gi) || []).length, 1, said);
  });
}

// W50: before the resource's row is back the header says it is reading, never "never surveyed" as a placeholder.
test('W50 the resource header reads "reading…" until the row answers, then the facts', async () => {
  const app = await load();
  app.state.resourceType = 'repo';
  app.state.projects = [];
  app.state.selectedSlug = 'laz_local_adventureworks';
  const before = text(app.resourceHeaderHtml('laz_local_adventureworks'));
  assert.match(before, /reading…/);
  assert.doesNotMatch(before, /never surveyed|not published to Egeria|no external links recorded|undecided/);
  app.state.projects = [{ slug: 'laz_local_adventureworks', display_name: 'adventureworks', last_surveyed_at: '' }];
  const after = text(app.resourceHeaderHtml('laz_local_adventureworks'));
  assert.doesNotMatch(after, /reading…/);
  assert.match(after, /never surveyed · not published to Egeria/);
});

// W49: a resource with no Survey Definitions drew the sub-tab strip twice on Survey & analyses.
test('W49 Survey & analyses with no Survey Definitions draws the sub-tab strip once', async () => {
  const { document } = makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'coco_pharma', stage: 'discovery', subTab: 'survey' });
  app.state.analysisRunFailures = new Map();
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  const routes = {
    'GET /api/survey-definitions/database/coco_pharma/candidates': { technology_type: 'PostgreSQL', scoping: 'questions',
      phase: 'discovery', egeria_native_processes: [], candidates: [] },
    'GET /api/survey-definitions/definitions': [],
    'GET /api/projects/coco_pharma/analyses-index': { analyses: [], counts: {} },
    'GET /api/native-surveys/database/coco_pharma': { technology_type: 'x', surveys: [] },
  };
  globalThis.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const key = Object.keys(routes).find((k) => { const [m, sub] = k.split(' '); return m === method && String(url).includes(sub); });
    if (!key) throw new Error(`_UNSTUBBED_FETCH: ${method} ${url}`);
    return { ok: true, status: 200, json: async () => routes[key] };
  };
  await app.loadSurveyPane();
  await new Promise((r) => setTimeout(r, 30));
  assert.match(text(content.innerHTML), /No Survey Definitions have been authored for PostgreSQL yet/);
  const strip = app.subTabsHtml().match(/^<div[^>]*>/)[0];
  assert.equal(content.innerHTML.split(strip).length - 1, 1, 'the sub-tab strip, once');
});

// W55: a per-card Run →'s Activity row says the stage it was pressed on and the intent it declares, both, always.
test('W55 a per-card run says "pressed on 4 Analysis · declared intent: discovery"; a survey keeps its intent', async () => {
  await load();
  const act = await import('/static/next/stages/activity.js');
  const perCard = (detail) => ({ intent: 'discovery', detail: JSON.stringify({
    survey_definition_ref: 'synthetic::database::coco_pharma_trial::statistics_freshness', ...detail }) });
  assert.equal(text(act.intentWordsHtml(perCard({ pressed_on: '4 Analysis' }))).trim(),
    'pressed on 4 Analysis · declared intent: discovery');
  assert.equal(text(act.intentWordsHtml(perCard({}))).trim(), 'pressed-on stage not recorded · declared intent: discovery');
  assert.equal(text(act.intentWordsHtml({ intent: 'discovery', detail: JSON.stringify({ survey_definition_ref: 'RE::db-survey' }) })).trim(),
    'discovery');
});

test('W55 the Run → press sends the stage it was pressed on', async () => {
  await load();
  const api = await import('/static/re-api.js');
  const urls = [];
  globalThis.fetch = async (url) => { urls.push(String(url)); return { ok: true, status: 200, json: async () => ({ status: 'started' }) }; };
  await api.runAnalysis('coco', 'statistics_freshness', 'database', { force: true, pressedOn: '4 Analysis' });
  assert.match(urls[0], /\/api\/databases\/coco\/analyses\/statistics_freshness\/run\?force=true&pressed_on=4%20Analysis$/);
});

// W56: "via survey" for a one-analysis Run → was literally true and misleading.
test('W56 a card run by its own Run → says so, with the survey id; a real survey keeps "via survey"', async () => {
  const app = await load();
  const words = app.lastRunViaWords({ last_run_via: 'per_card', last_run_survey_id: '3f19bc70-5e5c-4eed-b22b-47b11a92dfef' });
  assert.equal(words.replace(/<[^>]*>/g, ''), 'via Run → on this card (a one-analysis survey, 3f19bc70)');
  assert.equal(text(app.lastRunViaWords({ last_run_via: 'survey' })).trim(), 'via survey');
  assert.equal(text(app.lastRunViaWords({ last_run_via: 'step_runs' })).trim(), 'via step runs');
});
