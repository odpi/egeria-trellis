/* The Scouting vertical slice — work lists, batch runs, and the comparison grid.
 *
 * The path this exists to prove, end to end and without leaving /next:
 *
 *   select a set → save as a work list → run one survey across the whole set,
 *   concurrently → compare the set as rows × questions → disposition in bulk,
 *   with reasons → promote the survivors as a new, narrower list
 *
 * It is one continuous claim: if it works for fourteen repos it works for four
 * hundred, and if it does not the funnel needs a different design. Everything
 * built before this reads ONE resource at a time, which is the constraint the
 * whole review is about.
 *
 * THE GRID IS THE POINT, and its hardest requirement is the same one the
 * question rows have: every cell says which of the six states it is in,
 * including "ran and found nothing", which is not the same as an empty cell.
 *
 * One call per resource, not one per cell. `GET /api/analyses/facts/{slug}`
 * returns every fact for a resource already judged; the columns are the
 * stage's questions, and a cell is derived by looking up that question's
 * `analysis_ids` among them. Fourteen calls, not seventy.
 */

import {
  VALID_DISPOSITIONS,
  createWorkList,
  enqueueBatch,
  getBatchProgress,
  getQuestions,
  getBulkFacts,
  getContext,
  getBulkStates,
  getAnalysisTrend,
  getDispositionHistory,
  getWorkList,
  listAnalyses,
  listInvestigationMembers,
  listWorkLists,
  promoteWorkList,
  publishWorkList,
  fetchWorkListCsv,
  requireKind,
  setDisposition,
} from '/static/re-api.js';
import { saveCsv } from '/static/next/download.js';
import { ago, daysSince, verdictLineHtml, changedTimesHtml, readBackAt } from '/static/next/format.js';
import { STATES as GLYPH_STATES } from '/static/next/glyphs.js';
import { addActWording, addListToInvestigation, openStartFromList, START_LABEL } from '/static/next/worklist-actions.js';
import { shortName } from '/static/next/investigation-picker.js';
import { contextRecordedSpec, contextRecordedState } from '/static/next/context-recorded.js';
import { cue } from '/static/next/stages/repo-manifest.js';
import { inPagePrompt } from '/static/next/confirm-dialog.js';

/** Brief Z3: the members the zone check refused (a publish or a batch), each with the no_access cue, the reason
 *  on hover; the rest went ahead. '' when none. */
function notPermittedHtml(rows) {
  if (!rows?.length) return '';
  return `<span data-not-permitted>${rows.map((r) => `<span data-member-not-permitted="${esc(r.entity_slug)}">${
    cue('no_access', 'not permitted', String(r.words || '').replace(/^not permitted · /, ''))} ${esc(r.entity_slug)}</span>`).join('; ')}</span>`;
}

/** Each queued member's add, applied inside the publish (owner's ruling 2026-10-13), by its outbox row's state
 *  afterwards: one cue plus a short word per member, the why on hover. "attached", "not permitted", "could not
 *  attach" (hover says whether the background drain retries it), "left to the background drain". '' when none. */
const ATTACH_CUE = {
  attached: 'measured',
  'not permitted': 'no_access',
  'could not attach': 'error',
  'left to the background drain': 'queued',
};
function attachHtml(rows) {
  if (!rows?.length) return '';
  return `<span data-attach class="block">${rows.map((r) => {
    const state = ATTACH_CUE[r.state] ? r.state : 'left to the background drain';
    const why = String(r.words || '').startsWith(`${state} · `) ? String(r.words).slice(state.length + 3) : '';
    const hover = [why, r.state === 'could not attach' ? (r.will_retry ? 'the background drain sends it again'
      : 'not sent again') : ''].filter(Boolean).join(' · ');
    return `<span data-member-attach="${esc(r.entity_slug)}" data-attach-state="${esc(state)}">${
      cue(ATTACH_CUE[state], state, hover || state)} ${esc(r.entity_slug)}</span>`;
  }).join('; ')}</span>`;
}

/** A re-publish's removals (members dropped in RE, taken out of the WorkingSet), on their own line, each count from
 *  its outbox row's outcome: "N detached", "N removal(s) not permitted", "N could not be detached · why", plus the
 *  ones still waiting and the adds withdrawn before they were sent. '' when nothing was removed. */
function removalsHtml(rm) {
  if (!rm) return '';
  const names = (rows) => rows.map((r) => esc(r.entity_slug)).join(', ');
  const bits = [];
  if (rm.detached?.length) {
    bits.push(cue('measured', `${rm.detached.length} detached`, rm.detached.join(', ')));
  }
  if (rm.not_permitted?.length) {
    bits.push(`${cue('no_access', `${rm.not_permitted.length} removal(s) not permitted`,
      rm.not_permitted.map((r) => `${r.entity_slug}: ${String(r.words || '').replace(/^not permitted · /, '')}`).join('; '))}
      ${names(rm.not_permitted)}`);
  }
  if (rm.failed?.length) {
    bits.push(`${cue('error', `${rm.failed.length} could not be detached · ${rm.failed[0].words || 'Egeria gave no sentence'}`,
      rm.failed.map((r) => `${r.entity_slug}: ${r.words}`).join('; '))} ${names(rm.failed)}`);
  }
  if (rm.waiting?.length) {
    bits.push(`${cue('queued', `${rm.waiting.length} removal(s) waiting`,
      rm.waiting.map((r) => `${r.entity_slug}: ${r.words}`).join('; '))} ${names(rm.waiting)}`);
  }
  if (rm.withdrawn_before_sent?.length) {
    bits.push(cue('unrun', `${rm.withdrawn_before_sent.length} add(s) withdrawn before they were sent`,
      rm.withdrawn_before_sent.join(', ')));
  }
  return bits.length ? `<span data-removals class="block">${bits.join(' · ')}</span>` : '';
}

/** The link back to RE written on the WorkingSet (`additionalProperties.resourceExplorerUrl`): visible when it was
 *  refused or failed, quiet when it was set, unchanged or not configured. */
function linkBackHtml(lb) {
  if (!lb) return '';
  if (lb.state === 'not permitted') {
    return `<span data-link-back="not-permitted" class="block">${cue('no_access', 'link back to RE not permitted',
      String(lb.words || '').replace(/^not permitted · /, ''))}</span>`;
  }
  if (lb.state === 'failed') {
    return `<span data-link-back="failed" class="block">${cue('error', `link back to RE not set · ${lb.words || 'Egeria gave no sentence'}`,
      lb.url || '')}</span>`;
  }
  return '';
}

/** A whole refusal (403: nothing was permitted) as the cue, the reason on hover. */
const refusedCue = (err) => cue('no_access', 'not permitted', err.message);

/* ── Cell state ─────────────────────────────────────────────────────────
 *
 * The same state vocabulary the question rows use, derived per cell.
 * Deliberately NOT a boolean "has data": a grid of ticks and blanks would
 * collapse "never ran", "ran and found nothing" and "nothing can answer
 * this" into one mark, which is the failure this whole UI argues against —
 * and at grid density it would be far harder to notice.
 *
 * `CELL` is now a thin view over the one glyph table (glyphs.js) --
 * REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1: before this, `CELL` and
 * app.js's `GLYPH` each declared their own glyph, and disagreed -- `◐` meant
 * "partial" here and "ran, not at this level" there, and `no-surveyor` and
 * `unrun` shared ○ here, told apart ONLY by `tone` (colour), which breaks
 * the "colour is never the only channel" rule this same file's own header
 * comment states. `no-surveyor` now reads ◌, its own glyph.
 *
 * `tone` stays a value declared HERE, not derived from glyphs.js -- that
 * module unifies glyph MEANING (the symbol + its word), not colour role.
 * These tones are unchanged from before this pass; see glyphs.js's module
 * docstring for why a colour pass isn't part of this consolidation.
 */
const CELL_TONE = {
  answered: 'text-state-ok', nothing: 'text-state-ok', partial: 'text-state-warn',
  unrun: 'text-state-warn', human: 'text-accent-ink', 'no-surveyor': 'text-state-gap',
  unclassified: 'text-ink-muted', running: 'text-accent-ink', unknown: 'text-ink-muted',
  // Stored output exists; which of measured/partial it is has NOT been
  // established, because establishing it costs 47s for one analysis. Its own
  // glyph rather than a tick: a tick would claim `answered`, which is
  // precisely the state nobody has read.
  //
  // HOLLOW, not filled. A filled square was the heaviest mark in the set
  // while meaning the least — "we haven't looked" — and that inversion is a
  // shape problem, not a colour one; muting a heavy shape fights it rather
  // than fixing it. Hollow reads as a container waiting to be filled, which
  // is exactly what it is: opening the cell resolves it, and so does the
  // background pass. It also sits correctly beside `○` (nothing here) and
  // the filled glyphs (something definite).
  stored: 'text-ink-muted',
};

export const CELL = Object.fromEntries(Object.keys(CELL_TONE).map((k) => [
  k, { glyph: GLYPH_STATES[k].glyph, tone: CELL_TONE[k], label: GLYPH_STATES[k].word },
]));

/** The open work list's own resource kind. The `entity_type` column is
 *  authoritative (a work list is homogeneous by construction --
 *  routes/work_lists.py `WorkListCreate.entity_type`), so a work list that
 *  arrives without one is a bug to surface, never one to cover with 'repo':
 *  that silent default is what sent a database work list's column read to the
 *  repo catalog ("Project 'X' not found"). Throws, naming the work list. */
function kindOf(wl) {
  const k = wl && wl.entity_type;
  if (typeof k !== 'string' || !k) {
    throw new Error(`work list ${wl && wl.slug ? `'${wl.slug}' ` : ''}has no entity_type`);
  }
  return k;
}

/** The open list's kind, in words, for the title line ("9 databases"). An
 *  unknown kind says "resources" rather than guessing one. */
function kindPlural(wl, n) {
  const names = { repo: ['repo', 'repos'], database: ['database', 'databases'],
                  filesystem: ['file system', 'file systems'] }[wl && wl.entity_type];
  if (!names) return n === 1 ? 'resource' : 'resources';
  return n === 1 ? names[0] : names[1];
}

/** A question Context records (context-recorded.js): the SAME state word the
 *  Questions pane and its KEY use, read from each member's own Context
 *  (`grid.contexts`, loaded once when the list opens). '' = not such a
 *  question; 'unknown' = this member's Context could not be read (never a
 *  guessed word). */
function contextCellState(q, slug) {
  const kind = kindOf(grid.workList);
  if (!contextRecordedSpec(q.question, kind)) return '';
  const enrichment = grid.contexts?.get(slug);
  return contextRecordedState(q.question, kind, enrichment) || 'unknown';
}

/**
 * One cell's state, from a question and a resource's facts.
 *
 * `facts` is the array from `/api/analyses/facts/{slug}`, whose entries carry
 * `state` from `surveyors/result_status.py` — already judged, so nothing here
 * turns "never ran" into "none found".
 */
export function cellState(question, factsById) {
  const kind = question.kind || 'unknown';
  if (kind === 'gap') return 'no-surveyor';
  if (kind === 'human') return 'human';
  if (kind === 'unknown') return 'unclassified';

  const ids = question.analysis_ids || [];
  if (!ids.length) return 'unclassified';

  const found = ids.map((id) => factsById.get(id)).filter(Boolean);
  if (!found.length) return 'unrun';
  if (found.some((f) => f.state === 'measured')) {
    return found.some((f) => f.state === 'never_run') ? 'partial' : 'answered';
  }
  if (found.every((f) => f.state === 'nothing_found')) return 'nothing';
  if (found.some((f) => f.state === 'partial')) return 'partial';
  if (found.every((f) => f.state === 'never_run')) return 'unrun';
  return 'partial';
}

/** Every analysis id the given questions read — the scope for the bulk call.
 *  Questions with no analysis contribute nothing, which is why a stage of
 *  `gap`/`human` questions costs almost nothing to display. */
function qsAnalysisIds(questions) {
  return (questions || []).flatMap((q) => q.analysis_ids || []);
}

/**
 * Analyses whose RESULTS READER is slow enough to be worth loading second.
 *
 * Measured on `egeria_git`, not guessed: 47.7s and 22.5s respectively, where
 * the other 32 analyses with readers total 0.6s between them. Named here in
 * one place with the numbers, because a list like this rots silently — if a
 * reader gets fixed, or another gets slow, this is the thing to re-measure.
 *
 * Being on this list costs nothing but arrival order, so a wrong entry is
 * cheap; missing one costs a blank-looking pane.
 */
const EXPENSIVE_ANALYSES = new Set(['architecture_recovery', 'architecture_diagram']);

/* ── State ──────────────────────────────────────────────────────────── */

