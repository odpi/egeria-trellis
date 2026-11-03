/** Investigation purposes are SUGGESTIVE (owner ruling 2026-10-13): a value picked or typed outside the list
 *  carries a small "not in the list" cue plus the word -- never an error -- and Intended use sends the picked
 *  value and the typed text APART so the text can be mined. Real modules, real jsdom; only fetch is stubbed.
 *
 *  Imports are not cache-busted: enrichment.js and purposes.js must be the same instances `/next` uses, so
 *  `setPurposes` here is the list `fieldRowHtml` reads.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const VOCAB = {
  source: 'cache', stale: true, words: 'cached copy · read from Egeria 3 days ago · stale · Egeria could not be read now',
  values: [
    { name: 'Explore', description: 'The default; no commitment implied.' },
    { name: 'Find', description: 'Locate the resources that hold or serve what is needed.' },
    { name: 'Integrate', description: 'Connect resources so data or services flow between them.' },
  ],
};

async function setUp() {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const purposes = await import('/static/next/purposes.js');
  const enrichment = await import('/static/next/stages/enrichment.js');
  purposes.setPurposes(VOCAB);
  return { app, purposes, enrichment };
}

const INTENDED_USE = { key: 'intended_use', label: 'Intended use', placeholder: 'or in your own words', purposes: true };

test('a purpose outside the list shows the cue and the word; a listed one does not; no list, no claim', async () => {
  const { purposes } = await setUp();
  const names = purposes.purposesNow().purposes;
  const off = purposes.purposeChipHtml('Wander', names);
  assert.match(off, /data-purpose-cue="not-in-list"/);
  assert.match(off, /not in the list/);
  assert.doesNotMatch(off, /state-warn|error/i, 'a cue, never an error');
  assert.doesNotMatch(purposes.purposeChipHtml('find', names), /not in the list/, 'case is not a difference');
  assert.doesNotMatch(purposes.purposeChipHtml('Wander', []), /not in the list/, 'nothing to compare: no cue');
  const summary = purposes.purposesSummaryHtml({ purposes: ['Find'], purpose_text: 'feed the CRM' }, names);
  assert.match(summary, /data-purpose="feed the CRM" data-purpose-unlisted/);
  assert.equal((summary.match(/not in the list/g) || []).length, 1);
});

test('the investigation picker: listed boxes, a kept unknown value checked with its cue, typed text cued live', async () => {
  const { purposes } = await setUp();
  const host = document.createElement('div');
  document.body.appendChild(host);
  host.innerHTML = purposes.purposePickerHtml({ prefix: 'inv-edit', vocab: purposes.purposesNow(), selected: ['Find', 'Wander'] });
  const boxes = [...host.querySelectorAll('[data-purpose-box="inv-edit"]')];
  assert.deepEqual(boxes.map((b) => b.value), ['Explore', 'Find', 'Integrate', 'Wander']);
  assert.deepEqual(boxes.filter((b) => b.checked).map((b) => b.value), ['Find', 'Wander']);
  assert.match(boxes[3].parentElement.innerHTML, /not in the list/);
  assert.match(host.querySelector('[data-purpose-source]').textContent, /cached copy · read from Egeria 3 days ago/);
  purposes.wirePurposePicker(host, 'inv-edit');
  const input = host.querySelector('#inv-edit-purpose-text');
  input.value = 'vendor due diligence';
  input.dispatchEvent(new window.Event('input'));
  assert.match(host.querySelector('#inv-edit-purpose-text-cue').innerHTML, /not in the list/);
  input.value = 'Integrate';
  input.dispatchEvent(new window.Event('input'));
  assert.equal(host.querySelector('#inv-edit-purpose-text-cue').innerHTML, '');
  assert.deepEqual(purposes.readPurposePicker(host, 'inv-edit'),
    { purposes: ['Find', 'Wander'], purpose_text: 'Integrate' });
});

test('Intended use offers the list and free text; an unknown or free value carries the cue', async () => {
  const { app, enrichment } = await setUp();
  const now = new Date().toISOString();
  app.state.enrichment = { intended_use: { value: 'Find', picked: 'Find', free_text: '', kind: 'judgement', author: 'dan', set_at: now } };
  let row = enrichment.fieldRowHtml(INTENDED_USE, 'judgement');
  assert.match(row, /<select data-purpose-pick="intended_use"/);
  assert.match(row, /<option value="Find"[^>]*selected/);
  assert.doesNotMatch(row, /not in the list/);

  app.state.enrichment = { intended_use: { value: 'Find — feed the CRM', picked: 'Find', free_text: 'feed the CRM', kind: 'judgement', author: 'dan', set_at: now } };
  row = enrichment.fieldRowHtml(INTENDED_USE, 'judgement');
  assert.match(row, /data-field="intended_use" type="text" value="feed the CRM"/);
  assert.match(row, /not in the list/);

  // A row written before the split: its one value, not a list value, is the free text.
  app.state.enrichment = { intended_use: { value: 'a demo for sales', kind: 'judgement', author: 'dan', set_at: now } };
  row = enrichment.fieldRowHtml(INTENDED_USE, 'judgement');
  assert.match(row, /value="a demo for sales"/);
  assert.match(row, /not in the list/);
  assert.match(row, /data-purpose-source="cache"/, 'the list says it is a cache, and how old');
});

test('Save sends the picked value and the typed text apart', async () => {
  const { app, enrichment } = await setUp();
  app.state.enrichment = {};
  app.state.resourceType = 'db';
  const host = document.createElement('div');
  document.body.appendChild(host);
  host.innerHTML = enrichment.fieldRowHtml(INTENDED_USE, 'judgement');
  const sent = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), body: JSON.parse(init.body) });
    return { ok: true, status: 200, json: async () => ({ key: 'intended_use', field: { value: 'x' }, warnings: [] }) };
  };
  enrichment.wireEnrichmentFieldControls(host, 'crm', () => {});
  host.querySelector('[data-purpose-pick="intended_use"]').value = 'Integrate';
  host.querySelector('[data-field="intended_use"]').value = ' nightly feed ';
  host.querySelector('[data-field="intended_use"]').dispatchEvent(new window.Event('input'));
  assert.match(host.querySelector('[data-purpose-cue-slot="intended_use"]').innerHTML, /not in the list/);
  host.querySelector('[data-save="intended_use"]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(sent.length, 1);
  assert.match(sent[0].url, /\/api\/context\/database\/crm\/field$/);
  assert.equal(sent[0].body.picked, 'Integrate');
  assert.equal(sent[0].body.free_text, 'nightly feed');
  assert.equal(sent[0].body.value, 'Integrate — nightly feed');
});

test('the investigations list draws without waiting for the purposes list, then fills the cues', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const purposes = await import('/static/next/purposes.js');
  const { renderInvestigation } = await import('/static/next/stages/investigation.js');
  purposes.setPurposes(null);
  app.state.investigations = [];
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  let releasePurposes;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/investigations/purposes')) {
      await new Promise((r) => { releasePurposes = r; });
      return { ok: true, status: 200, json: async () => VOCAB };
    }
    if (u.includes('/api/investigations/?')) {
      return { ok: true, status: 200, json: async () => ([
        { slug: 'a', display_name: 'A', purposes: ['Find', 'Wander'], purpose_text: '', status: 'open', member_count: 0 }]) };
    }
    throw new Error(`unstubbed fetch ${u}`);
  };
  await renderInvestigation();
  const cell = host.querySelector('[data-inv-purposes-cell="a"]');
  assert.ok(cell, 'the row is drawn while the purposes list is still loading');
  assert.match(cell.textContent, /Find, Wander/);
  assert.doesNotMatch(cell.innerHTML, /not in the list/, 'no list yet, so no claim');
  releasePurposes();
  await new Promise((r) => setTimeout(r, 10));
  assert.match(host.querySelector('[data-inv-purposes-cell="a"]').innerHTML, /data-purpose="Wander" data-purpose-unlisted/);
});
