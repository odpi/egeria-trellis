/** A publish proved before the latest Egeria reset is history (architect ruling (b), 2026-10-23): the server marks it
 *  (`publish_proof.state === 'pre_reset'`, an activity row's `pre_reset`, a commit summary's `pre_reset` state) and the
 *  screen says "published before the <date> reset · not in Egeria now" in the ◌ lane, never ✓ and never
 *  "published as <identity>". With no `pre_reset` the words are what they were. Real modules; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const WORDS = 'published before the 2026-10-24 reset · not in Egeria now';
const PRE = { at: '2026-10-24T09:00:00Z', words: WORDS };
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('a pre_reset publish proof is ◌ with the server words, never ✓', async () => {
  makeDomEnvironment();
  const { publishProofHtml } = await import('/static/next/publish-proof.js');
  const html = publishProofHtml({ state: 'pre_reset', word: WORDS, at: '2026-10-20T10:00:00', reset_at: PRE.at });
  assert.match(html, /data-publish-proof="pre_reset"/);
  assert.match(html, /◌/);
  assert.doesNotMatch(html, /✓/);
  assert.ok(html.includes(WORDS));
  // the read-back proof is unchanged
  assert.match(publishProofHtml({ state: 'read_back', word: 'published · read back', at: 'x' }), /✓/);
});

async function runsDialog(runs) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity/?entity_slug=')) return ok(runs);
    return ok(u.includes('/groups') ? [] : {});
  };
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  await app.openRunsList('coco');
  return document.getElementById('wl-detail');
}

const dbRun = (extra) => ({
  id: 'r1', operation: 'survey', ts: '2026-10-20T10:00:00', status: 'ok', summary: 'Done',
  detail: JSON.stringify({ publish_state: 'published to Egeria', egeria_report_guid: 'g-1', published_as: 'peterprofile',
    steps: [{ step: 'S::pub', status: 'ok', answered_by: 'egeria-custom',
      detail: { source: 'egeria-custom', publish_state: 'published to Egeria' } }] }),
  ...extra,
});

test('a database run logged before the reset: no ✓, no "published as", the pre-reset words on run and step', async () => {
  const dlg = await runsDialog([dbRun({ pre_reset: PRE })]);
  const run = dlg.querySelector('[data-run-publish]');
  assert.ok(run, 'the run publish word renders');
  assert.ok(text(run).includes(WORDS));
  assert.doesNotMatch(run.innerHTML, /✓/);
  assert.doesNotMatch(text(run), /published as/);
  const step = dlg.querySelector('[data-step-publish]');
  assert.equal(step.getAttribute('data-step-publish'), 'pre_reset');
  assert.ok(text(step).includes(WORDS));
});

test('the same run with no pre_reset reads as before: ✓ published as <identity>', async () => {
  const dlg = await runsDialog([dbRun({})]);
  const run = dlg.querySelector('[data-run-publish]');
  assert.match(text(run), /published as peterprofile/);
  assert.equal(dlg.querySelector('[data-step-publish]').getAttribute('data-step-publish'), 'measured');
});

// ── fix round 1: the file-system publish proofs and the one word source ──────────────────────────────────────────

const PUB = { published: false, landed: 4, built: 4, landed_words: '4 of 4 annotations in Egeria', not_published_why: '', pre_reset: PRE };

test('the folder survey published before the reset: ◌ and the server words, never "published · N of M"', async () => {
  makeDomEnvironment();
  const { folderResultsHtml } = await import('/static/next/fs-survey.js');
  const view = { state: 'measured', surveyor: 'Resource Explorer', report_guid: 'rep-fs', published: false, publish: PUB, results: {} };
  const html = folderResultsHtml(view);
  const pub = html.match(/data-fs-published>([\s\S]*?)<\/div>/)[1];
  assert.match(pub, /◌/);
  assert.ok(pub.includes(WORDS));
  assert.doesNotMatch(pub, /annotations in Egeria|published ·/);
  // Egeria's own report read back before the reset: the same words, not "published · report …"
  const eg = folderResultsHtml({ state: 'measured', surveyor: "Egeria's Folder Survey", report_guid: 'r', published: false, pre_reset: PRE, results: {} });
  assert.ok(eg.match(/data-fs-published>([\s\S]*?)<\/div>/)[1].includes(WORDS));
  // no pre_reset: unchanged
  const live = folderResultsHtml({ ...view, published: true, publish: { ...PUB, published: true, pre_reset: undefined } });
  assert.match(live.match(/data-fs-published>([\s\S]*?)<\/div>/)[1], /published · 4 of 4 annotations in Egeria/);
});

test('the walk measures and the data-file profiles published before the reset say the server words', async () => {
  makeDomEnvironment();
  const { walkMeasuresHtml, fileProfilesHtml } = await import('/static/next/fs-survey.js');
  const ext = walkMeasuresHtml({ led_by: 're', surveyed_at: '2026-10-20', report_guid: 'rep-fs', publish: PUB, rows: {}, folders: {}, hashing: {}, owners: {} });
  const m = ext.match(/data-fs-measures-published>([\s\S]*?)<\/div>/)[1];
  assert.ok(m.includes(WORDS) && /◌/.test(m) && !/annotations in Egeria/.test(m));
  const fp = fileProfilesHtml({ price: { words: '' }, run: { run_id: '2026-10-20T10:00:00', counts: {}, files: [], words: '',
    publish: { report_guid: 'r', annotations_in_egeria: 2, annotation_count: 2, pre_reset: PRE } } });
  const p = fp.match(/data-fp-published[^>]*>([\s\S]*?)<\/div>/)[1];
  assert.ok(p.includes(WORDS) && /◌/.test(p) && !/annotations in Egeria/.test(p));
});

test('a publish older than the reset is said in the server words (one source), not a constant of the screen', async () => {
  makeDomEnvironment();
  const { publishedBadge } = await import('/static/next/format.js');
  const b = publishedBadge({ last_published_at: '2026-10-20T10:00:00', published_state: 'published_earlier',
    egeria_reset_at: PRE.at, published_earlier_words: WORDS });
  assert.ok(b.earlier && b.title.startsWith(WORDS));
  assert.doesNotMatch(b.title, /Egeria was reset/);
});
