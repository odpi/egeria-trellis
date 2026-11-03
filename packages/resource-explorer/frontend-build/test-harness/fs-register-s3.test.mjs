/** Behavior: the register form's S3 choice (S3 reach Part A, 2026-11-02).
 *
 *  Real fs-register.js, app.js, worklist.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - choosing "An S3 bucket" shows the S3 fields and the credential source, and hides the mount fields;
 *  - "Check reach" posts the fields once and shows the outcome sentence; a changed field is "not checked yet" again;
 *  - with no secrets store, choosing a key says so BEFORE the press and holds Register and Check reach;
 *  - Register posts the S3 payload (the secret only for a key source) to the existing route.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const SENTENCE = 'reachable by Resource Explorer over S3 from laz as public bucket · Egeria has no reach to this bucket · checked 11-02 17:10';

function backend({ store = true } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    const method = (options.method || 'GET').toUpperCase();
    calls.push({ method, url: u, body: options.body ? JSON.parse(options.body) : null });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (/\/reach-mechanisms$/.test(u)) return ok({ mechanisms: [{ name: 's3' }], skipped: {}, secrets_store_configured: store });
    if (/\/check-reach$/.test(u)) return ok({ reachable: true, sentence: SENTENCE });
    if (/\/api\/filesystems\/?$/.test(u) && method === 'GET') return ok([]);
    if (/\/groups/.test(u)) return ok([]);
    return ok({});
  };
  return calls;
}

async function open(opts) {
  const { document, window } = makeDomEnvironment();
  const calls = backend(opts);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const mod = await import('/static/next/fs-register.js');
  const el = await mod.openRegisterFileSystemDialog();
  const body = el.querySelector('#wl-detail-body');
  return { document, window, body, calls, mod };
}

function type(window, body, key, value) {
  const inp = body.querySelector(`[data-fsr="${key}"]`);
  inp.value = value;
  inp.dispatchEvent(new window.Event('input', { bubbles: true }));
}
function choose(window, body, key, value) {
  const sel = body.querySelector(`[data-fsr="${key}"]`);
  sel.value = value;
  sel.dispatchEvent(new window.Event('change', { bubbles: true }));
}

test('choosing S3 shows its fields and Check reach shows the sentence', async () => {
  const { window, body, calls } = await open();
  assert.ok(body.querySelector('[data-fsr="local_mount_point"]'), 'a local mount by default');
  choose(window, body, 'protocol', 's3');
  assert.equal(body.querySelector('[data-fsr="local_mount_point"]'), null);
  for (const key of ['endpoint_url', 'region', 'bucket', 'root_prefix', 'path_style', 'tls_verify', 'credential_kind']) {
    assert.ok(body.querySelector(`[data-fsr="${key}"]`), key);
  }
  assert.equal(body.querySelector('[data-fsr="tls_verify"]').checked, true, 'TLS verify defaults on');
  assert.match(text(body.querySelector('[data-fs-reach]')), /not checked yet/);
  const check = body.querySelector('[data-act="fsr-check-reach"]');
  assert.equal(check.disabled, true, 'no bucket, no check');
  type(window, body, 'bucket', 'datalake');
  assert.equal(body.querySelector('[data-act="fsr-check-reach"]').disabled, false);
  body.querySelector('[data-act="fsr-check-reach"]').click();
  await tick();
  const posted = calls.filter((c) => /check-reach$/.test(c.url));
  assert.equal(posted.length, 1);
  assert.equal(posted[0].body.bucket, 'datalake');
  assert.equal(posted[0].body.credential_kind, 'none');
  assert.equal(posted[0].body.secret_access_key, '');
  const line = body.querySelector('[data-fs-reach]');
  assert.equal(line.dataset.fsReach, 'reachable');
  assert.match(text(line), /over S3 from laz as public bucket/);
  type(window, body, 'root_prefix', 'demo/');
  assert.match(text(body.querySelector('[data-fs-reach]')), /not checked yet/, 'a changed field is not the checked one');
});

test('with no secrets store a key source is refused before the press', async () => {
  const { window, body } = await open({ store: false });
  choose(window, body, 'protocol', 's3');
  type(window, body, 'slug', 'dl');
  type(window, body, 'display_name', 'DL');
  type(window, body, 'bucket', 'datalake');
  choose(window, body, 'credential_kind', 'secret_ref');
  assert.match(text(body.querySelector('[data-fs-no-store]')), /no secrets store is configured on this server/);
  assert.equal(body.querySelector('[data-fsr="secret_access_key"]'), null, 'no field to type a key into');
  assert.equal(body.querySelector('[data-act="fsr-register"]').disabled, true);
  assert.equal(body.querySelector('[data-act="fsr-check-reach"]').disabled, true);
  choose(window, body, 'credential_kind', 'none');
  assert.equal(body.querySelector('[data-act="fsr-register"]').disabled, false);
});

test('Register posts the S3 payload with the key only for a key source', async () => {
  const { window, body, calls } = await open();
  choose(window, body, 'protocol', 's3');
  type(window, body, 'slug', 'dl');
  type(window, body, 'display_name', 'DL');
  type(window, body, 'bucket', 'datalake');
  choose(window, body, 'credential_kind', 'secret_ref');
  assert.match(text(body), /held in the secrets store by reference/);
  assert.equal(body.querySelector('[data-fsr="secret_access_key"]').type, 'password');
  assert.equal(body.querySelector('[data-act="fsr-register"]').disabled, true, 'a key source needs the key');
  type(window, body, 'access_key_id', 'AKIAEXAMPLE');
  type(window, body, 'secret_access_key', 'typed-secret');
  assert.equal(body.querySelector('[data-act="fsr-register"]').disabled, false);
  body.querySelector('[data-act="fsr-register"]').click();
  await tick();
  const reg = calls.filter((c) => c.method === 'POST' && /\/api\/filesystems\/?$/.test(c.url));
  assert.equal(reg.length, 1);
  assert.deepEqual(
    { protocol: reg[0].body.protocol, bucket: reg[0].body.bucket, kind: reg[0].body.credential_kind,
      key: reg[0].body.access_key_id, secret: reg[0].body.secret_access_key, mount: reg[0].body.local_mount_point },
    { protocol: 's3', bucket: 'datalake', kind: 'secret_ref', key: 'AKIAEXAMPLE', secret: 'typed-secret', mount: undefined });
});
