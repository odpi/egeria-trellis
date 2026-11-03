/** Panel A of the designer's state-as-visual-cue drawing: the choice cell is a two-part selector
 *  ("Include in Egeria?" · Include | Leave out · ×), the chosen segment filled in ink; a press dims
 *  the selector and says "saving…" in that cell; the record's answer fills the segment and prints
 *  "saved · you · just now" there, in the SAME render as the PUT's answer (not after the next scope
 *  read); a failed PUT rolls the cell back with a visible "not saved"; a second press while one is out
 *  sends nothing; a left-out row is LOWERED across its width and stays lowered after a redraw. Cues:
 *  fill = chosen, lowered = not included, a left-edge rule = needs you; no accent for state. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, flat, wait, calls, person } from './scope-test-kit.mjs';

const PREVIEW = { manifest: { lines: [] }, can_commit: false, blockers: [], button: 'Publish · 0 schemas', leave_out: [], refused: [], collisions: [] };
async function open(view, opts) {
  const ctx = await setUp(view, opts);
  ctx.server.preview = PREVIEW;
  return ctx;
}
const cell = (d, key) => row(d, key).querySelector('[data-scope-choice-cell]');
const seg = (d, key, choice) => row(d, key).querySelector(`[data-scope-act="set"][data-scope-choice="${choice}"]`);
const press = (d, key, choice) => seg(d, key, choice).click();
const hold = () => { let release; const p = new Promise((r) => { release = r; }); return [p, release]; };
const filled = (b) => b.className.includes('bg-ink') && b.getAttribute('aria-pressed') === 'true';

test('the header asks the question and the cell answers with a selector: Include | Leave out, × when decided', async () => {
  const v = baseView();
  v.schemas[0].explicit = person('catalogue'); v.schemas[0].effective = 'catalogue'; v.schemas[0].state = 'chosen';
  const { document } = await open(v);
  assert.equal(flat(document.querySelector('[data-scope-choice-head]')), 'Include in Egeria?');
  const sel = cell(document, 'schema:sales').querySelector('[data-scope-selector]');
  assert.equal(sel.getAttribute('role'), 'group');
  assert.equal(sel.getAttribute('aria-label'), 'Include in Egeria?');
  assert.deepEqual([...sel.querySelectorAll('button')].map((b) => flat(b)), ['Include', 'Leave out', '×']);
  assert.ok(filled(seg(document, 'schema:sales', 'catalogue')), 'the chosen segment is filled in ink');
  assert.ok(!filled(seg(document, 'schema:sales', 'leave_out')) && seg(document, 'schema:sales', 'leave_out').className.includes('border-rule-strong'), 'the other is outlined');
  assert.equal(sel.querySelector('[data-scope-act="clear"]').getAttribute('aria-label'), 'clear the choice (undecided)');
  // undecided: neither filled and no ×
  const u = cell(document, 'schema:archive').querySelector('[data-scope-selector]');
  assert.equal(u.querySelectorAll('[aria-pressed="true"]').length, 0);
  assert.equal(u.querySelector('[data-scope-act="clear"]'), null);
  assert.ok(!/[Cc]atalog/.test(flat(cell(document, 'schema:sales'))), 'the word catalog is not on the row');
});

test('a three-mark legend above the table: filled, lowered, tagged', async () => {
  const { document } = await open(baseView());
  assert.equal(flat(document.querySelector('[data-scope-legend]')), 'filled = your choice · lowered row = not included · bordered tag = differs or from a scope rule');
});

test('a left-out row is lowered across its width, not struck through; an included one is not', async () => {
  const v = baseView();
  v.schemas[1].explicit = person('leave_out'); v.schemas[1].effective = 'leave_out'; v.schemas[1].state = 'chosen';
  v.schemas[0].explicit = person('catalogue'); v.schemas[0].effective = 'catalogue'; v.schemas[0].state = 'chosen';
  const { document } = await open(v);
  const low = row(document, 'schema:archive');
  assert.ok(low.hasAttribute('data-scope-lowered') && low.className.includes('text-ink-muted') && low.className.includes('opacity-70'));
  assert.ok(!/line-through/.test(low.innerHTML), 'strike-through means superseded text only');
  assert.ok(!row(document, 'schema:sales').hasAttribute('data-scope-lowered'));
  for (const k of ['schema:sales', 'schema:archive', 'schema:empty_one']) assert.ok(!/accent/.test(row(document, k).className), 'no accent colour for state');
});

test('a row deleted in Egeria lowers like a left-out one, and says "deleted" with no glyph', async () => {
  const v = baseView();
  v.commit.schemas.sales = { state: 'deleted', words: 'deleted in Egeria · 10-05 09:30', second: '' };
  const { document } = await open(v);
  assert.ok(row(document, 'schema:sales').hasAttribute('data-scope-lowered'));
  const w = row(document, 'schema:sales').querySelector('[data-scope-egeria-state]');
  assert.equal(flat(w), 'deleted in Egeria · 10-05 09:30');
});

test('a failed or disagreeing row gets a left-edge rule: it needs you', async () => {
  const v = baseView();
  v.commit.schemas.archive = { state: 'failed', words: 'failed · AUTHORIZATION_ERROR_401', second: '' };
  const { document } = await open(v);
  assert.ok(row(document, 'schema:archive').className.includes('border-l-ink'));
  assert.ok(row(document, 'schema:sales').className.includes('border-l-transparent'));
});

test('on press the selector dims and disables, the cell says "saving…", and the segment is already filled', async () => {
  const ctx = await open(baseView());
  const g = hold(); ctx.server.holdPut = g[0];
  press(ctx.document, 'schema:sales', 'leave_out');
  await wait(15);
  const c = cell(ctx.document, 'schema:sales');
  assert.match(flat(c), /saving…/);
  assert.equal(c.getAttribute('aria-busy'), 'true');
  const btns = [...c.querySelectorAll('[data-scope-act]')];
  assert.ok(btns.length && btns.every((b) => b.disabled && b.className.includes('opacity-60')), 'dimmed and disabled');
  assert.ok(filled(seg(ctx.document, 'schema:sales', 'leave_out')), 'the pressed segment shows its value at once');
  assert.ok(row(ctx.document, 'schema:sales').hasAttribute('data-scope-lowered'), 'a left-out row lowers at once');
  g[1]();
  await wait(60);
});

test('a second press on the same row while the first is out sends no second PUT; another row is not blocked', async () => {
  const ctx = await open(baseView());
  const g = hold(); ctx.server.holdPut = g[0];
  press(ctx.document, 'schema:sales', 'leave_out');
  await wait(15);
  seg(ctx.document, 'schema:sales', 'leave_out').click();
  seg(ctx.document, 'schema:sales', 'catalogue').click();
  g[1]();
  await wait(80);
  assert.equal(calls(ctx.server, 'PUT', '/node').length, 1);
  press(ctx.document, 'schema:archive', 'catalogue');
  await wait(60);
  assert.equal(calls(ctx.server, 'PUT', '/node').length, 2);
});

test('the cell shows the answer and "saved · you · just now" when the PUT has answered and the scope GET is still pending', async () => {
  const ctx = await open(baseView());
  const g = hold(); ctx.server.holdPut = g[0];
  press(ctx.document, 'schema:sales', 'catalogue');
  await wait(15);
  const getGate = hold();
  ctx.server.holdGet = getGate[0];       // from now on the scope read hangs
  g[1]();
  await wait(60);
  const c = cell(ctx.document, 'schema:sales');
  assert.ok(filled(seg(ctx.document, 'schema:sales', 'catalogue')));
  const note = c.querySelector('[data-scope-saved-note]');
  assert.equal(flat(note), 'saved · you · just now');
  assert.match(note.title, /^saved in Resource Explorer · me · .* · not yet published to Egeria$/);
  assert.equal(c.querySelector('[data-scope-saving]'), null);
  assert.equal(c.getAttribute('aria-busy'), null);
  assert.ok(calls(ctx.server, 'GET', '/api/catalogue-scope/adventureworks').filter((x) => !x.url.includes('/commit')).length >= 2, 'the scope read had begun and is still pending');
  getGate[1]();
  await wait(80);
});

test('a failed PUT rolls the cell back and shows "not saved" with the cause', async () => {
  const ctx = await open(baseView());
  ctx.server.failPut = 'disk is full';
  press(ctx.document, 'schema:sales', 'leave_out');
  await wait(80);
  const r = row(ctx.document, 'schema:sales');
  assert.ok(!filled(seg(ctx.document, 'schema:sales', 'leave_out')), 'back to what it was');
  assert.ok(!r.hasAttribute('data-scope-lowered'));
  assert.match(flat(r.querySelector('[data-scope-not-saved]')), /^✕ not saved · disk is full/);
  assert.equal(r.querySelector('[data-scope-saving]'), null);
  assert.ok([...r.querySelectorAll('[data-scope-act]')].every((b) => !b.disabled), 'and it can be pressed again');
});

test('a 401 on a choice returns the cell to its previous look, says the session expired, keeps the choice as "unsaved" and offers save again', async () => {
  const ctx = await open(baseView());
  ctx.server.signedIn = false;
  press(ctx.document, 'schema:sales', 'leave_out');
  await wait(80);
  const r = row(ctx.document, 'schema:sales');
  assert.ok(!filled(seg(ctx.document, 'schema:sales', 'leave_out')) && !r.hasAttribute('data-scope-lowered'));
  assert.match(flat(r.querySelector('[data-scope-not-saved]')), /^✕ unsaved · your session expired · sign in again/);
  assert.ok(r.querySelector('[data-scope-sign-in]'));
  ctx.server.signedIn = true;                       // the person signed in again
  ctx.server.calls.length = 0;
  r.querySelector('[data-scope-save-again]').click();
  await wait(80);
  const [put] = calls(ctx.server, 'PUT', '/node');
  assert.deepEqual(put.body, { schema_name: 'sales', table_name: '', choice: 'leave_out' }, 'the same choice is re-sent: nothing was lost');
  assert.ok(filled(seg(ctx.document, 'schema:sales', 'leave_out')));
});

test('a 401 on a bulk press says so in the status line and offers save again', async () => {
  const ctx = await open(baseView());
  ctx.server.signedIn = false;
  const b = ctx.document.querySelector('[data-scope-select="archive"]'); b.checked = true; b.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  ctx.document.querySelector('[data-scope-bulk-act="leave_out"]').click();
  await wait(80);
  const st = ctx.document.querySelector('[data-scope-status]');
  assert.match(flat(st), /your session expired · sign in again/);
  assert.ok(st.querySelector('[data-scope-save-again]') && st.querySelector('[data-scope-sign-in]'));
  assert.ok(!row(ctx.document, 'schema:archive').hasAttribute('data-scope-lowered'), 'the row is back to its previous look');
});

test('the flash is brief', async () => {
  const mod = await import('/static/next/stages/curate-scope.js');
  mod.setFlashMs(40);
  const ctx = await open(baseView());
  press(ctx.document, 'schema:sales', 'catalogue');
  await wait(25);
  assert.ok(row(ctx.document, 'schema:sales').className.includes('bg-paper-surface'));
  await wait(120);
  assert.ok(!row(ctx.document, 'schema:sales').className.includes('bg-paper-surface'));
  mod.setFlashMs(1500);
});

test('the lowered row survives a redraw (a write elsewhere, the filter)', async () => {
  const v = baseView();
  v.schemas[1].explicit = person('leave_out'); v.schemas[1].effective = 'leave_out'; v.schemas[1].state = 'chosen';
  const ctx = await open(v);
  press(ctx.document, 'schema:sales', 'catalogue');
  await wait(100);
  assert.ok(row(ctx.document, 'schema:archive').hasAttribute('data-scope-lowered'), 'after the re-render that follows a write');
  const box = ctx.document.querySelector('[data-scope-filter]');
  box.value = 'arch'; box.dispatchEvent(new ctx.window.Event('input', { bubbles: true }));
  await wait(300);
  assert.ok(row(ctx.document, 'schema:archive').hasAttribute('data-scope-lowered'), 'after the filter redraws the tree');
});

test('a bulk press shows the new state on the ticked rows at once, and a second bulk press is ignored', async () => {
  const ctx = await open(baseView());
  const g = hold(); ctx.server.holdPut = g[0];
  const b = ctx.document.querySelector('[data-scope-select="archive"]'); b.checked = true; b.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  const bulk = () => ctx.document.querySelector('[data-scope-bulk-act="leave_out"]');
  bulk().click();
  await wait(15);
  assert.ok(filled(seg(ctx.document, 'schema:archive', 'leave_out')));
  assert.match(flat(cell(ctx.document, 'schema:archive')), /saving…/);
  assert.equal(bulk().disabled, true);
  bulk().click();
  g[1]();
  await wait(80);
  assert.equal(calls(ctx.server, 'POST', '/nodes').length, 1);
});
