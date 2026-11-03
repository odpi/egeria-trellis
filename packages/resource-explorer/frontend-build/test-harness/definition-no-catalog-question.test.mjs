/** Behaviour: a Survey Definition no catalog question is scoped to still has its card, runnable, and the card says
 *  why it is not under a question. Real app.js; the card is drawn from the route's candidate row. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const WORDS = 'no catalog question yet · run from Survey & analyses';

async function load() {
  makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  return import('/static/next/app.js');
}
const row = (over) => ({ qualified_name: 'GovActionProcess::DatabaseQualitySurvey', display_name: 'Database Quality Survey',
  description: 'Proposes and runs quality checks.', steps: [{ re_analysis_step: 'run_quality_checks' }], ...over });

test('an unscoped definition shows the absence as a fact and keeps its Run button', async () => {
  const app = await load();
  const html = app.surveyRowHtml(row({ no_catalog_question: WORDS }));
  assert.match(html, /data-def-no-question/);
  assert.ok(text(html).includes('no catalog question yet · run from Survey &amp; analyses'), text(html));
  assert.match(html, /data-run-survey="GovActionProcess::DatabaseQualitySurvey"/);
  assert.doesNotMatch(html, /data-run-survey="[^"]*"\s+disabled/);
});

test('a matched definition draws no such line', async () => {
  const app = await load();
  assert.doesNotMatch(app.surveyRowHtml(row({})), /data-def-no-question/);
});

test('the words are escaped', async () => {
  const app = await load();
  const html = app.surveyRowHtml(row({ no_catalog_question: '<img src=x onerror=1>' }));
  assert.doesNotMatch(html, /<img src=x/);
});
