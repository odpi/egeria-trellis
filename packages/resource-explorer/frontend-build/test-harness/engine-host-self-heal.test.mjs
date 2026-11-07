/** W65 (brief DESIGN-BRIEF-ENGINE-HOST-SELF-HEAL-2026-11-04): the shell banner's engine line, and Admin › Repair's
 *  "Declare RE's survey engine in Egeria" row: priced, confirmed in the page, ONE batch, never forced, result words.
 *  Real modules; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();

const ENG = (over = {}) => ({ display_name: 'Resource Explorer survey engine (engine host)', state: 'missing',
  present: false, opt_in: { key: 'RE_ENGINE_HOST_HEAL', on: false }, healing: false, last_healed_at: '',
  last_heal_result: '', ...over });
const B = (over = {}) => ({ display_name: 'Foundations', state: 'present', present: true, last_healed_at: '',
  last_heal_result: '', ...over });
const STATUS = (batches, over = {}) => ({ reinitializing: false, healing: '', egeria_reachable: true, batches, ...over });

async function loadShell() {
  const { document } = makeDomEnvironment();
  const mod = await import(`/static/next/shell-status.js?t=${Math.random()}`);
  return { document, mod };
}

/* ── the banner line ───────────────────────────────────────────────────── */

test('banner: a missing engine draws one line for everyone, with the self-heal setting off, and no run button', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS({ foundations: B(), 'engine-host': ENG() }));
  const bar = document.getElementById('bootstrap-banner');
  assert.ok(bar, 'the engine being missing must never be only a log line');
  const lines = bar.querySelectorAll('[data-engine-line]');
  assert.equal(lines.length, 1);
  assert.equal(text(lines[0]), `⚠ ${mod.ENGINE_MISSING_WORDS}`);
  assert.match(text(lines[0]), /not declared in this Egeria · RE-engine surveys cannot run in Egeria · an admin can declare it \(Admin › Repair\)/);
  assert.equal(bar.querySelector('[data-bootstrap-run]'), null, 'the press is Admin › Repair, not the banner');
  assert.equal(mod.bootstrapAttention(STATUS({ 'engine-host': ENG() })), null,
    'an opted-out batch is not one "Run bootstrap now" would heal');
});

test('banner: while the batch is being healed the line says declaring…', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS({ 'engine-host': ENG({ opt_in: { key: 'RE_ENGINE_HOST_HEAL', on: true } }) },
    { reinitializing: true, healing: 'engine-host' }));
  assert.equal(text(document.querySelector('[data-engine-line]')), `◔ ${mod.ENGINE_DECLARING_WORDS}`);
  assert.equal(document.querySelector('[data-engine-line]').dataset.engineLine, 'declaring');
});

test('banner: present, unknown and opted_out draw nothing; the line clears when the engine is declared', async () => {
  const { document, mod } = await loadShell();
  for (const state of ['present', 'unknown', 'opted_out']) {
    mod.renderBootstrapBanner(document, STATUS({ 'engine-host': ENG({ state }) }));
    assert.equal(document.getElementById('bootstrap-banner'), null, state);
  }
  mod.renderBootstrapBanner(document, STATUS({ 'engine-host': ENG() }));
  assert.ok(document.querySelector('[data-engine-line]'));
  mod.renderBootstrapBanner(document, STATUS({ 'engine-host': ENG({ state: 'present', present: true }) }));
  assert.equal(document.getElementById('bootstrap-banner'), null, 'after act A the line goes');
});

test('banner: with other batches missing too, both the batch word and the engine line show', async () => {
  const { document, mod } = await loadShell();
  mod.renderBootstrapBanner(document, STATUS({ foundations: B({ state: 'missing', present: false }), 'engine-host': ENG() }));
  assert.match(text(document.querySelector('[data-bootstrap-word]')), /^1 definition batch missing$/);
  assert.equal(document.querySelectorAll('[data-engine-line]').length, 1);
  assert.ok(document.querySelector('[data-bootstrap-run]'));
});

test('banner: an opted-IN missing engine still counts for Run bootstrap now', async () => {
  const { mod } = await loadShell();
  const need = mod.bootstrapAttention(STATUS({ 'engine-host': ENG({ opt_in: { key: 'RE_ENGINE_HOST_HEAL', on: true } }) }));
  assert.equal(need.kind, 'missing');
});

