/** Behaviour: Admin → Feedback routing (closed-loop step 2a, BRIEF-CLOSED-LOOP-STEP2-ROUTING.md). Each answer row
 *  lists where its diagnoses were routed; the evidence panel offers the charged analyses when there is a choice, and
 *  Save sends the one picked with a cause; a route that is not stored (not routed yet) is said once after Save.
 *  Real module; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const settle = async () => { for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0)); };
const CHAIN = ['catalog_gap', 'missing_evidence', 'stale', 'analysis_defect', 'wrong_evidence', 'over_compressed', 'reasoning'];
const CAUSES = [...CHAIN, 'unclear_question', 'not_an_issue'];

const LISTING = {
  feedback: [
    { id: 'a1', source: 'answer', vote: -1, query_text: 'what does it use?', response_text: 'requests', compile_id: 'c1',
      created_at: '2026-11-10T09:00:00+00:00', triage_status: 'triaged',
      triage: { status: 'triaged', note: '', actor: 'erinoverview', updated_at: '2026-11-10T10:00:00+00:00', events: 1,
        cause: 'missing_evidence', secondary_cause: '' },
      routes: [{ cause: 'missing_evidence', consumer: 'gap', target: '12', words: 'Routed: gap #12 on cve_scan (missing evidence).' }] },
  ],
  counts: { resource: { total: 0, resources: 0 }, page: { total: 0 }, answer: { total: 1 } },
  filtered: false,
  statuses: ['new', 'triaged', 'known_issue', 'actioned', 'deferred', 'not_an_issue'],
  causes: CAUSES, cause_chain: CHAIN, undiagnosed: 'undiagnosed',
};

const DIAGNOSIS = {
  id: 'a1',
  compile: { compile_id: 'c1', question: 'what does it use?', slug: 'r1', created_at: '', text: '',
    manifest: { coverage: { kind: 'analysis', question: 'Which dependencies?' }, packed: [{ key: 'deps', rung: 'FULL' }],
      gaps: [{ key: 'cve_scan', state: 'never_run', reason: 'has not run' }], dropped: [], deferred: [] } },
  suggestions: [], not_judged: [],
  charge_set: [{ analysis_id: 'cve_scan', role: 'absent', words: 'had no result' },
    { analysis_id: 'deps', role: 'FULL', words: 'packed in full' }],
};

async function setUp(routes) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    for (const [prefix, answer] of routes) {
      if (u.startsWith(prefix) && (answer.method || 'GET') === (opts.method || 'GET')) return answer.reply(u, opts);
    }
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.sessionStorage = window.sessionStorage;
  const pane = await import('/static/next/admin/feedback.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, pane, calls };
}

const BASE = [
  ['/api/curate/feedback', { reply: () => ok(LISTING) }],
  ['/api/feedback/causes', { reply: () => ok({ vote: -1, counts: {}, causes: CAUSES, by_analysis: [], uncharged: 0, read: 0 }) }],
  ['/api/feedback/a1/diagnosis', { reply: () => ok(DIAGNOSIS) }],
];

async function openEvidence(host) {
  const box = host.querySelector('[data-triage="a1"] [data-diagnosis]');
  box.open = true;
  box.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  await settle();
}

test('each answer row lists where its diagnoses were routed', async () => {
  const { host, pane } = await setUp(BASE);
  await pane.renderFeedback(host);
  const line = host.querySelector('[data-triage="a1"] [data-route="missing_evidence"]');
  assert.equal(text(line), 'Routed: gap #12 on cve_scan (missing evidence). Re-route');
  assert.ok(line.querySelector('[data-reroute="missing_evidence"]'), 'a gap route can be re-routed');
});

test('the evidence panel offers the charged analyses, and Save sends the one picked with the cause', async () => {
  const { host, pane, calls } = await setUp([...BASE,
    ['/api/feedback/a1', { method: 'PATCH', reply: () => ok({ id: 'a1', route: { state: 'routed', words: 'x' } }) }]]);
  await pane.renderFeedback(host);
  await openEvidence(host);
  const cell = host.querySelector('[data-triage="a1"]');
  const picker = cell.querySelector('[data-route-analysis]');
  assert.deepEqual([...picker.querySelectorAll('option')].map((o) => o.value), ['cve_scan', 'deps']);
  picker.value = 'deps';
  picker.dispatchEvent(new host.ownerDocument.defaultView.Event('change'));   // a pick the admin made
  cell.querySelector('[data-triage-cause]').value = 'analysis_defect';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').at(-1).body,
    { triage_status: 'triaged', note: '', cause: 'analysis_defect', secondary_cause: '', analysis_id: 'deps' });
});

test('a status-only Save sends no analysis', async () => {
  const { host, pane, calls } = await setUp([...BASE,
    ['/api/feedback/a1', { method: 'PATCH', reply: () => ok({ id: 'a1' }) }]]);
  await pane.renderFeedback(host);
  await openEvidence(host);
  const cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-status]').value = 'actioned';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').at(-1).body, { triage_status: 'actioned', note: '' });
});

test('a route that is not stored is said once after Save', async () => {
  const { host, pane } = await setUp([...BASE,
    ['/api/feedback/a1', { method: 'PATCH',
      reply: () => ok({ id: 'a1', route: { state: 'skipped', words: 'Not routed yet: stale is routed by step 2c (Automate).' } }) }]]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-cause]').value = 'stale';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.equal(text(host.querySelector('[data-triage="a1"] [data-route-note]')),
    'Not routed yet: stale is routed by step 2c (Automate).');
  await pane.renderFeedback(host);
  assert.equal(host.querySelector('[data-triage="a1"] [data-route-note]'), null);
});

test('picking a charged cause says which analysis it will charge before Save', async () => {
  const listing = { ...LISTING, feedback: [{ ...LISTING.feedback[0], routes: [],
    triage: { ...LISTING.feedback[0].triage, cause: 'reasoning' } }] };
  const { host, pane } = await setUp([['/api/curate/feedback', { reply: () => ok(listing) }], ...BASE.slice(1)]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  const cause = cell.querySelector('[data-triage-cause]');
  cause.value = 'missing_evidence';
  cause.dispatchEvent(new host.ownerDocument.defaultView.Event('change'));
  await settle();
  assert.equal(text(cell.querySelector('[data-route-default]')),
    'Will charge: cve_scan (default); pick another in the evidence panel.');
  const picker = cell.querySelector('[data-route-analysis]');
  picker.value = 'deps';
  picker.dispatchEvent(new host.ownerDocument.defaultView.Event('change'));
  await settle();
  assert.equal(text(cell.querySelector('[data-route-default]')), 'Will charge: deps.');
  cause.value = 'reasoning';
  cause.dispatchEvent(new host.ownerDocument.defaultView.Event('change'));
  await settle();
  assert.equal(text(cell.querySelector('[data-route-default]')), '');
});

test('Re-route posts the analysis picked in the evidence panel', async () => {
  const { host, pane, calls } = await setUp([...BASE,
    ['/api/feedback/a1/routes/missing_evidence/reroute', { method: 'POST', reply: () => ok({ state: 'routed' }) }]]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-reroute="missing_evidence"]').click();
  await settle();
  assert.match(text(cell.querySelector('[data-triage-error]')), /Pick the analysis in the evidence panel/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  cell.querySelector('[data-route-analysis]').value = 'deps';
  cell.querySelector('[data-reroute="missing_evidence"]').click();
  await settle();
  const post = calls.filter((c) => c.method === 'POST').at(-1);
  assert.equal(post.url, '/api/feedback/a1/routes/missing_evidence/reroute');
  assert.deepEqual(post.body, { analysis_id: 'deps' });
});

test('an untouched picker sends no analysis, so the server keeps its own default', async () => {
  const { host, pane, calls } = await setUp([...BASE,
    ['/api/feedback/a1', { method: 'PATCH', reply: () => ok({ id: 'a1' }) }]]);
  await pane.renderFeedback(host);
  await openEvidence(host);
  const cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-cause]').value = 'stale';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').at(-1).body,
    { triage_status: 'triaged', note: '', cause: 'stale', secondary_cause: '' });
});

test('stale says which analysis it will propose a schedule for: the stale section first', async () => {
  const diag = { ...DIAGNOSIS, suggestions: [{ cause: 'stale', sections: ['deps'], rule: 'deps is as of 2026-01-01' }] };
  const listing = { ...LISTING, feedback: [{ ...LISTING.feedback[0], routes: [],
    triage: { ...LISTING.feedback[0].triage, cause: 'reasoning' } }] };
  const { host, pane } = await setUp([['/api/curate/feedback', { reply: () => ok(listing) }],
    BASE[1], ['/api/feedback/a1/diagnosis', { reply: () => ok(diag) }]]);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  const cause = cell.querySelector('[data-triage-cause]');
  cause.value = 'stale';
  cause.dispatchEvent(new host.ownerDocument.defaultView.Event('change'));
  await settle();
  assert.equal(text(cell.querySelector('[data-route-default]')),
    'Will propose a schedule for: deps (default); pick another in the evidence panel.');
});
