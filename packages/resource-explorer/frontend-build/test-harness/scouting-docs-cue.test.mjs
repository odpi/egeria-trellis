/** Behaviour: the Scouting header shows the documentation link beside the homepage, with a short cue
 *  and no sentence: "docs ✓", "docs unreachable", "no docs found", "docs not checked". Nothing is drawn
 *  until the Homepage step has run (cue ''), because nothing measured is not "no docs found". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function load() {
  makeDomEnvironment();
  ensureLoaderRegistered();
  return import('/static/next/stages/repo-header.js');
}
const strip = (h) => h.replace(/<[^>]*>/g, '');

test('reachable docs: linked, cue "docs ✓"', async () => {
  const { docsHtml, scoutingTilesHtml } = await load();
  const ov = { homepage: 'https://datakitchen.io', docs_url: 'https://docs.datakitchen.io/testgen/', docs_cue: 'docs ✓', docs_state: 'reachable', docs_status: 200 };
  const h = docsHtml(ov);
  assert.match(h, /href="https:\/\/docs\.datakitchen\.io\/testgen\/"/);
  assert.equal(strip(h).replace(/\s+/g, ' ').trim(), '· docs ✓ ↗');
  assert.match(scoutingTilesHtml(ov), /datakitchen\.io[\s\S]*docs ✓/);
});

test('unreachable docs: still linked, cue says so', async () => {
  const { docsHtml } = await load();
  const h = docsHtml({ docs_url: 'https://x.dev/docs', docs_cue: 'docs unreachable', docs_state: 'unreachable', docs_status: 404 });
  assert.match(h, /href="https:\/\/x\.dev\/docs"/);
  assert.match(strip(h), /docs unreachable/);
});

test('no docs found and not checked: a word, no link', async () => {
  const { docsHtml } = await load();
  for (const cue of ['no docs found', 'docs not checked']) {
    const h = docsHtml({ docs_url: '', docs_cue: cue, docs_state: 'none_found' });
    assert.ok(!/<a /.test(h));
    assert.match(strip(h), new RegExp(cue));
  }
});

test('never run: draws nothing, not "no docs found"', async () => {
  const { docsHtml } = await load();
  assert.equal(docsHtml({ docs_cue: '' }), '');
  assert.equal(docsHtml(null), '');
});

test('cue with no homepage still shows beside "none found"', async () => {
  const { scoutingTilesHtml } = await load();
  const h = scoutingTilesHtml({ homepage: '', docs_url: '', docs_cue: 'no docs found', docs_state: 'none_found' });
  assert.match(strip(h), /none found[\s\S]*no docs found/);
});
