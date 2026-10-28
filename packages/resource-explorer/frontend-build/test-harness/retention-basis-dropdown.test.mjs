/** Retention is a drop-down of Egeria's basis values plus a free-text note. A value stored before that
 *  (free text such as 'Ongoing') is read as Project lifetime with its text kept as the note (owner ruling
 *  2026-10-08), with a visible cue + short word; nothing stored is 'pick one'. Read-time only. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function route(enrichment) {
  const slug = 'egeria_git';
  const { document, window } = makeDomEnvironment();
  const log = [];
  const saved = { ...enrichment };
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    log.push(`${opts.method || 'GET'} ${u}`);
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (opts.method === 'PATCH' && u.endsWith('/field')) {
      const b = JSON.parse(opts.body);
      log.push(`BODY ${opts.body}`);
      saved[b.key] = { value: b.value, note: b.note, kind: b.kind, author: 'dan', set_at: new Date().toISOString(), source: '', evidence: {} };
      return ok({ key: b.key, field: saved[b.key] });
    }
    if (u.includes('/api/context/')) return ok({ enrichment: saved, question_answers: {} });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    return ok({ questions: [] });
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: slug, stage: 'enrichment', subTab: 'questions',
    investigations: [], investigation: '', me: { user_id: 'dan' } });
  const content = document.createElement('div'); content.id = 'content'; document.body.appendChild(content);
  for (const id of ['rail-evidence', 'perspective-row', 'worklist-nav', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  content.innerHTML = app.subTabsHtml(); app.bindSubTabs();
  content.querySelector('[data-subtab="context"]').click();
  for (let i = 0; i < 100 && !document.getElementById('context-human-questions'); i++) await new Promise((r) => setTimeout(r, 10));
  await new Promise((r) => setTimeout(r, 60));
  return { document, log, saved, app };
}
const stored = (value, note) => ({ retention: { value, note, kind: 'observation', author: 'dan', set_at: new Date().toISOString(), source: 'user', evidence: {} } });

test('the page list is eight readable names with stable enum values', async () => {
  ensureLoaderRegistered();
  const { RETENTION_BASES, resolveRetention } = await import('/static/next/retention-basis.js');
  assert.deepEqual(RETENTION_BASES.map((b) => b.name), ['UNCLASSIFIED', 'TEMPORARY', 'PROJECT_LIFETIME', 'TEAM_LIFETIME',
    'CONTRACT_LIFETIME', 'REGULATED_LIFETIME', 'TIMEBOXED_LIFETIME', 'OTHER']);
  assert.deepEqual(resolveRetention({ value: 'Ongoing' }), { basis: 'PROJECT_LIFETIME', note: 'Ongoing', carried: true });
});

test('Egeria spelling and hover hints', async () => {
  const { document } = await route({});
  const o = [...document.querySelectorAll('select[data-field="retention"] option')];
  assert.ok(o.some((x) => x.textContent === 'Time Boxed Lifetime' && x.value === 'TIMEBOXED_LIFETIME' && /specified time/.test(x.title)));
  assert.doesNotMatch(document.querySelector('[data-field="retention"]').closest('div').parentElement.textContent, /specified time/);
});

test('nothing stored: a drop-down with eight values, unset, cue "pick one", the question on the note', async () => {
  const { document } = await route({});
  const sel = document.querySelector('select[data-field="retention"]');
  assert.equal([...sel.options].filter((o) => o.value).length, 8);
  assert.equal(sel.value, '');
  assert.equal(document.querySelector('[data-retention-cue]').dataset.retentionCue, 'unset');
  assert.match(document.querySelector('[data-retention-cue]').textContent, /pick one/);
  assert.equal(document.querySelector('[data-note="retention"]').placeholder, 'how long, and under whose retention rule?');
});

test('a legacy "Ongoing": Project lifetime selected, "set from existing" cue, text kept as the note', async () => {
  const { document } = await route(stored('Ongoing'));
  assert.equal(document.querySelector('select[data-field="retention"]').value, 'PROJECT_LIFETIME');
  const cue = document.querySelector('[data-retention-cue="carried"]');
  assert.match(cue.textContent, /set from existing · Project Lifetime/);
  assert.equal(document.querySelector('[data-note="retention"]').value, 'Ongoing');
});

test('a new-shape value shows no cue; picking and saving sends the enum name and the note', async () => {
  const { document, log, app } = await route(stored('TEAM_LIFETIME', 'per team charter'));
  assert.equal(document.querySelector('select[data-field="retention"]').value, 'TEAM_LIFETIME');
  assert.equal(document.querySelector('[data-retention-cue]'), null);
  assert.equal(document.querySelector('[data-note="retention"]').value, 'per team charter');
  document.querySelector('select[data-field="retention"]').value = 'REGULATED_LIFETIME';
  document.querySelector('[data-save="retention"]').click();
  await new Promise((r) => setTimeout(r, 80));
  const body = JSON.parse(log.find((l) => l.startsWith('BODY')).slice(5));
  assert.equal(body.key, 'retention'); assert.equal(body.value, 'REGULATED_LIFETIME'); assert.equal(body.note, 'per team charter');
  assert.equal(app.state.enrichment.retention.value, 'REGULATED_LIFETIME');
});

test('picking from a carried legacy value stores the new shape (value + note)', async () => {
  const { document, log } = await route(stored('Ongoing'));
  document.querySelector('[data-save="retention"]').click();
  await new Promise((r) => setTimeout(r, 80));
  const body = JSON.parse(log.find((l) => l.startsWith('BODY')).slice(5));
  assert.equal(body.value, 'PROJECT_LIFETIME'); assert.equal(body.note, 'Ongoing');
});

test('old values that NAME a basis get that basis (label, ordinal, any case); other text gets Project Lifetime', async () => {
  ensureLoaderRegistered();
  const { resolveRetention } = await import('/static/next/retention-basis.js');
  const basis = (v) => resolveRetention({ value: v }).basis;
  assert.equal(basis('Temporary'), 'TEMPORARY');
  assert.equal(basis('temporary'), 'TEMPORARY');
  assert.equal(basis('Time Boxed Lifetime'), 'TIMEBOXED_LIFETIME');
  assert.equal(basis('1'), 'TEMPORARY');
  assert.equal(basis(' 99 '), 'OTHER');
  assert.equal(basis('Ongoing'), 'PROJECT_LIFETIME');
  assert.equal(basis('7'), 'PROJECT_LIFETIME');
  assert.equal(resolveRetention({ value: 'temporary' }).note, 'temporary');
});

test('the page selects Temporary for a stored "temporary", not Project Lifetime', async () => {
  const { document } = await route(stored('temporary'));
  assert.equal(document.querySelector('select[data-field="retention"]').value, 'TEMPORARY');
  assert.match(document.querySelector('[data-retention-cue="carried"]').textContent, /set from existing · Temporary/);
});

test('"keep mine" on a legacy retention sends the resolved basis and the note (no 422, no cleared note)', async () => {
  const { document, log, app } = await route(stored('Ongoing'));
  const host = document.getElementById('content');
  const b = document.createElement('button'); b.dataset.keep = 'retention'; host.appendChild(b);
  const { wireEnrichmentFieldControls } = await import('/static/next/stages/enrichment.js');
  wireEnrichmentFieldControls(host, 'egeria_git', () => {});
  b.click();
  await new Promise((r) => setTimeout(r, 80));
  const body = JSON.parse(log.filter((l) => l.startsWith('BODY')).pop().slice(5));
  assert.equal(body.value, 'PROJECT_LIFETIME'); assert.equal(body.note, 'Ongoing');
  assert.ok(app.state.enrichment.retention);
});

test('the Curate plan line marks a skipped classification and does not count it as written', async () => {
  await route({});
  const { curateWritesHtml } = await import('/static/next/stages/curate.js');
  const plan = { writes: { classifications: [
    { classification: 'Confidentiality', value: 'public', author: 'dan', skipped: false },
    { classification: 'Retention', value: 'skipped · no retention basis picked', author: 'dan', skipped: true }] } };
  const out = curateWritesHtml(plan, [], 0);
  assert.match(out, /<span class="tnum">1<\/span> authored classification ·/);
  assert.match(out, /Retention · skipped · no retention basis picked/);
  assert.match(out, /data-plan-skipped/);
});