export const grid = {
  workList: null,        // the open work list
  questions: [],         // columns
  rows: new Map(),       // entity_slug -> { facts, factsById, error }
  selected: new Set(),   // rows ticked for a bulk action
  analyses: null,        // the CURRENT STAGE's analyses; null = not read yet
  stageLabel: '',        // for the question key's heading
  heldPerspectives: [],  // which perspectives narrowed the columns
  commonRationale: null, // a rationale every member shares, shown once
  pendingAnalyses: new Set(),  // slow columns still arriving
  states: {},            // the cheap projection, per resource
  statesError: null,
  slowError: null,
  questionsUnfiltered: null,
  batch: null,           // the running set's progress
  poll: null,            // its interval handle
  note: '',
  digestOpen: false,     // decided at list open by unresolved area
  digestShare: 0,        // that measurement, for the summary line
  digestFromPreference: false,
  digestLogId: '',
  onlyQuestion: null,    // narrowed to one column, by index
  narrowIndex: 0,        // transposed view: which resource
  narrowFilter: 'all',   // transposed view: all | unresolved | stale
  bgToken: 0,            // invalidates an in-flight background resolve
  bgRemaining: 0,        // resources still to resolve
  bgLast: 0,             // ms the last chunk took, for the note
  bgError: null,
  bgErrors: new Map(),   // slug -> why its background read FAILED (not cancelled)
  ctx: null,             // the pane context, for actions raised from a popup
  contexts: new Map(),   // slug -> enrichment map, for questions Context records (context-recorded.js)
  scope: null,           // the LINKED investigation's scope, read live: { slug, name, inScope: Set, error }
};

/* ── The linked investigation's scope ───────────────────────────────────
 *
 * REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md §2. A list tagged with an
 * investigation shows, live, how many of its members are in that scope. It is
 * read from `list_investigation_members` every time the pane opens or an add
 * lands, and never stored: the list and the scope are two tables, so this is a
 * copy, not a share, and if someone removes a member from scope the line
 * becomes "6 of 9". Drift shows up; it does not hide. */
async function loadScope(ctx) {
  const wl = grid.workList;
  grid.scope = null;
  if (!wl || !wl.investigation) return;
  const slug = wl.investigation;
  const name = ctx.host?.investigationName?.(slug) || slug;
  try {
    const members = await listInvestigationMembers(slug);
    const kind = kindOf(wl);
    grid.scope = { slug, name, error: null,
      inScope: new Set((members || []).filter((m) => m.entity_type === kind).map((m) => m.entity_slug)) };
  } catch (err) {
    // Unknown stays unknown: a failed read is not "nothing in scope".
    grid.scope = { slug, name, error: err.message, inScope: new Set() };
  }
}

function renderLinked() {
  const host = document.getElementById('wl-linked');
  if (!host) return;
  const sc = grid.scope;
  if (!sc) { host.innerHTML = ''; return; }
  const name = `<span title="${esc(sc.name)}">${esc(shortName(sc.name))}</span>`;
  if (sc.error) {
    host.innerHTML = `<span class="text-state-warn">The scope of ${name} could not be read: ${esc(sc.error)}</span>`;
    return;
  }
  const total = grid.workList.members.length;
  const inN = grid.workList.members.filter((m) => sc.inScope.has(m.entity_slug)).length;
  const notAdded = total - inN;
  host.innerHTML = `<span class="tnum">${inN}</span> of <span class="tnum">${total}</span> in scope for ${name}${
    notAdded ? ` · <span class="tnum">${notAdded}</span> not added` : ''}`;
}

/** The scope column's words: ink, no glyph, no accent colour. */
function scopeWord(slug) {
  const sc = grid.scope;
  if (!sc || sc.error) return 'scope unknown';
  return sc.inScope.has(slug) ? 'in scope' : 'not in scope';
}

/* ── Rendering ──────────────────────────────────────────────────────── */

const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&apos;');

/**
 * Render the whole slice into the content pane.
 *
 * `ctx` carries the few things this module needs from the shell rather than
 * reaching into it: `el` (the pane), `stage`, `analyses` (the catalog for the
 * run picker), and `onExit` (back to the questions pane).
 */
export async function renderWorkListPane(ctx) {
  const { el, stage } = ctx;
  const wl = grid.workList;
  if (!wl) return;

  // The sub-tab rail keeps its order here too (design rule: identical
  // across every stage; grey out what a view lacks, never remove it). A
  // work list is a view over MANY resources, so the per-resource tabs are
  // present and marked, and the title says how to reach them.
  el.innerHTML = `
    <div class="mb-s4 flex flex-wrap items-baseline gap-s3 font-heading text-subtab">
      ${(ctx.subTabs || []).map((t) => `<span class="text-ink-muted" title="${esc(t.label)} is per resource — pick one from the sidebar"
        style="border-bottom:1px dashed currentColor;padding-bottom:1px">${esc(t.label)}</span>`).join('')}
      <span class="border-b border-accent pb-[2px] text-ink">Work list</span>
      <button data-act="exit" class="cursor-pointer bg-transparent text-ink-muted hover:text-ink">← back to questions</button>
    </div>
    <div class="flex flex-wrap items-baseline gap-s3">
      <h3 class="m-0 font-heading text-name font-normal">${esc(wl.display_name)}</h3>
      <span class="tnum text-caveat text-ink-muted">
        <span class="tnum">${wl.members.length}</span> ${esc(kindPlural(wl, wl.members.length))} · <span data-wl-stage-line>${esc(ctx.stageLabel || stage)}’s questions</span>
        ${wl.derived_from ? ` · narrowed from <span class="font-mono">${esc(wl.derived_from)}</span>` : ''}
        ${wl.egeria_guid ? ' · published to Egeria' : ' · not published'}
      </span>
    </div>
    <div id="wl-actions" class="mt-s3 flex flex-wrap items-baseline gap-s3 text-caveat"></div>
    <div id="wl-linked" class="mt-s2 text-caveat text-ink"></div>
    <div id="wl-note" class="mt-s2 text-caveat text-ink"></div>
    <div id="wl-progress" class="mt-s2"></div>
    <div class="my-s3 h-px bg-rule"></div>
    <div id="wl-common-rationale" class="mb-s2 text-provenance text-ink-muted"></div>
    <div id="wl-grid" class="overflow-x-auto"></div>
    <div id="wl-legend" class="mt-s3 flex flex-wrap gap-s3 text-caveat text-ink"></div>`;

  el.querySelector('[data-act="exit"]').addEventListener('click', () => ctx.onExit());

  // The runnable analyses for THIS STAGE, re-read whenever the pane renders.
  //
  // The dropdown used to show the whole catalog — 36 analyses on every stage —
  // because it was filled once at boot with no intent filter. Offering
  // `security_scan` under Scouting is not a longer menu, it is a menu that
  // disagrees with the funnel the rest of the screen is arranged around.
  grid.stageLabel = stage;
  grid.analyses = null;
  renderActions(ctx);
  await loadScope(ctx);
  renderLinked();
  renderActions(ctx);
  renderLegend();
  try {
    // The work list itself is homogeneous in entity_type (work_lists.py's
    // WorkListCreate.entity_type, one value per list, default "repo") --
    // this used to hardcode 'repo' regardless, silently showing the repo
    // analysis catalog's menu on a database/filesystem work list rather than
    // an honest error or the right menu.
    grid.analyses = await listAnalyses(kindOf(wl), { intent: stage });
  } catch (err) {
    grid.analyses = { error: err.message };
  }
  renderActions(ctx);

  await loadGrid(ctx);
}

function renderActions(ctx) {
  const host = document.getElementById('wl-actions');
  if (!host) return;
  const n = grid.selected.size;
  const loaded = Array.isArray(grid.analyses);
  const failed = grid.analyses && grid.analyses.error;
  const analyses = loaded ? grid.analyses.filter((a) => a.id || a.analysis_id) : [];
  // Four states, kept apart. "Not read yet", "this stage has none",
  // "the catalog could not be read" and "here they are" are different
  // sentences, and an empty dropdown that means the first three looks
  // identical to a broken one.
  const runnable = analyses.length > 0;
  const addWording = addActWording({
    invName: ctx.host?.investigationName?.(ctx.host?.currentInvestigation?.() || ''),
    count: grid.workList.members.length, selected: n,
  });
  host.innerHTML = `
    <select id="wl-analysis" ${runnable ? '' : 'disabled'}
      class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-ink">
      ${!loaded && !failed ? '<option value="">reading the catalog…</option>' : ''}
      ${failed ? `<option value="">catalog unavailable</option>` : ''}
      ${loaded && !runnable ? `<option value="">no analyses for ${esc(ctx.stage)}</option>` : ''}
      ${analyses.map((a) => `<option value="${esc(a.id || a.analysis_id)}">${esc(a.display_name || a.name || a.id || a.analysis_id)}</option>`).join('')}
    </select>
    ${
      // Disabled still LOOKS like a control — greyed but clickable-shaped,
      // inviting the click it cannot honor. With zero runnable analyses for
      // this stage there is nothing "Run across" or "Bring up to date" could
      // ever enqueue, so both are absent rather than disabled. `promote` and
      // `publish` below stay: they act on the work list itself, not on
      // analyses, so they remain meaningful even here.
      runnable ? `
    <button data-act="run"
      class="cursor-pointer rounded-sm border border-accent text-accent-ink bg-transparent px-2 py-[2px]"
      >Run across <span class="tnum">${n || grid.workList.members.length}</span></button>
    <button data-act="refresh"
      class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-ink"
      title="Re-run only what is stale, for ${n ? 'the selected rows' : 'every row'}. Shows the plan first."
      >Bring up to date</button>` : ''}
    <span class="text-ink-muted">${
      failed ? `<span class="text-state-warn">the analysis catalog could not be read: ${esc(failed)}</span>`
      : !loaded ? 'reading the catalog…'
      : !runnable ? `<span class="text-accent-ink">No analyses are listed for the ${esc(ctx.stage)} stage, so there is nothing to run here — by design for Enrichment, which is served by its Context tab</span>`
      : n ? `${n} selected` : 'all rows'}</span>
    <span class="ml-auto flex flex-wrap items-baseline gap-s3">
      <select id="wl-disposition" ${n ? '' : 'disabled'}
        class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-ink">
        <option value="">mark selected as…</option>
        ${VALID_DISPOSITIONS.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}
      </select>
      <button data-act="promote" ${n ? '' : 'disabled'}
        class="cursor-pointer bg-transparent text-accent-ink underline">promote <span class="tnum">${n}</span> →</button>
      <button data-act="export-csv" class="cursor-pointer bg-transparent text-accent-ink underline"
        title="Download this work list as CSV: the same columns the Find dialog reads, status_ columns for a reader only"
        >export CSV</button>
      <button data-act="add-to-investigation" class="cursor-pointer bg-transparent text-accent-ink underline"
        title="${esc(addWording.title)}">${esc(addWording.label)}</button>
      <button data-act="start-from-list" class="cursor-pointer bg-transparent text-accent-ink underline"
        title="Create an investigation from this list: its name, its description, its members in scope"
        >${esc(START_LABEL)}</button>
      <button data-act="publish" class="cursor-pointer bg-transparent text-accent-ink underline"
        >${grid.workList.egeria_guid ? 'Publish again' : 'Publish to Egeria'}</button>
    </span>`;

  host.querySelector('[data-act="run"]')?.addEventListener('click', () => runBatch(ctx));
  host.querySelector('[data-act="refresh"]')?.addEventListener('click', () => openRefreshPlan(ctx));
  host.querySelector('[data-act="promote"]')?.addEventListener('click', () => promote(ctx));
  host.querySelector('[data-act="publish"]')?.addEventListener('click', () => publish(ctx));
  host.querySelector('[data-act="add-to-investigation"]')?.addEventListener('click', () => addToInvestigation(ctx));
  host.querySelector('[data-act="start-from-list"]')?.addEventListener('click', () => startFromList(ctx));
  host.querySelector('[data-act="export-csv"]')?.addEventListener('click', () => exportCsv());
  host.querySelector('#wl-disposition')?.addEventListener('change', (e) => {
    const value = e.target.value;
    e.target.value = '';
    if (value) bulkDisposition(ctx, value);
  });
}

function renderLegend() {
  const host = document.getElementById('wl-legend');
  if (!host) return;
  host.innerHTML = '<span class="text-caps uppercase tracking-caps text-ink-muted">Key</span>'
    + Object.entries(CELL).filter(([k]) => k !== 'unknown').map(([, c]) =>
      `<span class="inline-flex items-baseline gap-[5px]">
        <span class="${c.tone} font-glyph" title="${esc(c.label)}" aria-label="${esc(c.label)}"
          >${c.glyph}</span><span>${esc(c.label)}</span></span>`).join('');
}

function note(html) {
  const el = document.getElementById('wl-note');
  if (el) el.innerHTML = html;
}

