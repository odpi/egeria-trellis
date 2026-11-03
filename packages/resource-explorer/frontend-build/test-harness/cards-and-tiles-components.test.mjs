/** ui-cards.js (PLAN-CARDS-AND-TILES.md, slice C1): one render-harness test per card state and per tile state, and
 *  the rules the code enforces (a tile opens what it counted, an absent figure is never a number, six in a row). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const m = await import('/static/next/ui-cards.js');
  const mount = (html) => { const d = document.createElement('div'); d.innerHTML = html; document.body.appendChild(d); return d; };
  return { m, mount };
}
const flat = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const cls = (el) => [...el.classList];

/* ── cards ─────────────────────────────────────────────────────────────── */

test('card anatomy: title and meta, actions at the top right of the header, body, provenance footer last', async () => {
  const { m, mount } = await load();
  const root = mount(m.cardHtml({ title: 'Survey A', meta: '6 steps', actions: '<button>Run…</button>', body: '<p>the run line</p>', footer: 'who · when' }));
  const card = root.querySelector('[data-ui-card]');
  assert.equal(card.dataset.uiCardState, 'ordinary');
  assert.deepEqual([...card.children].map((c) => c.tagName), ['HEADER', 'DIV', 'FOOTER'], 'header, body, footer, in that order');
  const header = card.querySelector('header');
  assert.equal(flat(header.querySelector('[data-card-title]')), 'Survey A');
  assert.equal(flat(header.querySelector('[data-card-meta]')), '6 steps');
  assert.ok(header.querySelector('[data-card-actions] button'), 'actions live in the header');
  assert.ok(cls(header.querySelector('[data-card-actions]')).includes('ml-auto'), 'and sit to the right');
  assert.equal(card.querySelector('[data-card-body] button'), null, 'never mid-card');
  assert.ok(cls(card.querySelector('footer')).includes('text-provenance'));
  // The look: the card fill, the stronger border, the look's radius, 14px padding.
  for (const c of ['bg-card', 'border-card-line', 'rounded-md', 'p-[14px]']) assert.ok(cls(card).includes(c), c);
});

test('card, ordinary: border only (no left edge, no lowering, no tint)', async () => {
  const { m, mount } = await load();
  const card = mount(m.cardHtml({ title: 'T', body: 'b' })).querySelector('[data-ui-card]');
  assert.equal(card.dataset.uiCardState, 'ordinary');
  assert.ok(!cls(card).some((c) => /border-l-|text-ink-muted|bg-accent-tint|border-dashed/.test(c)));
});

test('card, needs you: a 3px left edge in state-warn, and it means nothing else', async () => {
  const { m, mount } = await load();
  const card = mount(m.cardHtml({ title: 'T', state: 'needs_you' })).querySelector('[data-ui-card]');
  assert.ok(cls(card).includes('border-l-[3px]') && cls(card).includes('border-l-state-warn'));
  for (const other of ['ordinary', 'lowered', 'off', 'chosen']) {
    const c = mount(m.cardHtml({ title: 'T', state: other })).querySelector('[data-ui-card]');
    assert.ok(!cls(c).includes('border-l-state-warn'), `${other} has no left edge`);
  }
});

test('card, lowered: the text in ink-muted (never lighter), the border solid', async () => {
  const { m, mount } = await load();
  const card = mount(m.cardHtml({ title: 'T', state: 'lowered', body: '<span class="text-ink">x</span>' })).querySelector('[data-ui-card]');
  assert.ok(cls(card).includes('text-ink-muted'));
  assert.ok(cls(card).includes('[&_.text-ink]:text-ink-muted'), 'ink inside is pulled down to ink-muted, not to anything lighter');
  assert.ok(cls(card).includes('border-card-line') && !cls(card).some((c) => /border-dashed|opacity|text-ink\/|text-chrome/.test(c)));
});

