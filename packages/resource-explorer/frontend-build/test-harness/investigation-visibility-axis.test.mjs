/** F56 (BRIEF-F56-INVESTIGATION-VISIBILITY-AXIS-2026-11-04): visibility is its own axis, and "Private investigation"
 *  is a preset. Pins the served words: the header ("<kind> · creator-only · in Egeria as <kind>" or "private
 *  investigation · not in Egeria"), the create form's preset above the five kinds and the visibility radio with its
 *  consequence line, Reclassify's "still creator-only", and that Publish to Egeria asks for the kind and visibility
 *  before it sends them. */
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

const CLASSIFICATIONS = {
  classifications: [
    { name: 'PersonalProject', label: 'Personal', requires_hypothesis: false },
    { name: 'Task', label: 'Task', requires_hypothesis: false },
    { name: 'StudyProject', label: 'Study', requires_hypothesis: false },
    { name: 'Campaign', label: 'Campaign', requires_hypothesis: false },
    { name: 'Experiment', label: 'Experiment', requires_hypothesis: true },
  ],
  bindings: [{ name: 'egeria', label: 'Create in Egeria' }, { name: 'local', label: 'Ad-hoc' }],
  default_classification: 'StudyProject',
  default_binding: 'egeria',
  visibilities: [
    { name: 'creator-only', label: 'Creator-only',
      consequence: 'creator-only: only you see it in Resource Explorer; published zoned to you' },
    { name: 'shared', label: 'Shared',
      consequence: 'shared: everyone signed in to Resource Explorer sees it; published in the publish zones' },
  ],
  default_visibility_by_kind: { PersonalProject: 'creator-only', Task: 'shared', StudyProject: 'shared',
    Campaign: 'shared', Experiment: 'creator-only' },
  private_preset: { label: 'Private investigation — mine, not in Egeria', description: 'Only you see it.',
    egeria_binding: 'local', visibility: 'creator-only', project_classification: '' },
};

async function setUp(inv) {
  const { document, window } = makeDomEnvironment();
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url); const method = (options.method || 'GET').toUpperCase();
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'POST') posts.push({ url: u, body: options.body ? JSON.parse(options.body) : null });
    if (/\/promote$/.test(u)) return ok({ ok: true, classification_requested: 'StudyProject', classification_confirmed: 'StudyProject', members_linked: [], members_unlinkable: [] });
    if (/\/reclassify$/.test(u)) return ok({ ok: true, to_classification: 'StudyProject', to_visibility: 'creator-only', visibility_kept: true, direction: 'lateral', still_public: [], errors: [], local_applied: true });
    if (/\/members$/.test(u)) return ok([]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/link-status$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok(CLASSIFICATIONS);
    if (/^\/api\/investigations\/([^/?]+)$/.test(u)) return ok(inv);
    if (/^\/api\/investigations\/\?/.test(u)) return ok([inv]);
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
  const mod = await import('/static/next/stages/investigation.js');
  app.state.stage = 'investigation'; app.state.me = { user_id: 'alice' }; app.state.investigations = [];
  mod.openInvestigationDetail(inv.slug);
  await mod.renderInvestigation();
  await tick();
  return { document, posts, mod };
}

const PRIVATE = { slug: 'just-looking', display_name: 'Just looking', status: 'open', description: '',
  project_classification: '', egeria_binding: 'local', visibility: 'creator-only', is_private_investigation: true,
  purposes: [], created_by: 'alice', is_mine: true };
const PERSONAL_IN_EGERIA = { slug: 'mine', display_name: 'Mine', status: 'open', description: '',
  project_classification: 'PersonalProject', egeria_binding: 'egeria', visibility: 'creator-only',
  is_private_investigation: false, egeria_project_guid: 'g-1', egeria_project_qualified_name: 'Project::mine',
  purposes: [], created_by: 'alice', is_mine: true };

test('the header names a private investigation only when it is local and creator-only', async () => {
  const t = await setUp(PRIVATE);
  assert.equal(text(t.document.querySelector('[data-inv-kind-words]')), 'private investigation · not in Egeria');
});

