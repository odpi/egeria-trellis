/** Behaviour tests: the right-hand evidence rail (#rail-evidence) must never
 *  show ANOTHER resource's -- or another pane's -- evidence.
 *
 *  Owner screenshot, 8810: viewing egeria_git, the rail read "for
 *  laz_local_adventureworks". The slot has several writers (openMembers,
 *  showEvidence, Context's renderEnrichmentEvidence, curate's Ports/Members)
 *  and, before the fix, only a render of Context cleared it on a slug change.
 *
 *  These tests drive the REAL app.js: they open the rail through the real
 *  writers (openMembers; the Questions pane's real "evidence" link ->
 *  showEvidence; the Enrichment/Context pane's real render) and then change
 *  the resource by clicking the real sidebar button, the stage by clicking
 *  the real nav button, the sub-tab by clicking the real sub-tab button.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));

/** A promise whose resolution the test controls, for a "late response". */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const MEMBERS = (slug) => ({
  analysis_id: 'a1', title: `A1 of ${slug}`, total: 1, source: 'tbl', scope_honoured: true,
  groups: [{ name: `group-of-${slug}`, count: 1, members: [{ name: `member-of-${slug}` }] }],
});

/** `gate` lets a test hold one URL pattern open until it chooses to answer. */
function stubServer({ gate = null } = {}) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    const slug = (u.match(/\/(?:members|analyses\/facts|context|questions)\/([^/?]+)/) || [])[1]
      || (u.match(/\/api\/[a-z-]+\/([^/?]+)\/(?:members|questions)/) || [])[1] || '';
    if (gate && gate.re.test(u)) {
      await gate.promise;
    }
    if (/\/api\/analyses\/(repo|database|filesystem)(\?|$)/.test(u)) return ok([]);
    if (u.includes('/members')) {
      const s = decodeURIComponent((u.match(/\/api\/[a-z-]+\/([^/?]+)\/[^?]*members/) || [])[1] || slug);
      return ok(MEMBERS(s));
    }
    if (u.includes('/answer')) {
      return ok({ answerable: true, question: 'Q1', facts: [{ analysis_id: 'a1', state: 'measured', is_known: true,
        headline: `headline-for-${decodeURIComponent((u.match(/facts\/([^/]+)\/answer/) || [])[1] || '')}`, value: {} }] });
    }
    if (u.includes('/api/analyses/facts')) return ok({ subjects: {} });
    if (u.includes('/api/doc-sources/')) return ok({ sources: [], published: false, publish_note: '' });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/questions')) {
      return ok({ questions: [{ question: 'Q1', kind: 'analysis', analysis_ids: ['a1'], phase: 'scouting' }] });
    }
    if (u.includes('/investigations')) return ok([]);
    if (u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
}

let seq = 0;
/** Two databases, alpha_N (selected) and beta_N, on the Questions tab of Scouting. */
async function setUp({ gate = null, stage = 'scouting', subTab = 'questions' } = {}) {
  const { document, window } = makeDomEnvironment();
  stubServer({ gate });
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.history.replaceState(null, '', '/next');
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const n = ++seq;
  const A = `alpha_${n}`;
  const B = `beta_${n}`;
  app.state.resourceType = 'db';
  app.state.databases = [{ slug: A, display_name: A }, { slug: B, display_name: B }];
  app.state.databasesLoaded = true;
  app.state.selectedSlug = A;
  app.state.stage = stage;
  app.state.subTab = subTab;
  app.state.investigations = [];
  app.state.investigation = '';
  app.state.workListSlug = null;
  app.state.workListIndex = false;
  app.state.answers = new Map();
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'sidebar', 'rail', 'rail-scope', 'scope-slug', 'investigation-name', 'whoami', 'activity-count']) {
    const d = document.createElement('div');
    d.id = id;
    document.body.appendChild(d);
  }
  const sw = document.createElement('a');
  sw.id = 'switch-ui';
  document.body.appendChild(sw);
  const rail = document.createElement('div');
  rail.id = 'rail-evidence';
  document.getElementById('rail').appendChild(rail);
  app.renderIntentNav();
  app.renderSidebar();
  // Land on the pane through the real nav, as a person would.
  document.querySelector(`#intent-nav button[data-stage="${stage}"]`).click();
  await wait();
  return { document, window, app, A, B, rail };
}

const clickResource = async (document, slug) => {
  const b = document.querySelector(`#sidebar button[data-slug="${slug}"]`);
  assert.ok(b, `sidebar must offer ${slug}`);
  b.click();
  await wait();
};
const clickStage = async (document, stage) => {
  const b = document.querySelector(`#intent-nav button[data-stage="${stage}"]`);
  assert.ok(b, `nav must offer ${stage}`);
  b.click();
  await wait();
};
const clickSubTab = async (document, sub) => {
  const b = document.querySelector(`#content button[data-subtab="${sub}"]`);
  assert.ok(b, `pane must offer sub-tab ${sub}`);
  b.click();
  await wait();
};

