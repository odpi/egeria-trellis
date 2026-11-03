/** Behaviour (architect rulings 2026-10-23):
 *  (A) the LANGUAGE tile names its source with a visible chip, "measured" (solid) or "GitHub" (outline); the basis is
 *      in the hover; neither source keeps the existing "not measured" cue, never an empty "measured".
 *  (B) the catalogue read-back is three count chips in a fixed order, "published by RE N", "attached N",
 *      "cataloged by Egeria N", each with its cue (✓ / ◌), and "not checked since" on a stale pending count.
 *      The word "waiting" is gone. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load(path) {
  makeDomEnvironment();
  ensureLoaderRegistered();
  return import(path);
}
const strip = (h) => h.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
const languageTile = (h) => (/<div data-scouting-tile="language"[\s\S]*?<\/div><\/div>/.exec(h) || [''])[0];

const MEASURED = {
  value: 'Python', source: 'measured', chip: 'measured', basis: 'source files 839/983',
  hover: 'measured · 839 of 983 source files · 2026-10-20 08:00\nGitHub says HTML (by bytes)',
  disagreement: 'GitHub says HTML (by bytes)',
};

test('measured: value with a solid "measured" chip, basis and disagreement in the hover', async () => {
  const { scoutingTilesHtml } = await load('/static/next/stages/repo-header.js');
  const t = languageTile(scoutingTilesHtml({ primary_language: 'Python', language: MEASURED, stats_unread: [] }));
  assert.match(t, /data-language-source="measured"[^>]*class="[^"]*bg-ink[^"]*text-paper/);
  assert.equal(strip(t), 'LanguagePython measured');
  assert.match(t, /title="measured · 839 of 983 source files · 2026-10-20 08:00\nGitHub says HTML \(by bytes\)"/);
});

test('GitHub only: outline "GitHub" chip and the by-bytes hover, no disagreement line', async () => {
  const { scoutingTilesHtml } = await load('/static/next/stages/repo-header.js');
  const lang = { value: 'HTML', source: 'github', chip: 'GitHub', basis: 'bytes', hover: 'GitHub · primary by bytes · fetched 2026-10-19', disagreement: '' };
  const t = languageTile(scoutingTilesHtml({ primary_language: 'HTML', language: lang, stats_unread: [] }));
  assert.match(t, /data-language-source="github"[^>]*class="[^"]*border-rule-strong/);
  assert.ok(!/bg-ink/.test(t));
  assert.equal(strip(t), 'LanguageHTML GitHub');
  assert.match(t, /title="GitHub · primary by bytes · fetched 2026-10-19"/);
  assert.ok(!/says/.test(t));
});

test('neither source: the existing "not read" cue, never an empty "measured"', async () => {
  const { scoutingTilesHtml } = await load('/static/next/stages/repo-header.js');
  const t = languageTile(scoutingTilesHtml({ primary_language: '', language: { value: '', source: '' }, stats_unread: ['primary_language'] }));
  assert.match(t, /data-unread/);
  assert.ok(!/data-language-source/.test(t));
  assert.ok(!/measured/.test(strip(t)));
});

const chips = (published, attached, catalogued, cue, stale = '') => ({ chips: [
  { key: 'published', word: 'published by RE', count: published, unit: '', cue: published ? 'read_back' : 'none', hover: 'the server and database elements RE published · read 10-23 09:00', not_checked_since: '' },
  { key: 'attached', word: 'attached', count: attached, unit: attached === 1 ? 'schema' : 'schemas', cue: attached ? 'read_back' : 'none', hover: 'chosen schemas attached', not_checked_since: '' },
  { key: 'catalogued', word: 'cataloged by Egeria', count: catalogued, unit: catalogued === 1 ? 'schema' : 'schemas', cue, hover: 'chosen schemas whose tables Egeria built', not_checked_since: stale },
] });

test('read-back chips: three in a fixed order, ✓ on read back, ◌ while the cataloguer has not run', async () => {
  const { readBackChipsHtml } = await load('/static/next/stages/curate-scope.js');
  const h = readBackChipsHtml(chips(2, 2, 0, 'pending'));
  assert.deepEqual([...h.matchAll(/data-read-back-chip="([a-z]+)"/g)].map((m) => m[1]), ['published', 'attached', 'catalogued']);
  // Units on screen, singular for 1; chips are separated by whitespace, never " · ".
  assert.equal(strip(h), 'published by RE 2 ✓ attached 2 schemas ✓ cataloged by Egeria 0 schemas ◌');
  assert.ok(!/·/.test(strip(h)));
  assert.match(h, /<\/span> <span data-read-back-chip="attached"/);
  assert.match(strip(readBackChipsHtml(chips(2, 1, 1, 'read_back'))), /attached 1 schema ✓ cataloged by Egeria 1 schema ✓$/);
  assert.ok(!/waiting/.test(h));
  assert.match(h, /data-read-back-chip="published"[^>]*title="the server and database elements RE published · read 10-23 09:00"/);
});

test('read-back chips: the tree built reads "cataloged by Egeria 18 ✓"', async () => {
  const { readBackChipsHtml } = await load('/static/next/stages/curate-scope.js');
  assert.match(strip(readBackChipsHtml(chips(2, 18, 18, 'read_back'))), /cataloged by Egeria 18 schemas ✓$/);
});

test('read-back chips: a stale pending count says "not checked since"', async () => {
  const { readBackChipsHtml } = await load('/static/next/stages/curate-scope.js');
  const h = readBackChipsHtml(chips(2, 2, 0, 'pending', '2026-10-23T09:12:00'));
  assert.match(h, /data-read-back-not-checked/);
  // F37: no number beside "not checked", one cue, and the date when the read was not today.
  assert.match(strip(h), /cataloged by Egeria ◌ not checked since 10-23 09:12$/);
  assert.equal((strip(h).match(/◌/g) || []).length, 1);
  const now = new Date(Date.now() - 1000).toISOString().slice(0, 19);
  const today = strip(readBackChipsHtml(chips(2, 2, 0, 'pending', now)));
  assert.match(today, new RegExp(`cataloged by Egeria ◌ not checked since ${now.slice(11, 16)}$`));
});

test('read-back chips: nothing derived draws nothing; the scope section carries the row under its header', async () => {
  const { readBackChipsHtml, scopeSectionHtml } = await load('/static/next/stages/curate-scope.js');
  assert.equal(readBackChipsHtml(null), '');
  assert.equal(readBackChipsHtml({ chips: [] }), '');
  // Absence is not three measured zeros (fix round 2): chips with no proof behind any of them draw nothing.
  assert.equal(readBackChipsHtml(chips(0, 0, 0, 'none')), '');
  const view = { schemas: [], declared: { declared: true }, commit: { header: { state: 'committed', text: 'Database element abcd1234 in Egeria' }, read_back: chips(2, 2, 1, 'pending') } };
  const h = scopeSectionHtml(view, { user_id: 'me' });
  assert.match(h, /data-scope-commit-header[\s\S]*data-read-back-host[\s\S]*data-read-back-chip="published"[\s\S]*data-read-back-chip="attached"[\s\S]*data-read-back-chip="catalogued"/);
});