test('a creator-only investigation in Egeria is named by kind and visibility, never "private investigation"', async () => {
  const t = await setUp(PERSONAL_IN_EGERIA);
  assert.equal(text(t.document.querySelector('[data-inv-kind-words]')), 'Personal · creator-only · in Egeria as PersonalProject');
  assert.doesNotMatch(text(t.document.getElementById('content')), /private investigation/i);
});

test('the create form offers the preset above the five kinds, and a kind shows its default visibility and consequence', async () => {
  const t = await setUp(PERSONAL_IN_EGERIA);
  await t.mod.openCreateDialog();
  await tick();
  const sel = t.document.getElementById('inv-new-class');
  const options = [...sel.options].map((o) => o.textContent.trim());
  assert.equal(options[0], 'Private investigation — mine, not in Egeria');
  assert.deepEqual(options.slice(1), ['Personal', 'Task', 'Study', 'Campaign', 'Experiment']);
  assert.equal(sel.value, 'StudyProject');
  const checked = () => t.document.querySelector('input[name="inv-new-vis"]:checked').value;
  assert.equal(checked(), 'shared');
  sel.value = 'PersonalProject'; sel.dispatchEvent(new t.document.defaultView.Event('change'));
  assert.equal(checked(), 'creator-only');
  assert.match(text(t.document.querySelector('[data-vis-group="inv-new"] [data-vis-consequence]')),
    /^creator-only: only you see it in Resource Explorer; published zoned to you$/);
  sel.value = '__private__'; sel.dispatchEvent(new t.document.defaultView.Event('change'));
  assert.ok(t.document.getElementById('inv-new-kind-only').classList.contains('hidden'), 'the preset fixes binding and visibility');
});

test('the preset creates local + creator-only with no kind', async () => {
  const t = await setUp(PERSONAL_IN_EGERIA);
  await t.mod.openCreateDialog();
  await tick();
  t.document.getElementById('inv-new-name').value = 'Just looking';
  const sel = t.document.getElementById('inv-new-class');
  sel.value = '__private__'; sel.dispatchEvent(new t.document.defaultView.Event('change'));
  t.document.getElementById('inv-new-submit').click();
  await tick(150);
  const create = t.posts.find((p) => p.url === '/api/investigations/');
  assert.ok(create, 'posted');
  assert.equal(create.body.project_classification, '');
  assert.equal(create.body.egeria_binding, 'local');
  assert.equal(create.body.visibility, 'creator-only');
});

test('Publish to Egeria asks for the kind and visibility, and sends them', async () => {
  const t = await setUp(PRIVATE);
  t.document.querySelector('[data-act="inv-promote"]').click();
  await tick();
  assert.ok(t.document.querySelector('[data-inv-promote-ask]'), 'the dialog opens; nothing is sent yet');
  assert.equal(t.posts.filter((p) => /\/promote$/.test(p.url)).length, 0);
  assert.equal(t.document.getElementById('inv-promote-kind').value, 'StudyProject', 'StudyProject is the default kind');
  assert.equal(t.document.querySelector('input[name="inv-promote-vis"]:checked').value, 'creator-only', 'kept');
  t.document.getElementById('inv-promote-go').click();
  await tick(150);
  const sent = t.posts.find((p) => /\/promote$/.test(p.url));
  assert.deepEqual(sent.body, { project_classification: 'StudyProject', hypothesis: '', visibility: 'creator-only' });
});

test('Reclassify says the visibility stays, and sends it unchanged', async () => {
  const t = await setUp(PERSONAL_IN_EGERIA);
  t.document.querySelector('[data-act="inv-reclassify"]').click();
  await tick();
  assert.match(text(t.document.querySelector('[data-vis-group="inv-reclass"] [data-vis-consequence]')), /\(still creator-only\)$/);
  t.document.getElementById('inv-reclass-select').value = 'StudyProject';
  t.document.getElementById('inv-reclass-save').click();
  await tick(150);
  const sent = t.posts.find((p) => /\/reclassify$/.test(p.url));
  assert.equal(sent.body.visibility, 'creator-only');
  assert.match(text(t.document.getElementById('inv-reclass-result')), /Reclassified to StudyProject · still creator-only\./);
});
