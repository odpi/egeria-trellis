/** Brief A (2026-10-09): Accept and Reject are decisions only; Publish writes the architecture.
 *  Owner: "Publish should be the verb to save to Egeria". The screen says so on every row (a cue and a short word,
 *  from the row's own cache row), lists what Publish will write BEFORE the press from the server's plan, and draws
 *  the per-item results AFTER it from the re-read plan, never from the click. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setUp, wait, norm, emptyPlan, verdictPosts, openBranch } from './curate-part3-kit.mjs';

const PLAN = () => ({
  ...emptyPlan(), nothing: false, label: 'Publish 3 components · 1 blueprint',
  components: { to_write: [{ path: 'a', name: 'alpha', type: 'Service' }, { path: 'b', name: 'beta', type: '' }, { path: 'c', name: 'gamma', type: '' }], in_egeria: 2, rejected_in_egeria: 1 },
  blueprints: { to_write: [{ key: 'deployment::core', name: 'Egeria Deployment Blueprint', state: 'new', unconfirmed_compositions: 0 }], in_egeria: 0, rejected_in_egeria: 0 },
});
const publishHost = (document) => document.querySelector('[data-architecture-publish]');

test('before the press: the control names its object and count, and the list of what will be written is shown', async () => {
  const { document } = await setUp({ publishPlan: PLAN() });
  const host = publishHost(document);
  assert.ok(host, 'the architecture Publish is in the publish band');
  assert.equal(norm(host.querySelector('[data-architecture-go]')), 'Publish 3 components · 1 blueprint →');
  const will = host.querySelector('[data-architecture-will-write]');
  assert.deepEqual([...will.querySelectorAll('[data-will-component]')].map((e) => e.dataset.willComponent), ['a', 'b', 'c']);
  assert.match(norm(will.querySelector('[data-will-blueprint]')), /Egeria Deployment Blueprint · new/);
  assert.match(norm(host), /2 in Egeria/);
  assert.match(norm(host), /1 rejected but still in Egeria/);
});

test('with nothing accepted and waiting the control says so and cannot be pressed', async () => {
  const { document, server } = await setUp({});
  const go = publishHost(document).querySelector('[data-architecture-go]');
  assert.equal(go.disabled, true);
  assert.match(norm(go), /Nothing to publish/);
  go.click();
  await wait(80);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/architecture/publish')).length, 0);
});

test('the press posts once, then draws per-item results from the RE-READ plan: done, already there, failed with Egeria\'s sentence', async () => {
  const after = { ...emptyPlan(), last: { run: 'a1', at: 'x', items: [
    { key: 'a', kind: 'component', name: 'alpha', status: 'done', words: 'created in Egeria' },
    { key: 'b', kind: 'component', name: 'beta', status: 'skipped', words: 'already in Egeria' },
    { key: 'c', kind: 'component', name: 'gamma', status: 'failed', words: 'OMAG-400-001 the type is not known' },
    { key: 'deployment::core', kind: 'blueprint', name: 'Egeria Deployment Blueprint', status: 'partial', words: 'written, but 1 of 6 compositions not confirmed' }] } };
  const { document, server } = await setUp({ publishPlan: PLAN(), afterPublish: after });
  const go = publishHost(document).querySelector('[data-architecture-go]');
  go.click(); go.click();                                              // a second press is ignored
  await wait(400);
  assert.equal(server.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/architecture/publish')).length, 1);
  const res = publishHost(document).querySelector('[data-architecture-results]');
  const by = (k) => res.querySelector(`[data-result="${k}"]`);
  assert.equal(by('a').dataset.resultStatus, 'done');
  assert.match(norm(by('b')), /already there/);
  assert.match(norm(by('c')), /failed.*OMAG-400-001 the type is not known/);
  assert.match(norm(by('deployment::core')), /partial.*1 of 6 compositions not confirmed/);
  assert.equal(by('c').querySelector('[data-cue]').dataset.cue, 'error', 'a failure is an error cue, never a check');
  assert.equal(publishHost(document).querySelector('[data-architecture-will-write]'), null, 'the list is the re-read plan\'s: nothing waits now');
});

test('a second press while one runs is said, not hidden', async () => {
  const { document } = await setUp({ publishPlan: PLAN(), publishFail: 409 });
  publishHost(document).querySelector('[data-architecture-go]').click();
  await wait(300);
  assert.match(norm(publishHost(document).querySelector('[data-architecture-feedback]')), /a publish is already running for this repository/);
});

test('Brief Z: an item the presser may not change is its own row, "not permitted", with the reason said once', async () => {
  const words = 'not permitted · zone sales-zone does not grant garygeeke update in Egeria';
  const after = { ...emptyPlan(), last: { run: 'a1', at: 'x', items: [
    { key: 'a', kind: 'component', name: 'alpha', status: 'done', words: 'created in Egeria' },
    { key: 'deployment::core', kind: 'blueprint', name: 'Egeria Deployment Blueprint', status: 'not_permitted', words }] } };
  const { document } = await setUp({ publishPlan: PLAN(), afterPublish: after });
  publishHost(document).querySelector('[data-architecture-go]').click();
  await wait(400);
  const row = publishHost(document).querySelector('[data-result="deployment::core"]');
  assert.equal(row.dataset.resultStatus, 'not_permitted');
  assert.equal(row.querySelector('[data-cue]').dataset.cue, 'no_access', 'a refusal has its own cue, not a failure');
  assert.equal(norm(row), '? not permitted Egeria Deployment Blueprint · zone sales-zone does not grant garygeeke update in Egeria');
  assert.equal(publishHost(document).querySelector('[data-result="a"]').dataset.resultStatus, 'done', 'the rest went on');
});

test('Brief Z: a whole press refused (nothing permitted) says "not permitted" and the reason', async () => {
  const { document } = await setUp({ publishPlan: PLAN(), publishFail: 403,
    publishFailDetail: 'zone sales-zone does not grant garygeeke update in Egeria' });
  publishHost(document).querySelector('[data-architecture-go]').click();
  await wait(300);
  const fb = publishHost(document).querySelector('[data-architecture-feedback]');
  assert.equal(fb.querySelector('[data-cue]').dataset.cue, 'no_access');
  assert.equal(norm(fb), '? not permitted · zone sales-zone does not grant garygeeke update in Egeria');
});

test('a verdict recorded on the tree makes Publish re-read its list', async () => {
  const { document, server } = await setUp({ publishPlan: PLAN() });
  const reads = () => server.calls.filter((c) => c.url.endsWith('/architecture/publish-plan')).length;
  const before = reads();
  document.querySelector('[data-branch-scope="with"]').click();
  await wait(60);
  document.querySelector('[data-branch-verdict="accepted"]').click();
  await wait(60);
  document.querySelector('[data-act="confirm"]').click();
  await wait(300);
  assert.ok(reads() > before, 'the plan was read again after the verdict');
});

test('Accept on a branch says it is a decision: nothing queued, nothing in Egeria until Publish', async () => {
  const { document, server } = await setUp({ publishPlan: PLAN() });
  document.querySelector('[data-branch-scope="with"]').click();
  await wait(60);
  document.querySelector('[data-branch-verdict="accepted"]').click();
  await wait(60);
  assert.match(norm(document.querySelector('#wl-detail-body')), /nothing is written to Egeria until you press Publish/i);
  assert.doesNotMatch(norm(document.querySelector('#wl-detail-body')), /will be created as software components|queued/);
  document.querySelector('[data-act="confirm"]').click();
  await wait(300);
  const status = norm(document.getElementById('component-tree-status'));
  assert.match(status, /3 verdicts? recorded|1 verdict recorded/);
  assert.match(status, /not in Egeria until Publish/);
  assert.doesNotMatch(status, /queued for Egeria/);
  assert.equal(verdictPosts(server).length, 1);
});

test('a row\'s own words: accepted is "not in Egeria yet" until it is; rejected but there says "still in Egeria"', async () => {
  const leafRows = { 'packages/x/solo': { materialized: false, verdict: { verdict: 'accepted' } } };
  const a = await setUp({ leafRows });
  await openBranch(a.document);
  const solo = () => a.document.querySelector('[data-leaf-path="packages/x/solo"]');
  assert.match(norm(solo()), /accepted.*not in Egeria yet/);
  assert.equal(solo().querySelector('[data-in-egeria]').dataset.inEgeria, 'no');
  const b = await setUp({ leafRows: { 'packages/x/solo': { materialized: true, verdict: { verdict: 'accepted' } } } });
  await openBranch(b.document);
  assert.equal(b.document.querySelector('[data-leaf-path="packages/x/solo"] [data-in-egeria]').dataset.inEgeria, 'yes');
  const c = await setUp({ leafRows: { 'packages/x/solo': { materialized: true, verdict: { verdict: 'rejected' } } } });
  await openBranch(c.document);
  const still = c.document.querySelector('[data-leaf-path="packages/x/solo"] [data-in-egeria]');
  assert.equal(still.dataset.inEgeria, 'still');
  assert.match(norm(still), /still in Egeria/);
  assert.equal(still.querySelector('[data-cue]').dataset.cue, 'partial');
  const d = await setUp({ leafRows: { 'packages/x/solo': { materialized: false, verdict: { verdict: 'rejected' } } } });
  await openBranch(d.document);
  assert.equal(d.document.querySelector('[data-leaf-path="packages/x/solo"] [data-in-egeria]'), null, 'a reject that left nothing behind says nothing more');
});

test('a blueprint row says "accepted · not in Egeria yet" and "rejected · still in Egeria" from its cache row', async () => {
  const { document } = await setUp({});
  const { blueprintRowHtml } = await import('/static/next/stages/curate.js');
  const box = document.createElement('div');
  const base = { perspective: 'deployment', cluster_name: 'core', members: ['a'], child_status: [], member_status: [{ slug: 'a', verdict: { verdict: 'accepted' } }] };
  box.innerHTML = blueprintRowHtml({ ...base, verdict: { verdict: 'accepted' }, materialized: null });
  assert.match(norm(box.querySelector('[data-in-egeria="no"]')), /accepted · not in Egeria yet/);
  assert.doesNotMatch(norm(box), /cataloged|re-accepting will retry/);
  box.innerHTML = blueprintRowHtml({ ...base, verdict: { verdict: 'rejected' }, materialized: { guid: 'abcdef012345' } });
  assert.match(norm(box.querySelector('[data-in-egeria="still"]')), /rejected · still in Egeria/);
  box.innerHTML = blueprintRowHtml({ ...base, verdict: { verdict: 'rejected' }, materialized: null });
  assert.match(norm(box), /rejected · nothing in Egeria/);
  box.innerHTML = blueprintRowHtml({ ...base, verdict: { verdict: 'accepted' }, materialized: { guid: 'abcdef012345' } });
  assert.match(norm(box.querySelector('[data-in-egeria="yes"]')), /in Egeria/);
});

test('a last publish that left no results says so instead of showing an earlier press as current', async () => {
  const { document } = await setUp({ publishPlan: { ...emptyPlan(), last: { run: 'act-new', at: '', items: [], missing: true } } });
  const host = publishHost(document);
  assert.match(norm(host.querySelector('[data-architecture-no-results]')), /the last publish left no results/);
  assert.equal(host.querySelector('[data-architecture-results]'), null);
});

test('a blueprint back only for unattached members says how many to attach, not "0 compositions"', async () => {
  const plan = { ...PLAN(), blueprints: { to_write: [{ key: 'deployment::core', name: 'Egeria Deployment Blueprint', state: 'finish', unconfirmed_compositions: 0, unattached: 2 }], in_egeria: 0, rejected_in_egeria: 0 } };
  const { document } = await setUp({ publishPlan: plan });
  const t = norm(publishHost(document).querySelector('[data-will-blueprint]'));
  assert.match(t, /already there · 2 members to attach/);
  assert.doesNotMatch(t, /0 compositions/);
});

test('a blueprint that needs an identifier is listed as held back, with its sentence, and is not counted on the control', async () => {
  const plan = PLAN();
  plan.blueprints.needs_identifier = [{ key: 'deployment::the parts', name: 'the parts',
    words: 'the services is accepted as the Deployment Blueprint for egeria_git and takes that name at Publish · give this one an identifier' }];
  const { document } = await setUp({ publishPlan: plan });
  const host = publishHost(document);
  const held = host.querySelector('[data-architecture-held-back] [data-held-blueprint="deployment::the parts"]');
  assert.ok(held, 'the held-back blueprint is listed');
  assert.match(norm(held), /needs an identifier/);
  assert.match(norm(held), /give this one an identifier/);
  assert.equal(host.querySelector('[data-will-blueprint="deployment::the parts"]'), null, 'it is not in what will be written');
  assert.equal(norm(host.querySelector('[data-architecture-go]')), 'Publish 3 components · 1 blueprint →');
});

test('a blueprint whose earlier press left no shape to read says "check Egeria", with the sentence', async () => {
  const plan = PLAN();
  plan.blueprints.check_egeria = [{ key: 'deployment::core', name: 'core', words: 'shape of the earlier press unknown — check Egeria' }];
  const { document } = await setUp({ publishPlan: plan });
  const row = publishHost(document).querySelector('[data-architecture-check-egeria] [data-check-blueprint="deployment::core"]');
  assert.ok(row, 'it is said on the band');
  assert.match(norm(row), /check Egeria.*core.*shape of the earlier press unknown/);
});
