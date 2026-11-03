/** Engine host step 4: a mixed process run (Egeria's native survey, then RE's step) is ONE line built by the server
 *  from what it read of each step, with each step's report opening as any report does; a failure that stopped the
 *  chain says so in the warn tone. A plain survey row is unchanged. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
makeDomEnvironment();
const ns = await import('/static/next/stages/native-surveys.js');
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const LINE = 'Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · step 2 re-database-staleness ✓ report 7c1d0f9e · started by peterprofile 10-15';
const row = (run) => ({ qualified_name: 'GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived', kind: 'mixed_process',
  display_name: 'Postgres survey then derived', runnable: true, run: { read_at: new Date().toISOString(), error: '', ...run } });

test('a completed two-step run is one line with a report link per step', () => {
  const h = ns.nativeSurveyStatusHtml(row({ state: 'complete', line: LINE, steps: [
    { report_guid: 'cb92eedc-5e24-4da7-ba5e-fc11d1707d8f' }, { report_guid: '7c1d0f9e-0000-4000-8000-00000000a002' }] }));
  assert.match(text(h), /^Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · step 2 re-database-staleness ✓/);
  assert.equal((h.match(/data-native-report="/g) || []).length, 2);
  assert.match(h, /data-native-report="cb92eedc-5e24-4da7-ba5e-fc11d1707d8f"/);
  assert.doesNotMatch(h, /text-state-warn/);
});

test('a failure that stopped the chain says so, in the warn tone, never "pending"', () => {
  const h = ns.nativeSurveyStatusHtml(row({ state: 'failed', egeria_status: 'FAILED',
    line: 'Postgres survey then derived · step 1 PostgreSQLSurvey ✗ survey-failed · step 2 not started · step 1 failed', steps: [{ report_guid: '' }] }));
  assert.match(text(h), /step 2 not started · step 1 failed/);
  assert.match(h, /text-state-warn/);
  assert.doesNotMatch(text(h), /pending/);
  assert.doesNotMatch(h, /data-native-report/);
});

test('the row names what runs it; a plain survey row is unchanged', () => {
  assert.match(text(ns.nativeSurveyRowHtml(row({ state: 'not_run' }))), /Egeria process · native survey, then Resource Explorer's engine host/);
  const plain = { qualified_name: 'PostgreSQLSurvey::survey-postgres-database', kind: 'survey_existing', display_name: 'Survey', runnable: true, run: { state: 'not_run' } };
  assert.match(text(ns.nativeSurveyRowHtml(plain)), /Egeria's own survey engine/);
});

test('report links are numbered by step, not by position after filtering', () => {
  const h = ns.nativeSurveyStatusHtml(row({ state: 'complete', line: LINE, steps: [
    { report_guid: '' }, { report_guid: '7c1d0f9e-0000-4000-8000-00000000a002' }] }));
  assert.match(text(h), /step 2 report ›/);
  assert.doesNotMatch(text(h), /step 1 report/);
});

test('an unclaimed RE step shows the waiting cue and its reason', () => {
  const h = ns.nativeSurveyStatusHtml(row({ state: 'running', line: 'Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · step 2 re-database-staleness not claimed yet',
    waiting_for: "waiting for RE's engine host · engine host off · turn it on, or cancel step 2 in Egeria", steps: [] }));
  assert.match(h, /data-native-waiting/);
  assert.match(text(h), /◌ waiting for RE(&apos;|')s engine host · engine host off/);
});
