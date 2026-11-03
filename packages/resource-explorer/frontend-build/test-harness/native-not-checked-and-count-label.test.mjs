/** The survey card says "not checked since <read_at>" when the run is in flight and its proof row's read_at is older
 *  than two read-back intervals (the server decides from read_at, and sends `not_checked_since`; the card draws it:
 *  a warn-toned glyph on the element plus the words). The annotation count reads as the server's three-part label. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
makeDomEnvironment();
const ns = await import('/static/next/stages/native-surveys.js');
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace('Z', '');

// What the server sends: `not_checked_since` = read_at when read_at is older than 120 s, else ''.
const inFlight = (readAgoMs) => {
  const read_at = iso(readAgoMs);
  return { run: { state: 'running', egeria_status: 'APPROVED', phase: 'waiting', submitted_at: iso(10 * 60e3), read_at,
    engine_action_guid: 'ea-1', stalled: false, error: '', not_checked_since: readAgoMs > 120e3 ? read_at : '' } };
};

test('read 3 minutes ago: the cue is on the line, as a glyph on the element and in words', () => {
  const h = ns.nativeSurveyStatusHtml(inFlight(3 * 60e3));
  assert.match(h, /data-native-not-checked/);
  assert.match(h, /text-state-warn" data-native-not-checked/);
  assert.match(h, /class="font-glyph" aria-hidden="true">◌<\/span> not checked since /);
  assert.match(text(h), /not checked since \S/);
});

test('read 1 minute ago: no cue', () => {
  const h = ns.nativeSurveyStatusHtml(inFlight(60e3));
  assert.doesNotMatch(h, /data-native-not-checked/);
  assert.doesNotMatch(text(h), /not checked since/);
});

test('a finished run carries no cue even when the server sends none', () => {
  const t = text(ns.nativeSurveyStatusHtml({ run: { state: 'complete', egeria_status: 'COMPLETED', read_at: iso(3600e3),
    report_at: iso(3600e3), report_guid: 'r1', annotation_count: 6, not_checked_since: '' } }));
  assert.doesNotMatch(t, /not checked since/);
});

test('a completed run shows the server\'s count label in the report button, else the bare count', () => {
  const run = { state: 'complete', egeria_status: 'COMPLETED', read_at: iso(1000), report_at: iso(1000), report_guid: 'r1', annotation_count: 6 };
  const label = '7 annotations on the report; 6 read into RE; 1 not read by design (file-name log)';
  const withLabel = ns.nativeSurveyStatusHtml({ run: { ...run, count_label: label } });
  assert.match(text(withLabel), /7 annotations on the report; 6 read into RE; 1 not read by design \(file-name log\) ›/);
  assert.match(withLabel, /data-native-report="r1"/);
  const bare = text(ns.nativeSurveyStatusHtml({ run: { ...run, count_label: '' } }));
  assert.match(bare, /6 annotations ›/); assert.doesNotMatch(bare, /on the report/);
});

// The sweep gave up on the report (20 tries or 24 h): the server's terminal words, no spinner cue, a refresh that retries.
const gaveUp = (state, words) => ({ run: { state, egeria_status: 'COMPLETED', read_at: iso(3 * 60e3), report_guid: '', error: '',
  not_checked_since: '', read_gave_up: true, read_gave_up_words: words, annotation_count: null } });

test('awaiting_report that gave up says "report not read after 20 tries" with a refresh button and no cue', () => {
  const h = ns.nativeSurveyStatusHtml(gaveUp('awaiting_report', 'report not read after 20 tries · refresh to retry'));
  assert.match(text(h), /report not read after 20 tries refresh to retry/);
  assert.match(h, /data-native-gave-up/);
  assert.match(h, /<button[^>]*data-native-check-now[^>]*>refresh to retry<\/button>/);
  assert.doesNotMatch(text(h), /not checked since|not read into RE yet/);
});

test('report_incomplete that gave up says "report incomplete after 20 tries"', () => {
  const t = text(ns.nativeSurveyStatusHtml(gaveUp('report_incomplete', 'report incomplete after 20 tries · refresh to retry')));
  assert.match(t, /report incomplete after 20 tries refresh to retry/);
  assert.doesNotMatch(t, /reading it again/);
});

test('inside the bound the waiting words are unchanged', () => {
  const t = text(ns.nativeSurveyStatusHtml({ run: { state: 'report_incomplete', egeria_status: 'COMPLETED', read_at: iso(1000), error: '', read_gave_up: false } }));
  assert.match(t, /RE's copy of the report is incomplete; reading it again/);
});
