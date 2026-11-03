/** G9 (tutorial outline §7, 2026-10-29): the header names who is signed in whether or not an investigation is chosen.
 *
 *  The 2026-10-28 audit saw "not signed in" with no investigation chosen and the user id with one. That was before
 *  d49fdea2 (the header reads only what /api/auth/me confirmed); re-checked live on 8813 at 10e8e208 signed in with no
 *  investigation, the header and the Connection popover both name the user. These pin it: the investigation never
 *  decides the sign-in word, and "not signed in" is said only when /api/auth/me said so. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_DIR = path.resolve(HERE, '../../resource_explorer/web/static/next');
const INDEX_HTML = fs.readFileSync(path.join(NEXT_DIR, 'index.html'), 'utf8');
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const SIGNED_IN = { authenticated: true, user_id: 'peterprofile', egeria_user: 'peterprofile', role: 'user' };
const INVESTIGATIONS = [{ slug: 'docling-family', display_name: 'Docling Family', status: 'open', egeria_binding: 'local' }];

async function setUp({ me, investigation }) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const app = await import('/static/next/app.js');
  const s = app.state;
  s.resourceType = 'db';
  s.selectedSlug = 'laz_local_adventureworks';
  s.investigations = INVESTIGATIONS.map((i) => ({ ...i }));
  s.investigation = investigation;
  s.me = me;
  app.renderTopBar();
  return { document, app, s };
}

const whoami = (d) => text(d.getElementById('whoami'));
const investigationName = (d) => text(d.getElementById('investigation-name'));

test('signed in, no investigation chosen: the header names the user, never "not signed in"', async () => {
  const { document } = await setUp({ me: SIGNED_IN, investigation: '' });
  assert.equal(investigationName(document), 'No investigation');
  assert.equal(whoami(document), 'peterprofile');
});

test('signed in, an investigation chosen: the header names the user and the investigation', async () => {
  const { document } = await setUp({ me: SIGNED_IN, investigation: 'docling-family' });
  assert.equal(investigationName(document), 'Docling Family');
  assert.equal(whoami(document), 'peterprofile');
});

test('clearing the investigation mid-session leaves the user named', async () => {
  const { document, app, s } = await setUp({ me: SIGNED_IN, investigation: 'docling-family' });
  s.investigation = '';
  app.renderTopBar();
  assert.equal(investigationName(document), 'No investigation');
  assert.equal(whoami(document), 'peterprofile');
});

test('signed out: "not signed in" only because /api/auth/me said authenticated: false', async () => {
  const { document } = await setUp({ me: { authenticated: false }, investigation: '' });
  assert.equal(whoami(document), 'not signed in');
});

test('/api/auth/me not yet answered: "checking sign-in…", not a confident "not signed in"', async () => {
  const { document } = await setUp({ me: null, investigation: '' });
  assert.equal(whoami(document), 'checking sign-in…');
});
