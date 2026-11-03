/** Brief Z2: a schema the person may not change shows as a cue plus a short word ("not permitted"), never a
 *  sentence; the reason is on demand (the cue's title). The leave-out row the server held for the same reason
 *  is not drawn a second time, and a commit where nothing is permitted says only "not permitted" by its button. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const env = makeDomEnvironment();
await loadAppModule();
const { commitPanelHtml, commitWhyNot } = await import('/static/next/stages/curate-scope.js');

const REASON = 'zone finance-zone does not grant dana attach in Egeria';
const L = (id, mechanism, text) => ({ id, mechanism, text });
const base = (over = {}) => ({
  can_commit: true, button: 'Publish · 1 schema', blockers: [], attach: ['archive'], refused: [], collisions: [],
  not_permitted: [
    { schema: 'sales', write: 'attach', reason: REASON, words: `not permitted · ${REASON}`, text: `sales: not permitted · ${REASON}` },
    { schema: 'hr', write: 'leave out', reason: REASON, words: `not permitted · ${REASON}`, text: `hr: not permitted · ${REASON}` },
  ],
  leave_out: [{ schema: 'hr', form: 'not_permitted', blocked: true, reason: `not permitted · ${REASON}`, text: `hr: not permitted · ${REASON}` }],
  survey: { schemas: ['archive'] },
  manifest: { lines: [L('re_publishes', 1, 'RE publishes.'), L('cataloguer_creates', 2, 'Creates.'), L('survey_measures', 3, 'Survey.'), L('whole_schemas', 0, 'Whole.')] },
  ...over,
});
const panel = (preview) => {
  const div = env.document.createElement('div');
  div.innerHTML = commitPanelHtml(preview, { user_id: 'dana' }, null, true, { counts: { schemas_undecided: 0 } });
  env.document.body.append(div);
  return div;
};

test('each part not permitted is a no-access cue and the word, with the reason on demand', () => {
  const p = panel(base());
  const rows = [...p.querySelectorAll('[data-scope-not-permitted]')];
  assert.deepEqual(rows.map((r) => r.getAttribute('data-scope-not-permitted')), ['sales', 'hr']);
  for (const r of rows) {
    const c = r.querySelector('[data-cue]');
    assert.equal(c.getAttribute('data-cue'), 'no_access');
    assert.equal(c.getAttribute('title'), REASON, 'the reason is on demand');
    assert.equal(r.textContent.replace(/\s+/g, ' ').trim().replace(/^\S+ /, ''), `not permitted ${r.getAttribute('data-scope-not-permitted')}`);
    assert.ok(!r.textContent.includes('does not grant'), 'no sentence on the row');
  }
  assert.equal(p.querySelectorAll('[data-scope-leave-out="hr"]').length, 0, 'the held leave-out is not drawn twice');
});

test('nothing permitted: the button says only "not permitted", and no blocker sentence is drawn', () => {
  const pv = base({ can_commit: false, all_refused: true, attach: [], blockers: [`not permitted · ${REASON}`] });
  assert.equal(commitWhyNot(pv), 'not permitted');
  const p = panel(pv);
  assert.equal(p.querySelectorAll('[data-scope-blocker]').length, 0);
  assert.equal(p.querySelector('[data-scope-commit-why]').textContent.trim(), '⚠ not permitted');
});

test('refused whole with the archive-cascade block too: the block stays visible as a cue and a short word', () => {
  const block = 'Egeria archives the whole database tree when any part of it is archived';
  const pv = base({ can_commit: false, all_refused: true, attach: [], blockers: [`not permitted · ${REASON}`],
    also_blocked: [{ word: 'archive-cascade block', reason: `hr: ${block}` }] });
  const p = panel(pv);
  const also = [...p.querySelectorAll('[data-scope-also-blocked]')];
  assert.equal(also.length, 1);
  const c = also[0].querySelector('[data-cue]');
  assert.equal(c.getAttribute('data-cue'), 'human');
  assert.equal(c.getAttribute('title'), `hr: ${block}`, 'the reason is on demand');
  assert.ok(also[0].textContent.includes('archive-cascade block') && !also[0].textContent.includes('archives'));
});