async function loadGrid(ctx) {
  const host = document.getElementById('wl-grid');
  const wl = grid.workList;
  host.innerHTML = '<div class="py-s3 text-answer text-ink-muted">Reading the set…</div>';

  // Columns: the stage's questions. Taken from the first member, because the
  // catalog is per resource TYPE, not per resource.
  try {
    // Perspectives NARROW THE COLUMNS here, exactly as they narrow the rows
    // in the questions pane. They were being ignored, so holding one changed
    // nothing on this screen — an inert control beside a live one.
    //
    // It is also the honest answer to "27 columns is too many": the axis for
    // cutting them down already exists and was simply not wired up.
    const held = [...(ctx.perspectives || [])];
    const checklist = await getQuestions(wl.members[0].entity_slug,
                                         { phase: ctx.stage, perspectives: held, entityType: kindOf(wl) });
    grid.questions = checklist.questions || [];
    grid.heldPerspectives = held;
    if (held.length) {
      // The residue, said out loud: a filtered grid that does not say what it
      // hid is a grid you cannot trust to be complete.
      try {
        const all = await getQuestions(wl.members[0].entity_slug, { phase: ctx.stage, entityType: kindOf(wl) });
        grid.questionsUnfiltered = (all.questions || []).length;
      } catch { grid.questionsUnfiltered = null; }
    } else {
      grid.questionsUnfiltered = grid.questions.length;
    }
  } catch (err) {
    host.innerHTML = `<div class="py-s3 text-answer text-state-warn">
      The question columns could not be read: ${esc(err.message)}</div>`;
    return;
  }

  // One shared rationale is said once, above the grid, rather than repeated
  // under all twelve rows — at 12 rows it was the noisiest thing on screen.
  const rationales = new Set(wl.members.map((m) => m.rationale || ''));
  grid.commonRationale = rationales.size === 1 ? [...rationales][0] : null;

  renderGrid();

  // TWO calls, cheap analyses first, so the matrix is READABLE IMMEDIATELY
  // and the slow columns fill in behind.
  //
  // Measured, on a large repo: `architecture_recovery`'s results reader takes
  // 47s and `architecture_diagram`'s 22s, against under a second for the
  // other 32 combined. Discovery's questions route to BOTH, so a four-member
  // Discovery matrix is ~65s even fanned out across resources — parallelism
  // cannot beat the slowest single resource, and that floor is one reader.
  //
  // So the split is not an optimisation, it is the difference between a blank
  // pane for a minute and a usable one in a second with two columns still
  // arriving. The cells that are still coming show their pending marker,
  // which the grid already distinguishes from every other state.
  const needed = [...new Set(qsAnalysisIds(grid.questions))];
  const quick = needed.filter((a) => !EXPENSIVE_ANALYSES.has(a));

  const applyBulk = (bulk) => {
    for (const m of wl.members) {
      const facts = bulk.subjects?.[m.entity_slug];
      const prev = grid.rows.get(m.entity_slug);
      if (facts) {
        const byId = prev?.factsById || new Map();
        for (const f of facts) byId.set(f.analysis_id, f);
        grid.rows.set(m.entity_slug, { facts: [...byId.values()], factsById: byId });
      } else if (!prev) {
        // Named by the server as unreadable, or simply absent. Either way it
        // is "we could not read this", not "this has no results".
        grid.rows.set(m.entity_slug, {
          error: bulk.unreadable?.[m.entity_slug] || 'no facts returned for this resource',
        });
      }
    }
  };

  const slugs = wl.members.map((m) => m.entity_slug);

  // PASS 1 — the cheap projection over EVERY column. Instant, and it carries
  // `measured_at`, so the grid can say how current it is before it knows what
  // it says.
  try {
    const proj = await getBulkStates(slugs, needed, kindOf(wl));
    grid.states = proj.states || {};
    renderGrid();
  } catch (err) {
    grid.statesError = err.message;
  }

  await loadMemberContexts(wl);

  // Decided HERE — once, when the list opens — and never re-evaluated as the
  // background pass resolves cells underneath it.
  decideDigest(wl.members, grid.questions);
  renderGrid();

  // PASS 2 — the full read for the analyses that are cheap to read.
  try {
    if (quick.length) { applyBulk(await getBulkFacts(slugs, quick, kindOf(wl))); }
  } catch (err) {
    grid.slowError = err.message;
  }
  grid.pendingAnalyses = new Set();
  renderGrid();

  // PASS 3 — resolve the remaining `□` in the BACKGROUND, a few resources at
  // a time, so a grid that opens full of squares settles into truth while the
  // first column is being read. That makes `□` a loading state that happens
  // to be honest, rather than a population you live with.
  //
  // It is deliberately not "viewport first": the grid is in triage order, so
  // row order already puts the least resolved rows at the top of each band —
  // which is where attention goes anyway. Claiming viewport precision we do
  // not implement would be worse than saying this.
  //
  // It yields between chunks, stops the moment another work list is opened,
  // and never blocks anything: every pass before it has already rendered.
  const slow = needed.filter((a) => EXPENSIVE_ANALYSES.has(a));
  if (slow.length) resolveInBackground(ctx, slugs, slow);
}

/** Read each member's Context enrichment, but ONLY when a column's answer is
 *  a Context row (context-recorded.js) -- otherwise no extra request is made.
 *  A member whose Context cannot be read is left out of the map, and its cell
 *  reads `?` ("could not read"), never a guessed state. */
async function loadMemberContexts(wl) {
  grid.contexts = new Map();
  const kind = kindOf(wl);
  if (!grid.questions.some((q) => contextRecordedSpec(q.question, kind))) return;
  const slugs = wl.members.map((m) => m.entity_slug);
  for (let i = 0; i < slugs.length; i += 6) {
    await Promise.all(slugs.slice(i, i + 6).map(async (slug) => {
      try {
        const ctx = await getContext(kind, slug);
        grid.contexts.set(slug, (ctx && ctx.enrichment) || {});
      } catch { /* left out: the cell says it could not read */ }
    }));
  }
  renderGrid();
}

/** Turn `□` into real states, a few resources at a time. */
async function resolveInBackground(ctx, slugs, analyses) {
  const token = ++grid.bgToken;
  const CHUNK = 2;
  grid.bgRemaining = slugs.length;
  grid.bgError = null;
  grid.bgErrors.clear();
  // Say so BEFORE the first chunk, not after it. The first chunk is the
  // slowest thing here — measured at 33s per resource on the architecture
  // analyses — so rendering the notice only on chunk completion left the
  // longest silence of the whole load completely unexplained.
  renderGrid();
  for (let i = 0; i < slugs.length; i += CHUNK) {
    // A new work list, or a second load of this one, invalidates this pass.
    if (token !== grid.bgToken) return;
    const chunk = slugs.slice(i, i + CHUNK);
    const started = Date.now();
    try {
      const bulk = await getBulkFacts(chunk, analyses, kindOf(grid.workList));
      if (token !== grid.bgToken) return;
      for (const slug of chunk) {
        const facts = bulk.subjects?.[slug];
        if (!facts) continue;
        const prev = grid.rows.get(slug);
        const byId = prev?.factsById || new Map();
        for (const f of facts) byId.set(f.analysis_id, f);
        grid.rows.set(slug, { facts: [...byId.values()], factsById: byId });
      }
    } catch (err) {
      // CANCELLED IS NOT ERRORED, and only this code knows which happened.
      //
      // `□` means "we have not looked". Once the pass has looked and FAILED,
      // that sentence is false — what is true is "we looked and could not read
      // it", which is exactly `?`, and `?` names its own cause. Keeping the
      // square would leave a promise of a read that is never coming, quietly.
      //
      // A cancellation learned nothing, so those cells stay squares and the
      // promise stays true: another pass will make it good.
      if (token !== grid.bgToken) return;         // cancelled — stays a square
      for (const slug of chunk) grid.bgErrors.set(slug, err.message);
      grid.bgError = err.message;
      grid.bgRemaining = 0;
      renderGrid();
      return;
    }
    grid.bgRemaining = Math.max(0, slugs.length - (i + chunk.length));
    grid.bgLast = Date.now() - started;
    renderGrid();
  }
  grid.bgRemaining = 0;
  renderGrid();
}

/** Fill the readout slot for one column, and mark that column's number.
 *
 * It HOLDS until another column is touched — the point is that you can read
 * the question while looking at the row you are comparing, which a tooltip
 * cannot do because it times out and covers the grid.
 */
/** True when the pane is too narrow for a grid to be honest. */
function isNarrow() {
  return window.matchMedia('(max-width: 719px)').matches;
}

