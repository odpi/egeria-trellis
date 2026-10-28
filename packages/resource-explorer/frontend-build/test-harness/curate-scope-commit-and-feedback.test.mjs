/** The commit control is an unmistakable button under the manifest, and a choice write says what it
 *  did: 'saved in Resource Explorer', not yet in Egeria. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, flat, wait } from './scope-test-kit.mjs';

const PREVIEW = (over = {}) => ({
  manifest: { lines: [{ id: 're_publishes', mechanism: 1, text: 'RE publishes the server and database assets.' },
    { id: 'whole_schemas', mechanism: 0, text: 'Egeria adds whole schemas' }] },
  can_commit: true, button: 'Publish · 2 schemas', blockers: [], leave_out: [], refused: [], collisions: [], ...over,
});
const declaredView = () => baseView({ declared: { declared: true, by: 'me', at: '2026-10-04T08:00:00', kind: 'declare', baseline_survey_at: '' } });
async function open(view, preview, opts) {
  const ctx = await setUp(view, opts);
  ctx.server.preview = preview;
  const fold = ctx.document.querySelector('[data-scope-collapse]');
  if (fold.getAttribute('aria-expanded') === 'false') { fold.click(); await wait(); }
  return ctx;
}
const btn = (d) => d.querySelector('[data-scope-commit-btn]');

test('the commit control is a filled primary button at the top right of the manifest table, with the refresh box under it', async () => {
  const { document } = await open(declaredView(), PREVIEW());
  const b = btn(document);
  assert.equal(b.tagName, 'BUTTON');
  assert.equal(flat(b), 'Publish · 2 schemas');
  assert.ok(b.className.includes('bg-accent') && b.className.includes('font-semibold'), 'filled, not link-styled text');
  assert.ok(!b.className.includes('underline'));
  const rowEl = b.closest('[data-scope-commit-row]');
  assert.ok(rowEl.querySelector('[data-scope-refresh-now]'), 'the refresh checkbox is under the button, in the same column');
  assert.equal(rowEl.querySelector('[data-scope-read-back]'), null, 'Read Egeria again is not on the primary column');
  const panel = document.querySelector('[data-scope-commit-panel]');
  const order = [...panel.querySelectorAll('[data-scope-manifest], [data-scope-commit-row], [data-scope-read-back]')]
    .map((e) => (e.hasAttribute('data-scope-manifest') ? 'manifest' : e.hasAttribute('data-scope-commit-row') ? 'button' : 'read'));
  assert.deepEqual(order, ['manifest', 'button', 'read']);
  assert.ok(document.querySelector('[data-scope-read-back]').className.includes('underline'), 'secondary stays a link-style control');
});

test('a disabled commit button says why, with a warning word, directly under it', async () => {
  const nothing = await open(declaredView(), PREVIEW({ can_commit: false, button: 'Publish · 0 schemas', blockers: ['nothing to commit: choose at least one schema to publish'] }));
  assert.equal(btn(nothing.document).disabled, true);
  assert.match(flat(nothing.document.querySelector('[data-scope-commit-why]')), /^⚠ nothing chosen/);
  assert.ok(btn(nothing.document).closest('[data-scope-commit-row]').contains(nothing.document.querySelector('[data-scope-commit-why]')));
  const coll = await open(declaredView(), PREVIEW({ can_commit: false, blockers: ["1 name collision Egeria's listing can't tell apart: leave the schema out, or rename it in the database"] }));
  assert.match(flat(coll.document.querySelector('[data-scope-commit-why]')), /^⚠ name collisions: /);
  const undeclared = await open(baseView(), PREVIEW({ can_commit: false, blockers: ['nothing to commit: choose at least one schema to publish'] }));
  assert.match(flat(undeclared.document.querySelector('[data-scope-commit-why]')), /^⚠ no scope declared/);
  const out = await open(declaredView(), PREVIEW(), { signedIn: false });
  assert.match(flat(out.document.querySelector('[data-scope-commit-why]')), /sign in/);
});

test('an enabled button has no reason text', async () => {
  const { document } = await open(declaredView(), PREVIEW());
  assert.equal(btn(document).disabled, false);
  assert.equal(document.querySelector('[data-scope-commit-why]'), null);
});

test('while the commit is sent the button says sending… and is disabled, then "sent · waiting for Egeria"', async () => {
  const ctx = await open(declaredView(), PREVIEW());
  let release; ctx.server.holdCommit = new Promise((r) => { release = r; });
  btn(ctx.document).click();
  await wait(20);
  assert.equal(flat(btn(ctx.document)), 'sending…');
  assert.equal(btn(ctx.document).disabled, true);
  release();
  await wait(60);
  assert.match(flat(ctx.document.querySelector('[data-scope-commit-sent]')), /^sent · waiting for Egeria$/);
  assert.ok(ctx.document.querySelector('[data-scope-commit-step]') || ctx.document.querySelector('[data-scope-commit-steps]'), 'the steps follow');
});

test('a choice write shows saving…, then a plain "saved in Resource Explorer" line that says Egeria is not touched, then fades', async () => {
  const mod = await import('/static/next/stages/curate-scope.js');
  mod.setSavedNoteMs(120);
  const ctx = await open(declaredView(), PREVIEW());
  let release; ctx.server.holdPut = new Promise((r) => { release = r; });
  row(ctx.document, 'schema:sales').querySelector('[data-scope-act="set"][data-scope-choice="catalogue"]').click();
  await wait(20);
  assert.match(flat(row(ctx.document, 'schema:sales').querySelector('[data-scope-choice-cell]')), /saving…/);
  release();
  await wait(60);
  const note = row(ctx.document, 'schema:sales').querySelector('[data-scope-saved-note]');
  assert.ok(note, 'the row says what happened');
  assert.equal(flat(note), 'saved · you · just now');
  assert.equal(note.getAttribute('title'), 'saved in Resource Explorer · me · 10-04 · not yet published to Egeria');
  assert.equal(ctx.document.querySelector('[role="dialog"]'), null, 'not a modal');
  await wait(220);
  assert.equal(row(ctx.document, 'schema:sales').querySelector('[data-scope-saved-note]'), null);
  assert.match(flat(row(ctx.document, 'schema:sales').querySelector('[data-scope-choice-cell]')), /include · set by me/, 'the choice itself stays');
  mod.setSavedNoteMs(6000);
});

test('the per-row choice is a two-part selector: Include | Leave out, and "catalog" is not on the row', async () => {
  const { document } = await open(declaredView(), PREVIEW());
  const r = row(document, 'schema:sales');
  assert.equal(flat(r.querySelector('[data-scope-act="set"][data-scope-choice="catalogue"]')), 'Include');
  assert.equal(flat(r.querySelector('[data-scope-act="set"][data-scope-choice="leave_out"]')), 'Leave out');
});

test('a step never says its state twice: "survey · submitted · 10-06 13:25", not "submitted · submitted"', async () => {
  await setUp(baseView());   // loads the real module graph
  const { commitStepsHtml } = await import('/static/next/stages/curate-scope.js');
  const rec = { id: 'cafe0123', state: 'running', author: 'me', steps: [
    { name: 'survey', state: 'submitted', detail: "submitted · 10-06 13:25 · Egeria's survey is limited to your chosen schemas: a, b · engine action 63547d8a · read back 10-06 13:27: running in Egeria · IN_PROGRESS · 6 annotations so far" },
    { name: 'survey_report', state: 'done', detail: 'report abcd1234 · 76 annotations published · from the 10-03 survey' },
    { name: 'read_back', state: 'done', detail: 'done · 2 of 2 read back' },
  ] };
  const host = document.createElement('div'); host.innerHTML = commitStepsHtml(rec);
  const t = (n) => flat(host.querySelector(`[data-scope-commit-step="${n}"]`));
  assert.match(t('survey'), /Egeria survey · submitted/);
  assert.doesNotMatch(t('survey'), /submitted · submitted/);
  assert.doesNotMatch(t('read_back'), /done · done/);
  assert.match(t('survey_report'), /Survey report · done/);
  assert.match(t('survey_report'), /report abcd1234 · 76 annotations published/);
});

test('a survey still running in Egeria says so with the count so far and a plain "check again in a minute" hint, no spinner', async () => {
  await setUp(baseView());
  const { commitStepsHtml } = await import('/static/next/stages/curate-scope.js');
  const rec = { id: 'cafe0123', state: 'running', author: 'me', steps: [
    { name: 'survey', state: 'submitted', detail: 'submitted · 10-06 13:25 · read back 10-06 13:27: running in Egeria · IN_PROGRESS · 6 annotations so far' } ] };
  const host = document.createElement('div'); host.innerHTML = commitStepsHtml(rec);
  const step = host.querySelector('[data-scope-commit-step="survey"]');
  assert.match(flat(step), /running in Egeria · IN_PROGRESS · 6 annotations so far/);
  assert.match(flat(step.querySelector('[data-scope-step-hint]')), /^running in Egeria · started 13:25 · .*6 annotations so far · usually takes about 15-25 minutes: come back and press Read Egeria again · check again · checking every 60 s$/);
  assert.equal(host.querySelector('[class*="animate-spin"], [role="progressbar"]'), null);
  // a finished survey carries no hint
  rec.steps[0] = { name: 'survey', state: 'done', detail: 'done · report abcd1234 · 9 annotations' };
  host.innerHTML = commitStepsHtml(rec);
  assert.equal(host.querySelector('[data-scope-step-hint]'), null);
});
