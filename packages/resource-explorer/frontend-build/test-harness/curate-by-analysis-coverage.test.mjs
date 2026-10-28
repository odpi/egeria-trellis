/** Curate's "By analysis" tab renders the Analytic Library's curation_coverage (extensibility brief 2b):
 *  the routine's three groups (published / not yet published / blocked), the envelope state as cue plus word,
 *  and for not_established / not_applicable the state and reason with NO table of zeros. The placeholder
 *  ("Under construction") is gone. Other stages keep the real By-analysis pane. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const line = (key, label, count, names = []) => ({ key, label, count, names, rows: {} });
const MEASURED = {
  resource_type: 'database', slug: 'coco', as_of: '2026-10-09T17:51:36Z', known_schemas: 8,
  envelope: { result_state: 'measured', result_state_reason: '' },
  groups: [
    { key: 'published', words: 'published · 2 schemas · 5 tables', lines: [line('schemas', 'schemas', 2, ['hr', 'sales']), line('tables', 'tables', 5), line('report', 'report', 1, ['76 annotations'])] },
    { key: 'not_yet', words: 'not yet published · 4', lines: [line('undecided', 'undecided', 2, ['a', 'b']), line('chosen', 'chosen by a person, not published', 1), line('chosen_confirmed', 'chosen by a confirmed proposal, not published', 0), line('left_out', 'left out by a person', 1), line('left_out_confirmed', 'left out by a confirmed proposal', 0)] },
    { key: 'blocked', words: 'blocked · 1', lines: [line('blocked', 'blocked (ISSUE-117, kept not sent)', 1, ['blk']), line('superseded', 'outbox writes superseded', 2)] },
  ],
};

async function mount(resourceType, stage, body) {
  const { document } = makeDomEnvironment();
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (body === 'fail') return { ok: false, status: 500, json: async () => ({}), text: async () => 'boom' };
    return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
  };
  const app = await loadAppModule();
  const content = document.createElement('div');
  content.id = 'content';
  document.body.appendChild(content);
  app.state.resourceType = resourceType;
  app.state.selectedSlug = 'coco';
  app.state.stage = stage;
  app.state.subTab = 'by_analysis';
  app.state.analyses = [];
  return { app, content, calls };
}

test('measured: the three groups, their counts and the read-back time are drawn from the routine result', async () => {
  const { app, content, calls } = await mount('db', 'curate', MEASURED);
  await app.loadByAnalysisPane();
  assert.deepEqual(calls, ['/api/analytics/curation-coverage/database/coco']);
  assert.doesNotMatch(content.textContent, /Under construction/);
  const root = content.querySelector('[data-coverage]');
  assert.equal(root.getAttribute('data-coverage-state'), 'measured');
  assert.ok(root.querySelector('[data-cue="measured"]'), 'the state is a cue');
  assert.match(root.querySelector('[data-cue="measured"]').textContent, /measured/);
  for (const g of ['published', 'not_yet', 'blocked']) assert.ok(root.querySelector(`[data-coverage-group="${g}"]`), g);
  const n = (k) => root.querySelector(`[data-coverage-line="${k}"]`).getAttribute('data-count');
  assert.equal(n('schemas'), '2'); assert.equal(n('tables'), '5'); assert.equal(n('undecided'), '2');
  assert.equal(n('blocked'), '1'); assert.equal(n('superseded'), '2');
  assert.match(root.querySelector('[data-coverage-as-of]').textContent, /read back 2026-10-09/);
});

for (const [state, reason] of [['not_established', 'the registry did not answer'], ['not_applicable', 'curation coverage is measured for databases in this release; repo has no curation record it reads']]) {
  test(`${state}: its state and reason, never a table of zeros`, async () => {
    const { app, content } = await mount('repo', 'curate', {
      resource_type: 'repo', slug: 'coco', groups: [], envelope: { result_state: state, result_state_reason: reason } });
    await app.loadByAnalysisPane();
    const root = content.querySelector('[data-coverage]');
    assert.equal(root.getAttribute('data-coverage-state'), state);
    assert.ok(root.querySelector(`[data-cue="${state}"]`));
    assert.match(root.querySelector('[data-coverage-reason]').textContent, new RegExp(reason.slice(0, 20)));
    assert.equal(root.querySelectorAll('[data-coverage-line]').length, 0, 'no counts');
    assert.doesNotMatch(root.textContent, /\b0\b/);
  });
}

test('a failed read says it could not read, not zero and not construction', async () => {
  const { app, content } = await mount('db', 'curate', 'fail');
  await app.loadByAnalysisPane();
  const root = content.querySelector('[data-coverage]');
  assert.equal(root.getAttribute('data-coverage-state'), 'not_established');
  assert.match(root.textContent, /could not be read/);
  assert.equal(root.querySelectorAll('[data-coverage-line]').length, 0);
});

test('another stage still gets the real By analysis pane and does not read the coverage', async () => {
  const { app, content, calls } = await mount('db', 'scouting', { boards: [], questions: [] });
  app.state.analyses = [{ id: 'b1', name: 'Board One', description: '', intent: 'scouting' }];
  app.loadByAnalysisPane();
  assert.equal(content.querySelector('[data-coverage]'), null);
  assert.ok(!calls.some((c) => c.includes('curation-coverage')));
});
