/** Egeria's own surveys: waiting-to-start and running are two states, each says when it was requested, and a
 *  stall is a cue plus a word (the server decides "stalled" from the stored request time). Also the scope
 *  message on Survey & analyses: it says what is true, not "stage filter unavailable". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
makeDomEnvironment();
const ns = await import('/static/next/stages/native-surveys.js');
const app = await import('/static/next/app.js');
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const ago = (ms) => new Date(Date.now() - ms).toISOString();
const H = 3600e3;
const run = (o) => ({ run: { state: 'running', egeria_status: 'IN_PROGRESS', submitted_at: ago(2.5 * 24 * H), read_at: ago(120e3),
  engine_action_guid: 'ea-1', phase: 'running', stalled: false, requested_age_s: 2.5 * 24 * 3600, stall_after_s: 21600, error: '', ...o } });

test('running: says requested how long ago; a stall is the cue plus "stalled?"', () => {
  const t = text(ns.nativeSurveyStatusHtml(run({ stalled: true })));
  assert.match(t, /^running · requested .* · ◐ stalled\? · Egeria says IN_PROGRESS · read /);
  assert.doesNotMatch(text(ns.nativeSurveyStatusHtml(run({ stalled: false }))), /stalled/);
});

test('waiting to start is its own state, not "running"', () => {
  const t = text(ns.nativeSurveyStatusHtml(run({ egeria_status: 'WAITING', phase: 'waiting', stalled: true })));
  assert.match(t, /^◌ waiting to start · requested .* ◐ stalled\?/);
  assert.doesNotMatch(t, /^running/);
});

test('with Egeria\'s start time stored: queued vs running, in place of "requested Xd ago"', () => {
  const t = text(ns.nativeSurveyStatusHtml(run({ stalled: true, requested_at: '2026-10-09T01:41:00', started_at: '2026-10-11T13:00:00',
    queued_s: 2.5 * 86400, running_s: 11 * 3600 })));
  assert.match(t, /^◌ queued 2\.5d before start · running 11h · ◐ stalled\? · Egeria says IN_PROGRESS/);
  assert.doesNotMatch(t, /requested/);
  const h = ns.nativeSurveyStatusHtml(run({ requested_at: '2026-10-09T01:41:00', started_at: '2026-10-11T13:00:00', queued_s: 2.5 * 86400, running_s: 11 * 3600 }));
  assert.match(h, /data-native-timing title="Egeria queued the action 2\.5d \(requested 2026-10-09T01:41:00 UTC, started 2026-10-11T13:00:00 UTC\)\. It has run 11h/);
});

test('a start time without a request time says running only; none stored falls back to "requested"', () => {
  const t = text(ns.nativeSurveyStatusHtml(run({ started_at: '2026-10-11T13:00:00', queued_s: null, running_s: 5 * 60 })));
  assert.match(t, /^running 5m · Egeria says/); assert.doesNotMatch(t, /queued/);
  const none = text(ns.nativeSurveyStatusHtml(run({ started_at: '', queued_s: null, running_s: null })));
  assert.match(none, /^running · requested /); assert.doesNotMatch(none, /queued/);
});

test('a zero or sub-minute gap reads "<1m", and the hover says "as of now"', () => {
  const o = { requested_at: '2026-10-11T13:00:00', started_at: '2026-10-11T13:00:00', queued_s: 0, running_s: 30 };
  const t = text(ns.nativeSurveyStatusHtml(run(o)));
  assert.match(t, /^◌ queued &lt;1m before start · running &lt;1m/);
  assert.match(ns.nativeSurveyStatusHtml(run(o)), /as of now\./);
  assert.match(text(ns.nativeSurveyStatusHtml(run({ ...o, queued_s: 60, running_s: 90 }))), /queued 1m before start · running 2m/);
});

test('waiting to start keeps "requested" (nothing has started)', () => {
  const t = text(ns.nativeSurveyStatusHtml(run({ egeria_status: 'WAITING', phase: 'waiting', started_at: '', queued_s: null, running_s: null })));
  assert.match(t, /^◌ waiting to start · requested /);
});

test('the sentence is on hover: the age and the limit, and that RE does not cancel on it', () => {
  const h = ns.nativeSurveyStatusHtml(run({ stalled: true }));
  assert.match(h, /data-native-stalled title="Requested 2\.5 days ago and Egeria still says IN_PROGRESS\. Flagged after 6 hours/);
});

test('a freshly submitted action is not flagged; one with no status read stalls only by request age', () => {
  assert.doesNotMatch(text(ns.nativeSurveyStatusHtml(run({ state: 'submitted', egeria_status: '', phase: '', stalled: false }))), /stalled/);
  assert.match(text(ns.nativeSurveyStatusHtml(run({ state: 'submitted', egeria_status: '', phase: '', stalled: true }))), /◐ stalled\?/);
});

test('scope message: no questions for the stage says so and offers no retry', () => {
  app.state.resourceType = 'db';
  const none = ns && app.scopeFallbackHtml({ scoping: 'full-scan', scoping_reason: 'no_questions' }, 'curate');
  assert.match(text(none), /all stages — no curate questions for a database yet/);
  assert.doesNotMatch(none, /data-act="rescope"/);
  const unres = app.scopeFallbackHtml({ scoping: 'full-scan', scoping_reason: 'none_resolved' }, 'discovery');
  assert.match(text(unres), /no published survey is scoped to discovery · retry/);
  assert.match(unres, /data-act="rescope"/);
  assert.match(text(app.scopeFallbackHtml({ scoping: 'full-scan' }, 'x')), /stage filter unavailable/, 'older server: the old words');
});

test('singular units: "1 hour", "1 day"', () => {
  const h = ns.nativeSurveyStatusHtml(run({ stalled: true, requested_age_s: 3600, stall_after_s: 3600, phase: 'waiting', egeria_status: 'WAITING' }));
  assert.match(h, /Requested 1 hour ago/); assert.match(h, /Flagged after 1 hour /);
  assert.match(ns.nativeSurveyStatusHtml(run({ stalled: true, requested_age_s: 86400 })), /Requested 1 day ago/);
});
