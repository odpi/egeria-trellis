/** Behaviour: the Schema Inventory line names Egeria's survey only as what it measured.
 *  A survey that never measured schemas reads "not measured", never "saw 0 schemas"; a
 *  measured survey prints its counts and ITS date (the 2026-10-21 coco_pharma rehearsal:
 *  "Egeria's survey saw 0 schemas, 0 tables, 10-20" for a database Egeria surveyed on
 *  10-17 with 20 schemas and 184 tables). scope-sources.js has no imports, so it loads through the harness loader. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { inventoryHeaderText, EGERIA_NOT_MEASURED } = await import('/static/next/stages/scope-sources.js');

const local = { state: 'measured', schema_count: 30, table_count: 200, surveyed_at: '2026-10-20T09:00:00' };
const chosen = { kind: 'local', as_of: '2026-10-20T09:00:00', schemas: 30 };

test('Egeria measured: its counts and its own date', () => {
  const text = inventoryHeaderText({ chosen, local, disagree: true,
    egeria: { state: 'measured', schema_count: 20, table_count: 184, surveyed_at: '2026-10-17T06:00:00' } });
  assert.equal(text, "30 schemas · RE local survey 10-20 · Egeria's survey saw 20 schemas, 184 tables, 10-17");
});

test('Egeria not measured: says so, never a 0', () => {
  const text = inventoryHeaderText({ chosen, local, disagree: false,
    egeria: { state: 'not_measured', schema_count: null, table_count: null, surveyed_at: '' } });
  assert.equal(text, `30 schemas · RE local survey 10-20 · ${EGERIA_NOT_MEASURED}`);
  assert.equal(EGERIA_NOT_MEASURED, "Egeria's survey has not measured schemas");
  assert.doesNotMatch(text, /saw|\b0\b/);
});

test('Egeria measured a true zero: it is still printed as a zero', () => {
  const text = inventoryHeaderText({ chosen, local, disagree: true,
    egeria: { state: 'measured', schema_count: 0, table_count: 0, surveyed_at: '2026-10-17T06:00:00' } });
  assert.match(text, /Egeria's survey saw 0 schemas, 0 tables, 10-17/);
});

test('Egeria is the chosen source: unchanged, and an unreadable local survey is still named', () => {
  const eg = { state: 'measured', schema_count: 20, table_count: 184, surveyed_at: '2026-10-17T06:00:00' };
  assert.equal(inventoryHeaderText({ chosen: { kind: 'egeria', as_of: eg.surveyed_at, schemas: 20 }, egeria: eg,
    local: { state: 'not_measured' }, disagree: false }), '20 schemas · Egeria survey 10-17');
  assert.equal(inventoryHeaderText({ chosen, egeria: { state: 'not_measured' }, local: { state: 'unreadable' }, disagree: false }),
    "30 schemas · RE local survey 10-20 · RE's own survey could not be read");
});
