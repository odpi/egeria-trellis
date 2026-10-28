/** Behaviour: the Egeria-visible host edit on a server and on a database.
 *  Only fetch is stubbed. A save changes the line itself (value + mark); a server offers its databases that
 *  still carry the old value and changes them only on a second, explicit press. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

function backend(replies) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method: (options.method || 'GET').toUpperCase(), url: String(url), body });
    const r = replies.shift();
    return r.status >= 400
      ? { ok: false, status: r.status, statusText: r.detail, json: async () => ({ detail: r.detail }) }
      : { ok: true, status: 200, json: async () => r.body };
  };
  return calls;
}

async function mount(kind, slug, value, replies) {
  const { document } = makeDomEnvironment();
  ensureLoaderRegistered();
  const calls = backend(replies);
  const mod = await import('/static/next/egeria-host-edit.js');
  const host = document.createElement('div');
  host.innerHTML = mod.egeriaHostLineHtml(kind, slug, value);
  document.body.appendChild(host);
  const carried = [];
  mod.bindEgeriaHostEdit(host.firstElementChild, kind, slug, { onCarried: (s, v) => carried.push([s, v]) });
  return { host, calls, carried };
}

const open = async (host, value) => {
  host.querySelector('[data-eh-change]').click();
  const input = host.querySelector('[data-eh-input]');
  input.value = value;
  host.querySelector('[data-eh-save]').click();
  await tick();
};

test('a database save sends one PATCH with that field only and the line shows the new value and a mark', async () => {
  const { host, calls } = await mount('database', 'd1', 'localhost', [
    { body: { egeria_host: 'host.docker.internal', previous: 'localhost', in_egeria: true, note: 'The elements Egeria holds keep the old address until the database is registered again. Deleting the old elements is the owner\'s job.' } }]);
  await open(host, 'host.docker.internal');
  assert.deepEqual(calls.map((c) => [c.method, c.url, c.body]),
    [['PATCH', '/api/databases/d1/egeria-host', { egeria_host: 'host.docker.internal' }]]);
  assert.equal(host.querySelector('[data-eh-value]').textContent, 'host.docker.internal');
  assert.match(host.querySelector('[data-eh-mark]').textContent, /saved/);
  assert.match(host.querySelector('[data-eh-note]').textContent, /keep the old address/);
});

test('a refused save keeps the old value, says why, and the button comes back', async () => {
  const { host } = await mount('database', 'd1', 'localhost', [{ status: 403, detail: 'not permitted on this resource' }]);
  await open(host, 'x');
  assert.equal(host.querySelector('[data-eh-value]').textContent, 'localhost');
  assert.match(host.querySelector('[data-eh-error]').textContent, /not saved · not permitted/);
  assert.equal(host.querySelector('[data-eh-save]').disabled, false);
});

test('a server offers its databases with the old value, and carries only on the second press', async () => {
  const { host, calls, carried } = await mount('server', 'srv', 'localhost', [
    { body: { egeria_host: 'host.docker.internal', previous: 'localhost', carriable: [{ slug: 'a', in_egeria: false }], carried: [], note: '' } },
    { body: { egeria_host: 'host.docker.internal', previous: 'localhost', carriable: [], carried: [{ slug: 'a', in_egeria: false }], note: '' } }]);
  await open(host, 'host.docker.internal');
  assert.equal(calls.length, 1);
  assert.match(host.querySelector('[data-eh-carry]').textContent, /1 database on this server still has localhost/);
  host.querySelector('[data-eh-carry-go]').click();
  await tick();
  assert.deepEqual(calls[1].body, { egeria_host: 'host.docker.internal', carry_databases_from: 'localhost' });
  assert.deepEqual(carried, [[['a'], 'host.docker.internal']]);
  assert.match(host.querySelector('[data-eh-carry-go]').textContent, /carried to 1/);
});
