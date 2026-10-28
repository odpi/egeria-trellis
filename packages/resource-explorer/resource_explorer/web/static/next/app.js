/* Resource Explorer — /next
 *
 * An experimental parallel UI. Skin 1c ("dark chrome, paper content") and one
 * redesigned screen: the Questions checklist, changed from reporting HOW a
 * question would be answered to reporting WHAT THE ANSWER IS, with its
 * caveat, its provenance and its state, per row.
 *
 * SCOPE, deliberately: the frame plus ONE real pane. That pane is Questions,
 * for a repo, live against the API — parameterised by stage rather than
 * duplicated per stage, because the endpoint already takes a `phase` and
 * because the six row states this screen argues for only ALL occur once you
 * leave Scouting. Every other sub-tab renders an honest "not built in /next"
 * rather than a half-working version: a half-built version of everything
 * proves nothing and takes ten times as long.
 *
 * THE ONE BEHAVIOUR THIS SCREEN EXISTS FOR: distinguishing "ran and found
 * nothing" from "never ran" from "cannot be answered here". The backend
 * already carries that distinction — `surveyors/result_status.py`'s states,
 * surfaced through `facts.py`'s Envelope — and this file must not collapse
 * it. A confident wrong answer is worse than no answer.
 */

// The Scouting slice — work lists, batch runs and the comparison grid. Its
// own module: it is the one surface that reads a SET rather than a resource,
// and it goes when the experiment goes.
import { listWorkLists, openWorkList, saveAsWorkList, openDialog, closeCellDetail, CELL }
  from '/static/next/worklist.js';
import { ago, whenMs, verdictLineHtml, changedTimesHtml, savedLine, readBackAt, PUBLISHED_EARLIER_SENTENCE, PUBLISHED_EARLIER_WORD } from '/static/next/format.js';
// The one glyph table (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1). `GLYPH`
// and `factGlyph` below are thin views over `GLYPH_STATES` -- this file
// declares no glyph-to-meaning mapping of its own any more.
import { STATES as GLYPH_STATES, stateEntry } from '/static/next/glyphs.js';
import { publishProofHtml } from '/static/next/publish-proof.js';
// `readEnvelope` and its own private helpers used to live here directly;
// split into their own module (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §D,
// 2026-09-27) so a node-run test can import `readEnvelope` and check it
// against `facts.py`'s `_renders_text` without dragging in this file's own
// top-level DOM/auth side effects. `esc`/`tnum` stay defined here and are
// passed into `readEnvelope`/`answerHtml` as parameters, not imported back
// (that would be circular) — see envelope.js's header comment.
import {
  readEnvelope, factMermaid, factGraphviz, prose, scalarMeasures, cap,
  firstSentence, leadAnalysisId, answerHtml,
} from '/static/next/envelope.js';
// One module per stage (PLAN-FINISH-REPOS.md, Part 2 §1) — each exports its
// own pane renderer(s); app.js keeps routing, shared state and the chrome.
// Enrichment, Understanding, Curate, Automate, Investigation and (item 11)
// Analysis have something to import; the remaining canonical stage ids —
// scouting, discovery, assessment — have a `next/stages/*.js` module too,
// but it is empty (Discovery and Assessment deliberately, item 11: the
// generic Questions-checklist engine below reaches both correctly with no
// stage-specific code; Scouting for other reasons — see its own stub
// header comment). Building one of them means adding real exports to its
// stub file and one import line here — see
// APP-JS-SPLIT-IMPLEMENTED.md. Investigation's stub
// (`stages/investigation.js`) is no longer empty — it now ports classic's
// Investigations tab (list/create/detail: members, dispositions,
// next-steps, purposes, classification, Egeria bind/promote/sync/
// reclassify) into /next; see that file's own header comment.
// Enrichment's Context tab (ENRICHMENT-E1-CONTEXT-TAB) — the new first
// sub-tab. The judgement/observation form (`renderEnrichment`,
// `stages/enrichment.js`) used to render inline on the Questions tab
// whenever `state.stage === 'enrichment'`; it now lives here instead (see
// loadPane()'s `context` branch below and the Questions branch's own
// comment on why `renderEnrichment` is no longer called from there).
// `renderEnrichment`/`renderEnrichmentForm` stay exported from that module
// (still called from within it, via `context.js`'s shared pieces) even
// though app.js itself no longer calls `renderEnrichment` directly.
import { renderContext } from '/static/next/stages/context.js';
import { openApiReference } from '/static/next/stages/api-reference.js';
import { inPageConfirm, inPageNotice, inPagePrompt } from '/static/next/confirm-dialog.js';
import { scoutingTilesHtml, scoutingSignalHtml, staleLinkBannerHtml, bindStaleLinkBanner } from '/static/next/stages/repo-header.js';
import { contextRecordedSpec, contextRecordedState } from '/static/next/context-recorded.js';
import { followUntil, ACTIVITY_LIVE } from '/static/next/live-status.js';
import { nativeSurveysSectionHtml, nativeSurveysUnreadableHtml, bindNativeSurveys, coordinateControlHtml, coordinateOffNoteHtml, bindCoordinateControls, definitionRunLineHtml, definitionRunsListHtml, bindRunLineControls, ownSurveyRows } from '/static/next/stages/native-surveys.js';
// The row anatomy shared with Enrichment's judgements/observations rows
// (ENRICHMENT-E0-ROW-ANATOMY, docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-
// STAGE-IA.md §0.3/§6 item 1) — who + when + the evidence-moved flag, one
// function, called from both stores' rows rather than reimplemented here.
import { personRowLineHtml } from '/static/next/row-anatomy.js';
import { renderInvestigation, openInvestigationDetail, refreshOpenInvestigation, openCreateDialog as openNewInvestigationDialog } from '/static/next/stages/investigation.js';
import { openInvestigationPicker, scopeActWording } from '/static/next/investigation-picker.js';
import { addActWording, addListToInvestigation, openStartFromList, START_LABEL } from '/static/next/worklist-actions.js';
import { loadChartsPane, renderUnderstandingTabs } from '/static/next/stages/understanding.js';
import { renderCurate, jumpToCurateSection } from '/static/next/stages/curate.js';
import { coverageHtml } from '/static/next/stages/curate-coverage.js';
// Stage-specific leading tabs (Brief S0, dormant): the gate and ids are a leaf module, the label counts and the
// running-commit line need `state`.
import { STAGE_TAB_ENTRIES, STAGE_TAB_IDS, isStageTabId, stageTabsOn } from '/static/next/stages/stage-tabs.js';
import { tabCountsHtml, commitLineHtml, bindCommitLine } from '/static/next/stages/curate-tabs.js';
import { inventoryHeaderText, nodeSourceLine, credentialLineText } from '/static/next/stages/scope-sources.js';
import { surveySourceWord, threeNumberLine, fkTarget } from '/static/next/stages/db-report.js';
// Analysis (RULING-SUBRESOURCES-PLACEMENT.md, 2026-09-22) -- Sub-Resources'
// candidate-selection/catalogue UI, attached to sub_resource_survey's own
// row in the Survey & analyses list below (analysisIndexRowHtml /
// renderAnalysesIndexSection), not a stage-level bypass. The old one-line
// deferral note this stage used to render from loadPane() is gone -- the
// feature it named is now built; see stages/analysis.js's header comment.
import { mountSubResourcePanel } from '/static/next/stages/analysis.js';
// The RFA drawer (PLAN-FINISH-REPOS.md item 10) — chrome-level, like
// worklist.js, not a per-resource stage; see next/rfa.js's own header
// comment for why it lives at this level rather than under stages/.
import { toggleRfaDrawer } from '/static/next/rfa.js';
import { openActivityPanel } from '/static/next/stages/activity.js';
import { noticeActivity } from '/static/next/stages/activity.js';
import { startActivityWatch } from '/static/next/activity-unread.js';
import { installHealthBanner, installBootstrapBanner, openConnectionPopover } from '/static/next/shell-status.js';
import { renderAutomate } from '/static/next/stages/automate.js';
// Admin (PLAN-FINISH-REPOS.md item 5) — chrome-level, same pattern as
// Activity: reachable from the header's own ⚙ Admin button, decoupled from
// #intent-nav/currentNavIntent, NOT a STAGES entry. See next/admin/index.js's
// own header comment for scope (five real ports, six named deferrals).
import { openAdminPanel } from '/static/next/admin/index.js';
// Discovery import (NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md) — the
// corpus-level "find and import candidate repos" dialog: GitHub search,
// the `/from-list` bulk loader, and the inventory CSV export. Chrome-level
// like worklist.js/rfa.js, not a stage module — see that file's own header
// comment for why (SPEC-ACTIONABLE-AND-HONEST.md point 2). Reached from the
// sidebar's `find-repos` action below, for `state.resourceType === 'repo'`.
import { openFindReposDialog } from '/static/next/discovery-import.js';
// Database server discovery — classic's real mechanism for databases
// (register a server once, then server-side introspection via
// POST /api/db-servers/{slug}/discover), ported for `state.resourceType
// === 'db'` in the same `find-repos` action. Chrome-level, same placement
// rule as discovery-import.js above. Filesystems still keep the
// old-UI-link stub — classic's own filesystem registration flow is not
// this file's scope yet.
import { openFindDbServersDialog } from '/static/next/db-server-discovery.js';
// Chat (PLAN-FINISH-REPOS.md item 9) — chrome-level, like worklist.js/rfa.js:
// the "Ask" rail and the pane it promotes an answer into, beside whichever
// stage is active rather than one of the eight itself. See next/chat.js's
// own header comment for the placement rule this follows and why it (unlike
// rfa.js) imports state/esc/$ back from app.js.
import { renderRail, renderRailScope, openQuestionTurn } from '/static/next/chat.js';
import {
  ApiError,
  VALID_DISPOSITIONS,
  addInvestigationMember,
  CHART_MEASURE,
  REPO_CHARTS,
  getAnswer,
  getChart,
  getDispositionHistory,
  enqueueBatch,
  getAnalysisTrend,
  getDeclaredVsReceived,
  getResourceRuns,
  getSurveyCandidates,
  getNativeSurveys,
  listSurveyDefinitions,
  getAnalysesIndex,
  getSurveyDashboards,
  listSurveyResultBoards,
  getCurationCoverage,
  runSurveyDefinition,
  getMe,
  getMemberChildren,
  getMembers,
  getMeasurements,
  getRunCost,
  getDepthOffer,
  saveReport,
  listRecords,
  recordExportHref,
  actOnRecord,
  postDepthOfferOutcome,
  promoteMembers,
  getQuestions,
  createSubscription,
  saveSchedule,
  getSchedules,
  getScoutingOverview,
  getSurveySummary,
  resolveEgeriaLink, listStaleLinkages,
  listActivity,
  listAnalyses,
  listDatabases,
  listFilesystems,
  getSchemaInventoryTree,
  listGroups,
  listInvestigationMembers,
  listInvestigations,
  listPerspectives,
  listAllPerspectives,
  listProjects,
  listRfas,
  clearCache,
  pollActivity,
  getActivityEntry,
  activityFailure,
  failureText,
  planPrerequisites,
  runPrerequisites,
  raiseCapabilityRfa,
  removeInvestigationMember,
  removeEntity,
  runAnalysis, runAnalysisWithCredential,
  setDisposition,
  setEntityDisposition,
  getEntityDispositionHistory,
  setWorkingSetHidden,
  getContext,
  getEnrichmentAnalysesMap,
  getJournal,
  questionKey,
  requireKind,
  writeJournal,
  saveQuestionAnswer,
} from '/static/re-api.js';
import { CREDENTIAL_UNREADABLE_TEXT, credentialMarkHtml, isCredentialUnreadable } from '/static/next/credential.js';
import { rememberedCredential, setRemembered } from '/static/next/run-credential.js';
import { mountDependencyTable } from '/static/next/stages/dependencies.js';
import { credentialChangeHtml, bindCredentialChange } from '/static/next/credential-change.js';
import { installSessionBanner } from '/static/next/session-banner.js';
import { isFreshnessSkip, offerRunAnyway, UP_TO_DATE_WORD } from '/static/next/run-anyway.js';

/* ════════════════════════════════════════════════════════════════════════
 * State
 * ════════════════════════════════════════════════════════════════════════ */

export const state = {
  resourceType: 'repo',        // repo | db | filesystem
  // The last survey launch's note ({slug, html}). Derived data, not a DOM append:
  // loadSurveyPane() replaces #survey-note wholesale, so the note is drawn from here.
  launchNote: null,
  projects: [],
  // Databases/filesystems are fetched lazily -- on first switch to that
  // sidebar chip, or on boot when the URL already names that type -- not
  // eagerly at start() like `projects`, since most sessions never touch
  // them. The `*Loaded` flags distinguish "fetched, zero results" from
  // "never fetched" so the sidebar can say which one it is instead of
  // rendering an empty list either way.
  databases: [],
  databasesLoaded: false,
  filesystems: [],
  filesystemsLoaded: false,
  groups: [],
  selectedSlug: null,
  overview: null,              // the selected repo's scouting-overview, or null
  filter: '',
  scope: 'working-set',        // lifecycle scope chip; '' means All
  dispositionFacet: 'all',
  showHidden: false,
  showEmptyFacets: false,
  showMarkKey: false,
  selectMode: false,
  selected: new Set(),
  investigations: [],
  investigation: '',           // slug; client-side only, no server session
  workingSet: new Set(),       // repo slugs in the current investigation
  workingSetUnknown: false,
  stage: 'scouting',
  subTab: 'questions',
  perspectives: [],            // the vocabulary, from the API — never hardcoded
  activePerspectives: new Set(),  // empty means all
  questions: [],              // the stage's questions AFTER perspective filtering
  allQuestions: null,         // the stage's questions UNFILTERED; null = unknown
  answers: new Map(),          // question text -> envelope | {error} | 'loading'
  // `${slug}|${analysisId}` -> failure text persisted by the finished run's own
  // activity row; shown in the Survey & analyses row until the next run.
  analysisRunFailures: new Map(),
  runsInFlight: new Map(),     // question text -> {analysisId, activityId}
  // §17.1 prerequisite proposals -- a run the resolver would not let start
  // unasked because it crosses the budget's tier. question -> {analysisId,
  // entityType, proposal, background} while awaiting the user's accept/
  // decline; cleared on either. Separate from `runsInFlight`, which is a run
  // actually in progress -- a pending proposal is a run that has NOT started.
  pendingProposals: new Map(),
  // A one-shot "ran X first" note for the within-budget auto-run case (no
  // proposal, nothing to accept -- the resolver already ran the producer by
  // the time the demanding step's own result comes back). question -> text;
  // read once by `bodyLines` on the next render and deleted, so it reads as
  // what just happened rather than persisting as a stale caveat.
  autoRanNotes: new Map(),
  // Questions whose "why is this here" disclosure the person opened (kept across a row redraw).
  whyOpen: new Set(),
  me: null,
  counts: { activity: null, rfas: null },
  chat: [],                    // the transcript: one entry per turn
  promoted: null,              // a chat answer promoted into the pane
  charts: null,                // Understanding's probed chart index
  workLists: [],               // saved work lists
  workListSlug: null,          // the open one; the pane takes over when set
  lastWorkListSlug: null,      // the one you were last in, for the way back
  workListIndex: false,        // showing the list OF work lists
  workListIndexNote: '',       // what the last add/start on the index said, shown once
  lastRunStage: 'scouting',    // the run stage a list re-opens on after a frame-stage click closed it
  scopeCount: null,            // members in the current investigation's scope, ALL kinds; null = unknown
  // The question (its full text, the same key `wireHumanAnswers` already
  // uses to look up `contextAnswers`) currently open for inline editing on
  // the Questions tab -- '' means none. Replaces `window.prompt()`
  // (ENRICHMENT-E0-ROW-ANATOMY): "Answer this ->"/"change" now open an
  // inline control on the row itself, the same input+save pattern
  // `stages/enrichment.js`'s judgement/observation fields already use,
  // rather than a blocking popup with no author.
  editingAnswer: '',
};

/** Three classes of nav item — RULING-NAV-GROUPING.md, answering a peer
 *  critique of the nine-item intent row. This replaces the old "eight
 *  intents, in their canonical order, plus Investigation as the frame"
 *  comment, which is wrong in its count: there are six ORDERED intents
 *  ("the run"), not eight, plus two cross-cutting, plus the frame.
 *
 *  `class` is declared HERE and nowhere else — `renderIntentNav()` derives
 *  grouping, numbering and separator style (chevron for `run`, middot
 *  everywhere else) from this field rather than hardcoding a second list of
 *  which ids go where. The ruling is explicit that regrouping in the
 *  renderer while this array still called them "eight ordered intents"
 *  would be the same class of bug as `unbuilt` (DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md — read in
 *  three places, set in none) and #130's
 *  dashed-styling-independent-of-its-flag bug: declare the fact once,
 *  derive appearance from it.
 *
 *  - `frame`        — Investigation (and, external to this array, Work
 *                      lists): why this body of work exists, and which
 *                      cohort you are working. Not a stage.
 *  - `run`           — Scouting, Discovery, Assessment, Analysis,
 *                      Enrichment, Curate: six ordered intents; sequence is
 *                      real, so they are numbered 1-6 and chevron-joined.
 *  - `cross-cutting` — Understanding, Automate: order does not apply.
 *                      Decision (project owner, 2026-09-18): Understanding
 *                      can be used at any time and will become a
 *                      user-configured dashboard — a surface the user
 *                      configures, not an operation on a corpus — so it is
 *                      not a milder or later stage of the run.
 *
 *  `built` is about /next, not about the product. */
const STAGES = [
  { id: 'investigation', label: 'Investigation', class: 'frame' },
  { id: 'scouting',      label: 'Scouting',      class: 'run', built: true },
  // Discovery, Assessment and Analysis were marked "not built" here.
  // ITEM-11-DISCOVERY-ASSESSMENT-ANALYSIS-IMPLEMENTED.md verified all three
  // have real catalogued questions (question_catalog.yaml: 10 Discovery, 15
  // Assessment, 11 Analysis rows) and that the generic Questions-checklist
  // engine (loadPane(), below) already reaches them correctly once `built`
  // is true -- the same mechanism Scouting/Enrichment/Curate use, with no
  // stage-specific rendering needed. Discovery's Disposition sub-tab was
  // already wired to a real write path (`POST /api/discovery/disposition`,
  // web/routes/discovery.py's set_repo_disposition) before this change; it
  // only needed `built: true` to become reachable. Classic's corpus-level
  // repo-search/`/from-list`/CSV-export features are now ported too, as the
  // sidebar's "Find repos" action (`next/discovery-import.js`,
  // NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md) rather than as Discovery
  // stage content -- see stages/discovery.js's header comment for why.
  // Analysis's "Sub-Resources" sub-view is still NOT ported -- named as a
  // deliberate deferral in ITEM-11-DISCOVERY-ASSESSMENT-ANALYSIS-IMPLEMENTED.md,
  // not silently dropped.
  { id: 'discovery',     label: 'Discovery',     class: 'run', built: true },
  { id: 'assessment',    label: 'Assessment',    class: 'run', built: true },
  { id: 'analysis',      label: 'Analysis',      class: 'run', built: true },
  // Enrichment was marked "not built" here. ITEM-1-ENRICHMENT-IMPLEMENTED.md
  // verified the judgement/observation fields, the save-and-revisit round
  // trip, and the evidence-moved perishability flag all work; the catalog
  // has 7 human-supplied questions tagged this phase, so the generic
  // Questions engine (loadPane()) reaches renderEnrichment() without a
  // special case, the same as any other built stage.
  { id: 'enrichment',    label: 'Enrichment', class: 'run', built: true },
  // Understanding was marked "not built" here. It renders charts now — see
  // loadChartsPane(); the catalog rows it lacks were never what fed it.
  // RULING-NAV-GROUPING.md §1: Understanding LEAVES the run — it is a
  // surface the user configures (eventually, per-user dashboards), not an
  // operation on a corpus, so it is cross-cutting rather than stage 6 of 6.
  { id: 'understanding', label: 'Understanding', class: 'cross-cutting', built: true },
  // Curate was marked "not built" here. ITEM-3-CURATE-IMPLEMENTED.md
  // verified the component-tree review, the catalogue-depth offer, and now
  // multi-branch selection and the blueprint list all work end to end — the
  // same "flip the flag once the doc says so" pattern as every other stage
  // above. Curate does not go through the generic Questions engine (it has
  // its own renderCurate, see loadPane()'s explicit `state.stage === 'curate'`
  // branch), but the nav's dashed/clickable choice reads this flag exactly
  // the same as a Questions-engine stage would.
  { id: 'curate',        label: 'Curate',        class: 'run', built: true },
  // Automate is a real, deliberately partial port (PLAN-FINISH-REPOS.md
  // item 4): renderAutomate() (next/stages/automate.js) shows and toggles
  // real subscriptions and the real global Schedules overview. Creating a
  // subscription is NOT built -- it rides on an Assessment/Analysis card's
  // "Notify me" action, and /next has no card grid there (item 11 built
  // Assessment/Analysis through the generic Questions-checklist engine,
  // question rows not cards) -- and that one gap is named and linked out
  // rather than the whole stage being deferred (see automate.js's own
  // header comment for the detail). Cross-cutting alongside Understanding
  // (RULING-NAV-GROUPING.md §2): it makes the run repeat rather than being
  // a step within it.
  { id: 'automate',      label: 'Automate',      class: 'cross-cutting', built: true },
];

/** The run's own order, 1-6 — derived from `STAGES`, never a second literal
 *  list of ids. A `Map` from stage id to its 1-based position within the
 *  run, used only for the nav's numbering. */
const RUN_ORDER = new Map(
  STAGES.filter((s) => s.class === 'run').map((s, i) => [s.id, i + 1]),
);

/** Sub-tab order is IDENTICAL across every stage, on purpose. A stage that
 *  lacks one greys it out rather than removing it, so the tab under the
 *  cursor does not change meaning when you switch stages. */
/**
 * Sub-tab order is IDENTICAL across every stage, on purpose.
 *
 * `label` is what the current UI calls it; `does` is what it actually is.
 * They differ for Search, which is REPO DISCOVERY — saved sources, a GitHub
 * search form and a list importer — and has nothing to do with the selected
 * resource. Naming the deferred stub "Search" made /next mis-describe the
 * thing it was deferring.
 */
/* Stage-page round (SPEC-THE-STAGE-PAGE.md): the strip after the fold.
 * `dashboard` is gone as its own tab -- its "by analysis" half becomes
 * `by_analysis` below, and its "by question" half is the disclosure now on
 * the Questions row itself (point 10, `provenanceLine`'s "the numbers behind
 * this N" link). See that function's comment for what has and has not moved
 * yet: findings and disagreements stay on `by_analysis` for this slice. */
/* `Find repos` left the strip (SPEC-ACTIONABLE-AND-HONEST.md, point 2, the
 * owner's round 2026-09-15): the uniform-strip rule ("grey out what a
 * stage lacks, never remove") is for things that are PER-STAGE, and finding
 * candidate repos is corpus-level work, the same action on Scouting as on
 * Curate -- nine dashed-underline copies of one non-stage-scoped action was
 * nine wrong promises, not nine honest gaps. It now lives beside the
 * sidebar's Repos/DBs/FS switcher (findReposButtonHtml, bindSidebar),
 * unchanged in what it can say: still "not built in /next", just no longer
 * pretending to be a ninth stage's affordance. */
const SUB_TABS = [
  // Context (ENRICHMENT-E1-CONTEXT-TAB, 2026-09-29): Enrichment's new FIRST
  // sub-tab and its default — everything a person has supplied about the
  // resource, each row signed, dated, and saying what it feeds. Gated to
  // the Enrichment stage via `stages`, the same shape `resourceTypes` gates
  // Schema Inventory to databases below — a stage-specific LEADING tab, per
  // the designer's reply §1 ("Schema Inventory is a type-specific extra
  // tab, and Context follows the same pattern"). Every other stage's strip
  // is unaffected: `subTabsHtml()`'s filter hides this tab entirely rather
  // than greying it out, since it names something that literally does not
  // exist off Enrichment (there is no per-stage "Context" content to defer
  // to), unlike the dashed/deferred tabs below which name a real, just
  // unbuilt, capability.
  { id: 'context', label: 'Context', does: "Everything a person has supplied about this resource, each row signed, dated, and saying what it feeds", built: true, stages: ['enrichment'] },
  // Curate's and Understanding's leading tabs (Brief S0). Hidden by visibleSubTabs() until stage-tabs.js's gate
  // (STAGE_TABS_KINDS) names the resource kind, so the strip is unchanged while the gate is empty.
  ...STAGE_TAB_ENTRIES,
  { id: 'questions', label: 'Questions', does: 'The question checklist', built: true },
  { id: 'survey', label: 'Survey & analyses', does: 'Survey definitions, with their fetch-step counts, and the analyses they run', built: true },
  { id: 'by_analysis', label: 'By analysis', does: 'Survey results grouped by analysis rather than by question', built: true },
  { id: 'disposition', label: 'Disposition', does: 'Set a verdict on this resource, its history, and the journal', built: true },
  // Slice 22 — database-only: a repo/filesystem has no schema/table/column
  // tree to show, so this tab is filtered out entirely for those types
  // (subTabsHtml() below), not merely left unbuilt-looking for them.
  // Found live, `laz_local_adventureworks`, 2026-09-27: `resourceTypes`
  // named the display-word 'database', but `state.resourceType` is always
  // the short form 'db' (line ~157's own comment: 'repo' | 'db' |
  // 'filesystem' — every other comparison site in this file agrees). The
  // filter's own `.includes(state.resourceType)` check silently never
  // matched, so this tab never appeared for any database at all.
  { id: 'schema_inventory', label: 'Schema Inventory', does: 'Schemas, tables and columns, with row/byte estimates, keys and comments', built: true, resourceTypes: ['db'] },
];

/* ════════════════════════════════════════════════════════════════════════
 * Helpers
 * ════════════════════════════════════════════════════════════════════════ */

export const $ = (id) => document.getElementById(id);

/** `state.resourceType` ('repo' | 'db' | 'filesystem') is /next's own UI
 *  shorthand -- the sidebar chip ids (renderSidebar's `types`) and the
 *  `type=` URL param. Every backend route uses the canonical vocabulary
 *  'repo' | 'database' | 'filesystem' instead (schedules.py's
 *  `_RESOURCE_LOOKUP`, survey_definition_executor.py's `_ADAPTERS`,
 *  analyses.py's `resource_type` param) — 'db' is not a valid entity_type
 *  anywhere server-side. Verified live 2026-09-22 against the running dev
 *  server: `GET /api/survey-definitions/db/.../candidates` 400s ("No Survey
 *  Definition adapter registered for entity_type='db'"), and worse,
 *  `GET /api/analyses/db` silently returns `[]` rather than erroring — the
 *  exact silent-wrong-data shape this task exists to fix elsewhere
 *  (automate.js/worklist.js's old 'repo' hardcodes). Call this at every
 *  boundary that sends `state.resourceType` to the server; 'filesystem'
 *  already matches on both sides and passes through unchanged. */
export function apiEntityType(resourceType) {
  return resourceType === 'db' ? 'database' : resourceType;
}

/** The sidebar's row data for whichever resource type is current. Databases
 *  and filesystems now carry the same `working_set_hidden`/`disposition`/
 *  `group_slug`/`is_published` fields repos do (`web/routes/databases.py`/
 *  `filesystems.py`), so `visibleRows()` filters all three the same way --
 *  this just returns the raw fetched list; filtering happens where it's
 *  used. `state.scope`'s investigation/lifecycle chips remain repo-only
 *  (see SCOPE_CHIPS) -- that is the one axis genuinely repo-specific. */
function currentResourceRows() {
  return state.resourceType === 'db' ? state.databases
    : state.resourceType === 'filesystem' ? state.filesystems
    : state.projects;
}

/** Fetch the database/filesystem list on first need -- repos are fetched
 *  once at boot (`start()`), but /next never fetched either of these lists
 *  at all until now, so there is no existing "refresh" path to extend.
 *  Cached behind `*Loaded` rather than re-fetched on every chip click; a
 *  session that wants fresh data can reload. No-op for 'repo' and for a
 *  type that's already loaded. */
export async function ensureResourceListLoaded(type) {
  if (type === 'db' && !state.databasesLoaded) {
    try { state.databases = (await listDatabases()) || []; }
    catch { state.databases = []; }
    state.databasesLoaded = true;
  } else if (type === 'filesystem' && !state.filesystemsLoaded) {
    try { state.filesystems = (await listFilesystems()) || []; }
    catch { state.filesystems = []; }
    state.filesystemsLoaded = true;
  }
}

/** The active stage's display label, for the pane header. */
const stageLabel = () =>
  STAGES.find((s) => s.id === state.stage)?.label || state.stage;

/** Escape for interpolation into a template literal that becomes innerHTML.
 *  Every value below that came from the API goes through this — question
 *  text, headlines and notes are authored content, and one unescaped `<`
 *  would silently eat the rest of a row. */
export function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    // NAMED entities only, never numeric. `tnum()` below wraps every run of
    // digits in a span, and it runs after this — so a `&#39;` became
    // `&#<span…>39</span>;`, which the browser renders as the literal text
    // "&#39;". Seen on screen as "Egeria&#39;s catalog". Named entities carry
    // no digits, so the two passes stop interfering.
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

/** The one place a control's dashed "not built" underline comes from.
 *
 * `#130` fixed two header buttons (Activity, Admin) that kept this styling
 * after they were built, because it was written inline at each site —
 * `style="border-bottom:1px dashed currentColor"` — independent of the flag
 * that actually gates the behaviour, so a stage or control could become
 * built and keep looking deferred with nobody the wiser.
 * SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §5 names this as a rule that will
 * recur (select-all, blueprint accept, the member-link affordance) and asks
 * for one shared helper rather than a fourth, fifth, sixth inline copy — used
 * here to also fix the two pre-existing inline copies (the stage nav's
 * unbuilt span, the sub-tab rail's deferred button) it was already too late
 * to catch in #130 itself.
 *
 * `isBuilt` is always read from the SAME flag the caller uses to decide
 * behaviour — never a second, independent guess at whether something is
 * "done". Returns an attribute string to splice into a template literal;
 * '' when built, so a built control carries no extra markup at all. */
export function deferredAttrs(isBuilt, { title = '', extraStyle = '' } = {}) {
  if (isBuilt) return '';
  const style = `border-bottom:1px dashed currentColor${extraStyle ? `;${extraStyle}` : ''}`;
  return ` style="${style}"${title ? ` title="${esc(title)}"` : ''}`;
}

/** Wrap every run of digits in a tabular-figures span.
 *  Applied to answer, caveat and provenance lines — the places that hold
 *  counts, percentages, run numbers and ages. Running prose keeps its
 *  default figures, which is why this is applied per line and not to body. */
export function tnum(html) {
  return html.replace(/(\d[\d,.]*%?)/g, '<span class="tnum">$1</span>');
}


/* ────────────────────────────────────────────────────────────────────────
 * Icons
 *
 * Lucide, vendored as a sprite by frontend-build/build-next-icons.py and
 * injected into the document once, so `currentColor` inherits — which is the
 * whole point, since several of these carry state.
 *
 * Emoji is not the icon system, but the replacement rule is "nearest Lucide
 * equivalent of the SAME metaphor", not "an abstract shape". A cloud stays a
 * cloud. Only the six question states use an abstract glyph, because no
 * metaphor exists for them and a legend keys them instead.
 * ──────────────────────────────────────────────────────────────────────── */

async function loadIcons() {
  if (document.getElementById('lucide-sprite')) return;
  try {
    const res = await fetch('/static/next/icons.svg');
    if (!res.ok) return;                     // icons are an enhancement
    const holder = document.createElement('div');
    holder.id = 'lucide-sprite';
    holder.style.display = 'none';
    holder.innerHTML = await res.text();
    document.body.prepend(holder);
  } catch { /* the labels still read without them */ }
}

/** An inline icon. `title` is required wherever the icon is the only label —
 *  an unlabelled pictogram is the emoji problem with better provenance. */
export function icon(name, { size = 15, cls = '', title = '' } = {}) {
  return `<svg width="${size}" height="${size}" class="inline-block shrink-0 align-[-2px] ${cls}"
    aria-hidden="${title ? 'false' : 'true'}" ${title ? `role="img"` : ''}
    ><use href="#i-${esc(name)}"/>${title ? `<title>${esc(title)}</title>` : ''}</svg>`;
}

/* ════════════════════════════════════════════════════════════════════════
 * Shared "was this right?" feedback control (2026-09-23 consolidation)
 *
 * chat.js's per-turn vote (thumbs-up/minus/thumbs-down, Lucide icons) and
 * feedback.js's per-question "Was this right?" bar (plain "Right/Partly/
 * Wrong" text links) were built independently against the IDENTICAL
 * agree/partly/disagree vocabulary, posting to the SAME `/api/feedback/
 * answer` endpoint — never unified. This is the one shared renderer both
 * now use for the button markup + icons; each caller keeps its own click
 * wiring, its own recording logic, and its own "what happened" message
 * (chat.js's `vote()`/feedback.js's `_sendAnswerVerdict()` are genuinely
 * different beyond the buttons themselves — feedback.js also prompts for an
 * optional comment on "Wrong" and surfaces the server's gap sentence).
 * ════════════════════════════════════════════════════════════════════════ */

/** [vote value, Lucide icon name, title/aria-label, tone] — `tone` is 'ok'
 *  or 'warn', resolved to a theme-appropriate class by `feedbackVotesHtml`
 *  below rather than baked in here, since chat's dark rail and feedback.js's
 *  light question row need different hover colors for the same state (see
 *  `STATE_CHIP_CLASSES`'s own paper/chrome pairing above for the same
 *  reasoning applied to row states). */
export const FEEDBACK_VOTES = [
  [1, 'thumbs-up', 'Right', 'ok'],
  [0, 'minus', 'Partly right — the right idea, incomplete or partly off', 'warn'],
  [-1, 'thumbs-down', 'Wrong', 'warn'],
];

/**
 * Markup for the three vote buttons, `data-vote="<value>"` on each — that
 * attribute is the one thing every caller's click-wiring agrees on.
 *
 * `theme`: 'paper' (light question row, feedback.js's default) or 'chrome'
 * (dark chat rail, chat.js's own background) — picks the same
 * text-state-ok(-on-dark)/text-state-warn(-on-dark)/text-ink(-chrome)-muted
 * pairing used elsewhere for exactly this light/dark split.
 *
 * `dataAttr`/`dataValue`: an extra `data-*` attribute stamped onto every
 * button besides `data-vote`, so a caller's own delegated or per-render
 * listener can find what the vote is FOR without this function knowing
 * anything about turns or question rows — chat.js passes `{dataAttr:
 * 'turn', dataValue: i}`, feedback.js needs none (its bar already carries
 * `data-fb-answer` on the ancestor).
 */
export function feedbackVotesHtml({ theme = 'paper', dataAttr = '', dataValue = '' } = {}) {
  const mutedCls = theme === 'chrome' ? 'text-chrome-muted' : 'text-ink-muted';
  const hoverCls = {
    ok: theme === 'chrome' ? 'text-state-ok-on-dark' : 'text-state-ok',
    warn: theme === 'chrome' ? 'text-state-warn-on-dark' : 'text-state-warn',
  };
  const extraAttr = dataAttr ? ` data-${dataAttr}="${esc(String(dataValue))}"` : '';
  return FEEDBACK_VOTES.map(([v, ic, title, tone]) => `<button type="button" data-vote="${v}"${extraAttr}
      title="${esc(title)}" aria-label="${esc(title)}"
      class="cursor-pointer bg-transparent ${mutedCls} hover:${hoverCls[tone]}"
      >${icon(ic, { size: 16 })}</button>`).join('');
}

/* ════════════════════════════════════════════════════════════════════════
 * Reading an envelope — the honest part
 * ════════════════════════════════════════════════════════════════════════ */

/**
 * The row states from the design's legend. Each is a different SENTENCE,
 * never a different shade of the same one.
 *
 * `answered`  — an analysis ran and produced a result
 * `automatic` — answered without a survey: a direct field, the registry, a chart
 * `partial`   — an analysis ran, but only as a whole-resource rollup, for a
 *               question asked below resource level (design §18.3) — the
 *               "✓ means the mapped analysis ran, not that the question was
 *               answered" failure REVIEW-SURVEY-PANE-285.md's small-findings
 *               list names. Not "nothing was measured" (that is `unrun`);
 *               something real ran, it just cannot name the schema/table/
 *               column the question asked about.
 * `unrun`     — nothing has run yet; there IS a surveyor
 * `human`     — needs someone to say; Enrichment's job
 * `no-surveyor` — nothing has run AND nothing can; no surveyor exists
 * `unclassified` — the catalog does not say how this would be answered
 */
export function rowState(entry, env) {
  const kind = entry.kind || 'unknown';
  if (kind === 'gap') return 'no-surveyor';
  if (kind === 'human') return 'human';
  if (kind === 'unknown') return 'unclassified';
  if (env && env.answerable) {
    if (env.level_mismatch) return 'partial';
    if (needsLensDeclaration(env)) return 'needs-lens';
    // A direct-field answer that is a known "nothing there" (every known
    // fact `nothing_found`, e.g. an empty description) is ∅, not ✓.
    const known = (env.facts || []).filter((f) => f.is_known);
    if (kind === 'direct' && known.length && known.every((f) => f.state === 'nothing_found')) return 'nothing';
    return ['direct', 'registry', 'chart'].includes(kind) ? 'automatic' : 'answered';
  }
  // A `direct`/`chart` row with no declared reader is NOT "not run" --
  // `unrun` promises a real analysis exists and just has not been
  // triggered yet, which is false here: nothing was ever built to answer
  // this one. DATABASE-DIRECT-FIELD-ROWS-IMPLEMENTED.md (2026-09-29, owner
  // live on laz_local_adventureworks / #8810): 11 database rows read
  // `○ not run` for exactly this reason before this fix -- none of them
  // had a surveyor at all. Mirrors facts.py's own `UNDECLARED_KINDS` table
  // (`direct`, `chart`) -- the backend's "answerable from a field, but the
  // catalog does not say which one machine-readably yet" classification.
  // Apply this generally: any `direct`/`chart` row that is not answerable
  // shows `◌ no reader`, never `○ not run` -- not only the 11 named above.
  if (['direct', 'chart'].includes(kind)) return 'no_reader';
  return 'unrun';
}

/** REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.4: `preliminary_fit`'s
 *  `no_requirement_declared` verdict (`lens_declared: false`) is a real,
 *  measured answer -- no lens was supplied, so fit is not a question this
 *  credential can settle -- but it is a statement ABOUT THE LENS, not about
 *  the resource (`compute_preliminary_fit`'s own docstring). Before this, a
 *  question backed by `preliminary_fit` ticked ✓ on exactly this case: a
 *  known fact with a real headline, `env.answerable` true, no level
 *  mismatch -- indistinguishable from a genuine answer to `rowState`'s
 *  existing checks. Checked directly against the fact's own value rather
 *  than against `verdict === 'no_requirement_declared'` (a string this
 *  module would otherwise have to keep in sync with db_derived.py's own
 *  constant) -- `lens_declared` is the one field `compute_preliminary_fit`
 *  sets specifically to answer "was there a lens to check against at all". */
function needsLensDeclaration(env) {
  return (env && env.facts ? env.facts : []).some(
    (f) => f.analysis_id === 'preliminary_fit' && f.value && f.value.lens_declared === false,
  );
}

/** Fully answered = a checkmark's worth of answer, not merely "something ran".
 *  A `level_mismatch` envelope has `answerable === true` (a real analysis DID
 *  run) but withholds the tick (design §18.3) -- callers that count or gate
 *  on "answered" must use this, not `env.answerable` alone, or the counter
 *  and the row glyph would disagree about the same envelope.
 *
 *  The `preliminary_fit`/`lens_declared` check below is `needsLensDeclaration`'s
 *  own predicate, inlined rather than called: `test_next_renders_text_cross_
 *  check.py` extracts this function's source verbatim and runs it standalone
 *  in node, so a call to a sibling helper defined elsewhere in this file
 *  would be a dangling reference there. Keep both in sync by hand (same
 *  reasoning `_renders_text`'s own docstring gives for its Python/JS pair). */
function isFullyAnswered(env) {
  return !!(env && env.answerable && !env.level_mismatch && !(env.facts || []).some(
    (f) => f.analysis_id === 'preliminary_fit' && f.value && f.value.lens_declared === false,
  ));
}

// The row states this screen distinguishes -- keys into `GLYPH_STATES`
// (glyphs.js), which is where the glyph for each one is actually declared.
// `no-surveyor` reads ◌ here now, not ○ -- REPLY-DESIGNER-ROUND2-DATABASE-
// SCREENS.md §1: it used to share ○ with `unrun`, which the work-list grid
// could only tell apart by colour, breaking the "colour is never the only
// channel" rule. Any key missing from `GLYPH_STATES` would throw here on
// module load, by design -- see `test_one_glyph_table.py`'s "no independent
// literal glyph-to-meaning mapping" bar. `needs-lens` (§2.4) is the fit row's
// own honest state -- same ⚠ family as `human`, its own word.
const GLYPH_KEYS = [
  'answered', 'automatic', 'nothing', 'unrun', 'partial', 'human', 'needs-lens', 'no-surveyor',
  // `no_reader` (glyphs.js's RESERVED-for-G2/G3 entry, now claimed here):
  // same ◌ glyph/family as `no-surveyor` ("no answer here"), its own word
  // ("no reader") -- `no-surveyor` means no MECHANISM exists at all (a
  // catalog `gap`); `no_reader` means a mechanism kind is declared
  // (`direct`/`chart`) but no reader has been wired up for it yet. Distinct
  // states, same reasoning as `unrun` vs `no-surveyor` already had.
  'no_reader',
  'unclassified', 'running', 'error', 'proposal',
];
const GLYPH = Object.fromEntries(GLYPH_KEYS.map((k) => [k, GLYPH_STATES[k].glyph]));

/**
 * State colour, per ground.
 *
 * Hue is back — ALONGSIDE the glyph, never instead of it. Colour is not the
 * only channel carrying meaning here (the glyph and the legend words carry
 * it too), and it does not carry two meanings at once: gold is reserved for
 * "needs your attention" and is not a state role.
 *
 * Two variants per role because one value cannot hold 4.5:1 against both a
 * paper and a chrome ground — see the measurement in tailwind-next.config.js.
 */
// GLYPH-STATE-TONE-COLOR-FIX-IMPLEMENTED.md: the accent color means "you can
// click this" (a control), and must never be used for a STATE. `partial`,
// `human`/`needs-lens`, and `running` used to violate that here.
export const STATE_TONE = {
  answered:      { paper: 'text-state-ok',    chrome: 'text-state-ok-on-dark' },
  partial:       { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
  automatic:     { paper: 'text-state-ok',    chrome: 'text-state-ok-on-dark' },
  nothing: { paper: 'text-state-ok', chrome: 'text-state-ok-on-dark' },
  unrun:         { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
  human:         { paper: 'text-ink',         chrome: 'text-chrome-ink' },
  // Same role as `human` -- "needs your attention" -- §2.4's fit row.
  'needs-lens':  { paper: 'text-ink',         chrome: 'text-chrome-ink' },
  'no-surveyor': { paper: 'text-state-gap',   chrome: 'text-state-gap-on-dark' },
  no_reader:     { paper: 'text-state-gap',   chrome: 'text-state-gap-on-dark' },
  unclassified:  { paper: 'text-ink-muted',   chrome: 'text-chrome-muted' },
  running:       { paper: 'text-ink-muted',   chrome: 'text-chrome-muted' },
  error:         { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
  // Same role as `unrun`/`error` -- "needs your attention" -- because a
  // proposal IS an attention-needing decision, not a different flavour of
  // answered or automatic.
  proposal:      { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
};

const tone = (st, ground = 'paper') =>
  (STATE_TONE[st] || STATE_TONE.unclassified)[ground];

/* ════════════════════════════════════════════════════════════════════════
 * Chrome
 * ════════════════════════════════════════════════════════════════════════ */

export function renderTopBar() {
  // The name is the resource's menu button (REPLY-DESIGNER-RESOURCE-CONTROLS-
  // PLACEMENT.md §1). `data-slug` carries the bare slug for feedback.js, which
  // used to read it from the text; the text now also shows the "▾".
  const slugEl = $('scope-slug');
  slugEl.dataset.slug = state.selectedSlug || '';
  slugEl.textContent = state.selectedSlug ? `${state.selectedSlug} ▾` : 'no resource selected';
  slugEl.disabled = !state.selectedSlug;
  slugEl.onclick = state.selectedSlug ? (e) => { e.stopPropagation(); toggleResourceMenu(); } : null;
  // Whatever the menu or its panel was about is no longer on screen once the
  // selection moves: a confirmation naming resource A must never outlive the
  // switch to B.
  if (!state.selectedSlug || $('resource-menu')?.dataset.slug !== state.selectedSlug) closeResourceMenu();
  const panel = $('resource-controls-panel');
  if (panel && panel.dataset.slug !== (state.selectedSlug || '')) panel.remove();
  // `data-entity-type` lets feedback.js's Questions-checklist "Was this
  // right?" bar (deliberately independent of this module, plain DOM reads
  // only) attribute an answer-feedback POST to the right resource type
  // without importing state itself. Kept alongside the slug it already reads
  // off this same element so the two can never fall out of sync.
  $('scope-slug').dataset.entityType = requireKind('renderTopBar', apiEntityType(state.resourceType));
  const inv = state.investigations.find((i) => i.slug === state.investigation);
  $('investigation-name').textContent = state.investigation
    ? (inv?.display_name || state.investigation)
    : 'No investigation';
  // RULING-NAV-GROUPING.md §3: the ad-hoc/bound-to-a-Project distinction
  // "deserves permanent visibility" — a fact about the investigation, read
  // straight off `egeria_binding` (investigations.py/registry.py
  // ProjectRegistry.BINDING_LOCAL/BINDING_EGERIA), never re-derived.
  // Deliberately NOT gated on `egeria_project_guid` being non-empty:
  // registry.py's own comment on this column says `egeria` "has one, or is
  // meant to" -- a promotion that has not run yet and a purely local
  // investigation look identical from a null GUID alone, and only the
  // `egeria_binding` column records which one was actually chosen. Empty
  // (not hidden) when there is no current investigation, so the badge does
  // not read as stale leftover state.
  const scopeEl = $('investigation-scope');
  if (scopeEl) {
    scopeEl.textContent = inv
      ? (inv.egeria_binding === 'egeria' ? '· bound to Egeria Project' : '· ad hoc')
      : '';
    scopeEl.title = inv?.egeria_project_qualified_name || '';
  }
  $('whoami').textContent =
    (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || 'not signed in';
  $('activity-count').textContent =
    state.counts.activity === null ? '–' : state.counts.activity;
  const link = $('switch-ui');
  // Same resource in the Classic UI (`/classic?resource=` is read there).
  link.textContent = 'Classic';
  link.href = oldUiHref();
  link.title = state.selectedSlug
    ? 'Open this resource in the Classic UI'
    : 'Open the Classic UI';
  wireActivityButton();
  wireAdminButton();
  wireWhoamiButton();
}

/** The connection-details popover (PI-137): the signed-in user, which Egeria, and which build is serving. */
let whoamiButtonWired = false;
function wireWhoamiButton() {
  if (whoamiButtonWired) return;
  const btn = $('whoami');
  if (!btn) return;
  whoamiButtonWired = true;
  btn.addEventListener('click', () => openConnectionPopover(document, btn, { me: state.me }));
}

/** Activity is a persistent header surface, not a STAGES entry (see
 *  activity.js's own top-of-file comment) — wired once, like the sidebar
 *  drawer and text-size controls above, rather than per render. */
let activityButtonWired = false;
function wireActivityButton() {
  if (activityButtonWired) return;
  const btn = $('activity-open-btn');
  if (!btn) return;
  activityButtonWired = true;
  btn.addEventListener('click', () => openActivityPanel());
}

/** Admin, same pattern as Activity above — chrome-level, wired once. See
 *  next/admin/index.js's own header comment. */
let adminButtonWired = false;
function wireAdminButton() {
  if (adminButtonWired) return;
  const btn = $('admin-open-btn');
  if (!btn) return;
  adminButtonWired = true;
  btn.addEventListener('click', () => openAdminPanel());
}

/** The app's own text-size control: 100 / 112 / 125%.
 *
 * Wired once, and persisted — a size someone chose because they could not
 * read the pane is not a per-session preference. It is stored globally rather
 * than per work list, deliberately: unlike the digest toggle, this is a fact
 * about the reader, not about a list.
 */
let textSizeWired = false;
function wireTextSize() {
  if (textSizeWired) return;
  textSizeWired = true;
  const host = $('textsize');
  if (!host) return;
  const apply = (v) => {
    document.documentElement.setAttribute('data-textsize', String(v));
    host.querySelectorAll('[data-textsize]').forEach((b) =>
      b.setAttribute('aria-pressed', b.dataset.textsize === String(v) ? 'true' : 'false'));
    LS.set('re-next.textSize', String(v));
  };
  host.querySelectorAll('[data-textsize]').forEach((b) =>
    b.addEventListener('click', () => apply(b.dataset.textsize)));
  apply(LS.get('re-next.textSize', '100'));
}

/** Below 780px the sidebar is a drawer, and this is the way in.
 *
 * Wired ONCE. This sits beside a function that re-renders, and attaching per
 * render stacked a second listener on the same button: the class was toggled
 * on and then straight back off, so the drawer never appeared and nothing
 * looked broken enough to suspect it.
 */
let sidebarWired = false;
function wireSidebarDrawer() {
  if (sidebarWired) return;
  sidebarWired = true;
  const btn = $('sidebar-toggle');
  if (!btn) return;
  const set = (open) => {
    document.body.classList.toggle('sidebar-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    set(!document.body.classList.contains('sidebar-open'));
  });
  // A tap outside closes it. At this width the drawer covers most of the
  // content, so leaving it open is never what the next tap meant.
  document.addEventListener('click', (e) => {
    if (!document.body.classList.contains('sidebar-open')) return;
    if (!e.target.closest('.pane-sidebar')) set(false);
  });
}

/** A middot separator between groups whose relative order carries no
 *  meaning (RULING-NAV-GROUPING.md §3: "chevrons inside the run, middots
 *  outside it"). Muted and `aria-hidden` — it is a visual grouping cue, not
 *  content a screen reader should announce as a word. */
const NAV_MIDDOT = '<span class="px-1 text-chrome-muted" aria-hidden="true">·</span>';

/** A chevron separator between two stages that ARE sequential — the run
 *  only. Same visibility treatment as the middot above. */
const NAV_CHEVRON = '<span class="px-1 text-chrome-muted" aria-hidden="true">›</span>';

/** One nav item's markup. `number` is passed only for `run`-class stages —
 *  it is what puts "1 " ahead of "Scouting", never a second, independently
 *  maintained ordering. */
function navItemHtml(s, { number } = {}) {
  const active = s.id === state.stage;
  const label = `${number ? `${number} ` : ''}${esc(s.label)}`;
  if (s.class === 'frame') {
    return `<button data-stage="${s.id}" class="cursor-pointer bg-transparent px-3 py-[9px] font-heading
      text-accent-on-dark ${active ? 'border-b-2 border-accent' : 'border-b-2 border-transparent'}">${label}</button>`;
  }
  if (!s.built) {
    // Marked, not dimmed: chrome-muted is a 6.7:1 role, not a fade.
    // DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md §3: `unbuilt` was read here
    // and set nowhere — every STAGES entry declares `built`, never
    // `unbuilt`, so this branch was dead and six stages rendered as live.
    // Inverted to read the flag that actually exists, so a stage added
    // without `built` is honest by default.
    return `<span${deferredAttrs(false, { title: 'Not implemented — zero rows in the analysis catalog and the activity log' })}
      class="whitespace-nowrap px-3 pb-[1px] pt-[9px] text-chrome-muted">${label}</span>`;
  }
  return `<button data-stage="${s.id}" class="cursor-pointer bg-transparent px-3 py-[9px]
    ${active ? 'border-b-2 border-accent text-chrome-ink' : 'border-b-2 border-transparent text-chrome-muted hover:text-chrome-ink'}"
    >${label}</button>`;
}

export function renderIntentNav() {
  const nav = $('intent-nav');

  // Three groups, read off `STAGES.class` — never a second hardcoded list of
  // which ids go where (RULING-NAV-GROUPING.md §2). The run's relative order
  // in `STAGES` is already correct (Scouting..Curate, in that order) even
  // though Understanding's array position sits between Enrichment and
  // Curate — filtering by class pulls it out of the run's sequence, which is
  // the whole point: the run stays contiguous and numbered 1-6, and
  // Understanding renders with the other cross-cutting item instead.
  const frameItems = STAGES.filter((s) => s.class === 'frame');
  const runItems = STAGES.filter((s) => s.class === 'run');
  const crossItems = STAGES.filter((s) => s.class === 'cross-cutting');

  const frameHtml = frameItems.map((s) => navItemHtml(s)).join('');
  const runHtml = runItems
    .map((s) => navItemHtml(s, { number: RUN_ORDER.get(s.id) }))
    .join(NAV_CHEVRON);
  const crossHtml = crossItems.map((s) => navItemHtml(s)).join(NAV_MIDDOT);

  const items = `${frameHtml}${runHtml}${crossHtml ? `${NAV_MIDDOT}${crossHtml}` : ''}`;

  nav.innerHTML = `${items}${NAV_MIDDOT}
    <!-- Work lists sit at the end of the frame row because, like
         Investigation, they are a FRAME around the stages rather than a stage:
         Investigation is why a body of work exists, a work list is which
         resources it covers. Fixed position, always present — the matrix had
         no front door before this, only a sidebar section and a crumb that
         existed once you had already found it. A middot precedes it, same as
         between the run and the cross-cutting group, since it too carries no
         sequence relationship to what comes before it. -->
    <span id="worklist-nav" class="flex items-center"></span>
    <span class="ml-auto flex gap-s2 text-subtab">
      <button id="rfa-drawer-toggle" type="button"
        class="cursor-pointer bg-transparent px-[10px] py-[9px] text-accent-on-dark">RFAs <span id="rfa-count" class="tnum">${
        state.counts.rfas === null ? '–' : state.counts.rfas}</span></button>
      <button id="chat-toggle" aria-expanded="true"
        class="cursor-pointer bg-transparent px-[10px] py-[9px] text-accent-on-dark">Chat ×</button>
    </span>`;

  renderWorkListNav();

  $('rfa-drawer-toggle').addEventListener('click', () => toggleRfaDrawer(state.selectedSlug || ''));

  $('chat-toggle').addEventListener('click', () => {
    const nowOpen = !$('app-grid').classList.contains('rail-closed');
    setRailOpen(!nowOpen, { persist: !shellIsNarrow() });
  });
  setRailOpen(railIsOpen() && !shellIsNarrow(), { persist: false });

  wireSidebarDrawer();
  wireTextSize();

  nav.querySelectorAll('button[data-stage]').forEach((b) => {
    b.addEventListener('click', () => {
      // A stage with leading tabs (stage-tabs.js) always opens on its first one, never on a sub-tab carried
      // over from another stage (owner, 2026-10-11: no landing preference).
      const enteringTabbed = b.dataset.stage !== state.stage && stageTabsOn(b.dataset.stage, state.resourceType);
      state.stage = b.dataset.stage;
      if (stageClassOf(state.stage) === 'run') state.lastRunStage = state.stage;
      // ENRICHMENT-E1-CONTEXT-TAB: "Enrichment opens on Context, Context tab
      // is the default." Only redirects the module-level default
      // ('questions') — a subTab the person deliberately chose on another
      // stage (Survey & analyses, Disposition, ...) survives the switch
      // unchanged, same as every other stage-to-stage navigation today.
      if (enteringTabbed || state.subTab === 'questions') state.subTab = stageDefaultSubTab();
      writeUrl();
      renderIntentNav();
      loadPane();
    });
  });
}

/** A stage's class, read from `STAGES` — the one place it is declared. */
function stageClassOf(id) {
  return STAGES.find((s) => s.id === id)?.class;
}

/** A work list is a bench against one STAGE's questions, so it can only be
 *  open on a run stage (REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md §5).
 *  Opening one while a frame or cross-cutting stage is showing moves to the
 *  last run stage first; without that, `loadPane()` would close the list
 *  again the moment it opened. */
function ensureRunStageForList() {
  if (stageClassOf(state.stage) === 'run') return;
  state.stage = state.lastRunStage || 'scouting';
  renderIntentNav();
}

/** The list OF work lists — the front door the matrix never had. */
const WORK_LIST_DEFINITION =
  'An investigation says why, and its scope says which resources, of any kind; '
  + 'a work list is a bench: one kind of resource, laid out against one stage’s '
  + 'questions, so you can decide which belong in a scope.';

/** An investigation's name for display; the slug only when it is not loaded. */
function investigationName(slug) {
  if (!slug) return '';
  return (state.investigations.find((i) => i.slug === slug) || {}).display_name || slug;
}

/** What the work-list actions need from the shell (worklist-actions.js). */
function workListHost() {
  return {
    currentInvestigation: () => state.investigation,
    investigations: () => state.investigations,
    investigationName,
    chooseInvestigationThen,
    makeCurrent: (slug) => setInvestigation(slug),
    refreshScope: async () => { await loadWorkingSet(); await refreshOpenInvestigation(state.investigation); renderSidebar(); },
    refreshAfterStart: async () => {
      await refreshInvestigationsAndSidebar();
      try { state.workLists = await listWorkLists(); } catch { /* the sidebar keeps what it had */ }
    },
  };
}

/** The index's row actions: add the list's members, or start an investigation from it. */
function bindWorkListIndexActions(el) {
  const done = async (out) => {
    try { state.workLists = await listWorkLists(); } catch { /* keep */ }
    state.workListIndexNote = out.noteHtml;
    renderSidebar();
    await loadPane();
  };
  el.querySelectorAll('[data-wl-add]').forEach((b) => b.addEventListener('click', () => {
    const list = state.workLists.find((w) => w.slug === b.dataset.wlAdd);
    if (list) addListToInvestigation({ list, host: workListHost(), onDone: done });
  }));
  el.querySelectorAll('[data-wl-start]').forEach((b) => b.addEventListener('click', () => {
    const list = state.workLists.find((w) => w.slug === b.dataset.wlStart);
    if (list) openStartFromList({ list, host: workListHost(), onDone: done });
  }));
}

function workListIndexHtml() {
  const { benches } = splitWorkLists();
  const definition = `<p class="mb-s3 max-w-[70ch] text-answer text-ink" data-wl-definition>${esc(WORK_LIST_DEFINITION)}</p>`;
  if (!benches.length) {
    return `${subTabsHtml()}
      <h3 class="m-0 font-heading text-name font-normal">No work lists yet</h3>
      <div class="my-s3 h-px bg-rule"></div>
      ${definition}
      <p class="max-w-[70ch] text-answer text-ink">
        Make one from the sidebar:
        <strong>Select</strong>, tick some resources, then <strong>save as work list</strong>.
      </p>`;
  }
  return `${subTabsHtml()}
    <h3 class="m-0 font-heading text-name font-normal">Work lists</h3>
    <div class="my-s3 h-px bg-rule"></div>
    ${definition}
    <div id="wl-index-note" class="mb-s2 text-caveat text-ink">${state.workListIndexNote || ''}</div>
    <div class="flex flex-col">
      ${benches.map((w) => {
        const wording = addActWording({ invName: investigationName(state.investigation), count: w.member_count });
        return `<div class="border-b border-rule py-s3 hover:bg-accent-tint">
        <button data-open-wl="${esc(w.slug)}"
          class="block w-full cursor-pointer bg-transparent p-0 text-left">
          <div class="font-heading text-question font-semibold text-ink">${esc(w.display_name)}</div>
          <div class="text-provenance text-ink-muted">
            <span class="tnum">${w.member_count}</span> resources
            ${w.investigation ? ` · ${esc(investigationName(w.investigation))}` : ''}
            ${w.derived_from ? ` · narrowed from ${esc(w.derived_from)}` : ''}
            · ${w.egeria_guid ? 'published to Egeria' : 'not published'}
          </div>
        </button>
        <div class="mt-s2 flex flex-wrap gap-s3 text-caveat">
          <button data-wl-add="${esc(w.slug)}" title="${esc(wording.title)}"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(wording.label)}</button>
          <button data-wl-start="${esc(w.slug)}"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(START_LABEL)}</button>
        </div>
      </div>`;
      }).join('')}
    </div>`;
}

/**
 * The work lists entry, permanently in the nav.
 *
 * The matrix is a view of a SET and every other pane is a view of one
 * resource, so there was nowhere on the Questions tab to put a route to it —
 * a single repo is not a set. The consequence was that the matrix had no
 * front door: you reached it from a sidebar section, or from a crumb that
 * only existed if you had already been there.
 *
 * ONE control, in a fixed place, doing both jobs: it names the open list when
 * you are in one, and takes you to the index when you are not. A separate
 * "back to X" crumb beside it would be two things for one job, which is the
 * complaint that retired the emoji.
 */
function renderWorkListNav() {
  const el = $('worklist-nav');
  if (!el) return;
  const byslug = (sl) => state.workLists.find((w) => w.slug === sl);
  const open = state.workListSlug ? byslug(state.workListSlug) : null;
  const last = !open && state.lastWorkListSlug ? byslug(state.lastWorkListSlug) : null;
  const n = splitWorkLists().benches.length;
  const active = Boolean(state.workListSlug || state.workListIndex);

  // TWO controls, because they are two jobs — not one control with two
  // meanings. The index is "show me the sets"; the return is "put me back in
  // the one I was reading". Collapsing them cost a click on the path people
  // actually take, which is matrix -> a question -> back.
  el.innerHTML = `<button data-act="worklists"
      title="${open ? 'Go back to the list of work lists' : 'Compare a set of resources as rows x questions'}"
      class="cursor-pointer whitespace-nowrap bg-transparent px-3 py-[9px] ${
        active ? 'border-b-2 border-accent text-accent-on-dark'
               : 'border-b-2 border-transparent text-chrome-muted hover:text-chrome-ink'}"
      >▦ ${open ? esc(open.display_name) : `Work lists${n ? ` <span class="tnum">${n}</span>` : ''}`}</button>
    ${last ? `<button data-act="back-to-matrix"
      title="Back to the matrix you were reading"
      class="cursor-pointer whitespace-nowrap bg-transparent px-2 py-[9px] text-chrome-muted hover:text-chrome-ink"
      style="border-bottom:1px dashed currentColor">↩ ${esc(last.display_name)}</button>` : ''}`;

  el.querySelector('[data-act="worklists"]').addEventListener('click', () => {
    state.workListSlug = null;
    state.workListIndex = true;
    writeUrl(); renderSidebar(); loadPane();
  });
  el.querySelector('[data-act="back-to-matrix"]')?.addEventListener('click', () => {
    state.workListSlug = state.lastWorkListSlug;
    state.workListIndex = false;
    ensureRunStageForList();
    writeUrl(); renderSidebar(); loadPane();
  });
}

/**
 * The perspective row.
 *
 * The count and the residue are REQUIRED, not decoration: today a held
 * perspective that matched nothing is indistinguishable from one that
 * matched and passed. A chip that can only ever empty the list must say so.
 */
function renderPerspectiveRow() {
  const row = $('perspective-row');
  const held = state.activePerspectives;
  const total = state.perspectives.length;

  // Which perspectives match nothing AT THIS STAGE.
  //
  // Computed from the stage's UNFILTERED question set, never from the
  // filtered one. Reading it off the filtered set inverts the answer in
  // exactly the case the chip exists for: hold a perspective that matches
  // nothing, the filtered set is empty, so "matched" is empty too and the
  // chip reports nothing unusual. Seen live — `Privacy` hid all five
  // questions and rendered as an ordinary held chip.
  const matched = new Set();
  for (const q of state.allQuestions || []) {
    for (const p of q.perspectives || []) matched.add(p);
  }
  const questionsLoaded = Array.isArray(state.allQuestions);

  const chips = state.perspectives.map((p) => {
    const isHeld = held.has(p);
    if (isHeld && questionsLoaded && !matched.has(p)) {
      return `<button data-persp="${esc(p)}" class="cursor-pointer rounded-pill border border-dashed
        border-chrome-muted bg-transparent px-[9px] py-[2px] text-chrome-muted"
        >${esc(p)} · no questions here</button>`;
    }
    return `<button data-persp="${esc(p)}" class="cursor-pointer rounded-pill bg-transparent px-[9px] py-[2px]
      ${isHeld ? 'border border-accent text-accent-on-dark' : 'border border-chrome-line text-chrome-muted hover:border-accent'}"
      >${esc(p)}</button>`;
  }).join('');

  // The residue. Held perspectives narrow the checklist; how much they
  // narrowed it is the thing the old row never said.
  const shown = state.questions.length;
  const total_q = (state.allQuestions || []).length;
  const residue = !questionsLoaded || !held.size
    ? ''
    : `<span class="ml-auto text-chrome-muted"><span class="tnum">${shown}</span> of
       <span class="tnum">${total_q}</span> questions shown ·
       <span class="tnum">${Math.max(0, total_q - shown)}</span> hidden</span>`;

  row.innerHTML = `
    <span class="mr-1 font-heading uppercase tracking-caps text-caps">Perspective ·
      <span class="tnum">${held.size}</span> of <span class="tnum">${total}</span></span>
    ${chips}
    ${residue}`;

  row.querySelectorAll('button[data-persp]').forEach((b) => {
    b.addEventListener('click', () => {
      const p = b.dataset.persp;
      if (state.activePerspectives.has(p)) state.activePerspectives.delete(p);
      else state.activePerspectives.add(p);
      writeUrl();
      loadPane();
    });
  });
}


/* ════════════════════════════════════════════════════════════════════════
 * Diagrams and charts
 *
 * They live in the CONTENT PANE, on paper. That is a real dividend of the
 * dark-chrome/paper-content split: Mermaid, Plotly and Kroki all default to
 * a light ground, so on paper they need no dark override and no second
 * theme — which is what the current dark UI has to fight for all three.
 *
 * Every renderer is bound to the TOKEN LAYER rather than to hardcoded
 * values, and the token values are read back off the live stylesheet rather
 * than restated here. Restating them would put the palette in two places,
 * which is the thing the token layer exists to prevent.
 *
 * Diagrams do NOT use the body serif. SVG text at small sizes in Lora or
 * Cormorant is a bad trade, so node labels, axis ticks and legends take the
 * `diagram` font token. This is the one place the type system is
 * deliberately overridden.
 * ════════════════════════════════════════════════════════════════════════ */

let _themeProbe = null;

/** Read a token's computed value off a probe element carrying its class.
 *  One source of truth: tailwind-next.config.js, via the built stylesheet. */
export function tokens() {
  if (!_themeProbe) {
    _themeProbe = document.createElement('div');
    _themeProbe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
    _themeProbe.innerHTML = `
      <span data-t="paper" class="bg-paper"></span>
      <span data-t="paper-surface" class="bg-paper-surface"></span>
      <span data-t="ink" class="text-ink"></span>
      <span data-t="ink-muted" class="text-ink-muted"></span>
      <span data-t="rule-strong" class="text-rule-strong"></span>
      <span data-t="accent" class="text-accent"></span>
      <span data-t="state-ok" class="text-state-ok"></span>
      <span data-t="state-warn" class="text-state-warn"></span>
      <span data-t="state-gap" class="text-state-gap"></span>
      <span data-t="font-diagram" class="font-diagram"></span>`;
    document.body.appendChild(_themeProbe);
  }
  const read = (name, prop) => {
    const el = _themeProbe.querySelector(`[data-t="${name}"]`);
    return el ? getComputedStyle(el)[prop] : '';
  };
  return {
    paper: read('paper', 'backgroundColor'),
    paperSurface: read('paper-surface', 'backgroundColor'),
    ink: read('ink', 'color'),
    inkMuted: read('ink-muted', 'color'),
    rule: read('rule-strong', 'color'),
    accent: read('accent', 'color'),
    ok: read('state-ok', 'color'),
    warn: read('state-warn', 'color'),
    gap: read('state-gap', 'color'),
    font: read('font-diagram', 'fontFamily'),
  };
}

/** Load a vendored script once. Both are already in static/vendor. */
const _scripts = new Map();
export function loadScript(src) {
  if (_scripts.has(src)) return _scripts.get(src);
  const p = new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  _scripts.set(src, p);
  return p;
}

/**
 * Form follows ANSWER SHAPE, not the model's preference.
 *
 *   scalar / short verdict     inline in the rail
 *   ranked list                table; promoted when wide
 *   anything over time         chart, promoted to the pane
 *   relationships / topology   Mermaid, always in the pane
 *   one question, many repos   the work-list grid (does not exist yet)
 *
 * A diagram cannot live in the rail: it is at most 290px wide and a topology
 * graph there is unreadable. So the rail shows a marker and promotes.
 */
export function answerForm(turn) {
  if (turn.mermaid || turn.graphviz) return 'diagram';
  if (turn.chart) return 'chart';
  if (turn.listSources && turn.listSources.length) return 'list';
  return 'inline';
}

/* ── Understanding: the charts pane ──────────────────────────────────── */

/**
 * Every chart kind, with what each actually holds for this resource.
 *
 * The three outcomes are kept apart, because they are three different
 * sentences and this app's whole discipline is not folding them together:
 *   - a figure with series      -> render it
 *   - a 200 with no series      -> "nothing recorded yet", NOT an error and
 *                                  NOT an empty chart, which would read as
 *                                  a measured zero
 *   - a failed call             -> say the call failed, name the reason
 */
/** Axis labels, a stated zero, and a title that names the measurement.
 *
 * Three faults compounded here: an unhighlighted selector meant you did not
 * know WHICH chart you were looking at; not knowing that, an unlabelled
 * vertical scale had no referent; and a bare date axis left nothing to anchor
 * it to. The selector is fixed above, which half-fixes these by context —
 * this states them outright.
 *
 * **If the axis does not start at zero, it says so on the axis.** A truncated
 * scale that does not admit it is the oldest chart lie there is.
 */
function chartAxes(entry) {
  const layout = { ...(entry.fig.layout || {}) };
  const [measure, range] = CHART_MEASURE[entry.kind] || [entry.label.toLowerCase(), ''];
  const span = entry.first && entry.last && entry.first !== entry.last
    ? `${entry.first} – ${entry.last}` : (entry.last || '');
  layout.title = {
    text: `${entry.label} — ${measure}${range ? `, ${range}` : ''}`,
    subtitle: undefined,
  };
  // Radar has no cartesian axes to label; everything else gets both.
  if ((entry.fig.data || []).some((t) => t.type === 'scatterpolar')) return layout;

  const ys = (entry.fig.data || []).flatMap((t) => (t.y || []).filter((v) => typeof v === 'number'));
  const min = ys.length ? Math.min(...ys) : 0;
  const zeroed = min <= 0;
  layout.yaxis = {
    ...(layout.yaxis || {}),
    title: { text: `${measure}${range ? ` (${range})` : ''}${
      zeroed ? '' : ' — axis does not start at zero'}` },
    showticklabels: true,
    rangemode: zeroed ? 'tozero' : 'normal',
  };
  layout.xaxis = {
    ...(layout.xaxis || {}),
    title: { text: entry.timeAxis
      ? 'Measurement date — plotted to scale, so gaps are real'
      : (layout.xaxis?.title?.text || layout.xaxis?.title || '') },
    showticklabels: true,
  };
  layout.margin = { l: 70, r: 20, t: 54, b: 62, ...(layout.margin || {}) };
  return layout;
}

/** A REAL TIME AXIS, not evenly spaced points.
 *
 * Surveys run irregularly. Three measurements at 14, 68 and 88 days drawn
 * equidistant invent a steady cadence that never happened — and the shape of
 * the line is precisely what someone reads off it. Plotted to scale, a long
 * unmeasured gap reads as a gap rather than as a slow steady climb: the same
 * argument as the staleness rule, drawn instead of marked.
 *
 * The last point is labelled with its value, because that is the number
 * someone came for.
 */
function timeAxisData(entry) {
  const data = entry.fig.data || [];
  if (!entry.timeAxis) return data;
  return data.map((t, i) => {
    if (i > 0 || !(t.x || []).length) return t;
    const y = t.y || [];
    const last = y.length - 1;
    return {
      ...t,
      mode: 'lines+markers+text',
      text: y.map((v, j) => (j === last ? String(Math.round(v * 10) / 10) : '')),
      textposition: 'top left',
      marker: { ...(t.marker || {}), size: 7 },
    };
  });
}

/** Not every chart endpoint returns a Plotly figure.
 *
 * `survey_history` returns `{dates, total_files}` — raw series, no `data`
 * array — because the current UI builds that figure client-side. Everything
 * else returns `fig.to_json()`. Reading `fig.data` on it therefore found no
 * traces, and `/next` reported **"nothing recorded yet"** on every repository
 * in the corpus while the registry held ten points per repo: 6,107 → 6,423
 * files over a month on egeria-workspaces alone.
 *
 * That is the failure this project keeps hunting, in a new place: a fact about
 * the ENDPOINT rendered as a fact about the RESOURCE. And it hid in the one
 * state that looks like diligence — an honest-sounding empty.
 *
 * The endpoint is not changed, because the current UI depends on this shape;
 * the adapting happens here, where the assumption was.
 */
export function asFigure(raw) {
  if (!raw || Array.isArray(raw.data)) return raw;
  if (Array.isArray(raw.dates) && Array.isArray(raw.total_files)) {
    return {
      data: [{
        type: 'scatter', mode: 'lines+markers',
        x: raw.dates, y: raw.total_files, name: 'Total files',
      }],
      layout: { xaxis: { title: 'Surveyed' }, yaxis: { title: 'Files' } },
    };
  }
  return raw;
}

/** Where a chart means something different from a number elsewhere, say so.
 *
 * The radar was cut from the Dashboard because `repository_health` composes
 * FOUR sub-scores on 0-100 while this plots FIVE different axes on 0-10 — two
 * models of one word, forty pixels apart. Cutting it there and leaving it here
 * did not resolve that; it moved the collision one pane away, where it is
 * harder to notice rather than absent.
 *
 * It stays, because on this pane it is not sitting beside its rival and the
 * series is real. It now says what it is not. The proper fix is the designer's
 * own: plot the four published sub-scores on their own scale, which needs an
 * endpoint that does not exist yet.
 */
const CHART_CAVEATS = {
  health: 'These five axes on a 0–10 scale are not the same composition as '
        + 'the Dashboard\'s repository_health score, which combines four '
        + 'sub-scores on 0–100. Two models of the same word: read them '
        + 'separately, and do not compare the numbers.',
};

/** Every date-shaped x-value in a figure, sorted. */
export function allPointDates(traces) {
  const out = [];
  for (const t of traces || []) {
    for (const x of t.x || []) {
      const v = String(x);
      if (/^\d{4}-\d{2}-\d{2}/.test(v)) out.push(v.slice(0, 10));
    }
  }
  return out.sort();
}

/** Same threshold as the grid's, and the same placeholder caveat. */
export function chartIsStale(dateStr) {
  const d = (Date.now() - Date.parse(dateStr)) / 86400000;
  return Number.isFinite(d) && d >= 7;
}

// loadChartsPane() moved to next/stages/understanding.js (PLAN-FINISH-REPOS.md
// Part 2 §1) — imported below, alongside the other stage modules.

/** Render one figure into the pane, themed from the token layer. */
export async function drawChart(entry, { bodyId = 'chart-body' } = {}) {
  const body = $(bodyId);
  const canvasId = bodyId === 'chart-body' ? 'chart-canvas' : `${bodyId}-canvas`;
  if (!body || !entry) return;
  body.innerHTML = `<div class="text-caveat text-ink-muted">Drawing ${esc(entry.label)}…</div>`;
  try {
    await loadScript('/static/vendor/plotly.min.js');
    body.innerHTML = `<div id="${canvasId}" style="height:min(62vh,560px)"></div>
      <div class="mt-s2 text-provenance text-ink-muted">${esc(entry.label)} ·
        <span class="tnum">${entry.points}</span> observation(s) in
        <span class="tnum">${entry.traces}</span> series${
        entry.last ? ` · latest ${esc(entry.last)}${
          chartIsStale(entry.last) ? ' — nothing newer has been recorded' : ''}` : ''} ·
        from the registry's recorded history · no retrieval</div>
      ${entry.points === 1 ? `<div class="mt-s1 text-caveat text-ink-muted">
        One observation. This is a value, not a trend — the shape of a chart
        with a single point is drawn by the axes, not by the data.</div>` : ''}
      ${CHART_CAVEATS[entry.kind]
        ? `<div class="mt-s1 max-w-[70ch] text-caveat text-accent-ink">${esc(CHART_CAVEATS[entry.kind])}</div>`
        : ''}`;
    await window.Plotly.newPlot($(canvasId), timeAxisData(entry),
                                chartLayout(chartAxes(entry)),
                                { displaylogo: false, responsive: true });
  } catch (err) {
    body.innerHTML = `<div class="text-answer text-state-warn">
      ${esc(entry.label)} could not be drawn: ${esc(err.message)}</div>`;
  }
}

/**
 * The figure's own layout, with the token layer laid over it.
 *
 * Plotly's default template is a dark-on-white theme of its own; on paper it
 * has to be overridden or the chart is a different design from the page it
 * sits in. `template: undefined` drops that default rather than fighting it
 * property by property.
 */
export function chartLayout(layout = {}) {
  const t = tokens();
  const axis = (a = {}) => Object.assign({
    gridcolor: t.rule, zerolinecolor: t.rule, linecolor: t.rule,
    tickfont: { family: t.font, color: t.inkMuted, size: 11 },
    titlefont: { family: t.font, color: t.inkMuted, size: 11 },
  }, a);
  return Object.assign({}, layout, {
    template: undefined,
    paper_bgcolor: t.paper,
    plot_bgcolor: t.paper,
    colorway: [t.accent, t.ok, t.gap, t.warn, t.inkMuted],
    font: { color: t.ink, family: t.font, size: 12 },
    xaxis: axis(layout.xaxis),
    yaxis: axis(layout.yaxis),
    legend: Object.assign({ font: { family: t.font, color: t.ink, size: 11 } }, layout.legend),
    margin: { l: 56, r: 24, t: 24, b: 48 },
  });
}

/** Kroki (egeria-shared-kroki, port 6002) is an optional dependency for
 *  every diagram this app renders — never assumed up. When it cannot be
 *  reached, or answers with anything but 200, the diagram SOURCE (DOT or
 *  Mermaid) is still real evidence and is offered as copyable text rather
 *  than leaving a broken image or a bare error where a picture should be.
 */
function renderRendererUnavailable(source, detail) {
  const id = `diagram-src-${Math.random().toString(36).slice(2, 8)}`;
  return `<div class="rounded-sm border border-rule-strong p-s3">
      <div class="text-answer text-state-warn">renderer unavailable
        — Kroki could not be reached${detail ? `: ${esc(String(detail))}` : ''}</div>
      <div class="mt-s2 text-provenance text-ink-muted">The diagram source below is
        unchanged and can still be read, or pasted into any Graphviz/Mermaid viewer.</div>
      <button data-copy-target="${id}"
        class="mt-s2 cursor-pointer rounded-sm border border-rule-strong bg-transparent
               px-[10px] py-[4px] text-chip text-ink">copy as evidence</button>
      <pre id="${id}" class="mt-s2 max-h-[40vh] overflow-auto whitespace-pre-wrap
        text-provenance text-ink-muted">${esc(source)}</pre>
    </div>`;
}

/** Wires up every `[data-copy-target]` button rendered by
 *  `renderRendererUnavailable` inside `root` — copies the named element's
 *  text to the clipboard, falling back silently (the text is already
 *  visible and selectable) if the Clipboard API is unavailable. */
function bindCopyTargets(root) {
  root.querySelectorAll('[data-copy-target]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pre = root.querySelector(`#${btn.dataset.copyTarget}`);
      if (!pre) return;
      try {
        await navigator.clipboard.writeText(pre.textContent || '');
        const original = btn.textContent;
        btn.textContent = 'copied';
        setTimeout(() => { btn.textContent = original; }, 1500);
      } catch { /* clipboard unavailable — text is still visible/selectable */ }
    });
  });
}

/**
 * Fetch and render a Mermaid or Graphviz diagram (`turn.mermaid` /
 * `turn.graphviz`) into `container` — server-side render via Kroki, pan/zoom
 * wired up, extreme-aspect framing, dropped-style note. Extracted out of
 * `promoteToPane`'s own diagram branch (BY-ANALYSIS-PROGRESSIVE-AND-GRAPH,
 * 2026-09-28) so the By-analysis card body's inline Relationship Graph
 * rendering and the Questions-tab Evidence rail's "open diagram in pane"
 * path go through the exact same renderer — one implementation, two mount
 * points — rather than the card growing its own copy of this logic.
 * `uid` namespaces the inner `#<uid>-svg`/`#<uid>-note` ids so two mounts
 * (e.g. a promoted pane behind a By-analysis card) never collide. */
async function renderDiagramInto(container, turn, uid = 'promoted') {
  const t = tokens();
  // A Graphviz DOT source (the relationship graph) skips mermaidForKroki
  // entirely — those transforms are Mermaid-specific escaping/cap rules
  // that do not apply to DOT — and hits the sibling /graphviz endpoint.
  const isGraphviz = !!turn.graphviz;
  const endpoint = isGraphviz ? '/api/diagrams/graphviz' : '/api/diagrams/mermaid';
  const source = isGraphviz ? turn.graphviz : mermaidForKroki(turn.mermaid).source;
  const prepped = isGraphviz ? { source, droppedStyles: 0 } : mermaidForKroki(turn.mermaid);
  // Server-side render via Kroki — the browser never loads mermaid.js
  // (or a graphviz.js equivalent).
  let res;
  try {
    res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    });
  } catch (networkErr) {
    // Kroki (or the network path to it) is down — not a bad diagram.
    // The DOT/Mermaid source itself is still real evidence, so offer it
    // as text rather than showing a blank/broken image.
    container.innerHTML = renderRendererUnavailable(source, networkErr.message);
    bindCopyTargets(container);
    return;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    if (res.status === 502) {
      // The backend's own "could not reach Kroki" / non-200-from-Kroki
      // signal — same "renderer unavailable, not a broken diagram"
      // treatment as a network-level failure above.
      container.innerHTML = renderRendererUnavailable(source, detail);
      bindCopyTargets(container);
      return;
    }
    throw new ApiError(res.status, detail, endpoint);
  }
  // The endpoint returns the RAW SVG body as image/svg+xml, not JSON —
  // it is a thin proxy to Kroki and hands back exactly what Kroki sent.
  const raw = await res.text();
  if (!raw.includes('<svg')) throw new Error('the renderer returned no SVG');

  container.innerHTML = `
    <div id="${uid}-svg" class="w-full overflow-hidden rounded-sm border border-rule-strong"
      style="height:min(70vh,640px);background:${t.paper}">${raw}</div>
    <div id="${uid}-note" class="mt-s2 text-provenance text-ink-muted"></div>`;

  await loadScript('/static/vendor/svg-pan-zoom.min.js');
  const svgEl = container.querySelector(`#${uid}-svg svg`);
  const note = [];
  if (svgEl) {
    themeSvgElement(svgEl, t);
    // Read the INTRINSIC size before touching it. These diagrams are
    // extreme strips — the real one measures 14102 x 193, a 73:1 ratio —
    // and forcing width AND height to 100% squashed it to an invisible
    // sliver, which is what "blank space where the diagram should be"
    // was. Let svg-pan-zoom own the sizing instead, and say how big the
    // thing actually is so a flat-looking strip is not a surprise.
    const vb = (svgEl.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    const w = Math.round(vb[2] || svgEl.getBoundingClientRect().width);
    const h = Math.round(vb[3] || svgEl.getBoundingClientRect().height);
    svgEl.removeAttribute('width');
    svgEl.removeAttribute('height');
    svgEl.style.width = '100%';
    svgEl.style.height = '100%';
    svgEl.style.maxWidth = 'none';       // mermaid sets an inline max-width
    if (window.svgPanZoom) {
      const pz = window.svgPanZoom(svgEl, {
        controlIconsEnabled: true, fit: true, contain: true, center: true,
        minZoom: 0.05, maxZoom: 60,
      });
      // `fit` fits the LIMITING dimension, which for a 73:1 strip means
      // fitting the width and leaving the diagram 8px tall — visually
      // indistinguishable from an empty box, and exactly what "blank
      // space where the diagram should be" looked like.
      //
      // For an extreme aspect the useful opening view is fit-to-HEIGHT
      // with the left edge in view: nodes are legible and you pan
      // sideways. Capped, so a pathological ratio cannot zoom to a pixel.
      try {
        const sz = pz.getSizes();
        const vbW = sz.viewBox.width;
        const vbH = sz.viewBox.height;
        if (vbW && vbH && vbW / vbH > 4) {
          const shownH = sz.width * (vbH / vbW);       // height after fit-to-width
          const factor = Math.min(sz.height / shownH, 12);
          if (factor > 1.2) {
            pz.zoom(pz.getZoom() * factor);
            // Let the library do the arithmetic. Computing the pan by
            // hand put the content at y = -609 — above the box, zero
            // nodes on screen, which measures as "53 nodes rendered at
            // 96x39" and looks like an empty white panel. Centre, then
            // move only the horizontal axis to the left edge.
            pz.center();
            pz.pan({ x: 0, y: pz.getPan().y });
          }
        }
      } catch (e) {
        // Pan/zoom tuning is a nicety; a diagram that opened badly
        // framed still beats one that threw on the way in.
        console.warn('could not frame the diagram:', e);
      }
    }
    if (w && h) {
      note.push(`<span class="tnum">${w}</span> × <span class="tnum">${h}</span> at full size`
        + (w / h > 6 ? ' — a wide strip; scroll-zoom or use the controls' : ''));
    }
  }
  if (prepped.droppedStyles) {
    // Say what was given up, and why. A silently unstyled node is the
    // kind of small loss this project keeps finding months later.
    note.push(`<span class="text-accent-ink">the dashed “pending” styling on
      <span class="tnum">${prepped.droppedStyles}</span> node(s) was dropped —
      this renderer refuses a diagram that styles more than
      <span class="tnum">${KROKI_MAX_CLASSED_NODES}</span></span>`);
  }
  const noteEl = container.querySelector(`#${uid}-note`);
  if (noteEl) noteEl.innerHTML = note.join(' · ');
}

/** Render a promoted artefact in the content pane, at full width. */
export async function promoteToPane(turn) {
  const el = $('content');
  if (!el) return;
  state.promoted = turn;
  const form = answerForm(turn);

  el.innerHTML = `${subTabsHtml()}
    <div class="flex flex-wrap items-baseline gap-s3">
      <h3 class="m-0 font-heading text-name font-normal">${esc(turn.question)}</h3>
      <button data-act="close-promoted"
        class="ml-auto cursor-pointer bg-transparent text-caveat text-accent-ink underline">back to questions</button>
    </div>
    <div class="mt-s1 text-provenance text-ink-muted">${esc(turn.source || '')}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="promoted-body" class="min-h-[320px]"></div>`;
  bindSubTabs();
  el.querySelector('[data-act="close-promoted"]').addEventListener('click', () => {
    state.promoted = null;
    loadPane();
  });

  const body = $('promoted-body');
  try {
    if (form === 'chart') {
      await loadScript('/static/vendor/plotly.min.js');
      const fig = turn.chart;
      // Same themed layout as the Understanding pane — one place, so a chat
      // chart and a stage chart cannot drift into two designs.
      await window.Plotly.newPlot(body, fig.data || [], chartLayout(fig.layout || {}),
                                  { displaylogo: false, responsive: true });
    } else if (form === 'diagram') {
      await renderDiagramInto(body, turn, 'promoted');
    } else {
      body.innerHTML = `<div class="whitespace-pre-wrap text-answer text-ink">${tnum(esc(turn.answer || ''))}</div>`;
    }
  } catch (err) {
    // Per-artefact failure, stated. Never a blank pane.
    body.innerHTML = `<div class="text-answer text-state-warn">
      This could not be rendered: ${esc(err.message)}</div>`;
  }
}

/** Mermaid's own init directive, carrying the token values.
 *  Prepended rather than configured in JS because the render happens on the
 *  server; the directive is the only channel a Kroki round trip has. */
/**
 * Make diagram source this renderer will actually accept.
 *
 * Three transforms, each measured against the live Kroki on 6002 rather than
 * assumed — every one of them was found by bisecting a diagram that returned
 * `400 Internal Server Error` with no other diagnostic:
 *
 *  1. NO `%%` LINES AT ALL. This Kroki rejects any line beginning `%%` —
 *     a plain comment and an `%%{init: …}%%` directive alike, on a two-node
 *     diagram. That is why theming moved out of an init directive and into
 *     `themeSvgElement()` below; it is not a preference.
 *  2. `%` IS ESCAPED to `&percnt;`. A literal percent anywhere in a node
 *     label fails the whole render, and `architecture_diagram` writes
 *     confidence as `40% ⚠` into every label, so this alone made it
 *     unrenderable. `&percnt;` and NOT `&#37;`: both are accepted (200), but
 *     the numeric one comes back rendered as `40&%` — measured by reading
 *     the text nodes of the returned SVG, which is the only way to tell the
 *     two apart, since both "work" by status code.
 *  3. `class` ASSIGNMENTS ARE CAPPED at 20 nodes total. 20 renders, 21 does
 *     not, deterministically, whether on one line or split across several.
 *     Excess assignments are dropped rather than the diagram, and the caller
 *     says how many — losing the dashed "pending" styling on some nodes
 *     beats losing the diagram.
 *
 * THE REAL FIX IS IN THE GENERATOR, which should not emit source its own
 * renderer refuses. Worth noting the fact value carries
 * `exceeds_renderer_limit: false` for a diagram that no renderer here will
 * take — a guard that reports the opposite of what is true.
 */
const KROKI_MAX_CLASSED_NODES = 20;

export function mermaidForKroki(source) {
  const out = [];
  let classed = 0;
  let droppedStyles = 0;
  for (const raw of String(source).split('\n')) {
    if (raw.trimStart().startsWith('%%')) continue;
    const line = raw.replace(/%/g, '&percnt;');
    const m = /^\s*class\s+(\S+)\s+\S+;\s*$/.exec(line);
    if (m) {
      const n = m[1].split(',').length;
      if (classed + n > KROKI_MAX_CLASSED_NODES) { droppedStyles += n; continue; }
      classed += n;
    }
    out.push(line);
  }
  return { source: out.join('\n'), droppedStyles };
}

/**
 * Theme a rendered SVG, scoped to that SVG.
 *
 * Mermaid's own theming is unreachable here (this Kroki rejects every `%%`
 * line, so there is no init directive to carry themeVariables), so the token
 * values are applied to the output instead.
 *
 * EVERY selector is prefixed with the element's own id. An SVG `<style>` is
 * NOT scoped — it is ordinary CSS in the same document — so an earlier
 * version of this containing bare `text, span, p { … !important }` restyled
 * the entire application the moment a diagram opened, chrome included. The
 * id is stamped on here rather than trusting mermaid's own `#container`,
 * which is neither unique nor ours.
 */
const DIAGRAM_ID = 're-diagram-svg';

export function themeSvgElement(svgEl, t) {
  svgEl.id = DIAGRAM_ID;
  const rules = [
    ['', `background:${t.paper}`],
    ['.node rect, .node polygon, .node circle, .node path',
     `fill:${t.paperSurface} !important; stroke:${t.rule} !important`],
    ['.cluster rect', `fill:none !important; stroke:${t.rule} !important`],
    ['.edgePath path, .flowchart-link', `stroke:${t.rule} !important; fill:none !important`],
    ['.arrowheadPath, marker path', `fill:${t.rule} !important; stroke:${t.rule} !important`],
    ['text, .nodeLabel, .edgeLabel, .cluster-label, span, p, div',
     `fill:${t.ink} !important; color:${t.ink} !important; font-family:${t.font} !important`],
    ['.edgeLabel rect, .labelBkg', `fill:${t.paper} !important; background:${t.paper} !important`],
    ['small', `color:${t.inkMuted} !important; fill:${t.inkMuted} !important`],
  ];
  // EVERY comma-separated part gets the prefix, not just the first.
  // `#id a, b, c` scopes only `a` — `b` and `c` stay global, which is how a
  // rule meant for diagram labels restyled the application's brand, nav and
  // sidebar the moment a diagram opened. The bug survived one fix because
  // the string LOOKED prefixed.
  const scope = (selectorList) => selectorList
    .split(',')
    .map((part) => `#${DIAGRAM_ID} ${part.trim()}`)
    .join(', ');
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
  style.textContent = rules
    .map(([sel, decl]) => `${sel ? scope(sel) : `#${DIAGRAM_ID}`}{${decl}}`)
    .join('\n');
  svgEl.prepend(style);
}
/* ════════════════════════════════════════════════════════════════════════
 * The sidebar — selection and grouping
 *
 * Restored wholesale after a first pass reduced it to a filter box and a
 * flat list. The handoff's advice to "show only non-zero disposition counts"
 * was about visual noise; read as a spec it deleted the disposition facets,
 * the scope chips, Select mode and the group tree together. These are not
 * chrome to be trimmed — a work list, the largest thing this UI is meant to
 * grow into, is built out of exactly this multi-select and grouping.
 * ════════════════════════════════════════════════════════════════════════ */

/** Lifecycle scope, matching the current UI's chips and their meanings. */
const SCOPE_CHIPS = [
  ['working-set', 'In scope'],   // needs a current investigation
  ['', 'All'],
  ['new', 'New'],
  ['surveyed', 'Surveyed'],
  ['published', 'Published'],
];

/** Where a repo is in the survey/catalog lifecycle. */
function lifecycleKind(p) {
  if (p.is_published) return 'published';
  if (p.last_surveyed_at) return 'surveyed';
  return 'new';
}

/** The mark shown beside a repo in the list.
 *
 *  Typographic, not emoji: the current UI uses ☁/📊/🆕 and 🔬/👍/✅, and this
 *  palette drops emoji as an icon system. Each mark carries a title so the
 *  glyph is never the only thing saying what it means. */
function lifecycleMark(p) {
  const kind = lifecycleKind(p);
  const name = { published: 'cloud', surveyed: 'bar-chart-2', new: 'sparkles' }[kind];
  const title = kind === 'published' ? 'Published to Egeria'
    : kind === 'surveyed' ? `Surveyed ${ago(p.last_surveyed_at)}`
    : 'Registered, not yet surveyed';
  const cls = kind === 'published' ? 'text-state-ok-on-dark' : 'text-chrome-muted';
  return icon(name, { size: 14, cls, title });
}

/**
 * The glyph for a disposition — ONE map, read by both the row mark and the
 * facet chip.
 *
 * That shared map is the whole key mechanism: the chips at the top of the
 * list already pair a glyph with a word (`◎ investigating 6`), so as long as
 * a row's mark is the SAME glyph its chip uses, the filter row IS the
 * legend. No popover to find, no second thing to keep in sync.
 *
 * `undecided` deliberately has none — a mark on the default state is noise
 * on 45 of 59 rows, and its chip carries the word anyway.
 */
const DISPOSITION_ICON = {
  tracking: 'eye',
  investigating: 'microscope',
  recommended: 'thumbs-up-check',
  using: 'check-circle-2',
  abandoned: 'archive',
  ignored: 'ban',
};

function dispositionMark(p) {
  const name = DISPOSITION_ICON[p.disposition];
  if (!name) return '';
  return icon(name, { size: 14, cls: 'text-accent-on-dark', title: p.disposition });
}

/** The compact key — all three mark families in one place.
 *
 *  A backstop for the first run, not the primary mechanism: the disposition
 *  chips are already a legend, the lifecycle icons are metaphors that read
 *  without one, and every mark carries a `title`. Three families on one
 *  dense row is exactly where hover text earns its place.
 */
function markKeyHtml() {
  const row = (ic, label, note) => `<div class="flex items-baseline gap-[6px]">
    <span class="w-[16px] text-chrome-muted">${icon(ic, { size: 13 })}</span>
    <span class="text-chrome-ink">${esc(label)}</span>
    <span class="text-chrome-muted">${esc(note)}</span></div>`;
  return `<div class="mb-s3 rounded-sm border border-chrome-line p-s2 text-caps">
    <div class="mb-[4px] uppercase tracking-caps text-chrome-muted">Lifecycle</div>
    ${row('sparkles', 'new', 'registered, not surveyed')}
    ${row('bar-chart-2', 'surveyed', 'has survey results')}
    ${row('cloud', 'published', 'in Egeria')}
    <div class="mb-[4px] mt-s2 uppercase tracking-caps text-chrome-muted">Disposition</div>
    ${Object.entries(DISPOSITION_ICON).map(([d, ic]) => row(ic, d, '')).join('')}
    <div class="mt-[3px] text-chrome-muted">undecided has no mark</div>
    <div class="mb-[4px] mt-s2 uppercase tracking-caps text-chrome-muted">Your view</div>
    ${row('eye-off', 'hidden', 'still registered — “Show hidden” brings it back')}
  </div>`;
}

// The find/discover action's title per resource type. Repos and databases
// both now have real ports of classic's mechanisms (GitHub search +
// list-import for repos via discovery-import.js; server-side introspection,
// POST /api/db-servers/{slug}/discover, for databases via
// db-server-discovery.js) — filesystem is the one that still falls through
// to the old-UI-link stub below (see Backlog.md).
const FIND_TITLE = {
  repo: 'Find and import candidate repos',
  db: 'Find databases',
  filesystem: 'Register a filesystem path',
};

/** The Find action's visible words, by the kind shown (REPLY-DESIGNER-
 *  RESOURCE-CONTROLS-PLACEMENT.md §6). Find is rare and its meaning changes
 *  with the chip beside it, so it carries a label. "File system", never "file
 *  share". File systems have no discovery yet, so the button says what it does
 *  today: add one by hand. */
const FIND_LABEL = {
  repo: '＋ Find repos',
  db: '＋ Find databases',
  filesystem: '＋ Add a file system',
};
function findButtonHtml(inRow = false) {
  const title = FIND_TITLE[state.resourceType] || FIND_TITLE.repo;
  return `<button data-act="find-repos" title="${esc(title)}"
    class="${inRow ? 'ml-auto' : ''} cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-accent-on-dark"
    >${esc(FIND_LABEL[state.resourceType] || FIND_LABEL.repo)}</button>`;
}

// Sidebar group collapse — persisted the same way classic's does (a JSON
// array of collapsed group slugs in localStorage), but under its own key so
// the two surfaces (classic's `index.html` and /next) never fight over one
// entry with different shapes (SPEC-PARITY-INVENTORY-AND-GROUPS.md §3).
const COLLAPSED_GROUPS_KEY = 're_next_collapsed_sidebar_groups';
function collapsedGroups() {
  try { return JSON.parse(localStorage.getItem(COLLAPSED_GROUPS_KEY) || '[]'); }
  catch { return []; }
}
// Ported from classic's `_toggleGroupCollapsed` (index.html). Deliberately
// NOT wired off the native <details> 'toggle' event — that event can also
// fire from the browser's own initial-state handling when the `open`
// attribute is set during a render, which would silently overwrite a
// reader's saved preference with whatever the force-expand-on-filter
// render happened to show. Driving it from summary's click instead means
// this only ever runs on a genuine user gesture.
function toggleGroupCollapsed(slug) {
  const current = collapsedGroups();
  const next = current.includes(slug) ? current.filter((s) => s !== slug) : [...current, slug];
  try { localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify(next)); }
  catch { /* per-viewer convenience only */ }
  renderSidebar();
}

// Selecting a group selects what it counted — including members hidden
// inside a currently-collapsed group, because the header's count already
// includes them and a selection that silently skipped them would disagree
// with the number the user just read. Ported from classic's
// `_toggleGroupSelected` (index.html).
function toggleGroupSelected(groupSlug) {
  const members = visibleRows()
    .filter((p) => (p.group_slug || '') === groupSlug)
    .map((p) => p.slug);
  if (!members.length) return;
  const allSelected = members.every((sl) => state.selected.has(sl));
  members.forEach((sl) => (allSelected ? state.selected.delete(sl) : state.selected.add(sl)));
  renderSidebar();
}

/** The repos passing every active filter, in list order. */
function visibleProjects() {
  const f = state.filter.trim().toLowerCase();
  return state.projects.filter((p) => {
    if (!state.showHidden && p.working_set_hidden) return false;
    if (state.dispositionFacet !== 'all'
        && (p.disposition || 'undecided') !== state.dispositionFacet) return false;
    if (state.scope === 'working-set') {
      if (!state.investigation) return false;
      if (!state.workingSet.has(p.slug)) return false;
    } else if (state.scope && lifecycleKind(p) !== state.scope) {
      return false;
    }
    if (f && !(`${p.slug} ${p.display_name}`.toLowerCase().includes(f))) return false;
    return true;
  });
}

/** The database/filesystem rows passing every active filter, in list order —
 *  the `visibleProjects()` above generalized once `DatabaseSummary`/
 *  `FileSystemSummary` grew `working_set_hidden`/`is_published` fields
 *  alongside the `disposition`/`group_slug` they already carried
 *  (`web/routes/databases.py`/`filesystems.py`). There is still no
 *  investigation-scope/lifecycle-kind concept for these rows (`state.scope`'s
 *  chips stay repo-only — see SCOPE_CHIPS), so this mirrors `visibleProjects`
 *  minus that one filter. */
function visibleNonRepoRows() {
  const f = state.filter.trim().toLowerCase();
  return currentResourceRows().filter((r) => {
    if (!state.showHidden && r.working_set_hidden) return false;
    if (state.dispositionFacet !== 'all'
        && (r.disposition || 'undecided') !== state.dispositionFacet) return false;
    if (f && !(`${r.slug} ${r.display_name}`.toLowerCase().includes(f))) return false;
    return true;
  });
}

/** The current resource type's rows passing every active filter — dispatches
 *  to `visibleProjects()` for repos (the lifecycle/scope-aware original) and
 *  `visibleNonRepoRows()` for databases/filesystems. The single entry point
 *  the grouped list, the "N shown" count and `toggleGroupSelected` all read,
 *  so a resource type only ever has one notion of "currently visible". */
function visibleRows() {
  return state.resourceType === 'repo' ? visibleProjects() : visibleNonRepoRows();
}

/** True when the currently selected resource is a database whose stored
 *  credential cannot be read: its survey/Run controls are disabled. */
function selectedCredentialUnreadable() {
  return state.resourceType === 'db' && isCredentialUnreadable(selectedProject());
}

export function renderSidebar() {
  const el = $('sidebar');
  const types = [
    { id: 'repo', label: 'Repos' },
    { id: 'db', label: 'DBs' },
    { id: 'filesystem', label: 'FS' },
  ];

  const chip = (active, extra = '') =>
    `cursor-pointer rounded-pill bg-transparent px-2 py-[2px] ${extra} ${
      active ? 'border border-accent text-accent-on-dark'
             : 'border border-chrome-line text-chrome-muted hover:border-accent'}`;

  // Disposition counts over everything the filters have NOT already removed
  // by disposition, so the numbers describe the list you are choosing from.
  // `currentResourceRows()` generalizes this over whichever resource type is
  // current -- `DatabaseSummary`/`FileSystemSummary` carry `disposition` the
  // same way `ProjectSummary` does (Backlog.md, "Disposition is NOT fixed
  // here", 2026-09-22), so this is no longer repo-only.
  const counts = {};
  for (const p of currentResourceRows()) {
    if (!state.showHidden && p.working_set_hidden) continue;
    const d = p.disposition || 'undecided';
    counts[d] = (counts[d] || 0) + 1;
  }
  // Zeros are COLLAPSED, not removed — every disposition stays reachable
  // through the "more" control, because a facet you cannot select is a
  // filter you cannot undo.
  const present = Object.keys(counts).sort();
  const absent = VALID_DISPOSITIONS.filter((d) => !counts[d]);

  const visible = visibleRows();
  const hiddenCount = currentResourceRows().filter((p) => p.working_set_hidden).length;
  const loaded = state.resourceType === 'repo' ? true
    : state.resourceType === 'db' ? state.databasesLoaded : state.filesystemsLoaded;
  const nonRepoLabel = state.resourceType === 'db' ? 'database' : 'filesystem';
  // The list is narrowed to a facet (a disposition chip, "In scope") or a text
  // filter: the most common reason to act on many is "everything I marked
  // ignored", so the count line offers to select exactly those.
  const facetFiltered = state.dispositionFacet !== 'all'
    || !!state.filter.trim()
    || (state.resourceType === 'repo' && !!state.scope);

  // Grouped by the resource's group. Group display names come from the
  // groups endpoint; a resource with no group lands in Ungrouped. Generic
  // across all three resource types since `group_slug` is on all three
  // summary shapes (`web/routes/projects.py`/`databases.py`/`filesystems.py`).
  const groups = new Map();
  for (const p of visible) {
    const g = p.group_slug || '';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(p);
  }
  const groupName = (slug) =>
    slug ? (state.groups.find((g) => g.slug === slug)?.display_name || slug) : 'Ungrouped';

  // A filter in effect force-expands every group regardless of its saved
  // collapse state — otherwise a match sitting inside a collapsed group
  // would silently disappear from the filtered results, which is worse than
  // just showing it. Collapse state itself is untouched (still exactly what
  // it was once the filter clears). `state.scope` defaults to 'working-set'
  // rather than '', so this mirrors classic's `_projectLifecycleFilter`
  // default the same way: active unless explicitly cleared to 'All'.
  const filterActive = !!(state.filter.trim() || state.scope);
  const collapsedSlugs = collapsedGroups();

  el.innerHTML = `
    <div class="mb-s2 flex items-center gap-[5px] text-chip">
      ${types.map((t) => `<button data-type="${t.id}" class="${chip(state.resourceType === t.id).replace('rounded-pill', 'rounded-sm')}">${t.label}</button>`).join('')}
      ${findButtonHtml(true)}
      <button data-act="mark-key" title="What the marks in this list mean"
        aria-label="What the marks in this list mean"
        class="cursor-pointer bg-transparent text-chrome-muted hover:text-chrome-ink"
        >${icon('circle-help', { size: 14 })}</button>
    </div>
    ${state.showMarkKey ? markKeyHtml() : ''}

    ${investigationBarHtml()}
    ${workListsSidebarHtml()}

    <input id="resource-filter" placeholder="Filter ${
      state.resourceType === 'repo' ? 'repos' : state.resourceType === 'db' ? 'databases' : 'filesystems'}…" value="${esc(state.filter)}"
      class="mb-s2 w-full rounded-sm border border-chrome-line bg-transparent px-[9px] py-[5px]
             text-chip text-chrome-ink placeholder:text-chrome-muted">

    <div class="mb-s2 flex flex-wrap gap-[5px] text-caps">
      ${SCOPE_CHIPS.map(([id, label]) => {
        const disabled = id === 'working-set' && !state.investigation;
        return `<button data-scope="${id}" ${disabled ? 'disabled' : ''}
          title="${disabled ? 'Needs a current investigation' : ''}"
          class="${chip(state.scope === id)}${disabled ? ' border-dashed' : ''}">${label}</button>`;
      }).join('')}
    </div>

    <div class="mb-[3px] text-caps uppercase tracking-caps text-chrome-muted"
      title="The current UI counts these over the investigation's working set instead; /next counts every registered repo, so the two do not match">
      Disposition · ${state.resourceType === 'repo' ? 'all registered repos' : state.resourceType === 'db' ? 'databases' : 'filesystems'}
    </div>
    <div class="mb-s2 flex flex-wrap gap-[5px] text-caps">
      <button data-facet="all" class="${chip(state.dispositionFacet === 'all')}">all</button>
      ${present.map((d) => `<button data-facet="${esc(d)}" class="${chip(state.dispositionFacet === d)}"
        >${DISPOSITION_ICON[d] ? icon(DISPOSITION_ICON[d], { size: 12 }) : ''} ${esc(d)}
        <span class="tnum">${counts[d]}</span></button>`).join('')}
      ${absent.length && state.showEmptyFacets
        ? absent.map((d) => `<button data-facet="${esc(d)}" class="${chip(state.dispositionFacet === d)} border-dashed"
            >${DISPOSITION_ICON[d] ? icon(DISPOSITION_ICON[d], { size: 12 }) : ''} ${esc(d)}
            <span class="tnum">0</span></button>`).join('')
        : absent.length
          ? `<button data-act="show-empty-facets" class="cursor-pointer bg-transparent text-chrome-muted underline"
              ><span class="tnum">${absent.length}</span> more…</button>`
          : ''}
    </div>

    <div class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caps text-chrome-muted">
      <button data-act="select-mode" class="cursor-pointer rounded-sm border bg-transparent px-2 py-[1px] ${
        state.selectMode ? 'border-accent text-accent-on-dark' : 'border-chrome-line text-chrome-ink hover:border-accent'}"
        title="Tick several resources and act on all of them. Shift-click or ⌘/Ctrl-click a row to start."
        >${state.selectMode ? 'Done selecting' : 'Select several…'}</button>
      ${hiddenCount ? `<button data-act="show-hidden" class="cursor-pointer bg-transparent ${
        state.showHidden ? 'text-accent-on-dark' : 'text-chrome-muted hover:text-chrome-ink'}"
        >Show hidden <span class="tnum">${hiddenCount}</span></button>` : ''}
      <span class="ml-auto"><span class="tnum">${visible.length}</span> shown${
        facetFiltered && visible.length
          ? ` · <button data-act="select-these" class="cursor-pointer bg-transparent p-0 text-chrome-ink underline"
              >select these <span class="tnum">${visible.length}</span></button>` : ''}</span>
    </div>

    ${state.selectMode ? selectActionsHtml() : ''}


    ${!loaded ? `
      <div class="text-chip text-chrome-ink">Loading ${nonRepoLabel}s…</div>`
    : visible.length === 0 ? `
      <div class="text-chip text-chrome-ink">${
        currentResourceRows().length
          ? 'Nothing matches these filters.'
          : state.resourceType === 'repo' ? 'Nothing matches these filters.'
          : `No ${nonRepoLabel}s registered. ${findButtonHtml()}`}</div>`
    : [...groups.entries()].sort((a, b) => groupName(a[0]).localeCompare(groupName(b[0]))).map(([g, rows]) => {
        const memberSlugs = rows.map((p) => p.slug);
        const selectedHere = memberSlugs.filter((sl) => state.selected.has(sl)).length;
        const collapsed = !filterActive && collapsedSlugs.includes(g);
        const groupCb = state.selectMode
          ? `<input type="checkbox" data-group-sel="${esc(g)}" class="shrink-0"
               ${selectedHere === memberSlugs.length && memberSlugs.length ? 'checked' : ''}>`
          : '';
        return `<details class="mb-s4" data-group="${esc(g)}" ${collapsed ? '' : 'open'}>
        <summary class="mb-[7px] flex cursor-pointer items-center gap-[6px] font-heading uppercase tracking-caps text-caps text-chrome-muted">
          ${groupCb}
          <span class="min-w-0 truncate">${esc(groupName(g))}</span>
          <span class="tnum">${rows.length}${
            state.selectMode && selectedHere ? `, ${selectedHere} selected` : ''}</span>
        </summary>
        <div class="flex flex-col gap-[1px]">
          ${rows.map((p) => `<div class="flex items-baseline gap-[6px] px-2 py-[5px] ${
            p.slug === state.selectedSlug
              ? 'border-l-2 border-accent bg-chrome-surface'
              : 'border-l-2 border-transparent hover:bg-chrome-surface'}">
            ${state.selectMode ? `<input type="checkbox" data-sel="${esc(p.slug)}" ${
              state.selected.has(p.slug) ? 'checked' : ''} class="shrink-0">` : ''}
            ${lifecycleMark(p)}${dispositionMark(p)}
            <button data-slug="${esc(p.slug)}"
              class="min-w-0 flex-1 cursor-pointer truncate bg-transparent text-left text-chrome-ink"
              >${esc(p.display_name || p.slug)}${credentialMarkHtml(p, 'block text-provenance')}</button>
            ${p.working_set_hidden
              ? icon('eye-off', { size: 13, cls: 'text-chrome-muted', title: 'Hidden from your list — a view preference, not a verdict' })
              : ''}
            ${p.github_url ? `<a href="${esc(p.github_url)}" target="_blank" rel="noopener noreferrer"
              title="Open ${esc(p.display_name || p.slug)} on GitHub"
              class="shrink-0 text-chrome-muted hover:text-accent-on-dark"
              >${icon('external-link', { size: 13 })}</a>` : ''}
          </div>`).join('')}
        </div>
      </details>`;
      }).join('')}
  `;

  bindSidebar();
}

/** Select ONE resource of a given type from outside the sidebar (chat's "Ask about <other resource>").
 *  Switches the type first when it differs, then selects the slug, and returns whether the selection
 *  is now what was asked for -- read back from `state`, so the caller never assumes the switch took. */
export async function selectResourceFromChat(type, slug) {
  if (state.resourceType !== type) await switchResourceType(type);
  const rows = currentResourceRows();
  if (!rows.some((r) => r.slug === slug)) return false;
  state.workListSlug = null;
  state.selectedSlug = slug;
  renderSidebar();
  writeUrl();
  renderTopBar();
  renderRailScope();
  loadPane();
  return state.selectedSlug === slug && state.resourceType === type;
}

/** Switch the sidebar's resource-type chip: fetches that type's list on
 *  first visit (`ensureResourceListLoaded`), then re-selects a resource of
 *  the NEW type -- `state.selectedSlug` otherwise keeps pointing at a
 *  resource of the type just left, which would be a repo slug while
 *  `state.resourceType` says 'db', mismatched in exactly the way
 *  `apiEntityType()` call sites downstream (Survey pane, `getQuestions`,
 *  etc.) assume can't happen. Renders once immediately (so the chip
 *  highlight and any "loading…" row show right away) and again once the
 *  fetch settles. */
async function switchResourceType(type) {
  state.resourceType = type;
  // A selection (and select-mode) is per-resource-type: `state.selected`
  // holds slugs, and a repo slug surviving a switch to 'db' would let a
  // bulk action fire against `state.databases` rows that don't exist, or
  // silently no-op against ones that share a slug by coincidence. Clearing
  // both here is the same rule `state.selectedSlug` already follows a few
  // lines down for the single-selection case.
  state.selected.clear();
  state.selectMode = false;
  renderSidebar();
  writeUrl();
  await ensureResourceListLoaded(type);
  // state.analyses is otherwise only ever fetched once, in start() -- for the
  // type it happened to be at boot. Same bug class as boot's own
  // listAnalyses('repo') hardcode just above: without this, switching from
  // repo to database/filesystem (or back) leaves trendSupport()/openWorkList()
  // reading the WRONG entity type's analysis catalog after the very first
  // switch, silently, since analysis ids can collide across catalogs.
  state.analyses = await listAnalyses(apiEntityType(type)).catch(() => state.analyses);
  const rows = currentResourceRows();
  if (!rows.some((r) => r.slug === state.selectedSlug)) {
    state.selectedSlug = rows[0]?.slug || null;
  }
  renderSidebar();
  writeUrl();
  renderTopBar();
  renderRailScope();
  loadPane();
}

/** Re-fetch groups and the project list and re-render the sidebar.
 *
 * state.groups/state.projects are otherwise only ever populated once, in
 * start() — nothing re-fetches them on its own. Admin → Groups (group
 * create/delete/assign, all real writes to group_slug) needs the sidebar's
 * grouping to reflect what it just changed rather than staying stale until
 * a full page reload, so it imports and calls this after each write. See
 * GROUPS-ADMIN-IMPLEMENTED.md. Admin → Groups assigns
 * groups to databases and filesystems too (admin/groups.js's own
 * listDatabases()/listFilesystems() calls), so this also refreshes
 * whichever of those two this session has already fetched -- not
 * unconditionally, to avoid fetching a list this session has never shown
 * any interest in. */
export async function refreshGroupsAndSidebar() {
  clearCache();
  const [groups, projects, databases, filesystems] = await Promise.allSettled([
    listGroups(),
    listProjects({ includeIgnored: true, includeHidden: true }),
    state.databasesLoaded ? listDatabases() : Promise.resolve(state.databases),
    state.filesystemsLoaded ? listFilesystems() : Promise.resolve(state.filesystems),
  ]);
  if (groups.status === 'fulfilled') state.groups = groups.value || [];
  if (state.databasesLoaded && databases.status === 'fulfilled') state.databases = databases.value || [];
  if (state.filesystemsLoaded && filesystems.status === 'fulfilled') state.filesystems = filesystems.value || [];
  if (projects.status === 'fulfilled') state.projects = projects.value || [];
  renderSidebar();
}

/** What a kind is called on screen, singular and plural (REPLY-DESIGNER-
 *  RESOURCE-CONTROLS-PLACEMENT.md §4/§6). "File system", never "file share". */
const KIND_NOUN = {
  repo: ['repo', 'repos'],
  database: ['database', 'databases'],
  filesystem: ['file system', 'file systems'],
};
function kindNoun(entityType, n = 1) {
  const [one, many] = KIND_NOUN[entityType] || KIND_NOUN.repo;
  return n === 1 ? one : many;
}

/** Select-bar scope wording (REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md §5,
 *  WORK-LISTS-VS-INVESTIGATIONS §4). Current investigation: "＋ add to <name>" /
 *  "− remove from <name>" (name cut at 24 chars, full name in the title). None
 *  current: add reads "add to an investigation…", ENABLED, and opens the
 *  investigation picker (investigation-picker.js); remove stays visible,
 *  disabled, with the reason as visible text. */
const SCOPE_WORDING = {
  add: (name) => `＋ add to ${name}`,
  remove: (name) => `− remove from ${name}`,
  addNone: { label: '＋ add to an investigation…', title: 'Choose an investigation, or start a new one' },
  removeNone: { label: '− remove from investigation', title: 'Choose an investigation first' },
  noneReason: 'no investigation selected',
};

/** The Select-mode action bar, grouped by weight, one group per line
 *  (REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md §5): scope; judgement and
 *  lists; view; then a rule and "remove…". Every action is a bulk write, so
 *  each says plainly what it touches. Removal is the quiet last control; only
 *  the confirmation's commit button carries the accent. */
function selectActionsHtml() {
  const n = state.selected.size;
  const entityType = apiEntityType(state.resourceType);
  const inv = state.investigations.find((i) => i.slug === state.investigation);
  const invName = state.investigation ? (inv?.display_name || state.investigation) : '';
  const shortInv = invName.length > 24 ? `${invName.slice(0, 23)}…` : invName;
  return `<div class="mb-s3 text-caps" data-select-bar>
    <div class="flex flex-wrap items-baseline gap-s2">
      <button data-act="sel-all" class="cursor-pointer bg-transparent text-chrome-ink underline">All shown</button>
      <button data-act="sel-none" class="cursor-pointer bg-transparent text-chrome-ink underline">None</button>
      <span class="text-chrome-muted"><span class="tnum">${n}</span> selected</span>
    </div>
    <div class="mt-[2px] text-chrome-muted" data-select-hint>Tick resources, then act on all of them.</div>
    <div class="mt-s1 flex flex-wrap items-baseline gap-s2" data-bar-group="scope">
      <button data-act="sel-scope-add"
        title="${esc(invName ? `Add to ${invName}’s scope` : SCOPE_WORDING.addNone.title)}"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink">${esc(invName ? SCOPE_WORDING.add(shortInv) : SCOPE_WORDING.addNone.label)}</button>
      <button data-act="sel-scope-remove" ${invName ? '' : 'disabled'}
        title="${esc(invName ? `Remove from ${invName}’s scope — the ${kindNoun(entityType)} itself is untouched` : SCOPE_WORDING.removeNone.title)}"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink ${
          invName ? '' : 'border-dashed'}">${esc(invName ? SCOPE_WORDING.remove(shortInv) : SCOPE_WORDING.removeNone.label)}</button>
      ${invName ? '' : `<span class="text-chrome-muted" data-scope-reason>${SCOPE_WORDING.noneReason}</span>`}
    </div>
    <div class="mt-s1 flex flex-wrap gap-s2" data-bar-group="judgement">
      <select data-act="sel-disposition"
        title="Set the disposition on every selected ${kindNoun(entityType)}"
        class="rounded-sm border border-chrome-line bg-chrome px-2 py-[2px] text-chrome-ink">
        <option value="">mark as…</option>
        ${VALID_DISPOSITIONS.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}
      </select>
      <button data-act="sel-worklist"
        title="Save the selected resources as a work list you can run, compare and narrow"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink">save as work list…</button>
    </div>
    <div class="mt-s1 flex flex-wrap gap-s2" data-bar-group="view">
      <button data-act="sel-hide"
        title="Hide from your own list. A view preference, not a judgement"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink">hide</button>
    </div>
    <div class="mt-s2 flex flex-wrap items-baseline gap-s2 border-t border-chrome-line pt-s2" data-bar-group="remove">
      <button data-act="sel-remove" ${n ? '' : 'disabled'}
        title="${n ? 'Remove from Resource Explorer: unregisters these and drops RE’s own records about them. The sources themselves are not touched.'
          : 'Tick resources first'}"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink ${n ? '' : 'opacity-60'}">${n
          ? `Remove <span class="tnum">${n}</span> ${kindNoun(entityType, n)}…` : 'remove…'}</button>
      <span class="text-chrome-muted">from Resource Explorer</span>
    </div>
    <div id="sidebar-action" class="w-full"></div>
  </div>`;
}

/** The current investigation — the frame every survey runs inside.
 *
 *  There is no server-side "current investigation": the current UI keeps it
 *  in localStorage under `re_current_investigation`, and /next reads and
 *  writes THE SAME KEY so switching between the two shells does not silently
 *  change what you are working on. */
const INVESTIGATION_KEY = 're_current_investigation';

function investigationBarHtml() {
  return `<div class="mb-s2 text-caps">
    <label for="investigation-select" class="mb-[3px] block uppercase tracking-caps text-chrome-muted">Investigation</label>
    <select id="investigation-select"
      class="w-full rounded-sm border border-chrome-line bg-chrome px-[6px] py-[3px] text-chip text-chrome-ink">
      <option value="">— none —</option>
      ${state.investigations.map((inv) => `<option value="${esc(inv.slug)}" ${
        inv.slug === state.investigation ? 'selected' : ''}>${esc(inv.display_name || inv.slug)}</option>`).join('')}
    </select>
  </div>`;
}

/** A saved list that is somebody's inbox, not a bench: the journal's
 *  "suggest to…" writes one `suggested-to-<audience>` list per audience
 *  (`journal.py` SUGGESTION_PREFIX). Same prefix, one place. */
const SUGGESTION_PREFIX = 'suggested-to-';

/** The saved lists, split into the two things they are
 *  (REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md §1). `linked` is the
 *  benches tagged to the current investigation: the same rows
 *  `list_all(investigation=)` would return, filtered here because the page
 *  already holds every row and each carries its `investigation`, so a second
 *  request could only disagree with this one. Counts are `.length` of these
 *  arrays, never a stored number. */
export function splitWorkLists() {
  const benches = state.workLists.filter((w) => !String(w.slug).startsWith(SUGGESTION_PREFIX));
  const suggestions = state.workLists.filter((w) => String(w.slug).startsWith(SUGGESTION_PREFIX));
  const linked = state.investigation ? benches.filter((w) => w.investigation === state.investigation) : [];
  const other = state.investigation ? benches.filter((w) => w.investigation !== state.investigation) : benches;
  return { benches, suggestions, linked, other };
}

function workListRowHtml(w) {
  return `<button data-worklist="${esc(w.slug)}"
    class="cursor-pointer truncate bg-transparent px-2 py-[5px] text-left ${
      w.slug === state.workListSlug
        ? 'border-l-2 border-accent bg-chrome-surface text-chrome-ink'
        : 'border-l-2 border-transparent text-chrome-ink hover:bg-chrome-surface'}"
    >${esc(w.display_name)} <span class="tnum text-chrome-muted">${w.member_count}</span>${
      w.egeria_guid ? ` ${icon('cloud', { size: 12, cls: 'text-state-ok-on-dark', title: 'Published to Egeria' })}` : ''}</button>`;
}

/** Panel A of the work-lists reply: under the investigation selector,
 *  top to bottom, the scope line and the lists for this investigation; the
 *  other lists folded to one line; the suggestions on their own line. With
 *  no investigation current the section reads "Work lists · N" and lists
 *  them all. */
function workListsSidebarHtml() {
  const { benches, suggestions, linked, other } = splitWorkLists();
  const inv = state.investigations.find((i) => i.slug === state.investigation);
  const invName = state.investigation ? (inv?.display_name || state.investigation) : '';
  const rows = (arr) => `<div class="mb-s2 flex flex-col gap-[1px]">${arr.map(workListRowHtml).join('')}</div>`;
  let html = '';
  if (invName) {
    html += `<button data-act="sidebar-scope" type="button"
        title="Show the repos in ${esc(invName)}’s scope (the In scope filter). The count is every kind of resource in the scope."
        class="mb-[7px] block w-full cursor-pointer bg-transparent px-0 text-left font-heading uppercase tracking-caps text-caps text-chrome-muted"
        >Scope · <span class="tnum" data-scope-count>${state.scopeCount === null ? '–' : state.scopeCount}</span></button>
      <div class="mb-[7px] font-heading uppercase tracking-caps text-caps text-chrome-muted" data-linked-lists-head>Work lists for ${esc(invName)} · <span class="tnum">${linked.length}</span></div>
      ${linked.length ? rows(linked) : ''}`;
    if (other.length) {
      html += `<details class="mb-s2" data-other-lists>
        <summary class="mb-[7px] cursor-pointer font-heading uppercase tracking-caps text-caps text-chrome-muted">Other work lists · <span class="tnum">${other.length}</span></summary>
        ${rows(other)}
      </details>`;
    }
  } else if (benches.length) {
    html += `<div class="mb-[7px] font-heading uppercase tracking-caps text-caps text-chrome-muted" data-all-lists-head>Work lists · <span class="tnum">${benches.length}</span></div>
      ${rows(benches)}`;
  }
  if (suggestions.length) {
    html += `<details class="mb-s2" data-suggestions>
        <summary class="mb-[7px] cursor-pointer font-heading uppercase tracking-caps text-caps text-chrome-muted">Suggestions · <span class="tnum">${suggestions.length}</span></summary>
        <div class="mb-s2 px-2 text-provenance text-chrome-muted">Entries other people were pointed at from the journal’s “suggest to…”. They are inboxes, not work lists.</div>
        ${rows(suggestions)}
      </details>`;
  }
  return html ? `<div class="mb-s3">${html}</div>` : '';
}

function bindSidebar() {
  const el = $('sidebar');
  const rerender = () => { renderSidebar(); writeUrl(); };

  el.querySelectorAll('button[data-type]').forEach((b) => b.addEventListener('click', () => switchResourceType(b.dataset.type)));
  el.querySelectorAll('button[data-scope]').forEach((b) => b.addEventListener('click', () => {
    if (b.disabled) return;
    state.scope = b.dataset.scope;
    rerender();
  }));
  el.querySelectorAll('button[data-facet]').forEach((b) => b.addEventListener('click', () => {
    state.dispositionFacet = b.dataset.facet;
    rerender();
  }));
  el.querySelectorAll('input[data-sel]').forEach((cb) => cb.addEventListener('change', () => {
    if (cb.checked) state.selected.add(cb.dataset.sel);
    else state.selected.delete(cb.dataset.sel);
    renderSidebar();
  }));
  // The group-select checkbox lives inside <summary>, whose own default
  // click action is toggling the <details> open/closed — preventDefault()
  // suppresses that (and the checkbox's own native check-toggle, which is
  // fine since toggleGroupSelected's re-render redraws it either way).
  el.querySelectorAll('input[data-group-sel]').forEach((cb) => cb.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleGroupSelected(cb.dataset.groupSel);
  }));
  // The rest of <summary> drives collapse/expand ourselves (preventDefault
  // + our own toggle) rather than the native disclosure + a 'toggle'
  // listener — see toggleGroupCollapsed's comment for why.
  el.querySelectorAll('details[data-group] > summary').forEach((summary) => {
    summary.addEventListener('click', (e) => {
      if (e.target.closest('input[data-group-sel]')) return;
      e.preventDefault();
      toggleGroupCollapsed(summary.closest('details').dataset.group);
    });
  });
  el.querySelector('[data-act="sidebar-scope"]')?.addEventListener('click', () => {
    state.scope = 'working-set';
    rerender();
  });
  el.querySelectorAll('button[data-worklist]').forEach((b) => b.addEventListener('click', () => {
    state.workListSlug = b.dataset.worklist;
    ensureRunStageForList();
    renderSidebar();
    loadPane();
  }));
  el.querySelectorAll('button[data-slug]').forEach((b) => b.addEventListener('click', (e) => {
    // Shift-click or ⌘/Ctrl-click is the convention every list teaches: it
    // turns Select mode on with that row ticked (and, once on, toggles the
    // row) instead of opening it.
    if (e.shiftKey || e.metaKey || e.ctrlKey) {
      e.preventDefault();
      const slug = b.dataset.slug;
      const wasOn = state.selectMode;
      state.selectMode = true;
      if (wasOn && state.selected.has(slug)) state.selected.delete(slug);
      else state.selected.add(slug);
      renderSidebar();
      return;
    }
    // Leaving the matrix is remembered, so the way back is one click rather
    // than a hunt. Losing a 12x27 grid to a stray click on a repo, with no
    // visible route back, is what "I somehow got off the matrix view and
    // don't know how to get back" was.
    if (state.workListSlug) state.lastWorkListSlug = state.workListSlug;
    state.workListSlug = null;
    // Selecting a resource preserves stage and perspectives, deliberately.
    state.selectedSlug = b.dataset.slug;
    rerender();
    renderTopBar();
    renderRailScope();
    loadPane();
  }));
  el.querySelector('#investigation-select')?.addEventListener('change', (e) => {
    setInvestigation(e.target.value);
  });
  el.querySelector('[data-act="sel-disposition"]')?.addEventListener('change', (e) => {
    const value = e.target.value;
    e.target.value = '';
    if (value) bulkDisposition(value);
  });

  const acts = {
    'show-empty-facets': () => { state.showEmptyFacets = true; renderSidebar(); },
    'mark-key': () => { state.showMarkKey = !state.showMarkKey; renderSidebar(); },
    // Corpus-level, not a stage: the same action on Scouting as on Curate,
    // so it lives beside the switcher that already scopes the whole left
    // column, not in the per-stage strip (SPEC-ACTIONABLE-AND-HONEST.md,
    // point 2). Repos: a real port (NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md)
    // -- GitHub search, the from-list bulk loader, and the inventory CSV
    // export. Databases: a real port too -- register a server, then
    // server-side introspection (POST /api/db-servers/{slug}/discover) to
    // find and add its databases (db-server-discovery.js). Filesystem keeps
    // the old-UI-link stub; classic's own filesystem registration flow is a
    // separate, not-yet-ported affordance.
    'find-repos': () => {
      if (state.resourceType === 'repo') { openFindReposDialog(); return; }
      if (state.resourceType === 'db') { openFindDbServersDialog(); return; }
      const title = FIND_TITLE[state.resourceType] || FIND_TITLE.repo;
      const d = openDialog(title, title);
      d.querySelector('#wl-detail-body').innerHTML = `
        <p class="max-w-[60ch] text-answer text-ink">${esc(title)}.</p>
        <p class="max-w-[60ch] text-answer text-ink">
          <a href="${esc(oldUiHref())}" class="text-accent-ink underline"
            >Open in the current UI</a> ${icon('external-link', { size: 13, cls: 'text-accent-ink' })}
        </p>`;
    },
    'show-hidden': () => { state.showHidden = !state.showHidden; rerender(); },
    'select-mode': () => {
      state.selectMode = !state.selectMode;
      if (!state.selectMode) state.selected.clear();
      renderSidebar();
    },
    // "select these N" on a facet-filtered list: enters Select mode with
    // exactly the rows the filters leave visible ticked.
    'select-these': () => {
      state.selectMode = true;
      visibleRows().forEach((p) => state.selected.add(p.slug));
      renderSidebar();
    },
    'sel-all': () => { visibleRows().forEach((p) => state.selected.add(p.slug)); renderSidebar(); },
    'sel-none': () => { state.selected.clear(); renderSidebar(); },
    'sel-scope-add': () => bulkScope(true),
    'sel-scope-remove': () => bulkScope(false),
    'sel-hide': () => bulkHide(),
    'sel-remove': () => confirmBulkRemove(),
    'sel-worklist': () => saveSelectionAsWorkList(),
  };
  for (const [name, fn] of Object.entries(acts)) {
    // All, not first: "find-repos" is also offered inline in an empty list.
    el.querySelectorAll(`[data-act="${name}"]`).forEach((b) => b.addEventListener('click', fn));
  }

  const filter = el.querySelector('#resource-filter');
  filter?.addEventListener('input', (e) => {
    state.filter = e.target.value;
    renderSidebar();
    const again = $('resource-filter');
    again.focus();
    again.setSelectionRange(again.value.length, again.value.length);
  });
}

/* ── Sidebar write paths ─────────────────────────────────────────────── */

function sidebarNote(html) {
  const el = $('sidebar-action');
  if (el) el.innerHTML = `<div class="mt-s2 text-chip text-chrome-ink">${html}</div>`;
}

/** Open the investigation picker and run `then(slug)` with the chosen one.
 *  "Start a new one…" reuses the existing New investigation dialog; the new
 *  investigation is handed straight on, and the person is not moved to its
 *  page (they were in the middle of something else). */
function chooseInvestigationThen(then) {
  openInvestigationPicker({
    investigations: state.investigations,
    onChoose: (slug) => then(slug),
    onNew: () => openNewInvestigationDialog({ onCreated: (inv) => then(inv.slug) }),
  });
}

async function bulkScope(add, invSlug = state.investigation) {
  if (!state.selected.size) { sidebarNote('Tick resources first.'); return; }
  if (!invSlug) {
    // None current: only "add" can be offered, and it asks which one first.
    if (add) chooseInvestigationThen((slug) => bulkScope(true, slug));
    return;
  }
  const slugs = [...state.selected];
  const entityType = apiEntityType(state.resourceType);
  const failed = [];
  for (const slug of slugs) {
    try {
      if (add) await addInvestigationMember(invSlug, entityType, slug);
      else await removeInvestigationMember(invSlug, entityType, slug);
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  // The chosen investigation becomes the current one (it is the one the person
  // just worked in); setInvestigation also reloads its scope and repaints.
  const becameCurrent = invSlug !== state.investigation;
  if (becameCurrent) await setInvestigation(invSlug);
  else await loadWorkingSet();
  // The open investigation page lists these members too; re-fetch it.
  await refreshOpenInvestigation(invSlug);
  // Repaint first, THEN write the note: renderSidebar rebuilds the bar and
  // the note's host with it, so a note written before it is wiped at once.
  renderSidebar();
  const inv = state.investigations.find((i) => i.slug === invSlug);
  const invName = inv?.display_name || invSlug;
  const done = slugs.length - failed.length;
  // Report per-resource, never "done": a bulk write where some calls failed
  // and the banner says success is how a partial write becomes invisible.
  sidebarNote(failed.length
    ? `<span class="text-accent-on-dark"><span class="tnum">${done}</span>
       of <span class="tnum">${slugs.length}</span> ${add ? 'added' : 'removed'};
       ${esc(failed.join('; '))}</span>`
    : add && becameCurrent
      ? `Added <span class="tnum">${slugs.length}</span> to ${esc(invName)}, now your current investigation.`
      : `<span class="tnum">${slugs.length}</span> ${add ? 'added to' : 'removed from'} scope.`);
}

async function bulkHide() {
  const slugs = [...state.selected];
  if (!slugs.length) return;
  const entityType = apiEntityType(state.resourceType);
  const failed = [];
  for (const slug of slugs) {
    try {
      await setWorkingSetHidden(entityType, slug, true);
      const p = currentResourceRows().find((x) => x.slug === slug);
      if (p) p.working_set_hidden = true;
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  sidebarNote(failed.length
    ? `<span class="text-accent-on-dark">${esc(failed.join('; '))}</span>`
    : `<span class="tnum">${slugs.length}</span> hidden. They are still registered — "Show hidden" brings them back.`);
  renderSidebar();
}

async function bulkDisposition(disposition) {
  const slugs = [...state.selected];
  if (!slugs.length) return;
  const isRepo = state.resourceType === 'repo';
  const entityType = apiEntityType(state.resourceType);
  const failed = [];
  const noUrl = [];
  for (const slug of slugs) {
    const p = currentResourceRows().find((x) => x.slug === slug);
    // Repos: the older endpoint is keyed on github_url, since it must also
    // resolve a repo that has not been imported yet. A repo without one
    // cannot be dispositioned that way, and saying so beats a silent no-op.
    // Databases/filesystems: `setEntityDisposition` is keyed on the slug
    // directly — no pre-import ambiguity to resolve, since a database/
    // filesystem's slug IS its stable identity from registration (see
    // `getEntityDisposition`'s own comment in re-api.js).
    if (isRepo && !p?.github_url) { noUrl.push(slug); continue; }
    try {
      if (isRepo) await setDisposition(p.github_url, disposition);
      else await setEntityDisposition(entityType, slug, disposition);
      if (p) p.disposition = disposition;
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  const parts = [];
  const ok = slugs.length - failed.length - noUrl.length;
  if (ok) parts.push(`<span class="tnum">${ok}</span> marked ${esc(disposition)}`);
  if (noUrl.length) parts.push(`<span class="text-accent-on-dark"><span class="tnum">${noUrl.length}</span> have no GitHub URL and could not be marked</span>`);
  if (failed.length) parts.push(`<span class="text-accent-on-dark">${esc(failed.join('; '))}</span>`);
  sidebarNote(parts.join(' · '));
  renderSidebar();
}

/**
 * Removal is the one action with no undo anywhere in the stack: the endpoint
 * takes no confirmation flag, drops the resource's pgvector collections and
 * removes the registry row. So the confirmation has to be here, it has to
 * name what is going, and it must not be a one-click button. One word
 * everywhere: "Remove from Resource Explorer" (REPLY-DESIGNER-RESOURCE-CONTROLS-
 * PLACEMENT.md §4) -- nothing here deletes a database or a file.
 *
 * Repo/database/filesystem removal are three genuinely different registry
 * operations (`removeProject`/`removeDatabase`/`removeFilesystem` in
 * re-api.js, each hitting its own HTTP route) -- `removeEntity()` dispatches
 * by `apiEntityType()`-translated type, the same pattern `POST /{slug}/group`
 * already uses server-side (projects.py). The sentence is the single
 * resource's, worded for N (`removeConfirmationHtml` takes a list); the sidebar
 * is scoped to one kind, so a bulk removal is always one kind.
 */
function confirmBulkRemove() {
  const slugs = [...state.selected];
  if (!slugs.length) return;
  const entityType = apiEntityType(state.resourceType);
  const n = slugs.length;
  sidebarNote(`
    <div class="text-chrome-ink" data-remove-sentence>${removeConfirmationHtml(entityType, slugs)}</div>
    <div class="mt-s1 break-words text-chrome-muted">${esc(slugs.join(', '))}</div>
    <div class="mt-s2 flex gap-s2">
      <button data-act="sel-remove-confirm"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-on-dark"
        >Remove ${n} ${kindNoun(entityType, n)}</button>
      <button data-act="sel-remove-cancel"
        class="cursor-pointer bg-transparent text-chrome-ink underline">Cancel</button>
    </div>`);
  $('sidebar-action').querySelector('[data-act="sel-remove-confirm"]')
    .addEventListener('click', () => bulkRemove(slugs));
  $('sidebar-action').querySelector('[data-act="sel-remove-cancel"]')
    .addEventListener('click', () => { $('sidebar-action').innerHTML = ''; });
}

async function bulkRemove(slugs) {
  const entityType = apiEntityType(state.resourceType);
  const listKey = state.resourceType === 'repo' ? 'projects'
    : state.resourceType === 'db' ? 'databases' : 'filesystems';
  const failed = [];
  for (const slug of slugs) {
    try {
      await removeEntity(entityType, slug);
      state[listKey] = state[listKey].filter((p) => p.slug !== slug);
      state.selected.delete(slug);
      if (state.selectedSlug === slug) state.selectedSlug = null;
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  renderSidebar();
  renderTopBar();
  renderRailScope();
  sidebarNote(failed.length
    ? `<span class="text-accent-on-dark">${esc(failed.join('; '))}</span>`
    : `<span class="tnum">${slugs.length - failed.length}</span> removed.`);
  if (!state.selectedSlug) loadPane();
}

/** Turn the sidebar's current selection into a work list.
 *
 *  This is the hinge of the slice: a selection is ephemeral and a work list
 *  is the thing you can run across, compare, narrow and publish. */
async function saveSelectionAsWorkList() {
  const slugs = [...state.selected];
  if (!slugs.length) return;
  const name = await inPagePrompt({
    title: 'Save as work list',
    message: `Name for a work list of ${slugs.length} resource(s):`,
    label: 'Work list name',
    defaultValue: state.investigation ? `${state.investigation} candidates` : 'Candidates',
    confirmLabel: 'Save',
  });
  if (name === null) return;
  sidebarNote('Saving…');
  try {
    const wl = await saveAsWorkList(name.trim() || 'Candidates', slugs, {
      investigation: state.investigation,
      rationale: 'selected in the sidebar',
      entityType: apiEntityType(state.resourceType),
    });
    state.workLists = await listWorkLists();
    state.workListSlug = wl.slug;
    ensureRunStageForList();
    state.selectMode = false;
    state.selected.clear();
    renderSidebar();
    await loadPane();
  } catch (err) {
    sidebarNote(`<span class="text-accent-on-dark">Not saved: ${esc(err.message)}</span>`);
  }
}

/* ── Investigation ───────────────────────────────────────────────────── */

function currentInvestigation() {
  return LS.get(INVESTIGATION_KEY, '') || '';
}

/** Exported so the Investigation pane (stages/investigation.js) can make a
 *  freshly created or reopened investigation the current one — the same
 *  write path the sidebar's own `<select>` uses, not a second one. */
export async function setInvestigation(slug) {
  state.investigation = slug;
  LS.set(INVESTIGATION_KEY, slug);
  if (!slug) {
    try { localStorage.removeItem(INVESTIGATION_KEY); } catch { /* private mode */ }
    state.workingSet = new Set();
    state.scopeCount = null;
    if (state.scope === 'working-set') state.scope = '';
  } else {
    await loadWorkingSet();
  }
  renderTopBar();
  renderSidebar();
}

/** Re-fetch the investigations list and repaint anything that shows it (the
 *  sidebar's Investigation `<select>`, the top-bar badge). Mirrors
 *  `refreshGroupsAndSidebar()` above — `state.investigations` is otherwise
 *  populated once, in `start()`, so a pane that creates/closes/reopens/
 *  renames an investigation must call this or the rest of the chrome keeps
 *  showing stale data until a full reload. */
export async function refreshInvestigationsAndSidebar() {
  try {
    state.investigations = (await listInvestigations({ includeClosed: true })) || [];
  } catch { /* keep whatever we had; the pane calling this shows its own error */ }
  renderSidebar();
  renderTopBar();
}

async function loadWorkingSet() {
  if (!state.investigation) { state.workingSet = new Set(); state.scopeCount = null; return; }
  try {
    const members = await listInvestigationMembers(state.investigation);
    state.scopeCount = (members || []).length;
    state.workingSet = new Set(
      (members || []).filter((m) => requireKind('loadWorkingSet', m.entity_type, 'member.entity_type') === 'repo')
                     .map((m) => m.entity_slug));
  } catch {
    // Unknown, and kept unknown: an empty working set and an unreadable one
    // are different, and "In scope" showing nothing because a call failed
    // would read as "this investigation has no members".
    state.workingSet = new Set();
    state.scopeCount = null;
    state.workingSetUnknown = true;
  }
}

/* ════════════════════════════════════════════════════════════════════════
 * The right rail — Ask, scoped here
 *
 * Moved to next/chat.js (PLAN-FINISH-REPOS.md item 9). app.js still owns
 * the shared pane-promotion machinery chat.js calls into
 * (`promoteToPane`, `answerForm`, `copyAsEvidence`, `railFrame`/
 * `railClaim`/`ensureRailShowing`, `openMembers`) — chat.js's own header
 * comment says why that machinery stayed here rather than moving with it.
 * ════════════════════════════════════════════════════════════════════════ */

/* ════════════════════════════════════════════════════════════════════════
 * The content pane
 * ════════════════════════════════════════════════════════════════════════ */

/* ────────────────────────────────────────────────────────────────────────
 * Pane widths and the chat drawer
 *
 * Both are per-viewer conveniences, so localStorage is the right home —
 * wrapped, because a private window or a browser set to block site data
 * makes the accessor itself throw, and a page that cannot remember a width
 * must still render.
 * ──────────────────────────────────────────────────────────────────────── */

const LS = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, String(value)); } catch { /* not fatal */ }
  },
};

const SIDEBAR_BOUNDS = [150, 520];
const RAIL_BOUNDS = [220, 620];

function clamp(n, [lo, hi]) { return Math.min(hi, Math.max(lo, n)); }

function applyWidths() {
  const grid = $('app-grid');
  const sidebar = clamp(parseInt(LS.get('re-next.sidebarWidth', '240'), 10) || 240, SIDEBAR_BOUNDS);
  const rail = clamp(parseInt(LS.get('re-next.railWidth', '290'), 10) || 290, RAIL_BOUNDS);
  grid.style.setProperty('--sidebar-w', `${sidebar}px`);
  grid.style.setProperty('--rail-w', `${rail}px`);
}

function initSeams() {
  const grid = $('app-grid');

  const drag = (seamId, cssVar, storageKey, bounds, measure) => {
    const seam = $(seamId);
    let raf = null;
    const onMove = (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = null;
        const px = clamp(Math.round(measure(e, grid.getBoundingClientRect())), bounds);
        grid.style.setProperty(cssVar, `${px}px`);
      });
    };
    const onUp = () => {
      seam.classList.remove('dragging');
      document.body.classList.remove('resizing');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      LS.set(storageKey, parseInt(grid.style.getPropertyValue(cssVar), 10));
    };
    seam.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      seam.classList.add('dragging');
      document.body.classList.add('resizing');
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
    // Keyboard, because a drag handle that only responds to a pointer is an
    // affordance some people simply do not have.
    seam.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 32 : 8;
      const cur = parseInt(getComputedStyle(grid).getPropertyValue(cssVar), 10) || bounds[0];
      const sign = seamId === 'seam-rail' ? -1 : 1;
      let next = null;
      if (e.key === 'ArrowLeft') next = cur - step * sign;
      if (e.key === 'ArrowRight') next = cur + step * sign;
      if (next === null) return;
      e.preventDefault();
      const px = clamp(next, bounds);
      grid.style.setProperty(cssVar, `${px}px`);
      LS.set(storageKey, px);
    });
  };

  drag('seam-sidebar', '--sidebar-w', 're-next.sidebarWidth', SIDEBAR_BOUNDS,
       (e, r) => e.clientX - r.left);
  drag('seam-rail', '--rail-w', 're-next.railWidth', RAIL_BOUNDS,
       (e, r) => r.right - e.clientX);
}

function railIsOpen() {
  return LS.get('re-next.railOpen', 'true') !== 'false';
}

/** Is the rail actually showing? The PREFERENCE (localStorage) and the DOM
 *  legitimately disagree on a narrow shell, where renderIntentNav closes the
 *  drawer without persisting. A writer that guards on the preference then
 *  declines to open a rail that is shut and writes into display:none --
 *  the blank rail the owner photographed (REPLY-BLANK-RAIL, 2026-09-12). */
function railIsShowing() {
  const grid = $('app-grid');
  return !!grid && !grid.classList.contains('rail-closed');
}

/** Open the rail if it is not showing. On a narrow shell this is a tap that
 *  is not written back over the wide-screen preference. */
export function ensureRailShowing() {
  if (!railIsShowing()) setRailOpen(true, { persist: !shellIsNarrow() });
}

/* The evidence slot has three writers -- showEvidence, openMembers and the
 * enrichment rail -- and one slot. The rules that make blank impossible:
 *   - the frame always renders: a heading naming WHAT is showing and FOR
 *     WHAT, then a body. A writer replaces the body, never the frame.
 *   - every terminal state is a sentence: loading, failed, empty, and
 *     "nothing was requested" are four different things.
 *   - a slot with three writers needs a request id: a writer takes a
 *     ticket before its awaits and stands down if a later click took one.
 *     Last CLICK wins, not last response. */
let railTicket = 0;
export function railClaim() { return ++railTicket; }
export function railStale(ticket) { return ticket !== railTicket; }

/* WHAT THE RAIL IS A FUNCTION OF. The evidence slot belongs to one
 * (resource type, resource, stage, sub-tab, work list) at a time. Before this,
 * only Context's own render cleared it, and only on a slug change; a plain
 * stage, sub-tab or resource change left the previous pane's evidence on
 * screen under the new pane -- owner screenshot, 8810: viewing egeria_git,
 * the rail read "for laz_local_adventureworks".
 *
 * ONE place enforces it: `syncRailToSelection()`, called by loadPane() (every
 * stage click, sub-tab click, resource click, URL restore and delete funnels
 * through it). When the key changed it (1) takes a ticket, so any writer that
 * was awaiting a fetch for the OLD key stands down, and (2) empties the slot.
 * A writer that has no ticket (Context's) compares `railKeyOf()` itself before
 * it writes. Same key => nothing happens, so a rail deliberately open on this
 * resource's evidence survives a re-render of the same pane. */
let railKey = null;
export function railKeyOf() {
  return [state.resourceType, state.selectedSlug || '', state.stage, state.subTab, state.workListSlug || ''].join('\u0001');
}
export function syncRailToSelection() {
  const key = railKeyOf();
  if (key === railKey) return false;
  railKey = key;
  railClaim();
  const out = $('rail-evidence');
  if (out) {
    out.innerHTML = '';
    delete out.dataset.railFor;
    delete out.dataset.railKey;
  }
  renderRailScope();
  return true;
}
/** Every rail writer tags what it wrote: the resource it is FOR and the key it
 *  was written under, so "what does the rail claim" is checkable from the DOM. */
export function railTag(out, forWhat) {
  if (!out) return;
  out.dataset.railFor = String(forWhat ?? '');
  out.dataset.railKey = railKey ?? railKeyOf();
}

export function railFrame(kind, forWhat, bodyHtml, { sub = '', actions = '' } = {}) {
  const out = $('rail-evidence');
  if (!out) return null;
  railTag(out, forWhat);
  out.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">${esc(kind)}</span>
      <span class="min-w-0 truncate text-caps text-chrome-muted">for <span class="font-mono">${esc(forWhat)}</span>${sub ? ` · ${sub}` : ''}</span>
      ${actions}
      <button data-act="rail-clear" class="ml-auto cursor-pointer bg-transparent text-caps text-chrome-muted underline">close</button>
    </div>
    <div data-rail-body>${bodyHtml}</div>`;
  out.querySelector('[data-act="rail-clear"]')?.addEventListener('click', () => {
    railClaim();
    out.innerHTML = '';
    delete out.dataset.railFor;
  });
  return out.querySelector('[data-rail-body]');
}

/** Below the drawer breakpoint the rail must not open ITSELF.
 *
 * The stored preference is a wide-screen preference: someone who likes the
 * chat rail open on a laptop has not asked for a drawer covering the whole
 * screen on a phone. So a narrow shell starts closed regardless, and opening
 * it there is a deliberate tap that is not written back over the preference.
 */
function shellIsNarrow() {
  return window.matchMedia('(max-width: 780px)').matches;
}

function setRailOpen(open, { persist = true } = {}) {
  if (persist) LS.set('re-next.railOpen', open ? 'true' : 'false');
  $('app-grid').classList.toggle('rail-closed', !open);
  const btn = $('chat-toggle');
  if (btn) {
    btn.innerHTML = `Chat ${icon(open ? 'panel-right-close' : 'panel-right-open', { size: 14 })}`;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.className = open
      ? 'cursor-pointer bg-transparent px-[10px] py-[9px] text-accent-on-dark'
      : 'cursor-pointer bg-transparent px-[10px] py-[9px] text-chrome-muted hover:text-chrome-ink';
  }
}

/* ────────────────────────────────────────────────────────────────────────
 * URL state
 *
 * Neither UI had this. It is the cheapest thing that makes a parallel UI
 * comparable at all: two testers cannot discuss the same screen without a
 * link to it, and "select amundsen, then Analysis, then hold Security" is
 * not a way to report a bug.
 * ──────────────────────────────────────────────────────────────────────── */

let _restoringUrl = false;

/** Go to Enrichment's Context tab (the `data-goto-context` links: the questions rows, Curate's "What we know"). */
export function openContext() {
  state.stage = 'enrichment';
  state.subTab = 'context';
  writeUrl();
  renderIntentNav();
  return loadPane();
}

/** Go to the Curate stage (where the Publish band and the commit table live). Used by a blocker that says
 *  "publish the repository first →" from another pane. */
export function openCurateStage() {
  return openCurate({ tab: 'curate_describe' });
}

/** Go to Curate, on a tab and optionally a section. While the stage-tabs gate is off for this kind the tab is
 *  ignored and Curate opens as it always has (on the Questions frame); with it on, an unknown tab opens tab 1.
 *  A section is jumped to once the pane is drawn (jumpToCurateSection queues it if the plan is still loading). */
export function openCurate({ tab, section } = {}) {
  // From one of Curate's own tabs to another, the panels are already in the document: switch in place (the plan
  // is not read again, a running commit keeps its line).
  const inPlace = curatePanelsMounted();
  state.stage = 'curate';
  state.subTab = stageTabsOn('curate', state.resourceType)
    ? (STAGE_TAB_IDS.curate.includes(tab) ? tab : STAGE_TAB_IDS.curate[0])
    : 'questions';
  if (inPlace && STAGE_TAB_IDS.curate.includes(state.subTab)) {
    switchCurateTab(state.subTab);
    renderIntentNav();
    return section ? Promise.resolve(jumpToCurateSection(section)) : Promise.resolve();
  }
  writeUrl();
  renderIntentNav();
  const loading = loadPane();
  if (section) return Promise.resolve(loading).then(() => jumpToCurateSection(section));
  return loading;
}

/** Switch to the Investigation stage on the current investigation's detail, for the publish gate's
 *  "Bind this investigation to a project" (G1). The header's own "Open Investigation →" handler keeps its
 *  inline body (a source-scanning test pins it). With no current investigation the stage still opens:
 *  its list is where one is made. */
export function openCurrentInvestigationStage() {
  if (state.investigation) openInvestigationDetail(state.investigation);
  state.stage = 'investigation';
  writeUrl();
  renderIntentNav();
  loadPane();
}

function writeUrl() {
  if (_restoringUrl) return;
  const p = new URLSearchParams();
  if (state.resourceType !== 'repo') p.set('type', state.resourceType);
  if (state.selectedSlug) p.set('resource', state.selectedSlug);
  if (state.stage !== 'scouting') p.set('stage', state.stage);
  // ENRICHMENT-E1-CONTEXT-TAB: Context is Enrichment's own default, not
  // 'questions' — omit `tab=` only when subTab already matches whichever
  // one is the default FOR THIS STAGE, so a bookmarked Enrichment URL
  // doesn't carry a redundant `tab=context` while still correctly carrying
  // `tab=questions` if that's what was deliberately chosen instead.
  // A stage with leading tabs always writes `tab=`, so a shared link names the sender's tab, not "the default".
  const defaultSubTab = stageDefaultSubTab();
  if (state.subTab !== defaultSubTab || stageTabsOn(state.stage, state.resourceType)) p.set('tab', state.subTab);
  if (state.workListSlug) p.set('worklist', state.workListSlug);
  if (state.activePerspectives.size) p.set('perspectives', [...state.activePerspectives].join(','));
  const url = `${location.pathname}${p.toString() ? `?${p}` : ''}`;
  history.replaceState(null, '', url);
}

/** Read the URL into state. Returns true if it named a resource. */
export function readUrl() {
  const p = new URLSearchParams(location.search);
  let rewriteUrl = false;
  _restoringUrl = true;
  try {
    if (p.get('type')) state.resourceType = p.get('type');
    if (p.get('resource')) state.selectedSlug = p.get('resource');
    if (p.get('stage')) state.stage = p.get('stage');
    // A `tab=` is kept only when the strip for THIS stage and kind offers it (visibleSubTabs()); a hand-edited
    // or stale id falls to the stage's landing tab, same as no `tab=`. 'dashboard' is the retired id loadPane()
    // aliases to By analysis. The URL is rewritten below so the bad id does not stick.
    const wantTab = p.get('tab');
    const tabOk = Boolean(wantTab) && (wantTab === 'dashboard' || visibleSubTabs().some((t) => t.id === wantTab));
    if (tabOk) state.subTab = wantTab;
    else if (wantTab) { state.subTab = stageDefaultSubTab(); rewriteUrl = true; }
    // A bookmarked/shared `?stage=enrichment` with no `?tab=` should open on
    // Context, same as clicking into Enrichment from the nav (see that
    // click handler's own comment) — the URL restore path was the one place
    // this default did not reach.
    // A stage with leading tabs and no `tab=` opens on tab 1.
    else if (state.subTab === 'questions' || stageTabsOn(state.stage, state.resourceType)) state.subTab = stageDefaultSubTab();
    if (p.get('worklist')) state.workListSlug = p.get('worklist');
    const persp = p.get('perspectives');
    if (persp) state.activePerspectives = new Set(persp.split(',').filter(Boolean));
  } finally {
    _restoringUrl = false;
  }
  if (rewriteUrl) writeUrl();
  return Boolean(p.get('resource'));
}

/* ────────────────────────────────────────────────────────────────────────
 * The resource header
 * ──────────────────────────────────────────────────────────────────────── */

/** The selected resource's summary row -- from `GET /api/projects/` for a
 *  repo, `GET /api/databases/`/`GET /api/filesystems/` for the others. The
 *  callers below (resourceHeaderHtml) read fields (`display_name`,
 *  `last_surveyed_at`) that all three summary shapes carry; `github_url`
 *  and `is_published` simply come back undefined for db/filesystem rows,
 *  which the rendering already treats as "no external links" / "not
 *  published" rather than erroring. */
function selectedProject() {
  return currentResourceRows().find((p) => p.slug === state.selectedSlug) || null;
}

/**
 * Name, external links, provenance and the disposition pill. Hide and Remove
 * are NOT here any more: they live in the resource's menu on its name in the
 * top bar (REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md §1) -- a verdict
 * belongs beside the content it judges, a view preference and an unregister do
 * not.
 *
 * The external links are here because they were missing, and they are marked
 * as external (`↗`, `rel="noopener"`, a new tab) so they read differently
 * from internal navigation — a link that leaves the app and a link that
 * changes a pane should not look alike.
 *
 * The provenance line is the page's own: when this resource was last
 * surveyed, and whether it has been published to Egeria. "Never surveyed" is
 * a distinct statement from "surveyed and nothing changed", the same
 * distinction the rows below make.
 */
export function resourceHeaderHtml(slug) {
  const p = selectedProject();
  const name = p?.display_name || slug;

  const links = [];
  if (p?.github_url) {
    links.push(`<a href="${esc(p.github_url)}" target="_blank" rel="noopener noreferrer"
      class="text-accent-ink underline">GitHub ↗</a>`);
  }
  // `homepage` is NOT on the summary row — it comes from the scouting
  // overview, and it is already the derived best link (GitHub's declared
  // homepage, then the packaging manifest, then the README), doing duty as
  // project site; the documentation site is its own field (docs_url) and is drawn below.
  const homepage = state.overview?.slug === slug ? state.overview.homepage : '';
  if (homepage) {
    links.push(`<a href="${esc(homepage)}" target="_blank" rel="noopener noreferrer"
      class="text-accent-ink underline">Project site ↗</a>`);
  }
  // Documentation beside it, with the short cue from the server (docs ✓ / docs unreachable /
  // no docs found / docs not checked). Nothing is drawn until the Homepage step has run.
  const docsOv = state.overview?.slug === slug ? state.overview : null;
  if (docsOv?.docs_cue) {
    links.push(docsOv.docs_url
      ? `<a href="${esc(docsOv.docs_url)}" data-docs-cue="${esc(docsOv.docs_state || '')}" target="_blank" rel="noopener noreferrer"
      class="text-accent-ink underline">${esc(docsOv.docs_cue)} ↗</a>`
      : `<span data-docs-cue="${esc(docsOv.docs_state || '')}" class="text-ink-muted">${esc(docsOv.docs_cue)}</span>`);
  }

  // PI-030: the source word comes from the summary row's `last_survey_source`, which the server
  // takes from the same latest valid survey row the Understanding history table lists first. A row
  // with no source says nothing rather than guessing one.
  const sourceWord = p?.last_surveyed_at ? surveySourceWord(p.last_survey_source) : '';
  const surveyed = p?.last_surveyed_at
    ? `surveyed <span class="tnum">${esc(ago(p.last_surveyed_at))}</span>${sourceWord
      ? ` · <span data-survey-source>${esc(sourceWord)}</span>` : ''}`
    : 'never surveyed';
  const ov = state.overview?.slug === slug ? state.overview : null;
  let published = p?.is_published ? 'published to Egeria' : 'not published to Egeria';
  if (ov?.last_published_at && p?.is_published) {
    published += ` <span class="tnum">${esc(ago(ov.last_published_at))}</span>`;
    // The row's existence is not proof: a publish that predates an Egeria reset says so, short word on the
    // element, the sentence on demand.
    if (ov.published_state === 'published_earlier') {
      published += ` · <span title="${esc(PUBLISHED_EARLIER_SENTENCE)}">${esc(PUBLISHED_EARLIER_WORD)}</span>`;
    }
  }
  // A "published" badge is actively misleading while the link is broken: it
  // reports a catalog entry RE can no longer reach. `p?.egeria_publish_note`
  // (on the summary row itself, every resource type) is the primary source —
  // computed from `egeria_linkage.describe_publish_status` at the same time
  // as `is_published`, so it needs no separate fetch and cannot silently be
  // missing the way `state.overview` can (repo-only, best-effort, absent
  // until it loads). `ov?.egeria_link_stale` (repo's own scouting-overview,
  // kept for its extra `egeria_link_stale_guid` detail) is checked second
  // and wins if both are somehow present, since it is the older, more
  // specific signal for repos.
  if (p?.egeria_publish_note) {
    published = `<span class="text-accent-ink">${esc(p.egeria_publish_note)}</span>`;
  }
  if (ov?.egeria_link_stale) {
    published = `<span class="text-accent-ink">published, but the Egeria link is stale —`
      + ` the Egeria entry cannot be reached</span>`;
  }
  // Fourth publish state (PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md /
  // REPLY-PUBLISH-STATE-GO-AHEAD.md §4): the DATE stays — the row is the only
  // evidence a publish happened — and the sentence states what was observed
  // (a failed resolve), never why (a wipe and an individual deletion look
  // identical from here). No "publish again" button here: /next has no
  // publish trigger of its own yet to wire it to, and its Analysis pane has
  // no publish control either, so the sentence below must not point there.
  if (ov?.publish_uncatalogued) {
    published += ` <span class="text-accent-ink">⚠ not published, publish needed</span>`;
  }
  if (ov?.publish_stale) {
    published += ` <span class="text-accent-ink">⚠ these elements are no longer in the store —`
      + ` this view has no publish control, so they are not republished from here</span>`;
  }

  // Persistent credential-visibility banner (design REPLY-DATABASE-
  // CREDENTIAL-CAPABILITY-VISIBILITY.md §4, Piece 1 of ASK-...-#251): a
  // database's `credential_capability` probe result, when one has run, is
  // carried on the summary row (`p.credential_capability` — see
  // `databases.py`'s `DatabaseSummary`) the same way `github_url`/
  // `is_published` are, and simply comes back undefined for a repo/
  // filesystem row, same convention `selectedProject()`'s own comment
  // documents for those two fields. Shown here rather than only inside a
  // Questions-row envelope so it stays visible regardless of which question
  // is open — "connected as X" is a fact about the WHOLE resource, not one
  // answer among many.
  let credentialBanner = '';
  const cap = p?.credential_capability;
  // `relation_total`/`relation_select` (renamed from `table_total`/
  // `table_select`, REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3): the
  // whole-database figure counts base tables + views + matviews, never
  // tables alone. `cap` is a stored probe blob that can predate this
  // rename, so the old key is read as a fallback -- without it every
  // database surveyed before this rename would show "SELECT on 0 of 0
  // table(s)" until re-surveyed.
  const relTotal = cap?.relation_total ?? cap?.table_total;
  const relSelect = cap?.relation_select ?? cap?.table_select;
  if (cap && (relTotal || cap.schema_total)) {
    // Slice A2.1: this line is about ONE survey, RE's own local one, and says so
    // (with its date). The Schema Inventory and Curate trees may read a fuller
    // Egeria survey, so a sentence about "this page" would speak for counts it
    // does not describe.
    const line = credentialLineText(cap, p?.credential_capability_at, relTotal, relSelect);
    credentialBanner = `
      <div data-credential-scope-line class="mt-s1 text-provenance ${line.thin ? 'text-accent-ink' : 'text-ink-muted'}">${esc(line.text)}</div>`;
  }

  // The stale-link banner (PI-052) for ANY resource type. It shows only when the stale-link read Admin → Egeria
  // Links uses (`GET /api/egeria/linkage/stale`) lists this resource; a repository's own scouting-overview flag
  // (which also carries the dead GUID) counts too. Neither read: no banner.
  const staleRow = state.staleLink?.slug === slug ? state.staleLink.row : null;
  const staleOv = ov?.egeria_link_stale ? ov
    : staleRow ? { egeria_link_stale: true, egeria_link_stale_guid: staleRow.stale_guid || '', published_state: ov?.published_state } : null;
  return `
    <div class="flex flex-wrap items-baseline gap-s3">
      <h3 class="m-0 font-heading text-name font-normal">${esc(name)}</h3>
      <span class="font-mono text-provenance text-ink-muted">${esc(slug)}</span>
      ${links.length
        ? `<span class="flex flex-wrap gap-s3 text-caveat">${links.join('')}</span>`
        : `<span class="text-caveat text-ink-muted">no external links recorded</span>`}
      <span class="ml-auto flex flex-wrap items-baseline gap-s2 text-caveat">
        ${state.investigation && state.workingSet.has(slug)
          ? `<button data-act="open-investigation" class="cursor-pointer bg-transparent text-accent-ink underline"
              title="Open the current investigation this resource is in scope for">Open Investigation →</button>`
          : ''}
        <button data-act="disposition" class="cursor-pointer rounded-pill border border-rule-strong bg-transparent px-2 py-[1px] text-ink hover:border-accent">
          ${esc(p?.disposition || 'undecided')} ▾
        </button>
      </span>
    </div>
    <div class="mt-s1 text-provenance text-ink-muted">${surveyed} · ${published}</div>
    ${credentialBanner}
    ${staleLinkBannerHtml(staleOv)}${state.resourceType === 'repo' ? scoutingTilesHtml(ov) + scoutingSignalHtml(state.scoutingSignal?.slug === slug ? state.scoutingSignal : null) : ''}
    ${state.resourceType === 'db' ? changeCredentialsLineHtml(p) : ''}
    ${isCredentialUnreadable(p) ? `<div class="mt-s1 text-provenance" data-credential-banner>${credentialMarkHtml(p)} — surveys and runs are disabled until the credential is re-entered.</div>` : ''}
    <div id="resource-action" class="mt-s2"></div>`;
}

/** Parity G2, PI-021: the header's door to the stored credential. It names when
 *  the credential last changed (the registry's own `credential_changed_at`, or
 *  says it predates that being recorded) and opens the test-before-save form. */
function changeCredentialsLineHtml(p) {
  const changed = p && p.credential_changed_at
    ? `credential changed ${esc(ago(p.credential_changed_at))}`
    : 'credential change date not recorded';
  return `<div class="mt-s1 text-provenance text-ink-muted">${changed} ·
    <button type="button" data-act="change-credentials"
      class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Change credentials…</button></div>`;
}

/** The dated verdict trail for one repo. */
// `target` names the element to fill. Two places show the trail -- the
// header popover and the Disposition pane -- and they carried the same id
// until 2026-09-12; getElementById found the popover's (earlier in the
// DOM) once it had been opened, so the pane's trail never refreshed again
// and the no-URL message landed in the popover instead of the pane.
async function renderDispositionHistory(entityType, identifier, target = 'disposition-history') {
  const el = $(target);
  if (!el) return;
  let rows;
  try {
    rows = entityType === 'repo'
      ? await getDispositionHistory(identifier)
      : await getEntityDispositionHistory(entityType, identifier);
  } catch (err) {
    el.innerHTML = `<span class="text-state-warn">History could not be read: ${esc(err.message)}</span>`;
    return;
  }
  if (!Array.isArray(rows) || !rows.length) {
    // Distinct from "no history was readable" above. Nothing has been set,
    // which is itself the answer.
    el.textContent = 'No disposition has been recorded for this repo.';
    return;
  }
  // Oldest first, the same as the matrix's trail: the point is the
  // sequence. One formatter for both views -- they had drifted into two
  // layouts of one fact.
  const ordered = [...rows].sort((a, b) => whenMs(a.decided_at) - whenMs(b.decided_at));
  el.innerHTML = `<div class="mb-[3px] uppercase tracking-caps text-caps">History${
      ordered.length > 1 ? ` · ${changedTimesHtml(ordered.length - 1)}` : ''}</div>`
    + ordered.map((r) => `<div>${verdictLineHtml(r, esc)}</div>`).join('');
}

/** The verdict picker, as one thing: the popover and the Disposition pane
 *  both show it, and a trail with no way to add to it was the drawing's
 *  complaint about the pane. `onSet` runs after a successful write. */
function dispositionPickerHtml(p) {
  return `<div class="flex flex-wrap items-baseline gap-s2 text-caveat">
    <span class="text-ink-muted">Set disposition</span>
    ${VALID_DISPOSITIONS.map((d) => `<button data-disp="${esc(d)}"
      class="cursor-pointer rounded-pill bg-transparent px-2 py-[1px] ${
        d === (p.disposition || 'undecided')
          ? 'border border-accent text-accent-ink'
          : 'border border-rule-strong text-ink hover:border-accent'}"
      >${esc(d)}</button>`).join('')}
  </div>`;
}

/** Verdicts that end a line of work. People explain why they stopped and
 *  not why they continued, in every system anyone has built, so a first
 *  verdict never asks for a reason. Where the absence costs something is
 *  the REVERSAL -- a `using` repo later abandoned with nothing on the
 *  record about why it was adopted -- so reversing one of these asks
 *  (FUNNEL-COST-RULINGS §4, 2026-09-13). */
const TERMINAL_DISPOSITIONS = new Set(['using', 'abandoned', 'ignored']);

/* ── The depth offer ─────────────────────────────────────────────────────
 *
 * FUNNEL-COST-RULINGS §3 and REPORT-RECORD-AND-TWO-CALLS §B (designer,
 * 2026-09-13). Keep investigating should schedule the deeper surveys --
 * with one condition: it OFFERS, it does not silently queue. At the moment
 * a verdict of investigating or tracking is recorded, the pane shows what
 * that verdict does not do, names the analyses at the analysis and
 * assessment tiers that have never run on this resource, prices them with
 * the split, and offers three buttons. Once per verdict, in the pane,
 * never a modal; the verdict is already recorded when this appears and
 * nothing waits on an answer. The decline is RECORDED on the verdict: a
 * corpus of declines says depth is not worth its price here, which is a
 * finding about the analyses, and it cannot be read off anything if the
 * decline leaves no trace. Never on abandoned or ignored -- offering to
 * spend at the moment someone decided to stop spending is the one place
 * this reads as an argument. recommended and using only when the repo has
 * never been measured: adopting something nobody looked at is the case
 * worth a sentence. */
function depthOfferApplies(disposition, measuredBefore) {
  if (disposition === 'investigating' || disposition === 'tracking') return true;
  if (disposition === 'recommended' || disposition === 'using') return !measuredBefore;
  return false;
}

async function renderDepthOffer(p, host, { afterVerdict = false } = {}) {
  if (!host || !p?.github_url) return;
  const disposition = p.disposition || 'undecided';
  if (disposition === 'abandoned' || disposition === 'ignored' || disposition === 'undecided') { host.innerHTML = ''; return; }
  let offer;
  try { offer = await getDepthOffer(p.slug); } catch { host.innerHTML = ''; return; }
  if (!depthOfferApplies(disposition, !!offer.measured_before)) { host.innerHTML = ''; return; }
  // Once per verdict: the latest verdict row carries the answer, if any.
  if (!afterVerdict) {
    try {
      const rows = await getDispositionHistory(p.github_url);
      const latest = [...(rows || [])].sort((a, b) => whenMs(b.decided_at) - whenMs(a.decided_at))[0];
      if (!latest || latest.depth_offer) { host.innerHTML = ''; return; }
    } catch { host.innerHTML = ''; return; }
  }
  const rows = offer.analyses || [];
  if (!rows.length) { host.innerHTML = ''; return; }
  const total = offer.total || {};
  const priceCell = (c) => {
    if (!c || c.basis === 'unknown') return `<span class="text-ink-muted">not priced</span>`;
    if (c.basis === 'declared') return `<span class="text-ink-muted">declared ${esc(declaredWord(c) || c.sentence || '')}</span>`;
    if (c.split_runs) return `<span class="tnum">${esc(fmtSeconds(c.steps_seconds))}</span> to run · <span class="tnum">${esc(fmtSeconds(c.publish_seconds))}</span> to publish`;
    return `about <span class="tnum">${esc(fmtSeconds(c.seconds))}</span> <span class="text-ink-muted">· not yet split</span>`;
  };
  host.innerHTML = `
    <div data-depth-offer class="mt-s3 border-t border-rule pt-s2">
      <div class="text-caveat text-ink">This verdict does not schedule anything. <span class="tnum">${rows.length}</span>
        ${rows.length === 1 ? 'analysis' : 'analyses'} at the analysis and assessment tiers ${rows.length === 1 ? 'has' : 'have'} never run on
        <span class="font-mono">${esc(p.slug)}</span>:</div>
      <table class="mt-s1 w-full border-collapse text-provenance">
        ${rows.map((a) => `<tr class="border-b border-rule">
          <td class="py-[2px] pr-s2"><input type="checkbox" data-depth-pick="${esc(a.analysis_id)}" hidden></td>
          <td class="py-[2px] pr-s3 font-mono text-ink">${esc(a.analysis_id)}</td>
          <td class="py-[2px] pr-s3 text-ink-muted">${esc(a.tier || '')}</td>
          <td class="py-[2px] text-ink">${priceCell(a.cost)}</td>
        </tr>`).join('')}
      </table>
      <div class="mt-s1 text-provenance text-ink-muted">${tnum(esc(total.sentence || ''))}</div>
      <div class="mt-s2 flex flex-wrap items-baseline gap-s3 text-caveat">
        <button data-depth="accepted" class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-accent-ink">Run these in background</button>
        <button data-depth="choose" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-ink">Choose which</button>
        <button data-depth="declined" class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">Not now</button>
        <span data-depth-status class="text-provenance text-ink-muted"></span>
      </div>
    </div>`;
  const box = host.querySelector('[data-depth-offer]');
  const status = box.querySelector('[data-depth-status]');
  const finish = async (outcome, ids) => {
    const runIds = [];
    for (const id of ids) {
      try { const started = await runAnalysis(p.slug, id, 'repo' /* the depth offer is repo-only: renderDepthOffer gates on github_url */); if (started?.run_id) runIds.push(started.run_id); }
      catch (err) { status.innerHTML = `<span class="text-accent-ink">${esc(id)}: ${esc(err.message)}</span>`; }
    }
    try {
      await postDepthOfferOutcome(p.github_url, { outcome, analysisIds: ids, runIds });
    } catch (err) {
      // Nothing was recorded. Say so; the offer stays so it can be answered.
      status.innerHTML = `<span class="text-accent-ink">${
        err.status === 401 ? 'not recorded — sign in to answer the offer' : `not recorded: ${esc(err.message)}`}</span>`;
      return;
    }
    box.innerHTML = `<div class="text-provenance text-ink-muted">depth offered, ${
      outcome === 'declined' ? 'declined' : `<span class="tnum">${ids.length}</span> queued in the background`} · on the verdict's record</div>`;
    // DepthOffer stays repo-only (it reasons about never-run analyses at
    // the analysis/assessment tiers, tied to the repo analysis catalog) —
    // `renderDepthOffer` above already gates on `p?.github_url`, so this
    // callback only ever runs for a repo.
    renderDispositionHistory('repo', p.github_url);
  };
  box.querySelector('[data-depth="accepted"]').addEventListener('click', () => finish('accepted', rows.map((a) => a.analysis_id)));
  box.querySelector('[data-depth="declined"]').addEventListener('click', () => finish('declined', []));
  box.querySelector('[data-depth="choose"]').addEventListener('click', (ev) => {
    box.querySelectorAll('[data-depth-pick]').forEach((c) => { c.hidden = false; c.checked = true; });
    ev.currentTarget.textContent = 'Run chosen in background';
    ev.currentTarget.onclick = () => {
      const ids = [...box.querySelectorAll('[data-depth-pick]:checked')].map((c) => c.dataset.depthPick);
      if (!ids.length) { status.textContent = 'nothing chosen — Not now records the decline'; return; }
      finish('chose', ids);
    };
  });
}

// `entityType`/`entitySlug` default to the repo shape every existing caller
// (the header popover, and the pane before it took database/filesystem)
// already uses. A database/filesystem caller passes both explicitly — its
// slug IS its stable identity, unlike a repo's github_url-keyed write path.
function wireDispositionPicker(host, p, { note, onSet }, entityType, entitySlug = '') {
  requireKind('wireDispositionPicker', entityType);
  const commit = async (value, reason = '') => {
    note('Saving…');
    try {
      if (entityType === 'repo') {
        await setDisposition(p.github_url, value, reason);
      } else {
        await setEntityDisposition(entityType, entitySlug, value, reason);
      }
      p.disposition = value;
      renderSidebar();
      await onSet(value);
      // The offer, at the moment the verdict is recorded, in the pane —
      // repo-only (renderDepthOffer gates on p?.github_url, a no-op
      // otherwise, since DepthOffer is deliberately not generalized here).
      const slot = $('depth-offer') || $('resource-action');
      if (slot) renderDepthOffer(p, slot, { afterVerdict: true });
    } catch (err) {
      note(`<span class="text-accent-ink">Not saved: ${esc(err.message)}</span>`);
    }
  };
  host.querySelectorAll('[data-disp]').forEach((b) => b.addEventListener('click', async () => {
    const value = b.dataset.disp;
    const current = p.disposition || 'undecided';
    if (value === current) return;
    if (!TERMINAL_DISPOSITIONS.has(current)) { await commit(value); return; }
    // A reversal: the record should say why the earlier verdict no longer
    // holds. The prompt exists before there is data for it, on purpose.
    host.querySelector('[data-reversal]')?.remove();
    host.insertAdjacentHTML('beforeend', `
      <div data-reversal class="mt-s2 flex flex-wrap items-baseline gap-s2 text-caveat">
        <span class="text-ink">Reversing <em>${esc(current)}</em> → <em>${esc(value)}</em> — why?</span>
        <input data-reversal-reason type="text" placeholder="what changed since it was ${esc(current)}"
          class="w-[28ch] rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink placeholder:text-ink-muted">
        <button data-reversal-go class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">record</button>
        <button data-reversal-cancel class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">keep ${esc(current)}</button>
      </div>`);
    const box = host.querySelector('[data-reversal]');
    const input = box.querySelector('[data-reversal-reason]');
    input.focus();
    const go = async () => {
      const reason = input.value.trim();
      if (!reason) { input.placeholder = 'a reversal needs a reason — one line'; input.focus(); return; }
      box.remove();
      await commit(value, reason);
    };
    box.querySelector('[data-reversal-go]').addEventListener('click', go);
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') go(); });
    box.querySelector('[data-reversal-cancel]').addEventListener('click', () => box.remove());
  }));
}

/** The header's write path: the disposition pill. (`hide` and `remove` moved
 *  to the resource menu, `toggleResourceMenu` below.) */
export function bindResourceHeader() {
  const el = $('resource-header') || $('content');
  const slot = $('resource-action');
  const p = selectedProject();
  if (!el || !slot) return;

  const note = (html) => { slot.innerHTML = `<div class="text-caveat text-ink">${html}</div>`; };

  // Classic's own "Open Investigation →" (index.html ~5779) just switches
  // the main view to Investigations — no deep link to a resource's
  // position within it either, since a resource can be in several at once
  // and there is no single "the" investigation to land on beyond whichever
  // one is current. Same shape here: switch stage, open the current
  // investigation's detail.
  el.querySelector('[data-act="open-investigation"]')?.addEventListener('click', () => {
    if (!state.investigation) return;
    openInvestigationDetail(state.investigation);
    state.stage = 'investigation';
    writeUrl();
    renderIntentNav();
    loadPane();
  });

  // PI-052: the stale-link banner's three repairs. Each ends by re-reading the overview so the header
  // shows what the server now says, not what the click implied.
  bindStaleLinkBanner(el, {
    resolve: (action) => resolveEgeriaLink(apiEntityType(state.resourceType), state.selectedSlug, action),
    confirmWrite: (choice) => inPageConfirm({ title: `${choice.label} ${state.selectedSlug}?`, message: choice.sentence, confirmLabel: choice.label }),
    onDone: async (_result, error) => {
      const slug = state.selectedSlug;
      if (state.resourceType === 'repo') {
        try { state.overview = await getScoutingOverview(slug); } catch { state.overview = null; }
      }
      // Re-read the stale list too: a resolved link is gone from it, so the banner leaves.
      try {
        const rows = await listStaleLinkages();
        const et = apiEntityType(state.resourceType);
        state.staleLink = { slug, row: (rows || []).find((r) => r.entity_slug === slug && r.entity_type === et) || null };
      } catch { state.staleLink = null; }
      const host = $('resource-header');
      if (host && state.selectedSlug === slug) { host.innerHTML = resourceHeaderHtml(slug); bindResourceHeader(); }
      if (error && state.selectedSlug === slug && $('resource-action')) {
        // The repair clears the dead link before it publishes, so a failure leaves the link gone: say what is true now.
        $('resource-action').innerHTML = `<div data-repair-failed class="text-caveat text-ink">${esc(error.message || 'The repair did not finish')}.
          The old link is already forgotten, so Publish to Egeria (Curate) can create a fresh one.</div>`;
      }
      if (error) inPageNotice({ title: 'The repair did not finish', message: `${error.message || 'The server could not be reached'}. The old link is already forgotten, so Publish to Egeria (Curate) can create a fresh one.` });
    },
  });

  el.querySelector('[data-act="change-credentials"]')?.addEventListener('click', () => {
    if (!p) return;
    slot.innerHTML = credentialChangeHtml(p);
    bindCredentialChange(slot, p.slug, {
      onSaved: (summary) => {
        // The list row follows what the server saved (never a password).
        p.db_user = summary.db_user || p.db_user;
        p.credential_changed_at = summary.credential_changed_at || p.credential_changed_at;
        p.credential_status = 'ok';
      },
      onCancel: () => { slot.innerHTML = ''; },
    });
  });

  el.querySelector('[data-act="disposition"]')?.addEventListener('click', () => {
    // Generalized 2026-09-23 alongside the Disposition pane (#223): a repo's
    // disposition is keyed on its github_url (stable across import/renames —
    // see registry.py's resolve_repo_entity_slug); a database/filesystem's is
    // keyed on its slug directly, since that IS its stable identity from
    // registration. This handler used to check `p?.github_url` and hard-code
    // 'repo' regardless of the selected resource type — correct for repos,
    // but it also fired the header's disposition button on db/fs resources,
    // which always have a slug and never a github_url, so it always refused.
    const entityType = apiEntityType(state.resourceType);
    const isRepo = entityType === 'repo';
    const identifier = isRepo ? p?.github_url : state.selectedSlug;
    if (!identifier) {
      note(isRepo
        ? `<span class="text-accent-ink">This repo has no GitHub URL recorded, and the
          disposition endpoint is keyed on that URL — so its disposition cannot be
          set from here.</span>`
        : `<span class="text-accent-ink">No resource is selected — its disposition
          cannot be set from here.</span>`);
      return;
    }
    slot.innerHTML = `
      ${dispositionPickerHtml(p)}
      <div id="disposition-history-popover" class="mt-s2 text-provenance text-ink-muted">Loading history…</div>`;
    // The HISTORY, alongside the picker. It exists in the current UI and
    // nowhere in /next, and it is the only place the SEQUENCE of verdicts is
    // visible — which is the rationale trail, not decoration. A single
    // current value cannot say that something was abandoned and then picked
    // back up.
    renderDispositionHistory(entityType, identifier, 'disposition-history-popover');
    wireDispositionPicker(slot, p, { note, onSet: async (value) => {
      el.innerHTML = '';        // rebuilt below by loadPane
      await loadPane();
      $('resource-action').innerHTML =
        `<div class="text-caveat text-ink">Disposition is now <strong>${esc(value)}</strong>.</div>`;
    } }, entityType, state.selectedSlug);
  });
}

/* ────────────────────────────────────────────────────────────────────────
 * The resource's menu (hide, remove) and the panel under the top bar
 * ──────────────────────────────────────────────────────────────────────── */

function closeResourceMenu() {
  const menu = $('resource-menu');
  if (menu) menu.remove();
  const btn = $('scope-slug');
  if (btn) btn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('click', onDocClickCloseMenu, true);
  document.removeEventListener('keydown', onKeyCloseMenu, true);
}
function onDocClickCloseMenu(e) {
  const menu = $('resource-menu');
  if (menu && !menu.contains(e.target) && !$('scope-slug')?.contains(e.target)) closeResourceMenu();
}
function onKeyCloseMenu(e) {
  if (e.key === 'Escape') { closeResourceMenu(); $('scope-slug')?.focus(); }
}

/** The menu on the top bar's resource name. It sits there because the bar is
 *  the one place that names the current resource on every stage and sub-tab
 *  (the content header is redrawn per pane). Hide/Unhide first, a rule, then
 *  Remove last, in ink: the accent is reserved for the control that commits. */
function toggleResourceMenu() {
  if ($('resource-menu')) { closeResourceMenu(); return; }
  const btn = $('scope-slug');
  const slug = state.selectedSlug;
  if (!btn || !slug) return;
  const hidden = !!selectedProject()?.working_set_hidden;
  const menu = document.createElement('div');
  menu.id = 'resource-menu';
  menu.dataset.slug = slug;
  menu.setAttribute('role', 'menu');
  menu.className = 'absolute z-20 w-[290px] rounded-sm border border-rule-strong bg-paper text-caveat text-ink shadow-lg';
  menu.style.left = `${btn.offsetLeft || 0}px`;
  menu.style.top = `${(btn.offsetTop || 0) + (btn.offsetHeight || 0) + 4}px`;
  menu.innerHTML = `
    <button type="button" role="menuitem" data-act="menu-hide" class="block w-full cursor-pointer bg-transparent px-3 py-2 text-left text-ink hover:bg-chrome-surface hover:text-chrome-ink">
      ${hidden ? 'Unhide' : 'Hide from my list'}
      <span class="mt-[2px] block text-provenance text-ink-muted">${hidden
        ? 'Put it back in your list. It was only ever hidden from your view.'
        : 'A view preference: it changes only your list. The resource stays registered.'}</span>
    </button>
    <div class="border-t border-rule" role="separator"></div>
    <button type="button" role="menuitem" data-act="menu-remove" class="block w-full cursor-pointer bg-transparent px-3 py-2 text-left text-ink hover:bg-chrome-surface hover:text-chrome-ink">Remove from Resource Explorer…</button>`;
  (btn.closest('header') || btn.parentElement || document.body).appendChild(menu);
  btn.setAttribute('aria-expanded', 'true');
  menu.querySelector('[data-act="menu-hide"]').addEventListener('click', () => { closeResourceMenu(); toggleHiddenFromMenu(); });
  menu.querySelector('[data-act="menu-remove"]').addEventListener('click', () => { closeResourceMenu(); openRemovePanel(); });
  document.addEventListener('click', onDocClickCloseMenu, true);
  document.addEventListener('keydown', onKeyCloseMenu, true);
  menu.querySelector('[data-act="menu-hide"]').focus();
}

/** The panel directly under the top bar. Both the removal confirmation and the
 *  one-line outcome of a hide land here, never in the content pane's
 *  `#resource-action` slot, which can be scrolled off-screen. Stamped with the
 *  resource it is about so `renderTopBar` can drop it when the selection moves. */
function showControlsPanel(html, slug = state.selectedSlug) {
  let panel = $('resource-controls-panel');
  if (!panel) {
    panel = document.createElement('section');
    panel.id = 'resource-controls-panel';
    panel.className = 'border-b border-rule-strong bg-paper px-4 py-s3 text-caveat text-ink';
    const header = $('scope-slug')?.closest('header');
    if (header) header.after(panel);
    else ($('scope-slug')?.parentElement || document.body).appendChild(panel);
  }
  panel.dataset.slug = slug || '';
  panel.innerHTML = html;
  return panel;
}
function closeControlsPanel() { $('resource-controls-panel')?.remove(); }

async function toggleHiddenFromMenu() {
  const slug = state.selectedSlug;
  const p = selectedProject();
  const hiding = !p?.working_set_hidden;
  const panel = showControlsPanel(hiding ? 'Hiding…' : 'Unhiding…', slug);
  try {
    // `resource_working_set` is keyed on (entity_type, entity_slug): the
    // entity type must follow the selected kind, or a database sharing a slug
    // with a repo hides the wrong row.
    await setWorkingSetHidden(apiEntityType(state.resourceType), slug, hiding);
    if (p) p.working_set_hidden = hiding;
    renderSidebar();
    await loadPane();
    showControlsPanel(`${hiding
      ? 'Hidden from your list. Still registered, and nothing was lost — “Show hidden” in the sidebar brings it back.'
      : 'Back in your list.'} <button type="button" data-act="panel-close" class="cursor-pointer bg-transparent text-ink underline">Close</button>`, slug)
      .querySelector('[data-act="panel-close"]').addEventListener('click', closeControlsPanel);
  } catch (err) {
    panel.innerHTML = `<span class="text-state-warn">Not saved: ${esc(err.message)}</span>`;
  }
}

/** Remove from Resource Explorer: the per-kind confirmation as a panel under
 *  the top bar. Its commit button is the only accent in it and names what goes. */
function openRemovePanel() {
  const entityType = apiEntityType(state.resourceType);
  const slug = state.selectedSlug;
  if (!slug) return;
  const panel = showControlsPanel(`
    <div class="text-ink" data-remove-sentence>${removeConfirmationHtml(entityType, slug)}</div>
    <div class="mt-s3 flex items-baseline gap-s3">
      <button type="button" data-act="remove-confirm"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink"
        >Remove the ${kindNoun(entityType)} ${esc(slug)}</button>
      <button type="button" data-act="remove-cancel" class="cursor-pointer bg-transparent text-ink underline">Cancel</button>
    </div>`, slug);
  panel.querySelector('[data-act="remove-cancel"]').addEventListener('click', closeControlsPanel);
  panel.querySelector('[data-act="remove-confirm"]').addEventListener('click', () => commitResourceRemoval(entityType, slug));
}

async function commitResourceRemoval(entityType, slug) {
  // Dispatch by kind (DB-REMOVE-BUTTON-IMPLEMENTED.md). This used to call
  // `removeProject` for every kind, which hits the repo-only
  // `DELETE /api/projects/{slug}` (404 for a database or filesystem).
  // `removeEntity` picks the route that exists for each kind.
  const listKey = state.resourceType === 'db' ? 'databases'
    : state.resourceType === 'filesystem' ? 'filesystems' : 'projects';
  const panel = showControlsPanel('Removing…', slug);
  try {
    await removeEntity(entityType, slug);
    state[listKey] = state[listKey].filter((x) => x.slug !== slug);
    state.selected.delete(slug);
    state.selectedSlug = state[listKey][0]?.slug || null;
    renderSidebar();
    renderTopBar();
    renderRailScope();
    await loadPane();
    showControlsPanel(`Removed the ${kindNoun(entityType)} ${esc(slug)} from Resource Explorer. <button type="button" data-act="panel-close" class="cursor-pointer bg-transparent text-ink underline">Close</button>`,
      state.selectedSlug)
      .querySelector('[data-act="panel-close"]').addEventListener('click', closeControlsPanel);
  } catch (err) {
    panel.innerHTML = `<span class="text-state-warn">Not removed: ${esc(err.message)}</span>`;
  }
}

/** What the remove confirmation says, per kind, for one resource or for a list
 *  (the Select bar's bulk removal uses the same sentence; the sidebar is scoped
 *  to one kind, so a list is always one kind). Worded from what each DELETE
 *  route does: repo drops its pgvector collections and registry row
 *  (projects.py `remove_project`); database and filesystem delete the registry
 *  row and survey records only (`registry.remove_database` / `remove_filesystem`)
 *  and make no Egeria call. Plain ink: the accent is for the control that
 *  commits, never for a sentence. "This cannot be undone" is said in words. */
export function removeConfirmationHtml(entityType, target) {
  const slugs = Array.isArray(target) ? target : [target];
  const many = slugs.length > 1;
  const name = many
    ? `these <span class="tnum">${slugs.length}</span> ${kindNoun(entityType, slugs.length)}`
    : `<span class="font-mono">${esc(slugs[0])}</span>`;
  const notIgnored = many
    ? `It is not the same as marking them <em>ignored</em> — an ignored
    resource stays registered and can come back.`
    : `It is not the same as marking it <em>ignored</em> — an ignored
    resource stays registered and can come back.`;
  if (entityType === 'database') {
    return many
      ? `Unregister ${name} and delete their local survey records
      (surveys, schema, table and column detail, coverage)? This cannot be undone.
      Not touched: the source databases themselves, anything already published to Egeria,
      and the server registrations they were discovered from. ${notIgnored}`
      : `Unregister the database ${name} and delete its local survey records
      (surveys, schema, table and column detail, coverage)? This cannot be undone.
      Not touched: the source database itself, anything already published to Egeria,
      and the server registration it was discovered from. ${notIgnored}`;
  }
  if (entityType === 'filesystem') {
    return many
      ? `Unregister ${name} and delete their local survey records?
      This cannot be undone. Not touched: the files on disk and anything already
      published to Egeria. ${notIgnored}`
      : `Unregister the file system ${name} and delete its local survey records?
      This cannot be undone. Not touched: the files on disk and anything already
      published to Egeria. ${notIgnored}`;
  }
  return many
    ? `Unregister ${name} and delete all their local survey data? This cannot be
    undone. Not touched: the GitHub repositories and anything already published to
    Egeria. ${notIgnored}`
    : `Unregister ${name} and delete all its local survey data? This cannot be
    undone. Not touched: the GitHub repository and anything already published to
    Egeria. ${notIgnored}`;
}

/**
 * The sub-tab rail.
 *
 * A parallel UI may DEFER an affordance; it may not silently omit one. The
 * four unbuilt sub-tabs looked identical to the one that works, so the pane
 * was claiming a capability it does not have. They are marked with the same
 * dashed rule the unbuilt intent uses, and clicking one says so and links
 * out rather than doing nothing.
 */
/** The sub-tabs the strip shows for the current resource type and stage.
 *  The ONE list: subTabsHtml() renders it and loadPane()'s
 *  reconcileSubTabForStage() validates against it, so the tab bar and the
 *  router cannot disagree about which sub-tab exists where. */
export function visibleSubTabs() {
  return SUB_TABS.filter((t) => !t.resourceTypes || t.resourceTypes.includes(state.resourceType))
    .filter((t) => !t.stages || t.stages.includes(state.stage))
    // A leading tab is also gated on the resource kind (stage-tabs.js STAGE_TABS_KINDS; empty = dormant).
    .filter((t) => !t.stageTab || stageTabsOn(state.stage, state.resourceType));
}

/** The ONE place a stage's default sub-tab is decided (it replaces four "enrichment ? context : questions"
 *  ternaries): Enrichment opens on Context; a stage whose leading tabs are on for this kind opens on its first
 *  (no landing preference); every other stage on Questions. */
export function stageDefaultSubTab(stage = state.stage, kind = state.resourceType) {
  if (stage === 'enrichment') return 'context';
  return stageTabsOn(stage, kind) ? STAGE_TAB_IDS[stage][0] : 'questions';
}

/** Is `id` one of the leading tabs AND on the strip right now? (A stale `curate_*` id off Curate, or with the
 *  gate off, is not: reconcileSubTabForStage() reverts it.) */
export function isStageTabActive(id) {
  return isStageTabId(id) && visibleSubTabs().some((t) => t.id === id);
}

/** The inputs the leading tabs' counts read. The Curate slices (S1/S2) fill `state.curate.counts` with
 *  { view, plan } as they load; until then every count says "reading", never 0. */
function stageTabCountCtx() {
  const c = (state.curate && state.curate.counts) || {};
  return { kind: state.resourceType, view: c.view || null, plan: c.plan || null, planFailed: !!c.planFailed, viewFailed: !!c.viewFailed };
}

/** A known sub-tab that is absent from the current strip (Context outside
 *  Enrichment, Schema Inventory on a non-database) reverts to the stage's
 *  default, the same default writeUrl() omits from the URL. Sub-tabs present
 *  on the new stage survive a stage switch untouched; retired/unknown ids
 *  are left for loadPane()'s own alias and not-built handling. Returns true
 *  when it changed state.subTab. */
export function reconcileSubTabForStage() {
  const known = SUB_TABS.some((t) => t.id === state.subTab);
  if (!known || visibleSubTabs().some((t) => t.id === state.subTab)) return false;
  state.subTab = stageDefaultSubTab();
  return true;
}

export function subTabsHtml() {
  // The leading tabs' extras (rail marker, running-commit line, label counts) exist only while the gate is on
  // for this stage and kind, so the rail is byte-identical to before otherwise.
  const tabbed = stageTabsOn(state.stage, state.resourceType);
  const labelOf = (t) => (t.stageTab ? `${t.label}${tabCountsHtml(t.id, stageTabCountCtx())}` : t.label);
  const commitLine = tabbed && state.stage === 'curate' ? commitLineHtml(state.curate && state.curate.commitLine) : '';
  return `<div class="mb-s4 flex flex-wrap items-baseline gap-s3 font-heading text-subtab"${tabbed ? ' data-subtab-rail' : ''}>
    ${visibleSubTabs().map((t) => {
      if (t.id === state.subTab) {
        return `<span class="border-b border-accent pb-[2px] text-ink">${labelOf(t)}</span>`;
      }
      return `<button data-subtab="${t.id}" class="cursor-pointer bg-transparent text-ink hover:text-accent-ink">${labelOf(t)}</button>`;
    }).join('')}
  </div>${commitLine}`;
}

/* The header no longer says "N of these are not built in /next". That
 * sentence was review-speak — correct in a handoff, unreadable in the
 * product to anyone who was not in the conversation — and the dashed
 * underline on a deferred tab already carries the fact (design rule 6). */
/** Is Curate's tabbed pane on screen (its three panels in the document), so a switch between its tabs can be
 *  done in place without reading the plan again? */
function curatePanelsMounted() {
  return state.stage === 'curate' && stageTabsOn('curate', state.resourceType)
    && isStageTabId(state.subTab) && !!$('content')?.querySelector('[data-curate-tab-panel]');
}

/** Is Understanding's tabbed pane on screen (its three panels in the document)? */
function understandingPanelsMounted() {
  return state.stage === 'understanding' && stageTabsOn('understanding', state.resourceType)
    && isStageTabId(state.subTab) && !!$('content')?.querySelector('[data-understanding-tab-panel]');
}

/** Show one of Understanding's tabs: swap which panel is hidden, redraw the rail, and let the pane start the
 *  reads that tab owns (once). Nothing already read is read again. Exported so a link inside the pane (the
 *  section index) can reveal the tab that holds its target. */
export function switchUnderstandingTab(tab) {
  state.subTab = tab;
  writeUrl();
  const want = tab.replace(/^understanding_/, '');
  $('content').querySelectorAll('[data-understanding-tab-panel]').forEach((p) => { p.hidden = p.dataset.understandingTabPanel !== want; });
  refreshStageRail();
  if (state.understandingTabs && state.understandingTabs.onTabShown) state.understandingTabs.onTabShown(want);
}

/** Redraw the rail (labels, counts, the running-commit line) in place. A no-op when the tabbed pane is not
 *  what is on screen. */
export function refreshStageRail() {
  const host = $('content')?.querySelector('[data-rail-host]');
  if (!host) return;
  host.innerHTML = subTabsHtml();
  bindSubTabsIn(host);
}

/** Show one of Curate's tabs: swap which panel is hidden, redraw the rail, start what the newly shown tab's open
 *  sections own. Nothing is fetched here that a visible tab did not already have. */
function switchCurateTab(tab) {
  state.subTab = tab;
  writeUrl();
  const want = tab.replace(/^curate_/, '');
  $('content').querySelectorAll('[data-curate-tab-panel]').forEach((p) => { p.hidden = p.dataset.curateTabPanel !== want; });
  refreshStageRail();
  if (state.curate && state.curate.onTabShown) state.curate.onTabShown();
}

/** Sub-tab clicks: a click switches. Between Curate's own tabs the
 *  switch is in place. */
export function bindSubTabs() {
  bindSubTabsIn($('content'));
}

function bindSubTabsIn(el) {
  el.querySelectorAll('[data-subtab]').forEach((b) => { b.onclick = () => {
    if (curatePanelsMounted() && STAGE_TAB_IDS.curate.includes(b.dataset.subtab)) { switchCurateTab(b.dataset.subtab); return; }
    if (understandingPanelsMounted() && STAGE_TAB_IDS.understanding.includes(b.dataset.subtab)) { switchUnderstandingTab(b.dataset.subtab); return; }
    state.subTab = b.dataset.subtab;
    writeUrl();
    loadPane();
  }; });
  bindCommitLine(el);
}

/* ════════════════════════════════════════════════════════════════════════
 * Survey and Dashboard
 *
 * Both were deferred in /next, and both were deferred against data that was
 * already one GET away — the same mistake Understanding turned out to be.
 * Neither pane invents anything: Survey lists the Survey Definitions the
 * adapter says can run against this resource, Dashboard reads the dashboards
 * the registry already declares for the current stage.
 * ════════════════════════════════════════════════════════════════════════ */

/** A resource must be selected and be a repo for either pane to mean anything. */
/* ── Disposition and the journal ─────────────────────────────────────────
 *
 * The verdict and its dated trail were already in the resource header. What
 * was missing is the journal: WHY a resource matters, written to be read by
 * someone else. Everything else on these screens is written to be correct;
 * this is the first thing written to be read. So the affordance shows it —
 * room to write, no dropdown, a name on it.
 *
 * Append-only. An entry is a statement someone made on a date; later events
 * do not invalidate it, they get a later entry. Outside the durable /
 * perishable split entirely: no review flags, nothing to reconcile.
 *
 * Suggestion is a routing question and perspectives answer it — a note on a
 * data-heavy repo is for Data Experts and Consumers, the same tags on every
 * question row. It arrives as a WORK-LIST ENTRY for them, not a
 * notification: work lists already exist, carry counts, and survive being
 * ignored for a fortnight; a notification is a thing you dismiss. The UI
 * says where it landed rather than "sent".
 *
 * Nothing prompts for an entry at cataloguing time, and nothing blocks on
 * one. Advocacy written to satisfy a required field is "useful library" on
 * two hundred assets. The empty state is visible instead.
 */
/**
 * Slice 22 — the Schemas → Tables → Columns tree for one database.
 *
 * One fetch, the whole tree (`getSchemaInventoryTree`) — the data is not
 * paginated router-side, and classic UI's own schema/table/column panel
 * already proved this is small enough to fetch in one call and toggle with
 * plain DOM show/hide (no lazy per-node fetch needed, unlike `openMembers`'s
 * two-level member rail).
 *
 * Interaction pattern borrowed from the sidebar's own group list (Backlog/
 * `renderSidebar`): native `<details>`/`<summary>` for free expand/collapse
 * semantics, all schemas collapsed by default (no `open` attribute), and a
 * text filter that narrows by name across all three levels — a filtered-in
 * leaf force-opens every `<details>` on its path to the root, exactly the
 * "a match inside a collapsed schema must not stay hidden" rule that list
 * already gets right.
 */
async function loadSchemaInventoryPane() {
  const el = $('content');
  if (state.resourceType !== 'db') {
    // Unreachable through loadPane() (reconcileSubTabForStage() reverts it first); kept as a safe landing.
    state.subTab = stageDefaultSubTab(); writeUrl(); await loadPane();
    return;
  }
  const slug = state.selectedSlug;
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div class="relative mb-s3 w-full max-w-[40ch]">
      <input id="schema-tree-filter" type="text" placeholder="Filter schemas, tables, columns…"
        class="w-full rounded-sm border border-rule bg-transparent px-s2 py-[4px] pr-[26px] text-caveat text-ink placeholder:text-ink-muted" />
      <button id="schema-tree-filter-clear" type="button" aria-label="Clear filter"
        class="absolute right-[6px] top-1/2 hidden -translate-y-1/2 cursor-pointer text-ink-muted hover:text-ink"
      >×</button>
    </div>
    <div data-schema-counts class="mb-[2px] text-answer text-ink"></div>
    <div id="schema-tree-sources" data-schema-tree-sources class="mb-s2 text-provenance text-ink-muted"></div>
    <div id="schema-tree">Reading the schema tree…</div>`;
  bindSubTabs();
  bindResourceHeader();

  let tree;
  try {
    tree = await getSchemaInventoryTree(slug);
  } catch (err) {
    if (slug !== state.selectedSlug) return;
    $('schema-tree').innerHTML = `<div class="text-state-warn">Could not read the schema tree: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug) return;
  $('schema-tree').innerHTML = schemaTreeHtml(tree.schemas || []);
  const srcLine = $('schema-tree-sources');
  if (srcLine) srcLine.textContent = inventoryHeaderText(tree.sources);
  const countsLine = el.querySelector('[data-schema-counts]');
  if (countsLine) countsLine.textContent = threeNumberLine(tree);
  bindSchemaTreeFilter();
}

/** The filter input's own wiring: typing filters live, the × button
 * (Dan's gate, 2026-09-27 -- "needs a clear control") appears once there is
 * something to clear and empties the box back to the unfiltered tree, and
 * Escape does the same without reaching for the mouse. */
function bindSchemaTreeFilter() {
  const input = $('schema-tree-filter');
  const clearBtn = $('schema-tree-filter-clear');
  if (!input || !clearBtn) return;
  const sync = () => { clearBtn.classList.toggle('hidden', !input.value); };
  const clear = () => {
    input.value = '';
    filterSchemaTree('');
    sync();
    input.focus();
  };
  input.addEventListener('input', (e) => { filterSchemaTree(e.target.value); sync(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') clear(); });
  clearBtn.addEventListener('click', clear);
}

const _SCHEMA_SHORTFALL_LABELS = {
  no_access: 'no access', structure_only: 'structure only', staging: 'staging (by name)',
  empty: 'empty', no_tables: 'no tables', not_measured: 'rows not measured',
  views_only: 'views only',
};

/** Any classification word the table above does not know: never the raw snake_case code on screen. */
function schemaShortfallWord(c) { return _SCHEMA_SHORTFALL_LABELS[c] || String(c ?? '').replace(/_/g, ' '); }

/** Size tail of a schema stamp, from the one field the size comes from (`bytes_total`). A size never read says
 *  so; it is not left out. The index share keeps its own words. */
function schemaSizeTail(s) {
  return `${s.bytes_total != null ? ` · ${fmtBytes(s.bytes_total)}` : ' · size not read'}${'index_bytes_total' in s ? (s.index_bytes_total == null ? ' · index not read' : ` (index ${fmtBytes(s.index_bytes_total)})`) : ''}`;
}

//: Table-kind labels -- quiet, muted words distinguishing a base table from
//: a view/materialized view (Dan's gate, 2026-09-27: he asked for a way to
//: tell them apart at a glance without being didactic about it).
const _TABLE_KIND_LABELS = {
  'BASE TABLE': 'table', 'PARTITIONED TABLE': 'table', 'VIEW': 'view', 'MATERIALIZED VIEW': 'matview',
  'FOREIGN': 'foreign table',
};

// `schemaTreeHtml`/`tableHtml`/`filterSchemaTree` are exported (no logic
// change -- these three lines add the `export` keyword only) so the node/
// jsdom render harness (frontend-build/test-harness/) can import and call
// the REAL functions directly, rather than re-extracting/re-executing their
// source text as a string (the pattern every other app.js-adjacent test
// uses -- see tests/test_next_schema_inventory_filter.py's own docstring).
// See NEXT-RENDER-HARNESS-IMPLEMENTED.md.
export function schemaTreeHtml(schemas) {
  if (!schemas.length) return `<div class="text-caveat text-ink-muted">No stored schema rows yet — run a survey first.</div>`;
  const parts = schemas.map((s) => {
    if (s.classification === 'system') {
      return `<div class="mb-s1 text-caveat text-ink-muted" data-tree-node data-tree-text="system">
        ${esc(String(s.system_count))} system schema(s) folded (pg_catalog, information_schema, pg_toast*, pg_temp*)</div>`;
    }
    // `measured` is a schema the Egeria native survey listed: it carries no row total
    // of its own, so the stamp says tables and, when known, size -- never a made-up 0.
    const stamp = s.classification === 'data'
      ? `${s.table_count} table(s) · ${Number(s.row_total || 0).toLocaleString('en-US')} row(s)${s.is_estimate ? ' (est.)' : ''}${schemaSizeTail(s)}`
      : s.classification === 'measured'
        ? `${s.table_count ?? '?'} table(s)${s.row_total != null ? ` · ${Number(s.row_total).toLocaleString('en-US')} row(s)${s.is_estimate ? ' (est.)' : ''}` : ''}${schemaSizeTail(s)}`
        : s.classification === 'views_only'
          // Views hold no storage, so there is no size to read: say so rather than leave the tail off.
          ? `${s.table_count} view(s) · views only · no storage`
          : `${s.table_count} table(s) — ${schemaShortfallWord(s.classification)}`;
    const schemaSrc = nodeSourceLine(s);
    // Found live, `laz_local_adventureworks`, 2026-09-27 (Dan's gate):
    // this used to be the schema name PLUS every table/column name
    // concatenated, so a node's own displayed match state was really "does
    // ANY descendant match", not "does the node ITSELF match" -- the two
    // got conflated in `filterSchemaTree()`, which is what silently hid a
    // matched table's own column rows (they carry only their own name, and
    // never matched the query that matched their PARENT table's name).
    // `data-tree-text` now holds only this node's own name, exactly like
    // the column rows below already did -- `filterSchemaTree()`'s own
    // recursion is what now decides "does a descendant match" separately.
    return `<details class="mb-s2 border-b border-rule pb-s2" data-tree-node data-tree-text="${esc(s.schema.toLowerCase())}">
      <summary class="cursor-pointer text-ink">
        <span class="font-semibold">${esc(s.schema)}</span>
        <span class="text-caveat text-ink-muted"> schema</span>
        <span class="text-provenance text-ink-muted"> — ${esc(stamp)}</span>
        ${schemaSrc ? `<span data-tree-source class="text-provenance text-ink-muted"> · ${esc(schemaSrc)}</span>` : ''}
      </summary>
      ${s.reason ? `<div class="ml-s3 mt-[4px] text-provenance text-ink-muted">${esc(s.reason)}</div>` : ''}
      <div class="ml-s3 mt-s2">${(s.tables || []).map(tableHtml).join('') || '<span class="text-caveat text-ink-muted">No tables.</span>'}</div>
    </details>`;
  });
  return parts.join('');
}

export function tableHtml(t) {
  const rowStamp = t.row_count == null
    ? 'not measured'
    : `${Number(t.row_count).toLocaleString('en-US')} row(s)${t.row_count_state === 'catalog_estimate' ? ' (est.)' : ''}`;
  const byteStamp = t.size_bytes == null ? 'not measured' : fmtBytes(t.size_bytes);
  // Brief D: the index share beside the total. Drawn only when the server sent
  // the field at all (an older payload says nothing); a NULL is "index not
  // read", never 0 B.
  const indexStamp = !('index_bytes' in t) ? ''
    : t.index_bytes == null ? ' · index not read' : ` · index ${fmtBytes(t.index_bytes)}`;
  const kindLabel = _TABLE_KIND_LABELS[t.table_type] || 'table';
  const tableSrc = nodeSourceLine(t);
  // Own name only -- see schemaTreeHtml's comment above on why this is no
  // longer the table+columns concatenation it used to be.
  return `<details class="mb-s1" data-tree-node data-tree-text="${esc(t.name.toLowerCase())}">
    <summary class="cursor-pointer text-ink">
      ${esc(t.name)}
      <span class="text-caveat text-ink-muted"> ${esc(kindLabel)}</span>
      <span class="text-provenance text-ink-muted"> — ${esc(rowStamp)} · ${esc(byteStamp)}${esc(indexStamp)} · ${t.column_count == null ? 'columns not measured' : `${t.column_count} column(s)`}</span>
      ${tableSrc ? `<span data-tree-source class="text-provenance text-ink-muted"> · ${esc(tableSrc)}</span>` : ''}
    </summary>
    <table class="ml-s3 mt-[4px] w-full max-w-[110ch] border-collapse text-caveat">
      ${(t.columns || []).length ? `<thead><tr class="border-b border-rule text-provenance text-ink-muted">
        <th class="py-[3px] pr-s2 text-left font-normal">Column</th><th class="py-[3px] pr-s2 text-left font-normal">Type</th>
        <th class="py-[3px] pr-s2 text-left font-normal">Null</th><th class="py-[3px] pr-s2 text-left font-normal">Key</th>
        <th class="py-[3px] pr-s2 text-left font-normal">Default</th><th class="py-[3px] pr-s2 text-left font-normal">References</th>
        <th class="py-[3px] text-left font-normal">Comment</th></tr></thead>` : ''}
      ${(t.columns || []).map((c) => `<tr class="border-b border-rule" data-tree-node data-tree-text="${esc(c.name.toLowerCase())}">
        <td class="py-[3px] pr-s2 font-mono text-ink">${esc(c.name)}</td>
        <td class="py-[3px] pr-s2 text-ink-muted">${esc(c.type)}</td>
        <td class="py-[3px] pr-s2 text-ink-muted">${c.nullable === null ? 'nullable unknown' : (c.nullable ? 'nullable' : 'not null')}</td>
        <td class="py-[3px] pr-s2 text-accent-ink">${esc(c.key_role || '')}</td>
        <td data-col-default class="py-[3px] pr-s2 font-mono text-ink-muted">${c.default ? esc(c.default) : '<span class="font-sans">none recorded</span>'}</td>
        <td data-col-fk class="py-[3px] pr-s2 font-mono text-ink-muted">${fkTarget(c.foreign_key) ? `→ ${esc(fkTarget(c.foreign_key))}` : '—'}</td>
        <td class="py-[3px] text-ink-muted">${c.comment ? esc(c.comment) : 'comments not captured'}</td>
      </tr>`).join('')}
    </table>
  </details>`;
}

/** Narrows the schema tree by name across all three levels.
 *
 * Found live, `laz_local_adventureworks`, 2026-09-27 (Dan's Slice 22 gate):
 * the previous version matched every `[data-tree-node]` independently
 * against `data-tree-text`, which used to hold a table/schema's own name
 * PLUS every descendant's name concatenated together (so "does this node
 * match" really meant "does this node OR anything under it match"). That
 * made a TABLE look matched (its concatenated text contained the query),
 * but its COLUMN rows -- each carrying only their own name -- did not, so
 * they were independently hidden even though the table's own `<details>`
 * was open: filtering on "salesorderheader" opened the table and showed
 * nothing underneath it.
 *
 * `data-tree-text` is now always a node's OWN name only (schemaTreeHtml/
 * tableHtml's own comments). This recursion is what decides descendant
 * matching, per the rule the gate asked for:
 *   - a node whose OWN name matches shows EVERY descendant (unconditionally
 *     visible, but still collapsed unless individually opened) -- a
 *     matched table therefore reveals all its columns, and a matched
 *     schema reveals all its tables collapsed;
 *   - a node whose own name does not match, but some descendant's does,
 *     stays visible and its own `<details>` opens (so the path down to the
 *     match is reachable), while sibling branches that contain no match
 *     are hidden entirely;
 *   - a node with no match anywhere under it is hidden.
 *
 * Clearing the filter leaves every node exactly as it was (no
 * saved-collapse-state clobbering, unlike the sidebar's persistent one —
 * this tree has no cross-session collapse preference to protect). */
export function filterSchemaTree(raw) {
  const q = raw.trim().toLowerCase();
  const root = $('schema-tree');
  if (!root) return;
  if (!q) {
    root.querySelectorAll('[data-tree-node]').forEach((n) => { n.style.display = ''; });
    return;
  }
  directTreeChildren(root).forEach((n) => filterTreeNode(n, q));
}

/** Filters one `[data-tree-node]` (and everything under it) against `q`,
 * per the rule in `filterSchemaTree`'s own docstring. Returns whether `el`
 * itself, or anything under it, matched -- so a caller one level up knows
 * whether to keep `el` visible as part of a deeper match's path. */
function filterTreeNode(el, q) {
  const ownMatch = (el.dataset.treeText || '').includes(q);
  if (ownMatch) {
    el.style.display = '';
    if (el.tagName === 'DETAILS') el.open = true;
    // Unconditionally visible from here down -- no further per-node
    // filtering, exactly the "a matched table shows all its columns" rule.
    // Nested `<details>` are left in whatever open/closed state they were
    // already in, which is how "a matched schema shows all its tables
    // COLLAPSED" falls out for free (a table's own columns stay invisible
    // behind its own closed `<details>`, regardless of this display style).
    el.querySelectorAll('[data-tree-node]').forEach((n) => { n.style.display = ''; });
    return true;
  }
  const children = directTreeChildren(el);
  const anyChildMatched = children.reduce((acc, c) => filterTreeNode(c, q) || acc, false);
  el.style.display = anyChildMatched ? '' : 'none';
  if (anyChildMatched && el.tagName === 'DETAILS') el.open = true;
  return anyChildMatched;
}

/** The `[data-tree-node]` elements directly under `el` in tree terms -- it
 * descends through plain wrapper markup (the schema's table-list `<div>`,
 * a table's own `<table>`/`<tr>` structure) but stops at the first
 * `[data-tree-node]` it finds along each branch, so a schema's traversal
 * yields its tables, never reaching past them into their own columns. */
function directTreeChildren(el) {
  const out = [];
  const walk = (node) => {
    for (const child of node.children) {
      if (child.matches('[data-tree-node]')) out.push(child);
      else walk(child);
    }
  };
  walk(el);
  return out;
}

async function loadDispositionPane() {
  const el = $('content');
  // Generalized 2026-09-22 (Backlog.md, "Disposition is NOT fixed here"):
  // `repo_dispositions`' PK widened to (entity_type, entity_slug), the
  // journal/records routes got entity-generic siblings, so this pane no
  // longer needs a repo-only backend gate — `paneNeedsRepo()` (any
  // resource selected) is enough, same as By-analysis/Questions since
  // PR #220.
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  const entityType = apiEntityType(state.resourceType);
  const isRepo = entityType === 'repo';
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div class="mb-s1 text-caps uppercase tracking-caps text-ink">Verdicts</div>
    <div id="disposition-picker" class="mb-s2"></div>
    <div id="disposition-history" class="mb-s4 text-provenance text-ink-muted">Loading history…</div>
    <div id="depth-offer" class="mb-s4"></div>
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="text-caps uppercase tracking-caps text-ink">Records</span>
      <span class="text-provenance text-ink-muted">what was published, and what was written down · a snapshot, not a query</span>
    </div>
    <div id="records" class="mb-s4 text-caveat text-ink-muted">Reading the records…</div>
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="text-caps uppercase tracking-caps text-ink">Journal</span>
      <span class="text-provenance text-ink-muted">why it matters, and to whom · written to be read</span>
    </div>
    <div id="journal-write"></div>
    <div id="journal-entries" class="mt-s3 text-caveat text-ink-muted">Reading the journal…</div>`;
  bindSubTabs();
  bindResourceHeader();
  const resource = selectedProject();
  // A repo's disposition is keyed on its github_url (stable across import/
  // renames — see registry.py's resolve_repo_entity_slug); a database/
  // filesystem's is keyed on its slug directly, since that IS its stable
  // identity from registration. Either way `resource` must exist.
  const canDispose = isRepo ? Boolean(resource?.github_url) : Boolean(resource);
  if (canDispose) {
    // The picker WITH its trail, as drawn -- the header popover is a click
    // away and above the heading; this is where a verdict is considered.
    const mountPicker = () => {
      const pick = $('disposition-picker');
      if (!pick) return;
      pick.innerHTML = dispositionPickerHtml(resource);
      wireDispositionPicker(pick, resource, {
        note: (html) => { const h = $('disposition-history'); if (h) h.innerHTML = html; },
        onSet: async () => { mountPicker(); await renderDispositionHistory(entityType, isRepo ? resource.github_url : slug); },
      }, entityType, slug);
    };
    mountPicker();
    renderDispositionHistory(entityType, isRepo ? resource.github_url : slug);
    // DepthOffer stays repo-only, deliberately -- it reasons about
    // never-run analyses at the analysis/assessment tiers against the repo
    // analysis catalog, which has no database/filesystem equivalent here;
    // the function itself no-ops without a github_url.
    if (isRepo) renderDepthOffer(resource, $('depth-offer'));
  } else {
    $('disposition-history').textContent = isRepo
      ? 'No GitHub URL, so no disposition can be keyed to this resource.'
      : 'This resource could not be found.';
  }
  renderJournalWrite(slug, entityType);
  await renderJournalEntries(slug, entityType);
  await renderRecords(slug, entityType);
}

/** Records under the resource, beside the journal and the verdict trail --
 *  not inside the journal, which is prose testimony and which a
 *  thirty-two-row table fights. A catalogue record shows its steps inline
 *  as Curate draws them; a report shows its header sentence. Same row
 *  grammar, same date, same author (REPORT-RECORD-AND-TWO-CALLS C4). */
export async function renderRecords(slug, entityType) {
  requireKind('renderRecords', entityType);
  const host = $('records');
  if (!host) return;
  let recs;
  try { recs = (await listRecords(slug, entityType)).records || []; }
  catch (err) { host.innerHTML = `<span class="text-state-warn">The records could not be read: ${esc(err.message)}</span>`; return; }
  if (slug !== state.selectedSlug) return;
  if (!recs.length) { host.textContent = 'No record has been written for this resource yet — nothing published, nothing written down.'; return; }
  const scopeAct = await scopeActState(entityType, slug);
  if (slug !== state.selectedSlug) return;
  host.innerHTML = recs.map((r) => {
    const when = `<span class="tnum">${esc(ago(r.requested_at))}</span> <span class="tnum">(${esc(String(r.requested_at).slice(0, 10))})</span>`;
    if (r.kind === 'report') {
      const rep = r.report || {};
      return `<div class="border-t border-rule py-s2" data-record="${esc(r.id)}">
        <div class="text-answer text-ink">${esc(r.name)}</div>
        <div class="text-caveat text-ink"><strong>${tnum(esc(rep.header || ''))}</strong></div>
        <div class="text-provenance text-ink-muted">${tnum(esc(rep.provenance || ''))} · a snapshot, not a query</div>
        <div class="text-provenance text-ink-muted">report · ${esc(r.author)} · ${when}${rep.question ? ` · asked as <em>${esc(rep.question)}</em>` : ''}
          · <a class="text-accent-ink underline" href="${recordExportHref(slug, r.id, 'md')}">markdown</a>
          · <a class="text-accent-ink underline" href="${recordExportHref(slug, r.id, 'csv')}">csv</a>
          · <button data-record-open="${esc(r.id)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">rows${icon('chevron-right', { size: 12 })}</button></div>
        ${r.out_of_date ? `<div class="text-provenance text-state-warn">⚠ ${esc(r.out_of_date)}</div>` : ''}
        ${r.corrects ? `<div class="text-provenance text-ink-muted">corrects an earlier record</div>` : ''}
        <div data-record-rows hidden class="mt-s1 text-provenance">${(rep.groups || []).map((g) => `
          <div class="text-ink"><span class="tnum">${g.count}</span> · ${esc(g.name)}</div>
          <ul class="m-0 list-none p-0 pl-s2">${g.rows.map((row) => `<li class="flex items-baseline gap-s2 font-mono text-ink">
            <input type="checkbox" data-record-row="${esc(row.name)}" class="shrink-0">${esc(row.name)}${row.detail ? ` <span class="font-body text-ink-muted">${esc(row.detail)}</span>` : ''}</li>`).join('')}${
            g.truncated ? `<li class="text-ink-muted">and more — the first ${g.rows.length} are shown</li>` : ''}</ul>`).join('')}</div>
        ${recordActsHtml(r, rowCount(rep), scopeAct)}
        ${recordUsesHtml(r)}
      </div>`;
    }
    // curateRecordHtml carries the author, date and state line itself.
    return `<div class="border-t border-rule py-s2" data-record="${esc(r.id)}">
      <div class="text-answer text-ink">published${r.manifest?.entities?.length ? ` · ${esc(r.manifest.entities.join(', '))}` : ''}</div>
      ${curateRecordHtml(r)}
    </div>`;
  }).join('');
  host.querySelectorAll('[data-record-open]').forEach((b) => b.addEventListener('click', () => {
    const rows = host.querySelector(`[data-record="${b.dataset.recordOpen}"] [data-record-rows]`);
    if (rows) rows.hidden = !rows.hidden;
  }));
  wireRecordActs(host, slug, recs, entityType);
}

function rowCount(rep) {
  return (rep.groups || []).reduce((n, g) => n + (g.rows || []).length, 0);
}

/* ── The three acts on a report ──────────────────────────────────────────
 *
 * REPORT-ACTS (designer, 2026-09-14). The three acts on a report are not
 * the three acts on a list: a list's selection is live, so an act on it is
 * an act on what is true; a report's rows are frozen, so an act on it is
 * an act on what WAS true. The footer defaults to the whole report -- the
 * rows were already chosen once, that is what saving them was -- with row
 * picking the secondary path. Each act points at the record; the server
 * acts on the snapshot and never re-derives; the journal opens seeded with
 * a citation, not a sentence; a stale record keeps all three live and what
 * they create carries the staleness; the record learns it was used. */
/** What the "add to <investigation>" act should say for this resource: which
 *  investigation is current and whether the resource is already in its scope.
 *  The scope is read from the server (it holds every kind; `state.workingSet`
 *  holds repos only). If it cannot be read the act is offered as "add" -- the
 *  server refuses to overwrite an existing member's reason either way. */
async function scopeActState(entityType, slug) {
  const invSlug = state.investigation;
  if (!invSlug) return { invName: '', inScope: false };
  const inv = state.investigations.find((i) => i.slug === invSlug);
  const invName = inv?.display_name || invSlug;
  let inScope = false;
  try {
    inScope = ((await listInvestigationMembers(invSlug)) || [])
      .some((m) => m.entity_type === entityType && m.entity_slug === slug);
  } catch { /* unknown: offer the act */ }
  return { invName, inScope };
}

/** The current investigation's display name, '' when none is current. */
function curInvName() {
  if (!state.investigation) return '';
  const inv = state.investigations.find((i) => i.slug === state.investigation);
  return inv?.display_name || state.investigation;
}

function scopeActHtml(scopeAct, attr, cls) {
  const w = scopeActWording(scopeAct.invName, scopeAct.inScope);
  if (w.mode === 'text') return `<span data-scope-act-text class="text-ink-muted" title="${esc(w.title)}">${esc(w.label)}</span>`;
  return `<button ${attr} title="${esc(w.title)}" class="${cls}">${esc(w.label)}</button>`;
}

function recordActsHtml(r, n, scopeAct = { invName: '', inScope: false }) {
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  return `<div data-record-acts class="mt-s2 text-caveat">
    <div class="text-ink"><span data-record-scope>The whole report · <span class="tnum">${n}</span> row${n === 1 ? '' : 's'}</span>
      <span class="text-ink-muted">· as recorded ${esc(String(r.requested_at).slice(0, 10))}${r.out_of_date ? ' · its evidence has since moved — what these create will say so' : ''}</span></div>
    <div class="mt-[3px] flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-provenance">
      ${scopeActHtml(scopeAct, `data-record-act="scope" ${me ? '' : 'disabled'}`, 'cursor-pointer bg-transparent p-0 text-accent-ink underline')}
      <button data-record-act="rfa" ${me ? '' : 'disabled'} class="cursor-pointer bg-transparent p-0 text-accent-ink underline">raise RFA</button>
      <button data-record-act="journal" ${me ? '' : 'disabled'} class="cursor-pointer bg-transparent p-0 text-accent-ink underline">note in journal</button>
      ${r.out_of_date && !r.corrected_by?.id ? `<button data-record-correct ${me ? '' : 'disabled'} class="cursor-pointer bg-transparent p-0 text-accent-ink underline">write a correction</button>` : ''}
      <span class="text-ink-muted">${me ? '· or pick rows above to act on some of them' : '· Sign in to act on a report — a work item needs someone who raised it.'}</span>
      <span data-record-status class="text-ink-muted"></span>
    </div>
  </div>`;
}

/** 'Used · added to work list "…" · 09-14 08:12 · dwolfson' — a separate
 *  append-only list attached to the record; and 'Corrected by "…" · 09-14'
 *  in the same place. */
function recordUsesHtml(r) {
  const uses = (r.uses || []).map((u) => {
    const what = u.act === 'scope' ? `added to ${esc(u.target_name)}’s scope`
      : u.act === 'work_list' ? `added to work list “${esc(u.target_name)}”`
      : u.act === 'rfa' ? `raised RFA “${esc(u.target_name)}”`
      : u.act === 'journal' ? 'cited in the journal' : esc(u.act);
    return `<div class="text-provenance text-ink-muted">Used · ${what} · <span class="tnum">${esc(String(u.at).slice(5, 16).replace('T', ' '))}</span> · ${esc(u.by)}</div>`;
  });
  if (r.corrected_by?.id) {
    uses.push(`<div class="text-provenance text-ink-muted">Corrected by “${esc(r.corrected_by.name)}” · <span class="tnum">${esc(String(r.corrected_by.at).slice(5, 10))}</span> · ${esc(r.corrected_by.by || '')}</div>`);
  }
  return uses.length ? `<div class="mt-s1">${uses.join('')}</div>` : '';
}

function wireRecordActs(host, slug, recs, entityType) {
  requireKind('wireRecordActs', entityType);
  host.querySelectorAll('[data-record]').forEach((box) => {
    const id = box.dataset.record;
    const rec = recs.find((x) => x.id === id);
    if (!rec || rec.kind !== 'report') return;
    const status = box.querySelector('[data-record-status]');
    const scope = box.querySelector('[data-record-scope]');
    const picked = () => [...box.querySelectorAll('[data-record-row]:checked')].map((c) => c.dataset.recordRow);
    const total = rowCount(rec.report || {});
    box.querySelectorAll('[data-record-row]').forEach((c) => c.addEventListener('change', () => {
      const n = picked().length;
      if (scope) scope.innerHTML = n
        ? `<span class="tnum">${n}</span> of <span class="tnum">${total}</span> rows picked`
        : `The whole report · <span class="tnum">${total}</span> row${total === 1 ? '' : 's'}`;
    }));
    box.querySelectorAll('[data-record-act]').forEach((b) => b.addEventListener('click', async () => {
      const action = b.dataset.recordAct;
      const rows = picked().length ? picked() : null;
      if (action === 'journal') {
        // Seeded with a citation, not a sentence; the person writes the
        // thought. The use is recorded when the entry lands.
        const ta = $('journal-body');
        if (!ta) { status.textContent = 'the journal is on this pane — scroll down'; return; }
        ta.value = `Per “${rec.name}” (${String(rec.requested_at).slice(0, 10)}): `;
        ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
        ta.dataset.citesRecord = id;
        status.textContent = '→ the journal, below — write the thought after the citation';
        return;
      }
      const perform = async (investigation = '') => {
        b.disabled = true; status.textContent = '…';
        try {
          const out = await actOnRecord(slug, id, { action, rows, investigation }, entityType);
          let becameCurrent = false;
          if (action === 'scope') {
            // The chosen investigation becomes the current one, as in the
            // Select bar; either way its scope is re-read for the member count.
            becameCurrent = investigation !== state.investigation;
            if (becameCurrent) await setInvestigation(investigation);
            else await loadWorkingSet();
            await refreshOpenInvestigation(investigation);
          }
          await renderRecords(slug, entityType);
          // renderRecords rebuilt the box; say where it went in the new one.
          const fresh = $('records')?.querySelector(`[data-record="${id}"] [data-record-status]`);
          const where = action === 'scope'
            ? (out.already_in_scope ? `already in ${out.investigation_name}’s scope, left as it was` : `added to ${out.investigation_name}’s scope${becameCurrent ? ', now your current investigation' : ''}`)
            : `RFA ${String(out.rfa).slice(0, 8)} raised, pointing at this record`;
          if (fresh) fresh.innerHTML = `<span class="text-state-ok">→ ${esc(where)}</span>`;
        } catch (err) {
          b.disabled = false;
          status.innerHTML = `<span class="text-accent-ink">not recorded${err.status === 401 ? ' — sign in to act on a report' : `: ${esc(err.message)}`}</span>`;
        }
      };
      if (action === 'scope') {
        // With none current, ask which investigation first; the act continues
        // in the picker's callback.
        if (state.investigation) await perform(state.investigation);
        else chooseInvestigationThen((chosen) => perform(chosen));
        return;
      }
      await perform();
    }));
    box.querySelector('[data-record-correct]')?.addEventListener('click', async (ev) => {
      // A correction is save-as-report with the superseded record's id
      // attached: the whole current list, snapshotted now, naming what it
      // corrects. The old record learns who corrected it.
      const b = ev.currentTarget; b.disabled = true; status.textContent = 'writing the correction…';
      try {
        const rep = rec.report || {};
        const out = await saveReport(slug, rep.analysis_id, {
          question: rep.question || '', metric: rep.metric || '', members: null, facet: rep.facet || '',
          name: `${rec.name} — corrected ${new Date().toISOString().slice(0, 10)}`, scope: 'all', corrects: id,
        });
        status.innerHTML = `<span class="text-state-ok">→ correcting record “${esc(out.record.name)}” · ${esc(out.record.report?.header || '')}</span>`;
        await renderRecords(slug, entityType);
      } catch (err) {
        b.disabled = false;
        status.innerHTML = `<span class="text-accent-ink">not recorded${err.status === 401 ? ' — sign in to write a correction' : `: ${esc(err.message)}`}</span>`;
      }
    });
  });
}

export function renderJournalWrite(slug, entityType) {
  requireKind('renderJournalWrite', entityType);
  const host = $('journal-write');
  if (!host) return;
  const who = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  // An AUDIENCE is the whole Egeria vocabulary, not the filter row's
  // subset: someone is a Data Owner whether or not any analysis is tagged
  // with it today. (The two are the same twelve at the moment; the point
  // is which list this one follows when they diverge.)
  const perspectives = (state.allPerspectives || state.perspectives || []).map((p) => p.name || p.id || p).filter(Boolean);
  host.innerHTML = `
    <textarea id="journal-body" rows="4" placeholder="Worth using for anyone who… Note the… before depending on it."
      class="w-full rounded-sm border border-rule-strong bg-transparent p-s2 text-answer text-ink placeholder:text-ink-muted"></textarea>
    <div class="mt-s1 flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-caveat">
      <span class="text-ink-muted">suggest to</span>
      ${perspectives.map((p) => `<label class="flex cursor-pointer items-baseline gap-[4px]">
        <input type="checkbox" data-suggest="${esc(p)}"> ${esc(p)}</label>`).join('')}
      <label class="flex items-baseline gap-[4px] text-ink-muted">+ <input id="journal-person" type="text" placeholder="a person…"
        class="w-[12ch] rounded-sm border border-rule-strong bg-transparent px-[4px] text-caveat text-ink placeholder:text-ink-muted"></label>
    </div>
    <div class="mt-s2 flex items-baseline gap-s3">
      <button id="journal-save" type="button" ${who ? '' : 'disabled title="sign in to save — an entry needs an author"'}
        class="${who ? 'cursor-pointer' : 'opacity-60'} rounded-sm border border-accent bg-transparent px-2 py-[2px] text-answer text-accent-ink">Save entry</button>
      <span class="text-provenance text-ink-muted">saved entries are permanent: no edit, no delete
        · ${who ? `as ${esc(who)}` : 'sign in to save — an entry needs an author'}
        · a suggestion is a work-list entry for them, not a notification</span>
    </div>`;
  $('journal-save').addEventListener('click', async () => {
    const body = $('journal-body').value.trim();
    if (!body) { $('journal-body').focus(); return; }
    const targets = [...host.querySelectorAll('[data-suggest]:checked')].map((c) => c.dataset.suggest);
    const person = ($('journal-person').value || '').trim();
    if (person) targets.push(person);
    const b = $('journal-save'); b.disabled = true; b.textContent = 'saving…';
    try {
      const out = await writeJournal(slug, body, targets, entityType);
      const cites = $('journal-body').dataset.citesRecord;
      if (cites) {
        // The record learns it was cited. Best effort: the entry is real
        // either way, and a failure here is not a failed write.
        try { await actOnRecord(slug, cites, { action: 'journal', journalId: out.id || '' }, entityType); } catch { /* the entry stands */ }
        delete $('journal-body').dataset.citesRecord;
        renderRecords(slug, entityType);
      }
      $('journal-body').value = ''; $('journal-person').value = '';
      host.querySelectorAll('[data-suggest]').forEach((c) => { c.checked = false; });
      b.disabled = false; b.textContent = 'Save entry';
      // Where it landed, by the list's NAME -- "Suggested to Data Expert"
      // is what was created; the slug is how the server finds it. And it
      // stays until the next write replaces it: this is the only record
      // the writer gets, and six seconds is not long enough to read one.
      const where = (out.work_lists || []).map((w) => w.name || w.work_list).join(', ');
      host.querySelector('[data-journal-note]')?.remove();
      const note = document.createElement('div');
      note.className = 'mt-s1 text-provenance text-ink';
      note.setAttribute('data-journal-note', '1');
      host.appendChild(note);
      // "saved · who · when" comes from the RE-READ entry, never from the click.
      const entries = await renderJournalEntries(slug, entityType);
      const mine = (entries || []).find((e) => out.id && e.id === out.id);
      note.textContent = mine
        ? `${savedLine(mine.author, mine.written_at)}${where ? ` · suggested — now in “${where}”` : ''}`
        : 'the save returned, but the entry is not in the re-read list';
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to save' : `not saved: ${err.message}`;
    }
  });
}

export async function renderJournalEntries(slug, entityType) {
  requireKind('renderJournalEntries', entityType);
  const host = $('journal-entries');
  if (!host) return;
  let data;
  try { data = await getJournal(slug, entityType); }
  catch (err) { host.innerHTML = `<span class="text-state-warn">The journal could not be read: ${esc(err.message)}</span>`; return []; }
  if (slug !== state.selectedSlug) return [];
  const entries = data.entries || [];
  if (!entries.length) {
    // Visible, and a fair thing for a corpus view to count: catalogued,
    // never written about.
    host.innerHTML = `<span class="text-ink-muted">Nobody has written about this resource yet.</span>`;
    return entries;
  }
  host.innerHTML = `
    <div class="mb-s1 text-caps uppercase tracking-caps text-ink-muted">Earlier entries · <span class="tnum">${entries.length}</span>${
      data.suggested_to?.length ? ` · suggested to ${esc(data.suggested_to.join(', '))}` : ''}</div>
    ${entries.map((e) => `<div class="border-t border-rule py-s2" data-journal-entry="${esc(e.id || '')}">
      <p class="m-0 max-w-[70ch] text-answer text-ink">${tnum(esc(e.body))}</p>
      <div class="text-provenance text-ink-muted">${esc(e.author || 'unsigned · from before authors were recorded')} · <span class="tnum">${esc(ago(e.written_at))}</span>${
        e.suggested_to?.length ? ` · suggested to ${esc(e.suggested_to.join(', '))}` : ''}<span data-journal-cue="${esc(e.id || '')}"></span></div>
    </div>`).join('')}`;
  // Brief F: Curate's notes band fills each entry's Egeria state (a cue and a word) into its slot.
  host.ownerDocument.dispatchEvent(new CustomEvent('re:journal-drawn', { detail: { slug, entityType } }));
  return entries;
}

function paneNeedsRepo() {
  if (!state.selectedSlug) {
    return paneMessage('Select a resource',
      `Pick a ${state.resourceType === 'repo' ? 'repository' : state.resourceType === 'db' ? 'database' : 'filesystem'} from the sidebar.`);
  }
  return '';
}

/** A stricter helper used to live here — a gate for panes whose BACKEND was
 *  repo-only rather than merely un-built in /next.
 *  By analysis and the Questions checklist came off it on 2026-09-22 (their
 *  backends generalized: `workflows.analysis.build_survey_results`,
 *  `workflows.scouting.build_question_checklist`) and Disposition — the
 *  last caller — came off it the same day once `repo_dispositions`' PK
 *  generalized from `github_url` alone to `(entity_type, entity_slug)` and
 *  the journal/records routes grew entity-generic siblings (Backlog.md,
 *  "Disposition is NOT fixed here"). With no callers left, that helper was
 *  removed rather than kept as dead code a future gate might reach for
 *  again without re-verifying the backend actually needs it. */

/** The tiers, in the order a funnel is worked through. */
const SURVEY_TIERS = ['scouting', 'discovery', 'assessment', 'analysis',
                      'refresh', 'automate_full'];

/** Placeholder, like the matrix's — see STALE_DAYS there. */
const SURVEY_STALE_DAYS = 7;

/** The first sentence of a description: what this will produce.
 *
 * The full text is a maintainer's CHANGELOG — which definition was renamed,
 * that `qualified_name` never changed across either rename. That is real
 * provenance and it is how a real bug was once found, but it answers a
 * question nobody deciding whether to spend compute is asking, and it was the
 * longest text on the pane. It moves behind a disclosure; the first sentence,
 * which is the purpose, comes up to the row.
 */
function onePurpose(text) {
  const t = String(text || '').trim();
  if (!t) return '';
  const stop = t.search(/[.!?](\s|$)/);
  const first = stop === -1 ? t : t.slice(0, stop + 1);
  return first.length > 160 ? `${first.slice(0, 157)}…` : first;
}

/** Last run, with the matrix's own staleness treatment — a rule, not a colour.
 *
 *  The ⚠ used to be a bare, unclickable glyph — an indicator with no
 *  explanation, found live 2026-09-26 clicking it on Database Scouting
 *  Scan and getting nothing. When the run recorded step errors
 *  (`last_run_errors`, from `get_survey_definition_last_activity`'s own
 *  parse of the run's activity-log detail), it is now a button opening
 *  that same "definition history"-style dialog with the failing step(s)
 *  and their message(s) — the tooltip (this function's own `title`) already
 *  named the bare status word, which stays as the fallback when there is
 *  nothing more specific to show. */
const RUN_LIVE = ACTIVITY_LIVE;
export function lastRunHtml(c) {
  const when = c.last_run_at || '';
  if (!when) return '<span class="text-ink-muted">never run</span>';
  const days = (Date.now() - Date.parse(when)) / 86400000;
  const stale = Number.isFinite(days) && days >= SURVEY_STALE_DAYS;
  const ok = (c.last_run_status || '') === 'ok';
  if (RUN_LIVE.has(String(c.last_run_status || '').toLowerCase())) {
    // Still going: the cue and one word, from the persisted row. The pane follows it to its end.
    return `<span data-run-live title="${esc(when)} · ${esc(c.last_run_status)}"><span class="text-ink-muted" aria-hidden="true">◔</span> running · started ${esc(ago(when))}</span>`;
  }
  const errors = c.last_run_errors || [];
  // "recorded" = a step_runs row proves it ran, not that it succeeded: no
  // success cue, but not a failure either, so no warning glyph.
  const warn = !ok && c.last_run_status && c.last_run_status !== 'recorded'
    ? (errors.length
        ? `<button type="button" data-run-errors="${esc(c.qualified_name || '')}"
             class="cursor-pointer bg-transparent text-state-warn underline decoration-dotted"
             title="Click to see what failed">⚠</button> `
        : `<span class="text-state-warn" title="${esc(c.last_run_status)}">⚠</span> `)
    : '';
  // Engine host case 3 (C4): who coordinated the run and who pressed Run, as the run itself recorded it.
  const by = c.last_run_by ? ` · started by ${esc(c.last_run_by)}` : '';
  const startedIn = c.last_run_coordinated_by === 'egeria'
    ? ` <span data-run-started-in class="text-ink-muted">· coordinated by Egeria${by}</span>`
    : (c.last_run_started_by === 're' && c.last_run_by
      ? ` <span data-run-started-in class="text-ink-muted">· run by RE${by}</span>` : '');
  return `<span title="${esc(when)}${c.last_run_status ? ` · ${esc(c.last_run_status)}` : ''}">
    ${ok ? '<span class="text-state-ok">✓</span> ' : warn}
    <span class="${stale ? 'wl-age-text' : ''}">ran ${esc(ago(when))}</span>${startedIn}</span>${
    c.last_publish_proof ? ` ${publishProofHtml(c.last_publish_proof)}` : ''}`;
}

/** Which engine actually ran a definition's last run, rendered ON THE ROW
 *  itself -- sourced from `last_run_engine_note` (persisted in the
 *  activity_log detail SurveyDefinitionExecutor already writes, carried
 *  through by registry.get_survey_definition_last_activity /
 *  survey_definitions.py), not from a page-lifetime DOM node.
 *
 *  Design bug this closes (2026-09-28,
 *  ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md): launchSurvey()
 *  used to append this as a transient `#survey-note` div and then, a few
 *  lines later, call loadSurveyPane() -- which re-renders the whole pane,
 *  including a fresh, empty `#survey-note`, wiping the note out right after
 *  writing it. A live check confirmed dispatch itself worked correctly
 *  (flow-run completed, step_runs recorded) -- only the UI signal was lost.
 *  Deriving the line from data instead of a transient write means it
 *  survives any re-render, and shows up for past runs too, not only the one
 *  just launched.
 *
 *  Exact display text per design sign-off:
 *    success:  "ran via Prefect (flow-run <id-prefix>…, <HH:MM>Z)"
 *    fallback: "ran locally: Prefect dispatch failed — <reason>"
 */
function engineNoteHtml(c) {
  const note = c.last_run_engine_note || '';
  if (!note) return '';
  const fellBack = /unreachable|dispatch failed/i.test(note);
  let text;
  if (fellBack) {
    // Two whole-definition fallback shapes the executor writes into
    // `engine_note` (survey_definition_executor.py):
    //   "Prefect API unreachable at <detail>: ran locally"  -- has a reason
    //   "Prefect dispatch failed: ran locally"               -- no reason at all
    // Strip the boilerplate on both ends; an empty remainder (the second
    // shape) means there is nothing to name after the dash, so leave it off
    // rather than rendering "... — ran locally", a reason that names no
    // reason.
    const reason = note
      .replace(/^Prefect API unreachable at\s*/i, '')
      .replace(/^Prefect dispatch failed:?\s*/i, '')
      .replace(/:\s*ran locally\s*$/i, '')
      .replace(/^ran locally$/i, '')
      .trim();
    text = reason
      ? `ran locally: Prefect dispatch failed — ${reason}`
      : 'ran locally: Prefect dispatch failed';
  } else {
    const m = note.match(/flow-run\s+([^\s)]+)/i);
    const idPart = m ? `flow-run ${m[1].slice(0, 8)}…` : 'flow-run';
    const ms = whenMs(c.last_run_at);
    const whenPart = Number.isFinite(ms)
      ? `, ${new Date(ms).toISOString().slice(11, 16)}Z` : '';
    text = `ran via Prefect (${idPart}${whenPart})`;
  }
  return `<div class="mt-[2px] text-provenance ${fellBack ? 'text-state-warn' : 'text-ink-muted'}">${esc(text)}</div>`;
}

/** The union of annotation types a definition's steps declare — RE steps carry
 *  their own `annotation_types`, native (Egeria-executed) steps carry theirs
 *  nested one level down, in `egeria_produced_annotation_types[].annotation_type`.
 *  De-duplicated because two steps commonly declare the same type. */
function producesTypes(c) {
  const seen = new Set();
  for (const s of c.steps || []) {
    for (const t of s.annotation_types || []) if (t) seen.add(t);
    for (const p of s.egeria_produced_annotation_types || []) if (p && p.annotation_type) seen.add(p.annotation_type);
  }
  return [...seen];
}

/** A run that failed because the database is not cataloged in Egeria (the
 *  executor's own two sentences, the same ones Classic matched). */
const NOT_CATALOGED_RE = /no stored Egeria asset guid|is not yet cataloged in Egeria|is not yet published to Egeria|uncataloged database|unpublished database/i;

/** Parity G2, PI-019. On a database definition whose last run failed because
 *  the database is not cataloged: "Catalog now →" (Curate's own Catalog button
 *  is the one place that writes to Egeria, so this only routes there) and, once
 *  the resource's publish-state line reads cataloged, "Retry →" on the SAME
 *  row. Retry opens the usual run plan; nothing is retried automatically. */
function catalogRetryHtml(c) {
  if (state.resourceType !== 'db') return '';
  if ((c.last_run_status || '') === 'ok') return '';
  if (!(c.last_run_errors || []).some((e) => NOT_CATALOGED_RE.test(String(e)))) return '';
  const cataloged = !!selectedProject()?.is_published;
  return cataloged
    ? `<button type="button" data-retry-run="${esc(c.qualified_name || c.guid)}"
        class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
        >Retry →</button>`
    : `<button type="button" data-catalog-now="${esc(c.qualified_name || c.guid)}"
        class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
        title="not published to Egeria · the Publish button on Curate writes it">Publish now →</button>`;
}

/** The run buttons of the survey pane's rows: plan a run, retry, catalog now. */
export function bindSurveyRowActions(el, all, slug) {
  el.querySelectorAll('[data-run-survey], [data-plan-survey], [data-retry-run]').forEach((b) =>
    b.addEventListener('click', () => {
      const ref = b.dataset.runSurvey || b.dataset.planSurvey || b.dataset.retryRun;
      planSurveyRun(all.find((x) => (x.qualified_name || x.guid) === ref), slug);
    }));
  el.querySelectorAll('[data-catalog-now]').forEach((b) =>
    b.addEventListener('click', () => {
      // Curate holds the Catalog button; this only goes there (Contents, once the tabs are on).
      openCurate({ tab: 'curate_contents' });
    }));
  // Engine host case 3: "coordinated by Egeria", beside each definition's Run. After a start the pane re-reads, so
  // the Egeria-coordinated run appears in "Egeria's own surveys" from its persisted row.
  bindCoordinateControls(el, slug, { after: () => { if (slug === state.selectedSlug) loadSurveyPane(); } });
  // The run line on a definition card carries the same controls as any run line: report links, "why", the credential
  // link of a fixable refusal and "check now" (one read, then the pane repaints from the server's rows).
  bindRunLineControls(el, slug, { checkNow: async () => { if (slug === state.selectedSlug) await loadSurveyPane(); } });
}

/** The definition row's button. It OPENS A DIALOG (the run plan, which since G2 offers "Use different
 *  credentials for this run"), so the word ends in an ellipsis, the opener cue every other dialog
 *  opener uses; "Re-run →" stays on the analyses rows below, whose button runs at once (brief
 *  section 8: same word, same effect). */
export const DEFINITION_RUN_WORD = (c) => (c.last_run_at ? 'Re-run…' : 'Run…');

/** "runs 5 analyses" beside the step count (brief section 9), from the definition's own steps mapped
 *  through the adapter on the server; the list is one gesture away. Nothing when the server did not
 *  say (a count the server did not give is not a zero). */
export function definitionRunsHtml(c) {
  const own = c.runs_analyses && Array.isArray(c.runs_analyses.own) ? c.runs_analyses.own : null;
  if (!own) return '';
  const also = c.runs_analyses.also || [];
  const list = (xs) => xs.map((a) => `<li>${esc(a.name || a.analysis_id)} <span class="font-mono text-ink-muted">${esc(a.analysis_id)}</span></li>`).join('');
  return ` · <details data-runs-analyses class="inline"><summary class="inline cursor-pointer underline">runs <span class="tnum">${own.length}</span> analys${own.length === 1 ? 'is' : 'es'}</summary>
    <ul class="ml-s3 list-disc text-provenance text-ink-muted">${list(own)}</ul>${also.length
      ? `<div class="text-provenance text-ink-muted">also produces, from the same steps:</div><ul class="ml-s3 list-disc text-provenance text-ink-muted">${list(also)}</ul>` : ''}</details>`;
}

/** Whether the card's run line is the Egeria-coordinated run's (the latest run, per the server's merge of the two
 *  sources) and the server gave that run's derived row. Else the short "ran 2 days ago" cell stands. */
function coordinatedRunShown(c) {
  return !!(c.coordinated_row && c.coordinated_row.run && c.last_run_coordinated_by === 'egeria');
}

/** The run line and the run history, on the definition's own card (designer 2026-10-16, Q1: one thing, one card). */
function definitionRunBlockHtml(c) {
  const line = coordinatedRunShown(c) ? definitionRunLineHtml(c.coordinated_row, { stopped: !!c._stopped }) : '';
  return `${line}${definitionRunsListHtml(c)}`;
}

export function surveyRowHtml(c) {
  const steps = (c.steps || []).length || c.step_count || 0;
  const produces = producesTypes(c);
  // The text column asks for a 24rem basis (flex: 1 1 24rem), not 0: with a zero basis it was never wrapped onto its
  // own line, it was squeezed to a sliver at ~800 px (one word per line, the title over "6 steps"). Now the
  // step count, last run and button drop to the next line once the pane is too narrow for the text beside them.
  return `<div data-def-row="${esc(c.qualified_name || '')}" class="flex flex-wrap items-baseline gap-s3 border-b border-rule py-s2">
    <div data-def-text class="min-w-0 flex-1" style="flex:1 1 24rem">
      <div class="text-answer text-ink">${esc(c.display_name || c.qualified_name)}</div>
      ${c.description ? `<div class="text-caveat text-ink-muted">${esc(onePurpose(c.description))}</div>` : ''}
      <div class="mt-[2px] font-mono text-provenance text-ink-muted">${esc(c.qualified_name || '')}${
        c.description ? ` · <button type="button" data-defhist="${esc(c.qualified_name)}"
          class="cursor-pointer bg-transparent underline">definition history</button>` : ''}</div>
      <div class="mt-[2px] text-provenance text-ink-muted">produces · ${
        produces.length ? `<span class="font-mono">${produces.map((t) => esc(t)).join(', ')}</span>` : 'nothing declared'}</div>
      ${engineNoteHtml(c)}
      ${definitionRunBlockHtml(c)}
    </div>
    <div class="tnum shrink-0 text-caveat text-ink-muted">${steps} step${steps === 1 ? '' : 's'}${
      // Point 2 (SPEC-THE-STAGE-PAGE.md): the axis tiering itself turns on --
      // "4 steps · 2 fetch" beside "7 steps · none fetch" reads as peers with
      // one that fetches twice and one that doesn't. `fetch_steps` is not on
      // every definition row yet (re/stage-page-backend); say nothing rather
      // than a false zero until it is.
      c.fetch_steps == null ? '' : ` · ${c.fetch_steps ? `<span class="tnum">${c.fetch_steps}</span> fetch` : 'none fetch'}`}${definitionRunsHtml(c)}</div>
    ${coordinatedRunShown(c) ? '' : `<div class="tnum shrink-0 text-caveat">${lastRunHtml(c)}</div>`}
    ${catalogRetryHtml(c)}
    ${selectedCredentialUnreadable()
      ? `<button data-run-survey="${esc(c.qualified_name || c.guid)}" disabled title="${CREDENTIAL_UNREADABLE_TEXT}"
      class="shrink-0 cursor-not-allowed rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink-muted"
      >${DEFINITION_RUN_WORD(c)}</button>`
      : `<button data-run-survey="${esc(c.qualified_name || c.guid)}"
      class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
      >${DEFINITION_RUN_WORD(c)}</button>`}
    ${coordinateControlHtml(c)}
  </div>`;
}

// Human-readable labels for `NativeProcess.kind` (technology_type_processes.py
// / configdata/technology_type_processes.yaml). Raw enum values rendered
// directly -- "(survey_existing)" -- meant nothing to a reader who hasn't read
// that config file (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §5). A
// fallback keeps an unmapped or future kind from disappearing rather than
// crashing the render.
const NATIVE_PROCESS_KIND_LABELS = {
  survey_existing: 'surveys an existing Egeria entry',
  catalog_and_survey: 'publishes, then surveys',
  delete: 'deletes an Egeria entry',
};
function nativeProcessKindLabel(kind) {
  return NATIVE_PROCESS_KIND_LABELS[kind] || `Egeria process kind: ${kind}`;
}

/** Renders `egeria_native_processes` -- real, Egeria-native survey/governance
 *  processes for this technology type that have no RE-authored Survey
 *  Definition candidate (that's `candidates`, a separate list). Ported from
 *  classic's `nativeProcessesHtml` (index.html) into /next's own visual
 *  idiom. Informational only: no "run" affordance, even for `survey_existing`
 *  processes -- wiring one of these to run from this pane is a separate,
 *  already-flagged follow-up (Backlog.md, #244), not part of this fix.
 *
 *  Three copy/visual fixes per REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-
 *  LANGUAGE.md §5, all inherited from the classic port: (1) the house caps
 *  style is for short labels, not a whole sentence, and the parenthetical
 *  carrying the fact that matters most here (these can't run from this pane)
 *  read worst in caps -- split into a caps label and a normal-case caveat
 *  below it; (2) `display_name` no longer renders in `text-accent-ink`, the
 *  same "click me" colour as the *Run →* buttons on candidate rows right
 *  above it, for a name that isn't runnable; (3) raw enum `kind` values are
 *  mapped to plain language via `nativeProcessKindLabel`. */
function nativeProcessesSectionHtml(nativeProcesses) {
  nativeProcesses = nativeProcesses || [];
  if (!nativeProcesses.length) return '';
  return `<div class="mt-s3 text-caveat text-ink-muted">
    <div class="text-caps uppercase tracking-caps text-ink-muted">Also known to Egeria</div>
    <div class="text-ink-muted">Not runnable from here yet — listed so you know they exist.</div>
    ${nativeProcesses.map((p) => `
      <div class="mt-s1 border-l border-rule pl-s2">
        <span class="font-mono text-ink">${esc(p.display_name)}</span>
        <span class="text-ink-muted">(${esc(nativeProcessKindLabel(p.kind))})</span>
        ${p.description ? `<div class="text-ink-muted">${esc(p.description)}</div>` : ''}
      </div>`).join('')}
  </div>`;
}

/* ── Enrichment: Context, and the "Survey & analyses" map ─────────────────
 *
 * ENRICHMENT-E1-CONTEXT-TAB (2026-09-29). Context is its own pane (not a
 * Questions-engine variant): it fetches the checklist itself so
 * `stages/context.js`'s `renderContext` can filter to `kind === 'human'`
 * rows for its own "What only you can answer" section, then hands off.
 *
 * Survey & analyses and By analysis on Enrichment are NOT the generic
 * per-stage panes every other stage uses — the designer's reply (§1,
 * corrected by the project owner's amendment the same day) makes this
 * stage's version of both tabs say something different: which
 * human-input-gated analyses this stage's own inputs unlock, and their
 * live state, rather than a list of survey definitions or a per-intent
 * results board.
 */
/** The reads a repository's resource header waits on: the scouting overview (homepage, last published, the
 *  stale-link flag) and the scouting signal chips. Fired once per selection; the header redraws in place as they land
 *  and is fine without them. Shared by the Questions frame and the Curate tabs. */
function primeRepoHeaderReads(slug) {
  // The overview carries `homepage`, `last_published_at` and the stale-link
  // flag, none of which are on the summary row. Fetched per selection, and
  // its absence is survivable — the header renders without it.
  // `/api/projects/{slug}/scouting-overview` is a repo-only endpoint (docs/
  // Backlog.md, "/next probes scouting-overview for database slugs and
  // always gets a 404") -- same class of bug as the earlier 'db'/'database'
  // resourceType mismatch: gate the call so it is never issued for a
  // resource type it doesn't apply to.
  // The stale-link read Admin → Egeria Links uses, for ANY resource type: the banner with the three repairs shows
  // only when it lists this resource. Failing to read it shows no banner (never a guessed one).
  if (state.staleLink?.slug !== slug) {
    state.staleLink = null;
    listStaleLinkages()
      .then((rows) => {
        if (state.selectedSlug !== slug) return;
        const et = apiEntityType(state.resourceType);
        state.staleLink = { slug, row: (rows || []).find((r) => r.entity_slug === slug && r.entity_type === et) || null };
        const host = $('resource-header');
        if (host && state.staleLink.row) { host.innerHTML = resourceHeaderHtml(slug); bindResourceHeader(); }
      })
      .catch(() => { /* no banner without the read */ });
  }
  if (state.resourceType === 'repo' && state.overview?.slug !== slug) {
    state.overview = null;
    getScoutingOverview(slug)
      .then((ov) => {
        if (state.selectedSlug !== ov.slug) return;
        state.overview = ov;
        // Replace the header IN PLACE, by its own id, so the rows already on
        // screen stay put. An earlier version matched the header by its class
        // list and inserted a second copy — the resource name rendered twice.
        const host = $('resource-header');
        if (host) {
          host.innerHTML = resourceHeaderHtml(slug);
          bindResourceHeader();
        }
      })
      .catch(() => { /* the header is fine without it */ });
    // PI-051: the scouting signal chips ride on the same selection; "could not read" is kept distinct from "none yet".
    state.scoutingSignal = null;
    getSurveySummary(slug, 'scouting')
      .then((r) => { state.scoutingSignal = { slug, tiles: r.tiles || [] }; })
      .catch((err) => { state.scoutingSignal = { slug, error: err && err.message ? err.message : 'unknown error' }; })
      .finally(() => {
        const host = $('resource-header');
        if (host && state.selectedSlug === slug) { host.innerHTML = resourceHeaderHtml(slug); bindResourceHeader(); }
      });
  }

}

/** The leading tabs' pane. Curate on a repository or a database draws its three panels (stages/curate.js `renderCurate` fills
 *  them); Understanding's tabs are still a stub (S4). The rail sits in its own host so a tab click can redraw
 *  it in place, and the resource header reads what it always read. */
async function loadStageTabPane() {
  const el = $('content');
  const slug = state.selectedSlug;
  if (state.stage === 'curate' && slug && (state.resourceType === 'repo' || state.resourceType === 'db')) {
    primeRepoHeaderReads(slug);
    el.innerHTML = `<div data-rail-host>${subTabsHtml()}</div>
      <div id="resource-header">${resourceHeaderHtml(slug)}</div>
      <div class="my-s3 h-px bg-rule"></div>
      <div id="curate-host"></div>`;
    bindSubTabs();
    bindResourceHeader();
    renderCurate(slug).catch((err) => {
      if (slug !== state.selectedSlug) return;
      const box = $('curate-host');
      if (box) box.innerHTML = `<div class="py-s3 text-answer text-accent-ink">Curate could not be drawn: ${esc(err.message)}</div>`;
    });
    return;
  }
  if (state.stage === 'understanding' && slug && (state.resourceType === 'repo' || state.resourceType === 'db')) {
    el.innerHTML = `<div data-rail-host>${subTabsHtml()}</div>
      <div id="resource-header">${resourceHeaderHtml(slug)}</div>
      <div class="my-s3 h-px bg-rule"></div>
      <div id="understanding-tabs-host"></div>`;
    bindSubTabs();
    bindResourceHeader();
    await renderUnderstandingTabs(slug, state.resourceType === 'db' ? 'database' : 'repo');
    return;
  }
  if (state.stage === 'understanding' && !slug) {
    el.innerHTML = `${subTabsHtml()}${paneMessage('Select a resource', 'Pick a resource from the sidebar to see what it holds and how it has changed.')}`;
    bindSubTabs();
    return;
  }
  el.innerHTML = `${subTabsHtml()}
    ${slug ? `<div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>` : ''}
    <div data-stage-tab-panel="${esc(state.subTab)}" class="text-caveat text-ink-muted">not built yet</div>`;
  bindSubTabs();
  if (slug) bindResourceHeader();
}

async function loadContextPane() {
  const el = $('content');
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;

  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="context-form"></div>`;
  bindSubTabs();
  bindResourceHeader();

  let checklist;
  try {
    checklist = await getQuestions(slug, {
      phase: 'enrichment',
      perspectives: [...state.activePerspectives],
      purposes: currentPurposes(),
      entityType: apiEntityType(state.resourceType),
    });
  } catch (err) {
    $('context-form').innerHTML = `<div class="py-s3 text-answer text-accent-ink">
      The checklist could not be loaded: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug || state.subTab !== 'context') return;   // a faster click won
  state.questions = checklist.questions || [];
  await renderContext(slug);
}

/** In words: what unlocks an Enrichment-tier analysis, from its catalog
 *  `requires_input` value — the same vocabulary `analysis_catalog.yaml`'s
 *  own comments use, said as a sentence rather than a snake_case token. */
export function requiresInputInWords(requiresInput) {
  const WORDS = {
    lens: 'a declared lens',
    documentation_source: 'a declared, reachable documentation source',
    ingested_documentation: 'ingested documentation',
    confirmed_glossary_term: 'a confirmed glossary term',
  };
  return WORDS[requiresInput] || (requiresInput ? requiresInput.replace(/_/g, ' ') : 'an unnamed human input');
}

/** locked | unrun (unlocked, Run available) | measured — the existing
 *  glyph vocabulary (glyphs.js's STATES, via `stateEntry`), not a new one,
 *  per the brief's own instruction to reuse "the existing analysis-card
 *  state rendering this codebase already has." `no-surveyor` (◌) is the
 *  closest existing meaning to "cannot be answered here yet" for locked;
 *  `unrun` (○) already means "a surveyor exists, nothing has run" and
 *  reads correctly for "unlocked, Run available". */
export function enrichmentAnalysisCardStateKey(row) {
  if (row.state === 'measured') return 'measured';
  // Unlocked, but no runner is mapped for it (`runnable === false`, computed
  // server-side from the run maps): nothing can answer it yet, so ◌ -- not
  // ○ "not run", which promises a runner exists.
  if (row.unlocked && row.runnable === false) return 'no-surveyor';
  if (row.unlocked) return 'unrun';
  return 'no-surveyor';
}

export function enrichmentAnalysisRowHtml(row) {
  const key = enrichmentAnalysisCardStateKey(row);
  const g = stateEntry(key);
  const runBtn = row.unlocked && row.runnable !== false && row.state !== 'measured'
    ? `<button type="button" data-run-enrichment-analysis="${esc(row.id)}" ${selectedCredentialUnreadable() ? `disabled title="${CREDENTIAL_UNREADABLE_TEXT}"` : ''}
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Run</button>`
    : '';
  return `<div class="border-b border-rule py-s2">
    <div class="flex items-baseline gap-s2 text-answer text-ink">
      <span class="${g.tone}">${g.glyph}</span>
      <span class="min-w-0 flex-1 font-mono">${esc(row.name || row.id)}</span>
      ${runBtn}
    </div>
    <div class="pl-[20px] text-provenance text-ink-muted">
      unlocked by: ${esc(requiresInputInWords(row.requires_input))} ·
      ${row.unlocked ? esc(row.reason) : `<span class="text-state-warn">${esc(row.reason)}</span>`}
      ${row.unlocked && row.runnable === false
        ? `<div class="text-ink-muted">unlocked · ${row.id === 'doc_source_ingestion' ? 'ingestion' : 'this analysis'} not built yet</div>` : ''}
    </div>
  </div>`;
}

/** Egeria-native survey rows for the resource in view: `{rows}` on success,
 *  `{rows: null}` when this kind has none (not applicable), `{failed: true}`
 *  when the read failed -- which the caller must DRAW, not drop. SHARED by every
 *  stage's Survey & analyses render (loadSurveyPane and, on Enrichment,
 *  loadEnrichmentAnalysesMapPane): the section lives on that tab on every
 *  stage, so no stage-special render path may skip it. */
async function fetchNativeSurveyRows(slug) {
  const entityType = apiEntityType(state.resourceType);
  if (entityType !== 'database' && entityType !== 'filesystem') return { rows: null };
  try {
    return { rows: (await getNativeSurveys(slug, { entityType })).surveys };
  } catch { return { rows: null, failed: true }; }
}

/** The section's markup for a fetchNativeSurveyRows result, or '' when the kind
 *  has no native surveys or has none to show. */
function nativeSurveysPaneHtml(res) {
  if (res.failed) return nativeSurveysUnreadableHtml();
  return res.rows && res.rows.length ? nativeSurveysSectionHtml(res.rows) : '';
}

/** Wire a rendered section (rows or the unreadable line). `reload` re-runs the pane. */
function bindNativeSurveysPane(root, slug, res, reload) {
  root.querySelector('[data-native-recheck]')?.addEventListener('click', () => reload());
  if (res.rows && res.rows.length) {
    // The definition cards follow the same rows: while one's run is in flight its run line is repainted in place from
    // each read, and when it ends the pane reloads once so the card, its controls and its history agree.
    let wasLive = res.rows.some((r) => r.kind === 're_definition' && r.in_flight);
    bindNativeSurveys(root, slug, res.rows, {
      onRows: (rows, { stopped }) => {
        for (const r of rows.filter((x) => x.kind === 're_definition')) {
          const slot = [...root.querySelectorAll('[data-def-run-line]')].find((e) => e.dataset.defRunLine === r.qualified_name);
          if (!slot) continue;
          slot.outerHTML = definitionRunLineHtml(r, { stopped });
        }
        const card = root.querySelector('[data-def-run-line]');
        if (card) bindRunLineControls(root, slug, { checkNow: async () => { await loadSurveyPane(); } });
        const live = rows.some((r) => r.kind === 're_definition' && r.in_flight);
        if (wasLive && !live) { wasLive = false; if (slug === state.selectedSlug) loadSurveyPane(); }
        wasLive = wasLive || live;
      },
    });
  }
}

/** Survey & analyses, on Enrichment: the amendment's map. */
async function loadEnrichmentAnalysesMapPane() {
  const el = $('content');
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="enrichment-analyses-map" class="text-caveat text-ink-muted">Reading what's unlocked…</div>
    <div id="enrichment-native-surveys"></div>`;
  bindSubTabs();
  bindResourceHeader();

  let rows = [];
  try {
    const res = await getEnrichmentAnalysesMap(apiEntityType(state.resourceType), slug);
    rows = res.analyses || [];
  } catch (err) {
    $('enrichment-analyses-map').innerHTML = `<div class="text-answer text-accent-ink">
      The map could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug || state.subTab !== 'survey') return;

  // Egeria's own surveys are surveys: the section renders here too, beneath
  // the map. A failed read is drawn (heading + "couldn't read"), and the map
  // above it is untouched.
  const nativeRes = await fetchNativeSurveyRows(slug);
  if (slug !== state.selectedSlug || state.subTab !== 'survey') return;

  const host = $('enrichment-analyses-map');
  host.innerHTML = `
    <p class="mb-s3 max-w-[70ch] text-answer text-ink">No analysis runs at the Enrichment stage from a
      survey read. What you supply here unlocks:</p>
    ${rows.length ? rows.map(enrichmentAnalysisRowHtml).join('')
      : `<div class="text-caveat text-ink-muted">No analyses declare an Enrichment-tier prerequisite for
          ${esc(state.resourceType === 'db' ? 'databases' : state.resourceType)} yet.</div>`}`;

  const nativeHost = $('enrichment-native-surveys');
  if (nativeHost) {
    nativeHost.innerHTML = nativeSurveysPaneHtml(nativeRes);
    bindNativeSurveysPane(nativeHost, slug, nativeRes, () => loadEnrichmentAnalysesMapPane());
  }

  host.querySelectorAll('[data-run-enrichment-analysis]').forEach((b) => b.addEventListener('click', async () => {
    const analysisId = b.dataset.runEnrichmentAnalysis;
    const label = b.textContent;
    b.disabled = true; b.textContent = 'running…';
    try {
      const started = await runAnalysis(slug, analysisId, apiEntityType(state.resourceType));
      if (isFreshnessSkip(started)) {
        b.disabled = false; b.textContent = UP_TO_DATE_WORD;
        setTimeout(() => { b.textContent = label; }, 4000);
        offerRunAnyway(started, { slug, analysisId, onForce: async () => {
          await runAnalysis(slug, analysisId, apiEntityType(state.resourceType), { force: true });
          await loadEnrichmentAnalysesMapPane();
        } });
        return;
      }
      await loadEnrichmentAnalysesMapPane();
    } catch (err) {
      b.disabled = false;
      // A stub id (doc_source_ingestion, doc_evidence_check,
      // semantic_suggestions) has no runner behind it yet — see
      // analysis_catalog.yaml's own comment on those three entries. This is
      // the honest degradation for that expected case, not a bug.
      b.textContent = `not built yet: ${err.message}`;
      setTimeout(() => { b.textContent = label; b.disabled = false; }, 4000);
    }
  }));
}

/** By analysis, on Enrichment: one summary line, per the reply §1. */
async function loadEnrichmentByAnalysisSummaryPane() {
  const el = $('content');
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="enrichment-by-analysis-summary" class="text-caveat text-ink-muted">Reading…</div>`;
  bindSubTabs();
  bindResourceHeader();

  let rows = [];
  try {
    const res = await getEnrichmentAnalysesMap(apiEntityType(state.resourceType), slug);
    rows = res.analyses || [];
  } catch { rows = []; }
  if (slug !== state.selectedSlug || state.subTab !== 'by_analysis') return;

  const unlocked = rows.filter((r) => r.unlocked).length;
  const host = $('enrichment-by-analysis-summary');
  host.innerHTML = `<p class="max-w-[70ch] text-answer text-ink">
    No analysis runs at the Enrichment stage from a survey read; what you supply here unlocks
    <span class="tnum">${unlocked}</span> of <span class="tnum">${rows.length}</span> analyses today —
    <button type="button" data-goto-enrichment-survey class="cursor-pointer bg-transparent p-0 text-accent-ink underline">see Survey & analyses ›</button></p>`;
  host.querySelector('[data-goto-enrichment-survey]')?.addEventListener('click', () => {
    state.subTab = 'survey';
    writeUrl();
    loadPane();
  });
}

/** Why the survey list is not narrowed to the stage, in short words, from the route's own `scoping_reason`.
 *  `no_questions`: the question catalog has none for this kind of resource at this stage (nothing to retry).
 *  `none_resolved`: questions exist, no published survey is scoped to them (a retry can change that).
 *  No reason (an older server): the old words, which claimed more than the response shows. */
export function scopeFallbackHtml(data, stage) {
  const retry = `<button type="button" data-act="rescope" class="cursor-pointer bg-transparent underline">retry</button>`;
  const kind = esc(apiEntityType(state.resourceType) || 'this kind');
  if (data.scoping_reason === 'no_questions') {
    return `<span data-scope-reason="no_questions">all stages — no ${esc(stage)} questions for a ${kind} yet</span>`;
  }
  if (data.scoping_reason === 'none_resolved') {
    return `<span data-scope-reason="none_resolved">all stages — no published survey is scoped to ${esc(stage)} · ${retry}</span>`;
  }
  return `all tiers — stage filter unavailable · ${retry}`;
}

/** Survey definition runs still going (per the persisted activity row) are followed to their end, then the pane is
 *  re-read once. One follower per activity id; a launch from this page reloads the pane itself and is skipped. */
const _runFollowers = new Map();
const _launchOwned = new Set();
function followRunningDefinitions(all, slug) {
  for (const c of all) {
    const id = c.last_run_activity_id;
    if (!id || !RUN_LIVE.has(String(c.last_run_status || '').toLowerCase()) || _runFollowers.has(id)) continue;
    _runFollowers.set(id, followUntil({
      read: () => getActivityEntry(id),
      isDone: (e) => !RUN_LIVE.has(String(e?.status || '').toLowerCase()),
      alive: () => slug === state.selectedSlug && state.subTab === 'survey',
      onUpdate: (e) => {
        if (RUN_LIVE.has(String(e?.status || '').toLowerCase())) return;
        _runFollowers.delete(id);
        if (!_launchOwned.has(id) && slug === state.selectedSlug && state.subTab === 'survey') loadSurveyPane();
      },
    }));
  }
}

export async function loadSurveyPane() {
  const el = $('content');
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  el.innerHTML = subTabsHtml() + '<div class="text-caveat text-ink-muted">Reading the survey catalog…</div>';
  bindSubTabs();

  let data;
  try {
    data = await getSurveyCandidates(slug, { entityType: apiEntityType(state.resourceType), phase: state.stage });
  } catch (err) {
    el.innerHTML = subTabsHtml() + paneMessage('The survey catalog could not be read',
      `${err.message}. This is a fact about the request, not about ${slug} — nothing
       here says the repo has no surveys.`);
    bindSubTabs();
    return;
  }
  const all = data.candidates || [];
  const stage = data.phase || state.stage;

  // Point 2: step_count/fetch_steps live on the catalog-wide definitions
  // list, not the candidates row -- joined here by qualified_name. Cached
  // (listSurveyDefinitions), and its own failure must not blank the pane
  // that already has real candidates to show; the fetch clause just stays
  // off the row, same as when the field is absent for any other reason.
  try {
    const defs = await listSurveyDefinitions();
    const byName = new Map(defs.map((d) => [d.qualified_name, d]));
    for (const c of all) {
      const d = byName.get(c.qualified_name);
      if (d) { c.step_count = d.step_count; c.fetch_steps = d.fetch_steps; c.unregistered_steps = d.unregistered_steps; }
    }
  } catch { /* the row still has everything the candidates call gave it */ }

  // THE TIER IS ON THE ROW, so an unscoped list stops being a problem worth a
  // paragraph. The four-line cold-server warning becomes a chip that says
  // which scope you are looking at, with a retry.
  // Informational only, matching classic's index.html: Egeria knows real,
  // runnable-elsewhere processes for this technology that have no RE-authored
  // Survey Definition candidate here. That is a separate fact from
  // `candidates` (RE-authored definitions) and is shown regardless of whether
  // `candidates` is empty -- not a fallback for the empty state.
  //
  // For a database or filesystem, the Egeria-native surveys are RUNNABLE rows
  // (BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md): what RE can run, why it cannot run
  // the rest, and the state derived from persisted proof. If that read fails
  // the pane falls back to the informational block, so a failure of the new
  // read never blanks the list Egeria already reported.
  const nativeRes = await fetchNativeSurveyRows(slug);
  // Each definition card takes its run line from the server's row for that definition (the latest run, whoever
  // coordinated it); nothing about the run is re-derived here.
  const defRows = new Map((nativeRes.rows || []).filter((r) => r.kind === 're_definition').map((r) => [r.qualified_name, r]));
  for (const c of all) c.coordinated_row = defRows.get(c.qualified_name) || null;
  // A failed read shows the unreadable line AND keeps the informational block
  // Egeria already reported, so the new read's failure blanks nothing.
  const nativeProcessesHtml = nativeRes.failed
    ? nativeSurveysUnreadableHtml() + nativeProcessesSectionHtml(data.egeria_native_processes)
    : ownSurveyRows(nativeRes.rows).length
      ? nativeSurveysSectionHtml(nativeRes.rows)
      : nativeProcessesSectionHtml(data.egeria_native_processes) + nativeSurveysSectionHtml(nativeRes.rows);

  const heavy = all.filter((c) => c.survey_kind === 'automate_full');
  const rest = all.filter((c) => c.survey_kind !== 'automate_full');
  const byTier = new Map();
  for (const c of rest) {
    const t = c.survey_kind || 'unclassified';
    byTier.set(t, [...(byTier.get(t) || []), c]);
  }
  const tierOrder = [...byTier.keys()].sort(
    (a, b) => (SURVEY_TIERS.indexOf(a) + 1 || 99) - (SURVEY_TIERS.indexOf(b) + 1 || 99));
  const here = tierOrder.filter((t) => t === stage);
  const elsewhere = tierOrder.filter((t) => t !== stage);
  const nElsewhere = elsewhere.reduce((n, t) => n + byTier.get(t).length, 0);

  el.innerHTML = subTabsHtml() + `
    <div class="mb-s3 flex flex-wrap items-baseline gap-s3">
      <span class="text-caps uppercase tracking-caps text-ink-muted">Survey definitions ·
        ${esc(data.technology_type || 'unknown technology type')}</span>
      <span class="ml-auto rounded-sm border ${
        data.scoping === 'full-scan' && all.length ? 'border-state-warn text-state-warn' : 'border-rule-strong text-ink-muted'}
        px-2 py-[1px] text-provenance">
        Scope: ${data.scoping === 'full-scan' && all.length ? scopeFallbackHtml(data, stage) : esc(stage)}</span>
    </div>

    ${nativeProcessesHtml}
    ${coordinateOffNoteHtml(all)}

    ${here.map((t) => `
      <div class="mt-s3 text-caps uppercase tracking-caps text-ink-muted">${esc(t)} ·
        <span class="tnum">${byTier.get(t).length}</span></div>
      ${byTier.get(t).map(surveyRowHtml).join('')}`).join('')}

    ${nElsewhere ? `
      <details class="mt-s3">
        <summary class="cursor-pointer text-caps uppercase tracking-caps text-ink-muted">
          Other stages · <span class="tnum">${nElsewhere}</span>
          <span class="normal-case tracking-normal">— ${esc(elsewhere.map(
            (t) => `${t} ${byTier.get(t).length}`).join(' · '))}</span>
        </summary>
        ${elsewhere.map((t) => `
          <div class="mt-s2 text-caps uppercase tracking-caps text-ink-muted">${esc(t)}</div>
          ${byTier.get(t).map(surveyRowHtml).join('')}`).join('')}
      </details>` : ''}

    ${heavy.map((c) => {
      // SET APART, GIVEN A PLAN VERB, AND NOT PLACED FIRST. Its own
      // description says it scales poorly by construction and is meant as a
      // scheduled choice; a row that argues against being clicked should not
      // be the most default-looking row on the pane.
      const steps = (c.steps || []).length;
      return `<div class="mt-s4 border border-rule bg-[rgba(32,31,29,.03)] p-s3">
        <div class="flex flex-wrap items-baseline gap-s2">
          <span class="text-answer text-ink">${esc(c.display_name)}</span>
          <span class="tnum rounded-sm border border-state-warn px-2 py-[1px] text-provenance text-state-warn"
            >${steps} steps · all tiers</span>
          <button data-plan-survey="${esc(c.qualified_name)}" ${selectedCredentialUnreadable() ? `disabled title="${CREDENTIAL_UNREADABLE_TEXT}"` : ''}
            class="ml-auto cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"
            >Plan a run…</button>
        </div>
        <div class="mt-s1 max-w-[60ch] text-caveat text-ink-muted">${esc(onePurpose(c.description))}</div>
      </div>`;
    }).join('')}

    ${!all.length ? paneMessage(
        `No Survey Definitions have been authored for ${data.technology_type || 'this technology'} yet`,
        'The analyses below still run individually; a Survey Definition only bundles them into an '
        + 'Egeria-launchable process.') : ''}
    <div id="survey-note" class="mt-s3 text-caveat text-ink">${launchNoteHtml(slug)}</div>

    <div class="mt-s5 border-t border-rule-strong pt-s3" id="analyses-index-section">
      <div class="text-caveat text-ink-muted">Reading the analyses…</div>
    </div>`;
  bindSubTabs();

  bindNativeSurveysPane(el, slug, nativeRes, () => loadSurveyPane());
  followRunningDefinitions(all, slug);
  el.querySelector('[data-act="rescope"]')?.addEventListener('click', () => loadSurveyPane());
  el.querySelectorAll('[data-defhist]').forEach((b) => b.addEventListener('click', () => {
    const c = all.find((x) => x.qualified_name === b.dataset.defhist);
    const d = openDialog(c.display_name || c.qualified_name, c.qualified_name);
    d.querySelector('#wl-detail-body').innerHTML = `
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Definition history</div>
      <p class="max-w-[70ch] whitespace-pre-line">${esc(c.description || '')}</p>`;
  }));
  el.querySelectorAll('[data-run-errors]').forEach((b) => b.addEventListener('click', () => {
    const c = all.find((x) => x.qualified_name === b.dataset.runErrors);
    if (!c) return;
    const d = openDialog(c.display_name || c.qualified_name, c.qualified_name);
    const errors = c.last_run_errors || [];
    d.querySelector('#wl-detail-body').innerHTML = `
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Last run — ${
        esc(c.last_run_status || 'error')}${c.last_run_at ? ` · ${esc(ago(c.last_run_at))}` : ''}</div>
      ${errors.length
        ? `<ul class="max-w-[70ch] list-disc pl-s4">${errors
            .map((e) => `<li class="whitespace-pre-line text-state-warn">${esc(String(e))}</li>`).join('')}</ul>`
        : '<p class="max-w-[70ch] text-ink-muted">No step-level error detail was recorded for this run.</p>'}`;
  }));
  bindSurveyRowActions(el, all, slug);

  renderAnalysesIndexSection(slug, stage);
}

/* ── Survey & analyses: the analyses half (SPEC-THE-STAGE-PAGE.md, points
 * 1-3, "AnalysesIndex") ────────────────────────────────────────────────────
 *
 * The definitions above answer "what can I run"; this answers "what has
 * this repo's catalog already got, and is it worth pressing". One call
 * (getAnalysesIndex) carries the row AND its popover -- catalog is the
 * entry's own to_dict, verbatim, so a description popover never needs a
 * second fetch.
 */
const ANALYSES_SORT_KEY = 're-next.analysesIndexSort';

function analysesIndexSort() {
  try {
    const v = localStorage.getItem(ANALYSES_SORT_KEY);
    return ['name', 'never_run', 'cost'].includes(v) ? v : 'name';
  } catch { return 'name'; }
}

/** State glyph from last_run_status/last_run_at -- the same vocabulary as
 *  factGlyph, not re-derived: `success` -> measured, an explicit failure ->
 *  error, nothing recorded -> unrun. */
export function analysisRowGlyph(row) {
  if (!row.last_run_at) return factGlyph('unrun');
  const s = String(row.last_run_status || '').toLowerCase();
  if (s === 'success' || s === 'ok' || s === '') return factGlyph('measured');
  if (s === 'failure' || s === 'error' || s === 'failed') return factGlyph('error');
  if (s === 'recorded') return factGlyph('unclassified');   // ran; outcome not on record
  return factGlyph('measured');
}

/** The compact two-number price this row wants -- "0.2s · 80s publish" or
 *  "declared fast" -- not the fuller sentence priceLineHtml renders for the
 *  run-choice popover, which is too much copy for a list row. */
function analysisRowPrice(cost) {
  if (!cost || cost.basis === 'unknown' || (cost.basis === 'measured' && !cost.runs)) return 'price not known';
  if (cost.basis === 'declared') return `declared ${esc(declaredWord(cost) || cost.sentence || '')}`;
  const bits = [fmtSeconds(cost.steps_seconds != null ? cost.steps_seconds : cost.seconds)];
  if (cost.publish_seconds) bits.push(`${fmtSeconds(cost.publish_seconds)} publish`);
  return bits.join(' · ');
}

// sub_resource_survey is the one analysis whose Run button, alone, was a
// dead end -- running it only produces a candidate list; deciding what to
// DO with that list (select, catalog, dispatch a scoped analysis) is real
// run-configuration with its own state, and that is what this toggle opens.
// RULING-SUBRESOURCES-PLACEMENT.md: attached to this row, not a fifth tab.
const SUBRES_ANALYSIS_ID = 'sub_resource_survey';

/** Which definitions run this analysis, and in which step (brief section 9). Words come from the
 *  server's `definitions` block, which derives them from each definition's `re_analysis_step`
 *  properties; nothing here keeps a list. Several definitions: "in N definitions" with the list one
 *  gesture away. */
export function analysisCoverageHtml(row) {
  const cov = row.definitions || {};
  const runs = cov.runs_in || [];
  const also = cov.also_in || [];
  const notIn = cov.not_in || [];
  const names = (xs) => xs.map((d) => esc(d.display_name)).join(', ');
  const bits = [];
  if (runs.length === 1) {
    bits.push(`in ${esc(runs[0].display_name)} · step <span class="font-mono">${esc(runs[0].step)}</span>`);
  } else if (runs.length > 1) {
    bits.push(`<details class="inline"><summary class="inline cursor-pointer underline">in <span class="tnum">${runs.length}</span> definitions</summary>
      <span class="block">${runs.map((d) => `${esc(d.display_name)} · step <span class="font-mono">${esc(d.step)}</span>`).join('<br>')}</span></details>`);
  } else if (notIn.length) {
    bits.push(`not part of ${names(notIn)} · runs on its own`);
  }
  if (also.length) {
    bits.push(`also runs when ${names(also)} runs (<span class="font-mono">${[...new Set(also.map((d) => d.step))].map(esc).join(', ')}</span>)`);
  }
  return bits.length ? `<span data-analysis-coverage class="text-ink-muted">${bits.join(' · ')}</span>` : '';
}

/** The credential word beside a database analysis row's button (brief section 8). Only said where it
 *  is TRUE: an analysis that opens a connection uses the credential saved for the database; a
 *  zero-fetch one (db_derived) reads stored rows and uses none. The sentence is one hover away. */
export const STORED_CREDENTIAL_SENTENCE =
  'runs in the background with the credential saved for this database; Re-run… runs it once, in this process, '
  + 'with another credential (nothing is stored and it is not retried)';
export function analysisCredentialHtml(row) {
  if (row.uses_credential === 'stored') {
    return `<span data-analysis-credential="stored" title="${esc(STORED_CREDENTIAL_SENTENCE)}" class="text-provenance text-ink-muted">uses stored credential</span>`;
  }
  if (row.uses_credential === 'none') {
    return `<span data-analysis-credential="none" title="This analysis reads rows already stored; it opens no connection." class="text-provenance text-ink-muted">needs no credential</span>`;
  }
  return '';
}

/** "ran as <user> (this run)" and the row's own statement that it was not retried: from the last
 *  run's activity row, never from the browser's memory of having sent a credential. */
export function analysisRanAsHtml(row) {
  const a = row.last_run_ran_as;
  if (!a || !a.user) return '';
  return `<span data-analysis-ran-as class="text-ink-muted">· ran as <span class="font-mono">${esc(a.user)}</span> (${esc(a.scope || 'this run')})${
    row.last_run_not_retried ? ` · ${esc(row.last_run_not_retried)}` : ''}</span>`;
}

export function analysisIndexRowHtml(row) {
  const failed = state.analysisRunFailures.get(`${state.selectedSlug}|${row.analysis_id}`);
  const g = analysisRowGlyph(row);
  const qn = (row.questions || []).length;
  const isSubRes = row.analysis_id === SUBRES_ANALYSIS_ID;
  return `<div class="flex flex-wrap items-baseline gap-s2 border-b border-rule py-s2">
    <span class="w-[16px] shrink-0 ${g.tone}">${g.glyph}</span>
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-baseline gap-s2">
        <span class="text-answer text-ink">${esc(row.name || row.analysis_id)}</span>
        <button type="button" data-analysis-popover="${esc(row.analysis_id)}"
          class="cursor-pointer bg-transparent p-0 text-caveat text-ink-muted underline">what it does</button>
        ${row.recommended ? `<span class="rounded-pill border border-accent px-2 py-[1px] text-provenance text-accent-ink">recommended</span>` : ''}
      </div>
      <div class="mt-[2px] text-provenance text-ink-muted">
        ${qn
          ? `<button type="button" data-analysis-questions="${esc(row.analysis_id)}"
               class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${qn} question${qn === 1 ? '' : 's'} ›</button>`
          : row.serves === 'chat-only' ? 'chat-only — no question asks' : 'nothing-yet — no question asks, no reader either'}
        · ${row.last_run_at ? `<span class="tnum">${esc(ago(row.last_run_at))}</span>${row.last_run_via ? ` · via ${esc(row.last_run_via.replace(/_/g, ' '))}` : ''}` : 'never run'}
        · ${analysisRowPrice(row.cost)}
        ${row.last_publish_proof ? `· ${publishProofHtml(row.last_publish_proof)}` : ''}
        ${analysisRanAsHtml(row)}
      </div>
      <div class="mt-[2px] text-provenance">${analysisCoverageHtml(row)}</div>
    </div>
    ${isSubRes ? `<button type="button" data-subres-toggle aria-expanded="false"
      class="shrink-0 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink-muted"
      >🗂 select &amp; publish</button>` : ''}
    ${analysisCredentialHtml(row)}
    <button data-analysis-run="${esc(row.analysis_id)}" ${row.runnable ? '' : 'disabled title="' + esc(row.runnable_reason) + '"'}
      class="shrink-0 cursor-pointer rounded-sm border ${row.runnable ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted'} bg-transparent px-2 py-[2px] text-caveat"
      >${row.last_run_at ? 'Re-run' : 'Run'} →</button>
    ${row.runnable && row.uses_credential === 'stored'
      ? `<button type="button" data-analysis-override="${esc(row.analysis_id)}"
      title="Opens a dialog: run this one analysis once with another credential"
      class="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-caveat text-ink-muted underline"
      >with options…</button>` : ''}
    <span data-analysis-run-error="${esc(row.analysis_id)}"
      class="hidden w-full text-provenance text-state-warn"></span>
    ${failed ? `<span data-analysis-run-failed="${esc(row.analysis_id)}" role="alert"
      class="w-full text-provenance text-state-warn">${esc(failed)}</span>` : ''}
  </div>
  ${isSubRes ? '<div id="subres-panel" class="hidden mb-s3 border-b border-rule pb-s3"></div>' : ''}`;
}

/** The description popover: the full prose PLUS the catalog facts named in
 *  the design (stage, declared run time, availability, perspectives) --
 *  `row.catalog` is the analysis catalog entry's own to_dict, so nothing
 *  here re-fetches to fill it. No ruleset-link field exists on the catalog
 *  entry today; shown only when one is actually present, never invented. */
function openAnalysisPopover(row) {
  const c = row.catalog || {};
  const d = openDialog(row.name || row.analysis_id, row.analysis_id);
  const facts = [
    ['stage', c.intent],
    ['declared run time', c.run_time],
    ['availability', c.availability],
    ['perspectives', (c.perspectives || []).join(', ') || 'none declared'],
    ['scope', c.target_shape],
  ].filter(([, v]) => v);
  d.querySelector('#wl-detail-body').innerHTML = `
    <p class="max-w-[70ch] whitespace-pre-line">${esc(row.description || row.short_description || '')}</p>
    <table class="mt-s3 w-full max-w-[50ch] border-collapse text-caveat">
      ${facts.map(([k, v]) => `<tr class="border-b border-rule">
        <td class="py-[4px] pr-s3 text-ink-muted">${esc(k)}</td>
        <td class="py-[4px] text-ink">${esc(String(v))}</td>
      </tr>`).join('')}
    </table>`;
}

function openAnalysisQuestionsPopover(row) {
  const d = openDialog(`Questions naming ${row.name || row.analysis_id}`, row.analysis_id);
  d.querySelector('#wl-detail-body').innerHTML = (row.questions || []).map((q) => `
    <div class="mb-s2 flex items-baseline gap-s2 border-b border-rule pb-s2">
      <span class="min-w-0 flex-1">${esc(q.question)}</span>
      <button type="button" data-goto-question="${esc(q.stage)}"
        class="shrink-0 cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(q.stage)} ›</button>
    </div>`).join('') || '<p>No question names this analysis.</p>';
  d.querySelectorAll('[data-goto-question]').forEach((b) => b.addEventListener('click', () => {
    state.stage = b.dataset.gotoQuestion;
    state.subTab = 'questions';
    closeCellDetail();
    writeUrl();
    renderIntentNav();
    loadPane();
  }));
}

async function renderAnalysesIndexSection(slug, stage) {
  const host = $('analyses-index-section');
  if (!host) return;
  let data;
  try {
    data = await getAnalysesIndex(slug, '', apiEntityType(state.resourceType));
  } catch (err) {
    if (slug === state.selectedSlug && state.subTab === 'survey') {
      host.innerHTML = `<span class="text-state-warn">The analyses could not be read: ${esc(err.message)}</span>`;
    }
    return;
  }
  if (slug !== state.selectedSlug || state.subTab !== 'survey') return;   // a faster click, or a different pane, won

  const rows = data.analyses || [];
  const sort = analysesIndexSort();
  const sorted = [...rows].sort((a, b) => {
    if (sort === 'never_run') return (b.last_run_at ? 0 : 1) - (a.last_run_at ? 0 : 1);
    if (sort === 'cost') return (a.cost?.seconds ?? Infinity) - (b.cost?.seconds ?? Infinity);
    return (a.name || a.analysis_id).localeCompare(b.name || b.analysis_id);
  });
  const here = sorted.filter((r) => r.tier === stage);
  const elsewhere = sorted.filter((r) => r.tier !== stage);

  host.innerHTML = `
    <div class="mb-s3 flex flex-wrap items-baseline gap-s3">
      <span class="text-caps uppercase tracking-caps text-ink-muted">Analyses ·
        <span class="tnum">${rows.length}</span> ·
        <span class="tnum">${data.counts?.never_run ?? 0}</span> never run ·
        <span class="tnum">${data.counts?.no_question ?? 0}</span> no question asks</span>
      <span class="ml-auto flex gap-[6px] text-caveat">
        ${[['name', 'by name'], ['never_run', 'never run first'], ['cost', 'by what it costs']].map(([k, label]) => `
          <button type="button" data-analyses-sort="${k}" aria-pressed="${sort === k}"
            class="wl-chartchip cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px]">${esc(label)}</button>`).join('')}
      </span>
    </div>
    ${here.map(analysisIndexRowHtml).join('') || `<p class="text-caveat text-ink-muted">No analyses run at this stage.</p>`}
    ${elsewhere.length ? `
      <details class="mt-s3">
        <summary class="cursor-pointer text-caps uppercase tracking-caps text-ink-muted">
          Other stages · <span class="tnum">${elsewhere.length}</span></summary>
        ${elsewhere.map(analysisIndexRowHtml).join('')}
      </details>` : ''}`;

  host.querySelectorAll('[data-analyses-sort]').forEach((b) => b.addEventListener('click', () => {
    try { localStorage.setItem(ANALYSES_SORT_KEY, b.dataset.analysesSort); } catch { /* per-viewer convenience only */ }
    renderAnalysesIndexSection(slug, stage);
  }));
  host.querySelectorAll('[data-analysis-popover]').forEach((b) => b.addEventListener('click', () => {
    openAnalysisPopover(rows.find((r) => r.analysis_id === b.dataset.analysisPopover));
  }));
  host.querySelectorAll('[data-analysis-questions]').forEach((b) => b.addEventListener('click', () => {
    openAnalysisQuestionsPopover(rows.find((r) => r.analysis_id === b.dataset.analysisQuestions));
  }));
  const subresToggle = host.querySelector('[data-subres-toggle]');
  const subresPanel = host.querySelector('#subres-panel');
  subresToggle?.addEventListener('click', async () => {
    const opening = subresPanel.classList.contains('hidden');
    subresPanel.classList.toggle('hidden', !opening);
    subresToggle.setAttribute('aria-expanded', String(opening));
    subresToggle.textContent = opening ? '🗂 select & publish ▲' : '🗂 select & publish';
    if (opening) await mountSubResourcePanel(slug, subresPanel);
  });
  const startRun = async (b, aid, credential = null, force = false) => {
    const errEl = host.querySelector(`[data-analysis-run-error="${CSS.escape(aid)}"]`);
    if (errEl) { errEl.classList.add('hidden'); errEl.textContent = ''; }
    b.disabled = true;
    const original = b.textContent;
    b.textContent = 'Queueing…';
    try {
      // A credential typed for this one run goes ONLY into this call's body (browser memory ->
      // request -> the server's in-process run); the plain call is unchanged.
      const started = credential
        ? await runAnalysisWithCredential(slug, aid, credential)
        : await runAnalysis(slug, aid, apiEntityType(state.resourceType), { force });
      if (isFreshnessSkip(started)) {
        // The server declined: the last run is still fresh. The button is usable again at once; the dialog says
        // "Already up to date" and offers the forced run.
        b.disabled = false;
        b.textContent = original;
        offerRunAnyway(started, { slug, analysisId: aid, onForce: () => startRun(b, aid, null, true) });
        return;
      }
      // Watch it rather than tell the user to reload — pollActivity is the
      // same mechanism the Questions checklist's run button already uses
      // (rerun(), above). A five-minute timeout still redraws the section
      // once so a slow run's real state (whatever it reaches) is on screen
      // instead of the stale pre-run row.
      b.textContent = 'Running…';
      const failKey = `${slug}|${aid}`;
      state.analysisRunFailures.delete(failKey);
      try {
        const finished = await pollActivity(started.activity_id, {
          onTick: (e) => {
            const s = (e?.status || '').toLowerCase();
            b.textContent = s === 'queued' || s === 'pending' ? 'Queued…' : 'Running…';
          },
        });
        // pollActivity resolves with the finished row whatever it ended as;
        // keep a failure on screen (the redraw below would otherwise show
        // only a glyph from last_run_status).
        const failure = activityFailure(finished);
        if (failure) state.analysisRunFailures.set(failKey, failureText(failure));
      } catch (err) {
        if (err.name !== 'PollTimeout') throw err;
        // Not a failure — this browser stopped watching, the run itself
        // has not failed (same distinction rerun() draws for questions).
      }
      if (slug === state.selectedSlug && state.subTab === 'survey') {
        await renderAnalysesIndexSection(slug, stage);
      }
    } catch (err) {
      b.disabled = false;
      b.textContent = original;
      const msg = err.status === 401 ? 'Sign in to run an analysis' : `Could not start: ${err.message}`;
      b.title = msg;
      // A tooltip alone is invisible unless the reader hovers -- reported as
      // "the button works but doesn't do anything", because Queueing… reverts
      // to run → the instant the request fails, with nothing else on screen.
      if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
    }
  };
  host.querySelectorAll('[data-analysis-run]').forEach((b) => b.addEventListener('click', () =>
    startRun(b, b.dataset.analysisRun)));
  host.querySelectorAll('[data-analysis-override]').forEach((b) => b.addEventListener('click', () => {
    const row = rows.find((r) => r.analysis_id === b.dataset.analysisOverride);
    if (row) openAnalysisOverrideDialog(row, slug, (credential) => {
      const runBtn = host.querySelector(`[data-analysis-run="${CSS.escape(row.analysis_id)}"]`);
      if (runBtn) startRun(runBtn, row.analysis_id, credential);
    });
  }));
}

/** "Re-run…" on a database analysis row (brief section 8, optional slice, owner-approved): the same
 *  credential dialog the definition run offers, for ONE analysis. The credential is held in browser
 *  memory (run-credential.js, per database), sent in the body of this one call, and the server runs
 *  the analysis in its own process: not queued, not stored, not retried. */
export function openAnalysisOverrideDialog(row, slug, onGo) {
  const el = openDialog(row.name || row.analysis_id, `${slug} · run once with another credential`);
  const body = el.querySelector('#wl-detail-body');
  body.innerHTML = `
    <p class="max-w-[70ch]">Runs <span class="font-mono">${esc(row.analysis_id)}</span> once, here, with the credential below
      instead of the one saved for this database. It is not queued and not retried, and nothing is stored.</p>
    <div class="mt-s2 flex flex-wrap items-center gap-s2">
      <input data-run-override-user type="text" autocomplete="off" placeholder="user"
        class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <input data-run-override-password type="password" autocomplete="off" placeholder="password"
        class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
    </div>
    <label class="mt-s1 flex items-baseline gap-s2 text-caveat text-ink-muted">
      <input type="checkbox" data-run-override-remember>
      <span>remember for this session</span>
    </label>
    <div class="text-caveat text-ink-muted">Used for this run only; never written to the registry. It is forgotten when this tab reloads.</div>
    <div data-run-override-error class="text-caveat text-state-warn"></div>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="go"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink">Run once</button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>`;
  // Values go in as PROPERTIES: a password is never an attribute or markup.
  const mem = rememberedCredential(slug);
  if (mem) {
    body.querySelector('[data-run-override-user]').value = mem.user;
    body.querySelector('[data-run-override-password]').value = mem.password;
    body.querySelector('[data-run-override-remember]').checked = true;
  }
  body.querySelector('[data-act="go"]').addEventListener('click', (ev) => {
    const go = ev.currentTarget;
    if (go.disabled) return;                       // a pressed control ignores a second press
    const user = body.querySelector('[data-run-override-user]').value.trim();
    const password = body.querySelector('[data-run-override-password]').value;
    if (!user || !password) {
      body.querySelector('[data-run-override-error]').textContent =
        'A credential for this run needs both a user and a password.';
      return;
    }
    setRemembered(slug, { user, password }, body.querySelector('[data-run-override-remember]').checked);
    go.disabled = true;
    go.textContent = 'Starting…';
    closeCellDetail();
    onGo({ user, password });
  });
  return el;
}

/** RUN GOES THROUGH THE SAME PREVIEW as the matrix's two plans.
 *
 * This is the third and most expensive of them, and if they answer "what am I
 * about to spend" in three shapes people learn to read one and skim the rest.
 */
export function planSurveyRun(c, slug) {
  if (!c) return;
  if (selectedCredentialUnreadable()) return;   // the buttons are disabled; this guards any other caller
  const steps = (c.steps || []).length;
  const el = openDialog(c.display_name || c.qualified_name, `${slug} · ${c.survey_kind || 'unclassified'}`);
  const body = el.querySelector('#wl-detail-body');
  const local = (c.steps_local || []).length;
  const native = (c.steps_native || []).length;
  body.innerHTML = `
    <p>This runs <span class="tnum">${steps}</span> step(s) against
      <span class="font-mono">${esc(slug)}</span>.</p>
    <ul class="ml-s3 mt-s2 list-disc">
      ${local ? `<li><span class="tnum">${local}</span> run here</li>` : ''}
      ${native ? `<li><span class="tnum">${native}</span> are coordinated by Egeria</li>` : ''}
      <li>${c.last_run_at
            ? `last ran ${esc(ago(c.last_run_at))}${
                c.last_run_status ? ` · ${esc(c.last_run_status)}` : ''} —
               <strong>everything it covers will be measured again</strong>`
            : 'it has never run on this resource'}</li>
      ${(c.analysis_ids || []).length
        ? `<li>writes: <span class="font-mono">${esc((c.analysis_ids || []).join(', '))}</span></li>` : ''}
      ${c.auto_publishes ? '<li>publishes its results to Egeria when it finishes</li>' : ''}
    </ul>
    <div id="plan-movement" class="mt-s2 text-caveat text-ink-muted"></div>
    ${state.resourceType === 'db' ? runOptionsHtml(slug) : ''}
    <p class="mt-s3 text-ink">Nothing runs until you confirm.</p>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="go"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
        >Run <span class="tnum">${steps}</span> step(s)</button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>`;
  const options = state.resourceType === 'db' ? bindRunOptions(body, slug) : null;
  body.querySelector('[data-act="go"]').addEventListener('click', (ev) => {
    const go = ev.currentTarget;
    if (go.disabled) return;                       // a pressed control ignores a second press
    const chosen = options ? options.read() : { credential: null, forceCustom: false };
    if (chosen.error) return;                      // the dialog said why; nothing is sent
    go.disabled = true;
    go.textContent = 'Starting…';
    closeCellDetail();
    launchSurvey(slug, c.qualified_name || c.guid, chosen);
  });

  // WHAT MOVED LAST TIME, HERE, WHERE IT CHANGES A DECISION.
  //
  // "Unchanged across N runs" reports a fact on a dashboard row; in a plan it
  // is an argument. A survey whose measurements have not moved across several
  // runs is a survey whose cadence is costing more than it returns, and this
  // is the moment that matters — before paying for it again rather than after.
  reportPlanMovement(slug, c);
}

/** Parity G2, PI-016/PI-018: what a database run can be told. Markup only; the
 *  credential fields are built on demand and never carry a value attribute. */
function runOptionsHtml(slug) {
  const cataloged = !!selectedProject()?.is_published;
  return `<div class="mt-s3 border-t border-rule pt-s2" data-run-options>
    <label class="flex items-baseline gap-s2 text-caveat ${cataloged ? 'text-ink' : 'text-ink-muted'}">
      <input type="checkbox" data-run-hybrid ${cataloged ? 'checked' : 'disabled'}>
      <span>Try Egeria first (hybrid)</span>
    </label>
    <div data-run-hybrid-note class="ml-s5 max-w-[70ch] text-caveat text-ink-muted"></div>
    <label class="mt-s2 flex items-baseline gap-s2 text-caveat text-ink">
      <input type="checkbox" data-run-override>
      <span>Use different credentials for this run</span>
    </label>
    <div data-run-override-fields class="ml-s5"></div>
  </div>`;
}

/** Wires the options and returns `read()`, the choice the Run press acts on:
 *  `{credential, forceCustom}` or `{error}` after saying why in the dialog. */
function bindRunOptions(body, slug) {
  const cataloged = !!selectedProject()?.is_published;
  const hybrid = body.querySelector('[data-run-hybrid]');
  const hybridNote = body.querySelector('[data-run-hybrid-note]');
  const override = body.querySelector('[data-run-override]');
  const fields = body.querySelector('[data-run-override-fields]');

  const sayHybrid = () => {
    hybridNote.textContent = !cataloged
      ? 'not published to Egeria · publish it on Curate first'
      : hybrid.checked
        ? 'Egeria is tried first. The run publishes to Egeria when it finishes (a SurveyReport is written to Egeria). Untick for a local-only run.'
        : 'Local only: nothing is asked of Egeria in the steps that would try it first.';
  };
  hybrid.addEventListener('change', sayHybrid);
  sayHybrid();

  const showFields = () => {
    if (!override.checked) { fields.innerHTML = ''; return; }
    fields.innerHTML = `
      <div class="mt-s1 flex flex-wrap items-center gap-s2">
        <input data-run-override-user type="text" autocomplete="off" placeholder="user"
          class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
        <input data-run-override-password type="password" autocomplete="off" placeholder="password"
          class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      </div>
      <label class="mt-s1 flex items-baseline gap-s2 text-caveat text-ink-muted">
        <input type="checkbox" data-run-override-remember>
        <span>remember for this session</span>
      </label>
      <div class="text-caveat text-ink-muted">Used for this run only; never written to the registry. It is forgotten when this tab reloads.</div>
      <div data-run-override-error class="text-caveat text-state-warn"></div>`;
    const mem = rememberedCredential(slug);
    if (mem) {
      // Values go in as PROPERTIES: a password is never an attribute or markup.
      fields.querySelector('[data-run-override-user]').value = mem.user;
      fields.querySelector('[data-run-override-password]').value = mem.password;
      fields.querySelector('[data-run-override-remember]').checked = true;
    }
  };
  override.addEventListener('change', showFields);
  if (rememberedCredential(slug)) { override.checked = true; showFields(); }

  return {
    read() {
      const forceCustom = !(cataloged && hybrid.checked);
      if (!override.checked) { setRemembered(slug, null, false); return { credential: null, forceCustom }; }
      const user = fields.querySelector('[data-run-override-user]').value.trim();
      const password = fields.querySelector('[data-run-override-password]').value;
      const keep = fields.querySelector('[data-run-override-remember]').checked;
      if (!user || !password) {
        fields.querySelector('[data-run-override-error]').textContent =
          'A credential for this run needs both a user and a password.';
        return { error: true };
      }
      const credential = { user, password };
      setRemembered(slug, credential, keep);
      return { credential, forceCustom };
    },
  };
}

async function reportPlanMovement(slug, c) {
  const ids = (c.analysis_ids || []).filter((a) => trendSupport(a) === 'tracked');
  if (!ids.length) return;
  const checked = ids.slice(0, 6);         // enough to characterise, not a survey of its own
  const deltas = await Promise.all(checked.map(async (a) => ({ a, text: await deltaFor(slug, a) })));
  const slot = document.getElementById('plan-movement');
  if (!slot) return;                        // the dialog was closed while we read
  const still = deltas.filter((d) => /^unchanged/.test(d.text));
  const moved = deltas.filter((d) => d.text && !/^unchanged|^first/.test(d.text));
  if (!still.length && !moved.length) return;
  slot.innerHTML = `
    ${still.length ? `<div class="text-accent-ink"><span class="tnum">${still.length}</span> of
      <span class="tnum">${checked.length}</span> tracked measurement(s) have not moved across
      their recorded runs — <span class="font-mono">${esc(still.map((d) => d.a).join(', '))}</span>.
      Running this again will re-measure them and, on this evidence, change nothing.</div>` : ''}
    ${moved.length ? `<div class="mt-[2px]"><span class="tnum">${moved.length}</span> did move
      last time: ${esc(moved.map((d) => `${d.a} ${d.text}`).join(' · '))}.</div>` : ''}`;
}

/** The launch note, drawn from state so a pane re-render cannot wipe it. */
export function launchNoteHtml(slug) {
  return state.launchNote && state.launchNote.slug === slug ? state.launchNote.html : '';
}
function setLaunchNote(slug, html) {
  state.launchNote = { slug, html };
  const note = $('survey-note');
  if (note) note.innerHTML = html;
}

/** The first sentence of a run's first failure, or '' when it recorded none.
 *  From the persisted entry (errors, then a failed step's detail, then the
 *  row's own reason), never from which branch the caller took. */
export function firstFailureSentence(entry) {
  let d = entry && entry.detail;
  if (typeof d === 'string') { try { d = JSON.parse(d); } catch { d = null; } }
  let msg = '';
  if (d && Array.isArray(d.errors) && d.errors.length) msg = String(d.errors[0]);
  if (!msg && d && Array.isArray(d.steps)) {
    const bad = d.steps.find((st) => st.status && st.status !== 'ok' && typeof st.detail === 'string');
    if (bad) msg = bad.detail;
  }
  if (!msg) { const f = activityFailure(entry); msg = f ? f.reason : ''; }
  msg = msg.replace(/\s+/g, ' ').trim();
  const m = msg.match(/^(.*?[.!?])(\s|$)/);
  return (m ? m[1] : msg).slice(0, 240);
}

export async function launchSurvey(slug, ref, { credential = null, forceCustom = false } = {}) {
  setLaunchNote(slug, `Launching <span class="font-mono">${esc(ref)}</span>…`);
  try {
    // The plain call is kept in its original shape (a source pin in
    // test_next_db_fs_gate_removal.py names it); the options call only differs
    // by the two things this run was told.
    const res = (credential || forceCustom)
      ? await runSurveyDefinition(slug, ref, { entityType: apiEntityType(state.resourceType), credential, forceCustom })
      : await runSurveyDefinition(slug, ref, { entityType: apiEntityType(state.resourceType) });
    // "ran as" is the SERVER's statement about the run, never this browser's
    // belief that it sent a credential.
    const ranAs = res && res.ran_as && res.ran_as.user
      ? ` · ran as <span class="font-mono">${esc(res.ran_as.user)}</span> (${esc(res.ran_as.scope || 'this run')}).` : '';
    let html = `Launched <span class="font-mono">${esc(ref)}</span>${ranAs || '.'}
      ${res && (res.guid || res.engine_action_guid)
        ? `Egeria action <span class="font-mono">${esc(res.guid || res.engine_action_guid)}</span>.` : ''}
      It runs asynchronously — its results appear in Dashboard and in the question rows
      as each step lands, not when this line changes.`;
    setLaunchNote(slug, html);
    // Found live, `adventureworks`, 2026-09-27: a Survey Definition run
    // that had genuinely completed (three survey rows written) still left
    // this pane reading "never run" — nothing here ever re-fetched. Watch
    // the run the same way the per-analysis run button already does
    // (pollActivity — see the `data-analysis-run` handler above), then
    // reload the whole Survey pane so its "last run" status, and
    // everything else `getSurveyCandidates` returns, reflects what
    // actually happened rather than the stale pre-run snapshot.
    if (res && res.activity_id) {
      _launchOwned.add(res.activity_id);
      // The row turns "running" now, from the persisted row, rather than staying as it was until the end.
      if (slug === state.selectedSlug && state.subTab === 'survey') loadSurveyPane();
      let finishedEntry = null;
      try {
        finishedEntry = await pollActivity(res.activity_id, {});
      } catch (err) {
        if (err.name !== 'PollTimeout') throw err;
        // Not a failure — this browser stopped watching; the run itself
        // may still be in flight. Reload anyway: it shows whatever state
        // the run has reached by now, which is still more current than a
        // note that never changes.
      }
      // Prefect dispatch honesty (2026-09-28,
      // PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md): a step
      // that silently fell back to local execution still reports
      // status "ok" (the work genuinely got done) — so it would otherwise
      // look identical to a step that was always local-by-design. Its
      // steps_report entry carries a "ran locally: Prefect dispatch
      // failed — <reason>" detail specifically to make that distinguishable
      // here, on the same line a person is already watching.
      if (finishedEntry) {
        let steps = [];
        try {
          const d = typeof finishedEntry.detail === 'string'
            ? JSON.parse(finishedEntry.detail) : (finishedEntry.detail || {});
          steps = d.steps || [];
        } catch (_) { /* a detail we cannot parse has nothing to report here */ }
        // Whole-definition engine choice (which engine actually ran this
        // run — Prefect, or a local fallback and why) is NOT appended here
        // any more (engine-note persistence, 2026-09-28,
        // ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md): it renders on the
        // definition's own row (surveyRowHtml -> engineNoteHtml), sourced
        // from the run detail persisted to the activity log, so it survives
        // the reload this function is about to trigger.
        const fellBack = steps.filter((s) =>
          typeof s.detail === 'string' && s.detail.startsWith('ran locally: Prefect dispatch failed'));
        if (fellBack.length) {
          html += `<div class="mt-s1 text-state-warn">${
            fellBack.map((s) => esc(`${String(s.step || '').split('::').pop()} — ${s.detail}`)).join('<br>')
          }</div>`;
        }
        // PI-020: a failed run shows its first sentence AT ONCE, unasked (cue
        // rule), on the note itself. The note is derived from state, so the
        // reload below cannot wipe it.
        const failed = activityFailure(finishedEntry) || (steps.some((s) => s.status === 'error' || s.status === 'failed')
          ? { reason: '' } : null);
        const sentence = failed ? firstFailureSentence(finishedEntry) : '';
        if (sentence) {
          html += `<div data-launch-failure class="mt-s1 text-state-warn"><span aria-hidden="true">${GLYPH_STATES.error.glyph}</span>
            failed · ${esc(sentence)}${/authenticat|FATAL|password|credential/i.test(sentence)
              ? ' <span class="text-ink-muted">Use different credentials for this run, or change the stored credential.</span>' : ''}</div>`;
        }
        setLaunchNote(slug, html);
      }
      _launchOwned.delete(res.activity_id);
      if (slug === state.selectedSlug && state.subTab === 'survey') {
        await loadSurveyPane();
      }
    }
  } catch (err) {
    // 401 is not a failure of the survey, it is a fact about this session.
    setLaunchNote(slug, err.status === 401
      ? `<span class="text-accent-ink">Not launched — running a survey is a write, and
         this session is not signed in. Sign in with an Egeria user id to launch it;
         everything else on this pane is readable without one.</span>`
      : `<span class="text-state-warn">It was not launched: ${esc(err.message)}</span>`);
  }
}

/* ── The shared measurement detail ────────────────────────────────────────
 *
 * THE MISSING MIDDLE. Survey says what you can run; Dashboard says what is
 * currently true; nothing said what a run FOUND, and no value opened into its
 * evidence or its history.
 *
 * Evidence was never missing — it was unrouted. The matrix cell popup has done
 * this job since the round that produced "the grid carries state, the popup
 * carries meaning"; the Dashboard simply did not call it. So this is one
 * component with several entry points: a dashboard finding, a count, and (via
 * the history section) the matrix's own cell popup. If they diverge, people
 * learn one and distrust the others.
 */

/** What the catalog says about an analysis: `tracked`, `not_tracked`, or
 *  `unknown` when it is not in the results map at all. */
function trendSupport(analysisId) {
  const entry = (state.analyses || []).find((a) => a.id === analysisId);
  return entry?.trend || 'unknown';
}

/** The recorded series for one analysis, oldest first.
 *
 * WITH ONE MEASUREMENT THERE IS NO TREND. It says "first measurement" rather
 * than drawing a flat line — a sparkline of a single point is the same lie as
 * a tick standing in for an unread cell.
 */
async function historyHtml(slug, analysisId, metric = '') {
  // ASK THE DESCRIPTOR, NOT THE ENDPOINT. A current-state classification
  // correctly keeps no series; that is a property of the analysis, so the UI
  // renders no history section at all rather than an empty one — and never
  // makes a request whose only possible answer is "no".
  //
  // This is what replaced a 400. The endpoint's message was a good sentence
  // in the wrong place: an error is not how a system reports that something
  // is working as designed.
  if (trendSupport(analysisId) === 'not_tracked') return '';

  let series;
  try {
    const res = await getAnalysisTrend(slug, analysisId, metric, apiEntityType(state.resourceType));
    series = (res.runs || res.series || []).filter((r) => r && r.surveyed_at);
  } catch (err) {
    // A 400 here is an ANSWER, not a failure: the endpoint says this analysis
    // keeps no trend because it is a current-state classification. Rendering
    // that as "could not be read" turns a fact about the analysis into a fault
    // in the reader — the same mistake as "no results" for "not looked at".
    if (err.status === 400) {
      return `<p class="text-caveat text-ink-muted">${esc(err.message)}</p>`;
    }
    return `<p class="text-caveat text-state-warn">The history could not be read:
      ${esc(err.message)}</p>`;
  }
  if (!series.length) return '<p class="text-caveat text-ink-muted">No recorded history.</p>';
  if (series.length === 1) {
    // "There WILL be a trend" is the whole difference from `not tracked`, and
    // it is the half a reader cannot infer from an empty list.
    return `<p class="text-caveat text-ink-muted">First measurement,
      ${esc(ago(series[0].surveyed_at))}. Tracked, but measured once — there will
      be a trend; there isn't one yet.</p>`;
  }
  series.sort((a, b) => String(a.surveyed_at).localeCompare(String(b.surveyed_at)));
  // A history is interesting exactly where it steps. Thirty rows of which
  // twenty-eight are identical HIDE the two that matter — the dependency
  // count went 57 to 68 over five weeks and a reader had to read thirty
  // figures to notice. So the transitions lead: each value the series ever
  // took, dated where it first appeared, and the count of runs and changes.
  // The full table stays one click on, for the reader who wants every run.
  const val = (r) => r.metric_value ?? r.value;
  const steps = [];
  for (const r of series) {
    if (!steps.length || steps[steps.length - 1].v !== val(r)) steps.push({ v: val(r), at: r.surveyed_at, m: r.metric });
  }
  const changes = steps.length - 1;
  const transitions = steps.map((st) => `<span class="tnum">${esc(fmtScalar(st.v, st.m || metric))}</span>
      <span class="text-ink-muted">${esc(String(st.at).slice(5, 10))}</span>`).join(' → ');
  return `
    <div class="mb-s1 text-caveat text-ink">${transitions}
      <span class="text-ink-muted">· <span class="tnum">${series.length}</span> runs, <span class="tnum">${changes}</span> change${changes === 1 ? '' : 's'}</span></div>
    <details><summary class="cursor-pointer text-caps uppercase tracking-caps text-ink-muted">Every run · oldest first ·
      <span class="tnum">${series.length}</span> recorded</summary>
    <table class="w-full border-collapse text-caveat">
      ${series.map((r, i) => `<tr class="border-b border-rule">
        <td class="tnum py-[4px] pr-s3 text-ink-muted">${esc(String(r.surveyed_at).slice(0, 10))}</td>
        <td class="tnum py-[4px] text-ink ${i === series.length - 1 ? 'font-semibold' : ''}">${
          esc(fmtScalar(r.metric_value ?? r.value, r.metric || metric))}</td>
      </tr>`).join('')}
    </table></details>`;
}

/** The inline delta beside a value: what it was, and when.
 *
 * That is the whole trend for most measurements most of the time — the series
 * itself lives in the detail.
 */
async function deltaFor(slug, analysisId, metric = '') {
  if (trendSupport(analysisId) === 'not_tracked') return '';
  try {
    const res = await getAnalysisTrend(slug, analysisId, metric, apiEntityType(state.resourceType));
    const series = (res.runs || res.series || []).filter((r) => r && r.surveyed_at);
    if (series.length < 2) return series.length === 1 ? 'first measurement' : '';
    series.sort((a, b) => String(a.surveyed_at).localeCompare(String(b.surveyed_at)));
    const now = series[series.length - 1];
    // The last value that DIFFERS, not simply the previous row: repeated runs
    // that changed nothing would otherwise report "was <the same> 2h ago",
    // which reads as movement where there was none.
    const prior = [...series].reverse().find(
      (r) => (r.metric_value ?? r.value) !== (now.metric_value ?? now.value));
    if (!prior) return `unchanged across ${series.length} runs`;
    // A row may name its own metric (the trend reader knows what `value`
    // is even when the caller does not); prefer that over the caller's.
    return `was ${fmtScalar(prior.metric_value ?? prior.value, prior.metric || metric)} ${ago(prior.surveyed_at)}`;
  } catch (_) {
    return '';
  }
}

/** One measurement, opened: what it says, its evidence, its history. */
/* ── What a measurement is entitled to claim ──────────────────────────────
 *
 * The catalog's Rationale/Source column carries, per question, what its
 * answer can and cannot mean: secret_scan never claims "no secrets", only no
 * matches against this ruleset in this snapshot; cve_scan sees declared
 * dependencies only, so a zero is "none found in what we can see".
 *
 * Those sentences have existed in the CSV since the catalog was authored and
 * have never reached a screen. They are the difference between a finding and
 * a claim, so they belong beside the value — as the second line of the
 * answer, not as help text.
 *
 * Keyed by analysis id because that is what a measurement knows about
 * itself. One analysis can answer several questions, so the value is a list
 * and duplicates are collapsed: two questions often share a rationale, and
 * printing it twice reads as two separate caveats.
 */
const RATIONALE_BY_ANALYSIS = new Map();

function rememberRationales(questions) {
  for (const q of questions || []) {
    const text = (q.rationale || '').trim();
    if (!text) continue;
    for (const id of q.analysis_ids || []) {
      const held = RATIONALE_BY_ANALYSIS.get(id) || [];
      if (!held.includes(text)) held.push(text);
      RATIONALE_BY_ANALYSIS.set(id, held);
    }
  }
}

/** The caveat block for one analysis, or '' when the catalog states none.
 *
 * Empty is rendered as nothing rather than as "no caveats recorded": this
 * cache is filled by whichever checklists have been loaded this session, so
 * an empty result means "not loaded here", NOT "this measurement claims
 * without limit". Saying the latter would be the fast-path-that-lies shape
 * the matrix was built to avoid.
 */
function rationaleHtml(analysisId) {
  const held = RATIONALE_BY_ANALYSIS.get(analysisId) || [];
  if (!held.length) return '';
  return `<div class="mt-s2 border-l-2 border-accent pl-s2">
      <div class="uppercase tracking-caps text-caps text-ink-muted">What this can claim</div>
      ${held.map((t) => `<p class="mt-[2px] max-w-[70ch] text-caveat text-accent-ink">${tnum(esc(t))}</p>`).join('')}
    </div>`;
}

async function openMeasurementDetail({ slug, analysisId, title, metric = '',
                                       glyph = '', summary = '', when = '' }) {
  const el = openDialog(title, `${slug} · ${analysisId}`);
  const body = el.querySelector('#wl-detail-body');
  body.innerHTML = `
    <div class="flex items-baseline gap-s2">
      ${glyph ? `<span>${glyph}</span>` : ''}
      <span class="text-answer text-ink">${esc(title)}</span>
      ${when ? `<span class="ml-auto text-provenance text-ink-muted">measured ${esc(ago(when))}</span>` : ''}
    </div>
    ${summary ? `<p class="mt-s1 max-w-[70ch] text-ink">${tnum(esc(summary))}</p>` : ''}
    ${rationaleHtml(analysisId)}
    ${trendSupport(analysisId) === 'not_tracked'
      // Said, flatly, once. No history SECTION — an empty section implies
      // something should be there — but not silence either: a reader who
      // expects a history and finds nothing cannot tell "correctly none" from
      // "we forgot". One line closes that.
      ? `<p class="mt-s3 text-caveat text-ink-muted">Current-state classification —
          not tracked over time.</p>`
      : `<div id="md-history" class="mt-s3 text-caveat text-ink-muted">Reading the history…</div>`}
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="rerun"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
        >Re-run <span class="font-mono">${esc(analysisId)}</span> →</button>
      <button type="button" data-act="runs"
        class="cursor-pointer bg-transparent text-ink-muted underline">Runs on this resource</button>
    </div>`;
  const slot = body.querySelector('#md-history');
  if (slot) {
    const hist = await historyHtml(slug, analysisId, metric);
    if (hist) slot.innerHTML = hist; else slot.remove();
  }
  body.querySelector('[data-act="runs"]')?.addEventListener('click', () => openRunsList(slug));
  body.querySelector('[data-act="rerun"]')?.addEventListener('click', async () => {
    const b = body.querySelector('[data-act="rerun"]');
    b.disabled = true;
    b.textContent = 'Queueing…';
    try {
      await enqueueBatch(analysisId, [slug], '', apiEntityType(state.resourceType));
      b.textContent = 'Queued';
    } catch (err) {
      b.textContent = err.status === 401 ? 'Not signed in' : `Refused: ${err.message}`;
    }
  });
}

/* ── The run record ───────────────────────────────────────────────────────
 *
 * The level that regressed. The old Survey pane showed what a run FOUND, and
 * a definitions list replaced it without replacing that.
 *
 * The per-step detail is in the activity log's own `detail` payload — steps
 * with their statuses — so this is wiring too, not new persistence.
 */
/*
 * Declared vs received — DECLARED IS NOT PROMISED. A step's `declared`
 * annotation types are "this step is capable of emitting these", not "this
 * run definitely produced them". Whether it actually did can only be read
 * from the run's own Egeria report, and two of the six verdicts below are not
 * "zero" even when they render on the empty side of the line: `not-published`
 * means the run has no report to check at all, and `not-recorded` means it
 * has one but predates the publish bookkeeping (2026-09-07) that would let us
 * trust an absence. Both say "cannot be known" — never "no" — and never count
 * toward a total the way a real zero would.
 */

/** One `types[]` entry from GET …/declared-vs-received, rendered as a line:
 *  glyph, the type in mono, and a short plain-English reading of the verdict. */
function declaredVsReceivedLineHtml(t) {
  let glyph = '<span class="text-ink-muted">·</span>';
  let phrase;
  switch (t.verdict) {
    case 'received':
      glyph = '<span class="text-state-ok">✓</span>';
      phrase = 'received';
      break;
    case 'step-did-not-finish': {
      glyph = '<span class="text-state-warn">⚠</span>';
      const statuses = t.step_statuses || {};
      const failing = (t.declared_by || []).filter((s) => statuses[s] && statuses[s] !== 'ok');
      const detail = (failing.length ? failing : (t.declared_by || []))
        .map((s) => `${s}${statuses[s] ? ` (${statuses[s]})` : ''}`).join(', ');
      phrase = `the declaring step did not finish — ${detail}`;
      break;
    }
    case 'step-not-in-run':
      phrase = 'declared, but this run never reached the step that produces it';
      break;
    case 'nothing-of-this-type':
      phrase = 'ran clean, found nothing of this type';
      break;
    case 'not-published':
      phrase = 'cannot be known — this run has no Egeria report to check';
      break;
    case 'not-recorded':
      phrase = 'cannot be known — published before publish bookkeeping began (2026-09-07)';
      break;
    default:
      phrase = String(t.verdict || '');
  }
  return `<li class="flex gap-s2">
    <span class="w-[16px] shrink-0">${glyph}</span>
    <span class="min-w-0 font-mono text-provenance">${esc(t.annotation_type)}</span>
    <span class="text-ink-muted">— ${tnum(esc(phrase))}</span>
  </li>`;
}

/** The whole declared-vs-received block for one run. `data` is the parsed
 *  response of GET …/declared-vs-received. */
function declaredVsReceivedHtml(data) {
  const s = data.summary || {};
  const didNotFinish = s['step-did-not-finish'] || 0;
  // When the run has no report (`published` false) or predates publish
  // bookkeeping (`recorded` false), every "unknown" type says the identical
  // thing — so it is said ONCE, not once per type, to avoid drowning the
  // defect lines (which ARE knowable from the run record and always shown).
  const collapseUnknown = !data.published || !data.recorded;
  let unknownShown = false;
  const rows = (data.types || []).map((t) => {
    if (collapseUnknown && (t.verdict === 'not-published' || t.verdict === 'not-recorded')) {
      if (unknownShown) return '';
      unknownShown = true;
      const reason = !data.published
        ? 'this run has no Egeria report, so what it produced cannot be listed from here'
        : 'this run was published before publish bookkeeping began (2026-09-07), so absence here means nothing';
      return `<li class="flex gap-s2">
        <span class="w-[16px] shrink-0 text-ink-muted">·</span>
        <span class="min-w-0 text-ink-muted">The rest cannot be known — ${esc(reason)}.</span>
      </li>`;
    }
    return declaredVsReceivedLineHtml(t);
  }).join('');
  return `
    <div class="mt-s2 text-caps uppercase tracking-caps text-ink-muted">Declared vs received</div>
    <div class="mt-s1 text-provenance text-ink-muted">
      <span class="tnum">${esc(s.declared ?? 0)} declared</span> · <span class="tnum">${esc(s.received ?? 0)} received</span>${
        didNotFinish ? ` · <span class="tnum text-state-warn">${esc(didNotFinish)} did not finish</span>` : ''}</div>
    <ul class="m-0 mt-s1 list-none p-0 text-caveat">${rows}</ul>`;
}

/** Which source answered a step (the run's own `answered_by`), and, when a
 *  credential was typed for the run, who the step ran as. Both are the run's
 *  persisted statements; a step that recorded neither says nothing. */
const ANSWERED_BY = {
  local: 'local',
  custom: 'local scan',
  'egeria-custom': 'local scan, published to Egeria',
  egeria: 'Egeria',
  prefect: 'Prefect',
};
export function stepSourceHtml(s) {
  const word = ANSWERED_BY[s && s.answered_by];
  const publish = stepPublishHtml(s);
  if (!word) return publish;
  const ranAs = s.ran_as && s.ran_as.user
    ? ` · ran as ${esc(s.ran_as.user)} (${esc(s.ran_as.scope || 'this run')})` : '';
  return `<span class="text-provenance text-ink-muted" data-step-source>answered by ${esc(word)}${ranAs}</span>${publish}`;
}

/** Brief D 7f.8: what happened to the publish, as a cue plus a short word.
 *  The step records one `publish_state` sentence in its detail ("published to
 *  Egeria" / "publish failed · <why>" / "local only · publish not chosen");
 *  `answered_by` says only which scan answered, so a failed publish and a
 *  publish nobody chose both read "local scan". The sentence stays on demand
 *  (title); the screen gets the glyph and one word. A step with no
 *  `publish_state` (every step but the adaptive one) draws nothing. */
export function stepPublishHtml(s) {
  const d = s && s.detail && typeof s.detail === 'object' ? s.detail : {};
  const state = String((s && s.publish_state) || d.publish_state || '');
  if (!state) return '';
  let kind = 'unclassified';
  let word = 'not published';
  if (/^published\b/i.test(state)) { kind = 'measured'; word = 'published'; }
  else if (/^publish failed\b/i.test(state)) { kind = 'error'; word = 'publish failed'; }
  else if (/^nothing to publish\b/i.test(state)) { kind = 'nothing'; word = 'nothing to publish'; }
  else if (/^publish not established\b/i.test(state)) { word = 'not established'; }
  const g = GLYPH_STATES[kind];
  const title = `title="${esc(state)}"`;
  const glyph = esc(g.glyph);
  if (kind === 'error') {
    return `<span class="text-provenance" data-step-publish="error" ${title}>`
      + `<span class="text-state-warn font-glyph" aria-hidden="true">${glyph}</span> `
      + `<span class="text-state-warn">${esc(word)}</span></span>`;
  }
  if (kind === 'measured') {
    return `<span class="text-provenance" data-step-publish="measured" ${title}>`
      + `<span class="text-state-ok font-glyph" aria-hidden="true">${glyph}</span> `
      + `<span class="text-ink-muted">${esc(word)}</span></span>`;
  }
  return `<span class="text-provenance" data-step-publish="${kind}" ${title}>`
    + `<span class="text-ink-muted font-glyph" aria-hidden="true">${glyph}</span> `
    + `<span class="text-ink-muted">${esc(word)}</span></span>`;
}

/** Backlog 7f.8: a database run's own publish word. It publishes once, after
 *  its steps, so the word is the run's `publish_state`; "published" is drawn
 *  only with the run's `egeria_report_guid` beside it (the proof), never from
 *  the sentence alone. A run that recorded no word draws nothing. */
export function runPublishHtml(d) {
  const state = String((d && d.publish_state) || '');
  if (!state) return '';
  if (/^published\b/i.test(state) && !String((d && d.egeria_report_guid) || '').trim()) {
    return stepPublishHtml({ publish_state: `publish not established · the run says "${state}" but recorded no report guid` });
  }
  return stepPublishHtml({ publish_state: state });
}

/** Backlog 7f.6: the header activity watch's page reader. The same page also tells an open Activity list that is
 *  not polling that something started or ended (`noticeActivity`). Exported so the harness drives the real wiring. */
export function activityWatchEntries(list = listActivity) {
  return async () => {
    const entries = await list(50);
    noticeActivity(entries).catch(() => { /* the list keeps what it shows */ });
    return entries;
  };
}

export async function openRunsList(slug) {
  const el = openDialog('Runs', slug);
  const body = el.querySelector('#wl-detail-body');
  let rows;
  try {
    rows = await getResourceRuns(slug, 40, apiEntityType(state.resourceType));
  } catch (err) {
    body.innerHTML = `<p class="text-state-warn">The runs could not be read: ${esc(err.message)}</p>`;
    return;
  }
  const runs = (rows || []).filter((r) => r.operation === 'survey');
  if (!runs.length) {
    body.innerHTML = `<p>No survey run is recorded for ${esc(slug)}.</p>`;
    return;
  }
  body.innerHTML = runs.slice(0, 12).map((r) => {
    let steps = [];
    let runPublish = '';
    try {
      const d = typeof r.detail === 'string' ? JSON.parse(r.detail) : (r.detail || {});
      steps = d.steps || [];
      // A run that published through the outbox carries its proof-derived word; a database run its own word.
      runPublish = r.publish_proof ? publishProofHtml(r.publish_proof) : runPublishHtml(d);
    } catch (_) { /* a detail we cannot parse is a run with no step list */ }
    const ok = steps.filter((s) => s.status === 'ok').length;
    const bad = steps.filter((s) => s.status && s.status !== 'ok');
    return `<details class="border-b border-rule py-s2">
      <summary class="cursor-pointer">
        <span class="text-ink">${esc(r.summary || 'survey run')}</span>
        <span class="text-provenance text-ink-muted"> · ${esc(ago(r.ts))} · ${esc(r.status || '')}</span>
      </summary>
      ${steps.length ? `
        <div class="mt-s1 text-provenance text-ink-muted">
          <span class="text-state-ok">${ok} ran</span>${
          bad.length ? ` · <span class="text-state-warn">${bad.length} did not</span>` : ''}${
          runPublish ? ` · <span data-run-publish>${runPublish}</span>` : ''}</div>
        <ul class="m-0 mt-s1 list-none p-0 text-caveat">
          ${steps.map((s) => {
            // A step that did not complete `ok` FAILED -- that is `✕`
            // (GLYPH_STATES.error), not `⚠`. Before this pass this used ⚠
            // for a non-ok step, but per REPLY-DESIGNER-ROUND2-DATABASE-
            // SCREENS.md §1, ⚠ now means ONLY "needs a person"; an error
            // uses ✕, so it does not collide with the human-attention glyph.
            const ok = s.status === 'ok';
            const g = ok ? GLYPH_STATES.measured : GLYPH_STATES.error;
            return `<li class="flex gap-s2">
            <span class="${g.tone} font-glyph" title="${esc(g.word)}" aria-label="${esc(g.word)}">${g.glyph}</span>
            <span class="min-w-0 font-mono text-provenance">${
              esc(String(s.step || '').split('::').pop())}</span>
            ${stepSourceHtml(s)}
            ${!ok && s.detail
              ? `<span class="text-ink-muted">— ${esc(String(s.detail).slice(0, 120))}</span>` : ''}
          </li>`;
          }).join('')}
        </ul>`
        : `${runPublish ? `<div class="mt-s1 text-provenance text-ink-muted"><span data-run-publish>${runPublish}</span></div>` : ''}<p class="mt-s1 text-caveat text-ink-muted">This run recorded no step list.</p>`}
      <div data-dvr="${esc(r.id)}"></div>
    </details>`;
  }).join('');

  // Lazily fetched, per run, on first open — the reconciliation is a second
  // request per row and most rows in a list of 12 are never expanded. `toggle`
  // fires on both open and close, and on close-then-reopen, so a `fetched`
  // flag closed over the one placeholder guards against firing it twice.
  body.querySelectorAll('details').forEach((det) => {
    const holder = det.querySelector('[data-dvr]');
    if (!holder) return;
    const entryId = holder.dataset.dvr;
    let fetched = false;
    det.addEventListener('toggle', async () => {
      if (!det.open || fetched) return;
      fetched = true;
      let dvr;
      try {
        dvr = await getDeclaredVsReceived(entryId);
      } catch (err) {
        // A 409 means this activity entry is not a survey run with a step
        // list — not an error worth showing, just nothing to reconcile.
        if (err instanceof ApiError && err.status === 409) return;
        holder.innerHTML = `<p class="mt-s1 text-caveat text-ink-muted">Could not read the declaration: ${esc(err.message)}</p>`;
        return;
      }
      holder.innerHTML = declaredVsReceivedHtml(dvr);
      attachRunReport(holder, apiEntityType(state.resourceType), slug, dvr);
    });
  });
}

/** PI-046: link a finished run to the Egeria report it published. The report GUID is the run's own
 *  (`egeria_report_guid` from declared-vs-received, which reads it off the run record); nothing is
 *  read from Egeria until the person presses the button, and a report that is no longer in Egeria
 *  says so instead of showing an empty list. A run with no report says that too. */
export function attachRunReport(holder, entityType, slug, dvr) {
  const guid = dvr && dvr.published ? String(dvr.egeria_report_guid || '') : '';
  if (!guid) {
    holder.insertAdjacentHTML('beforeend', `<p data-run-report="none" class="mt-s2 text-provenance text-ink-muted">
      <span class="font-glyph" aria-hidden="true">${stateEntry('unrun').glyph}</span> no Egeria report: this run was not published</p>`);
    return;
  }
  holder.insertAdjacentHTML('beforeend', `<div data-run-report="${esc(guid)}" class="mt-s2">
    <div class="flex flex-wrap items-baseline gap-s2 text-provenance text-ink-muted">
      <span>Egeria report</span> <span class="font-mono text-ink" data-run-report-guid>${esc(guid)}</span>
      <button type="button" data-run-report-open class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Show the report and its annotations</button>
      <span data-run-report-status aria-live="polite"></span></div>
    <div data-run-report-host class="mt-s1"></div></div>`);
  const wrap = holder.lastElementChild;
  const btn = wrap.querySelector('[data-run-report-open]');
  const status = wrap.querySelector('[data-run-report-status]');
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    status.textContent = 'reading Egeria…';
    try {
      const { mountEgeriaReports } = await import('/static/next/stages/publish.js');
      const host = wrap.querySelector('[data-run-report-host]');
      const reports = mountEgeriaReports(host, { entityType, slug, inEgeria: true });
      const read = await reports.ready;
      await reports.expand(guid);
      const there = [...host.querySelectorAll('[data-egeria-report]')].some((n) => n.dataset.egeriaReport === guid);
      // "Not in Egeria now" is a statement about a SUCCESSFUL read; a failed read says it could not be read.
      status.textContent = read === false ? 'Egeria could not be read, so this report was not checked'
        : (there || read !== true) ? '' : 'this report is not in Egeria now';
    } catch (err) {
      status.textContent = `could not be read: ${err && err.message ? err.message : 'unknown error'}`;
    }
    btn.disabled = false;
  });
}

/** Invalidates an in-flight dashboard read when the pane or resource changes. */
let dashToken = 0;

/* ── Dashboard ────────────────────────────────────────────────────────────
 *
 * THREE KINDS OF THING, THREE RANKS. Prose-as-peer worked and then everything
 * levelled up to meet it: twelve identical bordered cards, so a composed
 * score and "no specification file found" carried the same weight. Equal rank
 * for prose was the goal; equal rank for everything is what shipped.
 *
 *   headline  — the composed score, once, at size
 *   findings  — judgements, unresolved first, in the MATRIX'S OWN GLYPHS
 *   counts    — reference material, as a table, at reference weight
 *
 * Twelve bordered cards holding one number each IS a table, drawn expensively.
 */

/** Findings whose label means "nobody established this" sort to the top. */
const UNRESOLVED_LABELS = new Set([
  'not_established', 'unknown', 'none', 'not_measured', 'unavailable']);

/** ONE STATE VOCABULARY ACROSS BOTH SURFACES.
 *
 * The dashboard was shouting its enum — `— NOT_ESTABLISHED`, `— SOLE`,
 * `— PERIODIC` — as machine tokens welded to a title. They are the same kind
 * of fact the matrix encodes as glyphs, and someone who has learned the grid
 * should not have to learn a second language one click away.
 *
 * Reads `CELL` (worklist.js), itself now a thin view over the one glyph
 * table in glyphs.js -- see that module's docstring
 * (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1).
 */
function findingGlyph(label) {
  const l = String(label || '').toLowerCase();
  if (UNRESOLVED_LABELS.has(l)) return CELL.nothing;          // ∅ ran, established nothing
  if (/^(fail|gap|missing|no|absent|gone)$/.test(l)) return CELL.human;   // ⚠ needs a person
  if (/(sole|risk|low|weak|stale|declining|concentrat)/.test(l)) return CELL.partial;
  return CELL.answered;                                        // ✓
}

/** `not_established` -> `not established`; `SOLE` -> `sole`. */
const humanLabel = (l) => String(l || '').replace(/_/g, ' ').toLowerCase();

/* The dashboard's by-question view is retired (stage-page round,
 * SPEC-THE-STAGE-PAGE.md, point 10): its one surviving job -- reaching
 * the numbers behind a question's answer -- is now the Questions row's
 * own "the numbers behind this N" disclosure (see provenanceLine below).
 * Findings-per-question and cross-analysis disagreements have not moved
 * yet; `by_analysis` (loadByAnalysisPane) still carries those. */

/** A measured/error/unrun/unknown analysis state as one glyph, for the
 *  members rail, the run-history list, and Curate's task/step rows -- the
 *  same vocabulary the matrix and the questions row use, spelled out here
 *  since state-as-glyph is a cross-cutting need, not a "by analysis" or "by
 *  question" one.
 *
 *  The glyph for each case now comes from `GLYPH_STATES` (glyphs.js) --
 *  this function declares no glyph of its own any more, only which of the
 *  canonical states each of ITS OWN four input states maps to. Tones are
 *  unchanged from before this pass (see glyphs.js's docstring on why a
 *  colour pass is a separate, unreviewed change this one does not make). */
export function factGlyph(state) {
  switch (state) {
    case 'measured': return { glyph: GLYPH_STATES.measured.glyph, tone: 'text-state-ok' };
    case 'error': return { glyph: GLYPH_STATES.error.glyph, tone: 'text-state-warn' };
    case 'unrun': return { glyph: GLYPH_STATES.unrun.glyph, tone: 'text-ink-muted' };
    default: return { glyph: GLYPH_STATES.unclassified.glyph, tone: 'text-ink-muted' };
  }
}

/* ── The third door: the things a count counted ──────────────────────────
 *
 * There are three "show me more" requests on this pane and only one had a
 * path. About the MEASUREMENT — where did 68 come from, has it changed,
 * re-run it — is the popup. A question nobody asked — is this safe for
 * customer data? — is chat. The one in the middle, and the most common:
 * WHICH 68? Which 18 advisories, which 90 files, which three sub-resources
 * were not worthy? Nothing opened that. The popup looked like the door and
 * opened the number's history instead, so a reader learned that 18 had been
 * 18 for a fortnight.
 *
 * Members render in the right rail, which on Analysis held an empty ask box
 * and eighteen hundred pixels of nothing. Click a count, the rail lists the
 * members; the centre column keeps its place. No modal, no navigation.
 *
 * Members nest — file → symbol today; endpoint → operation → schema when
 * those are measured — and PURPOSE decides how much of the tree you see:
 * intent to use wants the public surface, intent to maintain wants that
 * plus the internal structure. One tree, two default expansions, because
 * maintain is a superset. Only symbols carry a public/internal marker, and
 * that marker is inferred from naming — the rail says so.
 *
 * This is also where two disagreeing counts get settled: "open either to
 * see what each counted" was already the instruction on the disagreement
 * block, and until now it could not be followed.
 */
function memberScope() {
  return currentPurposes().includes('Maintain') ? 'all' : 'public';
}

/* ── Promotion: the selection becomes a thing someone acts on ────────────
 *
 * A member list is the first place in the product where a person looks at
 * THINGS rather than NUMBERS, and things are what you act on. "18" is not a
 * work item; "three of these have no fix" is.
 *
 * The filters ARE the selection. "Everything matching the thing I noticed"
 * should not need a click per row: a facet — a severity, a package — selects
 * its members in one. Hand-picking stays. Facets come from the fields the
 * annotation already stores; nothing is invented, which is also why "no
 * fix" is not one here — cve_scan does not record fix availability, and a
 * facet the data cannot back would select nothing and look broken.
 *
 * Three acts, and they are different: add to <investigation> (this belongs
 * in my investigation's scope; W1-A, it used to mint a one-resource work
 * list), raise RFA (someone must), note in journal (worth knowing — no
 * obligation, so the likeliest used). One provenance line, composed on the
 * server, travels with all three. The selection is a SNAPSHOT of names,
 * never a query: a work item that changes what it refers to when the scan
 * re-runs is unusable.
 *
 * No "ignore" / "accept risk" here. That is a disposition on the finding — a
 * judgement with an author and a date — and belongs to the perishable-field
 * machinery, not a toolbar.
 */
function facetsHtml(groups, data) {
  const leaf = groups.flatMap((g) => g.members.filter((m) => !m.children_key).map((m) => ({ ...m, group: g.name })));
  const hasTree = groups.some((g) => g.members.some((m) => m.children_key));
  // A tree-shaped list has no leaves at this level; say so rather than
  // rendering no facet row and no checkboxes with nothing marking why.
  if (!leaf.length) {
    return hasTree
      ? `<div class="mb-s2 text-caps text-chrome-muted">this list is a tree · promotion selects leaves — open a branch to pick from it</div>`
      : '';
  }
  const byDetail = new Map();
  for (const m of leaf) if (m.detail) byDetail.set(m.detail, (byDetail.get(m.detail) || 0) + 1);
  const detailFacets = [...byDetail.entries()].filter(([, n]) => n < leaf.length).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const groupFacets = groups.filter((g) => g.members.some((m) => !m.children_key) && groups.length > 1).slice(0, 8);
  // The detail facets are counted over the members LISTED, which the 200-row
  // cap and truncated groups bound; the group facets use the group's own
  // count, which the server knows in full. Where the two differ, say which.
  const shown = groups.reduce((n, g) => n + g.members.length, 0);
  const capped = (data?.total || 0) > shown || groups.some((g) => g.truncated);
  if (!detailFacets.length && !groupFacets.length) {
    return `<div class="mb-s2 text-caps text-chrome-muted">no facets · ${byDetail.size === 1 ? 'one value across the list' : 'nothing to select by'}${
      hasTree ? ' · this list is a tree; promotion selects leaves' : ''}</div>`;
  }
  return `<div class="mb-s2 flex flex-wrap items-baseline gap-x-s2 gap-y-[2px] text-caps">
    <span class="text-chrome-muted">select</span>
    ${detailFacets.map(([d, n]) => `<button data-facet-detail="${esc(d)}" class="cursor-pointer bg-transparent p-0 font-mono text-chrome-ink underline">${esc(d)} <span class="tnum text-chrome-muted">${n}</span></button>`).join('')}
    ${groupFacets.map((g) => `<button data-facet-group="${esc(g.name)}" class="cursor-pointer bg-transparent p-0 font-mono text-chrome-ink underline">${esc(g.name)} <span class="tnum text-chrome-muted">${g.count}</span></button>`).join('')}
    <button data-facet-all class="cursor-pointer bg-transparent p-0 font-mono text-chrome-ink underline">all <span class="tnum text-chrome-muted">${leaf.length}</span></button>
    <button data-facet-none class="cursor-pointer bg-transparent p-0 text-chrome-ink underline">none</button>
    ${capped ? `<span class="text-chrome-muted">· counts are of the <span class="tnum">${shown}</span> shown</span>` : ''}
    ${hasTree ? `<span class="text-chrome-muted">· a tree: leaves only</span>` : ''}
  </div>`;
}

export function wireSelection(out, { slug, analysisId, metric, data }) {
  const picks = () => [...out.querySelectorAll('[data-pick]:checked')];
  const footer = out.querySelector('#member-selection');
  const project = state.projects.find((x) => x.slug === slug);
  const total = data.total || 0;
  // The server's date, on the payload. The line is composed on the server so
  // it cannot be forged; taking its date from the browser undid that.
  const runAt = data.run_at || '';
  // The facets ARE the selection and hand-picking refines it. So the facet is
  // remembered with the set it selected, and a refinement is described --
  // "high, plus 1 added by hand" -- rather than forgotten.
  let facetBase = '';
  let facetSet = null;      // Set of names the facet selected, or null for none
  // A typed name wins until it is cleared. Touched is tracked, not inferred
  // from the string -- the proposal starts with the display name too.
  let touched = false;
  let typed = '';

  const setFacet = (pred, label) => {
    out.querySelectorAll('[data-pick]').forEach((c) => { c.checked = pred(c); });
    facetBase = label;
    facetSet = label ? new Set(picks().map((c) => c.dataset.pick)) : null;
    render();
  };
  out.querySelector('[data-facet-all]')?.addEventListener('click', () => setFacet(() => true, ''));
  out.querySelector('[data-facet-none]')?.addEventListener('click', () => setFacet(() => false, ''));
  out.querySelectorAll('[data-facet-detail]').forEach((b) => b.addEventListener('click', () =>
    setFacet((c) => c.dataset.detail === b.dataset.facetDetail, b.dataset.facetDetail)));
  out.querySelectorAll('[data-facet-group]').forEach((b) => b.addEventListener('click', () =>
    setFacet((c) => c.dataset.group === b.dataset.facetGroup, b.dataset.facetGroup)));
  out.querySelectorAll('[data-pick]').forEach((c) => c.addEventListener('change', () => render()));

  function facetLabel() {
    if (!facetBase || !facetSet) return '';
    const now = new Set(picks().map((c) => c.dataset.pick));
    const added = [...now].filter((n) => !facetSet.has(n)).length;
    const removed = [...facetSet].filter((n) => !now.has(n)).length;
    return facetBase
      + (added ? `, plus ${added} added by hand` : '')
      + (removed ? `, less ${removed} removed by hand` : '');
  }
  // Mirrors members.singular(): only the last word, ies -> y, es after a
  // sibilant, else drop the s. The server proposes the same name on save.
  const singular = (noun) => {
    const i = noun.lastIndexOf(' ');
    const head = i >= 0 ? noun.slice(0, i + 1) : '';
    let last = i >= 0 ? noun.slice(i + 1) : noun;
    if (/ies$/.test(last) && last.length > 3) last = last.slice(0, -3) + 'y';
    else if (/(ses|xes|ches|shes)$/.test(last)) last = last.slice(0, -2);
    else if (/s$/.test(last) && !/ss$/.test(last)) last = last.slice(0, -1);
    return head + last;
  };
  function proposed(n) {
    const what = (metric || data.metric || 'members').replace(/_/g, ' ');
    const f = facetLabel();
    return `${project?.display_name || slug} — ${n} ${n === 1 ? singular(what) : what}${f ? `, ${f}` : ''}`;
  }
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  const what = (metric || data.metric || 'members').replace(/_/g, ' ');
  async function save(members, facet, name, status) {
    status.textContent = '…';
    try {
      const out = await saveReport(slug, analysisId, {
        question: `${what} of ${slug}`, metric: metric || data.metric || '', members, facet, name, scope: state.memberScope || memberScope(),
      });
      const r = out.record;
      status.innerHTML = `<span class="text-state-ok-on-dark">→ record “${esc(r.name)}” · ${esc(r.report?.header || '')}</span>`;
    } catch (err) {
      status.innerHTML = `<span class="text-state-warn-on-dark">${esc(err.status === 401 ? 'Sign in to save a report — a record needs an author.' : err.message)}</span>`;
    }
  }
  function render() {
    const sel = picks();
    footer.hidden = false;
    if (!sel.length) {
      // Nothing picked: the one act that makes sense on a list nobody has
      // triaged is to write it down, so it is the one act offered before
      // anything is picked (REPORT-RECORD-AND-TWO-CALLS C2).
      footer.innerHTML = `
        <div class="mb-[3px] text-caps text-chrome-ink">The whole list · <span class="tnum">${total}</span> ${esc(what)}
          <span class="text-chrome-muted">· from <span class="font-mono">${esc(analysisId)}</span>${runAt ? ` · <span class="tnum">${esc(ago(runAt))}</span>` : ' · run date not recorded'} · a snapshot, not a query</span></div>
        <input id="report-name" type="text" placeholder="${esc(project?.display_name || slug)} — ${total} ${esc(what)}, today"
          class="mb-[4px] w-full rounded-sm border border-chrome-line bg-transparent px-[6px] py-[2px] text-caps text-chrome-ink placeholder:text-chrome-muted">
        <div class="flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-caps">
          <button data-report-whole class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline" ${me ? '' : 'disabled'}>save as report</button>
          <span class="text-chrome-muted">${me ? 'work list, RFA and journal need a selection' : 'sign in to save a report — a record needs an author'}</span>
          <span id="promote-status" class="text-chrome-muted"></span>
        </div>`;
      footer.querySelector('[data-report-whole]')?.addEventListener('click', () =>
        save(null, '', footer.querySelector('#report-name').value.trim(), footer.querySelector('#promote-status')));
      return;
    }
    const facet = facetLabel();
    footer.innerHTML = `
      <div class="mb-[3px] text-caps text-chrome-ink"><span class="tnum">${sel.length}</span> selected${facet ? ` · ${esc(facet)}` : ''}
        <span class="text-chrome-muted">· from <span class="font-mono">${esc(analysisId)}</span>${runAt ? ` · <span class="tnum">${esc(ago(runAt))}</span>` : ' · run date not recorded'} · a snapshot, not a query</span></div>
      <input id="promote-name" type="text" value="${esc(touched && typed ? typed : proposed(sel.length))}"
        class="mb-[4px] w-full rounded-sm border border-chrome-line bg-transparent px-[6px] py-[2px] text-caps text-chrome-ink">
      <div class="flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-caps">
        ${scopeActHtml({ invName: curInvName(), inScope: !!state.investigation && state.workingSet.has(slug) },
          'data-promote="scope"', 'cursor-pointer bg-transparent p-0 text-accent-on-dark underline').replace('text-ink-muted', 'text-chrome-muted')}
        <button data-promote="rfa" class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">raise RFA</button>
        <button data-promote="journal" class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">note in journal</button>
        <button data-report-sel class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">save as report</button>
        <span id="promote-status" class="text-chrome-muted"></span>
      </div>`;
    const nameEl = footer.querySelector('#promote-name');
    footer.querySelector('[data-report-sel]').addEventListener('click', () =>
      save(picks().map((c) => c.dataset.pick), facetLabel(), nameEl.value.trim(), footer.querySelector('#promote-status')));
    nameEl.addEventListener('input', () => { typed = nameEl.value; touched = typed.trim().length > 0; });
    footer.querySelectorAll('[data-promote]').forEach((b) => b.addEventListener('click', async () => {
      const action = b.dataset.promote;
      const perform = async (investigation = '') => {
        const status = footer.querySelector('#promote-status');
        const members = picks().map((c) => c.dataset.pick);
        b.disabled = true; status.textContent = '…';
        try {
          const out2 = await promoteMembers(slug, analysisId, {
            action, metric: metric || data.metric || '', members, total, facet: facetLabel(), runAt,
            name: nameEl.value.trim(), investigation,
          }, apiEntityType(state.resourceType));
          // Say where it went, not "sent".
          let where = out2.rfa ? `RFA ${String(out2.rfa).slice(0, 8)}` : 'the journal';
          if (action === 'scope') {
            const becameCurrent = investigation !== state.investigation;
            if (becameCurrent) await setInvestigation(investigation);
            else await loadWorkingSet();
            await refreshOpenInvestigation(investigation);
            where = out2.already_in_scope
              ? `already in ${out2.investigation_name}’s scope, left as it was`
              : `added to ${out2.investigation_name}’s scope${becameCurrent ? ', now your current investigation' : ''}`;
            render();   // the act now reads "already in …’s scope"
          }
          const st = footer.querySelector('#promote-status');
          if (st) st.innerHTML = `<span class="text-state-ok-on-dark">→ ${esc(where)}</span>`;
        } catch (err) {
          b.disabled = false;
          status.innerHTML = `<span class="text-state-warn-on-dark">${esc(err.status === 401 ? 'sign in to promote' : err.message)}</span>`;
        }
      };
      if (action === 'scope') {
        if (state.investigation) await perform(state.investigation);
        else chooseInvestigationThen((chosen) => perform(chosen));
        return;
      }
      await perform();
    }));
  }
  render();   // the whole-list state, before any pick
}

export async function openMembers({ slug, analysisId, metric = '', title = '' }) {
  const out = $('rail-evidence');
  if (!out) return;
  ensureRailShowing();
  const ticket = railClaim();
  const scope = state.memberScope || memberScope();
  railFrame('Members', slug, `<div class="text-caps text-chrome-muted">Reading the members of ${esc(title || analysisId)}…</div>`, { sub: 'loading' });
  let data;
  try {
    data = await getMembers(slug, analysisId, { metric, scope }, apiEntityType(state.resourceType));
  } catch (err) {
    if (railStale(ticket)) return;   // a later click owns the slot now
    // A 400 is an ANSWER ("members aren't built for databases yet"), not a
    // fault in the reader: render the server's sentence as written.
    if (err.status === 400) {
      railFrame('Members', slug, `<div class="text-caps text-chrome-muted" data-members-not-built>${esc(err.message)}</div>`, { sub: 'not built yet' });
      return;
    }
    railFrame('Members', slug, `<div class="text-caps text-state-warn-on-dark">The members of ${esc(title || analysisId)} could not be read: ${esc(err.message)}</div>`, { sub: 'failed' });
    return;
  }
  if (railStale(ticket)) return;
  railTag(out, slug);
  if (data.not_applicable) {
    // Metrics-only analysis (repository_health, and the like): the list
    // slot holds one sentence, not a zero from the wrong table — and no
    // save-as-report offer, since there is nothing to report.
    out.innerHTML = `
      <div class="mb-s1 flex items-baseline gap-s2">
        <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Members</span>
        <span class="min-w-0 truncate text-caps text-chrome-muted">for <span class="font-mono">${esc(slug)}</span> · ${esc(data.title)}</span>
        <button data-act="close-members" class="ml-auto cursor-pointer bg-transparent text-caps text-chrome-muted underline">close</button>
      </div>
      <div class="mb-s2 text-caps text-chrome-muted"><span class="font-mono">${esc(data.analysis_id)}</span>${data.run_at ? ` · <span class="tnum">${esc(ago(data.run_at))}</span>` : ''}</div>
      <div class="text-caps text-chrome-muted">${esc(data.reason || `${data.analysis_id} records measurements, not members — there is nothing to list.`)}</div>
      <div class="mt-s2 text-caps text-chrome-muted">read from <span class="font-mono">${esc(data.source)}</span></div>`;
    out.querySelector('[data-act="close-members"]')?.addEventListener('click', () => { out.innerHTML = ''; });
    return;
  }
  const groups = data.groups || [];
  const shown = groups.reduce((n, g) => n + g.members.length, 0);
  // The sub-resource survey's members are CANDIDATES. Ticks here would be a one-off browser selection that
  // saves nothing; what is published is chosen with Include in "what's in it", so this list is read-only
  // and points there.
  const readOnly = analysisId === 'sub_resource_survey' && state.resourceType === 'repo';
  out.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Members</span>
      <span class="min-w-0 truncate text-caps text-chrome-muted">for <span class="font-mono">${esc(slug)}</span> · <span class="tnum">${data.total}</span> · ${esc(data.title)}${
        data.inventory ? ` · ${tnum(esc(data.inventory))}` : ''}</span>
      <button data-act="close-members" class="ml-auto cursor-pointer bg-transparent text-caps text-chrome-muted underline">close</button>
    </div>
    <div class="mb-s2 text-caps text-chrome-muted">
      <span class="font-mono">${esc(data.analysis_id)}</span>
      · <button data-act="member-history" class="cursor-pointer bg-transparent text-accent-on-dark underline">measurement ›</button>${analysisId === 'interface_surface' && state.resourceType === 'repo'
        ? `
      · <button data-act="api-reference" class="cursor-pointer bg-transparent text-accent-on-dark underline">API reference ›</button>` : ''}
      · scope
      <button data-scope="public" aria-pressed="${scope === 'public'}" class="wl-chartchip cursor-pointer bg-transparent px-[4px] text-chrome-muted">use</button>
      <button data-scope="all" aria-pressed="${scope === 'all'}" class="wl-chartchip cursor-pointer bg-transparent px-[4px] text-chrome-muted">maintain</button>
      ${data.scope_honoured ? '' : `<span class="text-chrome-muted">· scope not applicable to this set</span>`}
    </div>
    ${data.note ? `<div class="mb-s2 text-caps text-chrome-muted">${esc(data.note)}</div>` : ''}
    ${readOnly ? `<div data-members-not-saved class="mb-s2 text-caps text-chrome-ink"><span class="font-glyph" aria-hidden="true">○</span> not saved here · choose what is published with Include in what's in it
      ${state.stage === 'curate' ? `· <button data-members-goto-scope class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">go to what's in it ›</button>` : ''}</div>` : facetsHtml(groups, data)}
    ${groups.length ? '' : `<div data-members-empty class="text-caps text-chrome-muted"><span class="font-glyph" aria-hidden="true">○</span> ${esc(data.reason || `nothing stored by ${data.analysis_id} for this repo`)} · read from <span class="font-mono">${esc(data.source)}</span></div>`}
    <div class="flex flex-col gap-s1">
      ${groups.map((g, gi) => `<details class="border-b border-chrome-line-soft pb-s1" ${gi < 3 ? 'open' : ''}>
        <summary class="cursor-pointer text-subtab text-chrome-ink"><span class="tnum">${g.count}</span> · ${esc(g.name)}</summary>
        <ul class="m-0 mt-[2px] list-none p-0 pl-s2">
          ${g.members.map((m) => `<li data-member-row class="flex flex-wrap items-baseline gap-x-s2 py-[2px] text-caps">
            ${m.children_key || readOnly ? '' : `<input type="checkbox" data-pick="${esc(m.name)}" data-group="${esc(g.name)}" data-detail="${esc(m.detail || '')}" class="shrink-0">`}
            ${m.children_key
              ? `<button data-children="${esc(m.children_key)}" class="cursor-pointer bg-transparent p-0 text-left font-mono text-chrome-ink underline">${esc(m.name)}</button>
                 <span class="text-chrome-muted tnum">${m.count ?? ''}</span>`
              : `<span data-member-name class="min-w-[12ch] max-w-full break-words font-mono text-chrome-ink [overflow-wrap:anywhere]">${esc(m.name)}</span>`}
            ${m.detail ? `<span data-member-detail class="min-w-0 max-w-full break-words text-chrome-muted [overflow-wrap:anywhere]">${esc(m.detail)}</span>` : ''}
          </li>`).join('')}
          ${g.truncated ? `<li class="text-caps text-chrome-muted">and more — the first ${g.members.length} are shown</li>` : ''}
        </ul>
      </details>`).join('')}
    </div>
    ${shown < data.total && !groups.some((g) => g.truncated)
      ? `<div class="mt-s1 text-caps text-chrome-muted"><span class="tnum">${shown}</span> of <span class="tnum">${data.total}</span> listed; the rest are nested under what is shown</div>` : ''}
    <div class="mt-s2 text-caps text-chrome-muted">read from <span class="font-mono">${esc(data.source)}</span></div>
    <div id="member-selection" class="mt-s2 border-t border-chrome-line pt-s2"></div>`;

  out.querySelector('[data-act="close-members"]')?.addEventListener('click', () => { out.innerHTML = ''; });
  if (readOnly) {
    out.querySelector('#member-selection')?.remove();
    out.querySelector('[data-members-goto-scope]')?.addEventListener('click', () => jumpToCurateSection('curate-sec-what-holds'));
  } else {
    wireSelection(out, { slug, analysisId, metric, data });
  }
  out.querySelector('[data-act="api-reference"]')?.addEventListener('click', () => openApiReference({ slug }));
  out.querySelector('[data-act="member-history"]')?.addEventListener('click', () => openMeasurementDetail({
    slug, analysisId, title: title || analysisId, metric,
  }));
  out.querySelectorAll('[data-scope]').forEach((b) => b.addEventListener('click', () => {
    state.memberScope = b.dataset.scope;
    openMembers({ slug, analysisId, metric, title });
  }));
  // One level down, on demand: a file opens its symbols in place, and a class opens its methods.
  const wireChildren = (root) => root.querySelectorAll('[data-children]').forEach((b) => b.addEventListener('click', async () => {
    const li = b.closest('li');
    if (li.querySelector(':scope > ul')) { li.querySelector(':scope > ul').remove(); return; }
    b.textContent = `${b.textContent} …`;
    let rows;
    try { rows = (await getMemberChildren(slug, analysisId, b.dataset.children, { scope }, apiEntityType(state.resourceType))).members || []; }
    catch (err) { rows = [{ name: `could not read: ${err.message}`, detail: '' }]; }
    b.textContent = b.textContent.replace(/ …$/, '');
    const ul = document.createElement('ul');
    ul.className = 'm-0 mt-[2px] w-full list-none p-0 pl-s3';
    ul.innerHTML = rows.map((m) => `<li class="flex flex-wrap items-baseline gap-x-s2 py-[1px] text-caps">
        ${m.children_key
          ? `<button data-children="${esc(m.children_key)}" class="cursor-pointer bg-transparent p-0 text-left font-mono text-chrome-ink underline">${esc(m.name)}</button>`
          : `<span class="min-w-0 break-all font-mono text-chrome-ink">${esc(m.name)}</span>`}
        ${m.detail ? `<span class="min-w-0 break-words text-chrome-muted">${esc(m.detail)}</span>` : ''}</li>`).join('')
      || `<li class="text-caps text-chrome-muted">nothing at this level</li>`;
    li.appendChild(ul);
    wireChildren(ul);
  }));
  wireChildren(out);
  // The rail may be closed on a narrow shell; a members request opens it.
  if (typeof setRailOpen === 'function') setRailOpen(true);
}

/**
 * By-analysis, rebuilt (BRIEF-BY-ANALYSIS-PANEL-USABILITY.md +
 * REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2, 2026-09-28).
 *
 * Two problems this rebuild exists for, both live-confirmed by the project
 * owner: (1) the pane rendered nothing but "Reading the dashboards…" for up
 * to 109s -- the ORIGINAL comment above the old single `getSurveyDashboards`
 * call named this cost and did nothing about it; (2) the Relationship
 * Graph card showed only numbers, never the graph `factGraphviz` already
 * knows how to render (the Questions-tab Evidence rail's own path).
 *
 * THE FIX FOR (1) IS NOT "make the reader faster" -- `build_survey_results`'s
 * own results/headline readers are genuinely expensive (db_derived-backed);
 * this pane stops BLOCKING ON THEM AS ONE BATCH instead:
 *
 *   - The contents board (glyph + headline + run time per board) paints from
 *     `state.analyses` -- already fetched at boot (`start()`'s
 *     `listAnalyses(...)` call) -- with NO network call at all when the
 *     current entity type is database/filesystem (board id === analysis_id
 *     there, so state.analyses IS the board list). repo's dashboards group
 *     several analyses under one id state.analyses doesn't carry, so repo
 *     falls back to the new cheap `listSurveyResultBoards`/`/survey-results/
 *     boards` endpoint (catalog metadata only, no results/headline reader
 *     -- see `workflows.analysis.list_survey_result_boards`'s docstring).
 *     Either way this is visible before ANY board's own data has been
 *     fetched.
 *   - Each board's real data is then fetched ONE AT A TIME, in parallel,
 *     via `getSurveyDashboards(..., { boardId })` (the new `board_id` query
 *     param `build_survey_results` now takes) -- not the old single call
 *     that read every board in one sweep. A card fills in as its own read
 *     lands; a slow board no longer holds up a fast one.
 *   - `dashToken`'s existing stale-read-cancellation pattern still applies
 *     (`live()` below), scoped to the whole pane load exactly as before.
 */

/** How many board reads run at once -- found necessary live (not merely
 *  theoretical): switching stages quickly left several `db_derived`-backed
 *  board reads from the ABANDONED stage still running server-side (their
 *  RESULT was ignored via `live()`, but the request itself kept the thread
 *  pool busy), which starved even the cheap boards-catalog call for the new
 *  stage behind them. Small and fixed rather than tuned -- this pane's job
 *  is "don't block on one 109s sweep", not "find the server's exact
 *  concurrency ceiling". */
const BY_ANALYSIS_MAX_CONCURRENT_READS = 3;

/** How long a single board's fetch may sit 'loading' before the pane gives
 *  up spinning on it and offers a manual "open to load" trigger instead
 *  (design requirement added 2026-09-28, owner gate check on
 *  laz_local_adventureworks: pane settle ran 5+ minutes on an un-warmed
 *  board_summary, deemed unacceptable even as an interim while the
 *  board_summary read-cost fix lands). This is a SAFETY NET independent of
 *  that fix's effectiveness -- it applies whether a board's summary is
 *  missing/stale and the fallback recompute is fast or still slow, so the
 *  pane never hangs indefinitely on one card. The underlying fetch is not
 *  cancelled (no AbortController here -- the same reasoning `live()`
 *  already applies to an abandoned stage's in-flight reads: the request
 *  keeps running server-side either way, only the UI's wait for it ends);
 *  if it does resolve after the timeout, the card still updates to its
 *  real state then. */
export const BY_ANALYSIS_BOARD_TIMEOUT_MS = 30000;

/** localStorage convenience: whether this board's card was left open on
 *  this resource, on this browser. Per-viewer only -- never read back by
 *  this session, never shared. */
function byAnalysisOpenKey(slug, boardId) { return `re-next:by-analysis-open:${slug}:${boardId}`; }
function byAnalysisCardOpen(slug, boardId, defaultOpen) {
  try {
    const v = window.localStorage.getItem(byAnalysisOpenKey(slug, boardId));
    return v === null ? defaultOpen : v === '1';
  } catch { return defaultOpen; }
}
function setByAnalysisCardOpen(slug, boardId, open) {
  try { window.localStorage.setItem(byAnalysisOpenKey(slug, boardId), open ? '1' : '0'); } catch { /* fine without */ }
}

/**
 * Order boards the way the brief's "Order" section asks for: the order the
 * stage's own questions ask for them (the first question that names the
 * board's id, in the questions list's own order); a board no question names
 * keeps its catalog position, appended after every named board.
 *
 * `leadAnalysisId` (envelope.js) is reused here rather than reading
 * `analysis_ids[0]` -- the same reasoning that function's own docstring
 * gives for the Questions row: a question's note can name an analysis OTHER
 * than `analysis_ids[0]` as the one it is actually asking for.
 */
function orderBoardsByQuestions(boards, questions) {
  const boardIds = new Set(boards.map((b) => b.id));
  const named = [];
  const namedIds = new Set();
  for (const q of questions || []) {
    const ids = q.analysis_ids || [];
    const lead = leadAnalysisId(q);
    const preferred = [...(lead ? [lead] : []), ...ids];
    for (const id of preferred) {
      if (boardIds.has(id) && !namedIds.has(id)) { named.push(id); namedIds.add(id); }
    }
  }
  const rest = boards.filter((b) => !namedIds.has(b.id));
  const byId = new Map(boards.map((b) => [b.id, b]));
  return [...named.map((id) => byId.get(id)), ...rest];
}

/** Never-run boards last, stable otherwise -- the brief's own rule, applied
 *  once real state is known (a board whose fetch hasn't settled yet is left
 *  where it is; only a SETTLED, genuinely-never-run board moves). */
function neverRunLast(boards, boardState) {
  const scored = boards.map((b, i) => {
    const entry = boardState.get(b.id);
    const settledNeverRun = entry && entry.status === 'done' && entry.board && !entry.board.has_results;
    return { b, i, last: !!settledNeverRun };
  });
  scored.sort((x, y) => (x.last === y.last ? x.i - y.i : x.last ? 1 : -1));
  return scored.map((s) => s.b);
}

/** One STATES key (glyphs.js) for a board's current read state. Coarser
 *  than the Questions row's own `rowState` -- the by-analysis payload
 *  carries `has_results`/`results`, not a per-fact `result_status.py`
 *  state -- but reads the one honest signal that IS in that payload
 *  (`needs-lens`, via `preliminary_fit`'s own `lens_declared` marker,
 *  same check `needsLensDeclaration` above makes on the Questions
 *  envelope) rather than collapsing every "ran" case to a flat tick. */
export function boardStateKey(entry) {
  if (entry.status === 'loading') return null;   // rendered separately -- no STATES entry fits "still fetching"
  if (entry.status === 'timeout') return null;    // rendered separately -- "still reading -- open to load"
  if (entry.status === 'error') return 'error';
  const board = entry.board;
  if (!board) return 'error';
  const needsLens = (board.analyses || []).some(
    (a) => a.analysis_id === 'preliminary_fit' && a.results && a.results.lens_declared === false);
  if (needsLens) return 'needs-lens';
  // `result_status.py`'s NOT_ESTABLISHED, surfaced here for the first time
  // (BRIEF-BY-ANALYSIS-PANEL-USABILITY.md gate task 4: "the not-established
  // cards must read as such in the table of contents"). Before this, a
  // board whose analysis measured something but could not settle a result
  // -- `has_results: true`, `results.state === 'not_established'` -- fell
  // straight through to `measured` below and showed a plain ✓, the exact
  // "confident wrong answer" shape: a real read, but not a settled one.
  // Neither gate database (laz_local_adventureworks,
  // localhost_docker_coco_pharma) has a not-established analysis today, so
  // this is covered by a harness fixture test instead of a live screenshot
  // -- see frontend-build/test-harness's by-analysis-not-established test.
  const notEstablished = (board.analyses || []).some(
    (a) => a.results && a.results.state === 'not_established');
  if (notEstablished) return 'not_established';
  if (!board.has_results) return 'unrun';
  return 'measured';
}

/** The board's own headline, one sentence, per REPLY-DESIGNER-ROUND2-
 *  DATABASE-SCREENS.md §2.4 -- `a.headline` in the payload is `head` from
 *  `_headline_for`'s OWN `headline_reader(registry, slug)` call
 *  (`workflows.analysis._read_analyses`'s `headline_map.get(analysis_id)`
 *  is literally the same function object `facts.py`'s FactLayer resolves
 *  for the Questions tab's lead question on this analysis -- see
 *  `test_by_analysis_headline_matches_questions.py`), so this is the exact
 *  same sentence the Questions tab would show, run through the same
 *  one-sentence truncation rule (`firstSentence`) that slot uses. */
function boardHeadlineHtml(board) {
  if (!board) return '';
  const lead = (board.analyses || []).find((a) => a.headline && a.headline.label);
  if (!lead) return '';
  return answerHtml(firstSentence(lead.headline.label), esc, tnum);
}
function boardHeadlineText(board) {
  if (!board) return '';
  const lead = (board.analyses || []).find((a) => a.headline && a.headline.label);
  return lead ? firstSentence(lead.headline.label) : '';
}

/**
 * Match each board to the question that names it, the same "lead analysis,
 * then any analysis_id" preference `orderBoardsByQuestions` already applies
 * -- pulled out so the headline fallback below can use the identical
 * matching rule rather than a second guess at it.
 */
export function boardQuestionMap(boards, questions) {
  const boardIds = new Set(boards.map((b) => b.id));
  const map = new Map();
  for (const q of questions || []) {
    const ids = q.analysis_ids || [];
    const lead = leadAnalysisId(q);
    const preferred = [...(lead ? [lead] : []), ...ids];
    for (const id of preferred) {
      if (boardIds.has(id) && !map.has(id)) map.set(id, q);
    }
  }
  return map;
}

/**
 * The row's headline BEFORE the board's own (slow) read has landed --
 * BRIEF-BY-ANALYSIS-PANEL-USABILITY.md's "Progressive render" addendum:
 * "the contents board... reads only the per-analysis state already resolved
 * for the Questions tab, no dashboard fetch required."
 *
 * Genuinely free: this makes NO new network call. `answers` is `state.
 * answers` -- the Questions tab's own per-question envelope cache
 * (`loadAnswer`, `getAnswer`) -- so this only has something to show when the
 * Questions tab has already resolved the matching question's envelope for
 * this slug/stage, in this browser session. When it hasn't (a cold load
 * that goes straight to By-analysis without visiting Questions first), this
 * returns '' and the row shows the same "reading…" placeholder it always
 * did -- an honest floor, not a manufactured line. (`getQuestions()` itself,
 * fetched for board ordering just above this function's one call site, was
 * checked and does NOT carry a resolved headline of its own -- its
 * `has_data` field is a boolean, and the headline readers
 * `DATABASE_ANALYSIS_HEADLINE_MAP`/etc. name are each their own registry
 * read, not free -- see this branch's IMPLEMENTED doc for the full trace.)
 *
 * Reuses `leadAnalysisId`/`firstSentence` -- the same pair `boardHeadlineText`
 * and `readEnvelope` (envelope.js) both already use -- rather than a third,
 * bespoke extraction of "the one sentence this row shows".
 */
export function envelopeHeadlineText(question, answers) {
  if (!question) return '';
  const env = answers.get(question.question);
  if (!env || typeof env !== 'object' || env.__error) return '';
  const facts = (env.facts || []).filter((f) => f.is_known);
  if (!facts.length) return '';
  const lead = leadAnalysisId(question);
  const preferred = [
    ...(lead ? [lead] : []),
    ...(question.analysis_ids || []),
    ...facts.map((f) => f.analysis_id),
  ];
  const seen = new Set();
  for (const id of preferred) {
    if (seen.has(id) || !id) continue;
    seen.add(id);
    const f = facts.find((x) => x.analysis_id === id);
    if (!f) continue;
    if (f.headline) return firstSentence(f.headline);
    const p = prose(f);
    if (p) return firstSentence(p);
  }
  return '';
}

/** Every numeric measure across every SETTLED board, grouped by name --
 *  same "a name reported twice" detection the old per-card COUNTS table
 *  did, now computed once for the whole pane so the Shared Names block
 *  (REPLY §2.3, reworded per the owner's 2026-09-28 gate feedback -- see
 *  `sharedNamesHtml`'s own doc) can render each shared name ONCE instead of
 *  inside every card that carries it.
 *
 *  Two derived sets, kept separate on purpose:
 *   - `shared` -- every name carried by more than one analysis, whether or
 *     not their values agree. This, not "disagree", is the Shared Names
 *     block's own inclusion criterion (the owner: "I don't know what '1
 *     DISAGREE' means" -- a count that conflated "shared" with "shared and
 *     different" was the confusion).
 *   - `disagreeing` -- the subset of `shared` whose comparable values
 *     actually differ. Still the right (and only) set for the per-card `≠`
 *     mark (`boardCountsHtml`), whose own title text says "reported with
 *     different values elsewhere" -- broadening ITS set to plain "shared"
 *     would make the mark lie for a name two analyses happen to agree on.
 *
 *  `preliminary_fit`'s `confidence: 0` is marked `noLens` and excluded from
 *  the VALUES compared for disagreement (REPLY §2.3: "no lens was supplied,
 *  so there was nothing to be confident about... leave it out of the
 *  comparison") but still carried in the row for display, rendered as
 *  "— (no lens declared)" rather than a bare, misleading zero. */
export function collectMeasures(boards, boardState) {
  const seen = new Map();
  for (const b of boards) {
    const entry = boardState.get(b.id);
    if (!entry || entry.status !== 'done' || !entry.board) continue;
    for (const a of entry.board.analyses || []) {
      for (const [k, v] of Object.entries(a.results || {})) {
        if (typeof v !== 'number') continue;
        const noLens = a.analysis_id === 'preliminary_fit' && k === 'confidence' && v === 0;
        const rec = seen.get(k) || [];
        rec.push({ analysis: a.analysis_id, value: v, when: a.last_surveyed_at, noLens });
        seen.set(k, rec);
      }
    }
  }
  const shared = new Map();
  const disagreeing = new Set();
  for (const [k, rec] of seen) {
    if (rec.length > 1) shared.set(k, rec);
    const comparable = rec.filter((x) => !x.noLens);
    if (comparable.length > 1 && new Set(comparable.map((x) => x.value)).size > 1) disagreeing.add(k);
  }
  return { seen, shared, disagreeing };
}

/** A shared measure's display text -- the no-lens placeholder, or the
 *  ordinary formatted number. Used both by the Shared Names block and by
 *  `boardCountsHtml`'s own per-card row for the exact same value, so the
 *  two never disagree about how `preliminary_fit`'s no-lens 0 reads (the
 *  owner's 2026-09-28 coco_pharma finding: the Shared Names block already
 *  said "— (no lens declared)" while the card's own COUNTS table, computing
 *  its display text separately, still showed a bare "0" for the identical
 *  value). */
export function measureDisplay(x, key) {
  return x.noLens ? '— (no lens declared)' : fmtScalar(x.value, key);
}

/** COUNTS grouping (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2, the
 *  brief's own "Result / Coverage / Diagnostics" split): Result = the
 *  scalars a person quotes as the analysis's own headline numbers (edge/
 *  component/table counts, determined counts, confidence); Coverage =
 *  how much of the resource this reading covers (table/schema/column
 *  counts, measured/estimated/unmeasured, keys captured); everything else
 *  is Diagnostics. A heuristic on the NAME, same spirit as `fmtScalar`'s
 *  own suffix-only rule -- deliberately conservative rather than guessing
 *  from magnitude. */
function countGroupFor(key) {
  const k = key.toLowerCase();
  if (/table|schema|column|coverage|captured|measured|estimated|unmeasured|readable/.test(k)) return 'coverage';
  if (/edge|component|isolated|determined|grain|confidence|overall|count$/.test(k)) return 'result';
  return 'diagnostics';
}
const COUNT_GROUP_LABELS = { result: 'Result', coverage: 'Coverage', diagnostics: 'Diagnostics' };
const COUNT_GROUP_ORDER = ['result', 'coverage', 'diagnostics'];

/** Grouped COUNTS table for one board -- the non-headline scalar/boolean
 *  fields across its analyses, three groups instead of one flat list.
 *  Shared names are still shown HERE too (a reader looking at one card
 *  should not have to leave it to see the number this card itself
 *  measured), with a `≠` mark linking to the Shared Names block for the
 *  ones that actually disagree, per REPLY §2's "each card's COUNTS keeps
 *  its own value with a small ≠ mark".
 *
 *  `disagreeing` is `collectMeasures`'s disagreeing SET (names whose
 *  comparable values actually differ) -- not its `shared` map, which would
 *  mark every merely-shared name with `≠` even when they agree.
 *
 *  `noLens` is tracked per row exactly the way `collectMeasures` tracks it
 *  for the Shared Names block, and rendered through the same `measureDisplay`
 *  helper -- found 2026-09-28 on coco_pharma: this table used to format
 *  every value with a bare `fmtScalar`, so `preliminary_fit`'s no-lens
 *  `confidence: 0` showed as a plain "0" here while the Shared Names block,
 *  right next to it on the same screen, correctly said "— (no lens
 *  declared)" for the identical value -- two renderings of one number,
 *  disagreeing with each other. */
export function boardCountsHtml(board, disagreeing) {
  if (!board) return '';
  const groups = { result: [], coverage: [], diagnostics: [] };
  for (const a of board.analyses || []) {
    const res = a.results || {};
    for (const [k, v] of Object.entries(res)) {
      if (k === 'graphviz' || k === 'headline' || typeof v === 'object') continue;
      if (typeof v !== 'number' && typeof v !== 'boolean') continue;
      const noLens = a.analysis_id === 'preliminary_fit' && k === 'confidence' && v === 0;
      groups[countGroupFor(k)].push({
        key: k, value: v, analysisId: a.analysis_id, when: a.last_surveyed_at, noLens,
      });
    }
  }
  const anyRows = COUNT_GROUP_ORDER.some((g) => groups[g].length);
  if (!anyRows) return '';
  return COUNT_GROUP_ORDER.filter((g) => groups[g].length).map((g) => `
    <div class="mt-s3 text-caps uppercase tracking-caps text-ink-muted">${esc(COUNT_GROUP_LABELS[g])}</div>
    <table class="w-full border-collapse text-caveat">
      ${groups[g].map((c) => {
        const disagree = disagreeing.has(c.key);
        return `<tr class="wl-countrow cursor-pointer border-b border-rule ${disagree ? 'bg-[rgba(168,113,42,.07)]' : ''}"
          data-measure="${esc(c.analysisId)}" data-metric="${esc(c.key)}"
          data-title="${esc(c.key.replace(/_/g, ' '))}" data-when="${esc(c.when || '')}">
          <td class="py-[5px] pr-s3 text-ink">${esc(c.key.replace(/_/g, ' '))}${
            disagree ? ' <span class="text-state-warn" title="reported with different values elsewhere -- see Shared names above">≠</span>' : ''}</td>
          <td class="tnum py-[5px] pr-s3 text-right text-ink">${esc(measureDisplay(c, c.key))}</td>
          <td class="py-[5px] text-right font-mono text-provenance text-ink-muted">${esc(c.analysisId)}${
            c.when ? ` · ${esc(ago(c.when))}` : ''}</td>
        </tr>`;
      }).join('')}
    </table>`).join('');
}

/** Findings (repo-only groupings carry these; database/filesystem's one-
 *  analysis-per-board synthesis rarely does) -- unresolved first, same
 *  glyph vocabulary as before. */
function boardFindingsHtml(board) {
  const findings = [];
  for (const a of board.analyses || []) {
    const res = a.results || {};
    if (Array.isArray(res.findings) && res.findings.length) {
      for (const f of res.findings) findings.push({ ...f, analysis_id: a.analysis_id, when: a.last_surveyed_at });
    }
  }
  if (!findings.length) return '';
  findings.sort((x, y) => {
    const ux = UNRESOLVED_LABELS.has(String(x.label || '').toLowerCase()) ? 0 : 1;
    const uy = UNRESOLVED_LABELS.has(String(y.label || '').toLowerCase()) ? 0 : 1;
    return ux - uy;
  });
  return `
    <div class="mt-s3 text-caps uppercase tracking-caps text-ink-muted">Findings · unresolved first</div>
    ${findings.map((f) => {
      const c = findingGlyph(f.label);
      return `<button type="button" class="flex w-full items-baseline gap-s2 border-0 border-b border-rule bg-transparent px-0 py-s2 text-left"
        data-measure="${esc(f.analysis_id)}" data-check="${esc(f.check_name || '')}"
        data-title="${esc((f.check_name || f.analysis_id).replace(/_/g, ' '))}"
        data-summary="${esc(f.summary || '')}" data-when="${esc(f.when || '')}">
        <span class="w-[16px] shrink-0 ${c.tone}" title="${esc(c.label)}">${c.glyph}</span>
        <span class="min-w-0 flex-1 text-ink">
          <strong class="font-semibold">${
            f.check_name
              ? `${esc(f.check_name.replace(/_/g, ' '))}${f.label ? ` — ${esc(humanLabel(f.label))}` : ''}`
              : esc(humanLabel(f.label) || f.analysis_id)}.</strong>
          ${f.summary ? ` ${tnum(esc(f.summary))}` : ''}
          <span class="block text-provenance text-ink-muted"
            data-delta="${esc(f.analysis_id)}|${esc(f.check_name || '')}">·</span></span>
        <span class="shrink-0 font-mono text-provenance text-ink-muted">${esc(f.analysis_id)}${
          f.when ? ` · ${esc(ago(f.when))}` : ''} ›</span>
      </button>`;
    }).join('')}`;
}

/**
 * The Relationship Graph (and, generalized, any other) card body's inline
 * diagram -- reuses `factGraphviz`/`factMermaid` (envelope.js) UNCHANGED,
 * by wrapping the board's own `a.results` in the same `{facts: [{value}]}`
 * shape those functions already read from a Questions-tab envelope. Those
 * two functions read `f.value.graphviz`/`f.value.mermaid`; `a.results` IS
 * that `value` -- the SAME results_reader backs both the Questions
 * envelope's fact and this board's `a.results` (`facts.py`'s `_read_
 * results` and `workflows.analysis._read_analyses` both call the entry in
 * `*_ANALYSIS_RESULTS_MAP`) -- so no backend change or duplicate reader was
 * needed for this. Not hardcoded to `db_relationship_graph`: any analysis
 * in the board whose results carry a `graphviz` or `mermaid` field gets the
 * same treatment, first one found.
 */
function boardDiagramHtml(board) {
  if (!board) return { html: '', mount: null };
  for (const a of board.analyses || []) {
    const envelope = { facts: [{ value: a.results, analysis_id: a.analysis_id, last_run_at: board.last_surveyed_at }] };
    const gv = factGraphviz(envelope);
    if (gv) return { html: graphvizCardHtml(board.id, gv), mount: (root) => bindGraphvizCard(root, board.id, gv) };
    const mm = factMermaid(envelope);
    if (mm) {
      const uid = `ba-diagram-${esc(board.id)}`;
      return {
        html: `<div class="mt-s3 rounded-sm border border-rule-strong p-s3">
          <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Diagram</div>
          <div id="${uid}" class="min-h-[220px]"></div>
        </div>`,
        mount: (root) => {
          const container = root.querySelector(`#${uid}`);
          if (container) renderDiagramInto(container, { mermaid: mm.source }, uid).catch((err) => {
            container.innerHTML = `<div class="text-caveat text-state-warn">This could not be rendered: ${esc(err.message)}</div>`;
          });
        },
      };
    }
  }
  return { html: '', mount: null };
}

/** The three-zoom-level Relationship Graph card body -- schema map / whole
 *  database (or its own named fallback state) / a per-schema select --
 *  the SAME affordances the Questions-tab Evidence rail offers
 *  (`showEvidence`'s `graphvizBlock`, ~line 7200), rendered inline instead
 *  of behind a rail button since the gate asks this card's BODY to show the
 *  graph, not merely link to it. The schema map renders immediately on
 *  mount (no extra click) since it "always renders, any database size"
 *  (that function's own comment); the other two levels swap the same
 *  mount point's content in place via `renderDiagramInto` -- the identical
 *  renderer the Evidence rail's own "open diagram in pane" path uses (see
 *  that function's header comment), so this is not a second implementation
 *  of diagram rendering. */
function graphvizCardHtml(boardId, gv) {
  const schemaNames = Object.keys(gv.bySchema).sort();
  const uid = `ba-graphviz-${esc(boardId)}`;
  return `
    <div class="mt-s3 rounded-sm border border-rule-strong p-s3" data-graphviz-card="${esc(boardId)}">
      <div class="mb-s2 flex flex-wrap items-center gap-s2">
        <span class="text-caps uppercase tracking-caps text-ink-muted">Relationship graph
          ${gv.tableCount != null ? `· <span class="tnum">${gv.tableCount}</span> tables` : ''}</span>
        <div class="ml-auto flex flex-wrap gap-s2">
          <button type="button" data-graphviz-level="schema_map"
            class="cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[2px] text-chip text-accent-ink"
            >${icon('maximize-2', { size: 13 })} Schema map</button>
          ${gv.full
            ? `<button type="button" data-graphviz-level="full"
                 class="cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[2px] text-chip text-accent-ink"
                 >${icon('maximize-2', { size: 13 })} Whole database</button>`
            : gv.fallbackReason
              ? `<span class="text-chip text-state-warn">${esc(gv.fallbackReason)}</span>`
              : ''}
          ${schemaNames.length ? `<div class="relative">
              <select data-graphviz-schema
                   class="w-full cursor-pointer appearance-none rounded-sm border border-accent bg-transparent px-[10px] py-[2px] pr-[26px]
                          text-chip text-accent-ink hover:bg-accent-tint focus:outline-none focus:ring-1 focus:ring-accent">
                <option value="">Open a schema…</option>
                ${schemaNames.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
              </select>
              <span class="pointer-events-none absolute right-[8px] top-1/2 -translate-y-1/2 text-accent-ink">${icon('chevron-down', { size: 13 })}</span>
            </div>` : ''}
        </div>
      </div>
      <div id="${uid}" class="min-h-[220px] overflow-hidden rounded-sm border border-rule" style="height:min(50vh,480px)"></div>
      <div class="mt-s1 text-provenance text-ink-muted">${esc(gv.analysisId)}${
        gv.lastRun ? ` · run ${esc(ago(gv.lastRun))}` : ''} · rendered by Kroki, no retrieval</div>
    </div>`;
}
function bindGraphvizCard(root, boardId, gv) {
  const card = root.querySelector(`[data-graphviz-card="${boardId}"]`);
  if (!card) return;
  const uid = `ba-graphviz-${boardId}`;
  const mount = card.querySelector(`#${uid}`);
  const render = (source) => {
    if (!mount) return;
    renderDiagramInto(mount, { graphviz: source }, uid).catch((err) => {
      mount.innerHTML = `<div class="p-s2 text-caveat text-state-warn">This could not be rendered: ${esc(err.message)}</div>`;
    });
  };
  render(gv.schemaMap);   // the schema map "always renders, any database size" -- open by default
  card.querySelectorAll('[data-graphviz-level]').forEach((btn) => {
    btn.addEventListener('click', () => render(btn.dataset.graphvizLevel === 'full' ? gv.full : gv.schemaMap));
  });
  card.querySelector('[data-graphviz-schema]')?.addEventListener('change', (e) => {
    if (e.target.value) render(gv.bySchema[e.target.value]);
  });
}

/** The header time of a By-analysis card, from the analysis's OWN run.
 *  "run <when>" only when this card has a result row; otherwise "not run",
 *  or -- when the resource's survey ran but this step produced nothing --
 *  "survey ran <when> · no result for this step". The resource-wide survey
 *  time is never shown as this card's run (it contradicted the body's
 *  "Not run yet." cue). */
export function boardRunWords(board) {
  if (!board) return '';
  if (board.has_results) {
    const when = board.last_run_at || board.last_surveyed_at;
    return when ? `run ${ago(when)}` : 'run';
  }
  return board.last_surveyed_at
    ? `survey ran ${ago(board.last_surveyed_at)} · no result for this step`
    : 'not run';
}

/** One card's body -- headline first, description collapsed, findings,
 *  grouped COUNTS, and (REPLY §2's card-anatomy) a band boundary rather
 *  than a box: space above, a strong top rule, name-size heading. */
export function byAnalysisCardHtml(slug, boardId, catalogTitle, catalogDescription, entry, disagreeing, defaultOpen) {
  const board = entry.status === 'done' ? entry.board : null;
  const open = byAnalysisCardOpen(slug, boardId, defaultOpen);
  const glyphKey = boardStateKey(entry);
  const glyph = glyphKey ? stateEntry(glyphKey)
    : entry.status === 'timeout' ? { glyph: '⏸', tone: 'text-state-warn', word: 'still reading' }
    : { glyph: '◔', tone: 'text-ink-muted', word: 'reading' };
  const headline = entry.status === 'loading'
    ? '<span class="text-ink-muted">reading…</span>'
    : entry.status === 'timeout'
      ? `<span class="text-state-warn">still reading — <button type="button" class="underline" data-retry-board="${esc(boardId)}">open to load</button></span>`
      : entry.status === 'error'
      ? `<span class="text-state-warn">This could not be read: ${esc(entry.error || '')}</span>`
      : (boardHeadlineHtml(board) || (board && board.has_results
          ? '<span class="text-ink-muted">ran; no summary reader yet.</span>'
          : '<span class="text-ink-muted">Not run yet.</span>'));
  const runWhen = boardRunWords(board);
  const overall = board && (board.analyses || []).find((a) => typeof (a.results || {}).overall === 'number');
  const { html: diagramHtml } = board ? boardDiagramHtml(board) : { html: '' };
  return `
    <details data-by-analysis-card="${esc(boardId)}" class="mt-s6 border-t-2 border-rule-strong pt-s3" ${open ? 'open' : ''}>
      <summary class="flex cursor-pointer list-none flex-wrap items-baseline gap-s2">
        <span class="${glyph.tone} font-glyph" title="${esc(glyph.word)}" aria-label="${esc(glyph.word)}">${glyph.glyph}</span>
        <span class="font-heading text-name font-normal text-ink">${esc(catalogTitle)}</span>
        <span class="ml-auto text-provenance text-ink-muted">${esc(runWhen)}</span>
      </summary>
      <div class="mt-s2 max-w-[70ch] text-answer text-ink">${headline}</div>
      ${catalogDescription ? `
        <details class="mt-s2">
          <summary class="cursor-pointer text-caveat text-accent-ink">what it does ▸</summary>
          <p class="mt-s1 max-w-[70ch] text-caveat text-ink-muted">${esc(catalogDescription)}</p>
        </details>` : ''}
      ${overall ? headlineHtml(overall) : ''}
      ${boardId === 'dependencies' ? '<div class="mt-s2" data-dependency-table-host></div>' : ''}
      ${board ? boardFindingsHtml(board) : ''}
      ${board ? boardCountsHtml(board, disagreeing) : ''}
      ${diagramHtml}
    </details>`;
}

/** The Shared Names block (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3):
 *  every shared name, once, not per card.
 *
 *  Reworded 2026-09-28 per the owner's live coco_pharma gate feedback ("I
 *  don't know what '1 DISAGREE' means"): the header used to read "N
 *  DISAGREE", a count that read as a verdict on its own. The inclusion
 *  criterion was always "carried by more than one analysis" (`shared`,
 *  `collectMeasures`) -- whether those analyses' values actually differ is
 *  now said in words, per row, not folded into a header count. No "DISAGREE"
 *  string appears anywhere in this block any more. */
export function sharedNamesHtml(shared, disagreeing) {
  if (!shared.size) return '';
  return `
    <div class="mb-s5 rounded-sm border border-rule-strong p-s3" data-shared-names>
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Shared names ·
        <span class="tnum">${shared.size}</span> name${shared.size === 1 ? '' : 's'} carried by more than one analysis</div>
      ${[...shared.entries()].map(([key, rec]) => {
        const differs = disagreeing.has(key);
        return `
        <div class="mb-s1 text-caveat text-ink">
          <span class="${differs ? 'text-state-warn' : 'text-ink-muted'}">${differs ? '⚠' : '·'}</span> <strong class="font-semibold">${esc(key.replace(/_/g, ' '))}</strong>
          · ${rec.map((x) => `${esc(x.analysis)} <span class="tnum">${esc(measureDisplay(x, key))}</span>`).join(' · ')}
          <span class="block text-provenance text-ink-muted">${differs
            ? 'different measures share a name -- not necessarily wrong, likely worth a rename'
            : 'these measures share a name and agree'}</span>
        </div>`;
      }).join('')}
    </div>`;
}

/** The contents board / table of contents -- sticky, click-to-jump, and
 *  (BRIEF's "Progressive render") the ONE line that says how much is still
 *  outstanding. Re-rendered on every board settling, so the "still reading
 *  N of M" count and each row's glyph/headline stay live. */
export function renderByAnalysisContents(slug, boards, boardState, settled, total, boardQuestions) {
  const contentsEl = $('by-analysis-contents');
  if (!contentsEl) return;
  const ran = boards.filter((b) => (boardState.get(b.id) || {}).board && boardState.get(b.id).board.has_results).length;
  const neverRun = boards.filter((b) => {
    const e = boardState.get(b.id);
    return e && e.status === 'done' && e.board && !e.board.has_results;
  }).length;
  const stillReading = total - settled;
  const rows = boards.map((b) => {
    const entry = boardState.get(b.id) || { status: 'loading' };
    const glyphKey = boardStateKey(entry);
    const glyph = glyphKey ? stateEntry(glyphKey)
      : entry.status === 'timeout' ? { glyph: '⏸', tone: 'text-state-warn', word: 'still reading' }
      : { glyph: '◔', tone: 'text-ink-muted', word: 'reading' };
    // The board's own headline once its read has landed; before that, the
    // matching question's ALREADY-RESOLVED envelope, if one is sitting in
    // state.answers -- see envelopeHeadlineText's own doc for exactly when
    // that is (and isn't) the case. Never a fresh fetch either way. A board
    // that has given up waiting (status 'timeout') has nothing new to show
    // either, so it falls through to the same envelope fallback a loading
    // board uses.
    const headlineText = entry.status === 'done'
      ? boardHeadlineText(entry.board)
      : envelopeHeadlineText(boardQuestions.get(b.id), state.answers);
    const board = entry.status === 'done' ? entry.board : null;
    const runWhen = board ? boardRunWords(board)
      : entry.status === 'loading' ? 'reading…'
      : entry.status === 'timeout' ? 'still reading — open to load' : '';
    return `<button type="button" data-jump-board="${esc(b.id)}"
      class="flex w-full items-baseline gap-s2 border-0 border-b border-rule bg-transparent px-0 py-[3px] text-left">
      <span class="w-[16px] shrink-0 ${glyph.tone} font-glyph" title="${esc(glyph.word)}" aria-label="${esc(glyph.word)}">${glyph.glyph}</span>
      <span class="shrink-0 text-caveat text-ink">${esc(b.title)}</span>
      <span class="min-w-0 flex-1 truncate text-caveat text-ink-muted">${headlineText ? tnum(esc(headlineText)) : ''}</span>
      <span class="shrink-0 text-provenance text-ink-muted">${esc(runWhen)}</span>
    </button>`;
  }).join('');
  contentsEl.innerHTML = `
    <div class="sticky top-0 z-10 -mx-s1 border-b border-rule-strong bg-paper px-s1 pb-s2">
      <div class="mb-s1 text-caveat text-ink-muted">
        <span class="tnum">${boards.length}</span> analyses ·
        <span class="tnum">${ran}</span> ran ·
        <span class="tnum">${neverRun}</span> never run
        ${stillReading > 0
          ? ` · <span class="text-accent-ink">still reading <span class="tnum">${stillReading}</span> of <span class="tnum">${total}</span></span>`
          : ''}
      </div>
      <div>${rows}</div>
    </div>`;
  contentsEl.querySelectorAll('[data-jump-board]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = document.querySelector(`[data-by-analysis-card="${btn.dataset.jumpBoard}"]`);
      if (!card) return;
      card.open = true;
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

// Exported (no logic change -- `export` keyword only) so the node/jsdom
// render harness (frontend-build/test-harness/) can drive the REAL By-
// analysis pane -- including its 30s give-up timer and `retryBoard` click
// handler -- against a stubbed fetch, rather than asserting against the
// source text (BOARD-SUMMARY-READ-COST-IMPLEMENTED.md's own flagged gap).
// See NEXT-RENDER-HARNESS-IMPLEMENTED.md for the pattern.
export async function loadByAnalysisPane() {
  const el = $('content');
  // Curate's By analysis is the Analytic Library's curation_coverage (extensibility brief 2b): one read, and the
  // routine's own groups drawn as it returned them. A failed read is "could not read", never a table of zeros.
  if (state.stage === 'curate') {
    const slug = state.selectedSlug;
    const entityType = apiEntityType(state.resourceType);
    const token = ++dashToken;
    const live = () => token === dashToken && state.subTab === 'by_analysis' && slug === state.selectedSlug;
    el.innerHTML = `${subTabsHtml()}<div id="curate-coverage" data-curate-coverage class="text-caveat text-ink-muted">Reading…</div>`;
    bindSubTabs();
    let result = null;
    try { result = await getCurationCoverage(entityType, slug); } catch (err) { result = null; }
    if (!live()) return;
    $('curate-coverage').innerHTML = coverageHtml(result);
    return;
  }
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  const stage = state.stage;
  const entityType = apiEntityType(state.resourceType);

  // Its own token: an earlier click's slow read must not overwrite a
  // later one's -- the OLD single dashboards read cost 109s on Analysis;
  // now every board fetches independently, so this guards each one.
  const token = ++dashToken;
  const live = () => token === dashToken && state.subTab === 'by_analysis';

  el.innerHTML = subTabsHtml() + `
    <div class="mb-s3 flex flex-wrap items-baseline gap-s3">
      <span class="text-caps uppercase tracking-caps text-ink-muted">Survey results, by analysis · ${esc(stage)}</span>
    </div>
    <div id="by-analysis-contents" class="text-caveat text-ink-muted">Reading the analysis catalog…</div>
    <div id="by-analysis-shared"></div>
    <div id="by-analysis-cards"></div>`;
  bindSubTabs();

  // THE FAST PATH: for database/filesystem, a board's id IS its
  // analysis_id, so `state.analyses` (fetched at boot, `listAnalyses` --
  // see `start()`) already names every board with no network call at all.
  // repo's dashboards group several analyses under one id state.analyses
  // doesn't carry, so repo alone falls back to the cheap boards-catalog
  // endpoint below.
  let catalogBoards = null;
  if (entityType !== 'repo' && Array.isArray(state.analyses) && state.analyses.length) {
    catalogBoards = state.analyses
      .filter((a) => (a.intent || '').toLowerCase() === stage)
      .map((a) => ({ id: a.id, title: a.name || a.id, description: a.description || '' }));
  }
  if (!catalogBoards || !catalogBoards.length) {
    try {
      const catalog = await listSurveyResultBoards(slug, stage, { entityType });
      if (!live()) return;
      catalogBoards = (catalog.boards || []).map((b) => ({ id: b.id, title: b.title, description: b.description }));
    } catch (err) {
      if (live()) $('by-analysis-contents').innerHTML =
        `<span class="text-state-warn">The analysis catalog could not be read: ${esc(err.message)}</span>`;
      return;
    }
  }
  if (!live()) return;
  if (!catalogBoards.length) {
    $('by-analysis-contents').innerHTML = `No dashboard is registered for ${esc(stage)}.`;
    return;
  }

  // Per-board state, keyed by board id -- 'loading' until its own fetch
  // settles. Paint the contents board and every card's skeleton NOW, before
  // a single board's own data has been requested.
  const boardState = new Map(catalogBoards.map((b) => [b.id, { status: 'loading', board: null }]));
  let orderedBoards = catalogBoards;
  // Which question names each board -- filled in once getQuestions() (below)
  // resolves. Empty until then, which is fine: envelopeHeadlineText just has
  // nothing to look up yet, same as any other board with no matching
  // question.
  let boardQuestions = new Map();
  const paintAll = (final = false) => {
    if (!live()) return;
    const { shared, disagreeing } = collectMeasures(orderedBoards, boardState);
    const settled = [...boardState.values()].filter((e) => e.status !== 'loading').length;
    renderByAnalysisContents(slug, orderedBoards, boardState, settled, orderedBoards.length, boardQuestions);
    $('by-analysis-shared').innerHTML = sharedNamesHtml(shared, disagreeing);
    const cardsEl = $('by-analysis-cards');
    cardsEl.innerHTML = orderedBoards.map((b, i) => byAnalysisCardHtml(
      slug, b.id, b.title, b.description, boardState.get(b.id), disagreeing,
      i === 0 || orderedBoards.length <= 3,
    )).join('');
    cardsEl.querySelectorAll('[data-by-analysis-card]').forEach((card) => {
      card.addEventListener('toggle', () => setByAnalysisCardOpen(slug, card.dataset.byAnalysisCard, card.open));
    });
    // Brief section 3: the dependencies board carries ONE table with a kind column (read-only here;
    // confirming a runtime row happens under Curate's "how it relates").
    cardsEl.querySelectorAll('[data-dependency-table-host]').forEach((h) => {
      mountDependencyTable(h, slug, { confirmable: false, me: '' });
    });
    cardsEl.querySelectorAll('[data-measure]').forEach((n) => {
      n.addEventListener('click', () => openMeasurementDetail({
        slug,
        analysisId: n.dataset.measure,
        title: n.dataset.title || n.dataset.measure,
        metric: n.dataset.metric || '',
        summary: n.dataset.summary || '',
        when: n.dataset.when || '',
      }));
    });
    for (const n of cardsEl.querySelectorAll('[data-delta]')) {
      const [analysisId] = n.dataset.delta.split('|');
      deltaFor(slug, analysisId).then((text) => {
        if (!live()) return;
        n.textContent = text || '';
        n.className = text === 'first measurement' ? 'block text-provenance text-ink-muted' : 'block text-provenance text-ink';
      });
    }
    orderedBoards.forEach((b) => {
      const entry = boardState.get(b.id);
      if (entry.status === 'done' && entry.board) {
        const { mount } = boardDiagramHtml(entry.board);
        if (mount) mount(cardsEl);
      }
    });
    cardsEl.querySelectorAll('[data-retry-board]').forEach((n) => {
      n.addEventListener('click', () => retryBoard(n.dataset.retryBoard));
    });
  };
  paintAll();   // visible now -- no dashboards call has been made yet.

  // Refine the order in the background (question order, per the brief's
  // "Order" rule) -- a fast, catalog-only call, so this rarely changes what
  // the reader already sees, but nothing above depended on waiting for it.
  getQuestions(slug, { phase: stage, entityType }).then((checklist) => {
    if (!live()) return;
    const questions = checklist.questions || [];
    orderedBoards = orderBoardsByQuestions(orderedBoards, questions);
    // Same board/question matching `orderBoardsByQuestions` just used, kept
    // around so the contents row's headline fallback (envelopeHeadlineText)
    // can look up each board's question too -- see renderByAnalysisContents.
    boardQuestions = boardQuestionMap(orderedBoards, questions);
    paintAll();
  }).catch(() => { /* ordering is a nicety -- catalog order stands without it */ });

  // Now the slow part -- one fetch per board, in parallel, each filling its
  // own card the moment it lands. No board's fetch waits on another's --
  // but each board's own read is genuinely expensive (`db_derived`-backed
  // readers recompute from stored rows on every call, same cost the OLD
  // single sweep paid per analysis_id), so firing every board at once would
  // trade "one 109s block" for "N concurrent heavy reads fighting over the
  // same thread pool" -- measured live switching stages quickly: an
  // abandoned stage's in-flight board reads kept running server-side (this
  // pane only ignores their RESULT via `live()`, it does not cancel the
  // request) and starved even the cheap boards-catalog call behind them.
  // BY_ANALYSIS_MAX_CONCURRENT_READS bounds how many board reads run at
  // once, and `live()` is checked before EACH one starts (not just before
  // using its result), so switching away stops new reads from being
  // queued at all rather than merely discarding their answers.
  const queue = [...catalogBoards];

  // Manual "open to load" trigger for a card the automatic worker gave up
  // spinning on (BY_ANALYSIS_BOARD_TIMEOUT_MS) -- an independent fetch for
  // just this one board, outside the bounded queue above, so it isn't
  // waiting on a BY_ANALYSIS_MAX_CONCURRENT_READS slot the abandoned
  // worker read may still be holding. If that original read does
  // eventually resolve too, whichever of the two settles LAST simply
  // overwrites boardState with its own (equally correct) answer -- both
  // fetch the same board_id. Bound to `[data-retry-board]` clicks in
  // paintAll above (referenced there only lazily, at click time, so
  // declaring it here rather than earlier changes nothing about when a
  // click can actually reach it).
  const retryBoard = async (boardId) => {
    if (!live()) return;
    boardState.set(boardId, { status: 'loading', board: null });
    paintAll();
    let already = false;
    const giveUpTimer = setTimeout(() => {
      if (already || !live()) return;
      boardState.set(boardId, { status: 'timeout', board: null });
      paintAll();
    }, BY_ANALYSIS_BOARD_TIMEOUT_MS);
    try {
      const data = await getSurveyDashboards(slug, stage, { includeEmpty: true, entityType, boardId });
      already = true;
      clearTimeout(giveUpTimer);
      if (!live()) return;
      const board = (data.dashboards || [])[0] || null;
      boardState.set(boardId, { status: 'done', board });
    } catch (err) {
      already = true;
      clearTimeout(giveUpTimer);
      if (!live()) return;
      boardState.set(boardId, { status: 'error', board: null, error: err.message });
    }
    paintAll();
  };

  const worker = async () => {
    while (queue.length) {
      if (!live()) return;
      const b = queue.shift();
      // BY_ANALYSIS_BOARD_TIMEOUT_MS: give up SPINNING on this one card if
      // it hasn't settled in time -- the safety net for "board_summary
      // missing/stale AND the fallback recompute is still slow". The fetch
      // itself is not cancelled (see that constant's own comment); a
      // settled `already` flag stops the timeout from clobbering a result
      // that arrived just as it fired.
      let already = false;
      const giveUpTimer = setTimeout(() => {
        if (already || !live()) return;
        boardState.set(b.id, { status: 'timeout', board: null });
        paintAll();
      }, BY_ANALYSIS_BOARD_TIMEOUT_MS);
      try {
        const data = await getSurveyDashboards(slug, stage, { includeEmpty: true, entityType, boardId: b.id });
        already = true;
        clearTimeout(giveUpTimer);
        if (!live()) return;
        const board = (data.dashboards || [])[0] || null;
        boardState.set(b.id, { status: 'done', board });
      } catch (err) {
        already = true;
        clearTimeout(giveUpTimer);
        if (!live()) return;
        boardState.set(b.id, { status: 'error', board: null, error: err.message });
      }
      paintAll();
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(BY_ANALYSIS_MAX_CONCURRENT_READS, catalogBoards.length) }, worker),
  );
  if (!live()) return;

  // Everything has settled -- apply the "never-run last" ordering rule now
  // that real state is known, once, so cards don't reshuffle mid-read.
  orderedBoards = neverRunLast(orderedBoards, boardState);
  paintAll(true);
}

/** The composed score, once, at size — with its own sub-scores beside it.
 *
 * The radar chart is GONE. `repository_health` composes four sub-scores on
 * 0–100; the chart plotted five different axes on 0–10, on white, in a
 * foreign typeface — two incompatible definitions of the same word forty
 * pixels apart. If it returns it plots these four, on their own scale, in
 * this app's palette, and is then a picture of the number rather than a rival
 * to it.
 *
 * The summary tiles are gone for the same reason: they rendered `Health
 * 82/100` while the card below said `82.2` — one measurement at two
 * precisions, which costs trust in both.
 */
function headlineHtml(a) {
  const r = a.results || {};
  const subs = Object.entries(r)
    .filter(([k, v]) => k !== 'overall' && typeof v === 'number')
    .slice(0, 6);
  return `<div class="mt-s3 flex flex-wrap items-baseline gap-s5 border-b border-rule pb-s3">
    <div>
      <div class="text-caps uppercase tracking-caps text-ink-muted">${
        esc((a.analysis_id || '').replace(/_/g, ' '))}</div>
      <div class="tnum font-heading text-ink" style="font-size:44px;line-height:1.05">${
        esc(fmtScalar(r.overall))}</div>
    </div>
    <div class="text-caveat text-ink">
      ${subs.map(([k, v]) => `<span class="mr-s3">${esc(k.replace(/_/g, ' '))}
        <strong class="tnum font-semibold">${esc(fmtScalar(v))}</strong></span>`).join('')}
      <div class="mt-[3px] font-mono text-provenance text-ink-muted">${esc(a.analysis_id)}${
        a.last_surveyed_at ? ` · ${esc(ago(a.last_surveyed_at))}` : ''}</div>
    </div>
  </div>`;
}

/** A stored value rendered as what it IS, not as the number that stores it.
 *
 *  `was 21438268 8d ago` on ~40 sub_resource_survey rows was the defect: that
 *  is `total_size_bytes`, and 21,438,268 is a correct number that nobody can
 *  read as 21.4 MB at a glance. The formatter had no way to know, because it
 *  was handed the value and not the name — every call site HAD the name in
 *  scope and none passed it.
 *
 *  Rules, in order:
 *  - booleans as words;
 *  - a name ending in `bytes` renders as a size (B / KB / MB / GB / TB, one
 *    decimal above KB);
 *  - an integer at or above 1,000 gets digit grouping, so a count of files is
 *    read as a count and not as a code;
 *  - everything else to one decimal, as before.
 *
 *  Only the name's SUFFIX is read. Anything cleverer — guessing a unit from a
 *  magnitude — is exactly how a byte count becomes a "score" somewhere. With
 *  no name, only the grouping rule can apply: a bare 21,438,268 is still
 *  better than 21438268, and grouping is never wrong the way a unit can be. */
function fmtScalar(v, name = '') {
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (v == null || v === '') return '';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (/bytes$/i.test(name)) return fmtBytes(n);
  if (Number.isInteger(n) && Math.abs(n) >= 1000) return n.toLocaleString('en-US');
  return String(Math.round(n * 10) / 10);
}

function fmtBytes(n) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let x = Math.abs(n);
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1; }
  const shown = i === 0 ? String(Math.round(x)) : (Math.round(x * 10) / 10).toFixed(1);
  return `${n < 0 ? '-' : ''}${shown} ${units[i]}`;
}

/** A link into the Classic UI (`/classic`), on the same resource.
 *
 *  `index.html` gained a `?resource=` reader for this — it had no deep link
 *  of any kind, so "links out preserving the resource" was not satisfiable
 *  without adding one. Additive: an unrecognised slug selects nothing and
 *  the app starts exactly as before. */
export function oldUiHref() {
  return state.selectedSlug
    ? `/classic?resource=${encodeURIComponent(state.selectedSlug)}`
    : '/classic';
}

/**
 * The state legend, with counts.
 *
 * The current UI has one across the top of its Questions tab, and dropping it
 * hurt more here than it would have there: this palette moved state from hue
 * to glyph, and a glyph vocabulary without a key is strictly less legible
 * than colour without one.
 *
 * Counted rather than static — `3 answered · 1 not run` earns the space a
 * fixed key does not — and only states actually present are listed, since a
 * key to a glyph that is not on screen is noise. Rows arriving is what moves
 * these numbers, so it re-renders with them.
 *
 * Words come from `GLYPH_STATES` (glyphs.js), not a second copy declared
 * here -- REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1: "the count line at
 * the top of each surface is the legend", and every surface's legend reads
 * the same table. Before this pass `partial` read "ran, but not at this
 * level" here (◐'s OLD, Questions-only meaning) while the work-list grid's
 * own legend called the very same glyph "partial" -- exactly the
 * disagreement the reply names. Only the ORDER is declared here; it is a
 * reading order, not a second vocabulary.
 */
const LEGEND_ORDER = [
  'answered', 'automatic', 'nothing', 'partial', 'unrun', 'human', 'needs-lens', 'no-surveyor',
  // `no_reader` counted separately from `unrun` -- DATABASE-DIRECT-FIELD-ROWS-IMPLEMENTED.md: the
  // whole point of the vocabulary fix is that a
  // reader-less direct/chart row must not be folded into the "not run"
  // count, on the legend line same as on the row glyph.
  'no_reader',
  'unclassified', 'running', 'error',
];
const LEGEND = LEGEND_ORDER.map((k) => [k, GLYPH_STATES[k].word]);

export function renderLegend() {
  const el = $('state-legend');
  if (!el) return;

  const counts = {};
  let pending = 0;
  for (const q of state.questions) {
    const env = state.answers.get(q.question);
    if (env === undefined || env === 'loading') { pending += 1; continue; }
    const st = state.runsInFlight.has(q.question) ? 'running'
      : env.__error ? 'error'
      : effectiveRowState(q, env);
    counts[st] = (counts[st] || 0) + 1;
  }

  // States that share one glyph AND one word (`no-surveyor`, `no_reader`)
  // are one legend entry with their counts summed -- one word per glyph.
  const merged = [];
  for (const [k, label] of LEGEND) {
    if (!counts[k]) continue;
    const prior = merged.find((m) => m.label === label);
    if (prior) prior.n += counts[k];
    else merged.push({ k, label, n: counts[k] });
  }
  const items = merged.map(({ k, label, n }) => `
    <span class="inline-flex items-baseline gap-[5px]">
      <span class="${tone(k, 'paper')} font-glyph" title="${esc(label)}" aria-label="${esc(label)}"
        >${GLYPH[k]}</span>
      <span class="text-ink">${esc(label)}</span>
      <span class="tnum text-ink">${n}</span>
    </span>`);

  if (pending) {
    items.push(`<span class="text-ink-muted">
      <span class="tnum">${pending}</span> still loading</span>`);
  }

  // The ordering legend sits beside the state key, not inside it: the key
  // says what the glyphs mean, this says why the rows are in this order.
  // Separated by a middle dot so it reads as a second clause, not a seventh
  // glyph.
  const order = purposeLegendHtml();
  el.innerHTML = items.length
    ? `<span class="text-caps uppercase tracking-caps text-ink-muted">Key</span>${items.join('')}${
      order ? `<span class="text-ink-muted">·</span>${order}` : ''}`
    : order;
}

export function paneMessage(title, body) {
  return `${subTabsHtml()}
    <h3 class="m-0 font-heading text-name font-normal">${esc(title)}</h3>
    <div class="my-s3 h-px bg-rule"></div>
    <p class="max-w-[70ch] text-answer text-ink">${esc(body)}</p>`;
}

async function loadPane() {
  const el = $('content');
  let scopeIntoView = false;
  // The rail is a function of (resource, stage, sub-tab): empty it, and stand
  // down its in-flight writers, the moment any of those changed.
  syncRailToSelection();

  // The perspective row is re-rendered HERE, once, for every branch below.
  //
  // It used to be re-rendered inside individual branches, and the work-list
  // branch returned before reaching one — so holding a perspective updated
  // the state and never repainted the chip. Reported as "sometimes the
  // perspective stays highlighted and sometimes not": it depended entirely on
  // which pane you were in. One call site is the fix; a branch that forgets
  // is the bug.
  renderPerspectiveRow();
  renderWorkListNav();

  // A work list is a view of a SET, so it replaces the single-resource pane
  // rather than sitting inside it. Everything else in /next reads one
  // resource at a time; this is the one surface that does not.
  if (state.workListIndex && !state.workListSlug) {
    el.innerHTML = workListIndexHtml();
    state.workListIndexNote = '';
    bindSubTabs();
    bindWorkListIndexActions(el);
    el.querySelectorAll('[data-open-wl]').forEach((b) => b.addEventListener('click', () => {
      state.workListSlug = b.dataset.openWl;
      state.workListIndex = false;
      ensureRunStageForList();
      writeUrl(); renderSidebar(); loadPane();
    }));
    writeUrl();
    return;
  }

  // §5 of REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md: a work list is a
  // bench against one stage's questions. On a RUN stage it stays open and
  // re-scopes to that stage (the branch below). A FRAME or CROSS-CUTTING
  // stage (Investigation, Understanding, Automate) asks no questions, so the
  // click goes to the stage and the list closes into the "↩ <list>" link
  // `lastWorkListSlug` drives. The class is read from `STAGES`.
  if (state.workListSlug && stageClassOf(state.stage) !== 'run') {
    const closing = state.workLists.find((w) => w.slug === state.workListSlug);
    state.lastWorkListSlug = state.workListSlug;
    state.workListSlug = null;
    state.workListIndex = false;
    // Special case: Investigation while the list is linked to an
    // investigation opens THAT investigation, its Scope section in view,
    // current or not. Only `work_lists.investigation` links them today.
    if (state.stage === 'investigation' && closing?.investigation
        && state.investigations.some((i) => i.slug === closing.investigation)) {
      openInvestigationDetail(closing.investigation);
      scopeIntoView = true;
    }
    writeUrl(); renderSidebar(); renderWorkListNav();
  }

  if (state.workListSlug) {
    state.workListIndex = false;
    if (stageClassOf(state.stage) === 'run') state.lastRunStage = state.stage;
    try {
      await openWorkList({
        el,
        // The leading tabs (stage-tabs.js) are not columns of the work-list grid.
        subTabs: SUB_TABS.filter((t) => !t.stageTab).map((t) => ({ id: t.id, label: t.label })),
        stage: state.stage,
        stageLabel: STAGES.find((s) => s.id === state.stage)?.label || state.stage,
        perspectives: state.activePerspectives,
        projects: state.projects,
        analyses: state.analyses || [],
        host: workListHost(),
        onExit: () => {
          state.lastWorkListSlug = state.workListSlug;
          state.workListSlug = null;
          writeUrl(); renderSidebar(); loadPane();
        },
      }, state.workListSlug);
    } catch (err) {
      // A pane that throws on the way in leaves whatever was there before,
      // which reads as "clicking did nothing" — reported as both "the batch
      // could not be enqueued" and "switching the stage changed nothing"
      // when the server was restarted underneath the page. Say it instead.
      el.innerHTML = `
        <h3 class="m-0 font-heading text-name font-normal">This view could not be loaded</h3>
        <div class="my-s3 h-px bg-rule"></div>
        <p class="max-w-[70ch] text-answer text-ink">${esc(err.message)}</p>
        <p class="max-w-[70ch] text-caveat text-ink-muted">
          A network-level failure here usually means the server restarted. What
          you were looking at is unchanged — nothing was written.
        </p>
        <button data-act="retry-pane"
          class="mt-s2 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px]
                 text-caveat text-accent-ink">Try again</button>`;
      el.querySelector('[data-act="retry-pane"]').addEventListener('click', () => loadPane());
    }
    writeUrl();
    return;
  }

  // FRAME-CLASS STAGES ROUTE FIRST, before any subTab branch.
  //
  // Understanding, Automate and Investigation have no sub-tabs of their own,
  // but a stage click deliberately leaves state.subTab alone (a subTab chosen
  // on another real stage survives the switch). With these checks AFTER the
  // subTab branches, Enrichment -> Context then a click on Investigation left
  // state.subTab === 'context', loadContextPane() returned early, and the
  // frame never rendered. Only the CHECK ORDER is the fix; state.subTab is
  // intentionally not reset. Work-list branches above stay first: they replace
  // the pane for any stage and a stage click does not clear them (unchanged).
  // Understanding is CHARTS.
  //
  // It was marked "not built" on the strength of having zero rows in the
  // analysis catalog and the activity log — both true, and both irrelevant
  // to the seven Plotly figures `/api/stats/{slug}/charts/*` already serves
  // for a repo. The catalog is empty; the data is not. Since the fix round
  // puts "anything over time" in the pane as a chart, this is where charts
  // live, and leaving the marker up would have been marking a surface as
  // absent while its data sat one GET away.
  if (state.stage === 'understanding') {
    if (isStageTabActive(state.subTab)) { await loadStageTabPane(); renderPerspectiveRow(); return; }
    // With the leading tabs on for this kind, a shared tab (Questions, Survey & analyses, ...) is a real pane
    // on the same rail and falls through to the sub-tab branches below, as on Curate.
    if (!stageTabsOn('understanding', state.resourceType)) {
      await loadChartsPane();
      renderPerspectiveRow();
      return;
    }
  }

  // Automate is subscriptions/schedules, not questions -- its own two-tab
  // subnav (renderAutomate(), next/stages/automate.js), same bypass shape as
  // Understanding just above. Global by default, like the current UI's
  // Schedules overview: it works with no resource selected, and filters to
  // one via its own "Just <slug>" checkbox rather than requiring a selection.
  if (state.stage === 'automate') {
    await renderAutomate();
    renderPerspectiveRow();
    return;
  }

  // Investigation is the frame, not a Questions-checklist stage — same
  // bypass shape as Understanding/Automate above, not the generic engine.
  // stages/investigation.js's own renderer (list/create/detail: members,
  // dispositions, next-steps, purposes, classification, Egeria binding)
  // replaces the old "not in /next" placeholder this branch used to print
  // for `class === 'frame'`; see that file's header comment.
  if (state.stage === 'investigation') {
    await renderInvestigation();
    renderPerspectiveRow();
    if (scopeIntoView) $('inv-scope')?.scrollIntoView?.({ block: 'start' });
    return;
  }

  // A sub-tab left over from a stage/resource where it exists (Context from
  // Enrichment, Schema Inventory from a database) must not drive the pane
  // here: revert it to this stage's default and make the URL say so.
  if (reconcileSubTabForStage()) writeUrl();
  syncRailToSelection();   // the reconcile may have moved the sub-tab
  if (state.subTab === 'schema_inventory') { await loadSchemaInventoryPane(); return; }
  // Curate's and Understanding's leading tabs (Brief S0): loader stubs until the slices put the sections in.
  if (isStageTabActive(state.subTab)) { await loadStageTabPane(); return; }
  // ENRICHMENT-E1-CONTEXT-TAB: Context is its own pane, not a Questions-
  // engine variant — see loadContextPane() below.
  if (state.subTab === 'context') { await loadContextPane(); return; }
  // Survey & analyses on Enrichment is the amendment's MAP (project owner,
  // 2026-09-29), not the generic per-stage survey-candidates pane every
  // other stage uses: "No analysis runs at the Enrichment stage from a
  // survey read. What you supply here unlocks: ..." — see
  // loadEnrichmentAnalysesMapPane() below.
  if (state.subTab === 'survey' && state.stage === 'enrichment') { await loadEnrichmentAnalysesMapPane(); return; }
  if (state.subTab === 'survey') { await loadSurveyPane(); return; }
  // 'dashboard' is a retired tab id -- a bookmarked/shared URL from before
  // the stage-page round lands on its nearest surviving surface rather than
  // the deferred-pane message a stranger id would get.
  if (state.subTab === 'dashboard') { state.subTab = 'by_analysis'; writeUrl(); syncRailToSelection(); }
  // By analysis on Enrichment says the same thing as the map, in one line,
  // and links there (reply §1) — not the generic per-intent board.
  if (state.subTab === 'by_analysis' && state.stage === 'enrichment') { await loadEnrichmentByAnalysisSummaryPane(); return; }
  if (state.subTab === 'by_analysis') { await loadByAnalysisPane(); return; }
  if (state.subTab === 'disposition') { await loadDispositionPane(); return; }

  if (state.subTab !== 'questions') {
    // readUrl() keeps only ids the strip offers, so this is a safe landing, not a stub pane.
    state.subTab = stageDefaultSubTab(); writeUrl(); await loadPane();
    return;
  }

  // ONE pane, parameterised by stage — not eight panes.
  //
  // The questions endpoint already takes `phase`, so serving every stage from
  // this one pane costs a query parameter. It is also the only way the state
  // vocabulary gets exercised at all: Scouting's five questions are `analysis`
  // and `direct` only, while `gap`, `human`, `mixed`, `partial` and `chart`
  // all live in the Analysis and Enrichment stages. Building Scouting alone
  // would have shipped a screen whose whole argument — that six states are
  // scannable as glyph plus sentence — could not be looked at.
  const stageDef = STAGES.find((s) => s.id === state.stage);

  // DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md §3: same read-vs-write gap as
  // the nav item above — inverted to read `built`, which actually exists.
  // The `class === 'frame'` half is unreachable today (Investigation, the
  // only frame-class entry, returns above before this line is ever
  // reached) — kept as the fallback for a FUTURE frame-class stage added
  // without its own dedicated branch, same defensive shape as `!built`
  // covering a stage nobody has written a renderer for yet.
  if (stageDef?.class === 'frame' || !stageDef?.built) {
    el.innerHTML = paneMessage(
      `${stageDef.label} · not in /next`,
      stageDef.class === 'frame'
        ? 'This is a frame, not a built pane, and has no dedicated renderer of '
          + 'its own in /next yet.'
        : 'This stage has no rows in the analysis catalog or the activity log, so '
          + 'there is nothing for a questions pane to show. It is marked here '
          + 'rather than hidden, which is the point.');
    bindSubTabs();
    renderPerspectiveRow();
    return;
  }

  {
    // The stricter repo-only backend gate that used to sit here is
    // gone as of the database/filesystem generalization (docs/Backlog.md,
    // "scouting-questions was repo-only"): `GET /api/{databases,filesystems}
    // /{slug}/questions` now exist and reach the same, already-generic
    // question_catalog_reader.get_questions() the repo route always did.
    // Only paneNeedsRepo() remains -- a resource must still be selected.
    const blocked = paneNeedsRepo();
    if (blocked) { el.innerHTML = blocked; bindSubTabs(); return; }
  }

  if (!state.selectedSlug) {
    el.innerHTML = paneMessage('Select a resource',
      'Pick a repository from the sidebar to see its question checklist.');
    bindSubTabs();
    return;
  }

  const slug = state.selectedSlug;

  primeRepoHeaderReads(slug);

  // The frame first, then per-row skeletons — rows arrive independently, and
  // a centred spinner would hide the ones that are already here.
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="mt-s3 flex flex-wrap items-baseline gap-s3 text-provenance">
      <span id="answered-count" class="tnum text-caveat text-ink-muted">loading…</span>
    </div>
    <div id="state-legend" class="mt-s2 flex flex-wrap items-baseline gap-s3 text-caveat"></div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="question-rows"></div>`;
  bindSubTabs();
  bindResourceHeader();

  // Curate uses none of the checklist reads below, so it starts NOW and does not wait behind them
  // (getQuestions, getContext and the unfiltered getQuestions run in series and cost seconds). The
  // other stages are unchanged: they need `checklist` and render after it.
  // A repository's or a database's Curate draws on its own tabs (loadStageTabPane); the Questions tab is then the shared one.
  // A file system still draws Curate in this frame.
  const curateInFrame = state.stage === 'curate' && !stageTabsOn('curate', state.resourceType);
  let curateStarted = false;
  if (curateInFrame) {
    curateStarted = true;
    renderCurate(slug).catch((err) => {
      if (slug !== state.selectedSlug) return;
      const box = $('question-rows');
      if (box) box.innerHTML = `<div class="py-s3 text-answer text-accent-ink">Curate could not be drawn: ${esc(err.message)}</div>`;
    });
  }

  let checklist;
  try {
    checklist = await getQuestions(slug, {
      phase: state.stage,
      perspectives: [...state.activePerspectives],
      purposes: currentPurposes(),
      entityType: apiEntityType(state.resourceType),
    });
  } catch (err) {
    $('question-rows').innerHTML = `<div class="py-s3 text-answer text-accent-ink">
      The checklist could not be loaded: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug) return;   // a faster click won

  state.questions = checklist.questions || [];
  rememberRationales(state.questions);

  // Human answers for this resource, so a `human` row can show what was
  // already said rather than offering a blank box over the top of it. A
  // failure here leaves the rows answerable and unanswered, which is the
  // truthful degradation: we could not read them, so we do not claim any.
  try {
    const ctx = await getContext(apiEntityType(state.resourceType), slug);
    state.contextAnswers = ctx?.question_answers || {};
    state.enrichment = ctx?.enrichment || {};
  } catch {
    state.contextAnswers = {};
    state.enrichment = {};
  }

  // The residue count needs the unfiltered total. One extra call, only when
  // a perspective is held — the number is the whole point of the chip row.
  if (state.activePerspectives.size) {
    try {
      const all = await getQuestions(slug, { phase: state.stage, entityType: apiEntityType(state.resourceType) });
      state.allQuestions = all.questions || [];
    } catch {
      // Unknown, and it must stay unknown: with no unfiltered set there is
      // no basis for either the residue count or the matches-nothing chip,
      // and guessing one from the filtered set is the bug above.
      state.allQuestions = null;
    }
  } else {
    // Nothing held means nothing filtered — the two sets are the same list.
    state.allQuestions = state.questions;
  }
  renderPerspectiveRow();

  const rows = $('question-rows');
  // Curate's screen is a review-and-commit, not a question list; it renders
  // whether or not the catalog has rows for the stage (today it has none).
  // A failed render must be visible, never an empty body: the pane used to
  // go blank here when its host element had been deleted.
  if (curateInFrame && !curateStarted) {
    renderCurate(slug).catch((err) => {
      if (slug !== state.selectedSlug) return;
      const box = $('question-rows');
      if (box) box.innerHTML = `<div class="py-s3 text-answer text-accent-ink">Curate could not be drawn: ${esc(err.message)}</div>`;
    });
  }
  // Analysis has real catalog rows (unlike Curate), so it renders through
  // the generic engine below like any other built stage, with no bypass
  // branch here -- classic's Sub-Resources sub-tab is now ported onto the
  // Survey & analyses pane's sub_resource_survey row instead of a stage-
  // level note (RULING-SUBRESOURCES-PLACEMENT.md; next/stages/analysis.js).
  if (!state.questions.length) {
    rows.innerHTML = curateInFrame ? '' : `<div class="py-s3 text-answer text-ink">
      No questions match this stage and this perspective set.
      That is a fact about the filter, not about the repository.</div>`;
    $('answered-count').textContent = curateInFrame ? 'review and commit · Curate' : `0 questions · ${stageLabel()}`;
    return;
  }

  rows.innerHTML = state.questions.map((q, i) => rowShell(q, i)).join('');
  wireHumanAnswers(rows, slug);
  // The judgement/observation form used to render inline here
  // (`renderEnrichment`, writing to `#enrichment-form`) whenever
  // `state.stage === 'enrichment'`, regardless of sub-tab — the pre-E1
  // merged Enrichment/Questions pane E0's own doc describes. It now lives
  // on the Context tab instead (`loadContextPane()`, `stages/context.js`'s
  // `renderContext`), so this branch is gone; the Questions tab under
  // Enrichment renders exactly like every other stage's Questions tab.
  updateAnsweredCount();
  renderLegend();

  // Fetch each answer independently and replace its row as it lands.
  state.answers.clear();
  state.questions.forEach((q, i) => loadAnswer(q, i, slug));
}

/* ── Answering a question that only a person can answer ───────────────────
 *
 * Seven catalog questions are Human-Supplied: do we already support these
 * dependencies, what does it cost to run, do we have the skills, does it fit
 * our monitoring / security / governance, does it fit or extend the estate.
 * They had nowhere to be stored — the Enrichment context form holds
 * environment, sensitivity, backup status and location, none of which appears
 * in the catalog at all — so the rows said "not built in /next" and stopped.
 *
 * Delegated from the container because rows re-render independently as their
 * answers land; binding per row would attach to elements that are about to be
 * replaced. Attached ONCE and marked, because the pane re-renders on every
 * perspective toggle and a second listener would save twice per click — the
 * same stacked-listener bug the sidebar toggle had.
 */
/** Redraw one question's row in place, the way an arriving answer already
 *  does elsewhere in this file. A whole-pane reload would refetch every
 *  other answer to show one that is already in hand. */
function redrawQuestionRow(question) {
  const i = state.questions.findIndex((q) => q.question === question);
  if (i >= 0) replaceRow(state.questions[i], i, state.answers.get(question));
}

// Exported for the render harness (frontend-build/test-harness/) -- same
// minimal-change pattern as `rowInner`'s own export above: only the
// `export` keyword changed, no logic. Used by
// human-question-answer-row-anatomy.test.mjs to drive the REAL click ->
// textarea -> save -> PATCH -> re-render flow, not a parallel simulation
// of it.
export function wireHumanAnswers(host, slug) {
  if (host.dataset.humanWired === '1') return;
  host.dataset.humanWired = '1';
  host.addEventListener('click', async (ev) => {
    // Open the inline editor. No `window.prompt()` — see `bodyLines`'s
    // `st === 'human'` branch: the "Answer this →"/"change" button now
    // toggles `state.editingAnswer` and re-renders this row with an
    // input+save control on it, the same shape `stages/enrichment.js`
    // already uses for judgements/observations.
    if (ev.target.closest('[data-goto-context]')) {
      openContext();
      return;
    }
    const editBtn = ev.target.closest('[data-human-edit]');
    if (editBtn) {
      state.editingAnswer = editBtn.getAttribute('data-human-edit');
      redrawQuestionRow(state.editingAnswer);
      const row = host.querySelector('[data-answer-input]');
      row?.focus();
      return;
    }
    if (ev.target.closest('[data-answer-cancel]')) {
      const q = state.editingAnswer;
      state.editingAnswer = '';
      if (q) redrawQuestionRow(q);
      return;
    }
    const saveBtn = ev.target.closest('[data-answer-save]');
    if (saveBtn) {
      const question = saveBtn.getAttribute('data-answer-save');
      const row = saveBtn.closest('[id^="qrow-"]') || host;
      const input = row.querySelector('[data-answer-input]');
      const answer = (input?.value || '').trim();
      const label = saveBtn.textContent;
      saveBtn.disabled = true;
      saveBtn.textContent = 'saving…';
      try {
        // Same bug class as openNotifyDialog's 'repo' hardcode (found live
        // 2026-09-28): this handler is wired inside the same Questions-engine
        // pane, reachable for database/filesystem resources since the repo-only
        // gate lifted, and must send the real entity_type.
        const out = await saveQuestionAnswer(apiEntityType(state.resourceType), slug, question, answer);
        const key = questionKey(question);
        // The server stamps `answered_by`/`answered_at` from the signed-in
        // identity (context.py's new PATCH .../answer, mirroring
        // save_field's author-stamping for enrichment fields) — the field
        // is never taken from the client, same reasoning as `saveEnrichmentField`.
        state.contextAnswers = { ...(state.contextAnswers || {}), [key]: out.answer };
        state.editingAnswer = '';
        redrawQuestionRow(question);
      } catch (err) {
        // Left on the button rather than raised as a page error: the failure
        // is this one save, and the rest of the checklist is unaffected.
        saveBtn.disabled = false;
        saveBtn.textContent = err.status === 401 ? 'sign in to answer' : `not saved: ${err.message}`;
        setTimeout(() => { saveBtn.textContent = label; saveBtn.disabled = false; }, 4000);
      }
    }
  });
}

// renderEnrichment()/renderEnrichmentEvidence() moved to next/stages/enrichment.js
// (PLAN-FINISH-REPOS.md Part 2, section 1) -- imported above with the other stage modules.
// Curate (component tree, catalogue-depth offer, verdict recording) moved
// to next/stages/curate.js (PLAN-FINISH-REPOS.md Part 2, section 1) --
// renderCurate is imported above with the other stage modules.

function rowKey(i) { return `qrow-${i}`; }

/* ── Purpose orders; Perspective filters ──────────────────────────────────
 *
 * The catalog carries a Purposes column — Explore, Select, Learn, Assess,
 * Certify, Deploy, Maintain, Share, Attest — and the reader has honoured it
 * since 2026-08-24: entries serving the investigation's purposes sort first,
 * the rest follow in catalog order, nothing is hidden. YAML, reader, route and
 * `getQuestions()` all carried it. This screen passed `phase` and
 * `perspectives` and not `purposes`, so the ordering never happened.
 *
 * Why ORDER and not FILTER is a recorded measurement, not a preference:
 * docs/investigation-framing-design.md §3 measured Purpose's overlap at 0.22
 * and Perspective's at 0.37 with strictly nested sets. Filtering on the axis
 * that discriminates hardest would hide the most; ordering on it puts the
 * dozen questions the task needs at the top and leaves the rest below,
 * deprioritised rather than gone. Perspective then filters, as built.
 *
 * The purposes come from the current investigation. No investigation, or one
 * with none set, means catalog order — and the legend says nothing, because
 * "not ordered" is the absence of a claim, not a claim of its own.
 */
function currentPurposes() {
  const inv = state.investigations.find((i) => i.slug === state.investigation);
  return [...(inv?.purposes || [])];
}

/** The ordering, said out loud. An ordering with no legend is
 *  indistinguishable from an arbitrary one. */
function purposeLegendHtml() {
  const purposes = currentPurposes();
  if (!purposes.length) return '';
  const lead = state.questions.filter((q) => q.derivation?.purpose_ranked).length;
  const total = state.questions.length;
  if (!total) return '';
  // Zero promoted is a real answer — this stage's questions serve none of the
  // investigation's purposes — and "0 of 7 lead" is not how anyone would say
  // it. Measured: Assessment has 0 of 7 for Explore + Learn.
  if (!lead) {
    return `<span class="text-ink-muted">none of these serve ${esc(purposes.join(', '))} ·
      catalog order</span>`;
  }
  return `<span class="text-ink-muted">ordered by purpose · ${esc(purposes.join(', '))} ·
    <span class="tnum">${lead}</span> of <span class="tnum">${total}</span> lead${
    lead < total ? ', the rest follow in catalog order' : ''}</span>`;
}

/** The boundary between the questions the investigation's purposes promoted
 *  and the ones they did not. Rendered ONCE, at the first unranked row, and
 *  only when both groups are non-empty — a rule above the first row or below
 *  the last says nothing. Without this the second group reads as a
 *  continuation, or as an oversight; with it, it reads as what it is. */
function purposeBreakHtml(i) {
  if (!currentPurposes().length) return '';
  const q = state.questions[i];
  const prev = state.questions[i - 1];
  if (!prev || !prev.derivation?.purpose_ranked || q.derivation?.purpose_ranked) return '';
  return `<div class="mt-s2 mb-s1 flex items-baseline gap-s2 text-caps uppercase tracking-caps text-ink-muted">
    <span>Not among this investigation's purposes</span>
    <span class="h-px flex-1 bg-rule"></span>
  </div>`;
}

function rowShell(entry, i) {
  const last = i === state.questions.length - 1;
  return `${purposeBreakHtml(i)}<div id="${rowKey(i)}" class="py-s3 ${last ? '' : 'border-b border-rule'}">
    ${rowInner(entry, i, 'loading')}
  </div>`;
}

/** One question row. The layout is fixed across states so a column of rows
 *  scans: glyph at 22px, everything below indented to match. */
// Exported for the render harness (frontend-build/test-harness/) — same
// pattern as `surveyRowHtml`/`schemaTreeHtml`/`tableHtml`/`filterSchemaTree`
// above: only the `export` keyword changed, no logic. Used by
// human-question-answer-row-anatomy.test.mjs to render a real question row
// through the exact function `loadPane()` uses.
/** Catalogued questions whose answer now has a READER: a Context observation
 *  row a person fills (ENRICHMENT-E3 §4). The mapping and the state word live
 *  in context-recorded.js so every surface says the same thing. */
function contextRecorded(entry) {
  return contextRecordedSpec(entry.question, apiEntityType(state.resourceType));
}

/** The ONE state function every Questions-pane consumer reads (row glyph,
 *  the KEY/legend counts, the markdown copy): `rowState`, except that a row
 *  Context records is `answered`/`human`, never `no_reader`. */
function effectiveRowState(entry, env) {
  const st = rowState(entry, env);
  if (st !== 'no_reader') return st;
  return contextRecordedState(entry.question, apiEntityType(state.resourceType), state.enrichment || {}) || st;
}

export function rowInner(entry, i, env) {
  // ALL of them, wrapping — not just the first. Seeing that a question
  // carries four perspectives is how you learn the axis barely filters, and
  // the first-only version hid exactly that.
  const perspectives = entry.perspectives || [];
  const running = state.runsInFlight.get(entry.question);
  const pending = state.pendingProposals.get(entry.question);
  const st = running ? 'running'
    // A pending proposal takes over the row before `running` even starts --
    // nothing has been dispatched yet, which is the entire point of §17.1:
    // the ask happens BEFORE the run, not as a run that then fails.
    : pending ? 'proposal'
    : env === 'loading' ? 'loading'
    : env && env.__error ? 'error'
    : effectiveRowState(entry, env);

  const glyph = GLYPH[st] || '·';
  // Glyph AND colour. The glyph survives printing, greyscale and colour
  // blindness and is what the legend keys; the hue is what makes a column of
  // rows scannable for the exceptions. Neither is doing the job alone.
  //
  // The TITLE stays `ink` in every state. Colouring the state is not the same
  // as fading the question, and an earlier version put unrun titles in
  // `ink-muted`, which is de-emphasis by fading text toward the ground.
  const glyphColor = tone(st, 'paper');

  const perspectiveTags = perspectives.length
    ? `<span class="ml-auto flex flex-wrap justify-end gap-[4px]">${perspectives.map((pv) =>
        `<span class="rounded-pill border border-rule-strong px-2 py-[1px] text-caps text-ink-muted"
          >${esc(pv)}</span>`).join('')}</span>`
    : '';
  const tag = st === 'no-surveyor'
    ? `<span class="ml-auto flex flex-wrap justify-end gap-[4px]">
        <span class="rounded-pill border border-dashed border-state-gap px-2 py-[1px] text-caps text-state-gap">no reader yet</span>
        ${perspectives.map((pv) => `<span class="rounded-pill border border-rule-strong px-2 py-[1px] text-caps text-ink-muted"
          >${esc(pv)}</span>`).join('')}
      </span>`
    : st === 'no_reader'
    ? `<span class="ml-auto flex flex-wrap justify-end gap-[4px]">
        <span class="rounded-pill border border-dashed border-state-gap px-2 py-[1px] text-caps text-state-gap">no reader yet</span>
        ${perspectives.map((pv) => `<span class="rounded-pill border border-rule-strong px-2 py-[1px] text-caps text-ink-muted"
          >${esc(pv)}</span>`).join('')}
      </span>`
    : perspectiveTags;

  const glyphWord = (GLYPH_STATES[st] || {}).word || '';
  const head = `<div class="flex flex-wrap items-baseline gap-[9px]">
      <span class="w-[13px] ${glyphColor} font-glyph text-question"${
        st === 'loading' ? '' : ` title="${esc(glyphWord)}" aria-label="${esc(glyphWord)}"`
      }>${st === 'loading' ? '' : glyph}</span>
      <span ${st === 'loading' ? '' : `data-ask-chat="${i}" title="Answer this in the chat panel"`}
        class="font-heading text-question font-semibold text-ink ${st === 'loading' ? '' : 'cursor-pointer'}">${esc(entry.question)}</span>
      ${st === 'loading' ? '' : `<button type="button" data-ask-chat="${i}" title="Answer this in the chat panel, from the same answer this row shows"
        class="cursor-pointer bg-transparent p-0 text-provenance text-accent-ink underline">ask in chat ›</button>`}
      ${tag}
    </div>`;

  return head + (st === 'loading' ? '' : whyHereHtml(entry, i)) + bodyLines(entry, i, st, env);
}

/** "Why is this question here": the chain Perspective + Purpose -> question -> the analyses that answer it,
 *  every line read from the catalog entry the row was drawn from (the server computes `derivation` in
 *  question_catalog_reader.get_questions). A line that the entry does not carry says "not reported", never
 *  a guess; "no analysis answers this" is a stated catalog fact, not an empty section. */
export function whyHereHtml(entry, i) {
  const open = state.whyOpen.has(entry.question);
  const d = entry.derivation || {};
  const active = [...state.activePerspectives];
  const carries = entry.perspectives || [];
  const matched = carries.filter((p) => state.activePerspectives.has(p));
  const line = (label, value) => `<div><span class="text-ink-muted">${esc(label)}</span> <span class="text-ink">${value}</span></div>`;
  const bits = [];
  if (entry.stage) bits.push(line('Stage:', esc(entry.stage)));
  if (matched.length) bits.push(line('Shown because of perspective:', matched.map(esc).join(', ')));
  else if (active.length) bits.push(line('Perspective:', 'none of the chosen perspectives match it, yet it is shown'));
  else bits.push(line('Perspective:', `no filter is on; it carries ${carries.length ? carries.map(esc).join(', ') : 'none'}`));
  if (Array.isArray(entry.purposes)) {
    const mine = entry.purposes;
    const inv = currentPurposes();
    bits.push(line('Serves purpose:', mine.length ? mine.map(esc).join(', ') : 'none stated in the question list'));
    if (inv.length) {
      bits.push(line('This investigation:', d.purpose_ranked
        ? `leads, because it serves ${(d.matched_purposes || []).map(esc).join(', ')}`
        : `${esc(inv.join(', '))} — it serves none of these, so it follows in the list's own order`));
    }
  } else {
    bits.push(line('Serves purpose:', 'not reported'));
  }
  const ids = d.analysis_ids || entry.analysis_ids || [];
  const checks = d.checks || [];
  bits.push(ids.length
    ? line('Answered by:', ids.map(esc).join(', '))
    : `<div class="text-state-warn">No analysis answers this yet${entry.answering_mechanism ? ` — ${esc(entry.answering_mechanism)}` : ''}</div>`);
  if (checks.length) bits.push(line('Checks:', checks.map(esc).join(', ')));
  return `<details data-why="${i}" ${open ? 'open' : ''} class="ml-[22px] mt-[4px] text-provenance text-ink-muted">
    <summary class="cursor-pointer" title="Why this question is on this list, and what answers it">why is this here</summary>
    <div data-why-body class="mt-[2px] space-y-[1px] border-l border-rule pl-s2">${bits.join('')}</div>
  </details>`;
}

function contextRecordedBodyHtml(entry, indent) {
  const c = contextRecorded(entry);
  const held = (state.enrichment || {})[c.key];
  const link = `<button type="button" data-goto-context
    class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${held?.value ? 'Enrichment › Context ›' : 'record it on Context ›'}</button>`;
  return held?.value
    ? `<div class="ml-[22px] mt-[6px] text-answer text-ink" data-context-recorded>recorded on ${link}</div>`
    : `<div class="ml-[22px] mt-[6px] text-answer text-ink" data-context-recorded>no survey measures this for databases · ${link}</div>`;
}

function bodyLines(entry, i, st, env) {
  const indent = 'ml-[22px] mt-[6px]';

  if ((st === 'answered' || st === 'human') && contextRecorded(entry)
      && rowState(entry, env) === 'no_reader') {
    return contextRecordedBodyHtml(entry, indent);
  }

  if (st === 'loading') {
    // A skeleton, not a spinner, and not a claim.
    return `<div class="ml-[22px] mt-[6px] h-[14px] w-[42%] rounded-sm bg-paper-surface"></div>`;
  }

  if (st === 'proposal') {
    return prerequisiteProposalHtml(entry, i, indent);
  }

  if (st === 'running') {
    const r = state.runsInFlight.get(entry.question);
    return `<div class="ml-[22px] mt-[6px] text-answer text-accent-ink">${esc(r.label)}</div>`
      + provenanceLine(entry, i, { canRun: [], sources: r.analysisId ? [r.analysisId] : [] }, st);
  }

  if (st === 'error') {
    // Per-row failure, per-row retry. Never a page-level error.
    return `<div class="ml-[22px] mt-[6px] text-answer text-accent-ink">${esc(env.__error)}</div>
      <div class="ml-[22px] mt-[6px] text-provenance text-ink-muted">
        <button data-retry="${i}" class="cursor-pointer bg-transparent text-accent-ink underline">retry</button>
      </div>`;
  }

  if (st === 'no-surveyor') {
    const why = (env && env.blocked_reason)
      || entry.note
      || 'No surveyor exists for this question. Nothing has run and nothing can.';
    return `<div class="ml-[22px] mt-[6px] text-answer text-ink">${tnum(esc(why))}</div>`;
  }

  // The catalog names a mechanism (`direct`/`chart`), but no reader has been
  // wired up for it yet -- distinct from `no-surveyor` (no mechanism exists
  // at all). DATABASE-DIRECT-FIELD-ROWS-IMPLEMENTED.md.
  if (st === 'no_reader') {
    const why = (env && env.blocked_reason)
      || entry.note
      || 'Answerable from a stored field, but no reader has been wired up for it yet.';
    return `<div class="ml-[22px] mt-[6px] text-answer text-ink">${tnum(esc(why))}</div>`;
  }

  if (st === 'human') {
    const why = entry.note || 'This is answered by someone stating it, not by a survey.';
    const key = questionKey(entry.question);
    const held = (state.contextAnswers || {})[key];
    const declared = (entry.answering_mechanism || '');
    // The mechanism column declares "Egeria Queries" on six of these seven
    // questions, and none of the six names an analysis. So there is nothing
    // to run first, and the honest render says which — an empty state that
    // says what it looked in, per this round's own rule. Claiming a query
    // ran, or silently offering only the text box, would both misdescribe it.
    const wanted = /egeria quer/i.test(declared) && !(entry.analysis_ids || []).length;
    // Row anatomy shared with Enrichment's judgements/observations
    // (ENRICHMENT-E0-ROW-ANATOMY, row-anatomy.js): who + when, through the
    // SAME function that file's rows call. `held.answered_by` is absent on
    // an answer recorded before this change (no author was ever stored) --
    // that degrades to the old "answered <when>" wording rather than a
    // blank "· 2d ago", since `personRowLineHtml` returns '' with no author.
    const provenance = held?.answer
      ? (held.answered_by
          ? personRowLineHtml({ author: held.answered_by, whenIso: held.answered_at, verb: 'answered by' })
          : `answered ${esc(ago(held.answered_at))}`)
      : '';
    // Inline control, not a popup (ENRICHMENT-E0-ROW-ANATOMY §0.3/§6 item 1
    // of the designer's reply): `window.prompt()` blocked the whole page,
    // took one line, and recorded no author. This is the same input+save
    // shape `stages/enrichment.js`'s `fieldControlHtml` already uses for
    // judgements/observations, reused here rather than invented fresh —
    // there is no general Context tab yet (that's slice E1, a separate,
    // larger IA restructure), so the control opens on this row itself,
    // which is the narrower, in-scope place for it today.
    const editing = state.editingAnswer === entry.question;
    const editorHtml = editing ? `<div class="ml-[22px] mt-[6px] mt-s1 flex items-start gap-s2">
         <textarea data-answer-input rows="2"
           class="w-full max-w-[60ch] rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink"
           placeholder="${esc(why)}">${esc(held?.answer || '')}</textarea>
         <div class="flex shrink-0 flex-col gap-[2px]">
           <button type="button" data-answer-save="${esc(entry.question)}"
             class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Save</button>
           <button type="button" data-answer-cancel="1"
             class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">cancel</button>
         </div>
       </div>` : '';
    return `<div class="ml-[22px] mt-[6px] text-answer text-ink">${tnum(esc(why))}</div>
      ${held?.answer
        ? `<div class="ml-[22px] mt-[6px] mt-s1 text-answer text-ink">${tnum(esc(held.answer))}</div>
           <div class="ml-[22px] mt-[6px] text-provenance text-ink-muted">${provenance}
             · <button type="button" data-human-edit="${esc(entry.question)}"
                 class="cursor-pointer bg-transparent text-accent-ink underline">change</button></div>`
        : (editing ? '' : `<div class="ml-[22px] mt-[6px] mt-s1">
             <button type="button" data-human-edit="${esc(entry.question)}"
               class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
               >Answer this →</button>
           </div>`)}
      ${editorHtml}
      ${wanted
        ? `<div class="ml-[22px] mt-[6px] text-provenance text-ink-muted">The catalog says
             ${esc(declared)}, but no analysis is attached to this question, so
             nothing was queried — the answer here is yours alone.</div>`
        : ''}`;
  }

  if (st === 'unclassified') {
    return `<div class="ml-[22px] mt-[6px] text-answer text-ink">The catalog does not state how this
      question would be answered.</div>`;
  }

  if (st === 'unrun') {
    // NO answer line. Not a zero, not an empty string — the absence of the
    // line is the statement.
    const why = (env && env.blocked_reason) || 'Not run yet.';
    const lines = readEnvelope(entry, env, esc, tnum);
    return autoRanNoteHtml(entry, indent)
      + `<div class="ml-[22px] mt-[6px] text-answer text-ink">${tnum(esc(why))}</div>`
      + provenanceLine(entry, i, lines, st);
  }

  // answered | automatic | partial
  const lines = readEnvelope(entry, env, esc, tnum);
  let html = autoRanNoteHtml(entry, indent);
  if (lines.answer) {
    html += `<div class="ml-[22px] mt-[6px] text-answer text-ink">${lines.answer}</div>`;
  }
  // §18.3's own caveat, ahead of the analysis's own (below): a rollup with
  // no schema/table/column named is the more important thing to say here,
  // and the reader should not have to reach the analysis's own note to
  // learn the tick is withheld.
  if (st === 'partial' && env && env.level_note) {
    html += `<div class="ml-[22px] mt-[5px] text-caveat text-accent-ink">${tnum(esc(env.level_note))}</div>`;
  }
  if (lines.caveat) {
    html += `<div class="ml-[22px] mt-[5px] text-caveat text-accent-ink">${tnum(esc(lines.caveat))}</div>`;
  }
  return html + provenanceLine(entry, i, lines, st);
}

/** §17.1's within-budget half: the resolver already ran a producer before
 *  the demanding step, unasked, because it was cheap enough to stay inside
 *  the tier the user was already paying for. Nothing to accept or decline --
 *  it already happened -- but saying nothing would be the exact silent
 *  omission the design's condition 3 forbids: a row that took longer than
 *  usual with no visible reason.
 *
 *  Read-once: the note is deleted from `state.autoRanNotes` as soon as this
 *  renders it, so the NEXT re-render of this row (a perspective filter
 *  change, a tab switch back) shows the answer plainly rather than an
 *  ever-present caveat about a run that is now history. */
function autoRanNoteHtml(entry, indent) {
  const note = state.autoRanNotes.get(entry.question);
  if (!note) return '';
  state.autoRanNotes.delete(entry.question);
  return `<div class="ml-[22px] mt-[6px] text-caveat text-ink-muted">${tnum(esc(note))}</div>`;
}

/** §17.1's crossing-tier half: the resolver would not start the run unasked.
 *  Three things said, same as classic's card (design §17.1's own list): WHAT
 *  would run, WHAT it costs, and WHY it is being asked rather than simply
 *  done -- dropping the third makes this read as the system being timid
 *  about a cheap step, when the point is that the user's OWN budget is what
 *  is holding it.
 *
 *  Rendered as the row's whole body, same convention `st === 'human'` uses
 *  for its own decision point -- a proposal is a decision, not a finding, so
 *  it does not share `answered`'s "answer + caveat + provenance" shape. */
function prerequisiteProposalHtml(entry, i, indent) {
  const pending = state.pendingProposals.get(entry.question);
  if (!pending) return '';
  const p = pending.proposal;
  const steps = (p.steps || []).map((s) => `<code class="text-accent-ink">${esc(s)}</code>`).join(' → ');
  const est = Math.round(p.estimated_seconds || 0);
  const basis = p.estimated_is_measured
    ? '<span class="text-ink-muted">(median of previous runs)</span>'
    : '<span class="text-ink-muted">(from its declared cost, never yet measured)</span>';
  const reasons = (p.reasons || []).map((r) => `<li>${esc(r.detail)}</li>`).join('');

  // The second axis (REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md
  // §7.1). Rendered INSIDE this same proposal rather than as a surface of its
  // own, because §7.1's words are "one axis beside cost tier in the same
  // gate… the launcher shows one combined reason". A proposal carrying both a
  // tier reason and a capability reason is therefore one block with two
  // bullets and one lead line -- never two prompts to reconcile. The bullets
  // above already carry both; what changes below is only the LEAD sentence
  // (a capability-only proposal has no chain to name and no estimate to
  // quote) and the button row (a shortfall the credential cannot fix is
  // worth an RFA, which a cost-tier proposal has no use for).
  const cap = p.capability;
  const partial = !!p.run_partially;
  const frac = cap && cap.of
    ? ` <span class="tnum">${cap.have}</span> of <span class="tnum">${cap.of}</span> table(s)`
    : '';
  const lead = steps
    ? `Answering this needs ${steps} first —
        estimated <span class="tnum">${est}s</span> ${basis}.`
    // No chain, so no "needs X first" and no estimate. Saying either would be
    // a claim about work that does not exist.
    : `<code class="text-accent-ink">${esc(p.demanding_step || '')}</code> `
      + `can run, but not completely`
      + `${cap && cap.connected_as ? ` as <code>${esc(cap.connected_as)}</code>` : ''}${frac}.`;

  // §7.1 names three choices. Two are offered; the third is deliberately
  // absent and says so rather than appearing as a dead control.
  // "pick another visible connection" needs the multi-connection model that
  // is still gated on the project owner's ruling, and an enabled-looking
  // button that cannot do anything is worse than none.
  const accept = partial
    ? 'Run it anyway — and say so'
    : 'Run it';
  const rfa = partial
    ? `<button type="button" data-prereq-rfa="${i}"
        class="cursor-pointer bg-transparent text-caveat text-accent-ink underline"
        title="Raise a request to the database owner naming this step and the privilege it needs"
        >ask for broader access</button>`
    : '';
  return `<div class="ml-[22px] mt-[6px] text-answer text-ink">${lead}</div>
    ${reasons ? `<ul class="ml-[22px] mt-[6px] mt-[4px] list-disc list-inside text-caveat text-ink-muted">${reasons}</ul>` : ''}
    ${partial ? `<div class="ml-[22px] mt-[6px] mt-[4px] text-caveat text-ink-muted">Running it anyway is not wrong —
      the result is marked as measured within this credential's scope, never as a
      measurement of the whole database.</div>` : ''}
    <div class="ml-[22px] mt-[6px] mt-s2 flex flex-wrap items-center gap-s2">
      <button type="button" data-prereq-accept="${i}"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[1px] text-caveat text-accent-ink"
        >${accept}</button>
      ${rfa}
      <button type="button" data-prereq-decline="${i}"
        class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">not now — nothing has run</button>
    </div>`;
}

// Slice 21a point 4: mirrors `FactLayer._primary_level`'s own rule exactly
// ("resource" wins when declared alongside a sub-level; otherwise the first
// declared sub-level; otherwise "resource") so "the numbers behind this"
// asks the backend for the same level the question's own headline was
// rendered at, rather than always the flat resource scalars — found live,
// owner's question 2026-09-26: "Which schemas carry the data...?" opened
// the same table/column/row numbers "How big is this database" does.
function primaryQuestionLevel(entry) {
  const levels = entry.levels || [];
  if (levels.includes('resource')) return 'resource';
  return levels[0] || 'resource';
}

function provenanceLine(entry, i, lines, st) {
  const bits = [];
  const sources = lines.sources && lines.sources.length
    ? lines.sources.join(', ')
    : (entry.analysis_ids || []).join(', ');
  if (sources) bits.push(esc(sources));
  else if (entry.answering_mechanism) bits.push(esc(entry.answering_mechanism));

  // WHEN, said exactly as precisely as the record allows. Three different
  // statements, never folded together:
  //   a recorded timestamp        -> "run 19d ago"
  //   measured, but no timestamp  -> "run time not recorded"
  //   nothing measured            -> "never run"
  // The middle one is a gap in the record. Rendering it as "never run" —
  // which this row did until it was checked against the API — puts a tick
  // beside a claim that nothing ever ran, and both halves cannot be true.
  if (lines.lastRun) {
    const rel = ago(lines.lastRun);
    bits.push(`run ${rel ? `<span class="tnum">${esc(rel)}</span>` : esc(lines.lastRun)}`);
  } else if (lines.runTimeUnrecorded) {
    bits.push('run time not recorded');
  } else if (st !== 'running') {
    bits.push('never run');
  }

  // How it was known, when it was not a survey. A direct field and a survey
  // result are different kinds of claim and must not read alike.
  if (st === 'automatic' || st === 'nothing') {
    const how = { direct: 'direct field', registry: 'from the registry', chart: 'chart' }[entry.kind];
    if (how) bits.push(how);
  }

  const actions = [];
  if (st === 'answered' || st === 'automatic' || st === 'partial' || st === 'needs-lens' || st === 'nothing') {
    actions.push(`<button data-evidence="${i}" class="cursor-pointer bg-transparent text-accent-ink underline">evidence</button>`);
  }
  // A relationship answer has a diagram behind it. It cannot be read in a
  // 290px rail, so the row promotes it straight into the pane.
  if (lines.mermaid) {
    actions.push(`<button data-diagram="${i}" class="cursor-pointer bg-transparent text-accent-ink underline">diagram</button>`);
  }
  if (st !== 'loading') {
    actions.push(`<button data-copy="${i}" title="Copy this answer and its provenance as markdown"
      class="cursor-pointer bg-transparent text-accent-ink underline">copy as evidence</button>`);
  }
  const canRun = (lines.canRun && lines.canRun.length) || (entry.analysis_ids || []).length;
  if (canRun && st !== 'running') {
    actions.push(`<button data-rerun="${i}" class="cursor-pointer bg-transparent text-accent-ink underline">${
      st === 'unrun' ? 'run' : 're-run'}</button>`);
  }

  // Point 10 (SPEC-THE-STAGE-PAGE.md): "965 file names are not the fact
  // behind 965 source files -- the number is." The primary analysis's own
  // measurements, opened in place under the answer, not the rail and not a
  // second pane. One link per row, against the first analysis (the same one
  // "sources" names first) -- a question naming several analyses gets the
  // rest via that analysis's own row on `by_analysis`, not duplicated here.
  const primaryId = (entry.analysis_ids || [])[0];
  if (primaryId && (st === 'answered' || st === 'automatic' || st === 'partial' || st === 'needs-lens' || st === 'nothing')) {
    actions.push(`<button data-numbers="${i}" data-numbers-for="${esc(primaryId)}"
      data-numbers-level="${esc(primaryQuestionLevel(entry))}"
      class="cursor-pointer bg-transparent text-accent-ink underline">the numbers behind this ›</button>`);
  }

  // Automate (Part 4) — "🔔 Notify me" attached to the question row rather
  // than to a card, since Assessment/Analysis have no card grid in /next
  // (see automate.js's own comment block, and PLAN-FINISH-REPOS.md item 4).
  // A subscription watches an ANALYSIS, not a question, and `analysis_ids`
  // is not always 1:1 with a question (MIXED:/PARTIAL: answers can name
  // several) — gated on the array rather than `primaryId` alone so a
  // multi-analysis row still offers the action, and openNotifyDialog below
  // makes the reader pick rather than silently subscribing to the first.
  if ((entry.analysis_ids || []).length && st !== 'running') {
    actions.push(`<button data-notify="${i}" class="cursor-pointer bg-transparent text-accent-ink underline"
      title="Notify me (via RFA) when this changes on a future scheduled run — also set ⏱ Schedule in Automate, or this never fires">🔔 notify me</button>`);
  }

  if (!bits.length && !actions.length) return '';
  return `<div class="ml-[22px] mt-[7px] text-provenance text-ink-muted">${
    [bits.join(' · '), actions.join(' · ')].filter(Boolean).join(' · ')}</div>
    <div id="qm-${i}" hidden></div>`;
}

function updateAnsweredCount() {
  const el = $('answered-count');
  if (!el) return;
  const total = state.questions.length;
  const settled = state.questions.filter((q) => {
    const env = state.answers.get(q.question);
    return env && env !== 'loading' && !env.__error;
  });
  const answered = settled.filter((q) => {
    const env = state.answers.get(q.question);
    return isFullyAnswered(env);
  }).length;
  const pending = total - settled.length;
  el.innerHTML = `<span class="tnum">${answered}</span> of <span class="tnum">${total}</span> answered`
    + (pending ? ` · <span class="tnum">${pending}</span> still loading` : '')
    + ` · ${stageLabel()}`;
}

async function loadAnswer(entry, i, slug) {
  state.answers.set(entry.question, 'loading');
  let env;
  try {
    env = await getAnswer(slug, entry.question, apiEntityType(state.resourceType));
  } catch (err) {
    // 404 means the question text is not in the catalog the FactLayer reads —
    // a real mismatch between two catalogs, said plainly rather than shown
    // as an unanswered question, which would blame the repository for a
    // catalog problem.
    env = {
      __error: err instanceof ApiError && err.status === 404
        ? 'This question is not in the catalog the answer layer reads — the two catalogs disagree.'
        : `The answer could not be loaded: ${err.message}`,
    };
  }
  if (slug !== state.selectedSlug) return;
  state.answers.set(entry.question, env);
  replaceRow(entry, i, env);
  updateAnsweredCount();
  renderLegend();
}

function replaceRow(entry, i, env) {
  const el = document.getElementById(rowKey(i));
  if (!el) return;
  el.innerHTML = rowInner(entry, i, env);
  bindRowActions(el, entry, i);
}

function bindRowActions(el, entry, i) {
  el.querySelector(`[data-retry="${i}"]`)?.addEventListener('click', () => {
    replaceRow(entry, i, 'loading');
    loadAnswer(entry, i, state.selectedSlug);
  });
  el.querySelector(`[data-rerun="${i}"]`)?.addEventListener('click', (ev) => openRunChoice(entry, i, ev.currentTarget));
  el.querySelectorAll(`[data-ask-chat="${i}"]`).forEach((b) => b.addEventListener('click', () => askQuestionInChat(entry, i)));
  el.querySelector(`[data-why="${i}"]`)?.addEventListener('toggle', (ev) => {
    if (ev.currentTarget.open) state.whyOpen.add(entry.question); else state.whyOpen.delete(entry.question);
  });
  el.querySelector(`[data-evidence="${i}"]`)?.addEventListener('click', () => showEvidence(entry));
  el.querySelector(`[data-diagram="${i}"]`)?.addEventListener('click', () => showDiagram(entry));
  el.querySelector(`[data-copy="${i}"]`)?.addEventListener('click', (e) =>
    copyAsEvidence(rowAsMarkdown(entry, i), e.currentTarget));
  const numbersBtn = el.querySelector(`[data-numbers="${i}"]`);
  numbersBtn?.addEventListener('click', () =>
    toggleMeasurementsInPlace(i, numbersBtn.dataset.numbersFor, numbersBtn, numbersBtn.dataset.numbersLevel));
  el.querySelector(`[data-notify="${i}"]`)?.addEventListener('click', () => openNotifyDialog(entry));
  el.querySelector(`[data-prereq-accept="${i}"]`)?.addEventListener('click', () => acceptPrerequisiteProposal(entry, i));
  el.querySelector(`[data-prereq-decline="${i}"]`)?.addEventListener('click', () => declinePrerequisiteProposal(entry, i));
  el.querySelector(`[data-prereq-rfa="${i}"]`)?.addEventListener('click', () => raisePrerequisiteCapabilityRfa(entry, i));
}

/** The user's yes on a pending §17.1 proposal. Runs exactly the steps the
 *  proposal named (`runPrerequisites`, matching `/api/prerequisites/run`'s
 *  own contract: it re-resolves internally, so a step no longer needed by the
 *  time this lands is reported as such rather than re-run), then re-attempts
 *  the ORIGINAL request the proposal was blocking -- `skipPlanCheck` so the
 *  retry does not immediately re-ask about a chain it just ran. */
async function acceptPrerequisiteProposal(entry, i) {
  const pending = state.pendingProposals.get(entry.question);
  if (!pending) return;
  const { proposal, background, entityType } = pending;
  state.pendingProposals.delete(entry.question);
  // A capability-only proposal names no producers at all: the thing to run
  // IS the demanding step, and `run_partially` is where the server put it
  // (`steps` means "producers to run FIRST", which would read as nonsense
  // about the step the user just asked for). Appended rather than
  // substituted so a proposal carrying BOTH axes runs the chain and then the
  // step, in one accepted action -- §7.1's combined gate accepted as one.
  const toRun = proposal.run_partially
    ? [...(proposal.steps || []), proposal.run_partially]
    : (proposal.steps || []);
  state.runsInFlight.set(entry.question, {
    analysisId: pending.analysisId,
    label: `Running ${toRun.join(', ')}…`,
  });
  replaceRow(entry, i, state.answers.get(entry.question));
  try {
    const body = await runPrerequisites(entityType, state.selectedSlug, toRun,
                                        proposal.demanding_step,
                                        !!proposal.run_partially);
    if (body.status === 'error') {
      throw new Error((body.errors || []).join('; ') || 'prerequisite run failed');
    }
    state.autoRanNotes.set(entry.question, proposal.steps.length
      ? `Ran ${proposal.steps.join(', ')} first, then ${proposal.demanding_step}.`
      // Not "ran X first, then X". What happened is one step, run knowingly
      // within a credential that cannot see all of what it reads.
      : `Ran ${proposal.demanding_step} within this credential's scope.`);
  } catch (err) {
    state.runsInFlight.delete(entry.question);
    state.answers.set(entry.question, { __error: `The prerequisite could not be run: ${err.message}` });
    replaceRow(entry, i, state.answers.get(entry.question));
    updateAnsweredCount();
    renderLegend();
    return;
  }
  state.runsInFlight.delete(entry.question);
  await rerun(entry, i, { background, skipPlanCheck: true });
}

/** "not now" -- the design's own words for declining (§17.1: "or leave it —
 *  nothing has run"). Purely local: nothing was dispatched, so there is
 *  nothing to undo server-side, only the pending marker to clear. */
function declinePrerequisiteProposal(entry, i) {
  state.pendingProposals.delete(entry.question);
  replaceRow(entry, i, state.answers.get(entry.question));
}

/** §7.1's third choice at the capability gate: raise an RFA to the database
 *  owner naming THIS step and the privilege it needs.
 *
 *  Leaves the proposal pending on purpose. Asking for access is not a
 *  decision about the run -- the user can still choose "run it anyway" or
 *  "not now" afterwards, and clearing the row here would silently make the
 *  RFA read as a third answer to a two-answer question. */
async function raisePrerequisiteCapabilityRfa(entry, i) {
  const pending = state.pendingProposals.get(entry.question);
  if (!pending) return;
  const { proposal, entityType } = pending;
  const btn = document.querySelector(`[data-prereq-rfa="${i}"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'asking…'; }
  try {
    const body = await raiseCapabilityRfa(entityType, state.selectedSlug,
                                          proposal.run_partially);
    if (btn) {
      // Says which of the two outcomes happened. "not_raised" is a real,
      // honest answer (the shortfall is gone, or was never measured) and must
      // not render as if a request had been filed.
      btn.textContent = body.status === 'ok'
        ? 'asked — see the RFA drawer'
        : 'nothing to ask for';
    }
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = `could not ask: ${err.message}`; }
  }
}

/**
 * "🔔 Notify me" for a question row. Classic's own version
 * (`_createSubscriptionFromCard` in index.html) fires straight from the
 * button with no form — it can, because a card already names exactly one
 * analysis_id. A question row cannot assume that: `analysis_ids` is not
 * always 1:1 (a MIXED:/PARTIAL: answer can be produced by several), so this
 * always shows a small dialog rather than ever guessing — a picker when
 * there is more than one id, a single confirm when there is exactly one.
 * Never silently subscribes to `analysis_ids[0]`, unlike `rerun`/
 * `openRunChoice` above, which pick the first because re-running is
 * idempotent and safe to under-target; a subscription is a standing watch
 * on ONE analysis and picking the wrong one silently would be wrong, not
 * just incomplete.
 */
async function openNotifyDialog(entry) {
  const ids = entry.analysis_ids || [];
  const slug = state.selectedSlug;
  if (!ids.length || !slug) return;

  // The Questions engine this dialog hangs off used to be gated to
  // `state.resourceType === 'repo'` — that gate is gone (loadPane()'s "Repos
  // only, in /next" branch was lifted by the database/filesystem
  // generalization), so a question row can now legitimately be open on a
  // database or filesystem resource. `apiEntityType()` is the same
  // translation every other /next boundary crossing already uses; the
  // literal 'repo' this used to send unconditionally is what produced
  // "Repo 'laz_local_adventureworks' not found" for a database's own notify
  // dialog (found live 2026-09-28).
  const entityType = apiEntityType(state.resourceType);

  // Friendly names when available, same source `openAnalysisPopover` and the
  // "By analysis" section use (`getAnalysesIndex`) — falls back to the raw
  // id for any id that index doesn't carry (e.g. a not-yet-run analysis),
  // never blocks the dialog on this fetch failing.
  let namesById = {};
  try {
    const idx = await getAnalysesIndex(slug, '', apiEntityType(state.resourceType));
    namesById = Object.fromEntries(
      (idx.analyses || []).map((r) => [r.analysis_id, r.name || r.analysis_id]));
  } catch { /* names are a nicety; the ids alone still work */ }
  const nameOf = (id) => namesById[id] || id;

  const d = openDialog('🔔 Notify me', entry.question);
  const body = d.querySelector('#wl-detail-body');
  const pickerHtml = ids.length > 1
    ? `<p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">This question is answered by
         more than one analysis — pick the one to watch.</p>
       <div class="mb-s2 flex flex-col gap-[4px]">
         ${ids.map((id, n) => `<label class="inline-flex cursor-pointer items-center gap-[6px] text-caveat text-ink">
             <input type="radio" name="notify-analysis" value="${esc(id)}" ${n === 0 ? 'checked' : ''}>
             ${esc(nameOf(id))} <span class="font-mono text-ink-muted">${esc(id)}</span>
           </label>`).join('')}
       </div>`
    : `<input type="hidden" id="notify-analysis-only" value="${esc(ids[0])}">
       <p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">Watching
         <span class="font-mono">${esc(nameOf(ids[0]))}</span> for
         <span class="font-mono">${esc(slug)}</span>.</p>`;
  body.innerHTML = `
    ${pickerHtml}
    <label class="mb-[3px] block text-caveat text-ink-muted">Label</label>
    <input id="notify-label" type="text" value="${esc(`${nameOf(ids[0])} changed`)}"
      class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-answer text-ink">

    <div class="mb-s2 flex flex-wrap items-center gap-s2">
      <span class="text-caveat text-ink-muted">Schedule this analysis:</span>
      <select id="notify-schedule-cadence"
        class="rounded-sm border border-rule bg-paper px-2 py-[2px] text-caveat text-ink">
        <option value="manual">manual</option>
        <option value="daily" selected>daily</option>
        <option value="weekly">weekly</option>
        <option value="monthly">monthly</option>
      </select>
      <button id="notify-schedule-save" type="button"
        class="cursor-pointer rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">Set</button>
      <span id="notify-schedule-status" class="text-caveat text-ink-muted"></span>
    </div>

    <p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">
      Detection runs locally, against this app's own registry — the next time a
      <em>scheduled</em> run of that analysis detects a change, it writes an
      RFA straight into this app's drawer. No schedule above means this never
      fires. Separately, the scheduler's own background loop also creates a
      personal Egeria ToDo for each RFA it delivers — unlinked to
      <span class="font-mono">${esc(slug)}</span> itself, whatever this
      resource's publish state — so that half happens whether or not it has
      ever been published to Egeria.</p>
    <div id="notify-error" class="mb-s2 text-caveat text-state-warn"></div>
    <div class="flex gap-s2">
      <button id="notify-submit" type="button"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Subscribe</button>
      <button data-act="close" type="button" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
    </div>`;

  if (ids.length > 1) {
    body.querySelectorAll('input[name="notify-analysis"]').forEach((r) => r.addEventListener('change', () => {
      body.querySelector('#notify-label').value = `${nameOf(r.value)} changed`;
    }));
  }

  const currentAnalysisId = () => (ids.length > 1
    ? body.querySelector('input[name="notify-analysis"]:checked')?.value
    : body.querySelector('#notify-analysis-only').value);

  // Inline scheduling (§4's copy above literally says "no schedule above" —
  // this is that "above"). Reuses the exact same backend call the per-card
  // "⏱ Schedule" action and chat's inline scheduling form both make
  // (saveSchedule(), POST /api/schedules/{entityType}/{slug} — see
  // `_chatSubmitSchedule()` in index.html for the identical pattern this
  // follows), rather than sending the reader to Automate to do it there.
  // Reads back what was actually stored (cadence + next_run) via
  // getSchedules() rather than trusting the POST body it just sent, so the
  // confirmation shows what the server did, not what the client asked for.
  body.querySelector('#notify-schedule-save').addEventListener('click', async () => {
    const analysisId = currentAnalysisId();
    const statusEl = body.querySelector('#notify-schedule-status');
    if (!analysisId) { statusEl.textContent = 'Pick an analysis first.'; return; }
    const cadence = body.querySelector('#notify-schedule-cadence').value;
    const btn = body.querySelector('#notify-schedule-save');
    btn.disabled = true;
    statusEl.textContent = 'saving…';
    try {
      await saveSchedule(entityType, slug, analysisId, cadence, true);
      const rows = await getSchedules(entityType, slug);
      const saved = rows.find((r) => r.analysis_id === analysisId);
      statusEl.textContent = cadence === 'manual'
        ? '✓ manual — runs only when pressed; a notification needs daily, weekly or monthly'
        : saved?.next_run
        ? `✓ ${esc(saved.schedule || cadence)} — next run ${esc(ago(saved.next_run))}`
        : `✓ ${esc(cadence)} scheduled`;
    } catch (err) {
      statusEl.textContent = `could not schedule: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  body.querySelector('#notify-submit').addEventListener('click', async () => {
    const chosen = currentAnalysisId();
    const errEl = body.querySelector('#notify-error');
    if (!chosen) { errEl.textContent = 'Pick an analysis to watch.'; return; }
    const label = body.querySelector('#notify-label').value.trim();
    const btn = body.querySelector('#notify-submit');
    btn.disabled = true;
    btn.textContent = 'Subscribing…';
    try {
      await createSubscription(entityType, slug, chosen, label);
      closeCellDetail();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Subscribe';
      errEl.textContent = err.message;
    }
  });
}

/**
 * Point 10's disclosure: a table of measurements, indented under the answer,
 * the pane keeping its place -- not the rail, and not a second pane. Rows in
 * the table still open the rail (`opens` is a members reader), because a
 * column of file names is the one thing 290px suits; the count itself does
 * not belong there.
 *
 * Toggle, not always-open: opened once, closed on a second click, and the
 * fetch happens only then -- the row does not pay for this until asked.
 */
async function toggleMeasurementsInPlace(i, analysisId, btn, level) {
  const slot = $(`qm-${i}`);
  if (!slot) return;
  if (!slot.hidden) { slot.hidden = true; slot.innerHTML = ''; return; }
  const slug = state.selectedSlug;
  slot.hidden = false;
  slot.innerHTML = `<div class="ml-[22px] mt-s2 text-caveat text-ink-muted">Reading the measurements…</div>`;
  let data;
  try {
    data = await getMeasurements(slug, analysisId, apiEntityType(state.resourceType), level || 'resource');
  } catch (err) {
    slot.innerHTML = `<div class="ml-[22px] mt-s2 text-state-warn">The measurements could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug) return;   // a faster click, or a different resource, won
  const rows = data.measurements || [];
  if (data.not_applicable) {
    // Not_applicable is not zero -- "0" claims a count was taken and found
    // empty, which is a different fact than "this analysis keeps no
    // measurements table". The link keeps its un-counted label, and the
    // reason is the server's own (verified live against
    // re/stage-page-backend: "dependency_analysis records findings, not
    // measurements -- see the findings list.") -- not a fabricated one.
    slot.innerHTML = `<div class="ml-[22px] mt-s2 text-caveat text-ink-muted">${
      esc(data.reason || `${analysisId} keeps no measurements table.`)}</div>`;
    return;
  }
  if (btn) btn.textContent = `the numbers behind this ${rows.length} ›`;
  slot.innerHTML = `
    <table class="ml-[22px] mt-s2 w-full max-w-[60ch] border-collapse text-caveat">
      ${rows.map((m) => `<tr class="border-b border-rule">
        <td class="py-[4px] pr-s3 text-ink">${esc(String(m.name || '').replace(/_/g, ' '))}</td>
        <td class="tnum py-[4px] pr-s3 text-right text-ink">${m.value == null ? '<span class="text-ink-muted">not recorded</span>' : esc(fmtScalar(m.value, m.name))}</td>
        <td class="py-[4px] text-right text-provenance">${
          m.opens
            ? `<button data-numbers-open="${esc(m.opens.analysis_id || analysisId)}" data-numbers-metric="${esc(m.opens.metric || m.name)}"
                 data-numbers-title="${esc(String(m.name || '').replace(/_/g, ' '))}"
                 class="cursor-pointer bg-transparent text-accent-ink underline">the ${tnum(esc(String(m.value)))} ${esc(String(m.name || '').replace(/_/g, ' '))} ›</button>`
            // A row read from an annotation carries its envelope's state words (the reader derives them from
            // the envelope, never from the value): the word, then why when it is not a measurement.
            : m.state_words ? `<span class="text-ink">${esc(m.state_words)}</span>${
              m.reason ? `<span class="text-ink-muted"> · ${esc(m.reason)}</span>` : ''}`
            : m.note ? `<span class="text-ink-muted">${esc(m.note)}</span>` : ''
        }</td>
      </tr>`).join('')}
    </table>
    ${data.footer ? `<div class="ml-[22px] mt-s1 text-provenance text-ink-muted">${esc(data.footer)}</div>` : ''}`;
  slot.querySelectorAll('[data-numbers-open]').forEach((b) => b.addEventListener('click', () => openMembers({
    slug, analysisId: b.dataset.numbersOpen, metric: b.dataset.numbersMetric, title: b.dataset.numbersTitle,
  })));
}

/**
 * Re-run the analyses behind one row.
 *
 * The running state distinguishes QUEUED from RUNNING from STALLED. A pending
 * marker that cannot tell a stalled run from a slow one is worse than none:
 * it converts "we don't know" into "wait a bit longer" forever.
 */
/* ── The run choice ──────────────────────────────────────────────────────
 *
 * The one-click re-run stops being one click (REPORT-RECORD-AND-TWO-CALLS
 * §A, 2026-09-13). Not because the choice matters every time, but because
 * an action must name what it would do before it does it, and this is the
 * action whose price moved by a factor of six while it was being
 * discussed. The link opens a small popover anchored to itself; nothing
 * queues from the link. Background first, because on the evidence it is
 * right nearly every time; the waiting option keeps its own name.
 *
 * Four price variants, and BOTH buttons in all four, including not known:
 * a missing price is a reason to say so, not to withhold the action -- the
 * first run is what fixes it. */
export function fmtSeconds(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '';
  const s = Number(sec);
  if (s < 1) return `${s.toFixed(1)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60); const r = Math.round(s - m * 60);
  return r ? `${m}m ${r}s` : `${m}m`;
}

/** The price line for one of the four variants. `cost` is a RunCost or
 *  null (the read failed / nothing recorded). */
/** The declared word -- "fast" -- from RunCost.declared, or from the
 *  sentence's quotes when the serialiser carries only the sentence. */
function declaredWord(cost) {
  if (cost?.declared) return String(cost.declared);
  const m = /'([^']+)'/.exec(String(cost?.sentence || ''));
  return m ? m[1] : '';
}

function priceLineHtml(cost, analysisId) {
  if (!cost || cost.basis === 'unknown' || (cost.basis === 'measured' && !cost.runs)) {
    return `<span class="text-ink-muted">Price not known — no run of <span class="font-mono">${esc(analysisId)}</span> has been recorded yet. The first run is what fixes it.</span>`;
  }
  if (cost.basis === 'declared') {
    const w = declaredWord(cost);
    return `<span class="text-ink"><span class="text-ink-muted">declared</span> ${esc(w || cost.sentence || '')}</span>
      <span class="text-ink-muted">· not measured</span>`;
  }
  // measured
  if (cost.split_runs) {
    // the server's sentence carries the dominant-half rule
    return `<span class="text-ink">${tnum(esc(cost.sentence || `about ${fmtSeconds(cost.seconds)} in all`))}</span>`;
  }
  return `<span class="text-ink">about <span class="tnum">${esc(fmtSeconds(cost.seconds))}</span> in all</span>
    <span class="text-ink-muted">· median of <span class="tnum">${cost.runs}</span> run${cost.runs === 1 ? '' : 's'} · not yet split into run and publish</span>`;
}

async function openRunChoice(entry, i, anchor) {
  const analysisId = (entry.analysis_ids || [])[0];
  if (!analysisId) return;
  document.querySelector('[data-run-choice]')?.remove();
  anchor.insertAdjacentHTML('afterend', `
    <div data-run-choice class="mt-[4px] inline-block max-w-[60ch] rounded-sm border border-rule-strong bg-paper p-s2 text-caveat shadow-lg">
      <div data-run-price class="text-ink-muted">Reading the price…</div>
      <div class="mt-s2 flex flex-wrap items-baseline gap-s3">
        <button data-run-mode="background" class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-accent-ink">Background</button>
        <button data-run-mode="wait" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-ink">Run and wait</button>
        <button data-run-cancel class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">not now</button>
      </div>
    </div>`);
  const box = anchor.parentElement.querySelector('[data-run-choice]');
  box.querySelector('[data-run-cancel]').addEventListener('click', () => box.remove());
  box.querySelectorAll('[data-run-mode]').forEach((b) => b.addEventListener('click', () => {
    box.remove();
    rerun(entry, i, { background: b.dataset.runMode === 'background' });
  }));
  let cost = null;
  try { cost = await getRunCost(analysisId); } catch { cost = null; }
  const line = box.querySelector('[data-run-price]');
  if (line) line.innerHTML = priceLineHtml(cost, analysisId);
}

/**
 * §17.1's ask-before-you-run half. `analysisId` doubles as the resolver's
 * `step_key` -- true for the common one-analysis-one-step case this wiring
 * targets; an analysis mapped to SEVERAL survey steps (`DATABASE_ANALYSIS_
 * STEP_MAP`'s multi-step entries, a repo analysis owning more than one
 * `re_analysis_step`) is not resolved by this call, and `plan_prerequisites`
 * degrades to `{status: "satisfied"}` for a `step_key` it does not recognise
 * -- the conservative direction: the run proceeds exactly as it did before
 * this existed, rather than a guessed step_key producing a false proposal.
 * Known gap, named rather than silently accepted; see this PR's own report.
 *
 * Returns true when the caller should stop -- either a proposal is now
 * pending the user's answer, or the plan check itself failed and calling it
 * a second time on the same click would just repeat the failure.
 */
async function checkPrerequisitePlan(entry, i, analysisId, { background }) {
  const entityType = apiEntityType(state.resourceType);
  let plan;
  try {
    plan = await planPrerequisites(entityType, state.selectedSlug, analysisId);
  } catch (err) {
    // A plan-check failure must not silently block every run from now on --
    // the endpoint being briefly unreachable is not the same fact as "this
    // step is fine to run unasked", but it is also not license to wedge the
    // whole Questions checklist. Proceeds, same as the pre-§17.1 behaviour,
    // rather than leaving the row stuck on a question nobody can answer.
    console.warn('prerequisite plan check failed, proceeding without it:', err);
    return false;
  }
  if (plan.status === 'proposal' && plan.proposal) {
    state.pendingProposals.set(entry.question, {
      analysisId, entityType, background, proposal: plan.proposal,
    });
    replaceRow(entry, i, state.answers.get(entry.question));
    return true;
  }
  // `plan.status === 'auto_run'` needs no action here: the ordinary run
  // endpoint resolves the SAME chain again server-side and runs the
  // producers itself (§17.1's within-budget half is unconditional, not
  // gated on the client having asked first) -- this call only existed to
  // find out WHETHER to stop and ask. `rerun` below leaves the auto-run
  // note for `bodyLines` to show once the run comes back.
  if (plan.status === 'auto_run' && (plan.auto_run || []).length) {
    state.autoRanNotes.set(entry.question, `Ran ${plan.auto_run.join(', ')} first, then ${analysisId}.`);
  }
  return false;
}

async function rerun(entry, i, { background = false, skipPlanCheck = false, force = false } = {}) {
  const analysisId = (entry.analysis_ids || [])[0];
  if (!analysisId) return;
  const slug = state.selectedSlug;

  if (!skipPlanCheck && await checkPrerequisitePlan(entry, i, analysisId, { background })) {
    return;
  }

  state.runsInFlight.set(entry.question, { analysisId, label: `Queued · ${analysisId}` });
  replaceRow(entry, i, state.answers.get(entry.question));

  if (background) {
    // Enqueue and stop watching. The row says it is queued in the worker
    // and how to see the result; nothing here pretends to know when.
    try {
      const queued = await runAnalysis(slug, analysisId, apiEntityType(state.resourceType), { force });
      if (isFreshnessSkip(queued)) {
        state.runsInFlight.delete(entry.question);
        replaceRow(entry, i, state.answers.get(entry.question));
        offerRunAnyway(queued, { slug, analysisId, onForce: () =>
          rerun(entry, i, { background: true, skipPlanCheck: true, force: true }) });
        return;
      }
      state.runsInFlight.set(entry.question, { analysisId, label: `In background · ${analysisId} · reload to read the result` });
    } catch (err) {
      state.runsInFlight.delete(entry.question);
      state.answers.set(entry.question, { __error: `The run could not be started: ${err.message}` });
    }
    replaceRow(entry, i, state.answers.get(entry.question));
    return;
  }

  let finishedRun = null;
  try {
    const started = await runAnalysis(slug, analysisId, apiEntityType(state.resourceType), { force });
    if (isFreshnessSkip(started)) {
      state.runsInFlight.delete(entry.question);
      replaceRow(entry, i, state.answers.get(entry.question));
      offerRunAnyway(started, { slug, analysisId, onForce: () =>
        rerun(entry, i, { skipPlanCheck: true, force: true }) });
      return;
    }
    const activityId = started.activity_id;
    state.runsInFlight.set(entry.question, { analysisId, activityId, label: `Running · ${analysisId}` });
    replaceRow(entry, i, state.answers.get(entry.question));

    finishedRun = await pollActivity(activityId, {
      onTick: (e) => {
        const s = (e?.status || '').toLowerCase();
        const label = s === 'running' ? `Running · ${analysisId}`
          : s === 'queued' || s === 'pending' ? `Queued · ${analysisId}`
          : `Running · ${analysisId}`;
        const cur = state.runsInFlight.get(entry.question);
        if (cur && cur.label !== label) {
          state.runsInFlight.set(entry.question, { ...cur, label });
          replaceRow(entry, i, state.answers.get(entry.question));
        }
      },
    });
  } catch (err) {
    state.runsInFlight.delete(entry.question);
    const msg = err.name === 'PollTimeout'
      // Stopped watching is NOT failed. Saying "failed" here would report a
      // fact about this browser as a fact about the run.
      ? `Still running after five minutes — /next stopped watching. The run itself has not failed; check Activity in the current UI.`
      : `The run could not be started: ${err.message}`;
    state.answers.set(entry.question, { __error: msg });
    replaceRow(entry, i, state.answers.get(entry.question));
    updateAnsweredCount();
    renderLegend();
    return;
  }

  state.runsInFlight.delete(entry.question);
  // The finished row's own status decides, not the path taken to get here:
  // a run that ended in error stays visible on this row instead of being
  // replaced by a re-read of the previous answer.
  const failure = activityFailure(finishedRun);
  if (failure) {
    state.answers.set(entry.question, { __error: failureText(failure).replace('Failed — ', 'The run failed: ') });
    replaceRow(entry, i, state.answers.get(entry.question));
    updateAnsweredCount();
    renderLegend();
    return;
  }
  await loadAnswer(entry, i, state.selectedSlug);
}

/** One measure, rendered for reading rather than for parsing.
 *
 *  A structured value is SUMMARISED, never JSON.stringify'd into the rail —
 *  a dumped array is not evidence, it is a reason to stop reading. Lists of
 *  findings show their own summaries; anything else shows its shape and its
 *  size, which is enough to know whether to go and look at it properly. */
function measureHtml(key, v) {
  const label = `<span class="text-chrome-muted">${esc(key.replace(/_/g, ' '))}</span>`;
  if (v === null || v === undefined || v === '') {
    return `<div>${label} <span class="text-chrome-muted">not set</span></div>`;
  }
  if (Array.isArray(v)) {
    // Language/file-type breakdowns (e.g. `by_type`) are counted, sortable
    // rows like {type_label, file_count} — none of the finding-shaped field
    // names below, so without this branch every row fell through to an
    // empty name and empty text and rendered as a blank line.
    const isCountRow = (it) => it && typeof it === 'object' &&
      (it.type_label !== undefined || it.file_count !== undefined);
    const sorted = isCountRow(v[0])
      ? [...v].sort((a, b) => (b.file_count || 0) - (a.file_count || 0))
      : v;
    const shownCount = isCountRow(v[0]) ? 10 : 6;
    const items = sorted.slice(0, shownCount).map((it) => {
      if (isCountRow(it)) {
        return `<div class="ml-s2"><span class="text-accent-on-dark">${esc(it.type_label ?? '')}</span> ${tnum(esc(it.file_count ?? ''))}</div>`;
      }
      if (it && typeof it === 'object') {
        const name = it.check_name || it.name || it.id || '';
        const text = it.summary || it.detail || it.label || '';
        return `<div class="ml-s2">${name ? `<span class="text-accent-on-dark">${esc(name)}</span> ` : ''}${tnum(esc(text))}</div>`;
      }
      return `<div class="ml-s2">${tnum(esc(it))}</div>`;
    }).join('');
    const more = v.length > shownCount
      ? `<div class="ml-s2 text-chrome-muted">and <span class="tnum">${v.length - shownCount}</span> more</div>`
      : '';
    return `<div>${label} <span class="tnum">${v.length}</span></div>${items}${more}`;
  }
  if (typeof v === 'object') {
    const n = Object.keys(v).length;
    return `<div>${label} <span class="text-chrome-muted"><span class="tnum">${n}</span> fields</span></div>`;
  }
  const shown = typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v);
  // A long string is SUMMARISED, never dumped. `architecture_diagram`'s value
  // carries 9,384 characters of Mermaid source, and rendering it as a scalar
  // filled the rail with raw diagram code — which reads as the app having
  // broken, not as a measure. The rail is 290px; nothing that wide belongs in
  // it, and the diagram already has its own action.
  //
  // Explanation fields are the opposite case: they exist ONLY so a bare score
  // or label (`activity: 100`, `attention: low`) has something to explain it,
  // and collapsing one to "171 characters" throws away the reason it was put
  // there in the first place. Named by suffix/convention rather than length,
  // because a genuine prose explanation and a raw dump are not the same shape
  // even when they happen to be a similar number of characters.
  const isExplanation = /(^|_)(detail|summary)$/.test(key) || key === 'measures_disagree';
  if (shown.length > 120 && !isExplanation) {
    const isDiagram = /^(mermaid|diagram|svg)$/i.test(key);
    return `<div>${label} <span class="text-chrome-muted">${
      isDiagram ? 'diagram source' : 'text'} · <span class="tnum">${shown.length}</span> characters${
      isDiagram ? ' — use “Open diagram in pane”' : ''}</span></div>`;
  }
  if (isExplanation) {
    return `<div class="text-chrome-muted">${esc(shown)}</div>`;
  }
  return `<div>${label} <span class="tnum">${tnum(esc(shown))}</span></div>`;
}

/* ────────────────────────────────────────────────────────────────────────
 * Copy as evidence
 *
 * Labelled "Copy as evidence", not "Copy": the label says what the artifact
 * is for. These answers get pasted into issues, Dr.Egeria plans and review
 * documents — all markdown — and an answer pasted WITHOUT its source line
 * loses the thing this whole redesign is about. So the provenance is not
 * optional and is not a separate button.
 * ──────────────────────────────────────────────────────────────────────── */

/** Put text on the clipboard, and say so on the button that asked. */
export async function copyAsEvidence(markdown, btn) {
  const done = (msg, ok = true) => {
    if (!btn) return;
    const prev = btn.innerHTML;
    btn.innerHTML = `<span class="text-caps ${ok ? '' : 'text-state-warn'}">${esc(msg)}</span>`;
    setTimeout(() => { btn.innerHTML = prev; }, 1600);
  };
  try {
    await navigator.clipboard.writeText(markdown);
    done('copied');
  } catch (err) {
    // A clipboard write can be refused (no permission, not a user gesture,
    // an insecure origin). Saying nothing would leave someone pasting a
    // stale buffer into an issue and not knowing.
    done('could not copy', false);
    console.warn('clipboard write refused:', err);
  }
}

/** One question row's answer in parts: the sentence(s) it says, its caveat, and the provenance bits. The ONE
 *  place a row's answer is turned into text: "copy as evidence" and "ask in chat" both read it, so the chat
 *  answer cannot differ from the row it was asked from. */
function rowAnswerParts(entry) {
  const env = state.answers.get(entry.question);
  const st = effectiveRowState(entry, env);
  const lines = (env && env !== 'loading' && !env.__error)
    ? readEnvelope(entry, env, esc, tnum) : null;

  // The answer, as text. `lines.answer` is HTML by the time it reaches a row,
  // so it is rebuilt from the facts here rather than stripped of tags — a
  // regex over markup is how a stray `<` ends up in someone's issue.
  const said = [];
  for (const f of ((env && env.facts) || []).filter((x) => x.is_known)) {
    if (f.headline) said.push(f.headline);
    else if (prose(f)) said.push(f.value?.verdict ? `${cap(String(f.value.verdict))} — ${prose(f)}` : prose(f));
    else if (scalarMeasures(f.value)) said.push(scalarMeasures(f.value));
  }
  const answer = said.length
    ? said.join(' ')
    : `${(env && env.__error) || (env && env.blocked_reason) || STATE_SENTENCE[st] || 'No answer recorded.'}`;

  const bits = [state.selectedSlug];
  const sources = (lines && lines.sources.length ? lines.sources : entry.analysis_ids) || [];
  if (sources.length) bits.push(sources.join(', '));
  if (lines && lines.lastRun) bits.push(`run ${String(lines.lastRun).slice(0, 10)}`);
  else if (lines && lines.runTimeUnrecorded) bits.push('run time not recorded');
  else bits.push('never run');
  bits.push(env && env.answerable
    ? 'answered from survey metadata, no retrieval'
    : `state: ${STATE_LABEL[st] || st}`);
  return { st, said: said.length > 0, answer, caveat: lines && lines.caveat ? lines.caveat : '', bits, lines, sources };
}

/** One question row, as markdown with its provenance. */
function rowAsMarkdown(entry, i) {
  const p = rowAnswerParts(entry);
  const out = [`**${entry.question}**`, ''];
  out.push(p.said ? p.answer : `_${p.answer}_`, '');
  if (p.caveat) {
    // The caveat as a blockquote — it is the part a reader most needs to
    // carry across, and a quote survives being pasted into a thread.
    out.push(...p.caveat.split('\n').map((l) => `> ${l}`), '');
  }
  out.push(`— ${p.bits.filter(Boolean).join(' · ')}`);
  return out.join('\n');
}

/** The chat turn for a question row: the row's own answer text, its provenance line, and the analyses behind
 *  it (what "run" and "schedule" in the chat act on). `ran` is read from the same envelope the row drew. */
function rowChatTurn(entry) {
  const p = rowAnswerParts(entry);
  // Per analysis, from the envelope's own facts: an analysis with nothing recorded is "not run" even when a
  // sibling analysis behind the same question has run.
  const env = state.answers.get(entry.question);
  const facts = (env && env !== 'loading' && !env.__error && env.facts) || [];
  // `analyses_behind` is the server's read of the analyses the catalog lists behind a question answered from the
  // resource's own state, where `facts` holds only that one state fact.
  const behind = (env && env !== 'loading' && !env.__error && env.analyses_behind) || [];
  const hasRun = (id) => [...facts, ...behind].some((f) => f.analysis_id === id && (f.is_known || f.last_run_at));
  const text = p.caveat ? `${p.answer}\n\n${p.caveat}` : p.answer;
  return {
    answer: text,
    source: p.bits.filter(Boolean).join(' · '),
    runnable: (entry.analysis_ids || []).map((id) => ({ id, ran: hasRun(id) })),
  };
}

/** "Ask in chat" on a question row: opens the chat on the SAME answer the row shows (read again if the row
 *  has not loaded it), with the analyses behind it to run or schedule. Nothing is retyped at a model. */
async function askQuestionInChat(entry, i) {
  const slug = state.selectedSlug;
  if (!slug) return;
  const entityType = apiEntityType(state.resourceType);
  const read = async () => {
    let env = state.answers.get(entry.question);
    if (!env || env === 'loading' || env.__error) {
      try { env = await getAnswer(slug, entry.question, entityType); }
      catch (err) { env = { __error: `The answer could not be loaded: ${err.message}` }; }
      state.answers.set(entry.question, env);
    }
    return rowChatTurn(entry);
  };
  const first = await read();
  await openQuestionTurn({
    question: entry.question, slug, entityType, ...first,
    // After a run: read the answer again from the same endpoint and hand back the new text.
    refresh: async () => {
      state.answers.delete(entry.question);
      return read();
    },
  });
}

// `turnAsMarkdown` moved to next/chat.js (item 9) — it was chat-only and
// every call site went with it.

const STATE_LABEL = {
  answered: 'answered', automatic: 'automatic', nothing: 'nothing found', unrun: 'not run',
  partial: 'ran, but not at this level',
  human: 'needs human input', 'needs-lens': 'needs a person: declare a lens',
  'no-surveyor': 'no reader yet',
  unclassified: 'unclassified',
};
const STATE_SENTENCE = {
  unrun: 'Not run yet.',
  human: 'Answered by a person, not by a survey.',
  'no-surveyor': 'No reader yet for this question.',
  unclassified: 'The catalog does not state how this would be answered.',
};

/** Promote a question's diagram into the content pane. */
function showDiagram(entry) {
  const env = state.answers.get(entry.question);
  if (!env || env === 'loading' || env.__error) return;
  const found = factMermaid(env);
  if (!found) return;
  promoteToPane({
    question: entry.question,
    mermaid: found.source,
    source: `${found.analysisId}`
      + (found.lastRun ? ` · run ${ago(found.lastRun)}` : ' · run time not recorded')
      + ' · rendered by Kroki, no retrieval',
  });
}

/** Promote one zoom level of a question's relationship graph into the
 *  content pane. `level` is 'schema_map', 'full', or a schema name (looked
 *  up in `bySchema`). Mirrors `showDiagram` above but for the Graphviz path
 *  (three views instead of one, a possible fallback state instead of an
 *  always-available diagram). */
function showGraphvizPane(entry, level) {
  const env = state.answers.get(entry.question);
  if (!env || env === 'loading' || env.__error) return;
  const found = factGraphviz(env);
  if (!found) return;

  let source;
  let levelLabel;
  if (level === 'schema_map') {
    source = found.schemaMap;
    levelLabel = 'schema map';
  } else if (level === 'full') {
    if (!found.full) return;  // fallback state — the button is hidden for this case
    source = found.full;
    levelLabel = 'whole database';
  } else {
    source = found.bySchema[level];
    levelLabel = `schema: ${level}`;
    if (!source) return;
  }

  promoteToPane({
    question: entry.question,
    graphviz: source,
    source: `${found.analysisId} · ${levelLabel}`
      + (found.lastRun ? ` · run ${ago(found.lastRun)}` : ' · run time not recorded')
      + ' · rendered by Kroki, no retrieval',
  });
}

/** The full measurement behind a claim, in the rail. The row states the
 *  answer; this is where the numbers it came from live. */
function showEvidence(entry) {
  const env = state.answers.get(entry.question);
  // Evidence lands in the rail, so open the drawer if it is closed —
  // otherwise the link appears to do nothing. The DOM, not the preference.
  ensureRailShowing();
  railClaim();
  const forWhat = state.selectedSlug || '—';
  // A failure is more owed a sentence than an emptiness is.
  if (!env) {
    railFrame('Evidence', forWhat, `<div class="text-chip text-chrome-muted">Nothing was requested for this question yet — its answer has not been read.</div>`, { sub: 'nothing requested' });
    return;
  }
  if (env === 'loading') {
    railFrame('Evidence', forWhat, `<div class="text-chip text-chrome-muted">Still reading this question's answer…</div>`, { sub: 'loading' });
    return;
  }
  if (env.__error) {
    railFrame('Evidence', forWhat, `<div class="text-chip text-state-warn-on-dark">The answer for this question failed to read: ${esc(String(env.__error))}</div>`, { sub: 'failed' });
    return;
  }
  const out = $('rail-evidence');
  if (!out) return;

  // A "mixed" question (e.g. "How big is this database") answers from
  // MORE THAN ONE analysis_id, each with its own independently-written
  // results reader — nothing stops two readers from returning the same
  // field name for the same underlying number (schema_inventory's and
  // row_count_snapshot's readers both read the same stored `database_
  // tables` rows, and both happen to call their own fields "tables"/
  // "relation_count" -- was "table_count" on both until REPLY-DESIGNER-
  // ROUND2-DATABASE-SCREENS.md §2.3: an all-relation-kinds count named
  // "table_count" is a different population from a base-tables-only count
  // of the same name elsewhere on screen, e.g. the Relationship Graph
  // card's own "68" -- renamed at the source in both readers so the name
  // states what it counts). Each fact block used to render every key in
  // its own `.value` with no awareness of the others, so the same number
  // appeared once per fact that happened to carry it — found live, "How
  // big is this database"'s evidence panel listing `tables`/`relation_
  // count` twice.
  // Deduped by key, first fact in `env.facts` order wins — that ordering
  // already follows the question catalog's own `analysis_ids` list, so an
  // earlier entry is the one a reader would name first anyway.
  const shownKeys = new Set();
  const facts = (env.facts || []).map((f) => {
    const entries = f.value ? Object.entries(f.value) : [];
    const fresh = entries.filter(([k]) => !shownKeys.has(k));
    for (const [k] of fresh) shownKeys.add(k);
    const value = fresh.length
      ? `<div class="mt-[4px] leading-[1.95]">${fresh
          .map(([k, v]) => measureHtml(k, v)).join('')}</div>`
      : '';
    // REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3: `f.provenance`
    // defaults to the SAME string as `f.state` ("measured" -- `Fact.
    // provenance`'s own default is `PROVENANCE_MEASURED = "measured"`,
    // identical to `result_status.MEASURED`), so this line printed the
    // state twice -- "measured · measured · run 2m ago" -- for the common
    // case of a plain local measurement. Provenance only earns its own
    // word when it says something `state` doesn't (☁ Egeria vs. 🏠 local
    // vs. ⏳ pending, per this file's own "Survey source display" table).
    const stateWord = esc(f.state);
    const provenanceWord = f.provenance && f.provenance !== f.state ? ` · ${esc(f.provenance)}` : '';
    return `<div class="mb-s2 border-b border-chrome-line-soft pb-s2 last:border-0">
      <div class="text-chip text-accent-on-dark">${esc(f.analysis_id)}</div>
      <div class="text-caps text-chrome-muted">${stateWord}${provenanceWord} · ${
        f.last_run_at ? `run ${esc(ago(f.last_run_at))}`
          : f.is_known ? 'run time not recorded' : 'never run'}</div>
      ${f.headline ? `<div class="mt-[4px] text-subtab">${tnum(esc(f.headline))}</div>` : ''}
      ${f.note ? `<div class="mt-[4px] text-chip text-accent-on-dark">${tnum(esc(f.note))}</div>` : ''}
      ${value}
    </div>`;
  }).join('');

  const gv = factGraphviz(env);
  // Three zoom levels, per the relationship-graph design note: the schema
  // map always renders (any database size); "whole database" is either a
  // button or, above the ~100-table threshold, a named state rather than a
  // blank panel (`gv.fallbackReason`, `db_derived.py`'s `full_database_dot`);
  // "open a schema" is a select over every schema this question covers.
  const schemaNames = gv ? Object.keys(gv.bySchema).sort() : [];
  const graphvizBlock = gv ? `
    <div class="mb-s2 rounded-sm border border-rule-strong p-s2">
      <div class="mb-[6px] text-chip text-ink-muted">Relationship graph
        ${gv.tableCount != null ? `· <span class="tnum">${gv.tableCount}</span> tables` : ''}</div>
      <button data-act="evidence-graphviz" data-level="schema_map"
        class="mb-[6px] w-full cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[4px]
               text-chip text-accent-on-dark">${icon('maximize-2', { size: 13 })} Schema map</button>
      ${gv.full
        ? `<button data-act="evidence-graphviz" data-level="full"
             class="mb-[6px] w-full cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[4px]
                    text-chip text-accent-on-dark">${icon('maximize-2', { size: 13 })} Whole database</button>`
        : gv.fallbackReason
          ? `<div class="mb-[6px] text-chip text-state-warn">${esc(gv.fallbackReason)}</div>`
          : ''}
      ${schemaNames.length ? `<div class="relative">
          <select data-act="evidence-graphviz-schema"
               class="w-full cursor-pointer appearance-none rounded-sm border border-accent bg-transparent px-[10px] py-[4px] pr-[26px]
                      text-chip text-accent-on-dark hover:bg-accent-tint focus:outline-none focus:ring-1 focus:ring-accent">
            <option value="">Open a schema…</option>
            ${schemaNames.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
          </select>
          <span class="pointer-events-none absolute right-[8px] top-1/2 -translate-y-1/2 text-accent-on-dark">${icon('chevron-down', { size: 13 })}</span>
        </div>` : ''}
    </div>` : '';

  const body = railFrame('Evidence', forWhat, '', { sub: esc(String(entry.question).slice(0, 48)) });
  body.innerHTML = `
    <div class="rounded-sm border border-chrome-line p-s3">
      <div class="mb-s3 text-subtab">${esc(entry.question)}</div>
      ${facts || '<div class="text-chip text-chrome-muted">No facts on this envelope — the answer names no measurements.</div>'}
      ${graphvizBlock}
      ${factMermaid(env) ? `<button data-act="evidence-diagram"
        class="mb-s2 w-full cursor-pointer rounded-sm border border-accent bg-transparent px-[10px] py-[4px]
               text-chip text-accent-on-dark">${icon('maximize-2', { size: 13 })} Open diagram in pane</button>` : ''}
      <div class="mt-[10px] border-t border-chrome-line-soft pt-[9px] text-caps text-chrome-muted">
        Answered from <span class="tnum">${env.known_count ?? 0}</span> of
        <span class="tnum">${(env.known_count ?? 0) + (env.unknown_count ?? 0)}</span> measurements ·
        no retrieval
      </div>
    </div>`;
  out.querySelector('[data-act="evidence-diagram"]')
    ?.addEventListener('click', () => showDiagram(entry));
  out.querySelectorAll('[data-act="evidence-graphviz"]').forEach((btn) => {
    btn.addEventListener('click', () => showGraphvizPane(entry, btn.dataset.level));
  });
  out.querySelector('[data-act="evidence-graphviz-schema"]')?.addEventListener('change', (e) => {
    if (e.target.value) showGraphvizPane(entry, e.target.value);
  });
}

/* ════════════════════════════════════════════════════════════════════════
 * Boot
 * ════════════════════════════════════════════════════════════════════════ */

const ACTIVITY_LIMIT = 200;   // the endpoint's own default; le=1000
const RFA_LIMIT = 500;        // the endpoint's own default; le=2000

/** A page length turned into a count, or `"N+"` when the page filled. */
function countOf(settled, limit, key) {
  if (settled.status !== 'fulfilled') return null;
  const v = settled.value;
  const rows = Array.isArray(v) ? v : (v?.[key] ?? null);
  if (!Array.isArray(rows)) return null;
  return rows.length >= limit ? `${limit}+` : rows.length;
}

async function start() {
  // One banner for a dead session; the header stops saying "signed in" (session-banner.js).
  installSessionBanner(document, { onDead: () => { state.me = null; renderTopBar(); } });
  await loadIcons();
  applyWidths();
  initSeams();
  readUrl();
  renderIntentNav();
  renderRail();
  renderTopBar();

  // Everything below is independent; one failing must not take the frame
  // down with it. `allSettled`, and each consumer handles its own absence.
  state.investigation = currentInvestigation();

  const [me, projects, perspectives, activity, rfas, groups, investigations,
         workLists, analyses, allPerspectives] =
    await Promise.allSettled([
      getMe(),
      // The FULL list — every disposition, hidden included — because the
      // sidebar filters client-side and the default server filters would
      // make the `ignored`, `abandoned` and hidden facets permanently empty.
      listProjects({ includeIgnored: true, includeHidden: true }),
      listPerspectives(), listActivity(ACTIVITY_LIMIT), listRfas(),
      listGroups(), listInvestigations(), listWorkLists(),
      // Same bug class as openNotifyDialog's 'repo' hardcode (found live
      // 2026-09-28): `readUrl()` above may already have set
      // `state.resourceType` to 'db'/'filesystem' from a `?type=` URL param,
      // and analysis_catalog.yaml's `resource_types` genuinely differs per
      // entity type -- a repo-only fetch here leaves `state.analyses`
      // (read by trendSupport() and passed into openWorkList()) permanently
      // wrong for a session that starts on a non-repo resource.
      listAnalyses(apiEntityType(state.resourceType)),
      listAllPerspectives(),
    ]);

  if (me.status === 'fulfilled') state.me = me.value;
  if (perspectives.status === 'fulfilled') state.perspectives = perspectives.value || [];
  if (allPerspectives.status === 'fulfilled') state.allPerspectives = allPerspectives.value || [];
  // Both endpoints PAGE. A returned length equal to the limit is a page
  // that filled, not a total — rendering it as one would print an exact
  // figure that is exactly wrong, and nothing about the number would look
  // suspicious. Saturated counts are shown as "N+".
  state.counts.activity = countOf(activity, ACTIVITY_LIMIT, 'entries');
  state.counts.rfas = countOf(rfas, RFA_LIMIT, 'rfas');
  if (groups.status === 'fulfilled') state.groups = groups.value || [];
  if (workLists.status === 'fulfilled') state.workLists = workLists.value || [];
  if (analyses.status === 'fulfilled') state.analyses = analyses.value || [];
  if (investigations.status === 'fulfilled') state.investigations = investigations.value || [];
  if (projects.status === 'fulfilled') {
    state.projects = projects.value || [];
  }

  // The URL may already name a db/filesystem resource (`?type=db&resource=…`)
  // before either list has ever been fetched -- the batch above only ever
  // fetched repos, since most sessions never touch the other two chips.
  if (state.resourceType !== 'repo') {
    await ensureResourceListLoaded(state.resourceType);
  }

  // The investigation may have gone away since this browser last stored it.
  if (state.investigation
      && !state.investigations.some((i) => i.slug === state.investigation)) {
    state.investigation = '';
  }
  await loadWorkingSet();

  // "In scope" is the default chip, but only when it can mean anything.
  if (state.scope === 'working-set' && !state.investigation) state.scope = '';

  if (!state.selectedSlug) {
    const first = state.resourceType === 'repo'
      ? (visibleProjects()[0] || state.projects[0])
      : currentResourceRows()[0];
    if (first) state.selectedSlug = first.slug;
  }
  writeUrl();

  renderTopBar();
  renderIntentNav();
  renderPerspectiveRow();
  renderSidebar();
  renderRail();
  // The unread badge and the toast that jumps to an entry (PI-124). A failed first read leaves the badge empty.
  installHealthBanner(document);
  installBootstrapBanner(document);
  startActivityWatch({
    doc: document,
    first: activity.status === 'fulfilled' && Array.isArray(activity.value) ? activity.value : [],
    fetchEntries: activityWatchEntries(),
    openPanel: (opts) => openActivityPanel(opts),
  });
  await loadPane();
}

// See the button's comment in index.html: hideLogin() is itself the guard.
document.getElementById('login-dismiss-btn')
  ?.addEventListener('click', () => Auth.hideLogin());

// auth.js owns the gate: it either starts us now or after a successful
// sign-in. Nothing here renders before it says so.
Auth.init(start);
