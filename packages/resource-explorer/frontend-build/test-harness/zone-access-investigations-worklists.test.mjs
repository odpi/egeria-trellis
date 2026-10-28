/** Brief Z3: a refusal by the zone check on an investigation, a work list or an RFA ToDo shows as the existing
 *  no_access cue plus "not permitted", the reason on hover (the cue's title), never a sentence. A member the
 *  check refused in a promote or relink is named with the same cue; the rest of the result is unchanged.
 *  Real investigation.js / rfa.js against a recording fetch in the shape of the routes. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML = fs.readFileSync(path.resolve(HERE, '../../resource_explorer/web/static/next/index.html'), 'utf8');
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const REASON = 'zone sales-zone does not grant garygeeke create in Egeria';
const refused = (detail) => ({ ok: false, status: 403, statusText: 'Forbidden', json: async () => ({ detail }) });

async function investigation({ promote, relink, startBound = false }) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/\/promote$/.test(u)) return promote();
    if (/\/relink-members$/.test(u)) return relink();
    if (/\/link-status$/.test(u)) return ok({ bound: true, members: [], counts: { linked: 0, not_linked: 1, could_not_tell: 0 } });
    if (/\/members$/.test(u)) return ok([{ entity_type: 'repo', entity_slug: 'a' }]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    const m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m) return ok({ slug: m[1], display_name: 'C', status: 'open', description: 'd', project_classification: 'StudyProject',
      purposes: [], visibility: 'public', ...(startBound ? { egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::C' } : {}) });
    if (/^\/api\/investigations\/\?/.test(u)) return ok([]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const api = await import('/static/re-api.js'); api.clearCache();
  const app = await import('/static/next/app.js');
  const inv = await import('/static/next/stages/investigation.js');
  app.state.stage = 'investigation'; app.state.me = { user_id: 'me' }; app.state.investigations = [];
  inv.openInvestigationDetail('c');
  await inv.renderInvestigation();
  await tick();
  return { document };
}

test('a refused promote shows the no_access cue and "not permitted", the reason on hover', async () => {
  const t = await investigation({ promote: () => refused(REASON) });
  t.document.querySelector('[data-act="inv-promote"]').click();
  await tick(200);
  const cue = t.document.querySelector('[data-inv-promote-refused] [data-cue="no_access"]');
  assert.ok(cue, 'the cue');
  assert.match(text(cue), /not permitted/);
  assert.equal(cue.getAttribute('title'), REASON);
  assert.doesNotMatch(text(t.document.querySelector('[data-inv-promote-refused]')), /does not grant/, 'no sentence');
});

test('a member the promote check refused is named with the cue; the linked ones are unchanged', async () => {
  const t = await investigation({ promote: () => ({ ok: true, status: 200, json: async () => ({
    ok: true, classification_requested: 'StudyProject', classification_confirmed: 'StudyProject', members_linked: ['b'],
    members_unlinkable: [{ entity_type: 'repo', entity_slug: 'a', reason: `not permitted · ${REASON}` }] }) }) });
  t.document.querySelector('[data-act="inv-promote"]').click();
  await tick(250);
  const row = t.document.querySelector('[data-member-not-permitted="a"]');
  assert.ok(row, 'the refused member');
  const cue = row.querySelector('[data-cue="no_access"]');
  assert.equal(cue.getAttribute('title'), REASON);
  assert.match(text(t.document.getElementById('inv-egeria-form')), /Linked: b\./);
});

test('a refused relink puts the cue on its button', async () => {
  const t = await investigation({ startBound: true, relink: () => refused(REASON) });
  const btn = t.document.querySelector('[data-act="inv-relink"]');
  btn.click();
  await tick(200);
  const cue = t.document.querySelector('[data-act="inv-relink"] [data-cue="no_access"]');
  assert.ok(cue);
  assert.match(text(cue), /not permitted/);
  assert.equal(cue.getAttribute('title'), REASON);
});

test('a refused RFA status change shows the cue on the row and records nothing', async () => {
  const { document, window } = makeDomEnvironment();
  const rows = [{ id: 'e1::0', entry_id: 'e1', annotation_index: 0, ts: '2026-10-01T00:00:00Z', entity_type: 'repo',
    entity_slug: 'r1', entity_name: 'R1', annotation_type: 'RequestForAction', analysis_name: 'X', summary: 'No CI',
    explanation: '', action_requested: '', action_target_name: '', rfa_status: 'open', assignee: '', defer_until: '',
    resolution_note: '', notes: '', dismissal_key: 'k', dismissed: false, dismissal: null }];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url); const method = (options.method || 'GET').toUpperCase();
    if (method === 'GET' && u === '/api/activity/rfas') return { ok: true, status: 200, json: async () => rows };
    if (method === 'PATCH') return refused('zone todo-zone does not grant garygeeke update in Egeria');
    return { ok: true, status: 200, json: async () => ({}) };
  };
  ensureLoaderRegistered();
  const api = await import('/static/re-api.js'); api.clearCache();
  const mod = await import(`/static/next/rfa.js?t=${Math.random()}`);
  mod.openRfaDrawer('');
  await tick();
  // jsdom has no CSS.escape (the drawer finds the row by it).
  globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/([^\w-])/g, '\\$1') };
  document.querySelector('[data-rfa-act="completed"]').click();
  await tick(5);
  document.querySelector('[data-prompt-yes]').click();   // the in-page resolution-note box: leave it blank
  await tick(150);
  const cue = document.querySelector('[data-rfa-not-permitted] [data-cue="no_access"]');
  assert.ok(cue);
  assert.match(text(cue), /not permitted/);
  assert.equal(cue.getAttribute('title'), 'zone todo-zone does not grant garygeeke update in Egeria');
  assert.equal(rows[0].rfa_status, 'open', 'nothing recorded');
});
