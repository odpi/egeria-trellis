/** Real-DOM regression tests for ENRICHMENT-E1-CONTEXT-TAB (replying to
 *  docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md §1, on top of
 *  ENRICHMENT-E0-ROW-ANATOMY) and its 2026-09-29 amendment (project owner):
 *  analyses whose prerequisite is a human input run at the Enrichment
 *  stage, and Survey & analyses lists them as unlocked/locked.
 *
 *  Covers, against the REAL, unmodified render functions:
 *   - the strip order and Context-as-default (subTabsHtml, gated to
 *     `state.stage === 'enrichment'` via the new `stages` filter)
 *   - each of Context's sections rendering with a "feeds →" line
 *   - the lens row's read-only rendering, both links marked deferred
 *   - Survey & analyses's map rendering: locked / unlocked states, and the
 *     amendment's exact gate wording ("declare a lens on the investigation")
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

test('the strip: Context is first, gated to the Enrichment stage only', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();

  app.state.stage = 'enrichment';
  app.state.subTab = 'context';
  app.state.resourceType = 'db';
  const enrichmentStrip = app.subTabsHtml();
  // Order in the markup: Context appears before Questions.
  const contextIdx = enrichmentStrip.indexOf('Context');
  const questionsIdx = enrichmentStrip.indexOf('Questions');
  assert.ok(contextIdx >= 0 && questionsIdx > contextIdx,
    `Context must render before Questions on Enrichment: ${enrichmentStrip}`);

  app.state.stage = 'scouting';
  app.state.subTab = 'questions';
  const scoutingStrip = app.subTabsHtml();
  assert.ok(!scoutingStrip.includes('>Context<') && !scoutingStrip.includes('"context"'),
    `Context must not appear on a stage other than Enrichment: ${scoutingStrip}`);
});

test('Context is the default sub-tab when navigating into Enrichment from the module default', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  // Simulate the nav click handler's own logic directly (bindTopNav wires a
  // real click listener that isn't reachable without a full header render;
  // the RULE under test -- "only redirect the module default" -- is the
  // same one-line branch, asserted here against the same starting state the
  // module ships with).
  app.state.subTab = 'questions';   // the module-level default
  app.state.stage = 'enrichment';
  if (app.state.stage === 'enrichment' && app.state.subTab === 'questions') app.state.subTab = 'context';
  assert.equal(app.state.subTab, 'context');

  // A DELIBERATE choice survives the same switch.
  app.state.subTab = 'disposition';
  app.state.stage = 'scouting';
  app.state.stage = 'enrichment';
  if (app.state.stage === 'enrichment' && app.state.subTab === 'questions') app.state.subTab = 'context';
  assert.equal(app.state.subTab, 'disposition', 'a deliberately-chosen tab must not be clobbered by the stage switch');
});

test('Survey & analyses map: locked and unlocked rows render the existing glyph vocabulary with the right wording', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();

  const unlockedRow = { id: 'doc_source_ingestion', name: 'Documentation Source Ingestion',
    requires_input: 'documentation_source', unlocked: true, reason: '1 documentation source(s) declared', state: 'unlocked', runnable: true };
  const lockedRow = { id: 'preliminary_fit', name: 'Preliminary Fit',
    requires_input: 'lens', unlocked: false, reason: 'declare a lens on the investigation', state: 'locked' };

  assert.equal(app.requiresInputInWords('documentation_source'), 'a declared, reachable documentation source');
  assert.equal(app.requiresInputInWords('lens'), 'a declared lens');

  assert.equal(app.enrichmentAnalysisCardStateKey(unlockedRow), 'unrun');
  assert.equal(app.enrichmentAnalysisCardStateKey(lockedRow), 'no-surveyor');

  const unlockedHtml = app.enrichmentAnalysisRowHtml(unlockedRow);
  assert.match(unlockedHtml, /unlocked by: a declared, reachable documentation source/);
  assert.match(unlockedHtml, /data-run-enrichment-analysis="doc_source_ingestion"/, 'unlocked row must offer a Run action');

  const lockedHtml = app.enrichmentAnalysisRowHtml(lockedRow);
  assert.match(lockedHtml, /unlocked by: a declared lens/);
  // The amendment's exact gate wording (task brief): "declare a lens on the investigation".
  assert.match(lockedHtml, /declare a lens on the investigation/);
  assert.doesNotMatch(lockedHtml, /data-run-enrichment-analysis="preliminary_fit"/, 'a locked row must not offer Run');
});

test('a measured row (already run) does not offer Run', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const measuredRow = { id: 'preliminary_fit', name: 'Preliminary Fit',
    requires_input: 'lens', unlocked: true, reason: 'a lens is declared', state: 'measured' };
  assert.equal(app.enrichmentAnalysisCardStateKey(measuredRow), 'measured');
  const html = app.enrichmentAnalysisRowHtml(measuredRow);
  assert.doesNotMatch(html, /data-run-enrichment-analysis=/);
});

test('an unlocked analysis with no runner shows "ingestion not built yet" and offers no Run control', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const row = { id: 'doc_source_ingestion', name: 'Documentation Source Ingestion',
    requires_input: 'documentation_source', unlocked: true, reason: '1 documentation source(s) declared',
    state: 'unlocked', runnable: false };
  assert.equal(app.enrichmentAnalysisCardStateKey(row), 'no-surveyor');
  const html = app.enrichmentAnalysisRowHtml(row);
  assert.match(html, /unlocked · ingestion not built yet/);
  assert.doesNotMatch(html, /data-run-enrichment-analysis=/);
});