/** One resource, questions down the page. The transposed view. */
function renderNarrow(host, wl, qs) {
  const members = orderedRows(wl.members, qs).filter((e) => e.member).map((e) => e.member);
  if (!members.length) { host.innerHTML = ''; return; }
  const idx = Math.min(grid.narrowIndex, members.length - 1);
  const m = members[idx];
  const slug = m.entity_slug;
  const row = grid.rows.get(slug);
  const per = grid.states?.[slug] || {};

  const stateOf = (q) => {
    const ids = q.analysis_ids || [];
    const ctxSt = contextCellState(q, slug);
    if (ctxSt) return ctxSt;
    if (!ids.length) {
      return q.kind === 'gap' ? 'no-surveyor' : q.kind === 'human' ? 'human' : 'unclassified';
    }
    if (row?.error) return 'unknown';
    if (row && ids.some((a) => row.factsById.has(a))) return cellState(q, row.factsById);
    if (ids.some((a) => per[a]?.has_results)) return 'stored';
    if (ids.every((a) => per[a]?.certain_never_run)) return 'unrun';
    return 'unknown';
  };
  const stampOf = (q) => {
    const ss = (q.analysis_ids || []).map((a) => per[a]?.measured_at || '').filter(Boolean);
    return ss.length ? ss.sort()[0] : '';
  };
  const isUnresolved = (q) => ['unrun', 'human', 'unknown', 'no-surveyor'].includes(stateOf(q));
  const isStale = (q) => {
    const d = daysSince(stampOf(q));
    return d !== null && d >= STALE_DAYS;
  };

  // The filter row is what replaces scanning. On a phone you do not read 27
  // rows — you tap Unresolved and read nine.
  const counts = {
    all: qs.length,
    unresolved: qs.filter(isUnresolved).length,
    stale: qs.filter(isStale).length,
  };
  const filter = grid.narrowFilter;
  const list = qs.map((q, i) => ({ q, i })).filter(({ q }) =>
    filter === 'unresolved' ? isUnresolved(q) : filter === 'stale' ? isStale(q) : true);

  host.innerHTML = `
    <div class="mb-s2 flex items-baseline gap-s2 border-b border-rule pb-[4px]">
      <button type="button" data-narrow="prev" class="cursor-pointer bg-transparent text-ink-muted"
        aria-label="Previous resource">←</button>
      <div class="min-w-0">
        <div class="truncate font-mono text-answer text-ink">${esc(slug)}</div>
        <div class="tnum text-provenance text-ink-muted">${idx + 1} of ${members.length} ·
          ${esc(grid.stageLabel || '')}${
            grid.scope ? ` · ${esc(scopeWord(slug))}` : ''}${
            m.disposition ? ` · ${esc(m.disposition)}` : ''}</div>
      </div>
      <button type="button" data-narrow="next" class="ml-auto cursor-pointer bg-transparent text-ink-muted"
        aria-label="Next resource">→</button>
    </div>
    <div class="mb-s2 flex gap-s3 text-caveat">
      ${[['all', `All ${counts.all}`], ['unresolved', `Unresolved ${counts.unresolved}`],
         ['stale', `Stale ${counts.stale}`]].map(([k, label]) =>
        `<button type="button" data-nfilter="${k}"
          class="cursor-pointer bg-transparent ${
            filter === k ? 'text-ink underline' : 'text-ink-muted'}">${esc(label)}</button>`).join('')}
    </div>
    ${list.length ? `<ul class="m-0 list-none p-0">
      ${list.map(({ q, i }) => {
        const c = CELL[stateOf(q)] || CELL.unclassified;
        const iso = stampOf(q);
        return `<li class="border-b border-rule">
          <button type="button" data-cell="${esc(slug)}" data-q="${i}"
            class="flex w-full items-baseline gap-s2 border-0 bg-transparent px-0 py-s2 text-left">
            <span class="w-[16px] shrink-0 ${c.tone} font-glyph">${c.glyph}</span>
            <span class="min-w-0 flex-1">
              <span class="text-answer text-ink">${esc(q.question)}</span>
              <span class="block text-provenance text-ink-muted">${esc(c.label)}</span>
            </span>
            <span class="tnum shrink-0 text-provenance ${
              isStale(q) ? 'text-state-warn' : 'text-ink-muted'}">${
              iso ? esc(ago(iso)) : '—'}</span>
          </button>
        </li>`;
      }).join('')}
    </ul>` : `<p class="py-s3 text-caveat text-ink-muted">Nothing in this filter for
      ${esc(slug)}.</p>`}`;

  host.querySelectorAll('button[data-cell]').forEach((b) => b.addEventListener('click',
    () => openCellDetail(b.dataset.cell, Number(b.dataset.q), grid.ctx)));
  host.querySelectorAll('[data-nfilter]').forEach((b) => b.addEventListener('click', () => {
    grid.narrowFilter = b.dataset.nfilter;
    renderGrid();
  }));
  host.querySelectorAll('[data-narrow]').forEach((b) => b.addEventListener('click', () => {
    const step = b.dataset.narrow === 'next' ? 1 : -1;
    grid.narrowIndex = (idx + step + members.length) % members.length;
    grid.narrowFilter = filter;
    renderGrid();
  }));
}

/** One resource's verdict trail, from the matrix.
 *
 * The current UI shows the SEQUENCE of dispositions; /next showed the current
 * value and nothing else, and the matrix — the surface where triage actually
 * happens — had no route to it at all. A reviewer could see what was decided
 * but not that it was decided twice, reversed, or by whom.
 *
 * That is the provenance of a HUMAN judgement, and this round's argument for
 * surfacing provenance applies to it at least as strongly as to a survey's.
 */
async function openResourceDetail(member) {
  if (!member) return;
  const slug = member.entity_slug;
  const el = openDialog(slug, member.disposition
    ? `now: ${member.disposition}` : 'no verdict recorded');
  const body = el.querySelector('#wl-detail-body');
  if (!member.github_url) {
    body.innerHTML = `<p>Dispositions are keyed on a GitHub URL and this resource
      has none recorded, so its verdict trail cannot be read.</p>`;
    return;
  }
  let rows;
  try {
    rows = await getDispositionHistory(member.github_url);
  } catch (err) {
    body.innerHTML = `<p class="text-state-warn">The history could not be read:
      ${esc(err.message)}</p>`;
    return;
  }
  if (!Array.isArray(rows) || !rows.length) {
    body.innerHTML = `<p>No verdict has ever been recorded for ${esc(slug)}.
      That is the answer, not a gap.</p>`;
    return;
  }
  // Oldest first: the point is the sequence, and a sequence read backwards is
  // a list of values.
  const ordered = [...rows].sort((a, b) => (a.decided_at || '').localeCompare(b.decided_at || ''));
  body.innerHTML = `
    <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Verdicts, in order</div>
    <ol class="m-0 list-none p-0">
      ${ordered.map((r, i) => `<li class="flex gap-s2 border-b border-rule py-[5px]">
        <span class="tnum w-[18px] shrink-0 text-right text-ink-muted">${i + 1}</span>
        <span class="min-w-0 text-provenance text-ink-muted">${verdictLineHtml(r, esc)}</span>
      </li>`).join('')}
    </ol>
    ${ordered.length > 1 ? `<p class="mt-s2">${changedTimesHtml(ordered.length - 1)}.</p>` : ''}`;
}

/** Name the cause of an unreadable cell, from the server's own message.
 *
 * Deliberately a small, closed set of causes with a fallback that admits it
 * does not know. Guessing a cause would be worse than the bare `?` it
 * replaces — the point is to make a real defect recognisable on sight, and a
 * confident wrong label defeats exactly that.
 */
function whyUnreadable(msg) {
  const m = String(msg || '').toLowerCase();
  if (/timeout|timed out/.test(m)) return 'the read timed out';
  if (/no such|not found|unknown analysis|missing/.test(m)) return 'the analysis is not in the catalog';
  if (/json|decode|parse|unparsable|invalid/.test(m)) return 'the stored result could not be parsed';
  if (/permission|forbidden|denied|401|403/.test(m)) return 'not permitted to read this';
  if (/connect|refused|unreachable|network|load failed/.test(m)) return 'the server could not be reached';
  return 'the reader failed — cause not recognised';
}

/* ── The column digest ───────────────────────────────────────────────────
 *
 * One line per question showing how its states distribute across the rows in
 * view, sorted by how much is unresolved.
 *
 * At twelve rows it is a summary you glance at. Its real job is at fifty,
 * where the grid stops being scannable: the answer to scale is not a smaller
 * cell, it is an inversion — read the distribution first, then open the one
 * question that looks wrong. Growing the grid was never the fix, because a
 * matrix earns its cost by holding a cohort you can keep in your head, and
 * nobody holds fifty.
 *
 * Clicking a line narrows the grid to that question, which is the same move
 * as opening a cell, one axis up.
 */

const DIGEST_TONE = {
  answered: 'var(--wl-ok, #2f6f4f)',
  nothing: 'var(--wl-neutral, #6b6b68)',
  partial: 'var(--wl-warn, #a8712a)',
  human: 'var(--wl-warn, #a8712a)',
  stored: 'var(--wl-unread, #8d8a83)',
};
const DIGEST_REST = 'var(--wl-none, #c9c6bf)';

/** State counts for one question across the given members. */
function columnDigest(members, q) {
  const counts = {};
  let unresolved = 0;
  for (const m of members) {
    const slug = m.entity_slug;
    const row = grid.rows.get(slug);
    const per = grid.states?.[slug] || {};
    const ids = q.analysis_ids || [];
    let st;
    const ctxSt = contextCellState(q, slug);
    if (ctxSt) st = ctxSt;
    else if (!ids.length) st = q.kind === 'gap' ? 'no-surveyor' : q.kind === 'human' ? 'human' : 'unclassified';
    else if (row && !row.error && ids.some((a) => row.factsById.has(a))) st = cellState(q, row.factsById);
    else if (ids.some((a) => per[a]?.has_results)) st = 'stored';
    else if (ids.every((a) => per[a]?.certain_never_run)) st = 'unrun';
    else st = 'unknown';
    counts[st] = (counts[st] || 0) + 1;
    // "Unresolved" means nobody has an answer yet — NOT that the answer is
    // uncomfortable. `nothing` is a result; `stored` is one we have not read.
    if (['unrun', 'human', 'unknown', 'no-surveyor'].includes(st)) unresolved += 1;
  }
  return { counts, unresolved };
}

function digestHtml(members, qs) {
  if (!qs.length || !members.length) return '';
  const rows = qs.map((q, i) => ({ q, i, ...columnDigest(members, q) }))
    .sort((a, b) => b.unresolved - a.unresolved);
  const total = members.length;
  const bar = (counts) => {
    const order = ['answered', 'nothing', 'partial', 'stored', 'human'];
    const seg = order.filter((k) => counts[k]).map((k) =>
      `<i style="width:${(counts[k] / total) * 100}%;background:${DIGEST_TONE[k]}"></i>`).join('');
    const rest = total - order.reduce((n, k) => n + (counts[k] || 0), 0);
    return seg + (rest > 0 ? `<i style="width:${(rest / total) * 100}%;background:${DIGEST_REST}"></i>` : '');
  };
  return `
    <details id="wl-digest" class="mb-s3" ${grid.digestOpen ? 'open' : ''}>
      <summary class="cursor-pointer text-caps uppercase tracking-caps text-ink-muted">
        Column digest · <span class="tnum">${qs.length}</span> questions ×
        <span class="tnum">${total}</span> resources · sorted by unresolved${
          grid.digestFromPreference
            ? ' · your choice for this list'
            : grid.digestOpen
              ? ` · <span class="tnum">${Math.round(grid.digestShare * 100)}%</span> of this
                  grid is unread, so this opened first`
              : ''}
      </summary>
      <div class="mt-s2">
        ${rows.map((r) => `<button type="button" data-digest="${r.i}"
          class="flex w-full items-center gap-s2 border-0 bg-transparent px-0 py-[4px] text-left text-caveat text-ink hover:bg-[rgba(32,31,29,.05)]">
          <span class="tnum w-[22px] shrink-0 text-right font-mono text-ink-muted">${r.i + 1}</span>
          <span class="min-w-0 flex-1 truncate">${esc(r.q.question)}</span>
          <span class="wl-bar flex h-[9px] w-[160px] shrink-0 overflow-hidden rounded-[2px]">${bar(r.counts)}</span>
          <span class="tnum w-[34px] shrink-0 text-right ${
            r.unresolved ? 'text-accent-ink' : 'text-ink-muted'}">${r.unresolved}</span>
        </button>`).join('')}
      </div>
      <div class="mt-s2 flex flex-wrap gap-s3 text-provenance text-ink-muted">
        ${[['answered', 'answered'], ['nothing', 'nothing found'],
           ['partial', 'partial'], ['stored', 'not read yet'], ['human', 'needs a person']]
          .map(([k, label]) => `<span><i style="display:inline-block;width:9px;height:9px;
            border-radius:2px;margin-right:5px;background:${DIGEST_TONE[k]}"></i>${esc(label)}</span>`).join('')}
        <span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;
          margin-right:5px;background:${DIGEST_REST}"></i>not run · no reader yet · unread</span>
      </div>
    </details>`;
}

/** Rows in triage order, banded by disposition.
 *
 * NAME ORDER IS A FILING CONVENTION and means nothing in a triage view. Two
 * changes, both aimed at the eye:
 *
 * - Within a band, the least resolved rows come FIRST. Ragged rows collect at
 *   one end instead of being sprinkled through, so the eye stops sweeping
 *   settled space looking for work.
 * - Bands by disposition, because once verdicts land a long grid is really
 *   several small grids — kept, parked, dropped — and only one of them is
 *   still work.
 *
 * Returns a flat list of `{member}` and `{band, count}` entries so the table
 * body stays one map.
 */
function orderedRows(members, qs) {
  const unresolvedOf = (m) => {
    const per = grid.states?.[m.entity_slug] || {};
    const row = grid.rows.get(m.entity_slug);
    let n = 0;
    for (const q of qs) {
      const ids = q.analysis_ids || [];
      if (!ids.length) continue;
      const settled = row && !row.error
        && ['answered', 'nothing'].includes(cellState(q, row.factsById));
      const stored = ids.some((a) => per[a]?.has_results);
      if (!settled && !stored) n += 1;
    }
    return n;
  };
  const byBand = new Map();
  for (const m of members) {
    const d = m.disposition || 'undecided';
    byBand.set(d, [...(byBand.get(d) || []), m]);
  }
  // Bands in the order a triage session works through them, not alphabetical.
  const ORDER = ['undecided', 'investigating', 'tracking', 'recommended',
                 'using', 'abandoned', 'ignored'];
  const bands = [...byBand.keys()].sort(
    (a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99));
  const out = [];
  const banded = bands.length > 1;   // one band is not a grouping, it is noise
  for (const b of bands) {
    const rows = byBand.get(b).sort((x, y) => unresolvedOf(y) - unresolvedOf(x));
    if (banded) out.push({ band: b, count: rows.length });
    for (const m of rows) out.push({ member: m });
  }
  return out;
}

function readout(qi) {
  const q = grid.questions[qi];
  const el = document.getElementById('wl-readout');
  if (!q || !el) return;
  const ids = q.analysis_ids || [];
  el.innerHTML = `
    <span class="tnum font-mono text-accent-ink">${qi + 1}</span>
    <span class="text-ink">${esc(q.question)}</span>
    <span class="ml-auto shrink-0 text-provenance text-ink-muted">${
      ids.length
        ? `<span class="tnum">${ids.length}</span> ${
            ids.length === 1 ? 'analysis' : 'analyses'} · ${esc(ids.join(', '))}`
        : esc(q.kind === 'gap' ? 'no reader yet for this'
            : q.kind === 'human' ? 'answered by a person'
            : q.kind === 'direct' ? 'a direct field, not a survey'
            : 'no analysis is mapped to this')}</span>`;
  const host = document.getElementById('wl-grid');
  host?.querySelectorAll('th[data-col]').forEach((th) => {
    th.classList.toggle('wl-col-live', Number(th.dataset.col) === qi);
  });
}

function renderGrid() {
  const host = document.getElementById('wl-grid');
  if (!host) return;
  const wl = grid.workList;
  const qs = grid.questions;

  // Columns are NUMBERED and the questions are listed in full underneath.
  //
  // They used to be the question text clamped to three lines at 120px, which
  // at 27 columns meant nobody could read any of them — "I can't see the full
  // questions". A tooltip is not a fix: it shows one at a time, needs a mouse,
  // and cannot be read alongside the row you are comparing it against. The
  // key below is readable, printable, and keeps the cells narrow enough that
  // a wide stage still scans.
  if (!qs.length) {
    host.innerHTML = `<div class="py-s3 text-answer text-ink">
      No questions in ${esc(grid.stageLabel || 'this stage')}${
        grid.heldPerspectives.length
          ? ` match ${esc(grid.heldPerspectives.join(' · '))}. That is a fact about the
              filter, not about these resources — drop a perspective to widen it.`
          : '.'}</div>`;
    return;
  }

  // BELOW ~720px THE AXES SWAP. There is no narrow version of a wide grid;
  // there is a different view of the same data. One resource at a time,
  // questions down the page: frozen columns stop being needed because there
  // is one column, and a full-width row carries the glyph, the question text
  // you had been keying to a number, and its own date — the three things the
  // wide view had to compress. This is the honest layout at that size, not a
  // fallback.
  if (isNarrow()) { renderNarrow(host, wl, qs); return; }

  // The columns actually rendered. Narrowing keeps each question's TRUE
  // index, so cell clicks, the readout and the key all still address the same
  // question they did in the full grid.
  const shown = grid.onlyQuestion == null || !qs[grid.onlyQuestion]
    ? qs.map((q, i) => ({ q, i }))
    : [{ q: qs[grid.onlyQuestion], i: grid.onlyQuestion }];

  const cr = document.getElementById('wl-common-rationale');
  if (cr) cr.textContent = grid.commonRationale ? `All members: ${grid.commonRationale}` : '';

  host.innerHTML = `
    ${digestHtml(wl.members, qs)}
    ${grid.bgRemaining ? `<div class="mb-s2 text-provenance text-ink-muted">
      Resolving <span class="tnum">${grid.bgRemaining}</span> more resource(s) in the
      background — <span class="font-mono">□</span> cells become their real state as they land.</div>` : ''}
    ${grid.bgError ? `<div class="mb-s2 text-provenance text-state-warn">
      The background read stopped: ${esc(grid.bgError)}.
      <span class="tnum">${grid.bgErrors.size}</span> resource(s) now show
      <span class="font-mono">?</span> — looked at and unreadable, with the cause on
      the cell. Any remaining <span class="font-mono">□</span> were never reached.</div>` : ''}
    <div id="wl-readout" class="mb-s2 flex items-baseline gap-s2 border-b border-rule pb-[4px] text-caveat">
      <span class="text-ink-muted">Hover or focus a column to read its question.</span>
    </div>
    ${grid.onlyQuestion != null && qs[grid.onlyQuestion] ? `
      <div class="mb-s2 text-caveat text-accent-ink">
        Showing question <span class="tnum">${grid.onlyQuestion + 1}</span> only ·
        <button type="button" data-act="all-cols"
          class="cursor-pointer bg-transparent underline">show all
          <span class="tnum">${qs.length}</span></button>
      </div>` : ''}
    <table class="w-full border-collapse text-caveat">
      <thead>
        <tr class="border-b border-rule">
          <th class="wl-freeze-1 w-[26px] p-[6px]"></th>
          <th class="wl-freeze-2 p-[6px] text-left font-heading text-ink">Resource</th>
          ${shown.map(({ q, i }) => `<th class="wl-col p-[6px] align-bottom font-heading text-ink"
            data-col="${i}" title="${esc(q.question)}">
            <span class="tnum">${i + 1}</span></th>`).join('')}
          <th class="p-[6px] text-left font-heading text-ink">Answered</th>
          ${grid.scope ? '<th class="p-[6px] text-left font-heading text-ink" data-scope-col>Scope</th>' : ''}
        </tr>
      </thead>
      <tbody>
        ${orderedRows(wl.members, qs).map((entry) => entry.band
          ? `<tr class="wl-band"><td colspan="${shown.length + 3 + (grid.scope ? 1 : 0)}"
               class="p-[6px] text-caps uppercase tracking-caps text-ink-muted">
               ${esc(entry.band)} · <span class="tnum">${entry.count}</span></td></tr>`
          : rowHtml(entry.member, shown, qs)).join('')}
      </tbody>
    </table>

    <div class="mt-s4">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">
        The questions · <span class="tnum">${qs.length}</span> in ${esc(grid.stageLabel || '')}${
          grid.heldPerspectives.length && grid.questionsUnfiltered != null
            ? ` · <span class="tnum">${Math.max(0, grid.questionsUnfiltered - qs.length)}</span> hidden by
               ${esc(grid.heldPerspectives.join(' · '))}`
            : ''}
      </div>
      <ol class="m-0 flex list-none flex-col gap-[6px] p-0">
        ${qs.map((q, i) => `<li class="flex gap-s2">
          <span class="tnum w-[22px] shrink-0 text-right text-ink-muted">${i + 1}</span>
          <span class="min-w-0">
            <span class="text-answer text-ink">${esc(q.question)}</span>
            ${(q.perspectives || []).length ? `<span class="ml-s2 text-caps text-ink-muted"
              >${esc((q.perspectives || []).join(' · '))}</span>` : ''}
            ${q.analysis_ids && q.analysis_ids.length
              ? `<div class="text-provenance text-ink-muted">${esc(q.analysis_ids.join(', '))}</div>`
              : `<div class="text-provenance text-ink-muted">${
                  q.kind === 'gap' ? 'no reader yet for this'
                  : q.kind === 'human' ? 'answered by a person, not a survey'
                  : q.kind === 'direct' ? 'a direct field, not a survey'
                  : 'no analysis is mapped to this'}</div>`}
          </span>
        </li>`).join('')}
      </ol>
    </div>`;

  host.querySelectorAll('button[data-cell]').forEach((b) => {
    b.addEventListener('click', () => openCellDetail(b.dataset.cell, Number(b.dataset.q), grid.ctx));
    // Pointer AND keyboard, the same way. A tooltip only ever served the first.
    b.addEventListener('mouseenter', () => readout(Number(b.dataset.q)));
    b.addEventListener('focus', () => readout(Number(b.dataset.q)));
  });
  host.querySelector('[data-act="all-cols"]')?.addEventListener('click', () => {
    grid.onlyQuestion = null;
    renderGrid();
  });
  host.querySelector('#wl-digest')?.addEventListener('toggle', (e) => {
    grid.digestOpen = e.target.open;
    try {
      localStorage.setItem(digestKey(grid.workList?.slug || ''), String(e.target.open));
    } catch (_) { /* private mode: the toggle still works for this session */ }
    noteDigestToggled(e.target.open);
  });
  host.querySelectorAll('button[data-res]').forEach((b) => b.addEventListener('click', () => {
    const m = grid.workList.members.find((x) => x.entity_slug === b.dataset.res);
    openResourceDetail(m);
  }));
  host.querySelectorAll('button[data-digest]').forEach((b) => {
    b.addEventListener('mouseenter', () => readout(Number(b.dataset.digest)));
    b.addEventListener('click', () => {
      // Narrowing to one question is the same move as opening one cell, one
      // axis up: same rows, one column, nothing hidden that was not chosen.
      grid.onlyQuestion = grid.onlyQuestion === Number(b.dataset.digest)
        ? null : Number(b.dataset.digest);
      renderGrid();
    });
  });
  host.querySelectorAll('th[data-col]').forEach((th) => {
    th.addEventListener('mouseenter', () => readout(Number(th.dataset.col)));
  });
  host.querySelectorAll('input[data-row]').forEach((cb) => cb.addEventListener('change', () => {
    if (cb.checked) grid.selected.add(cb.dataset.row);
    else grid.selected.delete(cb.dataset.row);
    renderActions(window.__wlCtx);
  }));
}

function rowHtml(member, shown, qs) {
  const slug = member.entity_slug;
  const row = grid.rows.get(slug);
  // Each cell carries ITS OWN measurement date, because each analysis ran
  // when it ran. The Measured column summarises; this is where the fact is.
  const per = grid.states?.[slug] || {};
  const cellStamp = (q) => {
    const stamps = (q.analysis_ids || [])
      .map((a) => per[a]?.measured_at || '').filter(Boolean);
    return stamps.length ? stamps.sort()[0] : '';   // oldest input to this answer
  };
  const stampNote = (q) => {
    const iso = cellStamp(q);
    if (!iso) return '';
    const d = daysSince(iso);
    return `\nmeasured ${ago(iso)} (${iso})${
      d !== null && d >= STALE_DAYS ? ' — stale' : ''}`;
  };

  const cells = shown.map(({ q, i: qi }) => {
    // AGE IS A RULE UNDER THE GLYPH, not a mark beside it.
    //
    // An appended `·` inside a 26px cell was very nearly invisible, and it
    // could only ever say one bit. A rule on the cell's baseline does not
    // compete with the glyph for the same pixels, survives print, reads as an
    // annotation rather than as part of the mark — and it GRADES, which age
    // deserves, being continuous. Nothing under 7 days, a short hairline past
    // 7, the full cell width past 30.
    //
    // Still no recolouring: "this answer is old" and "this answer is bad" are
    // different claims, and the warn colour is already spent on the second.
    // Three channels that cannot collide — glyph says what state, hue says
    // whether a person is needed, rule says how old. A column of stale cells
    // then reads as a broken underline running down the grid.
    const ageClass = ageRule(cellStamp(q));

    // KIND FIRST. A question with no surveyor, or one only a person can
    // answer, has no analysis to read and no facts to wait for — it is
    // settled before any of the fact logic below applies. Losing this branch
    // left such a column showing `…` forever, because "still loading" is what
    // the fall-through says when nothing is known and nothing ever will be.
    const kindState = contextCellState(q, slug)
      || { gap: 'no-surveyor', human: 'human', unknown: 'unclassified' }[q.kind];
    if (kindState || !(q.analysis_ids || []).length) {
      const c = CELL[kindState] || CELL.unclassified;
      return `<td class="wl-cell p-[6px] ${c.tone} font-glyph" title="${esc(q.question)} — ${esc(c.label)}">${c.glyph}</td>`;
    }
    if (row?.error) {
      // A resource whose facts could not be read is NOT a resource with no
      // results. One state for "we could not look", never blank — and it says
      // WHY, because "could not read" found a real defect this round only
      // because somebody happened to be looking at it. A `?` that names its
      // own cause turns that accident into a routine catch.
      return `<td class="wl-cell p-[6px] ${CELL.unknown.tone} font-glyph"
        title="${esc(q.question)} — ${esc(whyUnreadable(row.error))}\n${esc(row.error)}"
        >${CELL.unknown.glyph}</td>`;
    }
    const running = !!grid.batch?.runs?.find(
      (r) => r.entity_slug === slug && ['queued', 'claimed', 'running'].includes(r.state));
    if (running) {
      return `<td class="wl-cell p-[6px] ${CELL.running.tone} font-glyph" title="${esc(q.question)} — ${esc(CELL.running.label)}">${CELL.running.glyph}</td>`;
    }
    // Full facts, when they have been read.
    const haveFacts = row && (q.analysis_ids || []).some((a) => row.factsById.has(a));
    if (haveFacts) {
      const c = CELL[cellState(q, row.factsById)] || CELL.unclassified;
      return `<td class="wl-cell p-[6px] ${c.tone} font-glyph"><button type="button" class="wl-cellbtn ${ageClass}" data-cell="${esc(slug)}" data-q="${qi}" title="${esc(q.question)} — ${esc(c.label)}${esc(stampNote(q))}