test('card, off: the card is unchanged and the CONTROL in its header is dashed', async () => {
  const { m, mount } = await load();
  const off = mount(m.cardHtml({ title: 'T', actions: '<button>Go</button>', state: 'off' })).querySelector('[data-ui-card]');
  const ordinary = mount(m.cardHtml({ title: 'T', actions: '<button>Go</button>' })).querySelector('[data-ui-card]');
  assert.equal(off.dataset.uiCardState, 'off');
  const strip = (c) => cls(c).filter((x) => !x.startsWith('[&_'));
  assert.deepEqual(strip(off), strip(ordinary), 'the card itself is the ordinary card');
  assert.ok(cls(off).includes('[&_[data-card-actions]_button]:border-dashed'), 'the control is dashed');
});

test('card, chosen: accent-tint fill instead of the card fill', async () => {
  const { m, mount } = await load();
  const card = mount(m.cardHtml({ title: 'T', state: 'chosen' })).querySelector('[data-ui-card]');
  assert.ok(cls(card).includes('bg-accent-tint') && !cls(card).includes('bg-card'));
});

test('card: an unknown state is refused; a card with no header draws none; a stack has 12px between cards', async () => {
  const { m, mount } = await load();
  assert.throws(() => m.cardHtml({ state: 'green' }), /unknown state/);
  assert.equal(mount(m.cardHtml({ body: 'just rows' })).querySelector('header'), null);
  const stack = mount(m.cardStackHtml([m.cardHtml({ title: 'a' }), m.cardHtml({ title: 'b' })])).querySelector('[data-ui-card-stack]');
  assert.ok(cls(stack).includes('gap-[12px]'));
  assert.equal(stack.querySelectorAll('[data-ui-card]').length, 2);
});

/* ── tiles ─────────────────────────────────────────────────────────────── */

const tile = (m, mount, over) => mount(m.tileHtml({ label: 'tables', figure: '13', opens: 'contents', ...over })).querySelector('[data-ui-tile]');

test('tile anatomy: a caps label, the figure, a one-line sub, and the whole tile is the control that opens its target', async () => {
  const { m, mount } = await load();
  const t = tile(m, mount, { sub: '3 readable by the surveyor', title: 'source · as of' });
  assert.equal(t.tagName, 'BUTTON');
  assert.equal(t.dataset.tileOpens, 'contents');
  assert.equal(t.title, 'source · as of', 'source and as-of are one hover away');
  assert.ok(cls(t.querySelector('[data-tile-label]')).includes('uppercase'));
  assert.ok(cls(t.querySelector('[data-tile-figure]')).includes('text-figure'), 'the figure size is the token');
  assert.ok(cls(t.querySelector('[data-tile-figure]')).includes('font-bold'));
  assert.equal(flat(t.querySelector('[data-tile-sub]')), '3 readable by the surveyor');
  assert.ok(cls(t.querySelector('[data-tile-figure]')).includes('text-ink'), 'the figure is ink');
});

test('tile, measured: the figure with the measured cue', async () => {
  const { m, mount } = await load();
  const t = tile(m, mount, { state: 'measured' });
  assert.equal(t.dataset.tileState, 'measured');
  assert.equal(flat(t.querySelector('[data-tile-figure]')), '● 13');
});

test('tile, partial: ◐, the figure, and the word "partial" on the sub line', async () => {
  const { m, mount } = await load();
  const t = tile(m, mount, { state: 'partial', sub: 'within this credential' });
  assert.equal(t.dataset.tileState, 'partial');
  assert.equal(flat(t.querySelector('[data-tile-figure]')), '◐ 13');
  assert.equal(flat(t.querySelector('[data-tile-sub]')), 'partial · within this credential');
  assert.equal(flat(tile(m, mount, { state: 'partial' }).querySelector('[data-tile-sub]')), 'partial');
});

