/** Declaration brief 2026-10-30, Part A, on the screen side: a graph's or distribution's declared categories (never stacked
 *  when they overlap; a stated total read from its field, never summed here) and a capped step line (a title saying the
 *  figure is a lower bound). Real graph-renderer.js and envelope.js, drawn into a DOM at 800 px and 1440 px. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { categoriesHtml, categoriesSpec, graphHtml, OVERLAP_NOTE } = await import('/static/next/graph-renderer.js');
const { readEnvelope } = await import('/static/next/envelope.js');

// #603's own case: the categories add up to 2,009; the result states 2,098.
const ITEMS = [{ label: 'tables', count: 1500 }, { label: 'views', count: 509 }];
const RESULT = { counts: { stated_total: 2098 } };
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

function draw(html, width) {
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  const host = document.createElement('div');
  host.style.width = `${width}px`;
  host.innerHTML = html;
  document.body.appendChild(host);
  return host;
}

for (const width of [800, 1440]) {
  test(`exclusive categories with a stated total show the stated total, not the sum, at ${width} px`, () => {
    const spec = categoriesSpec({ categories: { exclusive: true, sums_to: 'counts.stated_total' } }, RESULT, ITEMS);
    assert.equal(spec.total, 2098);
    const host = draw(categoriesHtml(spec), width);
    assert.ok(host.querySelector('[data-categories-stack]'), 'exclusive categories may stack');
    assert.match(text(host.querySelector('[data-categories-total]')), /2,098/);
    assert.doesNotMatch(text(host), /2,009/, 'the client-side sum is never shown');
    const widths = [...host.querySelectorAll('[data-stack-segment]')].map((s) => parseFloat(s.style.width));
    assert.ok(widths.reduce((a, b) => a + b, 0) < 100, 'bars are scaled to the stated total; the unexplained rest stays blank');
  });

  test(`overlapping categories are not stacked, say so, and state no total at ${width} px`, () => {
    const spec = categoriesSpec({ categories: { exclusive: false } }, RESULT, ITEMS);
    const host = draw(categoriesHtml(spec), width);
    assert.equal(host.querySelector('[data-categories-stack]'), null);
    assert.equal(host.querySelector('[data-categories-total]'), null);
    assert.match(text(host.querySelector('[data-categories-note]')), new RegExp(OVERLAP_NOTE));
    assert.equal(host.querySelectorAll('[data-category]').length, 2, 'each category is still listed, with its own count');
  });
}

test('exclusive categories with no stated total show no total at all', () => {
  const spec = categoriesSpec({ categories: { exclusive: true } }, RESULT, ITEMS);
  assert.equal(spec.total, null);
  assert.equal(draw(categoriesHtml(spec), 1440).querySelector('[data-categories-total]'), null);
});

test('a stated total the result does not hold is absent, not zero', () => {
  const spec = categoriesSpec({ categories: { exclusive: true, sums_to: 'missing.field' } }, RESULT, ITEMS);
  assert.equal(spec.total, null);
});

test('a graph carries its categories note beside the figure', () => {
  const html = graphHtml({ nodes: [{ id: 'a' }], edges: [], as_of: '2026-10-30',
    categories: categoriesSpec({ categories: { exclusive: false } }, {}, ITEMS) });
  assert.match(html, /data-graph-categories="overlapping"/);
});

test('a capped step line carries the lower-bound flag and an uncapped one does not', () => {
  const esc = (s) => String(s); const tnum = (s) => s;
  const env = { facts: [
    { analysis_id: 'a', library: false, is_known: true, state: 'measured', headline: 'x' },
    { analysis_id: 'b', library: true, is_known: true, state: 'measured', headline: 'at least 500 rows · not derived: the count is a lower bound', headline_capped: true },
    { analysis_id: 'c', library: true, is_known: true, state: 'measured', headline: '500 rows' },
  ] };
  const lines = readEnvelope({ analysis_ids: ['a'] }, env, esc, tnum);
  const by = Object.fromEntries(lines.stepLines.map((s) => [s.id, s]));
  assert.equal(by.b.capped, true);
  assert.equal(by.c.capped, false);
  assert.match(by.b.sentence, /^at least 500 rows/);
});

test('a step line carries the categories legend the server gave it, and none when there is none', () => {
  const esc = (s) => String(s); const tnum = (s) => s;
  const env = { facts: [
    { analysis_id: 'a', library: false, is_known: true, state: 'measured', headline: 'x' },
    { analysis_id: 'b', library: true, is_known: true, state: 'measured', headline: '3 tables', headline_categories: 'each item is in one category' },
    { analysis_id: 'c', library: true, is_known: true, state: 'measured', headline: '3 tables' },
  ] };
  const lines = readEnvelope({ analysis_ids: ['a'] }, env, esc, tnum);
  const by = Object.fromEntries(lines.stepLines.map((s) => [s.id, s]));
  assert.equal(by.b.categories, 'each item is in one category');
  assert.equal(by.c.categories, '');
});