click for the latest results">${c.glyph}</button></td>`;
    }
    // Otherwise the CHEAP PROJECTION answers, and only as far as it honestly
    // can: there is stored output, or there provably is not. It cannot tell
    // `measured` from `partial`, so it never claims `answered`.
    const proj = (q.analysis_ids || []).map((a) => per[a]).filter(Boolean);
    if (proj.length) {
      // Looked and failed. There is no such thing as a permanent square —
      // only a permanent failure, which says so.
      const readErr = grid.bgErrors.get(slug);
      if (readErr && proj.some((v) => v.has_results)) {
        const c = CELL.unknown;
        return `<td class="wl-cell p-[6px] ${c.tone} font-glyph"
          title="${esc(q.question)} — ${esc(whyUnreadable(readErr))}\n${esc(readErr)}"
          >${c.glyph}</td>`;
      }
      if (proj.some((v) => v.has_results)) {
        const c = CELL.stored;
        return `<td class="wl-cell p-[6px] ${c.tone} font-glyph"><button type="button" class="wl-cellbtn ${ageClass}" data-cell="${esc(slug)}" data-q="${qi}" title="${esc(q.question)} — ${esc(c.label)}${esc(stampNote(q))}
click for the latest results">${c.glyph}</button></td>`;
      }
      if (proj.every((v) => v.certain_never_run)) {
        const c = CELL.unrun;
        return `<td class="wl-cell p-[6px] ${c.tone} font-glyph"><button type="button" class="wl-cellbtn ${ageClass}" data-cell="${esc(slug)}" data-q="${qi}" title="${esc(q.question)} — ${esc(c.label)}
click for the latest results">${c.glyph}</button></td>`;
      }
    }
    // Nothing known yet — still loading. Never "not run": that would be a
    // confident wrong answer with a delay on it.
    return '<td class="wl-cell p-[6px] text-ink-muted" title="still loading">…</td>';
  }).join('');

  const staleCount = qs.reduce((n, q) => {
    const stamps = (q.analysis_ids || []).map((a) => per[a]?.measured_at || '').filter(Boolean);
    if (!stamps.length) return n;
    const d = daysSince(stamps.sort()[0]);
    return n + (d !== null && d >= STALE_DAYS ? 1 : 0);
  }, 0);

  const answered = row && !row.error
    ? qs.filter((q) => ['answered', 'nothing'].includes(cellState(q, row.factsById))).length
    : null;

  return `<tr class="border-b border-rule">
    <td class="wl-freeze-1 p-[6px]"><input type="checkbox" data-row="${esc(slug)}" ${
      grid.selected.has(slug) ? 'checked' : ''}></td>
    <td class="wl-freeze-2 p-[6px] text-ink">
      <button type="button" data-res="${esc(slug)}"
        class="cursor-pointer border-0 bg-transparent p-0 text-left font-mono text-provenance text-ink underline decoration-dotted"
        title="Its verdicts, in order">${esc(slug)}</button>${
        member.disposition
          ? `<span class="ml-s2 text-provenance text-ink-muted">${esc(member.disposition)}</span>`
          : ''}
      ${member.rationale && member.rationale !== grid.commonRationale
          ? `<div class="text-provenance text-ink-muted">${esc(member.rationale)}</div>` : ''}
    </td>
    ${cells}
    <td class="p-[6px] tnum text-ink-muted">${
      answered === null ? '—' : `${answered} of ${qs.length}`}${
      // A row's age signal is a COUNT, not a date and not a span. A span is
      // two dates to interpret in a narrow column, where the first reads as
      // "the" date; a count is a plain fact about the row — how many of its
      // cells are old — and needs no interpretation at all.
      staleCount ? `<div class="text-provenance text-state-warn"
        title="Measured more than ${STALE_DAYS} days ago. The dates themselves are on the cells."
        >${staleCount} stale</div>` : ''}</td>${
    grid.scope ? `<td class="p-[6px] text-ink" data-scope-cell="${esc(slug)}">${esc(scopeWord(slug))}</td>` : ''}
  </tr>`;
}

