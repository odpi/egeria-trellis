/** Shortened chart labels stay distinguishable, and each tick's tooltip is the right full name. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered, makeDomEnvironment } from './dom-harness.mjs';

ensureLoaderRegistered();

const A = 'purchasing.purchaseorderheader';
const B = 'purchasing.purchaseorderdetail';
const hbar = (names) => ({ data: [{ type: 'bar', orientation: 'h', y: names, x: names.map((_, i) => 10 - i) }], layout: {} });

test('two names that would shorten alike get two different shown labels, within the width budget', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const fit = cl.fitFigure(hbar(['sales.salesorderheader', 'production.product', B, A]), 800, 'Largest tables');
  const tt = fit.layout.yaxis.ticktext;
  assert.notEqual(tt[2], tt[3]);
  assert.ok(tt.every((s) => s.length <= 24), 'within MAX_Y_CHARS');
  assert.ok(tt[2].endsWith('orderdetail') || tt[2].endsWith('aseorderdetail'), tt[2]);
  assert.ok(tt[3].includes('…') && tt[3].endsWith('orderheader'), tt[3]);
  assert.deepEqual(fit.ticks.y.map((t) => t.full), ['sales.salesorderheader', 'production.product', B, A]);
});

test('unique names are unchanged (head ellipsis; short names untouched)', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const names = ['short', 'a_really_long_table_name_number_one', 'another_really_long_table_name_two'];
  const tt = cl.fitFigure(hbar(names), 800).layout.yaxis.ticktext;
  assert.deepEqual(tt, names.map((s) => cl.ellipsize(s, 24)));
  assert.equal(tt[0], 'short');
});

test('category x labels that collide are distinguished too', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const x = ['timestamp without time zone', 'timestamp with time zone'];
  const fit = cl.fitFigure({ data: [{ type: 'bar', x, y: [3, 2] }], layout: {} }, 900);
  const tt = fit.layout.xaxis.ticktext;
  assert.equal(new Set(tt).size, 2);
  assert.ok(tt.every((s) => s.length <= 16));
});

test('the summary still lists the full names', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const fit = cl.fitFigure(hbar([A, B]), 800, 'Largest tables');
  assert.match(fit.summary, new RegExp(A));
  assert.match(fit.summary, new RegExp(B));
});

test('annotateTicks gives each tick its own full name by position, even when shown text is equal', async () => {
  const cl = await import('/static/next/chart-labels.js');
  const env = makeDomEnvironment ? await makeDomEnvironment() : null;
  const doc = (env && env.document) || globalThis.document;
  const NS = 'http://www.w3.org/2000/svg';
  const plot = doc.createElement('div');
  const ticks = { x: [], y: [{ shown: 'same…', full: 'one-full' }, { shown: 'same…', full: 'two-full' }, { shown: 'ok', full: 'ok' }] };
  ticks.y.forEach((t) => {
    const g = doc.createElementNS(NS, 'g'); g.setAttribute('class', 'ytick');
    const tx = doc.createElementNS(NS, 'text'); tx.textContent = t.shown; g.appendChild(tx); plot.appendChild(g);
  });
  cl.annotateTicks(plot, [], 'sum', ticks);
  const titles = [...plot.querySelectorAll('.ytick text')].map((t) => t.querySelector('title')?.textContent ?? null);
  assert.deepEqual(titles, ['one-full', 'two-full', null]);
  assert.equal(plot.getAttribute('aria-label'), 'sum');
  const first = plot.querySelector('.ytick text');
  assert.equal(first.style.pointerEvents, 'all');
});
