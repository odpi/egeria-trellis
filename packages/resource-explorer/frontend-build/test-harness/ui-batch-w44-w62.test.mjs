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

// W51: the data-file profiles point at Measure beside when Egeria's survey left counts and no file list.
test('W51 Egeria counts only: the profiles price says so and points at Measure beside, with no Profile press', async () => {
  await load();
  const fs = await import('/static/next/fs-survey.js');
  const words = "Egeria's survey reports counts, not files · Measure beside Egeria's survey to list them, then profile";
  const html = fs.fileProfilesHtml({ price: { walk: false, needs: 'measure_beside', words }, run: null });
  assert.match(text(html).replace(/&apos;/g, "'"), /Egeria's survey reports counts, not files/);
  assert.match(html, /data-fs-act="measure"[^>]*data-fp-measure-pointer/);
  const plain = fs.fileProfilesHtml({ price: { walk: false, words: 'no walk yet · run the folder survey first' }, run: null });
  assert.doesNotMatch(plain, /data-fp-measure-pointer/);
});

// W47: an RE-led walk with 0 Egeria file types but deployed types known never reads "0 types".
test('W47 RE-led FILES header and tiles say the deployed types known, not "0 types"', async () => {
  await load();
  const fs = await import('/static/next/fs-survey.js');
  const view = { state: 'measured', surveyor: 'Resource Explorer', surveyed_at: '2026-11-03T10:37:00',
    results: { counts: { files: 12, total_size_bytes: 19600, file_types: 0, deployed_types: 3, unclassified: 12 },
      times: {}, file_types: [], extensions: [],
      deployed_types: [{ name: 'Parquet Data File', count: 9 }, { name: 'JSON File', count: 2 }, { name: 'Markdown Document', count: 1 }] } };
  const said = text(fs.folderResultsHtml(view, { open: true }));
  assert.match(said, /types: 3 deployed types known · Egeria file-type reference data not applied \(RE-led walk\)/);
  assert.doesNotMatch(said, /\b0 types\b/);
  assert.match(said, /File types 3 deployed types known · file-type reference data not applied/);
  assert.match(said, /Unclassified 12 without an Egeria file type/);
  assert.match(said, /Parquet Data File 9/);
  // Egeria's own survey, or a walk that applied file types, keeps its count.
  const egeria = { ...view, surveyor: "Egeria's Folder Survey" };
  assert.match(text(fs.folderResultsHtml(egeria, { open: true })), /\b0 types\b/);
});

// W59: the Q-0043 "what kind of thing" row never says nothing about datasets.
test('W59 a classification without a dataset finding says "not checked"; a stale one says re-run; "none" says none', async () => {
  await load();
  const v = await import('/static/next/stages/repo-analysis-views.js');
  const role = { check_name: 'repo_role', label: 'library', confidence: 80, detail: { roles: ['library'] } };
  const said = (findings, surveyed_at = '2026-10-01T00:00:00') => text(v.repoClassificationHtml({ findings, surveyed_at }));
  assert.match(said([role]), /dataset signal: not checked \(classification ran before descriptors were read\) · re-run reads it/);
  const none = { check_name: 'dataset_kind', label: 'no dataset signal', summary: 'no dataset signal (no descriptor, no data files)',
    detail: { state: 'none', descriptors_read_at: '2026-09-30T00:00:00' } };
  assert.match(said([role, none]), /no dataset signal \(no descriptor, no data files\)/);
  assert.doesNotMatch(said([role, none]), /re-run reads them/);
  const stale = { ...none, detail: { ...none.detail, descriptors_read_at: '2026-11-03T21:00:00' } };
  assert.match(said([role, stale]), /descriptors read after this classification ran · re-run reads them/);
});

// W61: a one-document feed's Add says its reach check reads the whole document.
test('W61 a one-document feed says the reach check reads the whole document; a paged feed keeps the short help', async () => {
  await load();
  const od = await import('/static/next/admin/open_data.js');
  assert.equal(od.seedAddHelp({ size_note: 'one document, ~31 MB (Project Open Data data.json; one call reads all of it)' }),
    'Registers this portal for everyone, as you. Checks reach first (reads the whole 31 MB document, about a minute); nothing is imported.');
  assert.equal(od.seedAddHelp({ size_note: 'paged, 100 per page (~22,900 datasets, about 1.4 MB a page)' }), od.SEED_ADD_HELP);
  const html = od.suggestedPortalsHtml({ rows: [{ display_name: 'healthdata.gov', feed_url: 'https://healthdata.gov/data.json',
    api_kind: 'DCAT-AP', status: 'answered', can_add: true, words: 'checked 2026-11-02 by the maintainers', why: '',
    size_note: 'one document, ~31 MB (Project Open Data data.json; one call reads all of it)' }] });
  assert.match(html, /title="Registers this portal for everyone, as you\. Checks reach first \(reads the whole 31 MB document/);
});
