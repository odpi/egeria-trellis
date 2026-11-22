/** TR-120: an investigation bound to Egeria but not promoted yet says so in words (as the server serves them),
 *  instead of reading "local" with an empty value. Nothing is created by looking at the page. */
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
const WORDS = 'Egeria Project not created yet: Publish to Egeria creates it';

async function setUp(invExtra) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url); const method = (options.method || 'GET').toUpperCase();
    calls.push(`${method} ${u}`);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/\/members$/.test(u)) return ok([]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/link-status$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    const m = u.match(/^\/api\/investigations\/([^/?]+)$/);
    if (m) return ok({ slug: m[1], display_name: 'C', status: 'open', description: 'd', project_classification: 'StudyProject',
      purposes: [], visibility: 'shared', egeria_binding: 'egeria', egeria_project_guid: '', ...invExtra });
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
  return { document, calls };
}

test('an unpromoted Egeria-bound investigation shows the server words, not "local"', async () => {
  const t = await setUp({ egeria_project_words: WORDS });
  const page = text(t.document.getElementById('content'));
  assert.match(page, new RegExp(WORDS.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.ok(t.document.querySelector('[data-inv-egeria-words]'), 'the header glyph carries the words');
  assert.doesNotMatch(page, /🏠 local/);
  assert.doesNotMatch(page, /No Egeria Project yet — local only/);
});

test('looking at the page creates nothing: no promote, bind or write call is made', async () => {
  const t = await setUp({ egeria_project_words: WORDS });
  assert.deepEqual(t.calls.filter((c) => !c.startsWith('GET ')), []);
});

test('a local investigation still reads local, and a bound one still reads bound', async () => {
  const local = await setUp({ egeria_binding: 'local' });
  assert.match(text(local.document.getElementById('content')), /🏠 local/);
  assert.equal(local.document.querySelector('[data-inv-egeria-words]'), null);
  const bound = await setUp({ egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::C360' });
  assert.ok(bound.document.querySelector('[data-inv-bound-cue]'));
  assert.equal(bound.document.querySelector('[data-inv-egeria-words]'), null);
});
