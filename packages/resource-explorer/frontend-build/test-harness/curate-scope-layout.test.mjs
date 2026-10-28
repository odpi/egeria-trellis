/** Layout of the Curate scope table at the owner's laptop width (about 1440 px with Chat open):
 *  the page never scrolls sideways; the table scrolls or wraps INSIDE its own box, and no header
 *  is clipped to a stub ("CLAS"). jsdom has no layout engine, so this pins the structure that
 *  produces the behaviour: the scroll wrapper, the wrapping classes, the full header words. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, scopeEl, flat } from './scope-test-kit.mjs';

test('the scope table sits in its own horizontal scroll box that may shrink inside the pane', async () => {
  const { document } = await setUp(baseView());
  const box = scopeEl(document).querySelector('[data-scope-tree]');
  for (const c of ['overflow-x-auto', 'min-w-0', 'max-w-full']) {
    assert.ok(box.classList.contains(c), `the scroll wrapper has ${c}`);
  }
  assert.equal(scopeEl(document).closest('[data-curate-work]').classList.contains('min-w-0'), true,
    'the section that holds it can shrink too (a grid or flex child would otherwise widen the page)');
});

test('every header is a full word on ONE line (ellipsis + full title if narrowed), never broken mid-word', async () => {
  const { document } = await setUp(baseView());
  const head = scopeEl(document).querySelector('[data-scope-tree-head]');
  const words = [...head.children].map((c) => flat(c)).filter(Boolean);
  assert.deepEqual(words, ['Include in Egeria?', 'Schema / table', 'Rows', 'Size', 'Activity', 'Classification', 'In Egeria']);
  for (const c of [...head.children].filter((x) => flat(x))) {
    const st = c.getAttribute('style');
    assert.match(st, /white-space:nowrap/, `${flat(c)}: no line break inside a header`);
    assert.match(st, /text-overflow:ellipsis/, `${flat(c)}: ellipsis when narrowed`);
    assert.equal(c.title.length > 0, true, `${flat(c)}: the full text rides in the title`);
    assert.ok(!c.classList.contains('break-words'), 'a header never breaks mid-word');
  }
  assert.ok(!/\b(truncate|text-ellipsis)\b/.test(scopeEl(document).querySelector('[data-scope-tree]').innerHTML),
    'no body cell in the table truncates');
});

test('the last two columns wrap their text', async () => {
  const { document } = await setUp(baseView());
  const r = scopeEl(document).querySelector('[data-scope-row="schema:sales"]');
  for (const sel of ['[data-scope-classes-cell]', '[data-scope-state-cell]', '[data-scope-lastwrite-cell]']) {
    assert.ok(r.querySelector(sel).classList.contains('break-words'), `${sel} wraps`);
  }
});
