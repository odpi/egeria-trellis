/** Behaviour: Admin → Feedback diagnosis (docs/feedback-closed-loop-design.md §6 step 1). A rated answer carries a
 *  cause menu; Save sends a cause only when it changed; the evidence panel shows the suggestion with its rule and
 *  "Use" sets the menu without saving; a save with nothing changed is refused on the row; another row's Save keeps
 *  what was typed elsewhere; the by-cause summary reads /api/feedback/causes. Real module; only fetch is stubbed. */
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
        cause: 'stale', secondary_cause: '' } },
    { id: 'p1', source: 'page', page: '/scouting', category: 'bug', message: 'broken', rating: null,
      created_at: '2026-11-09T10:00:00+00:00', triage_status: 'new',
      triage: { status: 'new', note: '', actor: '', updated_at: '', events: 0 } },
  ],
  counts: { resource: { total: 0, resources: 0 }, page: { total: 1 }, answer: { total: 1 } },
  filtered: false,
  statuses: ['new', 'triaged', 'known_issue', 'actioned', 'deferred', 'not_an_issue'],
  causes: CAUSES, cause_chain: CHAIN, undiagnosed: 'undiagnosed',
};

const SUMMARY = {
  vote: -1, counts: { stale: 1, undiagnosed: 2 }, causes: CAUSES, undiagnosed: 'undiagnosed',
  by_analysis: [{ analysis_id: 'deps', total: 1, causes: { stale: 1 }, roles: { FULL: 1 } }], uncharged: 0, read: 3, limited: false,
};

const DIAGNOSIS = {
  id: 'a1',
  compile: { compile_id: 'c1', question: 'what does it use?', slug: 'r1', created_at: '', text: '## deps\nrequests',
    manifest: { coverage: { kind: 'analysis', question: 'Which dependencies?' }, packed: [{ key: 'deps', rung: 'IDENTIFIERS' }],
      gaps: [{ key: 'cve_scan', state: 'never_run', reason: 'has not run' }], dropped: [], deferred: [] } },
  suggestions: [{ cause: 'missing_evidence', sections: ['cve_scan'], rule: 'had no result: cve_scan (has not run)' },
    { cause: 'over_compressed', sections: ['deps'], rule: 'deps was packed at identifiers only' }],
  not_judged: [{ cause: 'stale', why: 'no packed section declares an as-of date' }],
  charge_set: [{ analysis_id: 'cve_scan', role: 'absent', words: 'had no result' }],
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
  ['/api/feedback/causes', { reply: () => ok(SUMMARY) }],
  ['/api/feedback/a1/diagnosis', { reply: () => ok(DIAGNOSIS) }],
];

test('a rated answer has a cause menu set to its cause; a page report has none', async () => {
  const { host, pane } = await setUp(BASE);
  await pane.renderFeedback(host);
  const answer = host.querySelector('[data-triage="a1"]');
  assert.equal(answer.querySelector('[data-triage-cause]').value, 'stale');
  assert.deepEqual([...answer.querySelectorAll('[data-triage-cause] option')].map((o) => o.value), ['', ...CAUSES]);
  assert.deepEqual([...answer.querySelectorAll('[data-triage-secondary] option')].map((o) => o.value), ['', ...CHAIN]);
  assert.match(text(answer), /cause: stale/);
  assert.equal(host.querySelector('[data-triage="p1"] [data-triage-cause]'), null);
});

test('Save sends the cause only when it changed', async () => {
  const { host, pane, calls } = await setUp([...BASE,
    ['/api/feedback/a1', { method: 'PATCH', reply: () => ok({ id: 'a1' }) }]]);
  await pane.renderFeedback(host);
  let cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-status]').value = 'actioned';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').at(-1).body, { triage_status: 'actioned', note: '' });
  cell = host.querySelector('[data-triage="a1"]');
  cell.querySelector('[data-triage-cause]').value = 'wrong_evidence';
  cell.querySelector('[data-triage-secondary]').value = 'reasoning';
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.deepEqual(calls.filter((c) => c.method === 'PATCH').at(-1).body,
    { triage_status: 'triaged', note: '', cause: 'wrong_evidence', secondary_cause: 'reasoning' });
});

test('a save with nothing changed is refused on the row and sends nothing', async () => {
  const { host, pane, calls } = await setUp(BASE);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="p1"]');
  cell.querySelector('[data-triage-save]').click();
  await settle();
  assert.match(text(cell.querySelector('[data-triage-error]')), /Nothing to save/);
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 0);
});

