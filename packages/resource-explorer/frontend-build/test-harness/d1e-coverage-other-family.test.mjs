/** D1-e (design §14): Curate › By analysis draws the routine's in_other_family line like any other not_yet line,
 *  with its "which", and with no code of its own: curate-coverage.js renders lines generically. Pure render. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered, makeDomEnvironment } from './dom-harness.mjs';

ensureLoaderRegistered();
const PACK = 'Coco PostgreSQL Server 1';
const pack = Array.from({ length: 22 }, (_, i) => `pack_${String(i).padStart(2, '0')}`);
const line = (key, label, count, names = []) => ({ key, label, count, names, rows: {} });
const RESULT = {
  resource_type: 'database', slug: 'localhost_docker_coco_pharma', as_of: '2026-10-31T20:38:31Z', known_schemas: 30,
  envelope: { result_state: 'measured', result_state_reason: '' },
  groups: [
    { key: 'published', words: 'published · 4 schemas', lines: [line('schemas', 'schemas', 4)] },
    { key: 'not_yet', words: `not yet published · 26: 3 undecided · 22 in Egeria under '${PACK}' · not RE's · 1 left out by a confirmed proposal`,
      lines: [line('undecided', 'undecided', 3, ['demo', 'demo_auth', 'public']),
        { ...line('in_other_family', `in Egeria under '${PACK}' · not RE's`, 22, pack), rows: { catalogue_commit_proofs: [90] } },
        line('chosen', 'chosen by a person, not published', 0), line('chosen_confirmed', 'chosen by a confirmed proposal, not published', 0),
        line('left_out', 'left out by a person', 0), line('left_out_confirmed', 'left out by a confirmed proposal', 1, ['lab'])] },
    { key: 'blocked', words: 'blocked · none', lines: [line('blocked', 'blocked', 0)] },
  ],
};

test('the in_other_family line is drawn in not_yet, after undecided, with the family and its which', async () => {
  const { document } = makeDomEnvironment();
  const { coverageHtml } = await import('/static/next/stages/curate-coverage.js');
  document.body.innerHTML = coverageHtml(RESULT);
  const group = document.querySelector('[data-coverage-group="not_yet"]');
  assert.match(group.querySelector('h3').textContent, /22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's/);
  const keys = [...group.querySelectorAll('[data-coverage-line]')].map((li) => li.dataset.coverageLine);
  assert.deepEqual(keys.slice(0, 3), ['undecided', 'in_other_family', 'chosen']);
  const li = group.querySelector('[data-coverage-line="in_other_family"]');
  assert.equal(li.dataset.count, '22');
  assert.match(li.textContent, /^22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's/);
  assert.equal(li.querySelector('details summary').textContent, 'which');
  assert.equal(li.querySelector('details span').textContent, pack.join(', '));
});