/** The rail must claim nothing about any resource other than `allowed`. */
function assertNoOtherEvidence(rail, others, why) {
  const t = rail.textContent;
  for (const o of others) assert.ok(!t.includes(o), `${why}: rail still names ${o}: ${JSON.stringify(t.slice(0, 160))}`);
}

// ── Writer 1: openMembers (the "N members" counts) ────────────────────────
async function openMembersFor(app, document, slug) {
  await app.openMembers({ slug, analysisId: 'a1', title: 'A1' });
  const r = document.getElementById('rail-evidence');
  assert.ok(r.textContent.includes(`member-of-${slug}`), `precondition: members of ${slug} showing; got ${r.textContent.slice(0, 100)}`);
}

test('members open for A: selecting resource B clears the rail', async () => {
  const { document, app, A, B, rail } = await setUp();
  await openMembersFor(app, document, A);
  await clickResource(document, B);
  assert.equal(app.state.selectedSlug, B);
  assertNoOtherEvidence(rail, [A], 'after selecting B');
  assert.ok(!/Members/.test(rail.textContent), `A's Members panel must be gone; got ${rail.textContent.slice(0, 100)}`);
});

test('members open for A: a stage change clears the rail', async () => {
  const { document, app, A, rail } = await setUp();
  await openMembersFor(app, document, A);
  await clickStage(document, 'discovery');
  assert.equal(app.state.stage, 'discovery');
  assertNoOtherEvidence(rail, [A], 'after stage change');
  assert.ok(!/Members/.test(rail.textContent));
});

test('members open for A: a sub-tab change clears the rail', async () => {
  const { document, app, A, rail } = await setUp();
  await openMembersFor(app, document, A);
  await clickSubTab(document, 'survey');
  assert.equal(app.state.subTab, 'survey');
  assertNoOtherEvidence(rail, [A], 'after sub-tab change');
  assert.ok(!/Members/.test(rail.textContent));
});

// ── Writer 2: showEvidence, through the Questions pane's real link ────────
async function openQuestionEvidence(document, A) {
  const link = document.querySelector('#content button[data-evidence]');
  assert.ok(link, `precondition: Questions pane must offer an evidence link; pane: ${document.getElementById('content').textContent.slice(0, 200)}`);
  link.click();
  await wait();
  const r = document.getElementById('rail-evidence');
  assert.ok(r.textContent.includes(A), `precondition: Evidence for ${A} showing; got ${r.textContent.slice(0, 100)}`);
  assert.ok(/Evidence/.test(r.textContent));
}

test('question evidence open for A: selecting B clears the rail', async () => {
  const { document, A, B, rail } = await setUp();
  await openQuestionEvidence(document, A);
  await clickResource(document, B);
  assertNoOtherEvidence(rail, [A, `headline-for-${A}`], 'after selecting B');
});

test('question evidence open for A: a stage change clears the rail', async () => {
  const { document, A, rail } = await setUp();
  await openQuestionEvidence(document, A);
  await clickStage(document, 'discovery');
  assertNoOtherEvidence(rail, [A, `headline-for-${A}`], 'after stage change');
});

test('question evidence open for A: a sub-tab change clears the rail', async () => {
  const { document, A, rail } = await setUp();
  await openQuestionEvidence(document, A);
  await clickSubTab(document, 'survey');
  assertNoOtherEvidence(rail, [A, `headline-for-${A}`], 'after sub-tab change');
});

// ── Writer 3: Context's own rail (Enrichment) ─────────────────────────────
test('Context rail for A: selecting B shows B (or loading), never A', async () => {
  const { document, A, B, rail } = await setUp({ stage: 'enrichment', subTab: 'context' });
  assert.ok(rail.textContent.includes(A), `precondition: Context rail names ${A}; got ${rail.textContent.slice(0, 100)}`);
  await clickResource(document, B);
  assertNoOtherEvidence(rail, [A], 'after selecting B');
  assert.ok(rail.textContent.includes(B), `Context's rail must now be B's; got ${rail.textContent.slice(0, 100)}`);
});

test('Context rail for A: leaving Enrichment clears it', async () => {
  const { document, rail, A } = await setUp({ stage: 'enrichment', subTab: 'context' });
  assert.ok(rail.textContent.includes(A));
  await clickStage(document, 'discovery');
  assert.equal(rail.textContent.trim(), '', `rail must be empty off Context; got ${rail.textContent.slice(0, 100)}`);
});

