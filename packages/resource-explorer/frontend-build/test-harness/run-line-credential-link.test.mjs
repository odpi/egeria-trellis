/** The run line's "set the database credential ›" opens the same test-before-save form in place, on a stage that has
 *  no header door; saving shows the cue, Cancel closes, no row data says so on the link, and the header door still works. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const typeInto = (el, v) => { el.value = v; };

async function setUp({ withRow = true } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: String(url) });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'PATCH') return ok({ slug: 'coco', db_user: 'new_user', credential_changed_at: new Date().toISOString() });
    if (String(url).endsWith('/credential-drift')) return ok({ in_registry: true, in_omsecrets: true, omsecrets_configured: true, in_sync: true });
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  const ns = await import('/static/next/stages/native-surveys.js');
  app.state.resourceType = 'db';
  app.state.databasesLoaded = true;
  app.state.databases = withRow ? [{ slug: 'coco', display_name: 'Coco', db_user: 'old_user', credential_status: 'ok', is_published: true, disposition: 'undecided' }] : [];
  app.state.selectedSlug = 'coco';
  app.state.workingSet = new Set();
  app.state.me = { user_id: 'dan' };
  const run = { state: 'failed', line: 'Survey · SELECT on 3 of 61 relation(s)', fixable: { kind: 'credential', steps: [1], reason: 'r', words: 'SELECT on 3 of 61 relation(s)', action: 'set the database credential' } };
  const root = document.createElement('div');
  document.body.appendChild(root);   // no resource header: this is the Analysis stage
  root.innerHTML = `<div data-line>${ns.nativeSurveyStatusHtml({ qualified_name: 'q', kind: 'survey_existing', display_name: 'S', runnable: true, run })}</div>`;
  ns.bindRunLineControls(root, 'coco');
  return { document, app, ns, root, calls };
}

test('the link opens the form under the run line on a stage with no header door', async () => {
  const c = await setUp();
  assert.equal(c.document.querySelector('[data-act="change-credentials"]'), null);
  const link = c.root.querySelector('[data-native-fix-credential]');
  assert.match(text(link), /^set the database credential ›$/);
  assert.match(link.title, /Egeria/);
  link.click();
  const form = c.root.querySelector('[data-native-credential-slot] [data-cred-change="coco"]');
  assert.ok(form, 'the form is drawn in place');
  assert.equal(form.querySelector('[data-cred-user]').value, 'old_user');
});

test('saving closes the form and shows "credential saved · re-run to use it"; the row follows', async () => {
  const c = await setUp();
  c.root.querySelector('[data-native-fix-credential]').click();
  const slot = c.root.querySelector('[data-native-credential-slot]');
  typeInto(slot.querySelector('[data-cred-user]'), 'new_user');
  typeInto(slot.querySelector('[data-cred-password]'), 'pw-not-real');
  slot.querySelector('[data-cred-save]').click();
  await tick(80);
  assert.ok(c.calls.find((x) => x.method === 'PATCH' && x.url === '/api/databases/coco/credentials'));
  assert.equal(slot.querySelector('[data-cred-change]'), null);
  assert.equal(text(slot), 'credential saved · re-run to use it');
  assert.equal(c.app.state.databases[0].db_user, 'new_user');
});

test('Cancel closes the form', async () => {
  const c = await setUp();
  c.root.querySelector('[data-native-fix-credential]').click();
  const slot = c.root.querySelector('[data-native-credential-slot]');
  slot.querySelector('[data-cred-cancel]').click();
  assert.equal(slot.innerHTML, '');
});

test('with no row data the link says so on itself, never a silent press', async () => {
  const c = await setUp({ withRow: false });
  const link = c.root.querySelector('[data-native-fix-credential]');
  link.click();
  assert.equal(text(link), 'open Scouting to change the credential');
  assert.equal(c.root.querySelector('[data-cred-change]'), null);
});

test('the header door still opens the same form', async () => {
  const c = await setUp();
  const host = c.document.createElement('div');
  host.id = 'resource-header';
  c.document.body.appendChild(host);
  host.innerHTML = c.app.resourceHeaderHtml('coco');
  c.app.bindResourceHeader();
  host.querySelector('[data-act="change-credentials"]').click();
  assert.ok(host.querySelector('[data-cred-change="coco"]'));
});
