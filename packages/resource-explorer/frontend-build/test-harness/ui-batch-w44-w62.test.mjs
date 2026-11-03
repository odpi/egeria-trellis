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
