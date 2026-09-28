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
import { ago, whenMs, verdictLineHtml, changedTimesHtml } from '/static/next/format.js';
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
// docs/design-notes/APP-JS-SPLIT-IMPLEMENTED.md. Investigation's stub
// (`stages/investigation.js`) is no longer empty — it now ports classic's
// Investigations tab (list/create/detail: members, dispositions,
// next-steps, purposes, classification, Egeria bind/promote/sync/
// reclassify) into /next; see that file's own header comment.
import { renderEnrichment } from '/static/next/stages/enrichment.js';
import { renderInvestigation, openInvestigationDetail } from '/static/next/stages/investigation.js';
import { loadChartsPane } from '/static/next/stages/understanding.js';
import { renderCurate } from '/static/next/stages/curate.js';
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
import { renderRail, renderRailScope } from '/static/next/chat.js';
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
  listSurveyDefinitions,
  getAnalysesIndex,
  getSurveyDashboards,
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
  getScoutingOverview,
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
  planPrerequisites,
  runPrerequisites,
  raiseCapabilityRfa,
  removeInvestigationMember,
  removeProject,
  removeEntity,
  runAnalysis,
  setDisposition,
  setEntityDisposition,
  getEntityDispositionHistory,
  setWorkingSetHidden,
  getContext,
  getJournal,
  questionKey,
  writeJournal,
  saveQuestionAnswer,
} from '/static/re-api.js';

/* ════════════════════════════════════════════════════════════════════════
 * State
 * ════════════════════════════════════════════════════════════════════════ */

export const state = {
  resourceType: 'repo',        // repo | db | filesystem
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
  me: null,
  counts: { activity: null, rfas: null },
  chat: [],                    // the transcript: one entry per turn
  promoted: null,              // a chat answer promoted into the pane
  charts: null,                // Understanding's probed chart index
  workLists: [],               // saved work lists
  workListSlug: null,          // the open one; the pane takes over when set
  lastWorkListSlug: null,      // the one you were last in, for the way back
  workListIndex: false,        // showing the list OF work lists
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
 *  would be the same class of bug as `unbuilt` (DEFECT-UNBUILT-STAGES-
 *  RENDER-AS-BUILT.md — read in three places, set in none) and #130's
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

/** Fact states, from surveyors/result_status.py. */
const MEASURED = 'measured';
const NOTHING_FOUND = 'nothing_found';
const NOT_ESTABLISHED = 'not_established';
const NEVER_RUN = 'never_run';
const NO_READER = 'no_reader';
const PARTIAL = 'partial';

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
function rowState(entry, env) {
  const kind = entry.kind || 'unknown';
  if (kind === 'gap') return 'no-surveyor';
  if (kind === 'human') return 'human';
  if (kind === 'unknown') return 'unclassified';
  if (env && env.answerable) {
    if (env.level_mismatch) return 'partial';
    return ['direct', 'registry', 'chart'].includes(kind) ? 'automatic' : 'answered';
  }
  return 'unrun';
}

/** Fully answered = a checkmark's worth of answer, not merely "something ran".
 *  A `level_mismatch` envelope has `answerable === true` (a real analysis DID
 *  run) but withholds the tick (design §18.3) -- callers that count or gate
 *  on "answered" must use this, not `env.answerable` alone, or the counter
 *  and the row glyph would disagree about the same envelope. */
function isFullyAnswered(env) {
  return !!(env && env.answerable && !env.level_mismatch);
}

const GLYPH = {
  answered: '✓',
  automatic: '✓',
  unrun: '○',
  partial: '◐',
  human: '⚠',
  'no-surveyor': '○',
  unclassified: '·',
  running: '◔',
  error: '✕',
  // §17.1 -- "this needs something else first, and it costs enough that it
  // needs your yes." Same glyph classic uses (⏵) for the same reason: a
  // proposal is a decision point, not a state of the world, so it earns its
  // own mark rather than borrowing unrun's ○ or human's ⚠.
  proposal: '⏵',
};

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
const STATE_TONE = {
  answered:      { paper: 'text-state-ok',    chrome: 'text-state-ok-on-dark' },
  partial:       { paper: 'text-accent-ink',  chrome: 'text-accent-on-dark' },
  automatic:     { paper: 'text-state-ok',    chrome: 'text-state-ok-on-dark' },
  unrun:         { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
  human:         { paper: 'text-accent-ink',  chrome: 'text-accent-on-dark' },
  'no-surveyor': { paper: 'text-state-gap',   chrome: 'text-state-gap-on-dark' },
  unclassified:  { paper: 'text-ink-muted',   chrome: 'text-chrome-muted' },
  running:       { paper: 'text-accent-ink',  chrome: 'text-accent-on-dark' },
  error:         { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
  // Same role as `unrun`/`error` -- "needs your attention" -- because a
  // proposal IS an attention-needing decision, not a different flavour of
  // answered or automatic.
  proposal:      { paper: 'text-state-warn',  chrome: 'text-state-warn-on-dark' },
};

const tone = (st, ground = 'paper') =>
  (STATE_TONE[st] || STATE_TONE.unclassified)[ground];

/** Verdict words the design sets at weight 600. Matched only at the head of
 *  a sentence and only when a separator follows, so a headline that merely
 *  starts with "No" as part of a phrase is left alone. Bolding the wrong
 *  word would assert a verdict the analysis did not make. */
const VERDICT = /^(Yes|No|Partly|Partially|Likely|Unlikely|Mixed|Narrowly maintained|Actively maintained)(\s*[—–,-]\s+)/;

/** Sentence case for a verdict word an analysis wrote in lower case. */
function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

function answerHtml(headline) {
  const m = VERDICT.exec(headline);
  if (!m) return tnum(esc(headline));
  const rest = headline.slice(m[0].length);
  return `<strong class="font-semibold">${esc(m[1])}</strong>${esc(m[2])}${tnum(esc(rest))}`;
}

/** The analysis's own prose, if it wrote any. Never assembled here. */
function prose(f) {
  const v = f.value || {};
  const text = v.detail || v.summary || v.description || '';
  return typeof text === 'string' ? text.trim() : '';
}

/** The scalar measures, relayed as `key value` pairs.
 *
 *  Deliberately last-resort. Structured fields (lists, objects) are NOT
 *  flattened into this line — they belong in `evidence`, where they can be
 *  read rather than skimmed. */
function scalarMeasures(value, max = 6) {
  if (!value || typeof value !== 'object') return '';
  const pairs = [];
  for (const [k, v] of Object.entries(value)) {
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'object') continue;
    if (k === 'verdict') continue;   // already used as the verdict word
    const shown = typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v);
    if (shown.length > 60) continue;
    pairs.push(`${k.replace(/_/g, ' ')} ${shown}`);
    if (pairs.length >= max) break;
  }
  return pairs.join(' · ');
}

/**
 * Turn an envelope into the lines a row shows.
 *
 * The rule this function exists to keep: a fact's state decides the SENTENCE,
 * not just an icon. `nothing_found` is knowledge — a measured zero — and says
 * so. `never_run` says nothing ran. `not_established` says the method could
 * not settle it. Folding any two of those together is the failure the
 * FactLayer was built to prevent, and it would be undone here.
 */
function readEnvelope(entry, env) {
  const facts = (env && env.facts) || [];
  const known = facts.filter((f) => f.is_known);
  const lines = {
    answer: '', caveat: '', sources: [], lastRun: '', canRun: [],
    // Analyses that measured something but have no written summary, so the
    // answer line above is raw measures. Named, because the fix is a
    // headline_reader on that analysis and nobody can act on "somewhere".
    unwritten: [],
    // True when at least one fact is known but no fact recorded WHEN it ran.
    // Missing timestamp is a gap in the record, NOT evidence that nothing
    // ran — printing "never run" here would report a fact about the row as
    // a fact about the repository.
    runTimeUnrecorded: false,
  };

  for (const f of facts) {
    if (f.can_run && f.can_run.length) lines.canRun.push(...f.can_run);
    if (f.analysis_id) lines.sources.push(f.analysis_id);
    if (f.last_run_at && (!lines.lastRun || whenMs(f.last_run_at) > whenMs(lines.lastRun))) lines.lastRun = f.last_run_at;
  }
  lines.sources = [...new Set(lines.sources)];
  lines.canRun = [...new Set(lines.canRun)];

  // The answer, in preference order. Every rung RELAYS something the
  // analysis wrote; none of them composes a verdict here.
  //
  //   1. `headline`  — the analysis's own summary sentence.
  //   2. `value.detail` / `value.summary` — also its own prose, with
  //      `value.verdict` as the bolded verdict word when it states one.
  //   3. the scalar measures themselves, plus a caveat saying the analysis
  //      has no written summary.
  //
  // Rung 3 exists because most analyses do not have a headline_reader wired
  // up yet, and a row that shows a tick with nothing beside it is the exact
  // shape this screen was built to stop: a claim of "answered" with no
  // answer under it.
  const sentences = [];
  for (const f of known) {
    if (f.state === NOTHING_FOUND && !f.headline && !prose(f)) {
      // A measured zero. Said in words, because the bare number reads as
      // "we didn't look".
      sentences.push(tnum(esc(`${f.analysis_id} ran and found nothing.`)));
      continue;
    }
    if (f.headline) { sentences.push(answerHtml(f.headline)); continue; }
    const p = prose(f);
    if (p) {
      // The analysis's own verdict word, set at weight 600 like the design's
      // "Yes". Marked up here rather than re-detected from the joined string
      // downstream: this is the one place that knows the word came from a
      // `verdict` field rather than from the first word of a sentence.
      const verdict = f.value && f.value.verdict;
      sentences.push(verdict
        ? `<strong class="font-semibold">${esc(cap(String(verdict)))}</strong> — ${tnum(esc(p))}`
        : tnum(esc(p)));
      continue;
    }
    const scalars = scalarMeasures(f.value);
    if (scalars) {
      sentences.push(tnum(esc(scalars)));
      lines.unwritten.push(f.analysis_id);
    }
  }
  // NOTE: `lines.answer` is HTML, already escaped by each branch above.
  // Do not run it through esc() or answerHtml() again downstream.
  // Joined on ' · ', not ' '. Six analyses' sentences run together read as
  // one broken sentence — "all 3 checks pass one contributor writes most of
  // the code" — and a reader cannot tell where one claim ends. The separator
  // makes the boundaries visible without deciding how the claims combine,
  // which is the judgement still owed (Dashboard Round Three).
  lines.answer = sentences.join(' · ');

  // The caveat — the most important content on the screen. These sentences
  // already exist in the survey output; they used to sit three panes away in
  // the chat rail, which is not where the decision is made.
  // A caveat on a multi-analysis row names its analysis, or it does not
  // render (designer, SPEC-ACTIONABLE-AND-HONEST.md point 5): "This
  // analysis ran and found nothing" reads as a claim about whichever
  // number sits above it when three analyses answer one question, and it
  // is usually a claim about a DIFFERENT one. `f.note` is the analysis's
  // own prose, written as if it would be read alone -- attributed here,
  // not rewritten, since the sentence itself is correct and only its
  // referent was ambiguous.
  const attribute = (f, text) => (facts.length > 1 ? `${f.analysis_id} — ${text}` : text);
  const caveats = [];
  for (const f of facts) {
    if (f.note) caveats.push(attribute(f, f.note));
    if (f.state === PARTIAL && !f.note) {
      caveats.push(`${f.analysis_id} covered only part of what it measures.`);
    }
    if (f.state === NOT_ESTABLISHED && !f.note) {
      caveats.push(`${f.analysis_id} ran but could not establish a result.`);
    }
  }
  // A fact offered as evidence rather than as the answer says so, because
  // whether a resource REPLACES something you already have is a decision
  // about intent that no query settles.
  if (known.some((f) => f.evidence_only)) {
    caveats.push('Offered as evidence for a judgement, not as the judgement.');
  }
  lines.caveat = [...new Set(caveats)].join(' ');

  // Which facts have NOT run, when some have. A partly-answered row that
  // shows only the answered half is the confident-wrong-answer shape.
  const unrun = facts.filter((f) => f.state === NEVER_RUN).map((f) => f.analysis_id);
  if (known.length && unrun.length) {
    lines.caveat = `${lines.caveat} ${unrun.join(', ')} ${unrun.length === 1 ? 'has' : 'have'} not run, so this answer is partial.`.trim();
  }
  if (lines.unwritten.length) {
    lines.caveat = `${lines.caveat} ${lines.unwritten.join(', ')} ${
      lines.unwritten.length === 1 ? 'has' : 'have'} no written summary — the figures above are the raw measures.`.trim();
  }

  lines.runTimeUnrecorded = known.length > 0 && !lines.lastRun;
  lines.mermaid = factMermaid(env);

  return lines;
}

/* ════════════════════════════════════════════════════════════════════════
 * Chrome
 * ════════════════════════════════════════════════════════════════════════ */

function renderTopBar() {
  $('scope-slug').textContent = state.selectedSlug || 'no resource selected';
  // `data-entity-type` lets feedback.js's Questions-checklist "Was this
  // right?" bar (deliberately independent of this module, plain DOM reads
  // only) attribute an answer-feedback POST to the right resource type
  // without importing state itself. Kept alongside the slug it already reads
  // off this same element so the two can never fall out of sync.
  $('scope-slug').dataset.entityType = apiEntityType(state.resourceType) || 'repo';
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
  // Same resource in the other UI. index.html has no deep link for a
  // selected repo, so this is the app root — named, so nobody thinks the
  // selection carried over when it did not.
  link.textContent = state.selectedSlug
    ? '/next · open current UI'
    : '/next · open current UI';
  wireActivityButton();
  wireAdminButton();
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

function renderIntentNav() {
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
      state.stage = b.dataset.stage;
      writeUrl();
      renderIntentNav();
      loadPane();
    });
  });
}

