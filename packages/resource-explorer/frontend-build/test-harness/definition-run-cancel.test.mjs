/** Cancel on a definition's Egeria-coordinated run (DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API §2.4; designer words Q4,
 *  2026-10-17). "Cancel run" sits on its own line under the run line, only when the server offers it to this reader
 *  (owner Q2/Q3); the confirm is in place; every cue comes from the server's row (the press's proof row and Egeria's
 *  read-back), never from the click. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered, loadAppModule } from './dom-harness.mjs';

const QN = 'GovActionProcess::DatabaseAnalysisSurvey';
const GUID = '06994d5d-a228-408c-8842-fd6256db4c34';
const ctx = { running: { step: 2, label: 'postgres_operations' }, cancelled_step: null,
  later: ['db_derived', 'nested_columns'], finished: ['postgres_column_profile'], finished_count: 1, of: 4 };
const run = (over = {}) => ({ state: 'running', line: 'Database Analysis Survey · step 1 ✓ · step 2 running', egeria_status: 'IN_PROGRESS',
  engine_action_guid: GUID, submitted_at: '2026-10-17T09:00:00', read_at: '2026-10-17T09:05:00', error: '', steps: [],
  cancel_context: ctx, ...over });
const row = (r = run(), pc = {}) => ({ qualified_name: QN, display_name: 'Database Analysis Survey', kind: 're_definition',
  runnable: true, in_flight: ['running', 'submitted'].includes(r.state), run: r,
  process_cancel: { offered: false, outside: false, finishing: null, viewer: 'dan', ...pc } });
const norm = (el) => el.textContent.replace(/\s+/g, ' ').trim();

async function setUp() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  return { document, ns };
}
function draw(document, ns, r) {
  const box = document.createElement('div');
  box.innerHTML = ns.definitionRunLineHtml(r);
  document.body.appendChild(box);
  return box;
}

test('"Cancel run" is on its own line, only when the server offers it to this reader', async () => {
  const { document, ns } = await setUp();
  const on = draw(document, ns, row(run(), { offered: true }));
  const btn = on.querySelector('[data-process-cancel]');
  assert.equal(btn.textContent.trim(), 'Cancel run');
  assert.equal(btn.closest('[data-process-cancel-box]').tagName, 'DIV', 'its own line, not beside "check now"');
  assert.equal(draw(document, ns, row(run(), { offered: false })).querySelector('[data-process-cancel]'), null);
  assert.equal(draw(document, ns, row(run({ engine_action_guid: '' }), { offered: true })).querySelector('[data-process-cancel]'), null);
});

test('the confirm names the step, what will not start and what keeps its report; unknown step has its own words', async () => {
  const { ns } = await setUp();
  assert.equal(ns.PROCESS_CANCEL_WORDS.confirm(ctx),
    "Cancel this run? Step 2, postgres_operations, finishes but publishes nothing, and db_derived and nested_columns won't start. postgres_column_profile keeps its report.");
  assert.equal(ns.PROCESS_CANCEL_WORDS.confirm({ running: null }),
    'Cancel this run? The running step finishes but publishes nothing, and no later step starts. Finished steps keep their reports.');
});

test('press shows the confirm in place with "Yes, cancel" · "keep running"; keep running puts the control back', async () => {
  const { document, ns } = await setUp();
  const box = draw(document, ns, row(run(), { offered: true }));
  ns.bindRunLineControls(box, 'coco_pharma', {});
  box.querySelector('[data-process-cancel]').click();
  const conf = box.querySelector('[data-process-cancel-confirm]');
  assert.equal(conf.hidden, false);
  assert.match(norm(conf), /Yes, cancel/);
  assert.match(norm(conf), /keep running/);
  box.querySelector('[data-process-cancel-no]').click();
  assert.equal(conf.hidden, true);
  assert.equal(box.querySelector('[data-process-cancel]').hidden, false);
});

test('each cue reads from the row: pending, finishing, done, done outside, too late, not permitted, not sent, outside', async () => {
  const { document, ns } = await setUp();
  const pending = norm(draw(document, ns, row(run({ cancel: { state: 'pending', by: 'dan', at: '2026-10-17T09:06:00' } }))));
  assert.match(pending, /cancel requested by you · \d\d:\d\d · waiting for Egeria · step 2, postgres_operations, finishes first/);
  const other = norm(draw(document, ns, row(run({ cancel: { state: 'pending', by: 'peter', at: '2026-10-17T09:06:00' } }))));
  assert.match(other, /cancel requested by peter/);
  const cancelled = run({ state: 'failed', egeria_status: 'CANCELLED', cancel: { state: 'done', by: 'dan', at: '2026-10-17T09:06:00', finished: 1, of: 4 } });
  assert.match(norm(draw(document, ns, row(cancelled))), /⊘ cancelled by dan · \d\d:\d\d · 1 of 4 steps finished/);
  assert.match(norm(draw(document, ns, row(cancelled, { finishing: { step: 2, label: 'postgres_operations' } }))),
    /⊘ cancelled · step 2, postgres_operations, is finishing and will publish nothing/);
  const outside = run({ state: 'failed', egeria_status: 'CANCELLED', cancel: { state: 'done', outside: true, at: '2026-10-17T09:06:00', finished: 1, of: 4 } });
  assert.match(norm(draw(document, ns, row(outside))), /⊘ cancelled in Egeria · \d\d:\d\d · not from Resource Explorer/);
  assert.match(norm(draw(document, ns, row(run({ cancel: { state: 'refused', kind: 'too_late', by: 'dan' } })))),
    /too late to cancel · the run had already finished/);
  assert.match(norm(draw(document, ns, row(run({ cancel: { state: 'refused', kind: 'not_permitted', by: 'peter', reason: 'OMAG-403 not permitted' } })))),
    /Egeria won't let peter cancel this run · OMAG-403 not permitted/);
  const unsent = norm(draw(document, ns, row(run({ cancel: { state: 'unsent', by: 'dan' } }))));
  assert.match(unsent, /cancel not sent · couldn't reach Egeria · try again/);
  assert.doesNotMatch(unsent, /refused/);
  const out = draw(document, ns, row(run(), { outside: true })).querySelector('[data-process-cancel-outside]');
  assert.equal(norm(out), 'started outside Resource Explorer');
  assert.equal(out.getAttribute('title'), 'RE can cancel only runs it started; cancel this one in Egeria');
});

test('fix round 1: a refused, not-permitted or unsent cancel line draws no glyph (designer: no glyph on any of them)', async () => {
  const { document, ns } = await setUp();
  for (const cancel of [{ state: 'refused', kind: 'refused', by: 'dan', reason: 'OMAG-400 bad' },
    { state: 'refused', kind: 'not_permitted', by: 'peter', reason: 'OMAG-403' },
    { state: 'refused', kind: 'too_late', by: 'dan' }, { state: 'unsent', by: 'dan' }]) {
    const box = draw(document, ns, row(run({ cancel })));
    assert.ok(!/[◐◔◌⊘✕]/.test(box.querySelector('[data-process-cancel-refused], [data-process-cancel-unsent], [data-process-cancel-too-late]').textContent), JSON.stringify(cancel));
  }
});

test('review round 1: step identifiers are never capitalised in the confirm', async () => {
  const { ns } = await setUp();
  const words = ns.PROCESS_CANCEL_WORDS.confirm({ running: { step: 1, label: 'postgres_column_profile' }, later: [], finished: ['db_derived'] });
  assert.match(words, /Step 1, postgres_column_profile, finishes/);
  assert.match(words, / db_derived keeps its report\./);
  assert.doesNotMatch(words, /Postgres_column_profile|Db_derived/);
});

test('review round 1: a cancelled run line is drawn in the neutral tone, never the warn one', async () => {
  const { document, ns } = await setUp();
  const cancelled = run({ state: 'failed', egeria_status: 'CANCELLED', line: 'Database Analysis Survey · step 1 ⊘ cancelled',
    cancel: { state: 'done', by: 'dan', at: '2026-10-17T09:06:00', finished: 0, of: 4 } });
  const line = draw(document, ns, row(cancelled)).querySelector('[data-native-process-line]');
  assert.equal(line.classList.contains('text-state-warn'), false);
  assert.equal(line.classList.contains('text-ink-muted'), true);
  const failed = run({ state: 'failed', egeria_status: 'FAILED', line: 'Database Analysis Survey · step 1 failed' });
  assert.equal(draw(document, ns, row(failed)).querySelector('[data-native-process-line]').classList.contains('text-state-warn'), true);
});

test('review round 1: too late (from a read-back) and a plain refusal each have their own words', async () => {
  const { document, ns } = await setUp();
  assert.match(norm(draw(document, ns, row(run({ state: 'failed', egeria_status: 'FAILED', cancel: { state: 'too_late', by: 'dan' } })))),
    /too late to cancel · the run had already finished/);
  const refused = norm(draw(document, ns, row(run({ cancel: { state: 'refused', kind: 'refused', by: 'dan', reason: 'OMAG-400 bad instance' } }))));
  assert.match(refused, /Egeria refused the cancel: OMAG-400 bad instance/);
  assert.doesNotMatch(refused, /won't let/);
});

test("review round 1: RE's gate refusal stays on the line across checkNow's repaint until the next press", async () => {
  const { document, ns } = await setUp();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'db', selectedSlug: 'coco_pharma' });
  globalThis.fetch = async () => ({ ok: false, status: 409, statusText: 'Conflict',
    json: async () => ({ detail: 'too late to cancel · the run had already finished' }) });
  const host = document.createElement('div');
  document.body.appendChild(host);
  const paint = () => { host.innerHTML = ns.definitionRunLineHtml(row(run(), { offered: true })); bind(); };
  let repaints = 0;
  const bind = () => ns.bindRunLineControls(host, 'coco_pharma', { checkNow: async () => { repaints += 1; paint(); } });
  paint();
  host.querySelector('[data-process-cancel]').click();
  await host.querySelector('[data-process-cancel-yes]').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(repaints, 1, 'checkNow repainted the line');
  assert.match(norm(host), /too late to cancel · the run had already finished/, 'kept across the repaint');
  host.querySelector('[data-process-cancel]').click();          // the next press clears it
  assert.doesNotMatch(norm(host), /too late to cancel/);
});

// ── card fixes after the live Cancel test (2026-10-18) ─────────────────────────────────────────────────────────────
const cancelledRun = (over = {}) => run({ state: 'failed', egeria_status: 'CANCELLED',
  cancel: { state: 'done', by: 'peterprofile', at: '2026-10-18T07:41:00', finished: 0, of: 3 }, ...over });

test('D1: a step cancelled after its publish began reads "its report is kept", never "will publish nothing"', async () => {
  const { document, ns } = await setUp();
  const kept = draw(document, ns, row(cancelledRun(), { finishing: { step: 2, label: 'postgres_operations', report_kept: true,
    publish_started_at: '2026-10-18T07:40:08Z', report_guid: 'becae45f' } }));
  const cue = kept.querySelector('[data-process-cancel-finishing]');
  assert.equal(norm(cue), '⊘ cancelled · step 2, postgres_operations, is finishing · its report is kept');
  assert.ok(cue.hasAttribute('data-process-cancel-report-kept'));
  assert.doesNotMatch(norm(kept), /will publish nothing/);
  // Publishing began, no report element proven yet: "being written", not "kept".
  const writing = draw(document, ns, row(cancelledRun(), { finishing: { step: 2, label: 'postgres_operations', report_kept: false,
    publish_started_at: '2026-10-18T07:40:08Z', report_guid: '' } }));
  assert.equal(norm(writing.querySelector('[data-process-cancel-finishing]')),
    '⊘ cancelled · step 2, postgres_operations, is finishing · its report is being written');
  assert.doesNotMatch(norm(writing), /report is kept|will publish nothing/);
  // No publish proof on the row: the earlier words stay.
  assert.match(norm(draw(document, ns, row(cancelledRun(), { finishing: { step: 2, label: 'postgres_operations' } }))),
    /⊘ cancelled · step 2, postgres_operations, is finishing and will publish nothing/);
});

test("D2: the done line carries a cancelled step's kept report as a report link; the count is unchanged", async () => {
  const { document, ns } = await setUp();
  const box = draw(document, ns, row(cancelledRun(), { kept: [{ step: 2, guid: 'becae45f' }] }));
  const done = box.querySelector('[data-process-cancel-done]');
  assert.match(norm(done), /^⊘ cancelled by peterprofile · \d\d:\d\d · 0 of 3 steps finished · step 2's report kept ›$/);
  const link = done.querySelector('[data-native-report]');
  assert.equal(link.dataset.nativeReport, 'becae45f');
  assert.ok(link.hasAttribute('data-process-cancel-kept-report'));
  // None kept: the plain done line, no link.
  const plain = draw(document, ns, row(cancelledRun())).querySelector('[data-process-cancel-done]');
  assert.equal(plain.querySelector('[data-native-report]'), null);
  assert.equal(ns.PROCESS_CANCEL_WORDS.doneKept(2), "step 2's report kept ›");
});

test('D3: the runs history says "cancelled" (muted) for a cancelled run and plain "failed" for a real failure', async () => {
  const { document, ns } = await setUp();
  const box = document.createElement('div');
  box.innerHTML = ns.definitionRunsListHtml({ runs: [
    { when: '2026-10-18T07:39:59', coordinated_by: 'egeria', by: 'peterprofile', state: 'cancelled', ran: null, of: null, reports: [] },
    { when: '2026-10-17T07:39:59', coordinated_by: 'egeria', by: 'dan', state: 'failed', ran: null, of: null, reports: [] }] });
  const [cancelled, failed] = [...box.querySelectorAll('[data-def-run]')];
  assert.match(norm(cancelled), /coordinated by Egeria · started by peterprofile · cancelled$/);
  assert.doesNotMatch(norm(cancelled), /failed/);
  const cw = cancelled.querySelector('[data-run-state="cancelled"]');
  assert.ok(cw.classList.contains('text-ink-muted') && !cw.classList.contains('text-state-warn'));
  // "failed" stays the list's plain muted text (no warn tone, no marker): only the cancel is drawn apart.
  assert.equal(failed.querySelector('.text-state-warn'), null);
  assert.equal(failed.querySelector('[data-run-state]'), null);
  assert.equal(ns.PROCESS_CANCEL_WORDS.historyCancelled, 'cancelled');
});

test('fix 2: the last-run summary of a cancelled run is muted ⊘ "cancelled", never ⚠ or a failure', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const when = new Date(Date.now() - 2 * 86400000).toISOString();
  const out = app.lastRunHtml({ last_run_at: when, last_run_status: 'cancelled', last_run_coordinated_by: 'egeria',
    last_run_errors: ['step 1 x failed: y'] });
  assert.ok(out.includes('⊘') && !out.includes('⚠') && !out.includes('✓'), out);
  assert.match(out, /text-ink-muted/);
  assert.match(out, /· cancelled"/);
  assert.ok(app.lastRunHtml({ last_run_at: when, last_run_status: 'failed' }).includes('⚠'));
});
