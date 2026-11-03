/** Real-DOM regression test for ENRICHMENT-E0-ROW-ANATOMY
 *  (docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md §0.3/§6 item 1,
 *  ENRICHMENT-E0-ROW-ANATOMY-IMPLEMENTED.md).
 *
 *  THE PROBLEM: a person's input to a resource lived in two stores with two
 *  different rules. Enrichment's judgements/observations
 *  (`state.enrichment` via `saveEnrichmentField`, rendered by
 *  `stages/enrichment.js`'s `fieldRowHtml`) always carried who, when, and
 *  the "⚠ review — evidence moved: X" flag. The Questions tab's answers to
 *  the catalog's human questions (`state.contextAnswers` via
 *  `saveQuestionAnswer`, rendered by `app.js`'s `rowInner`/`bodyLines`)
 *  carried only a timestamp — no author at all — entered through a
 *  blocking `window.prompt()`.
 *
 *  THE FIX: `row-anatomy.js`'s `personRowLineHtml` is the ONE function that
 *  builds the who + when + evidence-moved-flag line. `fieldRowHtml` (judge-
 *  ments/observations) and `rowInner`'s `st === 'human'` branch (question
 *  answers) both call it — this is proven below by rendering BOTH kinds of
 *  row through the REAL, unmodified functions and asserting each one's
 *  provenance line matches, character for character, what `personRowLineHtml`
 *  itself returns for the equivalent input. A test that only checked "does
 *  the text look similar" could pass with two lookalike implementations that
 *  drift apart later; matching the shared function's own output exactly is
 *  what proves they are genuinely ONE component.
 *
 *  Imports below are deliberately NOT cache-busted (contrast
 *  `dom-harness.mjs`'s `loadAppModule()`, which busts on purpose so each
 *  *.test.mjs file gets its own `state`): this test needs `app.js`,
 *  `stages/enrichment.js` and `row-anatomy.js` to resolve to the SAME module
 *  instances real `/next` uses (enrichment.js and row-anatomy.js both import
 *  `esc`/`state` from the literal specifier `/static/next/app.js` with no
 *  query string), so that setting a field on `state` here is visible inside
 *  `fieldRowHtml`.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

test('a judgement row (enrichment.js) and a question-answer row (app.js) render their provenance line through the SAME shared function', async () => {
  makeDomEnvironment();
  // `/static/...` specifiers only resolve once static-loader.mjs is
  // registered -- loadAppModule() does that as a side effect. Its own
  // (cache-busted) return value is discarded: this test needs the
  // UN-busted `/static/next/app.js` instance, the same one
  // stages/enrichment.js itself imports internally, so writes to `state`
  // here are visible there.
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const enrichment = await import('/static/next/stages/enrichment.js');
  const { personRowLineHtml } = await import('/static/next/row-anatomy.js');
  const { questionKey } = await import('/static/re-api.js');

  // ── Judgement row: sensitivity, confirmed, with evidence moved ──────────
  app.state.enrichment = {
    sensitivity: {
      value: 'confidential', kind: 'judgement', author: 'peterprofile',
      set_at: new Date().toISOString(), source: '', interim: false,
      evidence: { cve_scan: '2020-01-01T00:00:00Z' },  // older than any fact -> "moved"
    },
  };
  app.state.enrichmentFacts = { cve_scan: { last_run_at: new Date().toISOString() } };

  const judgementDef = { key: 'sensitivity', label: 'Sensitivity', options: ['public', 'internal', 'confidential', 'restricted'] };
  const judgementRowHtml = enrichment.fieldRowHtml(judgementDef, 'judgement');

  // The evidence-moved warning is said once per resource (movedSummaryHtml); the row keeps who + when through the
  // shared function and carries only a small "review" cue, never the analyses list again.
  const expectedJudgementProvenance = personRowLineHtml({
    author: 'peterprofile',
    whenIso: app.state.enrichment.sensitivity.set_at,
  });
  assert.ok(
    judgementRowHtml.includes(expectedJudgementProvenance),
    'fieldRowHtml\'s provenance line must be exactly what personRowLineHtml produces for the same input',
  );
  assert.ok(judgementRowHtml.includes('data-moved-jump'), 'the row carries the small review cue');
  assert.ok(!judgementRowHtml.includes('evidence moved: cve_scan</span>'), 'the row does not repeat the warning text');
  assert.ok(enrichment.movedSummaryHtml().includes('⚠ review — evidence moved: cve_scan'), 'the one summary names the moved analysis');

  // ── Question-answer row: a human-question answer, author-stamped ───────
  const question = 'Do we already support these dependencies?';
  app.state.contextAnswers = {
    [questionKey(question)]: {
      question, answer: 'Yes, two teams do.',
      answered_at: new Date().toISOString(), answered_by: 'peterprofile',
    },
  };
  app.state.runsInFlight = new Map();
  app.state.pendingProposals = new Map();
  app.state.editingAnswer = '';

  const entry = { question, kind: 'human', note: '', answering_mechanism: '', analysis_ids: [], perspectives: [] };
  const questionRowHtml = app.rowInner(entry, 0, {});

  const expectedAnswerProvenance = personRowLineHtml({
    author: 'peterprofile',
    whenIso: app.state.contextAnswers[Object.keys(app.state.contextAnswers)[0]].answered_at,
    verb: 'answered by',
  });
  assert.ok(
    questionRowHtml.includes(expectedAnswerProvenance),
    'the question row\'s provenance line must be exactly what personRowLineHtml produces for the same input -- ' +
    'same anatomy as the judgement row above, through the same function, not a lookalike',
  );

  // Both rows carry who AND when -- the row anatomy the design reply
  // requires (§0.3's table: judgements had both, question answers had
  // neither). Render each into a real DOM container and check visually,
  // not just in the raw HTML string.
  const { document } = makeDomEnvironment();
  const jHost = document.createElement('div');
  jHost.innerHTML = judgementRowHtml;
  document.body.appendChild(jHost);
  assert.match(jHost.textContent, /peterprofile/, 'judgement row must show WHO');
  assert.match(jHost.textContent, /just now|ago/, 'judgement row must show WHEN');

  const qHost = document.createElement('div');
  qHost.innerHTML = questionRowHtml;
  document.body.appendChild(qHost);
  assert.match(qHost.textContent, /answered by peterprofile/, 'question-answer row must show WHO, with the gate\'s exact wording');
  assert.match(qHost.textContent, /just now|ago/, 'question-answer row must show WHEN');
});

test('the gate scenario: a freshly-answered question renders "answered by <user> · just now"', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();  // a fresh, isolated instance is fine here -- single-module test

  const question = 'What does it cost to run?';
  const key = 'what-does-it-cost-to-run';
  app.state.contextAnswers = {
    [key]: { question, answer: '$200/mo', answered_at: new Date().toISOString(), answered_by: 'dan' },
  };
  app.state.runsInFlight = new Map();
  app.state.pendingProposals = new Map();
  app.state.editingAnswer = '';

  const entry = { question, kind: 'human', note: '', answering_mechanism: '', analysis_ids: [], perspectives: [] };
  const html = app.rowInner(entry, 0, {});
  // "just now" renders inside a `<span class="tnum">` (same convention every
  // other relative-time render in `/next` uses) -- match through that span
  // rather than requiring literal adjacency.
  assert.match(html, /answered by dan\s*·\s*<span class="tnum">just now<\/span>/, `gate wording not found in: ${html}`);
  assert.match(html.replace(/<[^>]+>/g, ''), /answered by dan\s*·\s*just now/, 'plain-text reading must also say it');
});

/** A minimal `Response`-shaped object -- everything `re-api.js`'s
 *  `request()` reads off a fetch response (`.ok`, `.status`, `.json()`).
 *  Same helper as `by-analysis-board-timeout.test.mjs` -- duplicated here
 *  rather than shared, matching this directory's existing convention of
 *  each `*.test.mjs` carrying its own small fetch-stubbing helpers. */