/** Staleness threshold, for the mark on a cell and the wording in its popup.
 *
 * There is deliberately NO per-resource "as of" date. Each analysis ran when
 * it ran — on one row here the newest measurement is from today and the oldest
 * from 31 August — so a single date per row is wrong whichever end it takes.
 * The date belongs to the cell, and the cell shows it on click.
 */
/** PLACEHOLDERS. Seven and thirty days are calendar habits, not facts about
 *  surveys: a language classification is still fresh at ninety days and a
 *  release check is stale at fourteen. The honest measure is "older than this
 *  analysis's own re-run cadence", and that cadence is a value Automate will
 *  own (`notification_subscriptions` / `resource_schedules`).
 *
 *  Until it exists these two rounds are a stand-in. This comment is what stops
 *  them quietly becoming policy — when the per-analysis cadence lands, the
 *  rule should read from it and these constants should go. */
const STALE_DAYS = 7;
/** Past this, the rule runs the full width of the cell. Same placeholder. */
const VERY_STALE_DAYS = 30;

/** The age rule's class for one measurement date: '' / short / full. */
function ageRule(iso) {
  const d = daysSince(iso);
  if (d === null) return '';
  if (d >= VERY_STALE_DAYS) return 'wl-age2';
  if (d >= STALE_DAYS) return 'wl-age1';
  return '';
}


/* ── One cell, opened ────────────────────────────────────────────────────
 *
 * A glyph says which state a question is in; it cannot say what the answer
 * WAS. This is where the answer lives — for one resource, one question, on
 * demand.
 *
 * On demand is the point. Reading full facts for the whole matrix costs 47s
 * on the expensive analyses; reading them for ONE cell is instant, and it is
 * the only cell anyone is looking at. The grid stays cheap and the detail
 * stays complete, instead of trading one for the other.
 */

const STATE_WORD = {
  measured: 'measured',
  nothing_found: 'nothing found',
  partial: 'partial',
  not_established: 'ran, could not establish a result',
  never_run: 'never run',
};

export function closeCellDetail() {
  document.getElementById('wl-detail')?.remove();
  document.removeEventListener('keydown', detailKeys);
}

function detailKeys(e) { if (e.key === 'Escape') closeCellDetail(); }

/** The panel shell both the cell popup and the refresh plan use. One shell,
 *  so the two cannot drift apart in behaviour — backdrop closes, Escape
 *  closes, a click inside does not. */
