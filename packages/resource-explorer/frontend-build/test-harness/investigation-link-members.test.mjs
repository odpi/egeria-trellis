/** (rebind-button) Investigation Egeria section: after a successful promote the 'Creating…' button must not stay greyed out;
 *  the section shows the bound cue, and members the write could not attach are counted from its own result. */
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

async function setUp({ promoteResult, linkStatus, startBound = false, promoteFails = false, relinkResult, relinkFails = false }) {
  const { document, window } = makeDomEnvironment();
  let bound = startBound;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url); const method = (options.method || 'GET').toUpperCase();
    calls.push(`${method} ${u}`);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/\/promote$/.test(u)) {
      if (promoteFails) return { ok: false, status: 502, json: async () => ({ detail: 'Egeria refused: no such project' }), text: async () => 'Egeria refused: no such project' };
      bound = true; return ok(promoteResult);
    }
    if (/\/relink-members$/.test(u)) {
      if (relinkFails) return { ok: false, status: 502, json: async () => ({ detail: 'collection gone' }), text: async () => 'collection gone' };
      return ok(relinkResult || { ok: true, members_linked: ['a', 'b'], members_unlinkable: [] });
    }
    if (/\/link-status$/.test(u)) {
      if (linkStatus === 'fail') return { ok: false, status: 500, json: async () => ({ detail: 'nope' }), text: async () => 'nope' };
      return ok(linkStatus || {});
    }
    if (/\/members$/.test(u)) return ok([{ entity_type: 'repo', entity_slug: 'a' }, { entity_type: 'repo', entity_slug: 'b' }]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    let m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m) return ok({ slug: m[1], display_name: 'C', status: 'open', description: 'd', project_classification: 'StudyProject',
      purposes: [], visibility: 'public',
      ...(bound ? { egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::C360' } : {}) });
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
  inv.openInvestigationDetail('c360');
  await inv.renderInvestigation();
  await tick();
  return { document, calls, bodyText: () => text(document.body) };
}

const OKPROMOTE = { ok: true, classification_requested: 'StudyProject', classification_confirmed: 'StudyProject', members_linked: [], members_unlinkable: [] };
const status = (linkable, not_linked = linkable) => ({ bound: true, members: [], counts: { linked: 1, not_linked, could_not_tell: 0, linkable } });

test('a failed publish resets the button, leaves a cue on it, and pops up the reason', async () => {
  const t = await setUp({ promoteFails: true });
  const btn = t.document.querySelector('[data-act="inv-promote"]');
  btn.click();
  await tick(200);
  const again = t.document.querySelector('[data-act="inv-promote"]');
  assert.equal(again.disabled, false);
  assert.doesNotMatch(text(again), /Creating…/);
  assert.ok(again.querySelector('[data-cue="error"]'), 'persistent cue on the button');
  assert.match(text(t.document.querySelector('[data-inv-failure-popup]')), /Egeria refused: no such project/);
});

test('a successful publish leaves no greyed Creating… button anywhere', async () => {
  const t = await setUp({ promoteResult: OKPROMOTE });
  t.document.querySelector('[data-act="inv-promote"]').click();
  await tick(200);
  assert.equal([...t.document.querySelectorAll('button[data-act^="inv-"]')].filter((b) => b.disabled).length, 0);
  assert.doesNotMatch(t.bodyText(), /Creating…/);
  assert.equal(t.document.querySelector('[data-inv-failure-popup]'), null);
});

test('Link members appears only when linkable > 0 and shows that count', async () => {
  const some = await setUp({ startBound: true, linkStatus: status(3, 5) });
  assert.equal(text(some.document.querySelector('[data-act="inv-relink"]')), 'Link members (3)');
  const none = await setUp({ startBound: true, linkStatus: status(0, 2) });
  assert.equal(text(none.document.querySelector('[data-act="inv-relink"]')), 'Relink members');
  assert.doesNotMatch(text(none.document.querySelector('[data-act="inv-relink"]')), /Link members/);
});

test('pressing Link members calls the route once, shows linking…, then ✓ N linked from the response', async () => {
  const t = await setUp({ startBound: true, linkStatus: status(2), relinkResult: { ok: true, members_linked: ['a', 'b'], members_unlinkable: [], errors: [] } });
  const btn = t.document.querySelector('[data-act="inv-relink"]');
  btn.click();
  assert.match(text(btn), /◔ linking…/);
  assert.equal(btn.disabled, true);
  await tick(200);
  assert.equal(t.calls.filter((c) => c === 'POST /api/investigations/c360/relink-members').length, 1);
  assert.match(text(t.document.querySelector('[data-inv-relink-result]')), /✓ 2 linked/);
  assert.equal([...t.document.querySelectorAll('button[data-act^="inv-"]')].filter((b) => b.disabled).length, 0);
});

test('a failed Link members resets the button, keeps a cue on it and pops up the reason', async () => {
  const t = await setUp({ startBound: true, linkStatus: status(2), relinkFails: true });
  t.document.querySelector('[data-act="inv-relink"]').click();
  await tick(200);
  const btn = t.document.querySelector('[data-act="inv-relink"]');
  assert.equal(btn.disabled, false);
  assert.doesNotMatch(text(btn), /linking…/);
  assert.ok(btn.querySelector('[data-cue="error"]'));
  assert.match(text(t.document.querySelector('[data-inv-failure-popup]')), /collection gone/);
});
