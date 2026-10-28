/** The identifier input for a second blueprint of one kind (architect's ruling 2026-10-08, §2a): shown only
 *  when a blueprint of that kind already exists for the repository; a short word shows its state. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function curate() {
  ensureLoaderRegistered();
  const { document } = makeDomEnvironment();
  const mod = await import(`/static/next/stages/curate.js?t=${Math.random()}`);
  return { mod, document };
}

const IDENTITY = { needs_identifier: true, kind_word: 'Deployment',
  sentence: 'a Deployment Blueprint already exists for egeria_git · give this one an identifier' };

test('no input for the first blueprint of a kind', async () => {
  const { mod, document } = await curate();
  for (const id of [undefined, null, { needs_identifier: false }]) {
    const box = document.createElement('div');
    box.innerHTML = mod.identifierBoxHtml(id);
    assert.equal(box.querySelector('[data-identifier-input]'), null);
    assert.equal(mod.wireIdentifier(box, id), null);
  }
});

test('the input shows a short word, the sentence only on demand (title)', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.identifierBoxHtml(IDENTITY);
  assert.equal(box.querySelector('[data-identifier-word]').textContent, 'needed');
  assert.equal(box.querySelector('[data-identifier-box]').getAttribute('title'), IDENTITY.sentence);
  assert.doesNotMatch(box.textContent, /already exists/);
});

test('an empty or invalid identifier is not sent and says why in one word; a valid one is trimmed', async () => {
  const { mod, document } = await curate();
  const box = document.createElement('div');
  box.innerHTML = mod.identifierBoxHtml(IDENTITY);
  const wired = mod.wireIdentifier(box, IDENTITY);
  const input = box.querySelector('[data-identifier-input]');
  const word = box.querySelector('[data-identifier-word]');
  assert.equal(wired.check(), false);
  assert.equal(word.textContent, 'needed');
  assert.equal(input.getAttribute('aria-invalid'), 'true');
  input.value = 'a::b';
  assert.equal(wired.check(), false);
  assert.match(word.textContent, /::/);
  input.value = '  Servers ';
  input.dispatchEvent(new input.ownerDocument.defaultView.Event('input'));
  assert.equal(word.textContent, 'ready');
  assert.equal(wired.check(), true);
  assert.equal(input.getAttribute('aria-invalid'), 'false');
  assert.equal(wired.value(), 'Servers');
});

test('identifierProblem mirrors the server rule', async () => {
  const { mod } = await curate();
  assert.equal(mod.identifierProblem('Runtimes'), '');
  assert.equal(mod.identifierProblem('x'.repeat(65)), 'not valid');
  assert.equal(mod.identifierProblem('-lead'), 'not valid');
  assert.equal(mod.identifierProblem('  '), 'needed');
});
