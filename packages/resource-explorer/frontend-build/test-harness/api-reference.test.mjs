/** API reference pane + registration control: real jsdom renders of stages/api-reference.js.
 *
 *  The fixture (api-reference.fixture.json) is the REAL server payload: `service.build_reference` over the live
 *  TestGen OpenAPI document (26 operations, 73 schemas) plus five code routes, so the contract between the
 *  Python reader and this drawing is exercised, not re-typed. Regenerate with the script named in the report.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const REF = JSON.parse(readFileSync(new URL('./api-reference.fixture.json', import.meta.url), 'utf8'));
const URL_ = 'http://localhost:8530/api/openapi.json';
const clone = (o) => JSON.parse(JSON.stringify(o));
const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));

async function setUp() {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  globalThis.location = window.location;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const grid = document.createElement('div');
  grid.id = 'app-grid';
  document.body.appendChild(grid);
  const app = await import('/static/next/app.js');
  const mod = await import(`/static/next/stages/api-reference.js?t=${Date.now()}_${Math.random()}`);
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'dataops_testgen';
  return { document, app, mod };
}

function mount(document, html) {
  const host = document.createElement('div');
  host.innerHTML = html;
  document.body.appendChild(host);
  return host;
}

test('the source is labelled with where it came from, in the words the owner asked for', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.referenceHtml(REF, {}));
  const label = host.querySelector('[data-api-label]').textContent;
  assert.match(label, /from the running instance at http:\/\/localhost:8530\/api\/openapi\.json, fetched /);
  assert.ok(host.querySelector('[data-api-cue="running_instance"]'), 'the cue sits on the element');
  assert.match(host.querySelector('[data-api-cue="running_instance"]').textContent, /running instance/);
});

test('operations are grouped by tag with a method badge and the count says 26', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.referenceHtml(REF, {}));
  assert.match(host.querySelector('[data-api-interface]').textContent, /26 operations/);
  const groups = [...host.querySelectorAll('[data-api-group]')].map((g) => g.dataset.apiGroup);
  assert.ok(groups.includes('OAuth') && groups.length > 2, groups.join(','));
  const op = host.querySelector('details[data-api-op="GET /oauth/authorize"]');
  assert.equal(op.querySelector('[data-method]').dataset.method, 'GET');
  assert.match(op.querySelector('summary').textContent, /\/oauth\/authorize/);
});

test('an operation opens lazily to parameters, request body and responses; a schema opens one level in place', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.referenceHtml(REF, {}));
  mod.wireReference(host, REF, { pick: 0 }, {});
  const op = host.querySelector('details[data-api-op="GET /oauth/authorize"]');
  assert.equal(op.querySelector('[data-api-op-body]').innerHTML, '', 'nothing is drawn until it is opened');
  op.open = true;
  op.dispatchEvent(new document.defaultView.Event('toggle'));
  const body = op.querySelector('[data-api-op-body]');
  assert.ok(body.querySelector('[data-op-param="response_type"]'));
  assert.match(body.querySelector('[data-op-param="response_type"]').textContent, /query.*required/s);
  assert.ok(body.querySelector('[data-op-response="422"]'));
  const open = body.querySelector('[data-schema-open="HTTPValidationError"]');
  assert.ok(open, 'a response schema defined in the document is a control');
  open.click();
  assert.ok(body.querySelector('[data-schema-tree="HTTPValidationError"] [data-schema-prop="detail"]'));
  open.click();
  assert.equal(body.querySelector('[data-schema-tree="HTTPValidationError"]'), null, 'pressing again closes it');
});

test('a request-body operation shows content type and its schema', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.referenceHtml(REF, {}));
  mod.wireReference(host, REF, { pick: 0 }, {});
  const op = host.querySelector('details[data-api-op="POST /oauth/authorize"]');
  op.open = true;
  op.dispatchEvent(new document.defaultView.Event('toggle'));
  assert.match(op.querySelector('[data-op-body]').textContent, /application\/x-www-form-urlencoded/);
});

test('the comparison is shown, with both counts and the differences, and no stated cause', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.referenceHtml(REF, {}));
  const cmp = host.querySelector('[data-api-compare="derived_from_code"]');
  assert.ok(cmp);
  const head = cmp.querySelector('summary').textContent;
  assert.match(head, /differs from derived from code/);
  assert.match(head, /26 vs 5/);
  assert.match(cmp.textContent, /Only in running instance/);
  assert.match(cmp.textContent, /GET \/debug\/state/);
  assert.match(cmp.querySelector('[data-api-compare-summary]').textContent, /not decided here/);
  assert.doesNotMatch(cmp.textContent, /because/i);
});

test('the code-derived list says it is not a contract and what it does not state', async () => {
  const { document, mod } = await setUp();
  const only = clone(REF);
  only.interfaces = [only.interfaces[1]];
  only.comparisons = [];
  only.best = 'derived_from_code';
  const host = mount(document, mod.referenceHtml(only, {}));
  assert.match(host.querySelector('[data-api-label]').textContent, /derived from code, not a published contract/);
  assert.ok(host.querySelector('[data-api-cue="derived_from_code"]'));
  const op = host.querySelector('details[data-api-op="GET /api/v1/jobs/{job_id}"]');
  mod.wireReference(host, only, { pick: 0 }, {});
  op.open = true;
  op.dispatchEvent(new document.defaultView.Event('toggle'));
  const code = op.querySelector('[data-op-code]').textContent;
  assert.match(code, /get_job/);
  assert.match(code, /testgen\/api\/jobs\.py:20/);
  assert.match(code, /not derived/);
});

test('a committed spec chip names the file; sources can be switched', async () => {
  const { document, mod } = await setUp();
  const two = clone(REF);
  const c = clone(two.interfaces[0]);
  c.source = 'committed_spec'; c.origin = 'docs/openapi.yaml'; c.source_phrase = 'committed spec file docs/openapi.yaml, read at the survey of 2026-10-14T10:00:00';
  two.interfaces.unshift(c);
  let drawn = 0;
  const ui = { pick: 0 };
  const host = mount(document, mod.referenceHtml(two, ui));
  assert.match(host.querySelector('[data-api-label]').textContent, /committed spec file docs\/openapi\.yaml/);
  mod.wireReference(host, two, ui, { rerender: () => { drawn += 1; } });
  host.querySelector('[data-api-pick="1"]').click();
  assert.equal(ui.pick, 1);
  assert.equal(drawn, 1);
  const again = mount(document, mod.referenceHtml(two, ui));
  assert.match(again.querySelector('[data-api-label]').textContent, /running instance/);
});

test('nothing known: each missing source is a cue with its reason on demand, never a blank', async () => {
  const { document, mod } = await setUp();
  const empty = { slug: 'x', interfaces: [], best: '', comparisons: [], problems: [], instance: null,
    missing: [{ source: 'committed_spec', reason: 'no committed OpenAPI/Swagger file recorded' },
      { source: 'running_instance', reason: 'no running-instance URL is registered' },
      { source: 'derived_from_code', reason: 'not measured: no code markers were captured' }] };
  const host = mount(document, mod.referenceHtml(empty, {}));
  assert.ok(host.querySelector('[data-api-empty]'));
  assert.equal(host.querySelectorAll('[data-api-missing]').length, 3);
  assert.match(host.querySelector('[data-api-missing="running_instance"]').textContent, /no running-instance URL is registered/);
});

test('a failed re-check keeps the copy and says so on the pane', async () => {
  const { document, mod } = await setUp();
  const failed = clone(REF);
  failed.instance = { ...failed.instance, status: 'failed', error: 'the server answered 503' };
  const host = mount(document, mod.referenceHtml(failed, {}));
  assert.match(host.querySelector('[data-api-instance-failed]').textContent, /last check of .* failed .* 503 .* showing the copy fetched/);
});

test('kind-agnostic: an MCP-shaped interface draws through the same list (name, group, input schema)', async () => {
  const { document, mod } = await setUp();
  const mcp = { slug: 'x', best: 'running_instance', comparisons: [], missing: [], problems: [], instance: null,
    interfaces: [{ kind: 'mcp', source: 'running_instance', origin: 'http://h/mcp', noun: 'tools', count: 1, total_operations: 1,
      source_phrase: 'from the running instance at http://h/mcp, fetched now', source_word: 'running instance',
      groups: {}, schemas: {}, security_schemes: {}, notes: [], base_paths: [], title: 'TestGen MCP', version: '1', spec_version: '',
      operations: [{ key: 'tool:run_tests', name: 'run_tests', kind: 'mcp', kind_detail: 'tool', method: '', path: '', summary: 'Run a suite',
        description: '', operation_id: '', tags: ['tests'], group: 'tests', parameters: [], request_body: null, responses: [], security: [], deprecated: false,
        input_schema: { type: 'object', required: ['suite'], properties: { suite: { type: 'string', description: 'which suite' } } },
        handler: '', handler_signature: '', location: '' }] }] };
  const host = mount(document, mod.referenceHtml(mcp, {}));
  assert.match(host.querySelector('[data-api-interface]').textContent, /1 tools/);
  mod.wireReference(host, mcp, { pick: 0 }, {});
  const op = host.querySelector('details[data-api-op="tool:run_tests"]');
  assert.match(op.querySelector('summary').textContent, /tool.*run_tests/s);
  op.open = true;
  op.dispatchEvent(new document.defaultView.Event('toggle'));
  assert.match(op.querySelector('[data-api-op-body]').textContent, /suite.*string.*required/s);
});

// ── the registration control ────────────────────────────────────────────

test('the control shows the three sources as cues and the fetched state with a short word', async () => {
  const { document, mod } = await setUp();
  const host = mount(document, mod.instanceControlHtml(REF));
  const line = host.querySelector('[data-api-sources-line]').textContent;
  assert.match(line, /running instance 26/);
  assert.match(line, /from code 5/);
  assert.match(line, /no committed spec/);
  assert.equal(host.querySelector('#api-instance-url').value, URL_);
  assert.match(host.querySelector('[data-api-instance-state="ok"]').textContent, /fetched/);
});

test('a rejected URL shows the reason beside the field and keeps what was typed', async () => {
  const { document, app, mod } = await setUp();
  const host = document.createElement('div');
  host.id = 'api-reference-block';
  document.body.appendChild(host);
  const calls = [];
  const blank = { ...clone(REF), instance: null, interfaces: [], missing: REF.missing, comparisons: [] };
  globalThis.fetch = async (url, opts) => {
    calls.push(`${opts?.method || 'GET'} ${url}`);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (String(url).endsWith('/instance') && opts?.method === 'PUT') {
      return ok({ ok: false, reason: 'not an OpenAPI document: got text/html', url: 'http://localhost:8501/x' });
    }
    return ok(blank);
  };
  await mod.renderApiReferenceBlock('dataops_testgen');
  host.querySelector('#api-instance-url').value = 'http://localhost:8501/x';
  host.querySelector('#api-instance-save').click();
  await wait(60);
  assert.match(host.querySelector('[data-api-instance-state="rejected"]').textContent, /not an OpenAPI document: got text\/html/);
  assert.equal(host.querySelector('#api-instance-url').value, 'http://localhost:8501/x');
  assert.ok(calls.some((c) => c.startsWith('PUT ') && c.endsWith('/instance')));
  assert.equal(calls.filter((c) => c.startsWith('GET ')).length, 1, 'a rejection does not reload as if something was kept');
  void app;
});

test('an accepted URL reloads the block and the state reads fetched', async () => {
  const { document, mod } = await setUp();
  const host = document.createElement('div');
  host.id = 'api-reference-block';
  document.body.appendChild(host);
  let registered = false;
  const blank = { ...clone(REF), instance: null, interfaces: [], missing: REF.missing, comparisons: [] };
  globalThis.fetch = async (url, opts) => {
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (opts?.method === 'PUT') { registered = true; return ok({ ok: true, reason: '', url: URL_, operations: 26 }); }
    return ok(registered ? REF : blank);
  };
  await mod.renderApiReferenceBlock('dataops_testgen');
  host.querySelector('#api-instance-url').value = URL_;
  host.querySelector('#api-instance-save').click();
  await wait(60);
  assert.match(host.querySelector('[data-api-instance-state="ok"]').textContent, /fetched/);
  assert.ok(host.querySelector('#api-reference-open'));
});

test('Context mounts the block for a repository and the members drill-down offers the reference', async () => {
  const { document, app, mod } = await setUp();
  void mod;
  const ctx = await import('/static/next/stages/context.js');
  app.state.investigation = ''; app.state.investigations = []; app.state.questions = [];
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  content.innerHTML = '<div id="context-form"></div>';
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.body.appendChild(rail);
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    if (u.includes('/api/api-reference/')) return ok(REF);
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/members')) return ok({ analysis_id: 'interface_surface', title: 'endpoints', total: 26, source: 'api_reference:running_instance', scope_honoured: false, groups: [{ name: 'OAuth', count: 1, members: [{ name: 'GET /oauth/authorize' }] }] });
    return ok({ subjects: {} });
  };
  await ctx.renderContext('dataops_testgen');
  await wait(80);
  assert.match(content.textContent, /API reference/);
  assert.ok(document.querySelector('#api-reference-block #api-instance-url'));
  await app.openMembers({ slug: 'dataops_testgen', analysisId: 'interface_surface', title: 'interface surface' });
  const btn = document.querySelector('[data-act="api-reference"]');
  assert.ok(btn, 'the drill-down carries a way into the reference');
  btn.click();
  await wait(80);
  assert.ok(document.querySelector('#rail-evidence [data-api-interface]'));
  assert.match(document.querySelector('#rail-evidence').textContent, /API reference/);
});

test('a fragment of a split spec is information, not a warning; an unreadable file is a warning', async () => {
  const { document, mod } = await setUp();
  const r = clone(REF);
  r.problems = [{ path: 'openapi/paths.yaml', kind: 'fragment', reason: 'fragment, not a root document' },
    { path: 'docs/swagger.json', kind: 'unreadable', reason: 'not an OpenAPI document: no \'paths\' object' }];
  const host = mount(document, mod.referenceHtml(r, {}));
  assert.match(host.querySelector('[data-api-fragment]').textContent, /fragment, not a root document/);
  assert.equal(host.querySelectorAll('[data-api-problem]').length, 1);
});

test('a forgotten registration says who forgot it', async () => {
  const { document, mod } = await setUp();
  const r = clone(REF);
  r.instance = null;
  r.cleared = { cleared_by: 'dan', cleared_at: new Date().toISOString(), url: URL_ };
  const host = mount(document, mod.instanceControlHtml(r));
  assert.match(host.querySelector('[data-api-cleared]').textContent, /forgotten by dan/);
});

test('Admin shows the effective fetch list read-only, with the never-allowed ranges', async () => {
  const { document } = await setUp();
  const { policyHtml } = await import('/static/next/admin/api_fetch.js');
  const host = mount(document, policyHtml({ setting: 'RE_API_FETCH_ALLOW', source: 'default',
    allow: ['localhost', '127.0.0.1', '::1'], always_deny: ['169.254.0.0/16', 'fe80::/10'], note: 'An entry without a port admits any port.' }));
  assert.match(host.querySelector('[data-policy-allow]').textContent, /localhost.*127\.0\.0\.1.*::1/);
  assert.match(host.querySelector('[data-policy-deny]').textContent, /169\.254\.0\.0\/16/);
  assert.match(host.querySelector('[data-policy-source]').textContent, /default/);
  assert.equal(host.querySelectorAll('input, button').length, 0, 'read-only: nothing to press or type in');
  const empty = mount(document, policyHtml({ setting: 'X', source: 'environment', allow: [], always_deny: [] }));
  assert.match(empty.querySelector('[data-policy-allow]').textContent, /nothing is allowed/);
});
