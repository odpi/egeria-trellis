/** Real-DOM regression tests for `stages/context.js`'s `renderContext`
 *  (ENRICHMENT-E1-CONTEXT-TAB): the five sections in order, each row's
 *  "feeds →" line, and the lens row's read-only rendering with both links
 *  marked deferred.
 *
 *  Same module-resolution reasoning as human-question-answer-row-anatomy
 *  .test.mjs: `context.js` imports `state`/`esc`/`apiEntityType` from the
 *  literal specifier `/static/next/app.js` with no query string, and
 *  `fieldRowHtml`/`JUDGEMENTS`/`OBSERVATIONS`/`wireEnrichmentFieldControls`
 *  from `/static/next/stages/enrichment.js` — imports here are NOT
 *  cache-busted so writes to `app.state` are visible inside `context.js`.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

function stubContextFetch({ enrichment = {}, question_answers = {}, facts = [] } = {}) {
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/enrichment-analyses')) {
      return { ok: true, status: 200, json: async () => ({ analyses: [] }) };
    }
    if (u.includes('/api/context/')) {
      return { ok: true, status: 200, json: async () => ({ enrichment, question_answers }) };
    }
    if (u.includes('/api/analyses/facts')) {
      return { ok: true, status: 200, json: async () => ({ subjects: { adventureworks: facts } }) };
    }
    throw new Error(`context-tab-sections.test.mjs: unstubbed fetch ${u}`);
  };
}

test('the five sections render in order, each row saying what it feeds', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');

  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.investigation = '';
  app.state.investigations = [];
  app.state.questions = [
    { question: 'What does it cost to run?', kind: 'human', note: 'N/A — human-supplied via Enrichment Context form = may also be Agent over RAG content', answering_mechanism: 'Egeria Queries', analysis_ids: [] },
    { question: 'Is this in scope for GDPR?', kind: 'gap', note: '', answering_mechanism: '', analysis_ids: [] },
  ];

  const host = app.$('content') || document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';

  stubContextFetch({
    enrichment: {
      sensitivity: { value: 'confidential', kind: 'judgement', author: 'dan', set_at: new Date().toISOString(), evidence: {} },
      licence: { value: 'Apache-2.0', kind: 'observation', author: 'dan', set_at: new Date().toISOString(), source: 'license_classification' },
    },
    question_answers: {},
  });

  await renderContext('adventureworks');

  const html = document.getElementById('context-form').innerHTML;
  const order = ['What we judge', "What you're looking for", 'What we record',
    'What only you can answer', "Where it's documented"];
  let lastIdx = -1;
  for (const heading of order) {
    const idx = html.indexOf(heading);
    assert.ok(idx > lastIdx, `expected "${heading}" to appear after the previous section (found at ${idx}); html: ${html}`);
    lastIdx = idx;
  }

  // Every judgement/observation row says what it feeds.
  assert.match(html, /feeds → Curate \(publish record\)/);
  // License additionally names its survey source (`fromAnalysis`), not a
  // fabricated cross-reference.
  assert.match(html, /feeds → Curate \(publish record\) · sourced from license_classification/);

  // The human question row (section 4): no declared consumer (no
  // `analysis_ids`), so the honest line -- never the catalog's prose.
  assert.match(html, /What does it cost to run\?/);
  assert.match(html, /feeds → nothing reads this yet/);
  assert.doesNotMatch(html, /feeds → Egeria Queries/);

  // Section 4 must NOT include the non-human ("gap") question -- Context's
  // "What only you can answer" is human-catalog-questions only.
  assert.doesNotMatch(html, /Is this in scope for GDPR\?/);

  // Section 5 is #348's real block (mounted by renderContext); it must not
  // say "not built" anywhere on the tab.
  assert.doesNotMatch(html, /not built in \/next yet on this branch/);
  assert.ok(html.includes('id="doc-sources-block"'), 'the doc-sources host must be on the tab for a db resource');
});

test('the lens row: no lens declared, both links marked deferred, not built', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');

  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.investigation = 'gdpr-sweep';
  app.state.investigations = [{ slug: 'gdpr-sweep', display_name: 'GDPR Sweep' }];
  app.state.questions = [];

  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';

  stubContextFetch({
    facts: [{ analysis_id: 'preliminary_fit', value: { lens_declared: false }, state: 'measured', last_run_at: new Date().toISOString() }],
  });

  await renderContext('adventureworks');
  const html = document.getElementById('context-form').innerHTML;

  assert.match(html, /no lens declared for GDPR Sweep/);
  assert.match(html, /declare it on the investigation ›/);
  assert.match(html, /try one on this resource only/);
  // Both are marked "not built yet", the same convention every other
  // deferred affordance in this codebase uses -- not a real click target.
  const notBuiltCount = (html.match(/not built yet/g) || []).length;
  assert.ok(notBuiltCount >= 2, `expected both lens links marked deferred; html: ${html}`);
  assert.match(html, /feeds → preliminary_fit/);
});

test('a repo resource (amundsen): renderContext never fetches database-only facts and shows no database-only section', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');

  app.state.resourceType = 'repo';
  app.state.selectedSlug = 'amundsen';
  app.state.investigation = '';
  app.state.investigations = [];
  app.state.questions = [];

  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';

  // NOT stubbed for '/api/analyses/facts' (the preliminary_fit lens fetch) —
  // if renderContext calls it for a repo, the unstubbed-fetch guard throws
  // and this test fails loudly, proving the `state.resourceType === 'db'`
  // gate actually held rather than merely reading correctly.
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/context/')) {
      return { ok: true, status: 200, json: async () => ({ enrichment: {}, question_answers: {} }) };
    }
    throw new Error(`amundsen (repo) must not call this database-only endpoint: ${u}`);
  };

  await renderContext('amundsen');
  const html = document.getElementById('context-form').innerHTML;

  // The rail (renderEnrichmentEvidence, database-scoped evidence) must not
  // have been reached -- its target id stays absent/untouched.
  assert.equal(document.getElementById('rail-evidence'), null);
  // Nothing in Context's own markup names a database-only structure.
  assert.doesNotMatch(html, /Schema Inventory/i);
  assert.doesNotMatch(html, /administered by/i);   // §4's per-type db owner line (E2, not built)
});

test('the lens row: a declared lens (from preliminary_fit\'s own last read) renders without the decline wording', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');

  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.investigation = '';
  app.state.investigations = [];
  app.state.questions = [];

  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';

  stubContextFetch({
    facts: [{ analysis_id: 'preliminary_fit', value: { lens_declared: true }, state: 'measured', last_run_at: new Date().toISOString() }],
  });

  await renderContext('adventureworks');
  const html = document.getElementById('context-form').innerHTML;
  assert.match(html, /a lens is declared for/);
  assert.doesNotMatch(html, /no lens declared/);
});

test('no feeds line ever carries catalog note prose ("N/A", "may also"); a question with declared analyses names them', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.investigation = '';
  app.state.investigations = [];
  app.state.questions = [
    { question: 'Prose-only question?', kind: 'human',
      note: 'N/A — human-supplied via Enrichment Context form = may also be Agent over RAG content',
      answering_mechanism: 'N/A — may also be Agent over RAG content', analysis_ids: [] },
    { question: 'Declared question?', kind: 'human', note: 'N/A', answering_mechanism: '', analysis_ids: ['doc_evidence_check'] },
  ];
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';
  stubContextFetch({ enrichment: {}, question_answers: {} });
  await renderContext('adventureworks');

  const feeds = [...document.querySelectorAll('#context-form .text-provenance')]
    .map((n) => n.textContent.trim())
    .filter((t) => t.startsWith('feeds'));
  assert.ok(feeds.length >= 2, `expected feeds lines, got ${JSON.stringify(feeds)}`);
  for (const t of feeds) {
    assert.doesNotMatch(t, /N\/A/, `feeds line carries catalog prose: ${t}`);
    assert.doesNotMatch(t, /may also/i, `feeds line carries catalog prose: ${t}`);
  }
  assert.ok(feeds.includes('feeds → nothing reads this yet'));
  assert.ok(feeds.includes('feeds → doc_evidence_check'));
});

test('every form-ending control on Context says "Save" (capitalised), and no page word is a UK "catalogue"', async () => {
  makeDomEnvironment();
  await loadAppModule();
  const app = await import('/static/next/app.js');
  const { renderContext } = await import('/static/next/stages/context.js');
  app.state.resourceType = 'db';
  app.state.selectedSlug = 'adventureworks';
  app.state.investigation = '';
  app.state.investigations = [];
  app.state.questions = [{ question: 'What does it cost to run?', kind: 'human', note: '', answering_mechanism: '', analysis_ids: [] }];
  app.state.editingContextAnswer = 'What does it cost to run?';
  const host = document.createElement('div');
  host.id = 'content';
  document.body.appendChild(host);
  host.innerHTML = '<div id="context-form"></div>';
  stubContextFetch({ enrichment: {}, question_answers: {} });
  await renderContext('adventureworks');
  const form = document.getElementById('context-form');
  const fieldSaves = [...form.querySelectorAll('[data-save]')];
  assert.ok(fieldSaves.length >= 5, 'judgement and observation rows each have a save control');
  for (const b of fieldSaves) assert.equal(b.textContent.trim(), 'Save');
  assert.equal(form.querySelector('[data-context-answer-save]').textContent.trim(), 'Save', 'the human-question editor');
  assert.doesNotMatch(form.textContent, /[Cc]atalogu(e|ed|ing)\b/);
});