/* ── Admin › Repair ────────────────────────────────────────────────────── */

const PRICE = { documents: 8, engines: 1, services: 57, governance_action_types: 58, processes: 1,
  applying_as: 'resourceexplorernpa', seconds: null,
  self_heal: { key: 'RE_ENGINE_HOST_HEAL', on: false, interval_seconds: 600 },
  words: 'runs 8 Dr.Egeria documents in order as resourceexplorernpa · creates 1 engine, 57 services, 58 governance '
    + 'action types, 1 process · time not measured here yet · refused if already declared' };

function json(body, status = 200) {
  return { ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) };
}

async function setUpRepair({ status = STATUS({ 'engine-host': ENG() }), run } = {}) {
  const { document, window } = makeDomEnvironment();
  const posts = [];
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith('/api/projects/') || u.startsWith('/api/investigations/')) return json([]);
    if (u.startsWith('/api/bootstrap/status')) return json(status);
    if (u.startsWith('/api/bootstrap/price/engine-host')) return json(PRICE);
    if (u.startsWith('/api/bootstrap/run')) { posts.push(JSON.parse(init.body || '{}')); return run ? run() : json({ batches: {} }); }
    return json({});
  };
  globalThis.location = window.location;
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const pane = await import(`/static/next/admin/repair.js?t=${Math.random()}`);
  const host = document.createElement('div');
  document.body.appendChild(host);
  await pane.renderRepair(host);
  await tick();
  return { document, host, pane, posts };
}

test('repair: the row says the state, the self-heal setting and the price before any press', async () => {
  const { host } = await setUpRepair();
  const row = host.querySelector('[data-engine-row]');
  assert.ok(row);
  assert.match(text(row.querySelector('[data-engine-state]')), /^NOT declared in this Egeria/);
  assert.equal(text(row.querySelector('[data-engine-self-heal]')),
    'self-heal: off (RE_ENGINE_HOST_HEAL) · the engine is re-declared only by this press after an Egeria reset');
  assert.equal(text(row.querySelector('[data-engine-price]')), PRICE.words);
  assert.equal(text(row.querySelector('[data-engine-declare]')), "Declare RE's survey engine in Egeria");
});

test('repair: Cancel in the in-page confirm sends nothing; Confirm sends ONE batch, never force, and shows the result words', async () => {
  const healed = { batches: { 'engine-host': { action: 'healed', reason: '8 documents run · 131 ok' } } };
  const { document, host, posts } = await setUpRepair({ run: () => json(healed) });
  host.querySelector('[data-engine-declare]').click();
  await tick();
  const dialog = document.querySelector('[data-confirm-dialog]');
  assert.ok(dialog, 'an in-page confirm, never window.confirm');
  assert.match(text(dialog), /runs 8 Dr\.Egeria documents in order as resourceexplorernpa/);
  dialog.querySelector('[data-confirm-no]').click();
  await tick();
  assert.equal(posts.length, 0);
  host.querySelector('[data-engine-declare]').click();
  await tick();
  document.querySelector('[data-confirm-yes]').click();
  await tick(40);
  assert.deepEqual(posts, [{ batch: 'engine-host' }], 'one batch, no force');
  assert.equal(text(host.querySelector('[data-engine-result]')), 'declared · 8 documents run · 131 ok');
});

test('repair: on a declared engine the press answers the refusal words; a 409 says a heal is running', async () => {
  const { pane } = await setUpRepair();
  assert.equal(pane.engineResultWords({ batches: { 'engine-host': { action: 'present',
    reason: 'already declared · content applied on 2026-11-04' } } }), 'already declared · content applied on 2026-11-04');
  assert.equal(pane.engineResultWords(null, { status: 409, message: 'A bootstrap heal is already running' }),
    'not run · a bootstrap heal is already running · try again when it ends');
  assert.equal(pane.engineResultWords({ batches: { 'engine-host': { action: 'unknown' } } }),
    'not run · could not reach Egeria to check whether it is declared');
  assert.equal(pane.selfHealWords({ key: 'RE_ENGINE_HOST_HEAL', on: true }), 'self-heal: on · checked every 10 min');
});

test('repair: the 409 from the server reaches the row as words', async () => {
  const { document, host } = await setUpRepair({ run: () => json({ detail: 'A bootstrap heal is already running' }, 409) });
  host.querySelector('[data-engine-declare]').click();
  await tick();
  document.querySelector('[data-confirm-yes]').click();
  await tick(40);
  assert.equal(text(host.querySelector('[data-engine-result]')), 'not run · a bootstrap heal is already running · try again when it ends');
});

