// Chat citations and the Locator screen (Files follow-up 1): the chat source line and the compile panel list the
// narrative passages an answer could cite ("proposal"), a dropped section says so in words, and the Locator's results
// show the best passage with its citation, the proposal label, and each absence in its own words.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const next = join(here, '../../resource_explorer/web/static/next');
const chat = readFileSync(join(next, 'chat.js'), 'utf8');
const cut = (a, b) => chat.slice(chat.indexOf(a), chat.indexOf(b));
const chatSrc = 'const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");\n'
  + cut('export function narrativeSections(', '/** Mermaid source fenced') + '\n'
  + cut('export function narrativesHtml(', 'function evidenceStateHtml(');
const { narrativeSections, narrativeWords, narrativesClause, narrativesHtml } =
  await import('data:text/javascript,' + encodeURIComponent(chatSrc));

const finder = readFileSync(join(next, 'fs-passage-finder.js'), 'utf8');
const finderSrc = 'const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");\n'
  + finder.slice(finder.indexOf('const slugList'), finder.indexOf('export function openFindByPassagesDialog'));
const { passageResultsHtml } = await import('data:text/javascript,' + encodeURIComponent(finderSrc));

const passage = { passage_id: 'w1|survey/README.md|Variables', citation: 'stated by survey/README.md § Variables', status: 'proposal' };
const found = (packed) => ({ narratives: { state: 'found', packed, passages: [passage] } });

test('the source line says which passages the answer could cite', () => {
  assert.equal(narrativesClause(found(true)), ' · 1 narrative passage it could cite, each a proposal');
});

test('a section dropped for the budget says so in words', () => {
  const w = narrativesClause(found(false));
  assert.match(w, /matched but was left out to fit the budget, so the answer cannot cite it/);
});

test('each absence has its own words, none says there are no narratives', () => {
  for (const [state, re] of [['not_read', /not read yet/], ['no_match', /read, no passage matched/],
    ['unavailable', /could not be searched \(index unreachable\)/]]) {
    const w = narrativesClause({ narratives: { state, packed: true, passages: [] } });
    assert.match(w, re, state);
    assert.doesNotMatch(w, /no narratives/);
  }
});

test('a compile with no narratives section adds nothing', () => {
  assert.equal(narrativesClause({ packed: [] }), '');
  assert.equal(narrativesHtml({ packed: [] }), '');
});

test('a multi-resource compile names the file system', () => {
  const m = { per_resource: { tree: found(true), other: { narratives: { state: 'not_read', packed: true, passages: [] } } } };
  assert.deepEqual(narrativeSections(m).map((n) => n.slug), ['tree', 'other']);
  assert.match(narrativeWords(narrativeSections(m)[1]), /narrative files of other not read yet/);
});

test('the compile panel lists the citation and the word proposal', () => {
  const html = narrativesHtml(found(true));
  assert.match(html, /data-compile-narratives/);
  assert.match(html, /stated by survey\/README\.md § Variables/);
  assert.match(html, /data-narrative-label>proposal</);
  assert.match(html, /data-narrative-passage="w1\|survey\/README\.md\|Variables"/);
  assert.match(narrativesHtml(found(false)), /data-narrative-state="found-dropped"/);
});

test('the Locator shows the best passage with citation and proposal, and counts the unread', () => {
  const out = { state: 'found', words: '1 of 1 searched file system matched, ranked by best passage · 2 file systems not read yet, not searched',
    matches: [{ slug: 'tree', display_name: 'Tree', score: 0.8123, citation: 'stated by a/README.md § Scope', label: 'a proposal', text: 'about x' }],
    not_read: ['b', 'c'], not_read_words: '2 file systems not read yet, not searched', unknown: [], unknown_words: '', unavailable: [] };
  const html = passageResultsHtml(out);
  assert.match(html, /data-passage-match="tree"/);
  assert.match(html, /stated by a\/README\.md § Scope/);
  assert.match(html, /data-passage-label[^>]*>proposal</);
  assert.match(html, /data-passage-not-read>2 file systems not read yet, not searched/);
});

test('unknown and unavailable are said apart from not read', () => {
  const html = passageResultsHtml({ state: 'no_match', words: 'read, and no passage matched', matches: [], not_read: [],
    not_read_words: '', unknown: ['x'], unknown_words: '1 file system whether its narratives were pressed could not be read, not searched',
    unavailable: ['y'] });
  assert.match(html, /data-passage-unknown/);
  assert.match(html, /data-passage-unavailable>1 could not be searched \(index unreachable\)/);
  assert.doesNotMatch(html, /data-passage-not-read/);
});
