/** Parity P1, PI-130: Admin → Egeria Links. List stale links, resolve one, resolve several (preview first). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { autoAnswerConfirm } from './confirm-kit.mjs';

ensureLoaderRegistered();
const tick = () => new Promise((r) => setTimeout(r, 15));

const STALE = [
  { entity_type: 'repo', entity_slug: 'egeria_git', stale_guid: 'aaaaaaaa-1111-2222-3333-444444444444', detected_at: '2026-10-08T01:00:00', detail: '' },
  { entity_type: 'database', entity_slug: 'adventureworks', stale_guid: '', detected_at: '', detail: '' },
];

async function setUp({ stale = STALE, confirmAnswer = true, resolveFail = false, admin = true, configured = true, open = undefined } = {}) {
  const { window, document } = makeDomEnvironment();
  const calls = [];
  let rows = [...stale];
  const confirms = [];
  // The app's in-page confirm (confirm-dialog.js), not a native window.confirm.
  window.confirm = () => { throw new Error('native window.confirm must not be used'); };
  globalThis.confirm = window.confirm;
  autoAnswerConfirm(document, { answer: confirmAnswer, asked: confirms });
  globalThis.fetch = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    const u = String(url);
    calls.push({ url: u, method, body });
    const ok = (json) => ({ ok: true, status: 200, json: async () => json });
    if (u === '/api/egeria/admin-status') return ok({ admin, configured, open });
    if (u === '/api/egeria/linkage/stale') return ok(rows);
    let m;
    if ((m = u.match(/^\/api\/egeria\/linkage\/(\w+)\/([^/]+)\/resolve$/))) {
      if (resolveFail) return { ok: false, status: 502, statusText: 'Bad Gateway', json: async () => ({ detail: 'publish failed' }) };
      rows = rows.filter((r) => !(r.entity_type === m[1] && r.entity_slug === m[2]));
      return ok({ status: 'ok', next_step: `Link cleared for ${m[2]}.` });
    }
    if (u === '/api/egeria/linkage/resolve-all') {
      return ok({ action: body.action, dry_run: body.dry_run, succeeded: body.targets.length, failed: 0, skipped: 0,
        details: body.targets.map((t) => ({ entity_type: t.entity_type, slug: t.slug, result: 'ok', message: body.dry_run ? `would ${body.action}` : body.action })) });
    }
    throw new Error(`_UNSTUBBED_FETCH ${method} ${u}`);
  };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mod = await import(`/static/next/admin/egeria_links.js?t=${Math.random()}`);
  await mod.renderEgeriaLinks(host);
  return { document, host, calls, confirms, mod, window };
}

test('lists each stale link with its GUID, and says "not recorded" where there is none', async () => {
  const { host } = await setUp();
  const rows = host.querySelectorAll('[data-link-row]');
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /repo\/egeria_git/);
  assert.match(rows[0].textContent, /aaaaaaaa-1111/);
  assert.match(rows[1].textContent, /not recorded/);
});

test('no stale links is said as a clean state, not an empty table', async () => {
  const { host } = await setUp({ stale: [] });
  assert.ok(host.querySelector('[data-link-none]'));
  assert.equal(host.querySelector('table'), null);
  assert.equal(host.querySelector('[data-link-toolbar]'), null);
});

test('the three choices are offered per row, and none of the offered controls deletes anything', async () => {
  const { host, mod } = await setUp();
  const labels = [...host.querySelectorAll('[data-link-row]:first-child [data-link-resolve]')].map((b) => b.textContent.trim());
  assert.deepEqual(labels, ['Republish', 'Re-survey', 'Discard link']);
  assert.deepEqual(mod.ACTIONS.map((a) => a.id), ['republish', 'resurvey', 'discard']);
  const everything = host.innerHTML;
  assert.doesNotMatch(everything, /delete-local|delete-in-egeria|Delete in Egeria|Delete locally/);
  assert.equal(host.querySelectorAll('[data-link-bulk-action] option').length, 3);
});

test('resolving one row asks first, names what it does, posts the action, and the row leaves with its sentence kept', async () => {
  const { host, calls, confirms } = await setUp();
  host.querySelector('[data-link-resolve="republish"]').click();
  await tick();
  assert.match(confirms[0], /Writes a new SurveyReport to Egeria/);
  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/egeria/linkage/repo/egeria_git/resolve');
  assert.deepEqual(post.body, { action: 'republish' });
  assert.equal(host.querySelectorAll('[data-link-row]').length, 1);
  assert.match(host.querySelector('[data-link-done]').textContent, /Link cleared for egeria_git/);
});

test('declining the confirmation sends nothing', async () => {
  const { host, calls } = await setUp({ confirmAnswer: false });
  host.querySelector('[data-link-resolve="discard"]').click();
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  assert.equal(host.querySelectorAll('[data-link-row]').length, 2);
});

test('a failed resolution stays on the row with the server sentence and re-arms the button', async () => {
  const { host } = await setUp({ resolveFail: true });
  const btn = host.querySelector('[data-link-resolve="republish"]');
  btn.click();
  await tick();
  assert.equal(host.querySelectorAll('[data-link-row]').length, 2);
  assert.match(host.querySelector('[data-link-state]').textContent, /Not resolved: publish failed/);
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Republish');
});

test('bulk: nothing is armed until rows are ticked; Apply is armed only by a preview of that selection', async () => {
  const { host, calls } = await setUp();
  const preview = host.querySelector('[data-link-preview]');
  const apply = host.querySelector('[data-link-apply]');
  assert.equal(preview.disabled, true);
  assert.equal(apply.disabled, true);
  assert.match(host.querySelector('[data-link-count]').textContent, /0 selected/);
  host.querySelector('[data-link-pick="0"]').checked = true;
  host.querySelector('[data-link-pick="0"]').dispatchEvent(new globalThis.window.Event('change'));
  assert.match(host.querySelector('[data-link-count]').textContent, /1 selected/);
  assert.equal(preview.disabled, false);
  assert.equal(apply.disabled, true, 'ticking alone does not arm Apply');
  preview.click();
  await tick();
  const dry = calls.find((c) => c.url === '/api/egeria/linkage/resolve-all');
  assert.deepEqual(dry.body, { targets: [{ entity_type: 'repo', slug: 'egeria_git' }], action: 'republish', dry_run: true });
  assert.match(host.querySelector('[data-link-bulk-result]').textContent, /nothing was changed/);
  assert.equal(apply.disabled, false);
  // Changing the selection disarms it again.
  host.querySelector('[data-link-pick="1"]').checked = true;
  host.querySelector('[data-link-pick="1"]').dispatchEvent(new globalThis.window.Event('change'));
  assert.equal(host.querySelector('[data-link-apply]').disabled, true);
});

test('bulk apply sends dry_run false for exactly the previewed rows, after a confirmation, and removes the ones that worked', async () => {
  const { host, calls, confirms } = await setUp();
  host.querySelector('[data-link-all]').checked = true;
  host.querySelector('[data-link-all]').dispatchEvent(new globalThis.window.Event('change'));
  host.querySelector('[data-link-bulk-action]').value = 'discard';
  host.querySelector('[data-link-bulk-action]').dispatchEvent(new globalThis.window.Event('change'));
  host.querySelector('[data-link-preview]').click();
  await tick();
  host.querySelector('[data-link-apply]').click();
  await tick();
  assert.match(confirms[0], /Discard link 2 resource\(s\)/);
  const real = calls.filter((c) => c.url === '/api/egeria/linkage/resolve-all').at(-1);
  assert.equal(real.body.dry_run, false);
  assert.equal(real.body.action, 'discard');
  assert.equal(real.body.targets.length, 2);
  assert.equal(host.querySelectorAll('[data-link-row]').length, 0);
  assert.match(host.querySelector('[data-link-bulk-result]').textContent, /2\s*ok/);
});

test('a hostile slug and GUID are escaped', async () => {
  const { host } = await setUp({ stale: [{ entity_type: 'repo', entity_slug: '<img src=x onerror=alert(1)>', stale_guid: '"><b id="pwn">', detected_at: '' }] });
  assert.equal(host.querySelector('img'), null);
  assert.equal(host.querySelector('#pwn'), null);
});

test('the Discard tooltip says it removes RE\'s local publish records and writes nothing to Egeria', async () => {
  const { host, mod } = await setUp();
  const tip = host.querySelector('[data-link-resolve="discard"]').getAttribute('title');
  assert.match(tip, /Remove RE's local publish records/);
  assert.match(tip, /Writes nothing to Egeria/);
  assert.doesNotMatch(tip, /nothing is written.*kept; past publishes/i);
  assert.equal(mod.ACTIONS.find((a) => a.id === 'discard').writes, false);
});

test('a non-admin sees the bulk controls disabled with "admin only", can still resolve one row, and nothing is sent in bulk', async () => {
  const { host, calls } = await setUp({ admin: false });
  assert.ok(host.querySelector('[data-link-toolbar] [data-admin-only]'));
  assert.match(host.querySelector('[data-link-toolbar]').textContent, /admin only/);
  host.querySelector('[data-link-pick="0"]').checked = true;
  host.querySelector('[data-link-pick="0"]').dispatchEvent(new globalThis.window.Event('change'));
  assert.equal(host.querySelector('[data-link-preview]').disabled, true);
  assert.equal(host.querySelector('[data-link-apply]').disabled, true);
  assert.equal(host.querySelector('[data-link-bulk-action]').disabled, true);
  host.querySelector('[data-link-resolve="discard"]').click();
  await tick();
  assert.ok(calls.some((c) => c.url === '/api/egeria/linkage/repo/egeria_git/resolve'), 'single resolve stays open to curators');
  assert.equal(calls.filter((c) => c.url.endsWith('resolve-all')).length, 0);
});

test('the bulk calls carry the admin token held for this tab', async () => {
  const { window, host } = await setUp();
  window.sessionStorage.setItem('re_admin_token', 'tok-123');
  const seen = [];
  const inner = globalThis.fetch;
  globalThis.fetch = async (u, o = {}) => { if (String(u).endsWith('resolve-all')) seen.push(o.headers); return inner(u, o); };
  host.querySelector('[data-link-pick="0"]').checked = true;
  host.querySelector('[data-link-pick="0"]').dispatchEvent(new globalThis.window.Event('change'));
  host.querySelector('[data-link-preview]').click();
  await tick();
  assert.equal(seen[0]['X-Admin-Token'], 'tok-123');
});

test('no admin configured (owner ruling 2026-10-14): the bulk toolbar reads admin only', async () => {
  const { host } = await setUp({ admin: false, configured: false, open: false });
  assert.notEqual(host.querySelector('[data-link-toolbar] [data-admin-only]'), null);
});

test('no admin configured, local-dev flag on: the bulk toolbar is live for a signed-in non-admin', async () => {
  const { host } = await setUp({ admin: false, configured: false, open: true });
  assert.equal(host.querySelector('[data-link-toolbar] [data-admin-only]'), null);
  host.querySelector('[data-link-pick="0"]').checked = true;
  host.querySelector('[data-link-pick="0"]').dispatchEvent(new globalThis.window.Event('change'));
  assert.equal(host.querySelector('[data-link-preview]').disabled, false);
  assert.equal(host.querySelector('[data-link-bulk-action]').disabled, false);
});