test('repair: the state says when and by whom it was checked (Architect ruling Q4)', async () => {
  const { pane } = await setUpRepair();
  const at = new Date(Date.now() - 2 * 60 * 1000).toISOString();
  assert.equal(pane.engineStateWords(ENG({ checked_at: at, source: 'engine host' })),
    'NOT declared in this Egeria · RE-engine surveys cannot run in Egeria · checked 2m ago by the engine host');
  assert.equal(pane.engineStateWords(ENG({ state: 'present', checked_at: at, source: 'bootstrap check' })),
    'declared in this Egeria · checked 2m ago by the bootstrap check');
  assert.equal(pane.engineStateWords(ENG({ state: 'opted_out' })), 'not checked yet');
});

test('repair (F55): a completed partial batch answers its own words; a completion that stopped says not completed', async () => {
  const { pane } = await setUpRepair();
  assert.equal(pane.engineResultWords({ batches: { 'engine-host': { action: 'completed',
    reason: 'completed the batch: ran library/change_rates.md · skipped 1 already applied · 1 document run · 5 ok' } } }),
  'completed the batch: ran library/change_rates.md · skipped 1 already applied · 1 document run · 5 ok');
  assert.equal(pane.engineResultWords({ batches: { 'engine-host': { action: 'completion_failed',
    reason: 'completing the batch stopped at library/x.md: not run · Egeria answered 500' } } }),
  'not completed · completing the batch stopped at library/x.md: not run · Egeria answered 500');
});

test('repair (W67): a present engine carries the applied count on the row without a press; a partial one offers Complete the declaration', async () => {
  const present = ENG({ state: 'present', present: true, checked_at: new Date().toISOString(), source: 'engine host' });
  const priceWith = (applied) => ({ ...PRICE, documents_applied: { applied, total: 8, pending: [] },
    documents_applied_words: `${applied} of 8 documents applied` });
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith('/api/projects/') || u.startsWith('/api/investigations/')) return json([]);
    if (u.startsWith('/api/bootstrap/status')) return json(STATUS({ 'engine-host': present }));
    if (u.startsWith('/api/bootstrap/price/engine-host')) return json(priceWith(3));
    return json({});
  };
  globalThis.location = window.location; globalThis.history = window.history;
  const api = await import('/static/re-api.js'); api.clearCache();
  const pane = await import(`/static/next/admin/repair.js?t=${Math.random()}`);
  const host = document.createElement('div'); document.body.appendChild(host);
  await pane.renderRepair(host); await tick();
  assert.match(text(host.querySelector('[data-engine-state]')),
    /^declared in this Egeria · 3 of 8 documents applied · checked .* by the engine host$/);
  assert.equal(text(host.querySelector('[data-engine-declare]')), 'Complete the declaration');
  host.querySelector('[data-engine-declare]').click(); await tick();
  const dialog = document.querySelector('[data-confirm-dialog]');
  assert.match(text(dialog), /The engine is declared and 3 of 8 documents applied: only the documents with no run here/);
  assert.equal(text(dialog.querySelector('[data-confirm-yes]')), 'Complete it');
  dialog.querySelector('[data-confirm-no]').click(); await tick();
  // Pure words: complete → the declaring label; not read → no count, never "0 of 8".
  assert.equal(pane.pressLabel(present, priceWith(8)), "Declare RE's survey engine in Egeria");
  assert.equal(pane.engineStateWords(present, priceWith(8)).startsWith('declared in this Egeria · 8 of 8 documents applied'), true);
  assert.equal(pane.engineStateWords(present, PRICE).startsWith('declared in this Egeria · checked'), true);
  assert.equal(pane.pressLabel(present, PRICE), "Declare RE's survey engine in Egeria");
  assert.equal(pane.partialDeclaration(present, { ...PRICE, documents_applied: { error: 'run history not read (X)' },
    documents_applied_words: 'documents applied: not known (run history not read (X))' }), null);
  assert.equal(pane.engineStateWords(ENG(), priceWith(3)), 'NOT declared in this Egeria · RE-engine surveys cannot run in Egeria',
    'a missing engine says nothing about applied documents');
});
