/** TR-132 C1: the repository card shows ONE line about the branches, "Branches: main surveyed · 4 listed", and the list
 *  (name, kind, short head SHA, protected) only when the person opens it. Nothing else is added by default (TR-71). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  const { document, window } = makeDomEnvironment();
  ensureLoaderRegistered();
  const mod = await import('/static/next/stages/repo-header.js');
  return { mod, document, window };
}
const ROWS = [
  { ref_name: 'main', ref_kind: 'branch', surveyed_commit: 'm'.repeat(40), tier: 'surveyed', is_default: true, protected: true },
  { ref_name: 'dev', ref_kind: 'branch', surveyed_commit: 'd'.repeat(40), tier: 'listed', is_default: false, protected: false },
  { ref_name: 'wip/x', ref_kind: 'branch', surveyed_commit: 'e'.repeat(40), tier: 'listed', is_default: false, protected: null },
];
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const mount = (document, html) => { document.body.innerHTML = html; return document.body; };

test('one line: the surveyed branch and how many others are listed', async () => {
  const { mod, document } = await load();
  const body = mount(document, mod.branchesLineHtml({ branches: ROWS }));
  assert.equal(text(body.querySelector('[data-repo-branches] summary')), 'Branches: main surveyed · 2 listed');
});

test('the list is not shown until it is opened, and is a closed <details> by default', async () => {
  const { mod, document } = await load();
  const body = mount(document, mod.branchesLineHtml({ branches: ROWS }));
  const d = body.querySelector('details[data-repo-branches]');
  assert.ok(d && !d.hasAttribute('open'), 'closed by default');
  const rows = [...d.querySelectorAll('[data-repo-branches-list] tbody tr')].map((r) => [...r.querySelectorAll('td')].map(text));
  assert.deepEqual(rows, [
    ['main', 'branch', 'mmmmmmm', 'protected'],
    ['dev', 'branch', 'ddddddd', 'not protected'],
    ['wip/x', 'branch', 'eeeeeee', 'not read'],      // unknown protection is "not read", never "not protected"
  ]);
});

test('no recorded refs means no line at all: never "0 branches"', async () => {
  const { mod } = await load();
  assert.equal(mod.branchesLineHtml({ branches: [] }), '');
  assert.equal(mod.branchesLineHtml({}), '');
  assert.equal(mod.branchesLineHtml(null), '');
});

test('a tag survey says the kind, and the other branches are still counted', async () => {
  const { mod, document } = await load();
  const rows = [{ ref_name: 'v1', ref_kind: 'tag', surveyed_commit: '1'.repeat(40), tier: 'surveyed', is_default: false, protected: null },
    { ref_name: 'main', ref_kind: 'branch', surveyed_commit: 'm'.repeat(40), tier: 'listed', is_default: true, protected: true }];
  const body = mount(document, mod.branchesLineHtml({ branches: rows }));
  assert.equal(text(body.querySelector('summary')), 'Branches: v1 (tag) surveyed · 1 listed');
});

test('names are escaped', async () => {
  const { mod } = await load();
  const html = mod.branchesLineHtml({ branches: [{ ref_name: '<img src=x>', ref_kind: 'branch', surveyed_commit: 'a', tier: 'surveyed', protected: null }] });
  assert.ok(!html.includes('<img src=x>') && html.includes('&lt;img src=x&gt;'));
});

test('app.js draws the line under the survey line, for a repository only, from the overview', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../../resource_explorer/web/static/next/app.js', import.meta.url), 'utf8');
  assert.match(src, /state\.resourceType === 'repo' \? branchesLineHtml\(ov\) : ''/);
  assert.match(src, /import \{[^}]*branchesLineHtml[^}]*\} from '\/static\/next\/stages\/repo-header\.js'/);
});
