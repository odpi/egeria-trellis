/** B6: the catalog's restricted section, "Used by" filter, and the publish dialog's steps.
 *
 *  Runs the REAL specs_publish.js in a jsdom window against catalog markup built the way index.html's
 *  loadReports() builds it. Only fetch and Auth are stubbed.
 *
 *  Run:  node --test advisor/web/static/test/specs_publish.test.mjs
 *  jsdom is found the same way as render.test.mjs ($EA_JSDOM_DIR, or RE's frontend-build node_modules
 *  in this or the main trellis checkout); without it the tests are skipped with that reason, unless
 *  EA_RENDER_HARNESS_REQUIRED=1.
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
const opts = { skip: !REQUIRED && SKIP };

const ADVISOR = 'ReportSpecFolder::Consumer::EgeriaAdvisor';
const PORTAL = 'ReportSpecFolder::Consumer::Portal';
// The facet and the index use consumer DISPLAY names (live shape on laz: tests/unit/fixtures/egeria_live/
// consumer_index.json); restricted specs list folder QNs plus the server's `used_by` names.
const ADVISOR_NAME = 'Egeria Advisor';
const PORTAL_NAME = 'Portal';
const HOSTILE = '<img src=x onerror=alert(1)>';

function setup(posts = [], zonesReply = { ok: true, status: 200, data: { zones: ['finance', 'hr'], count: 2 } }) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><div id="side"><div id="report-catalog"></div></div></body></html>',
    { runScripts: 'outside-only' });
  const w = dom.window;
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.fetch = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : null;
    posts.push({ url, body });
    let data = {};
    if (url === '/api/egeria/zones') return { ok: zonesReply.ok, status: zonesReply.status, json: async () => zonesReply.data };
    else if (body && body.step === 'preview') data = { step: 'preview', digest: 'd1', markdown: '## Create Report Spec', steps: ['create it'], held: { how: 'not held' } };
    else if (body) data = { step: body.step, digest: 'd1', result: { success: true, commands_detail: [] }, after: { message: 'Syncing.' } };
    return { ok: true, status: 200, json: async () => data };
  };
  w.eval(fs.readFileSync(path.join(STATIC, 'specs_publish.js'), 'utf8'));
  return w;
}

/** The catalog the way loadReports() renders it: details > div > button(name + badge span). */
function catalog(w, rows) {
  const el = w.document.getElementById('report-catalog');
  const det = w.document.createElement('details');
  const ul = w.document.createElement('div');
  for (const [name, source] of rows) {
    const b = w.document.createElement('button');
    b.textContent = name;
    if (source !== 'builtin') { const s = w.document.createElement('span'); s.textContent = source; b.appendChild(s); }
    ul.appendChild(b);
  }
  det.appendChild(ul);
  el.appendChild(det);
  return el;
}

const DATA = {
  sources: { 'Spec-A': { source: 'builtin' }, 'Mine': { source: 'user' }, 'Held': { source: 'egeria' } },
  used_by: { state: 'ok', consumers: [ADVISOR_NAME, PORTAL_NAME], folders: { [ADVISOR]: ADVISOR_NAME, [PORTAL]: PORTAL_NAME },
    index: { 'Held': [ADVISOR_NAME], 'Spec-A': [PORTAL_NAME] }, message: null },
  restricted: { state: 'ok', specs: [{ label: 'Spec-Fin', zones: ['finance'], folders: [ADVISOR], used_by: [ADVISOR_NAME] }], message: null },
  catalog_status: { message: '2 spec(s) are not listed because they cannot run; each says why.', unrunnable: [] },
};

test('restricted specs show their zones; an empty state shows the server words', opts, () => {
  const w = setup();
  const el = catalog(w, [['Spec-A', 'builtin']]);
  w.SpecsPublish.onCatalog(DATA, el);
  const box = w.document.getElementById('specs-restricted');
  assert.match(box.textContent, /Restricted \(1\)/);
  assert.match(box.textContent, /Spec-Fin/);
  assert.match(box.textContent, /restricted: finance/);
  w.SpecsPublish.onCatalog({ ...DATA, restricted: { state: 'not_signed_in', specs: [], message: 'Sign in to see restricted report specs.' } }, el);
  assert.match(w.document.getElementById('specs-restricted').textContent, /Sign in to see restricted report specs\./);
  assert.match(w.document.getElementById('specs-catalog-status').textContent, /cannot run/);
});

test('"Used by" filters the catalog rows', opts, () => {
  const w = setup();
  const el = catalog(w, [['Spec-A', 'builtin'], ['Mine', 'user'], ['Held', 'egeria']]);
  w.SpecsPublish.onCatalog(DATA, el);
  const sel = w.document.querySelector('#specs-used-by select');
  sel.value = ADVISOR_NAME;
  sel.dispatchEvent(new w.Event('change'));
  const shown = [...el.querySelectorAll('[data-spec-row]')].filter(r => r.style.display !== 'none')
    .map(r => r.querySelector('button[data-spec]').getAttribute('data-spec'));
  assert.deepEqual(shown, ['Held']);
});

test('only local/user specs offer publish, only held ones offer withdraw', opts, () => {
  const w = setup();
  const el = catalog(w, [['Spec-A', 'builtin'], ['Mine', 'user'], ['Held', 'egeria']]);
  w.SpecsPublish.onCatalog(DATA, el);
  const actions = Object.fromEntries([...el.querySelectorAll('[data-spec-row]')].map(r =>
    [r.querySelector('button[data-spec]').getAttribute('data-spec'), [...r.querySelectorAll('button:not([data-spec])')].map(b => b.textContent)]));
  assert.deepEqual(actions, { 'Spec-A': [], 'Mine': ['publish'], 'Held': ['withdraw'] });
});