function fakeJsonResponse(data, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => data };
}

/** Flushes the microtask queue so an unawaited async click handler's chain
 *  (fetch -> .json() -> re-api.js's request()/patch() -> the handler's own
 *  `await` -> its `redrawQuestionRow` re-render) has a chance to run before
 *  assertions. Same helper and reasoning as `by-analysis-board-timeout.test.mjs`. */
function flushTasks(times = 5) {
  return new Promise((resolve) => {
    let n = 0;
    const step = () => { if (++n >= times) resolve(); else setImmediate(step); };
    setImmediate(step);
  });
}

test('clicking "Answer this ->" renders a real textarea; saving PATCHes the answer route and the row re-renders "answered by <user> · just now"', async () => {
  // THE GAP THIS CLOSES (PR #358 review): the tests above prove the shared
  // row-anatomy component renders the right text from FIXTURE state, and
  // `tests/test_no_window_prompt_for_answering_questions.py` proves
  // `window.prompt()` is gone from the source text -- but nothing actually
  // drove the real interaction: render -> click "Answer this ->" -> see a
  // textarea -> type -> save -> confirm the PATCH fires -> confirm the row
  // re-renders. This test does exactly that, through the REAL
  // `rowInner`/`wireHumanAnswers` functions app.js itself uses (the same
  // `wireHumanAnswers` `loadPane()` calls at app.js:6704), not a parallel
  // reimplementation of the click/save wiring.
  const { document } = makeDomEnvironment();

  const patchCalls = [];
  globalThis.fetch = async (url, options = {}) => {
    const u = String(url);
    if (u.includes('/api/context/') && u.endsWith('/answer') && options.method === 'PATCH') {
      const body = JSON.parse(options.body);
      patchCalls.push(body);
      // The server stamps author/date from the signed-in identity
      // (context.py's save_answer) -- the client never sends these, and
      // this stub reflects that: it returns a server-shaped QuestionAnswer
      // with an author the request body did not carry.
      return fakeJsonResponse({
        answer: {
          question: body.question,
          answer: body.answer,
          answered_at: new Date().toISOString(),
          answered_by: 'dan',
        },
      });
    }
    throw new Error(`_UNSTUBBED_FETCH in test: ${options.method || 'GET'} ${u}`);
  };

  const app = await loadAppModule();
  const question = 'Do we already support these dependencies?';
  const entry = { question, kind: 'human', note: '', answering_mechanism: '', analysis_ids: [], perspectives: [] };

  app.state.questions = [entry];
  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'test-repo';
  app.state.contextAnswers = {};
  app.state.answers = new Map();
  app.state.runsInFlight = new Map();
  app.state.pendingProposals = new Map();
  app.state.editingAnswer = '';

  const rowsHost = document.createElement('div');
  rowsHost.id = 'question-rows';
  document.body.appendChild(rowsHost);
  // Wrapper id matches app.js's own `rowKey(i)` convention (`qrow-${i}`) --
  // `replaceRow`/`redrawQuestionRow` (both internal to `wireHumanAnswers`'s
  // closure) look up the row by that id to re-render it in place. `rowInner`
  // itself is the exported render function the row-anatomy tests above
  // already use; passed env `{}` (not `'loading'`, no `__error`) so
  // `rowState()` reaches its real, unrun 'human' branch -- the state the
  // Questions tab renders a not-yet-answered human question in.
  rowsHost.innerHTML = `<div id="qrow-0">${app.rowInner(entry, 0, {})}</div>`;
  app.wireHumanAnswers(rowsHost, app.state.selectedSlug);

  assert.doesNotMatch(rowsHost.innerHTML, /<textarea/, 'no editor should be open before any click');
  const answerBtn = rowsHost.querySelector('[data-human-edit]');
  assert.ok(answerBtn, 'the "Answer this ->" control must be present');
  assert.match(answerBtn.textContent, /Answer this/);

  // Click "Answer this ->" -- a real DOM click through the real listener
  // wireHumanAnswers() attached, not a direct call into private state.
  answerBtn.click();

  const textarea = rowsHost.querySelector('[data-answer-input]');
  assert.ok(textarea, 'a textarea must appear in the DOM after clicking "Answer this ->"');
  assert.equal(textarea.tagName, 'TEXTAREA');

  // Type into it and save -- the real save control, a button click (this
  // row's save path is a click, not an Enter-to-submit form).
  textarea.value = 'Yes, two teams already do.';
  const saveBtn = rowsHost.querySelector('[data-answer-save]');
  assert.ok(saveBtn, 'a save control must be present once the editor is open');

  saveBtn.click();
  await flushTasks();

  assert.equal(patchCalls.length, 1, 'saving must PATCH the answer route exactly once');
  assert.equal(patchCalls[0].question, question);
  assert.equal(patchCalls[0].answer, 'Yes, two teams already do.', 'the PATCH body must carry the text that was typed');

  // The row must re-render afterward -- same gate wording the fixture-driven
  // tests above assert, now reached through the real save flow rather than
  // fixture state.
  const settledHtml = rowsHost.innerHTML;
  assert.doesNotMatch(settledHtml, /<textarea/, 'the editor must close after a successful save');
  assert.match(settledHtml, /answered by dan\s*·\s*<span class="tnum">just now<\/span>/, `gate wording not found after save in: ${settledHtml}`);
  assert.match(settledHtml.replace(/<[^>]+>/g, ''), /answered by dan\s*·\s*just now/);
  assert.match(settledHtml, /Yes, two teams already do\./, 'the saved answer text itself must also render');
});

test('a legacy answer with no recorded author degrades gracefully (no blank "· 2d ago" author slot)', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();

  const question = 'Does it fit our estate?';
  const key = 'does-it-fit-our-estate';
  app.state.contextAnswers = {
    [key]: { question, answer: 'Yes.', answered_at: new Date().toISOString() },  // no answered_by
  };
  app.state.runsInFlight = new Map();
  app.state.pendingProposals = new Map();
  app.state.editingAnswer = '';

  const entry = { question, kind: 'human', note: '', answering_mechanism: '', analysis_ids: [], perspectives: [] };
  const html = app.rowInner(entry, 0, {});
  assert.match(html, /answered\s+just now/);
  assert.doesNotMatch(html, /answered by\s*·/, 'must not render an empty author slot');
});