/** The list OF work lists — the front door the matrix never had. */
function workListIndexHtml() {
  if (!state.workLists.length) {
    return `${subTabsHtml()}
      <h3 class="m-0 font-heading text-name font-normal">No work lists yet</h3>
      <div class="my-s3 h-px bg-rule"></div>
      <p class="max-w-[70ch] text-answer text-ink">
        A work list is a set of resources you compare as rows x questions, run a
        survey across, and narrow down. Make one from the sidebar:
        <strong>Select</strong>, tick some repos, then <strong>save as work list</strong>.
      </p>`;
  }
  return `${subTabsHtml()}
    <h3 class="m-0 font-heading text-name font-normal">Work lists</h3>
    <div class="my-s3 h-px bg-rule"></div>
    <div class="flex flex-col">
      ${state.workLists.map((w) => `<button data-open-wl="${esc(w.slug)}"
        class="cursor-pointer border-b border-rule bg-transparent py-s3 text-left hover:bg-accent-tint">
        <div class="font-heading text-question font-semibold text-ink">${esc(w.display_name)}</div>
        <div class="text-provenance text-ink-muted">
          <span class="tnum">${w.member_count}</span> resources
          ${w.investigation ? ` · ${esc(w.investigation)}` : ''}
          ${w.derived_from ? ` · narrowed from ${esc(w.derived_from)}` : ''}
          · ${w.egeria_guid ? 'published to Egeria' : 'not published'}
        </div>
      </button>`).join('')}
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
  const n = state.workLists.length;
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
function loadScript(src) {
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
  if (turn.mermaid) return 'diagram';
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
export async function drawChart(entry) {
  const body = $('chart-body');
  if (!body || !entry) return;
  body.innerHTML = `<div class="text-caveat text-ink-muted">Drawing ${esc(entry.label)}…</div>`;
  try {
    await loadScript('/static/vendor/plotly.min.js');
    body.innerHTML = `<div id="chart-canvas" style="height:min(62vh,560px)"></div>
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
    await window.Plotly.newPlot($('chart-canvas'), timeAxisData(entry),
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
function chartLayout(layout = {}) {
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

/** The Mermaid source a fact carries, if it carries any.
 *
 *  `architecture_diagram` writes its source into the fact value, so the
 *  relationship question ("How do its components relate to each other?") has
 *  a real diagram sitting behind it. This is the product path to a diagram —
 *  a chat answer is not the only one, and wiring promotion ONLY to chat left
 *  the feature unreachable for anyone who had not asked a question first.
 */
function factMermaid(env) {
  for (const f of (env && env.facts) || []) {
    const src = f.value && (f.value.mermaid || f.value.diagram);
    if (typeof src === 'string' && src.trim()) {
      return { source: src.trim(), analysisId: f.analysis_id, lastRun: f.last_run_at || '' };
    }
  }
  return null;
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
      const t = tokens();
      const prepped = mermaidForKroki(turn.mermaid);
      // Server-side render via Kroki — the browser never loads mermaid.js.
      const res = await fetch('/api/diagrams/mermaid', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: prepped.source }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
        throw new ApiError(res.status, detail, '/api/diagrams/mermaid');
      }
      // The endpoint returns the RAW SVG body as image/svg+xml, not JSON —
      // it is a thin proxy to Kroki and hands back exactly what Kroki sent.
      const raw = await res.text();
      if (!raw.includes('<svg')) throw new Error('the renderer returned no SVG');

      body.innerHTML = `
        <div id="promoted-svg" class="w-full overflow-hidden rounded-sm border border-rule-strong"
          style="height:min(70vh,640px);background:${t.paper}">${raw}</div>
        <div id="diagram-note" class="mt-s2 text-provenance text-ink-muted"></div>`;

      await loadScript('/static/vendor/svg-pan-zoom.min.js');
      const svgEl = body.querySelector('#promoted-svg svg');
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
      const noteEl = $('diagram-note');
      if (noteEl) noteEl.innerHTML = note.join(' · ');
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
    ${row('cloud', 'published', 'in the Egeria catalog')}
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
  db: 'Discover databases on a registered server',
  filesystem: 'Register a filesystem path',
};

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

function renderSidebar() {
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
      <button data-act="find-repos" title="${esc(FIND_TITLE[state.resourceType] || FIND_TITLE.repo)}"
        aria-label="${esc(FIND_TITLE[state.resourceType] || FIND_TITLE.repo)}"
        class="ml-auto cursor-pointer bg-transparent text-chrome-muted hover:text-chrome-ink"
        >${icon('circle-plus', { size: 14 })}</button>
      <button data-act="mark-key" title="What the marks in this list mean"
        aria-label="What the marks in this list mean"
        class="cursor-pointer bg-transparent text-chrome-muted hover:text-chrome-ink"
        >${icon('circle-help', { size: 14 })}</button>
    </div>
    ${state.showMarkKey ? markKeyHtml() : ''}

    ${investigationBarHtml()}

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
      <button data-act="select-mode" class="cursor-pointer bg-transparent ${
        state.selectMode ? 'text-accent-on-dark' : 'text-chrome-muted hover:text-chrome-ink'}"
        >${state.selectMode ? '☑ Selecting' : '☐ Select'}</button>
      ${hiddenCount ? `<button data-act="show-hidden" class="cursor-pointer bg-transparent ${
        state.showHidden ? 'text-accent-on-dark' : 'text-chrome-muted hover:text-chrome-ink'}"
        >Show hidden <span class="tnum">${hiddenCount}</span></button>` : ''}
      <span class="ml-auto"><span class="tnum">${visible.length}</span> shown</span>
    </div>

    ${state.selectMode ? selectActionsHtml() : ''}

    ${state.workLists.length ? `
      <div class="mb-[7px] font-heading uppercase tracking-caps text-caps text-chrome-muted">
        Work lists · <span class="tnum">${state.workLists.length}</span>
      </div>
      <div class="mb-s4 flex flex-col gap-[1px]">
        ${state.workLists.map((w) => `<button data-worklist="${esc(w.slug)}"
          class="cursor-pointer truncate bg-transparent px-2 py-[5px] text-left ${
            w.slug === state.workListSlug
              ? 'border-l-2 border-accent bg-chrome-surface text-chrome-ink'
              : 'border-l-2 border-transparent text-chrome-ink hover:bg-chrome-surface'}"
          >${esc(w.display_name)} <span class="tnum text-chrome-muted">${w.member_count}</span>${
            w.egeria_guid ? ` ${icon('cloud', { size: 12, cls: 'text-state-ok-on-dark', title: 'Published to Egeria' })}` : ''}</button>`).join('')}
      </div>` : ''}

    ${!loaded ? `
      <div class="text-chip text-chrome-ink">Loading ${nonRepoLabel}s…</div>`
    : visible.length === 0 ? `
      <div class="text-chip text-chrome-ink">${
        currentResourceRows().length
          ? 'Nothing matches these filters.'
          : state.resourceType === 'repo' ? 'Nothing matches these filters.' : `No ${nonRepoLabel}s registered.`}</div>`
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
              >${esc(p.display_name || p.slug)}</button>
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
 * docs/design-notes/GROUPS-ADMIN-IMPLEMENTED.md. Admin → Groups assigns
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

/** The Select-mode action bar. Every action here is a bulk write, so each
 *  says plainly what it touches — "remove from scope" and "delete" differ by
 *  everything, and the current UI's own tooltips are the wording. */
function selectActionsHtml() {
  const n = state.selected.size;
  return `<div class="mb-s3 flex flex-wrap items-baseline gap-s2 text-caps">
    <button data-act="sel-all" class="cursor-pointer bg-transparent text-chrome-ink underline">All shown</button>
    <button data-act="sel-none" class="cursor-pointer bg-transparent text-chrome-ink underline">None</button>
    <span class="text-chrome-muted"><span class="tnum">${n}</span> selected</span>
    <div class="flex w-full flex-wrap gap-s2 pt-s1">
      <button data-act="sel-scope-add" ${state.investigation ? '' : 'disabled'}
        title="${state.investigation ? 'Add to the current investigation’s scope' : 'Needs a current investigation'}"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-on-dark ${
          state.investigation ? '' : 'border-dashed opacity-100'}">＋ scope</button>
      <button data-act="sel-scope-remove" ${state.investigation ? '' : 'disabled'}
        title="Remove from scope — the repo itself is untouched"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink">− scope</button>
      <button data-act="sel-hide"
        title="Hide from your own list. A view preference, not a judgement"
        class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[2px] text-chrome-ink">hide</button>
      <select data-act="sel-disposition"
        title="Set the disposition on every selected repo"
        class="rounded-sm border border-chrome-line bg-chrome px-2 py-[2px] text-chrome-ink">
        <option value="">mark as…</option>
        ${VALID_DISPOSITIONS.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}
      </select>
      <button data-act="sel-worklist"
        title="Save the selected resources as a work list you can run, compare and narrow"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-on-dark">save as work list</button>
      <button data-act="sel-delete"
        title="Unregister entirely and delete all local survey data"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-on-dark">delete…</button>
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
  el.querySelectorAll('button[data-worklist]').forEach((b) => b.addEventListener('click', () => {
    state.workListSlug = b.dataset.worklist;
    renderSidebar();
    loadPane();
  }));
  el.querySelectorAll('button[data-slug]').forEach((b) => b.addEventListener('click', () => {
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
    'sel-all': () => { visibleRows().forEach((p) => state.selected.add(p.slug)); renderSidebar(); },
    'sel-none': () => { state.selected.clear(); renderSidebar(); },
    'sel-scope-add': () => bulkScope(true),
    'sel-scope-remove': () => bulkScope(false),
    'sel-hide': () => bulkHide(),
    'sel-delete': () => confirmBulkDelete(),
    'sel-worklist': () => saveSelectionAsWorkList(),
  };
  for (const [name, fn] of Object.entries(acts)) {
    el.querySelector(`[data-act="${name}"]`)?.addEventListener('click', fn);
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

async function bulkScope(add) {
  if (!state.investigation || !state.selected.size) return;
  const slugs = [...state.selected];
  const entityType = apiEntityType(state.resourceType);
  const failed = [];
  for (const slug of slugs) {
    try {
      if (add) await addInvestigationMember(state.investigation, entityType, slug);
      else await removeInvestigationMember(state.investigation, entityType, slug);
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  await loadWorkingSet();
  // Report per-resource, never "done": a bulk write where some calls failed
  // and the banner says success is how a partial write becomes invisible.
  sidebarNote(failed.length
    ? `<span class="text-accent-on-dark"><span class="tnum">${slugs.length - failed.length}</span>
       of <span class="tnum">${slugs.length}</span> ${add ? 'added' : 'removed'};
       ${esc(failed.join('; '))}</span>`
    : `<span class="tnum">${slugs.length}</span> ${add ? 'added to' : 'removed from'} scope.`);
  renderSidebar();
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
 * Delete is the one action with no undo anywhere in the stack: the endpoint
 * takes no confirmation flag, drops the resource's pgvector collections and
 * removes the registry row. So the confirmation has to be here, it has to
 * name what is going, and it must not be a one-click button.
 *
 * Repo/database/filesystem deletion are three genuinely different registry
 * operations (`removeProject`/`removeDatabase`/`removeFilesystem` in
 * re-api.js, each hitting its own DELETE route) — `removeEntity()` dispatches
 * by `apiEntityType()`-translated type, the same pattern `POST /{slug}/group`
 * already uses server-side (projects.py).
 */
function confirmBulkDelete() {
  const slugs = [...state.selected];
  if (!slugs.length) return;
  const noun = state.resourceType === 'repo' ? 'repo'
    : state.resourceType === 'db' ? 'database' : 'filesystem';
  sidebarNote(`
    <div class="text-accent-on-dark">Unregister <span class="tnum">${slugs.length}</span>
      ${noun}${slugs.length === 1 ? '' : 's'} and delete all local survey data?
      This cannot be undone.</div>
    <div class="mt-s1 break-words text-chrome-muted">${esc(slugs.join(', '))}</div>
    <div class="mt-s2 flex gap-s2">
      <button data-act="sel-delete-confirm"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-on-dark">Delete</button>
      <button data-act="sel-delete-cancel"
        class="cursor-pointer bg-transparent text-chrome-ink underline">Cancel</button>
    </div>`);
  $('sidebar-action').querySelector('[data-act="sel-delete-confirm"]')
    .addEventListener('click', () => bulkDelete(slugs));
  $('sidebar-action').querySelector('[data-act="sel-delete-cancel"]')
    .addEventListener('click', () => { $('sidebar-action').innerHTML = ''; });
}

async function bulkDelete(slugs) {
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
  const name = window.prompt(
    `Name for a work list of ${slugs.length} resource(s):`,
    state.investigation ? `${state.investigation} candidates` : 'Candidates');
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
  if (!state.investigation) { state.workingSet = new Set(); return; }
  try {
    const members = await listInvestigationMembers(state.investigation);
    state.workingSet = new Set(
      (members || []).filter((m) => (m.entity_type || 'repo') === 'repo')
                     .map((m) => m.entity_slug));
  } catch {
    // Unknown, and kept unknown: an empty working set and an unreadable one
    // are different, and "In scope" showing nothing because a call failed
    // would read as "this investigation has no members".
    state.workingSet = new Set();
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
function railStale(ticket) { return ticket !== railTicket; }

export function railFrame(kind, forWhat, bodyHtml, { sub = '', actions = '' } = {}) {
  const out = $('rail-evidence');
  if (!out) return null;
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

function writeUrl() {
  if (_restoringUrl) return;
  const p = new URLSearchParams();
  if (state.resourceType !== 'repo') p.set('type', state.resourceType);
  if (state.selectedSlug) p.set('resource', state.selectedSlug);
  if (state.stage !== 'scouting') p.set('stage', state.stage);
  if (state.subTab !== 'questions') p.set('tab', state.subTab);
  if (state.workListSlug) p.set('worklist', state.workListSlug);
  if (state.activePerspectives.size) p.set('perspectives', [...state.activePerspectives].join(','));
  const url = `${location.pathname}${p.toString() ? `?${p}` : ''}`;
  history.replaceState(null, '', url);
}

/** Read the URL into state. Returns true if it named a resource. */
function readUrl() {
  const p = new URLSearchParams(location.search);
  _restoringUrl = true;
  try {
    if (p.get('type')) state.resourceType = p.get('type');
    if (p.get('resource')) state.selectedSlug = p.get('resource');
    if (p.get('stage')) state.stage = p.get('stage');
    if (p.get('tab')) state.subTab = p.get('tab');
    if (p.get('worklist')) state.workListSlug = p.get('worklist');
    const persp = p.get('perspectives');
    if (persp) state.activePerspectives = new Set(persp.split(',').filter(Boolean));
  } finally {
    _restoringUrl = false;
  }
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
 * Name, external links, provenance, and the write paths.
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
  // both project site and docs. There is no separate docs field to show.
  const homepage = state.overview?.slug === slug ? state.overview.homepage : '';
  if (homepage) {
    links.push(`<a href="${esc(homepage)}" target="_blank" rel="noopener noreferrer"
      class="text-accent-ink underline">Project site ↗</a>`);
  }

  const surveyed = p?.last_surveyed_at
    ? `surveyed <span class="tnum">${esc(ago(p.last_surveyed_at))}</span>`
    : 'never surveyed';
  const ov = state.overview?.slug === slug ? state.overview : null;
  let published = p?.is_published ? 'published to Egeria' : 'not published to Egeria';
  if (ov?.last_published_at && p?.is_published) {
    published += ` <span class="tnum">${esc(ago(ov.last_published_at))}</span>`;
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
      + ` the catalog entry cannot be reached</span>`;
  }
  // Fourth publish state (PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md /
  // REPLY-PUBLISH-STATE-GO-AHEAD.md §4): the DATE stays — the row is the only
  // evidence a publish happened — and the sentence states what was observed
  // (a failed resolve), never why (a wipe and an individual deletion look
  // identical from here). No "publish again" button here: /next has no
  // publish trigger of its own yet to wire it to; the Analysis pane's
  // existing publish action is where that stands until one exists.
  if (ov?.publish_stale) {
    published += ` <span class="text-accent-ink">⚠ these elements are no longer in the store —`
      + ` publish again from the Analysis pane</span>`;
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
  if (cap && (cap.table_total || cap.schema_total)) {
    const thin = (cap.table_select ?? 0) < (cap.table_total ?? 0)
      || (cap.schema_visible ?? 0) < (cap.schema_total ?? 0);
    credentialBanner = `
      <div class="mt-s1 text-provenance ${thin ? 'text-accent-ink' : 'text-ink-muted'}">
        connected as <span class="font-mono">${esc(cap.connected_as || '(unknown)')}</span> —
        sees ${esc(String(cap.schema_visible ?? 0))} of ${esc(String(cap.schema_total ?? 0))} schema(s),
        SELECT on ${esc(String(cap.table_select ?? 0))} of ${esc(String(cap.table_total ?? 0))} table(s)
        ${thin ? '· every count on this page is scoped to this credential, not the whole database' : ''}
      </div>`;
  }

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
        <button data-act="hide" class="cursor-pointer bg-transparent text-accent-ink underline">
          ${p?.working_set_hidden ? 'unhide' : 'hide'}
        </button>
        <button data-act="remove" class="cursor-pointer bg-transparent text-accent-ink underline">remove</button>
      </span>
    </div>
    <div class="mt-s1 text-provenance text-ink-muted">${surveyed} · ${published}</div>
    ${credentialBanner}
    <div id="resource-action" class="mt-s2"></div>`;
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
      try { const started = await runAnalysis(p.slug, id); if (started?.run_id) runIds.push(started.run_id); }
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
function wireDispositionPicker(host, p, { note, onSet }, entityType = 'repo', entitySlug = '') {
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

/** The header's three write paths. `hide` is reversible, `disposition` is a
 *  judgement, `remove` is neither — so only one of them asks. */
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

  el.querySelector('[data-act="hide"]')?.addEventListener('click', async () => {
    const hiding = !p?.working_set_hidden;
    note(hiding ? 'Hiding…' : 'Unhiding…');
    try {
      // Generalized alongside the sidebar's bulk "hide" action -- this used
      // to hard-code 'repo' regardless of the selected resource type, which
      // silently hid the WRONG row whenever a database/filesystem happened
      // to share a slug with a repo (`resource_working_set` is keyed on
      // (entity_type, entity_slug), so a wrong entity_type is a wrong key,
      // not a 404).
      await setWorkingSetHidden(apiEntityType(state.resourceType), state.selectedSlug, hiding);
      if (p) p.working_set_hidden = hiding;
      renderSidebar();
      await loadPane();
      $('resource-action').innerHTML = `<div class="text-caveat text-ink">${
        hiding
          ? 'Hidden from your list. Still registered, and nothing was deleted — “Show hidden” in the sidebar brings it back.'
          : 'Back in your list.'}</div>`;
    } catch (err) {
      note(`<span class="text-accent-ink">Not saved: ${esc(err.message)}</span>`);
    }
  });

  el.querySelector('[data-act="remove"]')?.addEventListener('click', () => {
    slot.innerHTML = `
      <div class="text-caveat text-accent-ink">
        Unregister <span class="font-mono">${esc(state.selectedSlug)}</span> and delete all its
        local survey data? This cannot be undone, and it is not the same as marking it
        <em>ignored</em> — an ignored repo stays registered and can come back.
      </div>
      <div class="mt-s2 flex gap-s3 text-caveat">
        <button data-act="remove-confirm"
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Remove</button>
        <button data-act="remove-cancel" class="cursor-pointer bg-transparent text-ink underline">Cancel</button>
      </div>`;
    slot.querySelector('[data-act="remove-cancel"]')
      .addEventListener('click', () => { slot.innerHTML = ''; });
    slot.querySelector('[data-act="remove-confirm"]').addEventListener('click', async () => {
      const slug = state.selectedSlug;
      note('Removing…');
      try {
        await removeProject(slug);
        state.projects = state.projects.filter((x) => x.slug !== slug);
        state.selectedSlug = state.projects[0]?.slug || null;
        renderSidebar();
        renderTopBar();
        renderRailScope();
        await loadPane();
      } catch (err) {
        note(`<span class="text-accent-ink">Not removed: ${esc(err.message)}</span>`);
      }
    });
  });
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
function subTabsHtml() {
  return `<div class="mb-s4 flex flex-wrap items-baseline gap-s3 font-heading text-subtab">
    ${SUB_TABS.filter((t) => !t.resourceTypes || t.resourceTypes.includes(state.resourceType)).map((t) => {
      if (t.id === state.subTab) {
        return `<span class="border-b border-accent pb-[2px] text-ink">${t.label}</span>`;
      }
      // DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md §3: a sub-tab's own `built`
      // flag said nothing about whether the STAGE it's shown under is
      // built, so all four module-level built:true SUB_TABS advertised
      // working panes on all six unbuilt stages too. Live only when the tab
      // AND the current stage are built.
      const stageDef = STAGES.find((s) => s.id === state.stage);
      if (stageDef?.built && (t.id === 'questions' || t.built)) {
        return `<button data-subtab="${t.id}" class="cursor-pointer bg-transparent text-ink hover:text-accent-ink">${t.label}</button>`;
      }
      return `<button data-deferred="${t.id}"${deferredAttrs(false, { title: `${t.does} — not built in /next`, extraStyle: 'padding-bottom:1px' })}
        class="cursor-pointer bg-transparent text-ink-muted">${t.label}</button>`;
    }).join('')}
  </div>`;
}

/* The header no longer says "N of these are not built in /next". That
 * sentence was review-speak — correct in a handoff, unreadable in the
 * product to anyone who was not in the conversation — and the dashed
 * underline on a deferred tab already carries the fact (design rule 6). */
/** Sub-tab clicks: the real one switches, a deferred one says it is deferred. */
export function bindSubTabs() {
  const el = $('content');
  el.querySelectorAll('[data-subtab]').forEach((b) => b.addEventListener('click', () => {
    state.subTab = b.dataset.subtab;
    writeUrl();
    loadPane();
  }));
  el.querySelectorAll('[data-deferred]').forEach((b) => b.addEventListener('click', () => {
    state.subTab = b.dataset.deferred;
    writeUrl();
    loadPane();
  }));
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
    el.innerHTML = deferredPaneHtml(
      { label: 'Schema Inventory', does: 'Only databases have a schema tree to show' });
    bindSubTabs();
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
    <div id="schema-tree">Reading the schema tree…</div>`;
  bindSubTabs();

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
  empty: 'empty',
};

//: Table-kind labels -- quiet, muted words distinguishing a base table from
//: a view/materialized view (Dan's gate, 2026-09-27: he asked for a way to
//: tell them apart at a glance without being didactic about it).
const _TABLE_KIND_LABELS = {
  'BASE TABLE': 'table', 'VIEW': 'view', 'MATERIALIZED VIEW': 'matview',
  'FOREIGN': 'foreign table',
};

function schemaTreeHtml(schemas) {
  if (!schemas.length) return `<div class="text-caveat text-ink-muted">No stored schema rows yet — run a survey first.</div>`;
  const parts = schemas.map((s) => {
    if (s.classification === 'system') {
      return `<div class="mb-s1 text-caveat text-ink-muted" data-tree-node data-tree-text="system">
        ${esc(String(s.system_count))} system schema(s) folded (pg_catalog, information_schema, pg_toast*, pg_temp*)</div>`;
    }
    const stamp = s.classification === 'data'
      ? `${s.table_count} table(s) · ${Number(s.row_total || 0).toLocaleString('en-US')} row(s)${s.is_estimate ? ' (est.)' : ''}`
      : `${s.table_count} table(s) — ${_SCHEMA_SHORTFALL_LABELS[s.classification] || s.classification}`;
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
      </summary>
      ${s.reason ? `<div class="ml-s3 mt-[4px] text-provenance text-ink-muted">${esc(s.reason)}</div>` : ''}
      <div class="ml-s3 mt-s2">${(s.tables || []).map(tableHtml).join('') || '<span class="text-caveat text-ink-muted">No tables.</span>'}</div>
    </details>`;
  });
  return parts.join('');
}

function tableHtml(t) {
  const rowStamp = t.row_count == null
    ? 'not measured'
    : `${Number(t.row_count).toLocaleString('en-US')} row(s)${t.row_count_state === 'catalog_estimate' ? ' (est.)' : ''}`;
  const byteStamp = t.size_bytes == null ? 'not measured' : fmtBytes(t.size_bytes);
  const kindLabel = _TABLE_KIND_LABELS[t.table_type] || 'table';
  // Own name only -- see schemaTreeHtml's comment above on why this is no
  // longer the table+columns concatenation it used to be.
  return `<details class="mb-s1" data-tree-node data-tree-text="${esc(t.name.toLowerCase())}">
    <summary class="cursor-pointer text-ink">
      ${esc(t.name)}
      <span class="text-caveat text-ink-muted"> ${esc(kindLabel)}</span>
      <span class="text-provenance text-ink-muted"> — ${esc(rowStamp)} · ${esc(byteStamp)} · ${t.column_count} column(s)</span>
    </summary>
    <table class="ml-s3 mt-[4px] w-full max-w-[70ch] border-collapse text-caveat">
      ${(t.columns || []).map((c) => `<tr class="border-b border-rule" data-tree-node data-tree-text="${esc(c.name.toLowerCase())}">
        <td class="py-[3px] pr-s2 font-mono text-ink">${esc(c.name)}</td>
        <td class="py-[3px] pr-s2 text-ink-muted">${esc(c.type)}</td>
        <td class="py-[3px] pr-s2 text-ink-muted">${c.nullable === null ? 'nullable unknown' : (c.nullable ? 'nullable' : 'not null')}</td>
        <td class="py-[3px] pr-s2 text-accent-ink">${esc(c.key_role || '')}</td>
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
function filterSchemaTree(raw) {
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
      <span class="text-provenance text-ink-muted">what was catalogued, and what was written down · a snapshot, not a query</span>
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
async function renderRecords(slug, entityType = 'repo') {
  const host = $('records');
  if (!host) return;
  let recs;
  try { recs = (await listRecords(slug, entityType)).records || []; }
  catch (err) { host.innerHTML = `<span class="text-state-warn">The records could not be read: ${esc(err.message)}</span>`; return; }
  if (slug !== state.selectedSlug) return;
  if (!recs.length) { host.textContent = 'No record has been written for this resource yet — nothing catalogued, nothing written down.'; return; }
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
        ${recordActsHtml(r, rowCount(rep))}
        ${recordUsesHtml(r)}
      </div>`;
    }
    // curateRecordHtml carries the author, date and state line itself.
    return `<div class="border-t border-rule py-s2" data-record="${esc(r.id)}">
      <div class="text-answer text-ink">catalogued${r.manifest?.entities?.length ? ` · ${esc(r.manifest.entities.join(', '))}` : ''}</div>
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
function recordActsHtml(r, n) {
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  return `<div data-record-acts class="mt-s2 text-caveat">
    <div class="text-ink"><span data-record-scope>The whole report · <span class="tnum">${n}</span> row${n === 1 ? '' : 's'}</span>
      <span class="text-ink-muted">· as recorded ${esc(String(r.requested_at).slice(0, 10))}${r.out_of_date ? ' · its evidence has since moved — what these create will say so' : ''}</span></div>
    <div class="mt-[3px] flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-provenance">
      <button data-record-act="work_list" ${me ? '' : 'disabled'} class="cursor-pointer bg-transparent p-0 text-accent-ink underline">add to work list</button>
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
    const what = u.act === 'work_list' ? `added to work list “${esc(u.target_name)}”`
      : u.act === 'rfa' ? `raised RFA “${esc(u.target_name)}”`
      : u.act === 'journal' ? 'cited in the journal' : esc(u.act);
    return `<div class="text-provenance text-ink-muted">Used · ${what} · <span class="tnum">${esc(String(u.at).slice(5, 16).replace('T', ' '))}</span> · ${esc(u.by)}</div>`;
  });
  if (r.corrected_by?.id) {
    uses.push(`<div class="text-provenance text-ink-muted">Corrected by “${esc(r.corrected_by.name)}” · <span class="tnum">${esc(String(r.corrected_by.at).slice(5, 10))}</span> · ${esc(r.corrected_by.by || '')}</div>`);
  }
  return uses.length ? `<div class="mt-s1">${uses.join('')}</div>` : '';
}

function wireRecordActs(host, slug, recs, entityType = 'repo') {
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
      b.disabled = true; status.textContent = '…';
      try {
        const out = await actOnRecord(slug, id, { action, rows }, entityType);
        const where = action === 'work_list' ? `now in “${out.name}”` : `RFA ${String(out.rfa).slice(0, 8)} raised, pointing at this record`;
        status.innerHTML = `<span class="text-state-ok">→ ${esc(where)}</span>`;
        await renderRecords(slug, entityType);
      } catch (err) {
        b.disabled = false;
        status.innerHTML = `<span class="text-accent-ink">not recorded${err.status === 401 ? ' — sign in to act on a report' : `: ${esc(err.message)}`}</span>`;
      }
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

function renderJournalWrite(slug, entityType = 'repo') {
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
      <button id="journal-save" type="button"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-answer text-accent-ink">Write</button>
      <span class="text-provenance text-ink-muted">${who ? `as ${esc(who)}` : 'sign in to write — an entry needs an author'}
        · a suggestion is a work-list entry for them, not a notification</span>
    </div>`;
  $('journal-save').addEventListener('click', async () => {
    const body = $('journal-body').value.trim();
    if (!body) { $('journal-body').focus(); return; }
    const targets = [...host.querySelectorAll('[data-suggest]:checked')].map((c) => c.dataset.suggest);
    const person = ($('journal-person').value || '').trim();
    if (person) targets.push(person);
    const b = $('journal-save'); b.disabled = true; b.textContent = 'writing…';
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
      b.disabled = false; b.textContent = 'Write';
      // Where it landed, by the list's NAME -- "Suggested to Data Expert"
      // is what was created; the slug is how the server finds it. And it
      // stays until the next write replaces it: this is the only record
      // the writer gets, and six seconds is not long enough to read one.
      const where = (out.work_lists || []).map((w) => w.name || w.work_list).join(', ');
      host.querySelector('[data-journal-note]')?.remove();
      const note = document.createElement('div');
      note.className = 'mt-s1 text-provenance text-ink';
      note.setAttribute('data-journal-note', '1');
      note.textContent = where ? `written · suggested — now in “${where}”` : 'written';
      host.appendChild(note);
      await renderJournalEntries(slug, entityType);
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to write' : `not written: ${err.message}`;
    }
  });
}

async function renderJournalEntries(slug, entityType = 'repo') {
  const host = $('journal-entries');
  if (!host) return;
  let data;
  try { data = await getJournal(slug, entityType); }
  catch (err) { host.innerHTML = `<span class="text-state-warn">The journal could not be read: ${esc(err.message)}</span>`; return; }
  if (slug !== state.selectedSlug) return;
  const entries = data.entries || [];
  if (!entries.length) {
    // Visible, and a fair thing for a corpus view to count: catalogued,
    // never written about.
    host.innerHTML = `<span class="text-ink-muted">Nobody has written about this resource yet.</span>`;
    return;
  }
  host.innerHTML = `
    <div class="mb-s1 text-caps uppercase tracking-caps text-ink-muted">Earlier entries · <span class="tnum">${entries.length}</span>${
      data.suggested_to?.length ? ` · suggested to ${esc(data.suggested_to.join(', '))}` : ''}</div>
    ${entries.map((e) => `<div class="border-t border-rule py-s2">
      <p class="m-0 max-w-[70ch] text-answer text-ink">${tnum(esc(e.body))}</p>
      <div class="text-provenance text-ink-muted">${esc(e.author)} · <span class="tnum">${esc(ago(e.written_at))}</span>${
        e.suggested_to?.length ? ` · suggested to ${esc(e.suggested_to.join(', '))}` : ''}</div>
    </div>`).join('')}`;
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
function lastRunHtml(c) {
  const when = c.last_run_at || '';
  if (!when) return '<span class="text-ink-muted">never run</span>';
  const days = (Date.now() - Date.parse(when)) / 86400000;
  const stale = Number.isFinite(days) && days >= SURVEY_STALE_DAYS;
  const ok = (c.last_run_status || '') === 'ok';
  const errors = c.last_run_errors || [];
  const warn = !ok && c.last_run_status
    ? (errors.length
        ? `<button type="button" data-run-errors="${esc(c.qualified_name || '')}"
             class="cursor-pointer bg-transparent text-state-warn underline decoration-dotted"
             title="Click to see what failed">⚠</button> `
        : `<span class="text-state-warn" title="${esc(c.last_run_status)}">⚠</span> `)
    : '';
  return `<span title="${esc(when)}${c.last_run_status ? ` · ${esc(c.last_run_status)}` : ''}">
    ${ok ? '<span class="text-state-ok">✓</span> ' : warn}
    <span class="${stale ? 'wl-age-text' : ''}">ran ${esc(ago(when))}</span></span>`;
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

function surveyRowHtml(c) {
  const steps = (c.steps || []).length || c.step_count || 0;
  const produces = producesTypes(c);
  return `<div class="flex flex-wrap items-baseline gap-s3 border-b border-rule py-s2">
    <div class="min-w-0 flex-1">
      <div class="text-answer text-ink">${esc(c.display_name || c.qualified_name)}</div>
      ${c.description ? `<div class="text-caveat text-ink-muted">${esc(onePurpose(c.description))}</div>` : ''}
      <div class="mt-[2px] font-mono text-provenance text-ink-muted">${esc(c.qualified_name || '')}${
        c.description ? ` · <button type="button" data-defhist="${esc(c.qualified_name)}"
          class="cursor-pointer bg-transparent underline">definition history</button>` : ''}</div>
      <div class="mt-[2px] text-provenance text-ink-muted">produces · ${
        produces.length ? `<span class="font-mono">${produces.map((t) => esc(t)).join(', ')}</span>` : 'nothing declared'}</div>
    </div>
    <div class="tnum shrink-0 text-caveat text-ink-muted">${steps} step${steps === 1 ? '' : 's'}${
      // Point 2 (SPEC-THE-STAGE-PAGE.md): the axis tiering itself turns on --
      // "4 steps · 2 fetch" beside "7 steps · none fetch" reads as peers with
      // one that fetches twice and one that doesn't. `fetch_steps` is not on
      // every definition row yet (re/stage-page-backend); say nothing rather
      // than a false zero until it is.
      c.fetch_steps == null ? '' : ` · ${c.fetch_steps ? `<span class="tnum">${c.fetch_steps}</span> fetch` : 'none fetch'}`}</div>
    <div class="tnum shrink-0 text-caveat">${lastRunHtml(c)}</div>
    <button data-run-survey="${esc(c.qualified_name || c.guid)}"
      class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
      >Run →</button>
  </div>`;
}

// Human-readable labels for `NativeProcess.kind` (technology_type_processes.py
// / configdata/technology_type_processes.yaml). Raw enum values rendered
// directly -- "(survey_existing)" -- meant nothing to a reader who hasn't read
// that config file (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §5). A
// fallback keeps an unmapped or future kind from disappearing rather than
// crashing the render.
const NATIVE_PROCESS_KIND_LABELS = {
  survey_existing: 'surveys an existing catalog entry',
  catalog_and_survey: 'catalogues, then surveys',
  delete: 'deletes a catalog entry',
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

async function loadSurveyPane() {
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
  const nativeProcessesHtml = nativeProcessesSectionHtml(data.egeria_native_processes);

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
        Scope: ${data.scoping === 'full-scan' && all.length
          ? `all tiers — stage filter unavailable · <button type="button" data-act="rescope"
              class="cursor-pointer bg-transparent underline">retry</button>`
          : esc(stage)}</span>
    </div>

    ${nativeProcessesHtml}

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
          <button data-plan-survey="${esc(c.qualified_name)}"
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
    <div id="survey-note" class="mt-s3 text-caveat text-ink"></div>

    <div class="mt-s5 border-t border-rule-strong pt-s3" id="analyses-index-section">
      <div class="text-caveat text-ink-muted">Reading the analyses…</div>
    </div>`;
  bindSubTabs();

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
  el.querySelectorAll('[data-run-survey], [data-plan-survey]').forEach((b) =>
    b.addEventListener('click', () => {
      const ref = b.dataset.runSurvey || b.dataset.planSurvey;
      planSurveyRun(all.find((x) => (x.qualified_name || x.guid) === ref), slug);
    }));

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
function analysisRowGlyph(row) {
  if (!row.last_run_at) return factGlyph('unrun');
  const s = String(row.last_run_status || '').toLowerCase();
  if (s === 'success' || s === 'ok' || s === '') return factGlyph('measured');
  if (s === 'failure' || s === 'error' || s === 'failed') return factGlyph('error');
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

function analysisIndexRowHtml(row) {
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
      </div>
    </div>
    ${isSubRes ? `<button type="button" data-subres-toggle aria-expanded="false"
      class="shrink-0 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink-muted"
      >🗂 select &amp; catalog</button>` : ''}
    <button data-analysis-run="${esc(row.analysis_id)}" ${row.runnable ? '' : 'disabled title="' + esc(row.runnable_reason) + '"'}
      class="shrink-0 cursor-pointer rounded-sm border ${row.runnable ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted'} bg-transparent px-2 py-[2px] text-caveat"
      >${row.last_run_at ? 're-run' : 'run'} →</button>
    <span data-analysis-run-error="${esc(row.analysis_id)}"
      class="hidden w-full text-provenance text-state-warn"></span>
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
    subresToggle.textContent = opening ? '🗂 select & catalog ▲' : '🗂 select & catalog';
    if (opening) await mountSubResourcePanel(slug, subresPanel);
  });
  host.querySelectorAll('[data-analysis-run]').forEach((b) => b.addEventListener('click', async () => {
    const aid = b.dataset.analysisRun;
    const errEl = host.querySelector(`[data-analysis-run-error="${CSS.escape(aid)}"]`);
    if (errEl) { errEl.classList.add('hidden'); errEl.textContent = ''; }
    b.disabled = true;
    const original = b.textContent;
    b.textContent = 'Queueing…';
    try {
      const started = await runAnalysis(slug, aid, apiEntityType(state.resourceType));
      // Watch it rather than tell the user to reload — pollActivity is the
      // same mechanism the Questions checklist's run button already uses
      // (rerun(), above). A five-minute timeout still redraws the section
      // once so a slow run's real state (whatever it reaches) is on screen
      // instead of the stale pre-run row.
      b.textContent = 'Running…';
      try {
        await pollActivity(started.activity_id, {
          onTick: (e) => {
            const s = (e?.status || '').toLowerCase();
            b.textContent = s === 'queued' || s === 'pending' ? 'Queued…' : 'Running…';
          },
        });
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
  }));
}

/** RUN GOES THROUGH THE SAME PREVIEW as the matrix's two plans.
 *
 * This is the third and most expensive of them, and if they answer "what am I
 * about to spend" in three shapes people learn to read one and skim the rest.
 */
function planSurveyRun(c, slug) {
  if (!c) return;
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
    <p class="mt-s3 text-ink">Nothing runs until you confirm.</p>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="go"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
        >Run <span class="tnum">${steps}</span> step(s)</button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>`;
  body.querySelector('[data-act="go"]').addEventListener('click', () => {
    closeCellDetail();
    launchSurvey(slug, c.qualified_name || c.guid);
  });

  // WHAT MOVED LAST TIME, HERE, WHERE IT CHANGES A DECISION.
  //
  // "Unchanged across N runs" reports a fact on a dashboard row; in a plan it
  // is an argument. A survey whose measurements have not moved across several
  // runs is a survey whose cadence is costing more than it returns, and this
  // is the moment that matters — before paying for it again rather than after.
  reportPlanMovement(slug, c);
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

async function launchSurvey(slug, ref) {
  const note = $('survey-note');
  if (note) note.innerHTML = `Launching <span class="font-mono">${esc(ref)}</span>…`;
  try {
    const res = await runSurveyDefinition(slug, ref, { entityType: apiEntityType(state.resourceType) });
    if (note) note.innerHTML = `Launched <span class="font-mono">${esc(ref)}</span>.
      ${res && (res.guid || res.engine_action_guid)
        ? `Egeria action <span class="font-mono">${esc(res.guid || res.engine_action_guid)}</span>.` : ''}
      It runs asynchronously — its results appear in Dashboard and in the question rows
      as each step lands, not when this line changes.`;
    // Found live, `adventureworks`, 2026-09-27: a Survey Definition run
    // that had genuinely completed (three survey rows written) still left
    // this pane reading "never run" — nothing here ever re-fetched. Watch
    // the run the same way the per-analysis run button already does
    // (pollActivity — see the `data-analysis-run` handler above), then
    // reload the whole Survey pane so its "last run" status, and
    // everything else `getSurveyCandidates` returns, reflects what
    // actually happened rather than the stale pre-run snapshot.
    if (res && res.activity_id) {
      try {
        await pollActivity(res.activity_id, {});
      } catch (err) {
        if (err.name !== 'PollTimeout') throw err;
        // Not a failure — this browser stopped watching; the run itself
        // may still be in flight. Reload anyway: it shows whatever state
        // the run has reached by now, which is still more current than a
        // note that never changes.
      }
      if (slug === state.selectedSlug && state.subTab === 'survey') {
        await loadSurveyPane();
      }
    }
  } catch (err) {
    if (!note) return;
    // 401 is not a failure of the survey, it is a fact about this session.
    note.innerHTML = err.status === 401
      ? `<span class="text-accent-ink">Not launched — running a survey is a write, and
         this session is not signed in. Sign in with an Egeria user id to launch it;
         everything else on this pane is readable without one.</span>`
      : `<span class="text-state-warn">It was not launched: ${esc(err.message)}</span>`;
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
    const res = await getAnalysisTrend(slug, analysisId, metric);
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
    const res = await getAnalysisTrend(slug, analysisId, metric);
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

async function openRunsList(slug) {
  const el = openDialog('Runs', slug);
  const body = el.querySelector('#wl-detail-body');
  let rows;
  try {
    rows = await getResourceRuns(slug);
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
    try {
      const d = typeof r.detail === 'string' ? JSON.parse(r.detail) : (r.detail || {});
      steps = d.steps || [];
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
          bad.length ? ` · <span class="text-state-warn">${bad.length} did not</span>` : ''}</div>
        <ul class="m-0 mt-s1 list-none p-0 text-caveat">
          ${steps.map((s) => `<li class="flex gap-s2">
            <span class="${s.status === 'ok' ? 'text-state-ok' : 'text-state-warn'}">${
              s.status === 'ok' ? '✓' : '⚠'}</span>
            <span class="min-w-0 font-mono text-provenance">${
              esc(String(s.step || '').split('::').pop())}</span>
            ${s.status !== 'ok' && s.detail
              ? `<span class="text-ink-muted">— ${esc(String(s.detail).slice(0, 120))}</span>` : ''}
          </li>`).join('')}
        </ul>`
        : '<p class="mt-s1 text-caveat text-ink-muted">This run recorded no step list.</p>'}
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
    });
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
 *  members rail and the run-history list -- the same vocabulary the matrix
 *  and the questions row use, spelled out here since state-as-glyph is a
 *  cross-cutting need, not a "by analysis" or "by question" one. */
export function factGlyph(state) {
  switch (state) {
    case 'measured': return { glyph: '✓', tone: 'text-state-ok' };
    case 'error': return { glyph: '✕', tone: 'text-state-warn' };
    case 'unrun': return { glyph: '○', tone: 'text-ink-muted' };
    default: return { glyph: '·', tone: 'text-ink-muted' };
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
 * Three acts, and they are different: add to work list (I will deal with
 * this), raise RFA (someone must), note in journal (worth knowing — no
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

function wireSelection(out, { slug, analysisId, metric, data }) {
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
        <button data-promote="work_list" class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">add to work list</button>
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
      const status = footer.querySelector('#promote-status');
      const members = picks().map((c) => c.dataset.pick);
      b.disabled = true; status.textContent = '…';
      try {
        const out2 = await promoteMembers(slug, analysisId, {
          action: b.dataset.promote, metric: metric || data.metric || '', members, total, facet: facetLabel(), runAt,
          name: nameEl.value.trim(),
        });
        // Say where it went, not "sent".
        const where = out2.work_list ? `work list ${out2.work_list}` : out2.rfa ? `RFA ${String(out2.rfa).slice(0, 8)}` : 'the journal';
        status.innerHTML = `<span class="text-state-ok-on-dark">→ ${esc(where)}</span>`;
      } catch (err) {
        b.disabled = false;
        status.innerHTML = `<span class="text-state-warn-on-dark">${esc(err.status === 401 ? 'sign in to promote' : err.message)}</span>`;
      }
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
    data = await getMembers(slug, analysisId, { metric, scope });
  } catch (err) {
    if (railStale(ticket)) return;   // a later click owns the slot now
    railFrame('Members', slug, `<div class="text-caps text-state-warn-on-dark">The members of ${esc(title || analysisId)} could not be read: ${esc(err.message)}</div>`, { sub: 'failed' });
    return;
  }
  if (railStale(ticket)) return;
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
  out.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Members</span>
      <span class="min-w-0 truncate text-caps text-chrome-muted">for <span class="font-mono">${esc(slug)}</span> · <span class="tnum">${data.total}</span> · ${esc(data.title)}${
        data.inventory ? ` · ${tnum(esc(data.inventory))}` : ''}</span>
      <button data-act="close-members" class="ml-auto cursor-pointer bg-transparent text-caps text-chrome-muted underline">close</button>
    </div>
    <div class="mb-s2 text-caps text-chrome-muted">
      <span class="font-mono">${esc(data.analysis_id)}</span>
      · <button data-act="member-history" class="cursor-pointer bg-transparent text-accent-on-dark underline">measurement ›</button>
      · scope
      <button data-scope="public" aria-pressed="${scope === 'public'}" class="wl-chartchip cursor-pointer bg-transparent px-[4px] text-chrome-muted">use</button>
      <button data-scope="all" aria-pressed="${scope === 'all'}" class="wl-chartchip cursor-pointer bg-transparent px-[4px] text-chrome-muted">maintain</button>
      ${data.scope_honoured ? '' : `<span class="text-chrome-muted">· scope not applicable to this set</span>`}
    </div>
    ${data.note ? `<div class="mb-s2 text-caps text-chrome-muted">${esc(data.note)}</div>` : ''}
    ${facetsHtml(groups, data)}
    ${groups.length ? '' : `<div class="text-caps text-chrome-muted">Nothing listed — <span class="font-mono">${esc(data.source)}</span> holds no rows for this analysis on this resource.</div>`}
    <div class="flex flex-col gap-s1">
      ${groups.map((g, gi) => `<details class="border-b border-chrome-line-soft pb-s1" ${gi < 3 ? 'open' : ''}>
        <summary class="cursor-pointer text-subtab text-chrome-ink"><span class="tnum">${g.count}</span> · ${esc(g.name)}</summary>
        <ul class="m-0 mt-[2px] list-none p-0 pl-s2">
          ${g.members.map((m) => `<li class="flex items-baseline gap-s2 py-[2px] text-caps">
            ${m.children_key ? '' : `<input type="checkbox" data-pick="${esc(m.name)}" data-group="${esc(g.name)}" data-detail="${esc(m.detail || '')}" class="shrink-0">`}
            ${m.children_key
              ? `<button data-children="${esc(m.children_key)}" class="cursor-pointer bg-transparent p-0 text-left font-mono text-chrome-ink underline">${esc(m.name)}</button>
                 <span class="text-chrome-muted tnum">${m.count ?? ''}</span>`
              : `<span class="min-w-0 break-words font-mono text-chrome-ink">${esc(m.name)}</span>`}
            ${m.detail ? `<span class="shrink-0 text-chrome-muted">${esc(m.detail)}</span>` : ''}
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
  wireSelection(out, { slug, analysisId, metric, data });
  out.querySelector('[data-act="member-history"]')?.addEventListener('click', () => openMeasurementDetail({
    slug, analysisId, title: title || analysisId, metric,
  }));
  out.querySelectorAll('[data-scope]').forEach((b) => b.addEventListener('click', () => {
    state.memberScope = b.dataset.scope;
    openMembers({ slug, analysisId, metric, title });
  }));
  // One level down, on demand: a file opens its symbols in place.
  out.querySelectorAll('[data-children]').forEach((b) => b.addEventListener('click', async () => {
    const li = b.closest('li');
    if (li.querySelector('ul')) { li.querySelector('ul').remove(); return; }
    b.textContent = `${b.textContent} …`;
    let rows;
    try { rows = (await getMemberChildren(slug, analysisId, b.dataset.children, { scope })).members || []; }
    catch (err) { rows = [{ name: `could not read: ${err.message}`, detail: '' }]; }
    b.textContent = b.textContent.replace(/ …$/, '');
    const ul = document.createElement('ul');
    ul.className = 'm-0 mt-[2px] w-full list-none p-0 pl-s3';
    ul.innerHTML = rows.map((m) => `<li class="flex items-baseline gap-s2 py-[1px] text-caps">
        <span class="min-w-0 break-all font-mono text-chrome-ink">${esc(m.name)}</span>
        ${m.detail ? `<span class="shrink-0 text-chrome-muted">${esc(m.detail)}</span>` : ''}</li>`).join('')
      || `<li class="text-caps text-chrome-muted">nothing at this level</li>`;
    li.appendChild(ul);
  }));
  // The rail may be closed on a narrow shell; a members request opens it.
  if (typeof setRailOpen === 'function') setRailOpen(true);
}

async function loadByAnalysisPane() {
  const el = $('content');
  const blocked = paneNeedsRepo();
  if (blocked) { el.innerHTML = subTabsHtml() + blocked; bindSubTabs(); return; }
  const slug = state.selectedSlug;
  const stage = state.stage;

  // Its own token: an earlier click's slow read must not overwrite a
  // later one's -- the dashboards read has cost 109s on Analysis.
  const token = ++dashToken;
  el.innerHTML = subTabsHtml() + `
    <div class="mb-s3 flex flex-wrap items-baseline gap-s3">
      <span class="text-caps uppercase tracking-caps text-ink-muted">Survey results, by analysis · ${esc(stage)}</span>
    </div>
    <div id="dash-boards" class="text-caveat text-ink-muted">Reading the dashboards…</div>`;
  bindSubTabs();

  const live = () => token === dashToken && state.subTab === 'by_analysis';

  // database/filesystem now have a real (if partial -- see docs/Backlog.md,
  // "By analysis" was repo-only) survey-results route of their own
  // (workflows.analysis.build_survey_results) -- apiEntityType() translates
  // state.resourceType at this boundary the same way getSurveyCandidates/
  // runSurveyDefinition already do, so 'db' never reaches the server
  // untranslated.
  let data;
  try {
    data = await getSurveyDashboards(slug, stage, { includeEmpty: true, entityType: apiEntityType(state.resourceType) });
  } catch (err) {
    if (live()) $('dash-boards').innerHTML =
      `<span class="text-state-warn">The dashboards could not be read: ${esc(err.message)}</span>`;
    return;
  }
  if (!live()) return;
  const boards = data.dashboards || [];
  if (!boards.length) {
    $('dash-boards').innerHTML = `No dashboard is registered for ${esc(stage)}.`;
    return;
  }

  // Every measurement on the pane, so a NAME REPORTED TWICE WITH DIFFERENT
  // VALUES can be marked where it is displayed rather than left for a reader
  // to notice or not.
  const seen = new Map();
  for (const b of boards) {
    for (const a of b.analyses || []) {
      for (const [k, v] of Object.entries(a.results || {})) {
        if (typeof v !== 'number') continue;
        const rec = seen.get(k) || [];
        rec.push({ analysis: a.analysis_id, value: v });
        seen.set(k, rec);
      }
    }
  }
  const disputed = new Map();
  for (const [k, rec] of seen) {
    if (rec.length > 1 && new Set(rec.map((x) => x.value)).size > 1) disputed.set(k, rec);
  }

  $('dash-boards').innerHTML = boards.map((b) => {
    const analyses = b.analyses || [];
    // THE GROUP HEADER CARRIES NO DATE. "Health & Maturity — measured 12h ago"
    // over cards from three analyses is the per-resource as-of date deleted
    // from the matrix, returned one level up. Dates belong on measurements.
    const headline = analyses.find((a) => typeof (a.results || {}).overall === 'number');
    const findings = [];
    const counts = [];
    for (const a of analyses) {
      const res = a.results || {};
      if (a === headline) continue;
      if (Array.isArray(res.findings) && res.findings.length) {
        for (const f of res.findings) findings.push({ ...f, analysis_id: a.analysis_id, when: a.last_surveyed_at });
        continue;
      }
      for (const [k, v] of Object.entries(res)) {
        if (typeof v === 'number' || typeof v === 'boolean') {
          counts.push({ key: k, value: v, analysis_id: a.analysis_id, when: a.last_surveyed_at });
        }
      }
    }
    findings.sort((x, y) => {
      const ux = UNRESOLVED_LABELS.has(String(x.label || '').toLowerCase()) ? 0 : 1;
      const uy = UNRESOLVED_LABELS.has(String(y.label || '').toLowerCase()) ? 0 : 1;
      return ux - uy;
    });

    return `
      <section class="mb-s5">
        <div class="text-answer text-ink">${esc(b.title || b.id)}</div>
        ${b.description ? `<p class="mt-[2px] max-w-[70ch] text-caveat text-ink-muted">${esc(b.description)}</p>` : ''}
        ${!b.has_results ? `<p class="mt-s1 text-caveat text-state-warn">Registered, never run.</p>` : ''}

        ${headline ? headlineHtml(headline) : ''}

        ${findings.length ? `
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
                    ? `${esc(f.check_name.replace(/_/g, ' '))}${
                        f.label ? ` — ${esc(humanLabel(f.label))}` : ''}`
                    : esc(humanLabel(f.label) || f.analysis_id)}.</strong>
                ${f.summary ? ` ${tnum(esc(f.summary))}` : ''}
                <span class="block text-provenance text-ink-muted"
                  data-delta="${esc(f.analysis_id)}|${esc(f.check_name || '')}">·</span></span>
              <span class="shrink-0 font-mono text-provenance text-ink-muted">${esc(f.analysis_id)}${
                f.when ? ` · ${esc(ago(f.when))}` : ''} ›</span>
            </button>`;
          }).join('')}` : ''}

        ${counts.length ? `
          <div class="mt-s3 text-caps uppercase tracking-caps text-ink-muted">Counts</div>
          <table class="w-full border-collapse text-caveat">
            ${(() => {
              // A disputed name is ONE row carrying every value, not one row
              // per analysis saying the same thing mirrored.
              const shown = new Set();
              return counts.map((c) => {
                const rec = disputed.get(c.key);
                if (rec) {
                  if (shown.has(c.key)) return '';
                  shown.add(c.key);
                  return `<tr class="border-b border-rule bg-[rgba(168,113,42,.07)]">
                    <td class="py-[5px] pr-s3 text-ink"><span class="text-state-warn">⚠</span>
                      ${esc(c.key.replace(/_/g, ' '))}
                      <span class="text-provenance text-ink-muted">— <span class="tnum">${
                        rec.length}</span> analyses report this name with different values;
                        they may not be measuring the same thing</span></td>
                    <td class="tnum py-[5px] pr-s3 text-right text-ink">${
                      esc(rec.map((x) => fmtScalar(x.value, c.key)).join(' / '))}</td>
                    <td class="py-[5px] text-right font-mono text-provenance text-ink-muted">${
                      esc(rec.map((x) => x.analysis).join(' / '))}</td>
                  </tr>`;
                }
                // Two analyses AGREEING on a name is one fact, not two rows.
                // Both are named, so the agreement itself stays visible.
                if (shown.has(c.key)) return '';
                shown.add(c.key);
                const agree = (seen.get(c.key) || []).filter((x) => x.analysis !== c.analysis_id);
                return `<tr class="wl-countrow cursor-pointer border-b border-rule"
                  data-measure="${esc(c.analysis_id)}" data-metric="${esc(c.key)}"
                  data-title="${esc(c.key.replace(/_/g, ' '))}" data-when="${esc(c.when || '')}">
                  <td class="py-[5px] pr-s3 text-ink">${esc(c.key.replace(/_/g, ' '))}</td>
                  <td class="tnum py-[5px] pr-s3 text-right text-ink">${esc(fmtScalar(c.value, c.key))}</td>
                  <td class="py-[5px] text-right font-mono text-provenance text-ink-muted">${
                    esc([c.analysis_id, ...agree.map((x) => x.analysis)].join(' · '))}${
                    c.when ? ` · ${esc(ago(c.when))}` : ''}</td>
                </tr>`;
              }).join('');
            })()}
          </table>` : ''}
      </section>`;
  }).join('');

  // EVERY MEASUREMENT OPENS THE SAME DETAIL. Three entry points, one
  // component — a dashboard finding, a count, and the matrix cell popup.
  $('dash-boards').querySelectorAll('[data-measure]').forEach((n) => {
    n.addEventListener('click', () => openMeasurementDetail({
      slug,
      analysisId: n.dataset.measure,
      title: n.dataset.title || n.dataset.measure,
      metric: n.dataset.metric || '',
      summary: n.dataset.summary || '',
      when: n.dataset.when || '',
    }));
  });

  // Inline deltas, from the same series the detail uses. Filled after render
  // so a slow trend read never delays the pane.
  for (const n of $('dash-boards').querySelectorAll('[data-delta]')) {
    const [analysisId] = n.dataset.delta.split('|');
    deltaFor(slug, analysisId).then((text) => {
      if (!live()) return;
      n.textContent = text || '';
      n.className = text === 'first measurement'
        ? 'block text-provenance text-ink-muted'
        : 'block text-provenance text-ink';
    });
  }
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

/** What a deferred sub-tab shows when you click it. */
/** A link into the current UI, on the same resource.
 *
 *  `index.html` gained a `?resource=` reader for this — it had no deep link
 *  of any kind, so "links out preserving the resource" was not satisfiable
 *  without adding one. Additive: an unrecognised slug selects nothing and
 *  the app starts exactly as before. */
export function oldUiHref() {
  return state.selectedSlug
    ? `/?resource=${encodeURIComponent(state.selectedSlug)}`
    : '/';
}

function deferredPaneHtml(tab) {
  return `${subTabsHtml()}
    <h3 class="m-0 font-heading text-name font-normal">${esc(tab.label)} · not built in /next</h3>
    <div class="my-s3 h-px bg-rule"></div>
    <p class="max-w-[70ch] text-answer text-ink">${esc(tab.does)}.</p>
    <p class="max-w-[70ch] text-answer text-ink">
      <a href="${esc(oldUiHref())}" class="text-accent-ink underline"
        >Open ${state.selectedSlug ? `<span class="font-mono">${esc(state.selectedSlug)}</span>` : 'this'}
        in the current UI</a> ${icon('external-link', { size: 13, cls: 'text-accent-ink' })}
    </p>`;
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
 */
const LEGEND = [
  ['answered',     'answered'],
  ['automatic',    'automatic'],
  ['partial',      'ran, but not at this level'],
  ['unrun',        'not run'],
  ['human',        'needs you'],
  ['no-surveyor',  'no surveyor'],
  ['unclassified', 'unclassified'],
  ['running',      'running'],
  ['error',        'could not load'],
];

function renderLegend() {
  const el = $('state-legend');
  if (!el) return;

  const counts = {};
  let pending = 0;
  for (const q of state.questions) {
    const env = state.answers.get(q.question);
    if (env === undefined || env === 'loading') { pending += 1; continue; }
    const st = state.runsInFlight.has(q.question) ? 'running'
      : env.__error ? 'error'
      : rowState(q, env);
    counts[st] = (counts[st] || 0) + 1;
  }

  const items = LEGEND.filter(([k]) => counts[k]).map(([k, label]) => `
    <span class="inline-flex items-baseline gap-[5px]">
      <span class="${tone(k, 'paper')}">${GLYPH[k]}</span>
      <span class="text-ink">${esc(label)}</span>
      <span class="tnum text-ink">${counts[k]}</span>
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
    bindSubTabs();
    el.querySelectorAll('[data-open-wl]').forEach((b) => b.addEventListener('click', () => {
      state.workListSlug = b.dataset.openWl;
      state.workListIndex = false;
      writeUrl(); renderSidebar(); loadPane();
    }));
    writeUrl();
    return;
  }

  if (state.workListSlug) {
    state.workListIndex = false;
    try {
      await openWorkList({
        el,
        subTabs: SUB_TABS.map((t) => ({ id: t.id, label: t.label })),
        stage: state.stage,
        perspectives: state.activePerspectives,
        projects: state.projects,
        analyses: state.analyses || [],
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

  if (state.subTab === 'schema_inventory') { await loadSchemaInventoryPane(); return; }
  if (state.subTab === 'survey') { await loadSurveyPane(); return; }
  // 'dashboard' is a retired tab id -- a bookmarked/shared URL from before
  // the stage-page round lands on its nearest surviving surface rather than
  // the deferred-pane message a stranger id would get.
  if (state.subTab === 'dashboard') { state.subTab = 'by_analysis'; writeUrl(); }
  if (state.subTab === 'by_analysis') { await loadByAnalysisPane(); return; }
  if (state.subTab === 'disposition') { await loadDispositionPane(); return; }

  if (state.subTab !== 'questions') {
    const tab = SUB_TABS.find((t) => t.id === state.subTab)
      || { id: state.subTab, label: state.subTab, does: 'Not a pane /next knows about' };
    el.innerHTML = deferredPaneHtml(tab);
    bindSubTabs();
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
    await loadChartsPane();
    renderPerspectiveRow();
    return;
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
    return;
  }

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

  // The overview carries `homepage`, `last_published_at` and the stale-link
  // flag, none of which are on the summary row. Fetched per selection, and
  // its absence is survivable — the header renders without it.
  if (state.overview?.slug !== slug) {
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
  }

  // The frame first, then per-row skeletons — rows arrive independently, and
  // a centred spinner would hide the ones that are already here.
  el.innerHTML = `${subTabsHtml()}
    <div id="resource-header">${resourceHeaderHtml(slug)}</div>
    <div class="mt-s3 flex flex-wrap items-baseline gap-s3 text-provenance">
      <span id="answered-count" class="tnum text-caveat text-ink-muted">loading…</span>
    </div>
    <div id="state-legend" class="mt-s2 flex flex-wrap items-baseline gap-s3 text-caveat"></div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="enrichment-form"></div>
    <div id="question-rows"></div>`;
  bindSubTabs();
  bindResourceHeader();

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
  if (state.stage === 'curate') renderCurate(slug);
  // Analysis has real catalog rows (unlike Curate), so it renders through
  // the generic engine below like any other built stage, with no bypass
  // branch here -- classic's Sub-Resources sub-tab is now ported onto the
  // Survey & analyses pane's sub_resource_survey row instead of a stage-
  // level note (RULING-SUBRESOURCES-PLACEMENT.md; next/stages/analysis.js).
  if (!state.questions.length) {
    rows.innerHTML = state.stage === 'curate' ? '' : `<div class="py-s3 text-answer text-ink">
      No catalogued questions match this stage and this perspective set.
      That is a fact about the filter, not about the repository.</div>`;
    $('answered-count').textContent = state.stage === 'curate' ? 'review and commit · Curate' : `0 questions · ${stageLabel()}`;
    return;
  }

  rows.innerHTML = state.questions.map((q, i) => rowShell(q, i)).join('');
  wireHumanAnswers(rows, slug);
  if (state.stage === 'enrichment') renderEnrichment(slug);
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
function wireHumanAnswers(host, slug) {
  if (host.dataset.humanWired === '1') return;
  host.dataset.humanWired = '1';
  host.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-human-edit]');
    if (!btn) return;
    const question = btn.getAttribute('data-human-edit');
    const key = questionKey(question);
    const prior = (state.contextAnswers || {})[key]?.answer || '';
    const next = window.prompt(question, prior);
    if (next === null) return;              // cancelled — not an empty answer
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      await saveQuestionAnswer('repo', slug, question, next.trim());
      state.contextAnswers = {
        ...(state.contextAnswers || {}),
        [key]: { question, answer: next.trim(), answered_at: new Date().toISOString() },
      };
      // Redraw just this row, the way an arriving answer does. A whole-pane
      // reload would refetch every other answer to show one that is already
      // in hand.
      const i = state.questions.findIndex((q) => q.question === question);
      if (i >= 0) replaceRow(state.questions[i], i, state.answers.get(question));
    } catch (err) {
      // Left on the button rather than raised as a page error: the failure is
      // this one save, and the rest of the checklist is unaffected.
      btn.disabled = false;
      btn.textContent = err.status === 401 ? 'Not signed in' : `Not saved: ${err.message}`;
      setTimeout(() => { btn.textContent = label; }, 4000);
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
function rowInner(entry, i, env) {
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
    : rowState(entry, env);

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
        <span class="rounded-pill border border-dashed border-state-gap px-2 py-[1px] text-caps text-state-gap">no surveyor yet</span>
        ${perspectives.map((pv) => `<span class="rounded-pill border border-rule-strong px-2 py-[1px] text-caps text-ink-muted"
          >${esc(pv)}</span>`).join('')}
      </span>`
    : perspectiveTags;

  const head = `<div class="flex flex-wrap items-baseline gap-[9px]">
      <span class="w-[13px] ${glyphColor} text-question">${st === 'loading' ? '' : glyph}</span>
      <span class="font-heading text-question font-semibold text-ink">${esc(entry.question)}</span>
      ${tag}
    </div>`;

  return head + bodyLines(entry, i, st, env);
}

function bodyLines(entry, i, st, env) {
  const indent = 'ml-[22px] mt-[6px]';

  if (st === 'loading') {
    // A skeleton, not a spinner, and not a claim.
    return `<div class="${indent} h-[14px] w-[42%] rounded-sm bg-paper-surface"></div>`;
  }

  if (st === 'proposal') {
    return prerequisiteProposalHtml(entry, i, indent);
  }

  if (st === 'running') {
    const r = state.runsInFlight.get(entry.question);
    return `<div class="${indent} text-answer text-accent-ink">${esc(r.label)}</div>`
      + provenanceLine(entry, i, { canRun: [], sources: r.analysisId ? [r.analysisId] : [] }, st);
  }

  if (st === 'error') {
    // Per-row failure, per-row retry. Never a page-level error.
    return `<div class="${indent} text-answer text-accent-ink">${esc(env.__error)}</div>
      <div class="${indent} text-provenance text-ink-muted">
        <button data-retry="${i}" class="cursor-pointer bg-transparent text-accent-ink underline">retry</button>
      </div>`;
  }

  if (st === 'no-surveyor') {
    const why = (env && env.blocked_reason)
      || entry.note
      || 'No surveyor exists for this question. Nothing has run and nothing can.';
    return `<div class="${indent} text-answer text-ink">${tnum(esc(why))}</div>`;
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
    return `<div class="${indent} text-answer text-ink">${tnum(esc(why))}</div>
      ${held?.answer
        ? `<div class="${indent} mt-s1 text-answer text-ink">${tnum(esc(held.answer))}</div>
           <div class="${indent} text-provenance text-ink-muted">answered ${esc(ago(held.answered_at))}
             · <button type="button" data-human-edit="${esc(entry.question)}"
                 class="cursor-pointer bg-transparent text-accent-ink underline">change</button></div>`
        : `<div class="${indent} mt-s1">
             <button type="button" data-human-edit="${esc(entry.question)}"
               class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
               >Answer this →</button>
           </div>`}
      ${wanted
        ? `<div class="${indent} text-provenance text-ink-muted">The catalog says
             ${esc(declared)}, but no analysis is attached to this question, so
             nothing was queried — the answer here is yours alone.</div>`
        : ''}`;
  }

  if (st === 'unclassified') {
    return `<div class="${indent} text-answer text-ink">The catalog does not state how this
      question would be answered.</div>`;
  }

  if (st === 'unrun') {
    // NO answer line. Not a zero, not an empty string — the absence of the
    // line is the statement.
    const why = (env && env.blocked_reason) || 'Not run yet.';
    const lines = readEnvelope(entry, env);
    return autoRanNoteHtml(entry, indent)
      + `<div class="${indent} text-answer text-ink">${tnum(esc(why))}</div>`
      + provenanceLine(entry, i, lines, st);
  }

  // answered | automatic | partial
  const lines = readEnvelope(entry, env);
  let html = autoRanNoteHtml(entry, indent);
  if (lines.answer) {
    html += `<div class="${indent} text-answer text-ink">${lines.answer}</div>`;
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
  return `<div class="${indent} text-caveat text-ink-muted">${tnum(esc(note))}</div>`;
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
  return `<div class="${indent} text-answer text-ink">${lead}</div>
    ${reasons ? `<ul class="${indent} mt-[4px] list-disc list-inside text-caveat text-ink-muted">${reasons}</ul>` : ''}
    ${partial ? `<div class="${indent} mt-[4px] text-caveat text-ink-muted">Running it anyway is not wrong —
      the result is marked as measured within this credential's scope, never as a
      measurement of the whole database.</div>` : ''}
    <div class="${indent} mt-s2 flex flex-wrap items-center gap-s2">
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
  if (st === 'automatic') {
    const how = { direct: 'direct field', registry: 'from the registry', chart: 'chart' }[entry.kind];
    if (how) bits.push(how);
  }

  const actions = [];
  if (st === 'answered' || st === 'automatic' || st === 'partial') {
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
  if (primaryId && (st === 'answered' || st === 'automatic' || st === 'partial')) {
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

  // Friendly names when available, same source `openAnalysisPopover` and the
  // "By analysis" section use (`getAnalysesIndex`) — falls back to the raw
  // id for any id that index doesn't carry (e.g. a not-yet-run analysis),
  // never blocks the dialog on this fetch failing.
  let namesById = {};
  try {
    const idx = await getAnalysesIndex(slug);
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
    <p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">Delivered as an RFA the next time a
      <em>scheduled</em> run of that analysis detects a change — set ⏱ Schedule for it on this
      resource in Automate, or this never fires.</p>
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

  body.querySelector('#notify-submit').addEventListener('click', async () => {
    const chosen = ids.length > 1
      ? body.querySelector('input[name="notify-analysis"]:checked')?.value
      : body.querySelector('#notify-analysis-only').value;
    const errEl = body.querySelector('#notify-error');
    if (!chosen) { errEl.textContent = 'Pick an analysis to watch.'; return; }
    const label = body.querySelector('#notify-label').value.trim();
    const btn = body.querySelector('#notify-submit');
    btn.disabled = true;
    btn.textContent = 'Subscribing…';
    try {
      // entity_type is 'repo' unconditionally: the Questions engine this
      // dialog is attached to is itself gated to `state.resourceType ===
      // 'repo'` a few lines up in loadPane() — there is no other value this
      // row could carry today. See createSubscription's own doc comment.
      await createSubscription('repo', slug, chosen, label);
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

async function rerun(entry, i, { background = false, skipPlanCheck = false } = {}) {
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
      await runAnalysis(slug, analysisId, apiEntityType(state.resourceType));
      state.runsInFlight.set(entry.question, { analysisId, label: `In background · ${analysisId} · reload to read the result` });
    } catch (err) {
      state.runsInFlight.delete(entry.question);
      state.answers.set(entry.question, { __error: `The run could not be started: ${err.message}` });
    }
    replaceRow(entry, i, state.answers.get(entry.question));
    return;
  }

  try {
    const started = await runAnalysis(slug, analysisId, apiEntityType(state.resourceType));
    const activityId = started.activity_id;
    state.runsInFlight.set(entry.question, { analysisId, activityId, label: `Running · ${analysisId}` });
    replaceRow(entry, i, state.answers.get(entry.question));

    await pollActivity(activityId, {
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

/** One question row, as markdown with its provenance. */
function rowAsMarkdown(entry, i) {
  const env = state.answers.get(entry.question);
  const st = rowState(entry, env);
  const lines = (env && env !== 'loading' && !env.__error)
    ? readEnvelope(entry, env) : null;

  const out = [`**${entry.question}**`, ''];

  // The answer, as text. `lines.answer` is HTML by the time it reaches a row,
  // so it is rebuilt from the facts here rather than stripped of tags — a
  // regex over markup is how a stray `<` ends up in someone's issue.
  const said = [];
  for (const f of ((env && env.facts) || []).filter((x) => x.is_known)) {
    if (f.headline) said.push(f.headline);
    else if (prose(f)) said.push(f.value?.verdict ? `${cap(String(f.value.verdict))} — ${prose(f)}` : prose(f));
    else if (scalarMeasures(f.value)) said.push(scalarMeasures(f.value));
  }
  if (said.length) out.push(said.join(' '), '');
  else out.push(`_${(env && env.blocked_reason) || STATE_SENTENCE[st] || 'No answer recorded.'}_`, '');

  if (lines && lines.caveat) {
    // The caveat as a blockquote — it is the part a reader most needs to
    // carry across, and a quote survives being pasted into a thread.
    out.push(...lines.caveat.split('\n').map((l) => `> ${l}`), '');
  }

  const bits = [state.selectedSlug];
  const sources = (lines && lines.sources.length ? lines.sources : entry.analysis_ids) || [];
  if (sources.length) bits.push(sources.join(', '));
  if (lines && lines.lastRun) bits.push(`run ${String(lines.lastRun).slice(0, 10)}`);
  else if (lines && lines.runTimeUnrecorded) bits.push('run time not recorded');
  else bits.push('never run');
  bits.push(env && env.answerable
    ? 'answered from survey metadata, no retrieval'
    : `state: ${STATE_LABEL[st] || st}`);
  out.push(`— ${bits.filter(Boolean).join(' · ')}`);

  return out.join('\n');
}

// `turnAsMarkdown` moved to next/chat.js (item 9) — it was chat-only and
// every call site went with it.

const STATE_LABEL = {
  answered: 'answered', automatic: 'automatic', unrun: 'not run',
  partial: 'ran, but not at this level',
  human: 'needs human input', 'no-surveyor': 'no surveyor exists yet',
  unclassified: 'unclassified',
};
const STATE_SENTENCE = {
  unrun: 'Not run yet.',
  human: 'Answered by a person, not by a survey.',
  'no-surveyor': 'No surveyor exists for this question.',
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
  // "table_count"). Each fact block used to render every key in its own
  // `.value` with no awareness of the others, so the same number appeared
  // once per fact that happened to carry it — found live, "How big is
  // this database"'s evidence panel listing `tables`/`table_count` twice.
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
    return `<div class="mb-s2 border-b border-chrome-line-soft pb-s2 last:border-0">
      <div class="text-chip text-accent-on-dark">${esc(f.analysis_id)}</div>
      <div class="text-caps text-chrome-muted">${esc(f.state)} · ${esc(f.provenance)} · ${
        f.last_run_at ? `run ${esc(ago(f.last_run_at))}`
          : f.is_known ? 'run time not recorded' : 'never run'}</div>
      ${f.headline ? `<div class="mt-[4px] text-subtab">${tnum(esc(f.headline))}</div>` : ''}
      ${f.note ? `<div class="mt-[4px] text-chip text-accent-on-dark">${tnum(esc(f.note))}</div>` : ''}
      ${value}
    </div>`;
  }).join('');

  const body = railFrame('Evidence', forWhat, '', { sub: esc(String(entry.question).slice(0, 48)) });
  body.innerHTML = `
    <div class="rounded-sm border border-chrome-line p-s3">
      <div class="mb-s3 text-subtab">${esc(entry.question)}</div>
      ${facts || '<div class="text-chip text-chrome-muted">No facts on this envelope — the answer names no measurements.</div>'}
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
      listGroups(), listInvestigations(), listWorkLists(), listAnalyses('repo'),
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
  await loadPane();
}

// See the button's comment in index.html: hideLogin() is itself the guard.
document.getElementById('login-dismiss-btn')
  ?.addEventListener('click', () => Auth.hideLogin());

// auth.js owns the gate: it either starts us now or after a successful
// sign-in. Nothing here renders before it says so.
Auth.init(start);
