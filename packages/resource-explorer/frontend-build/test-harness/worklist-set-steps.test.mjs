/** The work list's set steps section (Part C screen entry): offers with their priced line (an unmeasured time said as such,
 *  never 0), a Run button that disables and reads "running…" at once, the outcome (roll-up, every member, the skipped
 *  members with their reasons), the disabled sentences, and resizable tables, at 800 px and 1440 px. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const S = await import('/static/next/worklist-set-steps.js');
const { watchTables } = await import('/static/next/colresize.js');

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

const OFFER = {
  step: 'set_probe', display_name: 'Set Probe', description: 'Reads every member in one pass.',
  price: { step: 'set_probe', members: 4, priced: 3, skipped: 1, tables: 0,
    words: '3 member(s) · not measured yet · the first run sets the price · 1 skipped',
    total: { seconds: null, words: 'not measured yet · the first run sets the price' },
    rows: [
      { entity_slug: 'a', entity_type: 'database', can_run: true, time: { seconds: null }, words: '10 tables · size unknown · time not measured yet · the first run sets it' },
      { entity_slug: 'b', entity_type: 'database', can_run: true, time: { seconds: null }, words: '4 tables · time not measured yet · the first run sets it' },
      { entity_slug: 'c', entity_type: 'database', can_run: true, time: { seconds: null }, words: 'tables not recorded · no survey stored yet' },
      { entity_slug: 'fs1', entity_type: 'filesystem', can_run: false, time: null, words: 'skipped · fs1 is a filesystem; this step measures database' },
    ] },
};

const rec = (scope, state, summary, reason = '') => ({ scope, summary, additionalProperties: { resultState: state, resultStateReason: reason } });
const RESULT = { run: 'r1', set: 'wl_coco', per_member: 4, rollups: 1, requested_by: 'me', step_annotations: [
  rec('a', 'measured', '2 snapshots'), rec('b', 'measured', '1 snapshots'), rec('c', 'measured', '0 snapshots'),
  rec('fs1', 'not_applicable', 'not applicable · fs1 is a filesystem; step set_probe measures database', 'fs1 is a filesystem; step set_probe measures database'),
  rec('wl_coco', 'measured', '3 members read'),
] };

function backend({ result = RESULT, fail = '' } = {}) {
  const calls = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  const fetchImpl = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (!opts.method) return ok({ work_list: 'wl_coco', members: 4, offers: [OFFER] });
    await gate;
    if (fail) return { ok: false, status: 422, statusText: 'x', json: async () => ({ detail: fail }) };
    return ok(result);
  };
  return { fetchImpl, calls, release };
}

function host(width) {
  const { document, window } = makeDomEnvironment();
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  const h = document.createElement('div');
  h.style.width = `${width}px`;
  document.body.appendChild(h);
  return { h, document, window };
}

for (const width of [800, 1440]) {
  test(`offers show the priced line with an unmeasured time said as such, never 0, at ${width} px`, async () => {
    const { h } = host(width);
    const b = backend();
    await S.mountSetSteps(h, { slug: 'wl_coco', signedIn: () => true, fetchImpl: b.fetchImpl });
    assert.match(text(h.querySelector('[data-set-price]')), /not measured yet/);
    assert.doesNotMatch(text(h.querySelector('[data-set-price]')), /\b0 (s|seconds|min)/);
    assert.equal(h.querySelectorAll('[data-price-row]').length, 4);
    assert.match(text(h.querySelector('[data-price-row="fs1"]')), /skipped · fs1 is a filesystem/);
    assert.equal(b.calls.length, 1, 'reading the offers runs nothing');
  });

  test(`one press runs once: running… shows at once, then the roll-up, every member and the skipped one, at ${width} px`, async () => {
    const { h } = host(width);
    const b = backend();
    await S.mountSetSteps(h, { slug: 'wl_coco', signedIn: () => true, fetchImpl: b.fetchImpl });
    const press = () => h.querySelector('[data-set-run="set_probe"]');
    press().dispatchEvent(new h.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));
    await tick();
    assert.equal(text(press()), 'running…');
    assert.equal(press().disabled, true, 'the button is disabled while it runs');
    press().click(); press().click();               // a second press while running is ignored
    await tick();
    assert.equal(b.calls.filter((c) => c.method === 'POST').length, 1, 'one press, one run');
    b.release();
    await tick(40);
    assert.match(text(h.querySelector('[data-set-rollup]')), /wl_coco · 3 members read/);
    assert.equal(h.querySelectorAll('[data-member-row]').length, 4);
    const skipped = h.querySelector('[data-member-row="fs1"]');
    assert.equal(skipped.dataset.skipped, 'true');
    assert.match(text(skipped), /fs1 is a filesystem; step set_probe measures database/);
    assert.match(text(h.querySelector('[data-set-outcome]')), /4 member results and 1 roll-up, one run/);
  });

  test(`both tables are real resizable tables at ${width} px`, async () => {
    const { h, document } = host(width);
    const b = backend();
    await S.mountSetSteps(h, { slug: 'wl_coco', signedIn: () => true, fetchImpl: b.fetchImpl });
    h.querySelector('[data-set-run]').click();
    b.release();
    await tick(40);
    watchTables(document.body);
    for (const id of ['wl-set-price', 'wl-set-results']) {
      const t = h.querySelector(`table[data-rc-id="${id}"]`);
      assert.ok(t && t.querySelector('thead th'), `${id} has a header row`);
      assert.ok(t.querySelectorAll('.rc-handle').length >= 2, `${id} columns carry resize handles`);
    }
  });
}

test('a refused run says why in the section and does not claim an outcome', async () => {
  const { h } = host(1440);
  const b = backend({ fail: 'step set_probe returned 0 roll-ups for the set' });
  await S.mountSetSteps(h, { slug: 'wl_coco', signedIn: () => true, fetchImpl: b.fetchImpl });
  h.querySelector('[data-set-run]').click();
  b.release();
  await tick(40);
  assert.match(text(h.querySelector('[data-set-error]')), /The run did not complete: step set_probe returned 0 roll-ups/);
  assert.equal(h.querySelector('[data-set-outcome]'), null);
  assert.equal(h.querySelector('[data-set-run]').disabled, false, 'it can be pressed again');
});

test('disabled, with a sentence: nobody signed in, no members, no eligible members', async () => {
  const signedOut = host(1440);
  await S.mountSetSteps(signedOut.h, { slug: 'wl_coco', signedIn: () => false, fetchImpl: backend().fetchImpl });
  assert.equal(signedOut.h.querySelector('[data-set-run]').disabled, true);
  assert.match(text(signedOut.h.querySelector('[data-set-why]')), /Sign in to run a set step: it runs as you\./);

  const none = host(1440);
  await S.mountSetSteps(none.h, { slug: 'wl_coco', signedIn: () => true,
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ members: 0, offers: [] }) }) });
  assert.match(text(none.h), /This list has no members, so there is nothing for a set step to run on\./);
  assert.equal(none.h.querySelector('[data-set-run]'), null);

  const noneEligible = { ...OFFER, price: { ...OFFER.price, priced: 0 } };
  const e = host(1440);
  await S.mountSetSteps(e.h, { slug: 'wl_coco', signedIn: () => true,
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ members: 2, offers: [noneEligible] }) }) });
  assert.equal(e.h.querySelector('[data-set-run]').disabled, true);
  assert.match(text(e.h.querySelector('[data-set-why]')), /None of the 2 members is a kind this step measures/);

  const noOffers = host(1440);
  await S.mountSetSteps(noOffers.h, { slug: 'wl_coco', signedIn: () => true,
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ members: 3, offers: [] }) }) });
  assert.match(text(noOffers.h), /No installed set step measures the kinds of resource in this list\./);
});

test('what the server says is escaped', () => {
  const html = S.outcomeHtml({ set: 's', per_member: 1, rollups: 1, step_annotations: [rec('<img onerror=x>', 'measured', '<b>x</b>'), rec('s', 'measured', 'ok')] });
  assert.doesNotMatch(html, /<img|<b>/);
});