test('a hostile spec name is text, never markup', opts, () => {
  const w = setup();
  const el = catalog(w, [['x', 'builtin']]);
  w.SpecsPublish.onCatalog({ ...DATA, restricted: { state: 'ok', specs: [{ label: HOSTILE, zones: [HOSTILE], folders: [] }] } }, el);
  const box = w.document.getElementById('specs-restricted');
  assert.equal(box.querySelectorAll('img').length, 0);
  assert.match(box.textContent, /onerror/);
});

test('publish: preview, then confirm, then process with the previewed digest', opts, async () => {
  const posts = [];
  const w = setup(posts);
  await w.SpecsPublish.openPublish('Mine');
  const dlg = w.document.querySelector('[role=dialog]');
  const button = (t) => [...dlg.querySelectorAll('button')].find(b => b.textContent === t);
  button('Publish').click();
  await new Promise(r => setTimeout(r, 0));
  assert.match(dlg.textContent, /Preview the document first\./);
  assert.equal(posts.filter(p => p.body && p.body.step).length, 0);
  button('Preview').click();
  await new Promise(r => setTimeout(r, 0));
  assert.match(dlg.textContent, /## Create Report Spec/);
  button('Publish').click();
  await new Promise(r => setTimeout(r, 0));
  assert.match(dlg.textContent, /Tick the confirmation first\./);
  dlg.querySelector('#specs-confirm').checked = true;
  button('Publish').click();
  await new Promise(r => setTimeout(r, 0));
  const proc = posts.find(p => p.body && p.body.step === 'process');
  assert.ok(proc, 'process was posted');
  assert.equal(proc.url, '/api/reports/specs/Mine/publish');
  assert.deepEqual([proc.body.digest, proc.body.confirmed, proc.body.visibility], ['d1', true, 'public']);
  assert.match(dlg.textContent, /Done\./);
});

const PROVEN = { publish: { restricted: { enabled: true, message: null, proof: 'p' } } };
const UNPROVEN = { publish: { restricted: { enabled: false, message: 'Restricted publish is not yet proven on this platform.' } } };
const tick = () => new Promise(r => setTimeout(r, 0));

test('D5: restricted is disabled with the reason until the zone proof is recorded; no zone read', opts, async () => {
  const posts = [];
  const w = setup(posts);
  w.SpecsPublish.onCatalog({ ...DATA, ...UNPROVEN }, catalog(w, [['Mine', 'user']]));
  await w.SpecsPublish.openPublish('Mine');
  const dlg = w.document.querySelector('[role=dialog]');
  assert.equal(dlg.querySelector('input[value=restricted]').disabled, true);
  assert.match(dlg.querySelector('[data-restricted-off]').textContent, /not yet proven on this platform/);
  assert.equal(posts.filter(p => p.url === '/api/egeria/zones').length, 0);
});

test('zones: an HTTP error shows its detail, not "no zones"', opts, async () => {
  const w = setup([], { ok: false, status: 401, data: { detail: 'Sign in to read from Egeria.' } });
  w.SpecsPublish.onCatalog({ ...DATA, ...PROVEN }, catalog(w, [['Mine', 'user']]));
  await w.SpecsPublish.openPublish('Mine');
  const text = w.document.querySelector('[role=dialog]').textContent;
  assert.match(text, /Zones could not be read: Sign in to read from Egeria\./);
  assert.doesNotMatch(text, /returned no governance zones/);
});

test("zones: A-P's {state, message} is shown when the state is not ok", opts, async () => {
  const w = setup([], { ok: true, status: 200, data: { zones: [], state: 'unreachable', message: 'Egeria not reached: timeout.' } });
  w.SpecsPublish.onCatalog({ ...DATA, ...PROVEN }, catalog(w, [['Mine', 'user']]));
  await w.SpecsPublish.openPublish('Mine');
  assert.match(w.document.querySelector('[role=dialog]').textContent, /Egeria not reached: timeout\./);
});

test('a hostile zone name in the picker is text, never markup', opts, async () => {
  const w = setup([], { ok: true, status: 200, data: { zones: [HOSTILE], count: 1 } });
  w.SpecsPublish.onCatalog({ ...DATA, ...PROVEN }, catalog(w, [['Mine', 'user']]));
  await w.SpecsPublish.openPublish('Mine');
  await tick();
  const dlg = w.document.querySelector('[role=dialog]');
  assert.equal(dlg.querySelectorAll('img').length, 0);
  assert.match(dlg.textContent, /onerror/);
  assert.equal(dlg.querySelector('input[type=checkbox][value]').value, HOSTILE);
});

test('"Used by" filters the restricted rows by their own folders', opts, () => {
  const w = setup();
  const el = catalog(w, [['Spec-A', 'builtin']]);
  const data = { ...DATA, restricted: { state: 'ok', specs: [
    { label: 'Fin-EA', zones: ['finance'], folders: [ADVISOR], used_by: [ADVISOR_NAME] },
    { label: 'Fin-Portal', zones: ['finance'], folders: [PORTAL], used_by: [PORTAL_NAME] }] } };
  w.SpecsPublish.onCatalog(data, el);
  const sel = w.document.querySelector('#specs-used-by select');
  sel.value = PORTAL_NAME;
  sel.dispatchEvent(new w.Event('change'));
  const shown = [...w.document.querySelectorAll('[data-restricted-row]')].filter(r => r.style.display !== 'none')
    .map(r => r.querySelector('button').firstChild.textContent);
  assert.deepEqual(shown, ['Fin-Portal']);
});
