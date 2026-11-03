/** re/next-batch-ui: four front-end honesty fixes.
 *  1 connection popover wording; 2 By-analysis card header time agrees with
 *  the body cue; 3 the chat rail folds turns for other resources; 4 a chat
 *  turn never lists compile-record field names as its source or answer. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function dom() {
  const env = makeDomEnvironment();
  ensureLoaderRegistered();
  env.window.matchMedia = env.window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  return env;
}

test('1 connection popover: what you start runs as you; scheduled and queued work runs as the service account', async () => {
  const { document } = await dom();
  const mod = await import(`/static/next/shell-status.js?t=${Math.random()}`);
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  await mod.openConnectionPopover(document, anchor, {
    me: { user_id: 'dan' }, getInfo: async () => ({ user_id: 'svc' }), getStatus: async () => null,
  });
  const note = [...document.querySelectorAll('#connection-popover p')].map((p) => p.textContent).join('|');
  assert.equal(note, 'What you start runs as you in Egeria; scheduled and queued work runs as the service account, recorded as yours.');
});

test('2 By-analysis header: run time only when the card has its own result; the cue and the time agree', async () => {
  await dom();
  const app = await import('/static/next/app.js');
  const ago = (await import('/static/next/format.js')).ago;
  assert.equal(typeof app.boardRunWords, 'function');
  const surveyed = '2026-10-04T10:00:00Z';
  // survey ran, this step was not in it: the header says when the survey was, not that this ran
  assert.equal(app.boardRunWords({ has_results: false, result_state: 'not_requested', last_surveyed_at: surveyed, last_run_at: '' }),
    `last survey ${ago(surveyed)}`);
  // never surveyed at all: the body says "not run yet"; the header says nothing more
  assert.equal(app.boardRunWords({ has_results: false, result_state: 'never_surveyed', last_surveyed_at: '', last_run_at: '' }), '');
  // has a result: its own run time wins over the resource's survey time
  const own = '2026-10-10T10:00:00Z';
  assert.equal(app.boardRunWords({ has_results: true, last_surveyed_at: surveyed, last_run_at: own }), `run ${ago(own)}`);
  // and the card never says "run <when>" beside the not-run body
  const html = app.byAnalysisCardHtml('s', 'activity_signals', 'Activity Signals', '',
    { status: 'done', board: { id: 'activity_signals', has_results: false, result_state: 'not_requested', analyses: [], last_surveyed_at: surveyed } }, new Set(), true);
  assert.ok(!html.includes('Not yet run.'), '"Not yet run." is only for a resource never surveyed');
  assert.ok(!/>\s*run \d/.test(html) && !html.includes('run 8d'), 'no "run <when>" next to the not-run cue');
  assert.ok(html.replace(/&#39;|&#x27;|&apos;/g, "'").includes("its step wasn't requested"));
});

function chatEnv() { return dom().then(async () => {
  const app = await import('/static/next/app.js');
  const chat = await import('/static/next/chat.js');
  const document = globalThis.document;
  for (const id of ['rail', 'rail-scope']) { if (!document.getElementById(id)) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); } }
  return { app, chat, document };
}); }

test('3 chat rail: turns for another resource are folded into one closed group, not shown inline', async () => {
  const { app, chat, document } = await chatEnv();
  app.state.selectedSlug = 'laz_local_usda';
  app.state.chat = [
    { question: 'what does coco_pharma do?', slug: 'coco_pharma', pending: false, answer: 'x', source: 's' },
    { question: 'what is usda?', slug: 'laz_local_usda', pending: false, answer: 'y', source: 's' },
  ];
  app.state.promoted = null;
  chat.renderRail();
  const log = document.getElementById('chat-log');
  const group = log.querySelector('details[data-other-resources]');
  assert.ok(group, 'other-resource turns sit in a group');
  assert.equal(group.hasAttribute('open'), false);
  assert.equal(group.querySelector('summary').textContent, '1 turn on other resources');
  const inline = [...log.querySelectorAll('[data-open-turn]')].filter((b) => !group.contains(b));
  assert.equal(inline.length, 1);
  assert.ok(inline[0].textContent.includes('what is usda?'));
  assert.ok(!inline[0].textContent.includes('coco_pharma'));
  assert.equal(group.querySelector('[data-open-turn]').dataset.openTurn, '0');   // index into state.chat is preserved
});

test('4 chat turn: a compile record never lists its field names; a turn with no answer says so', async () => {
  const { app, chat, document } = await chatEnv();
  const manifest = { compile_id: 'c1', resource_type: 'database', spec_id: 's', budget: 1, used: 1, headroom: 0,
    packed: [{ key: 'schema_inventory', role: 'evidence' }, { key: 'schema_inventory', role: 'evidence' }, { key: 'db_fingerprint', role: 'evidence' }],
    lists: {}, coverage: {}, instructions_variant: 'v', gaps: [], recorded: true };
  const line = chat.sourceLine({ compiled: { manifest } });
  assert.equal(line, 'From 2 stored results · schema_inventory, db_fingerprint');
  for (const k of ['compile_id', 'headroom', 'instructions_variant']) assert.ok(!line.includes(k));
  // an older manifest with no packed list: say sources are not listed, never list the keys
  const old = chat.sourceLine({ compiled: { manifest: { compile_id: 'c1', resource_type: 'database' } } });
  assert.equal(old, 'Compiled evidence is on file · its sources are not listed');
  // a finished turn with neither text nor error
  app.state.selectedSlug = 'd';
  app.state.chat = [
    { question: 'q1', slug: 'd', pending: false, answer: '' },
    { question: 'q2', slug: 'd', pending: false, answer: '', error: 'The question could not be asked: 500' },
  ];
  app.state.promoted = null;
  chat.renderRail();
  const turns = [...document.querySelectorAll('#chat-log [data-open-turn]')];
  assert.equal(turns[0].querySelector('[data-turn-no-answer]').textContent, 'no answer recorded');
  assert.equal(turns[1].querySelector('[data-turn-failed]').textContent, 'failed · The question could not be asked: 500');
});
