/** EA dashboard authoring, B7b: nothing is written before the confirm, the preview shows what the server
 *  planned (as text, never as HTML), deleting a sheet sends its typed name, and the hooks land where the
 *  page expects. Runs the REAL render.js, dashboards_view.js and dashboards_author.js in jsdom; only fetch
 *  and Auth are stubbed. jsdom: see render.test.mjs.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';

test('the harness can run (jsdom found)', { skip: !REQUIRED && SKIP }, () => {
  assert.ok(jsdom, `EA_RENDER_HARNESS_REQUIRED=1 but ${SKIP}`);
});

const read = (rel) => fs.readFileSync(path.join(STATIC, rel), 'utf8');
const tick = () => new Promise(r => setTimeout(r, 5));

const PLAN = {
  state: 'planned', plan_id: 'p1', kind: 'save', sheet: 'My Sheet',
  actions: ['create Dashboard Dashboard::My Sheet', '<img src=x onerror="window.__pwned=1">'],
  notes: [], report: { state: 'create', message: "create Report 'Asset Count' (Dr.Egeria Create Report)" },
  filing: { state: 'not_filed', message: "not filed: the EgeriaAdvisor dashboard folder isn't seeded" },
  dr_egeria: ['## Create Report\n\n### Display Name\nAsset Count\n\n___\n'],
};

function makeWindow(answers) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><div id="messages"></div><div id="dashboard-status"></div>' +
    '<div><div id="dashboard-list"></div></div></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(read('vendor/marked.min.js'));
  w.eval(read('vendor/purify.min.js'));
  w.eval(read('render.js'));
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.calls = [];
  w.fetch = async (url, init) => {
    const body = init && init.body ? JSON.parse(init.body) : null;
    w.calls.push({ url, body });
    const a = answers(url, body);
    const status = (a && a.__status) || 200;     // an answer may carry an HTTP status, to test failures
    return { ok: status < 400, status, json: async () => a };
  };
  w.eval(read('dashboards_view.js'));
  w.eval(read('dashboards_author.js'));
  return w;
}

test('Save to a dashboard: preview first, write only on confirm, and the preview is text', { skip: SKIP }, async () => {
  const w = makeWindow((url) => url.startsWith('/api/dashboards?') || url === '/api/dashboards' ? { state: 'ok', sheets: [], viewer: 'v' }
    : url.endsWith('/plan') ? PLAN : url.endsWith('/apply') ? { state: 'written', actions: PLAN.actions, filing: PLAN.filing, notes: [] }
      : { state: 'not_visible', message: 'not yet' });
  await w.EADashAuthor.openSave({ spec: 'Analytic Demo - Element Count by Type', title: 'Asset Count' });
  const modal = w.document.querySelector('.dash-author-modal');
  const inputs = modal.querySelectorAll('input');
  inputs[0].value = 'My Sheet';                                    // the new sheet's name
  modal.querySelector('input[placeholder^="{"]').value = '{"type_name": "Asset"}';   // analytic params
  modal.querySelector('.dash-author-preview').click();
  await tick();
  const plan = w.calls.find(c => c.url.endsWith('/plan'));
  assert.equal(plan.body.kind, 'save');
  assert.equal(plan.body.request.sheet, 'My Sheet');
  assert.deepEqual(plan.body.request.report.analytic_params, { type_name: 'Asset' });
  assert.deepEqual(plan.body.request.new_sheet, { heading: '', description: '' });
  assert.ok(!w.calls.some(c => c.url.endsWith('/apply')), 'nothing written before the confirm');
  const items = [...modal.querySelectorAll('.dash-author-actions li')].map(li => li.textContent);
  assert.ok(items.includes('<img src=x onerror="window.__pwned=1">'), 'shown as text');
  assert.equal(modal.querySelectorAll('.dash-author-actions img').length, 0);
  assert.match(modal.querySelector('.dash-author-filing').textContent, /not filed/);
  modal.querySelector('.dash-author-confirm').click();
  await tick();
  const apply = w.calls.find(c => c.url.endsWith('/apply'));
  assert.deepEqual(apply.body, { plan_id: 'p1', confirm: true });
  assert.match(modal.querySelector('.dash-author-result').textContent, /Written: 2 action/);
  await tick();
  assert.ok(w.calls.some(c => c.url === '/api/dashboards?refresh=true'), 'the sheet list is read again after a write');
  assert.equal(w.__pwned, undefined);
});

test('bad analytic params are caught before anything is sent', { skip: SKIP }, async () => {
  const w = makeWindow(() => ({ state: 'ok', sheets: [] }));
  await w.EADashAuthor.openSave({ spec: 'S' });
  const modal = w.document.querySelector('.dash-author-modal');
  modal.querySelector('input[placeholder^="{"]').value = '[1, 2]';
  modal.querySelector('.dash-author-preview').click();
  await tick();
  assert.match(modal.querySelector('.dash-author-error').textContent, /JSON object/);
  assert.ok(!w.calls.some(c => c.url.includes('/author/')));
});

test('a refused plan is said and offers no confirm', { skip: SKIP }, async () => {
  const w = makeWindow(() => ({ state: 'refused', message: "A Report named 'X' already exists for spec 'Collections'" }));
  const { modal } = await w.EADashAuthor.previewAndConfirm('t', 'save', { sheet: 's' });
  assert.match(modal.body.querySelector('.dash-author-refused').textContent, /already exists/);
  assert.equal(modal.body.querySelector('.dash-author-confirm'), null);
});

test('deleting a sheet sends the name the user typed as the confirm', { skip: SKIP }, async () => {
  const w = makeWindow((url) => url.endsWith('/plan')
    ? { ...PLAN, kind: 'delete_sheet', confirm_with: 'My Sheet', report: null, filing: null, dr_egeria: [] }
    : { state: 'refused', message: "Type the sheet's name, 'My Sheet', to confirm." });
  const { modal } = await w.EADashAuthor.previewAndConfirm('Delete', 'delete_sheet', { sheet: 'My Sheet' });
  modal.body.querySelector('input').value = 'My Shee';
  modal.body.querySelector('.dash-author-confirm').click();
  await tick();
  assert.deepEqual(w.calls.find(c => c.url.endsWith('/apply')).body, { plan_id: 'p1', confirm: 'My Shee' });
  assert.match(modal.body.textContent, /Type the sheet's name/);
});

test('hooks: tiles get move / remove, the sheet gets delete, a report result gets Save', { skip: SKIP }, async () => {
  const w = makeWindow((url, body) => url.endsWith('/plan') ? { state: 'refused', message: 'x' }
    : { state: 'ok', tile: { kind: 'kpi', display: '1' }, cache: {} });
  const sheet = { name: 'S', heading: 'S', viewer: 'v', breaks: [], placements: [
    { position: 1, kind: 'report', ref: 'Asset Count', title: 'Asset Count', runnable: true, params: {}, report: {} }] };
  const wrap = w.EADashboards.renderDashboard(sheet);
  await wrap._ready;
  assert.ok(wrap.querySelector('.dash-delete-sheet'));
  const buttons = [...wrap.querySelectorAll('.dash-author-tile button')].map(b => b.textContent);
  assert.deepEqual(buttons, ['↑', '↓', '✕']);
  wrap.querySelectorAll('.dash-author-tile button')[2].click();
  await tick();
  const plan = w.calls.find(c => c.url.endsWith('/plan'));
  assert.deepEqual(plan.body, { kind: 'remove', request: { sheet: 'S', placement: 'Asset Count' } });

  const msg = w.document.createElement('div');
  w.EADashAuthor.offerSave(msg, { query_type: 'report', report_name: 'Collections' });
  assert.ok(msg.querySelector('.dash-offer-save'));
  const failed = w.document.createElement('div');
  w.EADashAuthor.offerSave(failed, { query_type: 'report', report_name: 'Collections', run_error: 'timed out' });
  w.EADashAuthor.offerSave(failed, { query_type: 'general' });
  assert.equal(failed.querySelector('.dash-offer-save'), null);
  assert.doesNotMatch(read('dashboards_author.js'), /\son[a-z]+\s*=\s*["']/);
});

test('a sheet list that cannot be read says so, and is not passed off as "no sheets"', { skip: SKIP }, async () => {
  const w = makeWindow((url) => url === '/api/dashboards' ? { __status: 503, detail: 'Egeria connection is not configured' } : {});
  await w.EADashAuthor.openSave({ spec: 'S' });
  const modal = w.document.querySelector('.dash-author-modal');
  assert.equal(modal.querySelector('.dash-author-sheets-error').textContent,
    'Could not read your sheets: Egeria connection is not configured');
});

test('zones that cannot be read say why, from the status or from the answer', { skip: SKIP }, async () => {
  for (const [answer, words] of [
    [{ __status: 500, detail: 'boom' }, 'Could not read zones: boom'],
    [{ state: 'read_failed', message: 'Egeria refused the zone read', zones: [] }, 'Could not read zones: Egeria refused the zone read'],
    [{ zones: [], count: 0, error: 'ConnectionError: refused' }, 'Could not read zones: ConnectionError: refused'],
  ]) {
    const w = makeWindow((url) => url === '/api/egeria/zones' ? answer : { state: 'ok', sheets: [] });
    await w.EADashAuthor.openSave({ spec: 'S' });
    const vis = [...w.document.querySelectorAll('.dash-author-zones select')][0];
    vis.value = 'zones';
    vis.dispatchEvent(new w.Event('change'));
    await tick();
    assert.equal(w.document.querySelector('.dash-author-zones-error').textContent, words);
  }
});
