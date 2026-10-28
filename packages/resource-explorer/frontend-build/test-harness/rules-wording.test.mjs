/** Brief section 6 (owner, 2026-10-07): "there are many kinds of rules". Every "rule" on the Curate
 *  screen carries its kind. The label strings are asserted, not inferred.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

async function bands() {
  ensureLoaderRegistered();
  makeDomEnvironment();
  return import(`/static/next/stages/curate-bands.js?t=${Math.random()}`);
}

test('the block is labelled as DATA-CLASS rules', async () => {
  const b = await bands();
  const html = b.rulesBlockShellHtml();
  assert.match(html, />Data-class rules Egeria applies</);
  assert.doesNotMatch(html, />Rules Egeria applies</);
  assert.match(html, /Reading the data-class rules…/);
});

test('the built-in list disclosure names its kind', async () => {
  const b = await bands();
  const html = b.rulesBodyHtml([{ name: 'Password', source: 'Local Fallback', keywords: ['pw'] }]);
  assert.match(html, /RE's built-in data-class keyword list, not read from Egeria \(1\)/);
  assert.doesNotMatch(html, /RE's built-in keyword list/);
  // the no-reader sentence counts data-class rules too
  assert.match(html, /\(1 data-class rules\)/);
});

test('no screen string in the Next scripts says a bare "rule" (every one carries its kind)', () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../resource_explorer/web/static/next');
  const files = [];
  const walk = (d) => { for (const e of readdirSync(d, { withFileTypes: true })) {
    if (e.isDirectory()) { if (e.name !== 'fonts') walk(path.join(d, e.name)); } else if (e.name.endsWith('.js')) files.push(path.join(d, e.name)); } };
  walk(root);
  const offenders = [];
  // a rule that is not preceded by one of its kinds (or a code-ish context), outside comments
  const allowed = /(data-class|scope|retention|built-in|inclusion) rules?|rule \d|rule-strong|border-rule|bg-rule|text-rule|rule\)|\brules\b\s*[=:(\[]|const rules|, rules|\(rules|rules\)|'rule'|\.rule|rule:|rulesBodyHtml|rules\.|rules\b\s*$/i;
  for (const f of files) {
    readFileSync(f, 'utf8').split('\n').forEach((line, i) => {
      const t = line.trim();
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return;
      if (!/(?<![-\w$.])rules?(?![-\w])/i.test(line)) return;
      if (allowed.test(line)) return;
      offenders.push(`${path.relative(root, f)}:${i + 1}: ${t.slice(0, 120)}`);
    });
  }
  assert.deepEqual(offenders, [], `a bare "rule" on screen:\n${offenders.join('\n')}`);
});