export function openDialog(title, sub, { wide = false } = {}) {
  closeCellDetail();
  const el = document.createElement('div');
  el.id = 'wl-detail';
  el.className = 'fixed inset-0 z-50 flex items-start justify-center '
    + 'bg-black/40 p-s4 overflow-auto';
  // `wide` is for content that is a table, not a paragraph — the discovery
  // import dialog's search-results grid is the first caller
  // (discovery-import.js); every other dialog on this shell is prose or a
  // short list and keeps the original width unless it opts in.
  el.innerHTML = `
    <div class="mt-[6vh] w-full ${wide ? 'max-w-[960px]' : 'max-w-[640px]'} rounded bg-paper p-s4 shadow-lg"
      role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="flex items-start justify-between gap-s3">
        <div>
          <div class="font-heading text-answer text-ink">${esc(title)}</div>
          ${sub ? `<div class="mt-[2px] font-mono text-provenance text-ink-muted">${esc(sub)}</div>` : ''}
        </div>
        <button type="button" data-act="close"
          class="text-ink-muted hover:text-ink" aria-label="Close">×</button>
      </div>
      <div id="wl-detail-body" class="mt-s3 text-caveat text-ink-muted">reading…</div>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => {
    // The backdrop closes; the panel does not close itself out from under a
    // click meant for its own contents.
    if (e.target === el || e.target.closest('[data-act="close"]')) closeCellDetail();
  });
  document.addEventListener('keydown', detailKeys);
  return el;
}

/* ── Bringing things up to date ───────────────────────────────────────────
 *
 * Staleness is per CELL, so refreshing is too. "Bring everything up to date"
 * across a twelve-repo list would re-run analyses measured this morning
 * alongside ones last measured in August — most of the work wasted, and none
 * of it visible before it started.
 *
 * So this plans first: which (resource, analysis) pairs are actually stale,
 * grouped BY ANALYSIS, because that is how a batch is enqueued — one run per
 * analysis carrying only the resources that need it. What is current is named
 * and skipped. What has NEVER run is counted separately and left out by
 * default: a first run is not a refresh, it can cost far more, and nobody
 * asking to freshen a matrix is asking to populate it.
 */

/** `{stale, never, current}` for `slugs` over the current columns. */
function refreshPlan(slugs) {
  const ids = [...new Set(qsAnalysisIds(grid.questions))];
  const stale = new Map();     // analysis id -> [slug]
  const never = new Map();     // analysis id -> [slug]
  let current = 0;
  // Pairs the projection knows NOTHING about. A confident zero here would be
  // the same failure the cells were fixed for: the fast path must not report
  // certainty it does not have, and "never run" is a claim, not a default.
  let unknown = 0;
  for (const slug of slugs) {
    const per = grid.states?.[slug] || {};
    for (const aid of ids) {
      const v = per[aid];
      // No projection entry means nothing has been established about this
      // pair. Not stale, not never-run — unknown, and unknown is not a
      // reason to run something.
      if (!v) { unknown += 1; continue; }
      if (!v.has_results) {
        if (v.certain_never_run) never.set(aid, [...(never.get(aid) || []), slug]);
        continue;
      }
      const d = daysSince(v.measured_at);
      if (d === null) continue;    // has results but no usable date: not evidence of age
      if (d >= STALE_DAYS) stale.set(aid, [...(stale.get(aid) || []), slug]);
      else current += 1;
    }
  }
  return { stale, never, current, unknown };
}

/* ── When the digest is the primary view ──────────────────────────────────
 *
 * THE MEASURE IS UNRESOLVED AREA, NOT ROW COUNT. Fifty rows nearly all
 * answered scans perfectly well — the eye is hunting the few marks that break
 * the pattern, and there are few. Twenty rows of unknown is a wall. What makes
 * a grid unreadable is how much of it is unknown, not how tall it is; screen
 * height measures the window, which was never the problem.
 *
 * It also decays correctly, which is why it is the right measure rather than
 * merely a better one: a fresh list opens on the digest because nobody has a
 * per-resource question yet, and as the session fills cells in the grid earns
 * its place back. The view follows the work.
 *
 * DECIDED ONCE, AT LIST OPEN. A view that flips underneath someone as cells
 * resolve is worse than either view, so this is evaluated when the list loads
 * and then left alone until another one is.
 */

/** PLACEHOLDER, like STALE_DAYS. "Mostly unknown" as a first stand-in, to be
 *  replaced by what a fortnight of real lists shows — the proportion at open
 *  and whether the user then toggled are both recorded for exactly that. */
const DIGEST_PRIMARY_ABOVE = 0.5;

/** The share of cells in view whose state nobody has established. */
function unresolvedShare(members, qs) {
  let total = 0, unknown = 0;
  for (const m of members) {
    const per = grid.states?.[m.entity_slug] || {};
    const row = grid.rows.get(m.entity_slug);
    for (const q of qs) {
      const ids = q.analysis_ids || [];
      if (!ids.length) continue;          // settled by kind, not by a read
      total += 1;
      const read = row && !row.error && ids.some((a) => row.factsById.has(a));
      const stored = ids.some((a) => per[a]?.has_results);
      if (!read && !stored) unknown += 1;
    }
  }
  return total ? unknown / total : 0;
}

/** Per-WORK-LIST, never global. A preference set on a fifty-repo scouting
 *  sweep should not govern a four-repo assessment — the same mistake the chat
 *  rail made across a breakpoint. A stored preference carries the context that
 *  produced it and does not apply outside it. */
const digestKey = (slug) => `re-next.digestOpen.${slug}`;

function decideDigest(members, qs) {
  const slug = grid.workList?.slug || '';
  let held = null;
  try {
    const v = localStorage.getItem(digestKey(slug));
    if (v !== null) held = v === 'true';
  } catch (_) { /* private mode: fall through to the measurement */ }

  const share = unresolvedShare(members, qs);
  grid.digestShare = share;
  grid.digestOpen = held !== null ? held : share >= DIGEST_PRIMARY_ABOVE;
  grid.digestFromPreference = held !== null;

  // Logged so the threshold can be chosen from real lists rather than from
  // anyone's imagination: the share at open, what was shown, and — appended
  // later — whether the user disagreed enough to toggle.
  logDigestDecision({
    at: new Date().toISOString(),
    workList: slug,
    resources: members.length,
    questions: qs.length,
    unresolvedShare: Math.round(share * 100) / 100,
    opened: grid.digestOpen,
    fromPreference: grid.digestFromPreference,
    toggled: false,
  });
}

/** A short rolling record, kept client-side. Never more than 50 entries. */
function logDigestDecision(entry) {
  grid.digestLogId = entry.at;
  try {
    const raw = localStorage.getItem('re-next.digestLog');
    const log = raw ? JSON.parse(raw) : [];
    log.push(entry);
    localStorage.setItem('re-next.digestLog', JSON.stringify(log.slice(-50)));
  } catch (_) { /* not worth failing a render over */ }
}

/** Mark the most recent decision as one the user overrode. */
function noteDigestToggled(open) {
  try {
    const raw = localStorage.getItem('re-next.digestLog');
    const log = raw ? JSON.parse(raw) : [];
    const row = log.find((e) => e.at === grid.digestLogId);
    if (row) { row.toggled = true; row.toggledTo = open; }
    localStorage.setItem('re-next.digestLog', JSON.stringify(log));
  } catch (_) { /* same */ }
}

const planSize = (m) => [...m.values()].reduce((n, a) => n + a.length, 0);

function openRefreshPlan(ctx) {
  const slugs = targets();
  const scope = grid.selected.size ? `${grid.selected.size} selected` : 'all rows';
  const plan = refreshPlan(slugs);
  const el = openDialog('Bring up to date', `${slugs.length} resource(s) · ${scope}`);
  const body = el.querySelector('#wl-detail-body');

  const staleCount = planSize(plan.stale);
  const neverCount = planSize(plan.never);
  if (!staleCount && !neverCount) {
    body.innerHTML = `<p>Nothing here was measured more than
      <span class="tnum">${STALE_DAYS}</span> days ago.
      <span class="tnum">${plan.current}</span> measurement(s) across
      <span class="tnum">${slugs.length}</span> resource(s) are current, so
      there is nothing to refresh.</p>`;
    return;
  }

  const listing = (m) => [...m.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([aid, ss]) => `<li><span class="font-mono">${esc(aid)}</span>
      — <span class="tnum">${ss.length}</span> resource(s)</li>`).join('');

  body.innerHTML = `
    ${staleCount
      ? `<p>Stale — measured more than <span class="tnum">${STALE_DAYS}</span> days ago:</p>
         <ul class="ml-s3 mt-[4px] list-disc">${listing(plan.stale)}</ul>`
      : `<p>Nothing is stale.</p>`}
    ${plan.current ? `<p class="mt-s2"><span class="tnum">${plan.current}</span>
      measurement(s) are already current and will be skipped.</p>` : ''}
    ${plan.unknown ? `<p class="mt-s2"><span class="tnum">${plan.unknown}</span>
      pair(s) have no state read yet, so whether they have ever run is
      <em>unknown</em>, not no. They are left out — this plan will not claim a
      first run for something it has not looked at.</p>` : ''}
    ${neverCount ? `
      <p class="mt-s2">
        <label><input type="checkbox" id="wl-incl-never"> also run the
        <span class="tnum">${neverCount}</span> that ${
          neverCount === 1 ? 'has' : 'have'} never run</label>
        — a first run, not a refresh, and it can cost far more.</p>
      <ul class="ml-s3 mt-[4px] list-disc" id="wl-never-list" hidden>${listing(plan.never)}</ul>`
      : ''}
    <p class="mt-s3 text-ink">This queues <span class="tnum" id="wl-plan-n">${staleCount}</span>
      run(s), one batch per analysis carrying only the resources that need it.
      Nothing runs until you confirm.</p>
    <div id="wl-plan-movement"></div>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="go"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
        >Queue them</button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>`;

  const incl = body.querySelector('#wl-incl-never');
  incl?.addEventListener('change', () => {
    body.querySelector('#wl-never-list').hidden = !incl.checked;
    body.querySelector('#wl-plan-n').textContent =
      planSize(plan.stale) + (incl.checked ? planSize(plan.never) : 0);
  });
  reportPlanMovement('wl-plan-movement',
    [...plan.stale.entries()].flatMap(([analysis, ss]) => ss.map((slug) => ({ slug, analysis }))));

  body.querySelector('[data-act="go"]').addEventListener('click', () => {
    // Merged per analysis, so one that is stale on some resources and never
    // run on others is ONE batch, not two.
    const merged = new Map();
    for (const [aid, ss] of plan.stale) merged.set(aid, [...ss]);
    if (incl?.checked) {
      for (const [aid, ss] of plan.never) {
        merged.set(aid, [...new Set([...(merged.get(aid) || []), ...ss])]);
      }
    }
    closeCellDetail();
    runRefresh(merged, ctx);
  });
}

/**
 * How many of the runs a plan queues are for measurements that have not moved.
 *
 * The same fact the dashboard shows on a measurement, counted over a PLAN —
 * "of the N runs this queues, M are for measurements unchanged across their
 * recorded runs". On a measurement it reports; here it argues, because this is
 * the moment before paying again, and a cadence that returns nothing is only
 * visible as a cost at the point of spending.
 *
 * Best-effort and non-blocking: it fills in after the plan renders, and an
 * unreadable series simply is not counted. A plan must never wait on it.
 */
async function reportPlanMovement(slotId, slug_pairs) {
  const slot = document.getElementById(slotId);
  if (!slot || !slug_pairs.length) return;
  // getAnalysisTrend hits /api/projects/{slug}/analyses/{id}/trend
  // (re-api.js), a repo-only route (projects.py) with no entity_type
  // parameter and no database/filesystem backend equivalent today.
  // DEPENDENCY, not attempted here: generalizing getAnalysisTrend and its
  // backend route to a non-repo entity type is a separate change.
  //
  // Without this gate, every read below would fail for a non-repo work
  // list and land in the generic `catch (_) { return null }` further down
  // — indistinguishable from "checked, this measurement is unreadable".
  // The plan-movement callout would then simply never appear for a
  // database/filesystem work list, silently undercounting "would repeat
  // an unchanged measurement" as "nothing repeats" rather than saying the
  // heuristic was never evaluated for this entity type. Low-visibility
  // (this is a preview-only, best-effort callout, not a blocking check)
  // but a real instance of the same absence-vs-never-measured conflation
  // this session's other fixes address — so skip the doomed requests and
  // say so, rather than let the catch block quietly stand in for an
  // honest "not available for this type yet".
  const entityType = kindOf(grid.workList);
  if (entityType !== 'repo') {
    slot.innerHTML = `<div class="mt-s2 text-caveat text-ink-muted">
      Whether any of these run(s) would repeat an unchanged measurement
      isn't tracked yet for ${esc(entityType)} resources — this heuristic
      only reads repo analysis trends today.</div>`;
    return;
  }
  // One read per ANALYSIS, not per pair: the series is per (resource,
  // analysis), but an unchanged analysis is usually unchanged across the set,
  // and a plan preview must not cost more than the run it is describing.
  const sample = slug_pairs.slice(0, 12);
  const results = await Promise.all(sample.map(async ({ slug, analysis }) => {
    try {
      const res = await getAnalysisTrend(slug, analysis, '', entityType);
      const series = (res.runs || res.series || []).filter((r) => r && r.surveyed_at);
      if (series.length < 2) return null;
      const values = new Set(series.map((r) => r.metric_value ?? r.value));
      return values.size === 1 ? { slug, analysis, runs: series.length } : null;
    } catch (_) {
      return null;                     // not tracked, or unreadable — not counted
    }
  }));
  const still = results.filter(Boolean);
  if (!still.length || !document.getElementById(slotId)) return;
  const names = [...new Set(still.map((x) => x.analysis))];
  slot.innerHTML = `<div class="mt-s2 text-caveat text-accent-ink">
    Of the <span class="tnum">${slug_pairs.length}</span> run(s) this queues,
    <span class="tnum">${still.length}</span> are for measurements
    <strong>unchanged across their recorded runs</strong> —
    <span class="font-mono">${esc(names.join(', '))}</span>. Re-running those will
    measure them again and, on this evidence, change nothing.</div>`;
}

/** Enqueue one batch per analysis, each carrying only its own resources. */
async function runRefresh(byAnalysis, ctx) {
  const entries = [...byAnalysis.entries()];
  if (!entries.length) return;
  let last = null;
  const done = [];
  for (const [aid, ss] of entries) {
    try {
      last = await enqueueBatch(aid, ss, grid.workList.slug, kindOf(grid.workList));
      done.push(`${aid} \u00d7 ${ss.length}`);
      if (last?.not_permitted?.length) done.push(notPermittedHtml(last.not_permitted));
    } catch (err) {
      // Report what DID get queued. A failure on the fourth analysis does not
      // un-queue the first three, and "it failed" would leave three batches
      // running that nobody was told about.
      note(`<span class="text-state-warn">Queued ${done.length ? done.map((d) => (d.startsWith('<') ? d : esc(d))).join(', ') : 'nothing'},
        then <span class="font-mono">${esc(aid)}</span> was refused:
        ${err.status === 403 ? refusedCue(err) : esc(err.message)}</span>`);
      if (last?.set_id) watchBatch(ctx, last.set_id);
      return;
    }
  }
  note(`Queued ${done.map((d) => (d.startsWith('<') ? d : esc(d))).join(', ')}. Watching the last batch.`);
  if (last?.set_id) watchBatch(ctx, last.set_id);
}

/** What this question's answer is entitled to claim.
 *
 * The catalog's Rationale/Source column, carried through since 2026-09-11.
 * Rendered ABOVE the results and in every branch — including the two empty
 * ones — because the caveat is most load-bearing exactly when there is
 * nothing to read: "no matches against this ruleset in this snapshot" and
 * "nothing stored" are very different statements, and without the first the
 * second reads as an all-clear.
 */
function rationaleBlock(q) {
  const text = (q?.rationale || '').trim();
  if (!text) return '';
  return `<div class="mb-s2 border-l-2 border-accent pl-s2">
      <div class="uppercase tracking-caps text-caps text-ink-muted">What this can claim</div>
      <p class="mt-[2px] max-w-[70ch] text-caveat text-accent-ink">${esc(text)}</p>
    </div>`;
}

async function openCellDetail(slug, qi, ctx) {
  const q = grid.questions[qi];
  if (!q) return;
  const el = openDialog(q.question, slug);
  const body = el.querySelector('#wl-detail-body');
  const why = rationaleBlock(q);
  const ids = q.analysis_ids || [];
  if (!ids.length) {
    body.innerHTML = why + `<p>No analysis answers this question yet, so there is
      nothing to read. That is a gap in the catalog, not a finding about
      ${esc(slug)}.</p>`;
    return;
  }
  let facts;
  try {
    const res = await getBulkFacts([slug], ids, kindOf(grid.workList));
    facts = res.subjects?.[slug] || [];
  } catch (err) {
    body.innerHTML = why + `<p class="text-state-warn">Could not read the results:
      ${esc(err.message)}</p>`;
    return;
  }
  // Fold what we just paid for back into the grid: a cell you have opened
  // stops being `▪ not read`, because now it HAS been read. Cheaper than the
  // matrix-wide read and it accumulates exactly where attention went.
  if (facts.length) {
    const prev = grid.rows.get(slug);
    const byId = prev?.factsById || new Map();
    for (const f of facts) byId.set(f.analysis_id, f);
    grid.rows.set(slug, { facts: [...byId.values()], factsById: byId });
    renderGrid();
  }
  if (!facts.length) {
    body.innerHTML = why + `<p>Nothing stored for ${esc(ids.join(', '))} on this
      resource.</p>`;
    return;
  }
  // A question is usually answered by MORE THAN ONE analysis, and they were
  // not measured at the same time — 8h ago and 3d ago on this very question.
  // So the re-run is offered per analysis, and the all-of-them action NAMES
  // them and says how many. "Re-run for this resource" said neither, and
  // quietly queued every one of them.
  const runnable = facts.map((f) => f.analysis_id).filter(Boolean);
  body.innerHTML = why + facts.map((f) => factDetailHtml(f, slug)).join('')
    + (runnable.length > 1
        ? `<div class="mt-s3 border-t border-rule pt-s2 text-caveat text-ink-muted">
            <button type="button" data-act="rerun"
              class="text-accent-ink underline">Re-run all
              <span class="tnum">${runnable.length}</span></button>
            — ${esc(runnable.join(', '))}. Each is queued as its own run.
          </div>`
        : '');
  el.querySelector('[data-act="rerun"]')?.addEventListener('click', () => {
    closeCellDetail();
    rerunOne(slug, runnable, ctx);
  });
  el.querySelectorAll('[data-rerun]').forEach((b) => b.addEventListener('click', () => {
    closeCellDetail();
    rerunOne(slug, [b.dataset.rerun], ctx);
  }));
}

/** One analysis's contribution, shown as what it is rather than summarised.
 *  The measurement date is HERE, per analysis, because that is the only place
 *  it is true — each one ran when it ran. */
function factDetailHtml(f, slug) {
  // WHEN this was measured, from the stronger of the two records.
  //
  // They disagree, and not rarely. `last_run_at` comes from the run registry;
  // `surveyed_at` is stamped on the result rows themselves. On amundsen's
  // repository_health the run registry said 24 August while the metrics rows
  // were written 10 September at 02:57 — something wrote results without
  // recording a run. The rows are the harder evidence: they exist because a
  // measurement happened. So they win, and `last_run_at` is the fallback.
  //
  // Using the same source as the cell also means the popup and the grid can
  // never show one date each for the same measurement.
  const measured = grid.states?.[slug]?.[f.analysis_id]?.measured_at || '';
  const when = measured || f.last_run_at || '';
  const d = daysSince(when);
  const stale = d !== null && d >= STALE_DAYS;
  const bits = [];
  if (f.headline) bits.push(`<p class="text-answer text-ink">${esc(f.headline)}</p>`);
  const verdict = f.value && f.value.verdict;
  const detail = f.value && (f.value.detail || f.value.summary);
  if (!f.headline && (verdict || detail)) {
    bits.push(`<p class="text-answer text-ink">${
      verdict ? `<strong class="font-semibold">${esc(String(verdict))}</strong>` : ''}${
      verdict && detail ? ' — ' : ''}${detail ? esc(String(detail)) : ''}</p>`);
  }
  if (!bits.length) {
    bits.push(`<p>This analysis recorded measures but no written summary, so
      there is no sentence to show. Its values are in the resource's own
      results view.</p>`);
  }
  return `<div class="mb-s3">
    <div class="text-caps text-ink-muted">
      <span class="font-mono">${esc(f.analysis_id || 'unknown')}</span>
      · ${esc(STATE_WORD[f.state] || f.state || 'unknown')}
      · ${when
          ? `<span class="${stale ? 'text-state-warn' : ''}" title="${esc(when)}"
              >${esc(ago(when))}${stale ? ' — stale' : ''}</span>`
          : 'run time not recorded'}
      ${f.analysis_id
          ? ` · <button type="button" data-rerun="${esc(f.analysis_id)}"
                class="text-accent-ink underline">re-run this one</button>`
          : ''}
    </div>
    ${bits.join('')}
  </div>`;
}

/** Re-run exactly the analyses behind one cell, for one resource — the
 *  narrowest version of "bring this up to date". Same batch machinery the
 *  whole-list run uses, so it queues rather than blocking the page. */
async function rerunOne(slug, ids, ctx) {
  let last = null;
  try {
    // One batch per analysis: enqueueBatch takes a single analysis id, and a
    // question can be answered by more than one.
    for (const aid of ids) {
      last = await enqueueBatch(aid, [slug], grid.workList.slug, kindOf(grid.workList));
    }
  } catch (err) {
    const networkish = /load failed|failed to fetch|networkerror/i.test(err.message || '');
    note(networkish
      ? `<span class="text-state-warn">The server could not be reached, so nothing was
         enqueued (${esc(err.message)}). If it was restarting, try again.</span>`
      : err.status === 403 ? refusedCue(err)
        : `<span class="text-state-warn">The re-run was refused: ${esc(err.message)}</span>`);
    return;
  }
  note(`Queued <span class="font-mono">${esc(ids.join(', '))}</span> for
        <span class="font-mono">${esc(slug)}</span>.`);
  // Watches the LAST set only — the progress line shows one set at a time,
  // and saying so is better than silently reporting one of several as if it
  // were the whole re-run.
  if (last?.set_id) watchBatch(ctx, last.set_id);
}

/* ── Actions ────────────────────────────────────────────────────────── */

function targets() {
  return grid.selected.size
    ? [...grid.selected]
    : grid.workList.members.map((m) => m.entity_slug);
}

/**
 * Run one analysis across the set — THROUGH THE SAME PLAN PREVIEW the refresh
 * uses.
 *
 * Both actions answer one question, "what am I about to spend", and if they
 * answer it in two different shapes people learn to read one and skim the
 * other. So this one shows what it would run and what it already has, and
 * queues nothing until it is confirmed.
 */
function runBatch(ctx) {
  const analysisId = document.getElementById('wl-analysis')?.value;
  if (!analysisId) { note('<span class="text-state-warn">No analysis selected.</span>'); return; }
  const slugs = targets();
  const scope = grid.selected.size ? `${grid.selected.size} selected` : 'all rows';
  const el = openDialog('Run across the set', `${analysisId} · ${slugs.length} resource(s) · ${scope}`);
  const body = el.querySelector('#wl-detail-body');

  // What is already held for this analysis, so "run it again" is a choice
  // rather than an accident.
  let fresh = 0, stale = 0, none = 0;
  for (const slug of slugs) {
    const v = grid.states?.[slug]?.[analysisId];
    if (!v || !v.has_results) { none += 1; continue; }
    const d = daysSince(v.measured_at);
    if (d !== null && d >= STALE_DAYS) stale += 1; else fresh += 1;
  }
  body.innerHTML = `
    <p>This runs <span class="font-mono">${esc(analysisId)}</span> on
      <span class="tnum">${slugs.length}</span> resource(s), whatever they
      already hold.</p>
    <ul class="ml-s3 mt-s2 list-disc">
      <li><span class="tnum">${none}</span> have no result for it</li>
      <li><span class="tnum">${stale}</span> were measured more than
        <span class="tnum">${STALE_DAYS}</span> days ago</li>
      <li><span class="tnum">${fresh}</span> were measured more recently —
        <strong>these will be re-run too</strong>. To skip them, use
        <em>Bring up to date</em> instead.</li>
    </ul>
    <div id="wl-run-movement"></div>
    <p class="mt-s3 text-ink">Nothing runs until you confirm.</p>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="go"
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink"
        >Run <span class="tnum">${slugs.length}</span></button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>`;
  reportPlanMovement('wl-run-movement', slugs.map((slug) => ({ slug, analysis: analysisId })));

  body.querySelector('[data-act="go"]').addEventListener('click', () => {
    closeCellDetail();
    enqueueRun(ctx, analysisId, slugs);
  });
}

async function enqueueRun(ctx, analysisId, slugs) {
  note(`Enqueueing <span class="tnum">${slugs.length}</span> run(s)…`);
  let started;
  try {
    started = await enqueueBatch(analysisId, slugs, grid.workList.slug, kindOf(grid.workList));
  } catch (err) {
    // `Load failed` / `Failed to fetch` is the browser's wording for a
    // network-level failure, not an application error — most often the
    // server restarting. Distinguish it, because "the batch could not be
    // enqueued" reads as a rejection and this is not one: nothing was
    // written and retrying is the right move.
    const networkish = /load failed|failed to fetch|networkerror/i.test(err.message || '');
    note(networkish
      ? `<span class="text-state-warn">The server could not be reached, so nothing was
         enqueued (${esc(err.message)}). If it was restarting, try again.</span>`
      : err.status === 403 ? refusedCue(err)
        : `<span class="text-state-warn">The batch was refused: ${esc(err.message)}</span>`);
    return;
  }
  note(`Batch <span class="font-mono">${esc(started.set_id)}</span> ·
        <span class="tnum">${started.runs.length}</span> run(s) across the set.${
        started.not_permitted?.length ? ` ${notPermittedHtml(started.not_permitted)}` : ''}`);
  watchBatch(ctx, started.set_id);
}

/**
 * Poll a set to completion.
 *
 * Shows queued / running / done / failed SEPARATELY. "12 of 14 finished" with
 * no split hides that two of them failed, and a batch view whose only number
 * is progress is the one thing worse than no batch view.
 */
function watchBatch(ctx, setId) {
  clearInterval(grid.poll);
  let misses = 0;
  const tick = async () => {
    let p;
    try {
      p = await getBatchProgress(setId);
      misses = 0;
    } catch (err) {
      // One failed read is not the end of the batch: give up only after five in a row.
      if ((misses += 1) < 5) return;
      clearInterval(grid.poll);
      renderProgress(`<span class="text-state-warn">Lost track of this batch: ${esc(err.message)}</span>`);
      return;
    }
    grid.batch = p;
    // The queue's OWN vocabulary — `ProjectRegistry.RUN_STATES`. `done` is not
    // one of them; assuming it was is what made a finished batch poll forever.
    const order = ['queued', 'claimed', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'];
    const tone = { succeeded: 'text-state-ok', failed: 'text-state-warn',
                   cancelled: 'text-state-warn', unknown: 'text-ink-muted' };
    const parts = order.filter((s) => p.counts[s]).map((s) =>
      `<span class="${tone[s] || 'text-ink'}"><span class="tnum">${p.counts[s]}</span> ${s}</span>`);
    // Anything the server flagged as unrecognised is named, not folded in.
    for (const s of p.unrecognised_states || []) {
      parts.push(`<span class="text-state-warn"><span class="tnum">${p.counts[s] || 0}</span> ${esc(s)} (unrecognised)</span>`);
    }
    renderProgress(`<span class="text-caps uppercase tracking-caps text-ink-muted">Batch</span>
      ${parts.join(' · ')} · <span class="tnum">${p.finished}</span> of
      <span class="tnum">${p.total}</span> finished`);
    renderGrid();
    if (p.complete) {
      clearInterval(grid.poll);
      grid.batch = null;
      // Re-read the facts: the whole reason for running was to change them.
      await loadGrid(ctx);
      renderProgress(`<span class="text-caps uppercase tracking-caps text-ink-muted">Batch</span>
        finished — <span class="tnum">${p.counts.succeeded || 0}</span> succeeded${
          p.counts.failed ? `, <span class="text-state-warn"><span class="tnum">${p.counts.failed}</span> failed</span>` : ''}`);
    }
  };
  tick();
  grid.poll = setInterval(tick, 3000);
}

function renderProgress(html) {
  const el = document.getElementById('wl-progress');
  if (el) el.innerHTML = `<div class="flex flex-wrap items-baseline gap-s3 text-caveat">${html}</div>`;
}

async function bulkDisposition(ctx, disposition) {
  const slugs = [...grid.selected];
  const asked = await inPagePrompt({
    title: `Mark ${slugs.length} as ${disposition}`,
    message: `Why are these ${slugs.length} being marked "${disposition}"?\n\n`
      + 'The reason is stored with the verdict and is what the trail is for. '
      + 'Leave blank to record none.',
    label: 'Reason (optional)',
    confirmLabel: 'Mark',
  });
  if (asked === null) return;      // cancelled -- nothing marked
  const reason = asked;
  const failed = [];
  const noUrl = [];
  for (const slug of slugs) {
    const p = (ctx.projects || []).find((x) => x.slug === slug);
    if (!p?.github_url) { noUrl.push(slug); continue; }
    try {
      await setDisposition(p.github_url, disposition, reason);
      p.disposition = disposition;
    } catch (err) { failed.push(`${slug}: ${err.message}`); }
  }
  const ok = slugs.length - failed.length - noUrl.length;
  note([
    ok ? `<span class="tnum">${ok}</span> marked ${esc(disposition)}${reason ? ` — “${esc(reason)}”` : ' with no reason recorded'}` : '',
    noUrl.length ? `<span class="text-state-warn"><span class="tnum">${noUrl.length}</span> have no GitHub URL and could not be marked: ${esc(noUrl.join(', '))}</span>` : '',
    failed.length ? `<span class="text-state-warn">${esc(failed.join('; '))}</span>` : '',
  ].filter(Boolean).join(' · '));
}

async function promote(ctx) {
  const survivors = [...grid.selected];
  const name = await inPagePrompt({
    title: 'Promote to a shortlist',
    message: `Name for the shortlist of ${survivors.length}:`,
    label: 'Shortlist name',
    defaultValue: `${grid.workList.display_name} — shortlist`,
    confirmLabel: 'Promote',
  });
  if (name === null) return;
  try {
    const next = await promoteWorkList(grid.workList.slug, survivors, name);
    grid.workList = next;
    grid.selected.clear();
    grid.rows.clear();
    note(`Promoted <span class="tnum">${next.members.length}</span> into
      <strong>${esc(next.display_name)}</strong>, which records
      <span class="font-mono">${esc(next.derived_from)}</span> as its parent.`);
    await renderWorkListPane(ctx);
  } catch (err) {
    note(`<span class="text-state-warn">Could not promote: ${esc(err.message)}</span>`);
  }
}

/** After an add or a start: re-read the list's link and scope, repaint. */
async function afterInvestigationChange(ctx, out) {
  try {
    const fresh = await getWorkList(grid.workList.slug);
    grid.workList.investigation = fresh.investigation;
  } catch { /* the header reads what it had */ }
  await loadScope(ctx);
  renderActions(ctx);
  renderLinked();
  renderGrid();
  note(out.noteHtml);
}

function addToInvestigation(ctx) {
  const ticked = grid.selected.size ? [...grid.selected] : null;
  return addListToInvestigation({
    list: grid.workList, slugs: ticked, host: ctx.host,
    onDone: (out) => afterInvestigationChange(ctx, out),
  });
}

function startFromList(ctx) {
  return openStartFromList({
    list: grid.workList, host: ctx.host,
    onDone: (out) => afterInvestigationChange(ctx, out),
  });
}

async function exportCsv() {
  try {
    const { text, filename } = await fetchWorkListCsv(grid.workList.slug);
    saveCsv(text, filename);
    note(`Downloaded <span class="font-mono">${esc(filename)}</span>.`);
  } catch (err) {
    note(`<span class="text-state-warn">Could not export: ${esc(err.message)}</span>`);
  }
}

async function publish(ctx) {
  note('Publishing to Egeria…');
  try {
    const res = await publishWorkList(grid.workList.slug);
    grid.workList = await getWorkList(grid.workList.slug);
    // "published · read back <when>" only once the re-read work list proves the collection exists in Egeria;
    // until then it is "sent · waiting for Egeria" (the Egeria family's words follow the read-back).
    const proven = !!(grid.workList && grid.workList.egeria_guid);
    const bits = [
      `${proven ? `published · ${readBackAt()}` : 'sent · waiting for Egeria'}: a <strong>${esc(res.type_name)}</strong> collection
       (<span class="font-mono">${esc(res.egeria_guid)}</span>).`,
    ];
    if (Array.isArray(res.attach)) {
      // Each member by its own outbox row after the inline drain, never by what was asked for.
      if (res.attach.length) bits.push(attachHtml(res.attach));
      // The inline send itself failed (not a member's write): said, with the error cue; the rows wait for the drain.
      if (res.attach_error) {
        bits.push(`<span data-attach-error class="block">${cue('error', 'inline send failed', res.attach_error)}</span>`);
      }
    } else {
      // An answer without `attach` (an older server): the adds were only queued.
      bits.push(`<span class="tnum">${res.queued_members}</span> membership(s) queued —
       they are applied by the worker that drains the outbox, not here.`);
      if (res.queued_to_attach?.length) {
        bits.push(`<span data-publish-queued>queued to attach: ${esc(res.queued_to_attach.join(', '))}.</span>`);
      }
    }
    // Named, not counted, with the member's type. These are not failed writes to retry: the resource has no
    // Egeria element yet, so there is nothing to attach.
    const unpublished = res.not_published
      || (res.members_without_an_egeria_asset || []).map((s) => ({ entity_slug: s, words: 'not published to Egeria yet' }));
    if (unpublished.length) {
      bits.push(`<span class="text-accent-ink" data-publish-unpublished>
        <span class="tnum">${unpublished.length}</span> not attached:
        ${unpublished.map((m) => `${esc(m.entity_slug)} · ${esc(m.words)}`).join('; ')}</span>`);
    }
    // RE could not establish whether these have an element (unsupported type, not held, unreadable): never
    // folded into "not published".
    if (res.not_resolved?.length) {
      bits.push(`<span class="text-state-warn" data-publish-unresolved>
        <span class="tnum">${res.not_resolved.length}</span> not checked:
        ${res.not_resolved.map((m) => `${esc(m.entity_slug)} · ${esc(m.words)}`).join('; ')}</span>`);
    }
    if (res.not_permitted?.length) bits.push(notPermittedHtml(res.not_permitted));
    const removals = removalsHtml(res.removals);
    if (removals) bits.push(removals);
    const linkBack = linkBackHtml(res.link_back);
    if (linkBack) bits.push(linkBack);
    note(bits.join(' '));
    renderActions(ctx);
  } catch (err) {
    note(err.status === 403 ? `<span data-publish-refused>${refusedCue(err)}</span>`
      : `<span class="text-state-warn">Publish failed: ${esc(err.message)}</span>`);
  }
}

/* ── Entry points used by the shell ─────────────────────────────────── */

/** Crossing the breakpoint swaps the view, once, not on every resize tick. */
let narrowWas = null;
window.addEventListener('resize', () => {
  const now = isNarrow();
  if (now === narrowWas) return;
  narrowWas = now;
  if (grid.workList) renderGrid();
});

export async function openWorkList(ctx, slug) {
  grid.workList = await getWorkList(slug);
  grid.rows.clear();
  grid.selected.clear();
  // Invalidate any background resolve still running for the previous list —
  // its results would land in the new list's rows.
  grid.bgToken += 1;
  grid.bgRemaining = 0;
  grid.bgError = null;
  clearInterval(grid.poll);
  window.__wlCtx = ctx;
  grid.ctx = ctx;   // the cell popup's re-run needs it, and it is not passed down
  await renderWorkListPane(ctx);
}

export async function saveAsWorkList(displayName, slugs, { investigation = '', rationale = '', entityType } = {}) {
  return createWorkList(displayName, slugs, { investigation, rationale, entityType });
}

export { listWorkLists };