test('tile, not read: "? not read" in the figure slot, and the figure passed in is never drawn', async () => {
  const { m, mount } = await load();
  for (const bad of [0, '0', '', null, undefined, '13']) {
    const t = tile(m, mount, { state: 'not_read', figure: bad });
    assert.equal(flat(t.querySelector('[data-tile-figure]')), '? not read', `figure ${JSON.stringify(bad)}`);
    assert.doesNotMatch(flat(t), /\b0\b|\b13\b/);
  }
});

test('tile, not measured: "○ not measured", never 0 whatever the caller passes', async () => {
  const { m, mount } = await load();
  for (const bad of [0, '0', '', null, undefined]) {
    const t = tile(m, mount, { state: 'not_measured', figure: bad });
    assert.equal(flat(t.querySelector('[data-tile-figure]')), '○ not measured');
    assert.doesNotMatch(flat(t), /\b0\b/);
  }
});

test('tile, zero: a measured zero is "∅ 0", distinct from the absent states', async () => {
  const { m, mount } = await load();
  const t = tile(m, mount, { state: 'zero', figure: 'ignored' });
  assert.equal(flat(t.querySelector('[data-tile-figure]')), '∅ 0');
  assert.notEqual(t.dataset.tileState, 'not_measured');
});

test('tile, state count: the figure takes state-warn, and no other state does', async () => {
  const { m, mount } = await load();
  const count = tile(m, mount, { state: 'count', figure: '2', label: 'need you' });
  assert.ok(count.querySelector('[data-tile-figure] .text-state-warn'));
  assert.equal(flat(count.querySelector('[data-tile-figure]')), '2');
  for (const s of ['measured', 'partial', 'not_read', 'not_measured', 'zero']) {
    assert.doesNotMatch(tile(m, mount, { state: s }).outerHTML, /text-state-(warn|ok)\b/, `${s} is not coloured by size`);
  }
});

test('tile rules: no target is refused; an unknown state is refused; at most six in a row', async () => {
  const { m, mount } = await load();
  assert.throws(() => m.tileHtml({ label: 'x', figure: '1' }), /opens what it counted/);
  assert.throws(() => m.tileHtml({ label: 'x', figure: '1', opens: 'a', state: 'green' }), /unknown state/);
  const t = (i) => m.tileHtml({ label: `t${i}`, figure: String(i), opens: `o${i}` });
  assert.throws(() => m.tileRowHtml([1, 2, 3, 4, 5, 6, 7].map(t)), /at most six/);
  const row = mount(m.tileRowHtml([1, 2, 3, 4, 5, 6].map(t))).querySelector('[data-ui-tile-row]');
  assert.equal(row.querySelectorAll('[data-ui-tile]').length, 6);
  assert.ok(cls(row).includes('flex-wrap'), 'wraps to the next line rather than squeezing a tile');
});

test('bindTiles: a click hands the tile\'s target to the opener, once per tile however often it is bound', async () => {
  const { m, mount } = await load();
  const root = mount(m.tileRowHtml([m.tileHtml({ label: 'a', figure: '1', opens: 'alpha' }), m.tileHtml({ label: 'b', figure: '2', opens: 'beta' })]));
  const opened = [];
  m.bindTiles(root, (target) => opened.push(target));
  m.bindTiles(root, (target) => opened.push(target));
  root.querySelectorAll('[data-ui-tile]')[1].click();
  assert.deepEqual(opened, ['beta']);
});

test('fact tile: small, the card border and radius, no big figure', async () => {
  const { m, mount } = await load();
  const f = mount(m.factTileHtml({ label: 'Language', value: 'Python' })).querySelector('[data-ui-fact-tile]');
  assert.equal(flat(f), 'Language Python');
  assert.ok(cls(f).includes('border-card-line') && cls(f).includes('rounded-md'));
  assert.ok(!f.outerHTML.includes('text-figure'));
  assert.equal(f.tagName, 'DIV', 'a value that is not a count opens nothing, so it is not a button');
});
