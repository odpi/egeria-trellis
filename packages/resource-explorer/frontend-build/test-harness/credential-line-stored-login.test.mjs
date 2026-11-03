/** Behaviour (W40): the page's credential line says when the survey's login is not the stored one.
 *  Three cases: same (unchanged words), a credential typed for that run, a login changed since. Never a password. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { credentialLineText } = await import('/static/next/stages/scope-sources.js');
const CAP = (over = {}) => ({ connected_as: 'surveyor', stored_login: 'surveyor', schema_visible: 6, schema_total: 8, ...over });

test('same login: the sentence is what it was', () => {
  const { text } = credentialLineText(CAP(), '2026-10-24T09:00:00', 61, 3);
  assert.match(text, /connected as surveyor, sees 6 of 8 schemas/);
  assert.doesNotMatch(text, /stored login/);
});

test('a credential typed for that run says so', () => {
  const { text } = credentialLineText(CAP({ connected_as: 'egeria_admin', credential_typed: true }), '2026-10-24T09:00:00', 61, 3);
  assert.match(text, /connected as egeria_admin \(a credential typed for that run; the stored login is surveyor\), sees/);
});

test('a login that differs without a typed marker says it may have changed since', () => {
  const { text } = credentialLineText(CAP({ connected_as: 'egeria_admin' }), '2026-10-24T09:00:00', 61, 3);
  assert.match(text, /connected as egeria_admin \(not the stored login, surveyor; the login may have changed since\), sees/);
});

test('no stored login known: nothing is added', () => {
  const { text } = credentialLineText(CAP({ stored_login: '' }), '', 61, 3);
  assert.doesNotMatch(text, /stored login/);
});