test("another row's Save keeps what was typed in this one", async () => {
  const { host, pane } = await setUp([...BASE,
    ['/api/feedback/p1', { method: 'PATCH', reply: () => ok({ id: 'p1' }) }]]);
  await pane.renderFeedback(host);
  host.querySelector('[data-triage="a1"] [data-triage-note]').value = 'half typed';
  host.querySelector('[data-triage="a1"] [data-triage-cause]').value = 'reasoning';
  const page = host.querySelector('[data-triage="p1"]');
  page.querySelector('[data-triage-status]').value = 'triaged';
  page.querySelector('[data-triage-save]').click();
  await settle();
  const answer = host.querySelector('[data-triage="a1"]');
  assert.equal(answer.querySelector('[data-triage-note]').value, 'half typed');
  assert.equal(answer.querySelector('[data-triage-cause]').value, 'reasoning');
  assert.equal(host.querySelector('[data-triage="p1"] [data-triage-status]').value, 'new', 'the saved row shows the store, not its draft');
});

test('the evidence panel shows the suggestion with its rule, and Use sets the menu without saving', async () => {
  const { host, pane, calls } = await setUp(BASE);
  await pane.renderFeedback(host);
  const cell = host.querySelector('[data-triage="a1"]');
  const box = cell.querySelector('[data-diagnosis]');
  box.open = true;
  box.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  await settle();
  const body = text(box.querySelector('[data-diagnosis-body]'));
  assert.match(body, /Suggested: missing evidence — had no result: cve_scan \(has not run\)/);
  assert.match(body, /Also signalled: over-compressed/);
  assert.match(body, /Charged to: cve_scan had no result/);
  assert.match(body, /Not judged: stale — no packed section declares an as-of date/);
  box.querySelector('[data-use-cause="missing_evidence"]').click();
  assert.equal(cell.querySelector('[data-triage-cause]').value, 'missing_evidence');
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 0, 'Use must not record a diagnosis');
});

test('with nothing suggested, a cause that was not judged is not said to be absent', async () => {
  const { host, pane } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/causes', { reply: () => ok(SUMMARY) }],
    ['/api/feedback/a1/diagnosis', { reply: () => ok({ ...DIAGNOSIS, suggestions: [] }) }],
  ]);
  await pane.renderFeedback(host);
  const box = host.querySelector('[data-triage="a1"] [data-diagnosis]');
  box.open = true;
  box.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  await settle();
  const body = text(box.querySelector('[data-diagnosis-body]'));
  assert.match(body, /signals none of catalog gap, missing evidence, over-compressed\./);
  assert.match(body, /Not judged: stale/);
});

test('a cause judged for some sections only is still listed, and its Not judged line names the rest', async () => {
  const partial = { cause: 'stale', partial: true, why: '2 of 3 sections have no as-of date (ci, docs), so their ages were not judged' };
  const { host, pane } = await setUp([
    ['/api/curate/feedback', { reply: () => ok(LISTING) }],
    ['/api/feedback/causes', { reply: () => ok(SUMMARY) }],
    ['/api/feedback/a1/diagnosis', { reply: () => ok({ ...DIAGNOSIS, suggestions: [], not_judged: [partial] }) }],
  ]);
  await pane.renderFeedback(host);
  const box = host.querySelector('[data-triage="a1"] [data-diagnosis]');
  box.open = true;
  box.dispatchEvent(new host.ownerDocument.defaultView.Event('toggle'));
  await settle();
  const body = text(box.querySelector('[data-diagnosis-body]'));
  assert.match(body, /signals none of catalog gap, missing evidence, stale, over-compressed\./);
  assert.match(body, /Not judged: stale — 2 of 3 sections have no as-of date \(ci, docs\)/);
});

test('the summary vote toggle asks for partly-right votes', async () => {
  const { host, pane, calls } = await setUp(BASE);
  await pane.renderFeedback(host);
  assert.ok(calls.some((c) => c.url === '/api/feedback/causes?vote=-1'));
  host.querySelector('[data-summary-vote="0"]').click();
  await settle();
  assert.ok(calls.some((c) => c.url === '/api/feedback/causes?vote=0'));
});

test('the summary counts thumbs-down by cause and by analysis; the cause filter asks the server', async () => {
  const { host, pane, calls } = await setUp(BASE);
  await pane.renderFeedback(host);
  const summary = host.querySelector('[data-cause-summary]');
  assert.match(text(summary), /stale 1 · undiagnosed 2/);
  assert.ok(summary.querySelector('[data-cause-analysis="deps"]'));
  host.querySelector('[data-filter-key="cause"][data-filter-val="undiagnosed"]').click();
  await settle();
  assert.ok(calls.some((c) => c.url.includes('cause=undiagnosed')));
});