test('Context rail: leave and return on the SAME resource repopulates it for that resource', async () => {
  const { document, rail, A } = await setUp({ stage: 'enrichment', subTab: 'context' });
  await clickStage(document, 'discovery');
  await clickStage(document, 'enrichment');
  assert.ok(/Evidence · enrichment/.test(rail.textContent) && rail.textContent.includes(A),
    `Context must write its rail again; got ${JSON.stringify(rail.textContent.slice(0, 100))}`);
});

// ── Late async responses ──────────────────────────────────────────────────
test('late members response for A arriving after selecting B is dropped', async () => {
  const d = deferred();
  const { document, app, A, B, rail } = await setUp();
  // arm the gate now that the pane is up
  const gate = { re: /\/members/, promise: d.promise };
  stubServer({ gate });
  const p = app.openMembers({ slug: A, analysisId: 'a1', title: 'A1' });   // not awaited: in flight
  await wait(20);
  await clickResource(document, B);
  assertNoOtherEvidence(rail, [A], 'before the late response');
  d.resolve();
  await p;
  await wait();
  assertNoOtherEvidence(rail, [A, 'Members'], 'after the late response for A landed');
});

test('late Context render for A after a STAGE change (same resource) does not write the rail', async () => {
  const d = deferred();
  const { document, app, A, rail } = await setUp({ stage: 'discovery', subTab: 'questions' });
  stubServer({ gate: { re: /\/api\/context\//, promise: d.promise } });
  document.querySelector('#intent-nav button[data-stage="enrichment"]').click();
  await wait(30);
  await clickStage(document, 'discovery');
  assert.equal(app.state.stage, 'discovery');
  d.resolve();
  await wait(300);
  assert.equal(rail.textContent.trim(), '', `a Context fetch landing on Discovery must not write the rail; got ${rail.textContent.slice(0, 100)}`);
  assert.ok(A);
});

test('late question-evidence click state: showEvidence for A, then B, never leaves A', async () => {
  const { document, A, B, rail } = await setUp();
  await openQuestionEvidence(document, A);
  await clickResource(document, B);
  await wait(300);   // B's questions/answers land
  assertNoOtherEvidence(rail, [A], 'after B loaded');
});

// ── The legitimate case must survive ──────────────────────────────────────
test('staying on A: re-selecting A, re-entering the same stage and a no-op re-render keep the rail', async () => {
  const { document, app, A, rail } = await setUp();
  await openMembersFor(app, document, A);
  await clickResource(document, A);
  assert.ok(rail.textContent.includes(`member-of-${A}`), 're-selecting the same resource must keep the rail');
  await clickStage(document, 'scouting');
  assert.ok(rail.textContent.includes(`member-of-${A}`), 'clicking the current stage must keep the rail');
});

test('Context for A: a same-resource re-render (what a save does) keeps its rail', async () => {
  const { document, rail, A } = await setUp({ stage: 'enrichment', subTab: 'context' });
  await clickStage(document, 'enrichment');
  assert.ok(/Evidence · enrichment/.test(rail.textContent) && rail.textContent.includes(A));
});

test('KNOWN-NEGATIVE: with the rail never opened, a resource change leaves it empty', async () => {
  const { document, app, B, rail } = await setUp();
  assert.equal(rail.textContent.trim(), '');
  await clickResource(document, B);
  assert.equal(app.state.selectedSlug, B);
  assert.equal(rail.textContent.trim(), '');
});

test('KNOWN-NEGATIVE: the assertion bites -- rail text that names A fails assertNoOtherEvidence', () => {
  const { document } = makeDomEnvironment();
  const r = document.createElement('div');
  r.textContent = 'Members for alpha_x';
  assert.throws(() => assertNoOtherEvidence(r, ['alpha_x'], 'self-check'), /rail still names/);
});

// ── The header cannot name another resource ───────────────────────────────
test('rail headers: ASK scope and the evidence frame name the selected resource after every change', async () => {
  const { document, app, A, B, rail } = await setUp();
  const scope = () => document.getElementById('rail-scope').textContent;
  await clickResource(document, B);
  assert.ok(scope().includes(B), `scope line must name ${B}; got ${scope()}`);
  await openMembersFor(app, document, B);
  assert.ok(rail.textContent.includes(`for ${B}`));
  await clickResource(document, A);
  assert.ok(scope().includes(A) && !scope().includes(B), `scope line must follow to ${A}; got ${scope()}`);
  assert.ok(!rail.textContent.includes(B));
});

test('rail tag: whenever the rail has content, it is tagged for the selected resource', async () => {
  const { document, app, A, B, rail } = await setUp();
  await openMembersFor(app, document, A);
  assert.equal(rail.dataset.railFor, A);
  await clickResource(document, B);
  assert.equal(rail.dataset.railFor, undefined, 'a cleared rail carries no tag');
  await openQuestionEvidence(document, B);
  assert.equal(rail.dataset.railFor, B);
  assert.equal(rail.dataset.railFor, app.state.selectedSlug);
});
