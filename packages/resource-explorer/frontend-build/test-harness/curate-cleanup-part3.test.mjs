/** Brief A section 7: the five follow-ups from #560's review (Curate part 2), all in stages/curate.js.
 *  (a) renderBlueprintList took no token: an older read could paint into a newer render's slot.
 *  (b) the retry handler did not join a read already in flight: a second read started.
 *  (c) a REJECTED dependency-table read left the section at "loading…" with no retry; an early `if (!host) return`
 *      and tree errors outside loadSectionData left a section marked as loaded.
 *  (d) the render token was taken after the band setup and the non-repository early return.
 *  (e) `_groupBusy` was per branch box: a press on a second group of the same branch was silently blocked. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setUp, wait, norm, PARENT } from './curate-part3-kit.mjs';

const kinds = [{ kind: 'deployment', perspective: 'physical', name: 'Deployment', source: 's', state: 'proposed', drawn: true },
  { kind: 'logical', perspective: 'logical', name: 'Logical', source: 's', state: 'proposed', drawn: true }];
const bp = (name, persp = 'physical') => ({ perspective: persp, cluster_name: name, members: [], member_status: [], child_status: [], verdict: null });

test('(a) an older blueprint read finishing after a newer one never paints over it', async () => {
  const { document, server } = await setUp({ blueprints: { blueprints: [bp('first')], perspectives: ['physical', 'logical'], kinds } });
  server.bpCalls = 0;
  server.blueprintsFn = (n) => ({ blueprints: [bp(n === 1 ? 'OLD-ONE' : 'NEW-ONE')], perspectives: ['physical', 'logical'], kinds });
  server.holdBlueprints = async (n) => { if (n === 1) await wait(450); };       // the older read is the slow one
  const view = document.querySelector('[data-blueprint-view]');
  view.click(); view.click();                                                      // render A (slow), then render B (fast)
  await wait(900);
  const t = norm(document.getElementById('blueprint-list'));
  assert.match(t, /NEW-ONE/, 'the newer render is on screen');
  assert.doesNotMatch(t, /OLD-ONE/, 'the older response did not overwrite it');
});

test('(b) a retry pressed while that section\'s read is already in flight joins it: no second read', async () => {
  const { document, server, app } = await setUp({});
  const reads = () => server.calls.filter((c) => c.method === 'GET' && c.url.includes('/components/tree')).length;
  server.holdTree = async () => { await wait(400); };
  app.state.curate.loaded.tree = false;
  const before = reads();
  app.state.curate.loadSection('curate-sec-made-of');                              // a tracked load, slow
  await wait(50);
  assert.equal(reads(), before + 1);
  const host = document.getElementById('curate-host');
  host.insertAdjacentHTML('beforeend', '<button type="button" data-curate-retry="tree">retry</button>');
  host.querySelector('button[data-curate-retry="tree"]').click();
  await wait(550);
  assert.equal(reads(), before + 1, 'the retry joined the read in flight');
});

test('(c) a dependency read that is REJECTED gets the error slot and a retry, not "loading…" for ever', async () => {
  const { document, app } = await setUp({}, { open: false });
  const dep = await import('/static/next/stages/dependencies.js');
  assert.ok(dep.mountDependencyTable);
  // The read answers, but the table cannot draw the answer: the mount itself rejects (not a read error).
  dep.forgetDependencyTable();
  globalThis.fetch = (real => async (url, opts) => (String(url).endsWith('/dependencies')
    ? { ok: true, status: 200, json: async () => ({ heading: 'x', kinds: [], counts: {}, runtime_state: '', rows: [null] }) } : real(url, opts)))(globalThis.fetch);
  (await import('/static/next/stages/curate.js')).jumpToCurateSection('curate-sec-relates');   // Structure, opened
  await wait(300);
  const slot = document.querySelector('[data-dependency-table-host]');
  const t = norm(slot);
  assert.doesNotMatch(t, /loading dependencies/, 'no longer stuck at loading');
  assert.ok(slot.querySelector('[data-curate-retry="deps"]'), 'a retry control is offered');
  assert.equal(app.state.curate.loaded.deps, false, 'a failed read is not remembered as loaded');
});

test('(c) a section with nowhere to draw is not marked loaded', async () => {
  const { document, app } = await setUp({}, { open: false });
  document.getElementById('component-tree').remove();
  app.state.curate.loaded.tree = false;
  app.state.curate.loadSection('curate-sec-made-of');
  await wait(150);
  assert.equal(app.state.curate.loaded.tree, false);
  document.getElementById('blueprint-list').remove();
  app.state.curate.loaded.blueprints = false;
  app.state.curate.loadSection('curate-sec-blueprints');
  await wait(150);
  assert.equal(app.state.curate.loaded.blueprints, false);
});

test('(c) a tree error outside loadSectionData (a redraw after a pick) leaves the section not loaded, with a retry', async () => {
  const { document, app, server } = await setUp({});
  assert.equal(app.state.curate.loaded.tree, true);
  server.treeFail = true;
  document.querySelector('[data-curate-pick]').click();                            // draw() then refreshTree()
  await wait(300);
  assert.equal(app.state.curate.loaded.tree, false, 'the failed redraw is not "loaded"');
  assert.ok(document.querySelector('#component-tree [data-curate-retry="tree"]'));
  server.treeFail = false;
  document.querySelector('#component-tree [data-curate-retry="tree"]').click();
  await wait(300);
  assert.ok(document.querySelector('[data-branch="packages/x"]'), 'the retry read it again');
  assert.equal(app.state.curate.loaded.tree, true);
});

test('(d) a non-repository render retires a repository render still waiting for its plan', async () => {
  let release;
  const holdPlan = new Promise((r) => { release = r; });
  const { app, document } = await setUp({ holdPlan }, { settle: 120 });
  assert.equal(app.state.curate.loadSection, null);
  app.state.resourceType = 'database';
  const { renderCurate } = await import('/static/next/stages/curate.js');
  await renderCurate('egeria_git');                                                // the newer, non-repository render
  release();
  await wait(300);
  assert.equal(app.state.curate.loadSection, null, 'the older repository render did not take over shared state');
  assert.ok(document);
});

test('(e) a press on a second group of the same branch is not blocked by the first group\'s batch in flight', async () => {
  const releases = [];
  const { document, server } = await setUp({ holdVerdicts: () => new Promise((r) => releases.push(r)) });
  document.querySelector(`[data-branch-open="${PARENT.path}"]`).click();
  await wait(200);
  const press = async (group) => {
    document.querySelector(`[data-leaf-group="${group}"] [data-group-verdict="accepted"]`).click();
    await wait(80);
    document.querySelector('[data-act="confirm"]').click();
    await wait(80);
  };
  await press('compose');                                                          // posted, held open
  await press('other');                                                            // a different group: must not be blocked
  const posts = server.calls.filter((c) => c.url.endsWith('/components/verdicts'));
  assert.equal(posts.length, 2, 'both groups were posted');
  assert.deepEqual(posts.map((p) => p.body.scope_locators.sort()), [
    ['packages/x/compose/a', 'packages/x/compose/b'], ['packages/x/other/c', 'packages/x/other/d']]);
  releases.forEach((r) => r());
  await wait(200);
});

test('(e) the SAME group pressed twice while its batch is in flight is still posted once', async () => {
  const releases = [];
  const { document, server } = await setUp({ holdVerdicts: () => new Promise((r) => releases.push(r)) });
  document.querySelector(`[data-branch-open="${PARENT.path}"]`).click();
  await wait(200);
  for (let i = 0; i < 2; i += 1) {
    document.querySelector('[data-leaf-group="compose"] [data-group-verdict="accepted"]').click();
    await wait(80);
    document.querySelector('[data-act="confirm"]')?.click();
    await wait(80);
  }
  assert.equal(server.calls.filter((c) => c.url.endsWith('/components/verdicts')).length, 1);
  releases.forEach((r) => r());
  await wait(200);
});
