/** Brief A section 6 (owner, 2026-10-09): three words on screen, used consistently.
 *    component        architecture recovery, published as a SolutionComponent
 *    files and folders  what "What's in it" includes or leaves out
 *    blueprint        a SolutionBlueprint
 *  "sub-resource" never appears on screen (it stays the internal table name `sub_resources`), and "Catalog" is
 *  retired from controls, labels and result lines: it survives only as the name of Egeria's cataloguer.
 *
 *  A source scan of every user-visible line in static/next. The allowlist is closed and each entry says why it is
 *  there; anything else fails, which is how a new "sub-resource" or "Catalog" label gets caught. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_DIR = path.resolve(HERE, '../../resource_explorer/web/static/next');

const SUB = /sub-resources?/i;
const CATALOG = /\bCatalog(s|ed|ing)?\b/;

/** Lines allowed to keep the word, with the reason. Closed list. */
const ALLOW = [
  // A different object: the catalogue of QUESTIONS and the annotation-type registry's catalog bindings.
  { file: /^admin\//, why: 'the Question Catalog and annotation-type catalog bindings are other objects' },
  // Code that merely names Egeria's cataloguer or a module (an identifier, an error message nobody reads).
  { line: /Catalogue|getCatalog|setCatalog|_catalog|QuestionCatalog|AnalysisCatalog/, why: 'identifier or Egeria\'s cataloguer' },
];

function userVisibleLines(dir, rel0 = '') {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    const rel = path.join(rel0, ent.name);
    if (ent.isDirectory()) { if (ent.name !== 'fonts') out.push(...userVisibleLines(full, rel)); continue; }
    if (!/\.(js|html)$/.test(ent.name) || ent.name.startsWith('tailwind')) continue;
    let inBlock = false;
    fs.readFileSync(full, 'utf8').split('\n').forEach((line, i) => {
      const t = line.trim();
      if (inBlock) { if (t.includes('*/')) inBlock = false; return; }
      if (t.startsWith('/*')) { if (!t.includes('*/')) inBlock = true; return; }
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('<!--')) return;
      out.push({ rel, n: i + 1, line, t });
    });
  }
  return out;
}

const offenders = (re) => userVisibleLines(NEXT_DIR).filter(({ rel, line }) => re.test(line)
  && !ALLOW.some((a) => (!a.file || a.file.test(rel)) && (!a.line || a.line.test(line)))).map((o) => `${o.rel}:${o.n}: ${o.t.slice(0, 140)}`);

test('"sub-resource" is not in any user-visible line of static/next', () => {
  assert.deepEqual(offenders(SUB), [], 'say "files and folders" on screen; sub_resources stays the internal table name');
});

test('"Catalog" is not a control, label or result line in static/next (it is Egeria\'s cataloguer, and nothing else)', () => {
  assert.deepEqual(offenders(CATALOG), []);
});

test('known-negative: the scan\'s patterns would flag the words they exist to catch', () => {
  assert.match('<div>Cataloged sub-resources</div>', SUB);
  assert.match('<div>Cataloged sub-resources</div>', CATALOG);
  assert.match('title="Pressing Catalog would create it"', CATALOG);
  assert.doesNotMatch('const kind = "SubResource"; getCatalogueScope()', SUB);
  assert.doesNotMatch('getCatalogueScope(slug)', CATALOG);
});

test('the three words are the ones the Curate rows use', () => {
  const src = fs.readFileSync(path.join(NEXT_DIR, 'stages/curate.js'), 'utf8');
  assert.match(src, /this component only/);
  assert.match(src, /Publish would create it in Egeria/);
  const analysis = fs.readFileSync(path.join(NEXT_DIR, 'stages/analysis.js'), 'utf8');
  assert.match(analysis, /Chosen files and folders/);
});
