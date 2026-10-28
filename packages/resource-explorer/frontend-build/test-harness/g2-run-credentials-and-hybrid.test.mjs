/** Behaviour: PI-016 (credential for ONE run), PI-018 (try Egeria first),
 *  PI-019 (Catalog now, then Retry — never automatic), PI-020 (the failing
 *  sentence on the launch note at once).
 *
 *  Real app.js and router; only fetch is stubbed, speaking the shapes of
 *  web/routes/survey_definitions.py and activity.py. The credential override is
 *  session memory: every test greps the DOM, every URL and every request log
 *  line for the fake password. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const FAKE_PW = 'test-password-not-real';
const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const CAND = (over = {}) => ({
  display_name: 'Database Analysis Survey', qualified_name: 'SurveyDefinition::db-analysis',
  survey_kind: 'scouting', steps: [{}, {}, {}], steps_local: [{}, {}], steps_native: [{}],
  analysis_ids: [], last_run_at: null, ...over,
});

function makeBackend({ runResponse = null, activity = null, runs = [], candidates = [CAND()] } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method, url: u, body, raw: options.body || '' });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (method === 'POST' && /\/api\/survey-definitions\/database\/[^/]+\/run$/.test(u)) {
      return ok(runResponse || { status: 'started', activity_id: 'a1', run_id: 7 });
    }
    if (method === 'GET' && u === '/api/activity/a1') {
      return ok(activity || { id: 'a1', status: 'ok', summary: 'Survey Definition run complete', detail: JSON.stringify({ steps: [], errors: [] }) });
    }
    if (method === 'GET' && u.startsWith('/api/activity/?entity_slug=')) return ok(runs);
    if (method === 'GET' && /\/candidates/.test(u)) {
      return ok({ technology_type: 'PostgreSQL Database', candidates, scoping: 'questions', phase: 'scouting', egeria_native_processes: [] });
    }
    if (method === 'GET' && u === '/api/survey-definitions/definitions') return ok([]);
    return ok(u.includes('/groups') ? [] : {});
  };
  return calls;
}

async function setUp(opts = {}, { published = true } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = makeBackend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  app.state.resourceType = 'db';
  app.state.databasesLoaded = true;
  app.state.databases = [{ slug: 'coco', display_name: 'Coco', is_published: published, credential_status: 'ok', db_user: 'stored_user' }];
  app.state.selectedSlug = 'coco';
  app.state.stage = 'scouting';
  app.state.subTab = 'survey';
  app.state.me = { user_id: 'dan' };
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  return { document, window, app, calls, content };
}

const dlgOf = (document) => document.getElementById('wl-detail');
const post = (calls) => calls.find((c) => c.method === 'POST' && c.url.endsWith('/run'));
const leakFree = (ctx) => {
  assert.ok(!ctx.document.documentElement.outerHTML.includes(FAKE_PW), 'password not in the DOM');
  for (const c of ctx.calls) assert.ok(!c.url.includes(FAKE_PW), `password not in URL ${c.url}`);
};
function typeInto(el, value) {
  el.value = value;
  el.dispatchEvent(new el.ownerDocument.defaultView.Event('input', { bubbles: true }));
}

// ── PI-016 ──────────────────────────────────────────────────────────────────

test('the run dialog offers "Use different credentials for this run" and reveals user, password and remember only when ticked', async () => {
  const ctx = await setUp();
  ctx.app.planSurveyRun(CAND(), 'coco');
  const d = dlgOf(ctx.document);
  const box = d.querySelector('[data-run-override]');
  assert.ok(box, 'the override checkbox is there');
  assert.match(text(d), /Use different credentials for this run/);
  assert.equal(d.querySelector('[data-run-override-user]'), null, 'fields stay hidden until asked for');
  box.click();
  assert.ok(d.querySelector('[data-run-override-user]'));
  assert.equal(d.querySelector('[data-run-override-password]').type, 'password');
  assert.match(text(d), /remember for this session/);
});

test('an override run sends user and password in the body only, and the note says "ran as <user> (this run)"', async () => {
  const ctx = await setUp({ runResponse: { status: 'started', activity_id: 'a1', run_id: null, ran_as: { user: 'one_off_user', scope: 'this run' } } });
  const note = ctx.document.createElement('div'); note.id = 'survey-note'; ctx.document.body.appendChild(note);
  ctx.app.planSurveyRun(CAND(), 'coco');
  const d = dlgOf(ctx.document);
  d.querySelector('[data-run-override]').click();
  typeInto(d.querySelector('[data-run-override-user]'), 'one_off_user');
  typeInto(d.querySelector('[data-run-override-password]'), FAKE_PW);
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  const p = post(ctx.calls);
  assert.equal(p.body.db_user, 'one_off_user');
  assert.equal(p.body.db_pwd, FAKE_PW);
  assert.ok(!p.url.includes('one_off_user') && !p.url.includes(FAKE_PW), 'never in the URL');
  assert.match(text(note), /ran as one_off_user \(this run\)/);
  leakFree(ctx);
});

test('half an override is refused in the dialog and nothing is sent', async () => {
  const ctx = await setUp();
  ctx.app.planSurveyRun(CAND(), 'coco');
  const d = dlgOf(ctx.document);
  d.querySelector('[data-run-override]').click();
  typeInto(d.querySelector('[data-run-override-user]'), 'one_off_user');
  d.querySelector('[data-act="go"]').click();
  await tick();
  assert.equal(post(ctx.calls), undefined);
  assert.match(text(d.querySelector('[data-run-override-error]')), /both a user and a password/);
});

test('without the override ticked no credential is sent and the stored one is left to the server', async () => {
  const ctx = await setUp();
  ctx.app.planSurveyRun(CAND(), 'coco');
  dlgOf(ctx.document).querySelector('[data-act="go"]').click();
  await tick(80);
  const p = post(ctx.calls);
  assert.equal('db_user' in p.body, false);
  assert.equal('db_pwd' in p.body, false);
});

test('"remember for this session" keeps it in memory for the next dialog on that database; unticked forgets it', async () => {
  const ctx = await setUp();
  ctx.app.planSurveyRun(CAND(), 'coco');
  let d = dlgOf(ctx.document);
  d.querySelector('[data-run-override]').click();
  typeInto(d.querySelector('[data-run-override-user]'), 'one_off_user');
  typeInto(d.querySelector('[data-run-override-password]'), FAKE_PW);
  d.querySelector('[data-run-override-remember]').click();
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  ctx.app.planSurveyRun(CAND(), 'coco');
  d = dlgOf(ctx.document);
  assert.equal(d.querySelector('[data-run-override]').checked, true, 'remembered: ticked again');
  assert.equal(d.querySelector('[data-run-override-user]').value, 'one_off_user');
  assert.equal(d.querySelector('[data-run-override-password]').value, FAKE_PW);
  assert.ok(!ctx.document.documentElement.outerHTML.includes(FAKE_PW), 'a value, never markup');
  // a different database does not inherit it
  ctx.app.planSurveyRun(CAND(), 'other_db');
  assert.equal(dlgOf(ctx.document).querySelector('[data-run-override]').checked, false);
  // not remembered when the box is unticked on a run
  ctx.app.planSurveyRun(CAND(), 'coco');
  d = dlgOf(ctx.document);
  d.querySelector('[data-run-override-remember]').click();
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  ctx.app.planSurveyRun(CAND(), 'coco');
  assert.equal(dlgOf(ctx.document).querySelector('[data-run-override]').checked, false);
  assert.ok(!ctx.window.localStorage.getItem('x') && !JSON.stringify(ctx.window.localStorage).includes(FAKE_PW));
  assert.ok(!JSON.stringify(ctx.window.sessionStorage).includes(FAKE_PW), 'not in web storage either');
});

test('only a database run offers the override and the hybrid switch', async () => {
  const ctx = await setUp();
  ctx.app.state.resourceType = 'repo';
  ctx.app.planSurveyRun(CAND(), 'coco');
  const d = dlgOf(ctx.document);
  assert.equal(d.querySelector('[data-run-override]'), null);
  assert.equal(d.querySelector('[data-run-hybrid]'), null);
});

// ── PI-018 ──────────────────────────────────────────────────────────────────

test('hybrid is disabled with its reason when the database is not cataloged in Egeria, and the run says local only', async () => {
  const ctx = await setUp({}, { published: false });
  ctx.app.planSurveyRun(CAND(), 'coco');
  const d = dlgOf(ctx.document);
  const h = d.querySelector('[data-run-hybrid]');
  assert.equal(h.disabled, true);
  assert.equal(h.checked, false);
  assert.match(text(d), /not published to Egeria · publish it on Curate first/);
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  assert.equal(post(ctx.calls).body.force_custom, true, 'no Egeria-first for a database Egeria does not hold');
});

test('hybrid is on by default for a cataloged database, shows its confirm sentence, and unticking sends force_custom', async () => {
  const ctx = await setUp({}, { published: true });
  ctx.app.planSurveyRun(CAND(), 'coco');
  let d = dlgOf(ctx.document);
  const h = d.querySelector('[data-run-hybrid]');
  assert.equal(h.disabled, false);
  assert.equal(h.checked, true);
  assert.match(text(d), /publishes to Egeria when it finishes/);
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  assert.equal('force_custom' in post(ctx.calls).body, false, 'default behaviour is unchanged');
  ctx.calls.length = 0;
  ctx.app.planSurveyRun(CAND(), 'coco');
  d = dlgOf(ctx.document);
  d.querySelector('[data-run-hybrid]').click();
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  assert.equal(post(ctx.calls).body.force_custom, true);
});

// ── per-step provenance on the run rows ─────────────────────────────────────

test('run rows say which source answered each step and who it ran as', async () => {
  const detail = JSON.stringify({ steps: [
    { step: 'S::local', status: 'ok', answered_by: 'local', ran_as: { user: 'one_off_user', scope: 'this run' } },
    { step: 'S::adaptive', status: 'ok', answered_by: 'egeria-custom', ran_as: { user: 'one_off_user', scope: 'this run' } },
    { step: 'S::native', status: 'ok', answered_by: 'egeria' },
  ] });
  const ctx = await setUp({ runs: [{ id: 'r1', operation: 'survey', ts: new Date().toISOString(), status: 'ok', summary: 'Done', detail }] });
  await ctx.app.openRunsList('coco');
  const t = text(dlgOf(ctx.document));
  assert.match(t, /local · ran as one_off_user \(this run\)/);
  assert.match(t, /local scan, published to Egeria · ran as one_off_user \(this run\)/);
  assert.match(t, /S::native|native/);
  const native = [...dlgOf(ctx.document).querySelectorAll('li')].find((li) => /native/.test(li.textContent));
  assert.match(native.textContent, /answered by Egeria/);
  assert.doesNotMatch(native.textContent, /ran as/);
  leakFree(ctx);
});

// ── PI-020 ──────────────────────────────────────────────────────────────────

test('a failing credential shows its first sentence on the launch note at once, and still after the pane re-renders', async () => {
  const detail = JSON.stringify({
    errors: [`RE step 'postgres_schema_and_stats' failed: connection to server at "pg" failed: FATAL: password authentication failed for user "stored_user". More text.`],
    steps: [{ step: 'S::a', status: 'error', answered_by: 'local' }],
  });
  const ctx = await setUp({ activity: { id: 'a1', status: 'error', summary: 'Completed with 1 error(s)', detail } });
  const note = ctx.document.createElement('div'); note.id = 'survey-note'; ctx.document.body.appendChild(note);
  ctx.app.planSurveyRun(CAND(), 'coco');
  dlgOf(ctx.document).querySelector('[data-act="go"]').click();
  await tick(120);
  const n = ctx.document.getElementById('survey-note');
  assert.match(text(n), /password authentication failed for user "stored_user"/);
  assert.doesNotMatch(text(n), /More text/, 'the first sentence, the rest on demand');
  // the pane re-renders (loadSurveyPane replaces the note): the sentence is derived, not appended
  await ctx.app.loadSurveyPane();
  assert.match(text(ctx.document.getElementById('survey-note')), /password authentication failed for user "stored_user"/);
});

// ── PI-019 ──────────────────────────────────────────────────────────────────

const NOT_CATALOGED = CAND({
  last_run_at: new Date().toISOString(), last_run_status: 'error',
  last_run_errors: ["Step 'x' failed: Database 'coco' has no stored Egeria asset guid — cannot trigger Egeria's native survey for an unpublished database."],
});

test('a run that failed because the database is not cataloged offers "Publish now →" which goes to Curate, with no Retry yet', async () => {
  const ctx = await setUp({}, { published: false });
  ctx.content.innerHTML = ctx.app.surveyRowHtml(NOT_CATALOGED);
  const catalog = ctx.content.querySelector('[data-catalog-now]');
  assert.ok(catalog);
  assert.match(text(catalog), /Publish now →/);
  assert.equal(ctx.content.querySelector('[data-retry-run]'), null);
  assert.equal(ctx.calls.filter((c) => c.method === 'POST').length, 0, 'no automatic anything');
});

test('Publish now → routes to the Curate stage; once the database reads cataloged the same row offers Retry →, which plans, never runs', async () => {
  const ctx = await setUp({}, { published: false });
  ctx.content.innerHTML = ctx.app.surveyRowHtml(NOT_CATALOGED);
  ctx.app.bindSurveyRowActions(ctx.content, [NOT_CATALOGED], 'coco');
  ctx.content.querySelector('[data-catalog-now]').click();
  await tick();
  assert.equal(ctx.app.state.stage, 'curate');
  // the publish-state line now proves it is cataloged
  ctx.app.state.databases[0].is_published = true;
  ctx.content.innerHTML = ctx.app.surveyRowHtml(NOT_CATALOGED);
  assert.equal(ctx.content.querySelector('[data-catalog-now]'), null);
  const retry = ctx.content.querySelector('[data-retry-run]');
  assert.ok(retry);
  assert.match(text(retry), /Retry →/);
  ctx.app.bindSurveyRowActions(ctx.content, [NOT_CATALOGED], 'coco');
  retry.click();
  await tick();
  assert.ok(dlgOf(ctx.document), 'Retry opens the same plan dialog');
  assert.equal(post(ctx.calls), undefined, 'nothing ran until the person confirms');
});

test('an ordinary failure, or a database that is fine, offers neither', async () => {
  const ctx = await setUp({}, { published: true });
  ctx.content.innerHTML = ctx.app.surveyRowHtml(CAND({ last_run_at: new Date().toISOString(), last_run_status: 'error', last_run_errors: ['boom'] }));
  assert.equal(ctx.content.querySelector('[data-catalog-now]'), null);
  assert.equal(ctx.content.querySelector('[data-retry-run]'), null);
});

test('running with the override box unticked forgets what was remembered', async () => {
  const ctx = await setUp();
  ctx.app.planSurveyRun(CAND(), 'coco');
  let d = dlgOf(ctx.document);
  d.querySelector('[data-run-override]').click();
  typeInto(d.querySelector('[data-run-override-user]'), 'one_off_user');
  typeInto(d.querySelector('[data-run-override-password]'), FAKE_PW);
  d.querySelector('[data-run-override-remember]').click();
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  ctx.app.planSurveyRun(CAND(), 'coco');
  d = dlgOf(ctx.document);
  const box = d.querySelector('[data-run-override]');
  box.checked = false;
  box.dispatchEvent(new ctx.window.Event('change', { bubbles: true }));
  d.querySelector('[data-act="go"]').click();
  await tick(80);
  ctx.app.planSurveyRun(CAND(), 'coco');
  assert.equal(dlgOf(ctx.document).querySelector('[data-run-override]').checked, false);
});

test('both wordings of the surveyor error offer "Publish now →": the server now says "not yet published to Egeria"', async () => {
  // The server text changed (owner, 2026-10-12); the old text stays matched so either PR can merge first.
  for (const e of ["Database 'coco' is not yet published to Egeria under qualified name x — cannot survey it",
                   "Database 'coco' is not yet cataloged in Egeria under qualified name x — cannot survey it"]) {
    const ctx = await setUp({}, { published: false });
    ctx.content.innerHTML = ctx.app.surveyRowHtml(CAND({ last_run_at: new Date().toISOString(), last_run_status: 'error', last_run_errors: [e] }));
    assert.ok(ctx.content.querySelector('[data-catalog-now]'), e);
  }
});
