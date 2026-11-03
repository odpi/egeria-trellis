/** Leg 3 (gate 2026-10-29): a question answered by RE's own analysis AND a library step shows both lines, and the row's
 *  Re-run follows the same predicate as Survey & analyses (the fact carries `runnable` / `runnable_reason`). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const fact = (id, over = {}) => ({ analysis_id: id, state: 'measured', is_known: true, headline: '', value: {}, last_run_at: '2026-10-29T10:00:00',
  as_of: '', can_run: [], note: '', runnable: true, runnable_reason: '', library: false, ...over });
const entry = { question: 'Are this database\'s planner statistics current?', id: 'Q-0073', kind: 'analysis', stage: 'Analysis',
  analysis_ids: ['db_statistics_freshness', 'statistics_freshness'], answering_mechanism: 'Code Analysis' };
const env = (facts) => ({ answerable: true, facts, caveat: '', sources: [] });

async function load() {
  makeDomEnvironment();
  await loadAppModule();
  return import('/static/next/app.js');
}

test('a question answered by RE and by a library step shows both analyses, each with its run time', async () => {
  const app = await load();
  const html = app.rowInner(entry, 0, env([
    fact('db_statistics_freshness', { headline: '3 of 4 tables have stale statistics.' }),
    fact('statistics_freshness', { library: true, headline: 'measured · of 4 tables: 4 need attention (as of 2026-10-26)' })]));
  const text = html.replace(/<[^>]*>/g, '');
  assert.match(text, /3 of 4 tables have stale statistics/);                       // the lead's line
  const line = /<div data-step-line="statistics_freshness"[\s\S]*?<\/div>/.exec(html);
  assert.ok(line, 'the library step has a line of its own');
  assert.match(line[0].replace(/<[^>]*>/g, ''), /statistics_freshness · measured · of 4 tables: 4 need attention/);
  assert.match(line[0], /run /);
  assert.ok(line[0].includes('data-numbers-step'), 'its own numbers');
  assert.ok(line[0].includes('data-rerun-for="statistics_freshness"'), 'its own re-run');
  assert.ok(!/\d+ results?: \d+ measured/.test(html), 'never a count of results');
});

test('a library step that is the only analysis is the row itself and not a second line', async () => {
  const app = await load();
  const only = { ...entry, analysis_ids: ['statistics_freshness'] };
  const html = app.rowInner(only, 0, env([fact('statistics_freshness', { library: true, headline: 'measured · 4 tables' })]));
  assert.ok(!html.includes('data-step-line'));
});

test('a library step with no run says so on its own line and offers Run', async () => {
  const app = await load();
  const html = app.rowInner(entry, 0, env([
    fact('db_statistics_freshness', { headline: 'x.' }),
    fact('statistics_freshness', { library: true, state: 'never_run', is_known: false, last_run_at: '' })]));
  const line = /<div data-step-line="statistics_freshness"[\s\S]*?<\/div>/.exec(html)[0];
  const said = line.replace(/<[^>]*>/g, '');
  assert.match(said, /not yet run/);
  // F23: the absence is said once, in the owner's words.
  assert.ok(!/never run|not run yet/.test(said), said);
  assert.equal(said.match(/not yet run/g).length, 1, said);
  assert.ok(!line.includes('data-numbers-step'), 'nothing to show the numbers of');
});

test("the row's Re-run is disabled, with the real reason, when the run route would refuse it", async () => {
  const app = await load();
  const why = "step not installed here: 'statistics_freshness'";
  const blocked = app.rowInner({ ...entry, analysis_ids: ['statistics_freshness'] }, 0,
    env([fact('statistics_freshness', { library: true, runnable: false, runnable_reason: why, headline: 'x.' })]));
  const btn = /<button data-rerun="0"[^>]*>/.exec(blocked)[0];
  assert.match(btn, /disabled/);
  assert.ok(btn.includes('step not installed here'), btn);
  const open = app.rowInner(entry, 0, env([fact('db_statistics_freshness', { headline: 'x.' })]));
  assert.ok(!/<button data-rerun="0"[^>]*disabled/.test(open), 'enabled when the route would run it');
});

test("a library step line's Re-run is disabled with its own reason", async () => {
  const app = await load();
  const why = "step not installed here: 'statistics_freshness'";
  const html = app.rowInner(entry, 0, env([
    fact('db_statistics_freshness', { headline: 'x.' }),
    fact('statistics_freshness', { library: true, runnable: false, runnable_reason: why, headline: 'y.' })]));
  const btn = /<button data-rerun-for="statistics_freshness"[^>]*>/.exec(html)[0];
  assert.match(btn, /disabled/);
  assert.ok(btn.includes('step not installed here'), btn);
});

test('F22: a library step that is the lead shows its headline as the answer, not the bare state word', async () => {
  const app = await load();
  const q = { question: 'How recently have files here changed?', id: 'Q-0107', kind: 'analysis', stage: 'Analysis',
    analysis_ids: ['fs_activity'], note: 'fs_activity', answering_mechanism: 'Code Analysis' };
  const html = app.rowInner(q, 0, env([fact('fs_activity', {
    library: true, headline: 'measured · 3 files modified within 7 days; 12 within 30 days' })]));
  const text = html.replace(/<[^>]*>/g, '');
  assert.match(text, /measured · 3 files modified within 7 days; 12 within 30 days/);
  const { readEnvelope } = await import('/static/next/envelope.js');
  const lines = readEnvelope(q, env([fact('fs_activity', { library: true, headline: 'measured · 3 files modified within 7 days' })]),
    (s) => s, (s) => s);
  assert.notEqual(lines.answer.trim(), 'measured', 'never the bare state word');
  assert.equal(lines.answer, 'measured · 3 files modified within 7 days');
});

test('F22: a non-library headline is still cut to its first sentence', async () => {
  await load();
  const { readEnvelope } = await import('/static/next/envelope.js');
  const lines = readEnvelope(entry, env([fact('db_statistics_freshness', { headline: '3 of 4 stale · 1 schema' })]),
    (s) => s, (s) => s);
  assert.equal(lines.answer, '3 of 4 stale');
});

test('F23: a step that ran with no recorded time says so, never "never run"', async () => {
  const app = await load();
  const html = app.rowInner(entry, 0, env([
    fact('db_statistics_freshness', { headline: 'x.' }),
    fact('statistics_freshness', { library: true, headline: 'measured · 4 tables', last_run_at: '' })]));
  const line = /<div data-step-line="statistics_freshness"[\s\S]*?<\/div>/.exec(html)[0].replace(/<[^>]*>/g, '');
  assert.match(line, /run time not recorded/);
  assert.ok(!/never run|not run yet|not yet run/.test(line), line);
});

test('Q-0073: a note shared by the lead and a step is said once, and the step line gives the state, not the note', async () => {
  const app = await load();
  const why = 'This resource was surveyed by runs that predate per-step recording, so whether this analysis was among them cannot be determined.';
  const html = app.rowInner(entry, 0, env([
    fact('db_statistics_freshness', { state: 'not_established', is_known: false, note: why, last_run_at: '' }),
    fact('statistics_freshness', { library: true, state: 'not_established', is_known: false, note: why, last_run_at: '' })]));
  const text = html.replace(/<[^>]*>/g, '');
  assert.equal(text.split('predate per-step recording').length - 1, 1, text);
  const line = /<div data-step-line="statistics_freshness"[\s\S]*?<\/div>/.exec(html)[0].replace(/<[^>]*>/g, '');
  assert.match(line, /statistics_freshness · not established/);
  assert.ok(!line.includes('predate'), line);
});
