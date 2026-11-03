/** D1-a: the IN EGERIA cell draws what Egeria holds at the database's endpoint BESIDE RE's own state (another family's
 *  element, or a duplicate link), and Contents draws the re-anchor plan from the newest read-back's endpoint row.
 *  A proposed duplicate is "possible duplicate · not confirmed", muted and with no in-Egeria glyph; a unique endpoint
 *  (no plan) draws nothing; the plan offers no press. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const env = makeDomEnvironment();
await loadAppModule();
const { egeriaStateHtml, reanchorPlanHtml } = await import('/static/next/stages/curate-scope.js');

const PACK = 'Coco PostgreSQL Server 1';
const draw = (html) => {
  const div = env.document.createElement('div');
  div.innerHTML = html;
  env.document.body.append(div);
  return div;
};
const cell = (st) => draw(egeriaStateHtml({ kind: 'schema', name: 'aus_inventory' }, { schemas: { aus_inventory: st } }));

test('a schema RE never published, held by the pack, reads in Egeria under the pack', () => {
  const d = cell({ state: 'none', words: '', second: '', endpoint: {
    state: 'in_egeria_other', words: `in Egeria under '${PACK}'`, second: 'element b798274a · found by endpoint', element_guid: 'b798274a' } });
  const ep = d.querySelector('[data-scope-endpoint-state]');
  assert.equal(ep.getAttribute('data-scope-endpoint-word'), 'in_egeria_other');
  assert.match(ep.textContent, /in Egeria under 'Coco PostgreSQL Server 1'/);
  assert.ok(!d.querySelector('[data-scope-egeria-word="none"]'), 'no dash when the endpoint says something');
});

test("RE's own state stays first; the endpoint line is added under it", () => {
  const d = cell({ state: 'left_out', words: 'left out', second: 'scope record · never published', endpoint: {
    state: 'in_egeria_other', words: `in Egeria under '${PACK}'`, second: '' } });
  const words = [...d.querySelectorAll('[data-scope-egeria-state], [data-scope-endpoint-state]')].map((x) => x.textContent.trim());
  assert.equal(words.length, 2);
  assert.match(words[0], /left out/);
  assert.match(words[1], /in Egeria under/);
});

test('a proposed duplicate is muted and never drawn as in Egeria', () => {
  const d = cell({ state: 'none', words: '', endpoint: { state: 'possible_duplicate', words: 'possible duplicate · not confirmed · X', second: '' } });
  const ep = d.querySelector('[data-scope-endpoint-state]');
  assert.ok(ep.className.includes('text-ink-muted'));
  assert.ok(!ep.textContent.startsWith('in Egeria'));
});

test('the plan lists one row per RE schema with both GUIDs, or that the pack has none', () => {
  const plan = { pairs: 1, words: 'this endpoint (…): 4 schemas RE\'s (…) · 22 schemas under \'Coco PostgreSQL Server 1\' · re-anchor plan: 1 of 2 RE schemas has a pack element of the same name · plan only, nothing is written',
    database_link_type: 'DataSetContent',
    rows: [
      { schema: 'coco_ods', re_guid: 'a6ea0faf-1', pack_guid: 'b798274a-2', pack_family: PACK, words: 're-anchor onto b798274a', hangs_off: { annotations: 12, rules: 0, catalog_target: 'ct-1234567' } },
      { schema: 'coco_sus', re_guid: '6c805aaa-1', pack_guid: '', pack_family: '', words: 'the other family has no schema of this name · nothing to re-anchor', hangs_off: { annotations: null, rules: 0, catalog_target: '' } },
    ] };
  plan.database_row = { re_guid: 'c990f349-1', link_type: 'DataSetContent', to_add: [{}, {}],
    words: "add DataSetContent from c990f349 to the pack's 22 schemas under 'Coco PostgreSQL Server 1' · after the press: 26 members" };
  const neither = { schemas: ['demo', 'public'], words: 'demo, public: not cataloged by RE or the pack' };
  const d = draw(reanchorPlanHtml({ plan, error: '', neither }));
  const db = d.querySelector('[data-scope-plan-database]');
  assert.equal(db.dataset.linkType, 'DataSetContent');
  assert.match(db.textContent, /after the press: 26 members/);
  assert.match(d.querySelector('[data-scope-plan-neither]').textContent, /not cataloged by RE or the pack/);
  const rows = [...d.querySelectorAll('[data-scope-plan-row]')];
  assert.deepEqual(rows.map((r) => [r.dataset.scopePlanRow, r.dataset.reGuid, r.dataset.packGuid]),
    [['coco_ods', 'a6ea0faf-1', 'b798274a-2'], ['coco_sus', '6c805aaa-1', '']]);
  assert.match(rows[0].textContent, /12 annotations naming it/);
  assert.match(rows[1].textContent, /annotations not read/, 'a count not read is never 0');
  assert.match(d.textContent, /database link read from Egeria: DataSetContent/);
  assert.equal(d.querySelectorAll('[data-scope-plan-row] button').length, 0, 'the schema rows are a plan only: no press');
  assert.equal(d.querySelectorAll('button').length, 1, 'the one press is the database row (D1-b)');
  assert.ok(d.querySelector('[data-scope-plan-database] [data-scope-reanchor-btn]').disabled, 'signed out here: off');
});

test('a unique endpoint draws no plan; a failed endpoint read says so', () => {
  assert.equal(reanchorPlanHtml(null), '');
  assert.equal(reanchorPlanHtml({ plan: null, error: '' }), '');
  const d = draw(reanchorPlanHtml({ plan: null, error: 'Egeria answered 503', words: 'not read · Egeria answered 503' }));
  assert.equal(d.querySelector('[data-scope-endpoint-plan]').dataset.scopeEndpointPlan, 'unread');
});
