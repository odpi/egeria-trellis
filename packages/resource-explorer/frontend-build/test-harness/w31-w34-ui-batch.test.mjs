/** W31-W34 (the Architect's read-backs of ui-batch W23-W30):
 *  W31 By analysis's "read back" uses the page's one time form, MM-DD HH:MM, never the raw as_of;
 *  W32 a portal's Modified column says YYYY-MM-DD, with the year (owner: "show the year for that column");
 *  W33 the portals table's Cost note keeps a readable width instead of one word per line;
 *  W34 the Admin dialog can shrink below the candidates table's min width, so only the results pane scrolls sideways.
 *  Real modules, a fake fetch. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { ensureLoaderRegistered, makeDomEnvironment } from './dom-harness.mjs';

ensureLoaderRegistered();
const { document } = makeDomEnvironment();
const src = (rel) => readFileSync(new URL(`../../resource_explorer/web/static/next/${rel}`, import.meta.url), 'utf8');

test('W31: the coverage header reads back in MM-DD HH:MM; the raw as_of rides in the title', async () => {
  const { coverageHtml } = await import('/static/next/stages/curate-coverage.js');
  const el = document.createElement('div');
  el.innerHTML = coverageHtml({ as_of: '2026-10-31T20:38:31.123456Z', groups: [],
    envelope: { result_state: 'nothing_found', result_state_reason: '' } });
  const at = el.querySelector('[data-coverage-as-of]');
  assert.equal(at.textContent, 'read back 10-31 20:38');
  assert.equal(at.getAttribute('title'), '2026-10-31T20:38:31.123456Z');
});

test("W31: stamp is the routine's _stamp: ISO, compact and date-only forms; unreadable text as it was", async () => {
  const { stamp } = await import('/static/next/format.js');
  assert.equal(stamp('2026-10-09T17:51:36.4'), '10-09 17:51');
  assert.equal(stamp('2026-10-09 17:51:36+00:00'), '10-09 17:51');
  assert.equal(stamp('20261006T2100Z'), '10-06 21:00');
  assert.equal(stamp('2026-10-09'), '10-09');
  assert.equal(stamp('soon'), 'soon');
  assert.equal(stamp(null), '');
});

test('W32: Modified shows YYYY-MM-DD, year included, for a date and for a date-time', async () => {
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '{}' });
  const od = await import('/static/next/admin/open_data.js');
  assert.equal(od.modifiedWords('2019-03-04'), '2019-03-04');
  assert.equal(od.modifiedWords('2021-10-17T14:31:00.123456'), '2021-10-17');   // the source's date, cut, no zone shift
  assert.equal(od.modifiedWords('2021-10-31T23:59:00Z'), '2021-10-31');
  assert.equal(od.modifiedWords('last spring'), 'last spring');
  assert.match(od.modifiedWords('not_reported'), /not reported by this source/);
});

test('W33: the Cost cell holds its note in a min-width box; the portals table scrolls in its own wrapper', () => {
  const s = src('admin/open_data.js');
  assert.match(s, /data-od-cost><div class="min-w-\[12rem\]">/);
  assert.match(s, /<div class="overflow-x-auto"><table class="w-full text-left" data-rc-id="od-portals">/);
});

test('W34: the Admin dialog may shrink below its content (min-w-0), so the results pane scrolls, not the panel', () => {
  assert.match(src('admin/index.js'), /<div class="mt-\[3vh\] w-full min-w-0 max-w-\[920px\]/);
  assert.match(src('admin/open_data.js'), /<div data-od-results class="overflow-x-auto"><\/div>/);
});

test('the new classes are in the built CSS', () => {
  const css = src('tailwind-next.css');
  assert.ok(css.includes('.min-w-0{'), 'min-w-0');
  assert.ok(css.includes('.min-w-\\[12rem\\]{'), 'min-w-[12rem]');
});
