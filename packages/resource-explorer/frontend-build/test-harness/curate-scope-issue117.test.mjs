/** ISSUE-117: a left-out schema that is cataloged in Egeria shows a visible "kept · not sent" mark on its row
 *  (short mark + short word, the sentence in the title); and every row begins with a bullet, schema blocks with a rule. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { baseView, setUp, row, flat, person } from './scope-test-kit.mjs';

test('a left-out schema cataloged in Egeria says "kept · not sent" on its row, with the reason on hover', async () => {
  const v = baseView();
  v.schemas[0].explicit = person('leave_out'); v.schemas[0].effective = 'leave_out'; v.schemas[0].state = 'chosen';
  v.commit.schemas.sales = { state: 'catalogued', words: 'published · 2 tables', second: '' };
  const { document } = await setUp(v);
  const b = row(document, 'schema:sales').querySelector('[data-scope-blocked]');
  assert.ok(b, 'the blocked state is on the row');
  assert.match(flat(b), /^⛔ kept · not sent why Egeria archives the whole database tree/);
  assert.match(b.getAttribute('title'), /ISSUE-117, archiveBeanInRepository\) · choice kept, nothing sent$/);
});

test('no blocked mark for an included schema, a never-cataloged one, or a table', async () => {
  const v = baseView();
  v.schemas[1].explicit = person('leave_out'); v.schemas[1].effective = 'leave_out'; v.schemas[1].state = 'chosen';
  v.commit.schemas.sales = { state: 'catalogued', words: 'published', second: '' };   // included, cataloged
  const { document } = await setUp(v);
  assert.equal(document.querySelectorAll('[data-scope-blocked]').length, 0);
});

test('every row begins with a bullet and each schema block with a rule', async () => {
  const { document } = await setUp(baseView());
  document.querySelector('[data-scope-toggle="sales"]').click();
  assert.equal(row(document, 'schema:sales').querySelector('[data-scope-bullet]').textContent, '▪');
  assert.equal(row(document, 'table:sales.orders').querySelector('[data-scope-bullet]').textContent, '·');
  for (const blk of document.querySelectorAll('[data-scope-schema-block]')) {
    assert.ok(blk.className.includes('border-t') && blk.className.includes('border-rule-strong'));
  }
});
