// Every evidence list under an answer has a label (which analysis, what it
// lists); an empty list shows no link and says why (2026-10-15, dataops_testgen
// "Does TestGen have an API?": unlabeled links, one opened "0 findings").
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const chat = readFileSync(join(here, '../../resource_explorer/web/static/next/chat.js'), 'utf8');
const src = chat.slice(chat.indexOf('const MEMBER_LISTED = new Set('), chat.indexOf('function renderTurnList('));
const mod = 'const esc = (s) => String(s); const icon = (n) => `<svg data-icon="${n}"/>`;\n' + src
  + '\nexport { listSentences, evidenceFooterListsHtml };\n';
const { listSentences, evidenceFooterListsHtml } = await import('data:text/javascript,' + encodeURIComponent(mod));

const ext = (total, shown = total) => ({ total, shown: { FULL: shown, SUMMARY: shown } });
const body = (lists, keys) => ({ compiled: { manifest: {
  packed: keys.map((key) => ({ key, role: 'evidence', rung: 'FULL' })), lists } } });

test('every list names its analysis and what it lists', () => {
  const b = body({ dependency_analysis: { 'by_ecosystem.python': ext(32, 10) }, api_structure: { entry_points: ext(3) },
                   cve_scan: { findings: ext(4) } }, ['dependency_analysis', 'api_structure', 'cve_scan']);
  const html = evidenceFooterListsHtml(listSentences(b), 'p');
  const rows = html.split('</div>').filter((r) => r.includes('data-list-source'));
  assert.equal(rows.length, 3);
  for (const key of ['dependency_analysis', 'api_structure', 'cve_scan']) {
    const row = rows.find((r) => r.includes(`data-list-source="${key}"`));
    assert.match(row, new RegExp(`data-list-label[^>]*>${key.replace(/_/g, ' ')}<`), key);
  }
});

test('an empty list shows no link and says why', () => {
  const b = body({ api_structure: { entry_points: ext(0) } }, ['api_structure']);
  const html = evidenceFooterListsHtml(listSentences(b), 'p');
  assert.ok(!html.includes('data-list-source'), 'no link for an empty list');
  assert.match(html, /nothing stored by api_structure for this repo/);
  assert.match(html, /data-list-empty/);
});

test('one row per section, naming each list field with its count and noun', () => {
  const b = body({ cve_scan: { ecosystems_seen: ext(1), findings: ext(1) } }, ['cve_scan']);
  const html = evidenceFooterListsHtml(listSentences(b), 'p');
  assert.equal((html.match(/data-list-source=/g) || []).length, 1, 'one link');
  assert.equal((html.match(/data-list-label/g) || []).length, 1, 'one row');
  assert.match(html, /<span class="tnum">1<\/span> finding\b/);
  assert.match(html, /<span class="tnum">1<\/span> ecosystem\b/);
});

test('a row shows count and noun, then the extent', () => {
  const b = body({ interface_surface: { endpoints: ext(49, 20) } }, ['interface_surface']);
  const html = evidenceFooterListsHtml(listSentences(b), 'p');
  const row = html.split('</div>').find((r) => r.includes('data-list-source'));
  assert.match(row, /interface surface<\/span> · <span class="tnum">49<\/span> endpoints · <span[^>]*><span class="tnum">20<\/span> of <span class="tnum">49<\/span> shown to the model/);
});

test('a cut list gets a note from the manifest; a whole one does not', () => {
  const cut = evidenceFooterListsHtml(listSentences(body({ interface_surface: { endpoints: ext(49, 20) } }, ['interface_surface'])), 'p');
  assert.match(cut, /data-partial-note[^>]*>Listed <span class="tnum">20<\/span> of <span class="tnum">49<\/span> endpoints/);
  const whole = evidenceFooterListsHtml(listSentences(body({ interface_surface: { endpoints: ext(3) } }, ['interface_surface'])), 'p');
  assert.ok(!whole.includes('data-partial-note'));
});

test('footer colours follow the ground: paper has no chrome-* text, the rail has no paper ink', () => {
  const b = body({ interface_surface: { endpoints: ext(49, 20) }, cve_scan: { findings: ext(1) }, api_structure: { entry_points: ext(0) } },
    ['interface_surface', 'cve_scan', 'api_structure']);
  const lists = listSentences(b);
  const paper = evidenceFooterListsHtml(lists, 'p');
  assert.ok(!/text-chrome-|text-accent-on-dark/.test(paper), 'paper: ' + paper);
  assert.match(paper, /text-ink\b/);
  const rail = evidenceFooterListsHtml(lists, 'p', 'chrome');
  assert.ok(!/text-ink\b|text-ink-muted|text-accent-ink/.test(rail), 'rail: ' + rail);
  assert.match(rail, /text-chrome-ink/);
});
